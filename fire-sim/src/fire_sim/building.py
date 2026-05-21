"""A single simulated fire panel exposing a Modbus TCP server.

Each ``Building`` owns:
  * its own ``ModbusServerContext`` populated with input registers,
  * an asyncio task running the Modbus TCP server on a unique port,
  * a tick task that increments the heartbeat / uptime once per second.

State changes (alarm/fault, power, supervisory, freeze, offline) are
applied via async methods so the HTTP control plane can drive them
without touching internals.

Failure-injection semantics:
  * ``set_frozen(True)``  - server keeps responding but heartbeat freezes.
                            Simulates a hung MCU / firmware lockup.
  * ``set_offline(True)`` - server stops listening on the port. Simulates
                            network loss or full power failure.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from pymodbus.datastore import (
    ModbusSequentialDataBlock,
    ModbusServerContext,
    ModbusSlaveContext,
)
from pymodbus.server import ModbusTcpServer

from .registers import (
    NUM_REGS,
    REG_FIRE,
    REG_FW_VERSION,
    REG_HEARTBEAT_HIGH,
    REG_HEARTBEAT_LOW,
    REG_POWER,
    REG_SUPERVISORY,
    REG_UPTIME_HIGH,
    REG_UPTIME_LOW,
    FireState,
    PowerState,
    SupervisoryState,
)

log = logging.getLogger(__name__)

# Modbus function code 4 == read input registers. setValues() uses fx codes.
_FX_INPUT_REGISTERS = 4


@dataclass
class BuildingState:
    fire: int = int(FireState.NORMAL)
    supervisory: int = int(SupervisoryState.NORMAL)
    power: int = int(PowerState.MAINS)
    heartbeat: int = 0
    uptime: int = 0
    fw_version: int = 0x0100  # v1.0
    frozen: bool = False
    offline: bool = False


class Building:
    """One simulated fire panel + Modbus TCP server."""

    def __init__(self, building_id: str, host: str, port: int) -> None:
        self.id = building_id
        self.host = host
        self.port = port
        self.state = BuildingState()

        self._slave = ModbusSlaveContext(
            di=ModbusSequentialDataBlock(0, [0] * NUM_REGS),
            co=ModbusSequentialDataBlock(0, [0] * NUM_REGS),
            hr=ModbusSequentialDataBlock(0, [0] * NUM_REGS),
            ir=ModbusSequentialDataBlock(0, [0] * NUM_REGS),
        )
        self._context = ModbusServerContext(slaves=self._slave, single=True)

        self._server: ModbusTcpServer | None = None
        self._server_task: asyncio.Task | None = None
        self._tick_task: asyncio.Task | None = None
        self._t0 = time.monotonic()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def start(self) -> None:
        await self._start_server()
        self._tick_task = asyncio.create_task(self._tick_loop(), name=f"tick-{self.id}")
        # Push initial register values so first poll gets sensible data.
        self._write_registers()

    async def stop(self) -> None:
        if self._tick_task:
            self._tick_task.cancel()
            try:
                await self._tick_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._tick_task = None
        await self._stop_server()

    async def _start_server(self) -> None:
        if self._server is not None:
            return
        self._server = ModbusTcpServer(
            context=self._context,
            address=(self.host, self.port),
        )
        self._server_task = asyncio.create_task(
            self._run_server(), name=f"modbus-{self.id}-{self.port}"
        )
        # Yield so the server has a chance to bind before callers continue.
        await asyncio.sleep(0)

    async def _run_server(self) -> None:
        try:
            await self._server.serve_forever()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Modbus server crashed for %s on %s:%s", self.id, self.host, self.port)

    async def _stop_server(self) -> None:
        if self._server is not None:
            try:
                await self._server.shutdown()
            except Exception:  # noqa: BLE001
                log.exception("error shutting down server for %s", self.id)
            self._server = None
        if self._server_task is not None:
            if not self._server_task.done():
                self._server_task.cancel()
            try:
                await self._server_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._server_task = None

    # ------------------------------------------------------------------
    # Tick loop: heartbeat + uptime
    # ------------------------------------------------------------------
    async def _tick_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(1.0)
                if not self.state.frozen:
                    self.state.heartbeat = (self.state.heartbeat + 1) & 0xFFFFFFFF
                    self.state.uptime = int(time.monotonic() - self._t0)
                self._write_registers()
            except asyncio.CancelledError:
                break
            except Exception:  # noqa: BLE001
                log.exception("tick error for %s", self.id)

    def _write_registers(self) -> None:
        regs = [0] * NUM_REGS
        regs[REG_FIRE] = self.state.fire
        regs[REG_SUPERVISORY] = self.state.supervisory
        regs[REG_POWER] = self.state.power
        regs[REG_HEARTBEAT_LOW] = self.state.heartbeat & 0xFFFF
        regs[REG_HEARTBEAT_HIGH] = (self.state.heartbeat >> 16) & 0xFFFF
        regs[REG_FW_VERSION] = self.state.fw_version
        regs[REG_UPTIME_LOW] = self.state.uptime & 0xFFFF
        regs[REG_UPTIME_HIGH] = (self.state.uptime >> 16) & 0xFFFF
        self._slave.setValues(_FX_INPUT_REGISTERS, 0, regs)

    # ------------------------------------------------------------------
    # State control (called from HTTP control plane and chaos engine)
    # ------------------------------------------------------------------
    async def set_fire(self, state: FireState) -> None:
        self.state.fire = int(state)
        self._write_registers()

    async def set_supervisory(self, state: SupervisoryState) -> None:
        self.state.supervisory = int(state)
        self._write_registers()

    async def set_power(self, state: PowerState) -> None:
        self.state.power = int(state)
        self._write_registers()

    async def set_frozen(self, frozen: bool) -> None:
        self.state.frozen = frozen

    async def set_offline(self, offline: bool) -> None:
        if offline and not self.state.offline:
            await self._stop_server()
        elif not offline and self.state.offline:
            await self._start_server()
            self._write_registers()
        self.state.offline = offline

    # ------------------------------------------------------------------
    # Snapshot for API responses
    # ------------------------------------------------------------------
    def snapshot(self) -> dict:
        return {
            "id": self.id,
            "host": self.host,
            "port": self.port,
            "fire": self.state.fire,
            "supervisory": self.state.supervisory,
            "power": self.state.power,
            "heartbeat": self.state.heartbeat,
            "uptime": self.state.uptime,
            "fw_version": self.state.fw_version,
            "frozen": self.state.frozen,
            "offline": self.state.offline,
        }
