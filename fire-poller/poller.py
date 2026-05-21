# =============================================================================
# poller.py  —  Reads one building's Modbus registers and detects changes.
#
# WHAT THIS FILE DOES:
#   - Connects to one STM32 panel over Modbus TCP.
#   - Reads the 8 status registers defined in config.py.
#   - Compares the result to the previous reading to spot transitions
#     (e.g. fire went from NORMAL → ALARM).
#   - Returns a plain dict with the decoded values.
#   - Tracks consecutive failures and marks a building OFFLINE when
#     the failure threshold is crossed.
#
# WHAT THIS FILE DOES NOT DO:
#   - It does not store data in a database (that is main.py's job).
#   - It does not run a loop (main.py calls poll_building() on a timer).
#   - It does not know about other buildings.
#
# HOW TO EXTEND:
#   - Add a new register? Add its offset to config.py, read it in
#     _decode_registers(), return it in the dict.
#   - Add a new state transition? Add an if-block inside detect_changes().
# =============================================================================

import logging
import time

from pymodbus.client import ModbusTcpClient

import config

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Building state tracker
# Each building gets one instance, created once by main.py at startup.
# ---------------------------------------------------------------------------

class BuildingState:
    """
    Holds the last-known state of one building so we can detect changes.

    Attributes
    ----------
    building_id   : str    — e.g. "BLDG-042"
    host          : str    — IP address of the STM32
    port          : int    — Modbus TCP port
    fire          : int    — last fire register value
    power         : int    — last power register value
    heartbeat     : int    — last combined 32-bit heartbeat
    hb_last_moved : float  — monotonic time when heartbeat last changed
    failures      : int    — consecutive poll failures
    offline       : bool   — True when failure_threshold reached
    offline_since : float  — monotonic time when building went offline
    """

    def __init__(self, building_id: str, host: str, port: int) -> None:
        self.building_id   = building_id
        self.host          = host
        self.port          = port

        # Previous register values (None = first poll, no comparison yet)
        self.fire          = None
        self.power         = None
        self.heartbeat     = None

        # Heartbeat staleness tracking
        self.hb_last_moved = None   # monotonic timestamp

        # Failure / offline tracking
        self.failures      = 0
        self.offline       = False
        self.offline_since = None


# ---------------------------------------------------------------------------
# Main polling function  —  one call = one building, one Modbus read
# ---------------------------------------------------------------------------

def poll_building(state: BuildingState):
    """
    Poll one building and return a result dict.

    Parameters
    ----------
    state : BuildingState
        Carries previous values for change detection.
        Updated in-place by this function.

    Returns
    -------
    dict
        Decoded register values + change flags.
        Example::

            {
                "building_id": "BLDG-001",
                "fire":        1,           # register value
                "fire_label":  "ALARM",     # human-readable
                "power":       0,
                "power_label": "MAINS",
                "supervisory": 0,
                "heartbeat":   1042,
                "uptime_s":    300,
                "fw_version":  "v1.0",
                "latency_ms":  3.4,
                "fire_changed":  True,      # True if fire state changed this poll
                "power_changed": False,
                "heartbeat_frozen": False,  # True if MCU heartbeat has stopped
                "offline": False,
            }

    None
        If the poll failed (connection refused, timeout, etc.).
        The caller should check ``state.offline`` to see if the building
        has been marked offline.
    """
    # ── 1. Skip immediately if we know the building is offline ───────── #
    if state.offline:
        # Wait for recovery timeout before trying again
        if time.monotonic() - state.offline_since < config.RECOVERY_TIMEOUT_S:
            return None

        # Recovery window open — try one probe
        log.info("[%s] Trying recovery probe...", state.building_id)

    # ── 2. Open Modbus TCP connection ─────────────────────────────────── #
    client = ModbusTcpClient(
        host=state.host,
        port=state.port,
        timeout=config.READ_TIMEOUT,
    )

    try:
        if not client.connect():
            _record_failure(state, reason="connect_failed")
            return None

        # ── 3. Read registers ─────────────────────────────────────────── #
        t0 = time.monotonic()
        response = client.read_input_registers(
            address=0,
            count=config.NUM_REGISTERS,
            slave=config.MODBUS_UNIT_ID,
        )
        latency_ms = (time.monotonic() - t0) * 1000.0

        if response.isError():
            log.warning("[%s] Modbus error: %s", state.building_id, response)
            _record_failure(state, reason="modbus_error")
            return None

        # ── 4. Decode registers ───────────────────────────────────────── #
        result = _decode_registers(response.registers, state, latency_ms)

        # ── 5. Detect state changes ───────────────────────────────────── #
        result = _detect_changes(result, state)

        # ── 6. Reset failure counter on success ───────────────────────── #
        if state.offline:
            log.info("[%s] BUILDING BACK ONLINE after %.0fs",
                     state.building_id,
                     time.monotonic() - state.offline_since)

        state.failures = 0
        state.offline  = False

        # Save current values for next poll's comparison
        state.fire      = result["fire"]
        state.power     = result["power"]

        return result

    except Exception as exc:
        log.error("[%s] Unexpected error: %s", state.building_id, exc)
        _record_failure(state, reason="exception")
        return None

    finally:
        client.close()


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _decode_registers(regs: list, state: BuildingState, latency_ms: float) -> dict:
    """
    Convert raw register values into a plain, readable dict.

    All the register-offset arithmetic is in this one function so no other
    code has to import config.REG_* constants directly.
    """
    fire        = regs[config.REG_FIRE]
    supervisory = regs[config.REG_SUPERVISORY]
    power       = regs[config.REG_POWER]
    hb_high     = regs[config.REG_HEARTBEAT_HIGH]
    hb_low      = regs[config.REG_HEARTBEAT_LOW]
    up_high     = regs[config.REG_UPTIME_HIGH]
    up_low      = regs[config.REG_UPTIME_LOW]
    fw          = regs[config.REG_FW_VERSION]

    heartbeat = (hb_high << 16) | hb_low
    uptime_s  = (up_high << 16) | up_low
    fw_str    = f"v{(fw >> 8) & 0xFF}.{fw & 0xFF}"

    return {
        "building_id":      state.building_id,
        "fire":             fire,
        "fire_label":       config.FIRE_LABELS.get(fire, f"UNKNOWN({fire})"),
        "supervisory":      supervisory,
        "supervisory_label":config.SUPER_LABELS.get(supervisory, f"UNKNOWN({supervisory})"),
        "power":            power,
        "power_label":      config.POWER_LABELS.get(power, f"UNKNOWN({power})"),
        "heartbeat":        heartbeat,
        "uptime_s":         uptime_s,
        "fw_version":       fw_str,
        "latency_ms":       round(latency_ms, 1),
        # change flags filled in by _detect_changes()
        "fire_changed":     False,
        "power_changed":    False,
        "heartbeat_frozen": False,
        "offline":          False,
    }


def _detect_changes(result: dict, state: BuildingState) -> dict:
    """
    Compare the current reading to the previous one.
    Set the change-flag fields in the result dict.

    TO ADD A NEW DETECTION:
        1. Add an if-block below.
        2. Set the flag key in result.
        3. Add a log line.
    """
    now = time.monotonic()

    # ── Fire state change ─────────────────────────────────────────────── #
    if state.fire is not None and result["fire"] != state.fire:
        result["fire_changed"] = True
        log.warning(
            "[%s] FIRE STATE CHANGED: %s → %s",
            result["building_id"],
            config.FIRE_LABELS.get(state.fire, state.fire),
            result["fire_label"],
        )

    # ── Power state change ────────────────────────────────────────────── #
    if state.power is not None and result["power"] != state.power:
        result["power_changed"] = True
        log.warning(
            "[%s] POWER STATE CHANGED: %s → %s",
            result["building_id"],
            config.POWER_LABELS.get(state.power, state.power),
            result["power_label"],
        )

    # ── Heartbeat staleness (frozen MCU) ──────────────────────────────── #
    hb = result["heartbeat"]
    if state.heartbeat is None:
        # First reading — just record it
        state.heartbeat     = hb
        state.hb_last_moved = now
    elif hb != state.heartbeat:
        # Counter advanced — healthy
        state.heartbeat     = hb
        state.hb_last_moved = now
    else:
        # Counter did not move — check how long
        stale_for = now - state.hb_last_moved
        if stale_for >= config.HEARTBEAT_STALE_S:
            result["heartbeat_frozen"] = True
            log.warning(
                "[%s] HEARTBEAT FROZEN for %.0fs (MCU may be hung)",
                result["building_id"],
                stale_for,
            )

    return result


def _record_failure(state: BuildingState, reason: str) -> None:
    """Increment the failure counter; mark building OFFLINE if threshold reached."""
    state.failures += 1
    log.warning(
        "[%s] Poll failed (%s) — consecutive failures: %d/%d",
        state.building_id, reason, state.failures, config.FAILURE_THRESHOLD,
    )

    if state.failures >= config.FAILURE_THRESHOLD and not state.offline:
        state.offline       = True
        state.offline_since = time.monotonic()
        log.error(
            "[%s] BUILDING OFFLINE — %d consecutive failures.",
            state.building_id, state.failures,
        )
