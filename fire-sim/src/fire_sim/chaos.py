"""Chaos engine: probabilistic failure injection across the fleet.

Useful for soak-testing: kicks off random alarms, faults, MCU freezes,
and offline events at configurable rates, then auto-recovers each event
after a short delay so the system returns to a healthy baseline.
"""

from __future__ import annotations

import asyncio
import logging
import random

from .registers import FireState, PowerState, SupervisoryState
from .simulator import Simulator

log = logging.getLogger(__name__)


class Chaos:
    def __init__(
        self,
        sim: Simulator,
        *,
        alarm_per_min: float = 0.1,
        fault_per_min: float = 0.05,
        offline_per_min: float = 0.05,
        freeze_per_min: float = 0.05,
        recover_after_s: float = 30.0,
    ) -> None:
        self.sim = sim
        # Convert per-minute rates to per-second probabilities (per building).
        self._p_alarm = alarm_per_min / 60.0
        self._p_fault = fault_per_min / 60.0
        self._p_offline = offline_per_min / 60.0
        self._p_freeze = freeze_per_min / 60.0
        self._recover_after_s = recover_after_s
        self._task: asyncio.Task | None = None
        self._enabled = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._enabled = True
        self._task = asyncio.create_task(self._loop(), name="chaos")
        log.info(
            "Chaos enabled: alarm/min=%.3f fault/min=%.3f offline/min=%.3f freeze/min=%.3f recover=%.1fs",
            self._p_alarm * 60,
            self._p_fault * 60,
            self._p_offline * 60,
            self._p_freeze * 60,
            self._recover_after_s,
        )

    async def stop(self) -> None:
        self._enabled = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(1.0)
                for b in list(self.sim.buildings.values()):
                    r = random.random()
                    if r < self._p_alarm:
                        await b.set_fire(FireState.ALARM)
                        asyncio.create_task(self._auto_recover(b, "fire"))
                    elif r < self._p_alarm + self._p_fault:
                        await b.set_fire(FireState.FAULT)
                        asyncio.create_task(self._auto_recover(b, "fire"))
                    elif r < self._p_alarm + self._p_fault + self._p_offline:
                        if not b.state.offline:
                            await b.set_offline(True)
                            asyncio.create_task(self._auto_recover(b, "offline"))
                    elif r < self._p_alarm + self._p_fault + self._p_offline + self._p_freeze:
                        if not b.state.frozen:
                            await b.set_frozen(True)
                            asyncio.create_task(self._auto_recover(b, "freeze"))
            except asyncio.CancelledError:
                break
            except Exception:  # noqa: BLE001
                log.exception("chaos loop error")

    async def _auto_recover(self, building, kind: str) -> None:
        try:
            await asyncio.sleep(self._recover_after_s)
            if kind == "fire":
                await building.set_fire(FireState.NORMAL)
                await building.set_supervisory(SupervisoryState.NORMAL)
                await building.set_power(PowerState.MAINS)
            elif kind == "offline":
                await building.set_offline(False)
            elif kind == "freeze":
                await building.set_frozen(False)
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001
            log.exception("auto-recover error for %s (%s)", building.id, kind)
