"""Modbus register map for the simulated fire panel.

This mirrors the contract that real STM32 firmware should expose. The poller
service must use the same offsets. All values live in the *input register*
space (Modbus function code 4, addresses 30001+ in 1-based / 0+ in 0-based).

Keep this file in sync with the firmware spec; it is the single source of
truth for both simulator and poller.
"""

from enum import IntEnum

# --- Register offsets (0-based; add 30001 for 1-based PLC notation) ---
REG_FIRE = 0           # 30001 - fire alarm state (FireState)
REG_SUPERVISORY = 1    # 30002 - supervisory / trouble state (SupervisoryState)
REG_POWER = 2          # 30003 - panel power state (PowerState)
REG_HEARTBEAT_LOW = 3  # 30004 - heartbeat counter, low 16 bits
REG_FW_VERSION = 4     # 30005 - firmware version (e.g. 0x0100 = v1.0)
REG_UPTIME_LOW = 5     # 30006 - uptime seconds, low 16 bits
REG_UPTIME_HIGH = 6    # 30007 - uptime seconds, high 16 bits
REG_HEARTBEAT_HIGH = 7 # 30008 - heartbeat counter, high 16 bits

# Total registers exposed (with headroom for future fields)
NUM_REGS = 32


class FireState(IntEnum):
    NORMAL = 0
    ALARM = 1
    FAULT = 2


class SupervisoryState(IntEnum):
    NORMAL = 0
    TROUBLE = 1


class PowerState(IntEnum):
    MAINS = 0
    BATTERY = 1
    FAIL = 2


# String <-> enum helpers (used by the HTTP control plane)
FIRE_FROM_STR = {"normal": FireState.NORMAL, "alarm": FireState.ALARM, "fault": FireState.FAULT}
SUPER_FROM_STR = {"normal": SupervisoryState.NORMAL, "trouble": SupervisoryState.TROUBLE}
POWER_FROM_STR = {"mains": PowerState.MAINS, "battery": PowerState.BATTERY, "fail": PowerState.FAIL}

FIRE_TO_STR = {v: k for k, v in FIRE_FROM_STR.items()}
SUPER_TO_STR = {v: k for k, v in SUPER_FROM_STR.items()}
POWER_TO_STR = {v: k for k, v in POWER_FROM_STR.items()}
