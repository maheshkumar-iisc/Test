# =============================================================================
# config.py  —  All settings live here.
#
# HOW TO USE:
#   - Change values in this file only. Never hard-code IPs or ports elsewhere.
#   - Every developer reads settings by importing this module:
#       from config import BUILDINGS, POLL_INTERVAL_S, LOG_LEVEL
# =============================================================================

import logging

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_LEVEL = logging.INFO          # Change to logging.DEBUG for verbose output
LOG_FORMAT = "%(asctime)s  %(levelname)-8s  %(message)s"

# ---------------------------------------------------------------------------
# Modbus connection
# ---------------------------------------------------------------------------
MODBUS_PORT      = 502            # Standard Modbus TCP port (simulator uses 5020+)
MODBUS_UNIT_ID   = 1              # Slave/unit address on the STM32
CONNECT_TIMEOUT  = 3.0            # Seconds to wait for TCP handshake
READ_TIMEOUT     = 2.0            # Seconds to wait for a register response

# ---------------------------------------------------------------------------
# Polling behaviour
# ---------------------------------------------------------------------------
POLL_INTERVAL_S       = 1.0      # How often each building is polled (seconds)
HEARTBEAT_STALE_S     = 5.0      # Seconds without heartbeat change → OFFLINE
FAILURE_THRESHOLD     = 5        # Consecutive failures → mark building OFFLINE
RECOVERY_TIMEOUT_S    = 30.0     # Seconds in OFFLINE state before retry

# ---------------------------------------------------------------------------
# Buildings list  (id, host, port)
# Add or remove rows here when buildings change.
#
# For the simulator:
#   host = "127.0.0.1", ports start at 5020
# For real STM32 panels:
#   host = panel IP, port = 502
# ---------------------------------------------------------------------------
BUILDINGS = [
    {"id": "BLDG-001", "host": "127.0.0.1", "port": 5020},
    {"id": "BLDG-002", "host": "127.0.0.1", "port": 5021},
    {"id": "BLDG-003", "host": "127.0.0.1", "port": 5022},
    # ... add up to 100 buildings
]

# ---------------------------------------------------------------------------
# Modbus register offsets  (must match STM32_MODBUS_REGISTER_MAP.md)
# ---------------------------------------------------------------------------
REG_FIRE           = 0   # 30001 — 0=Normal, 1=Alarm, 2=Fault
REG_SUPERVISORY    = 1   # 30002 — 0=Normal, 1=Trouble
REG_POWER          = 2   # 30003 — 0=Mains,  1=Battery, 2=Fail
REG_HEARTBEAT_HIGH = 3   # 30004 — heartbeat MSW
REG_HEARTBEAT_LOW  = 4   # 30005 — heartbeat LSW
REG_UPTIME_HIGH    = 5   # 30006 — uptime seconds MSW
REG_UPTIME_LOW     = 6   # 30007 — uptime seconds LSW
REG_FW_VERSION     = 7   # 30008 — 0xMMNN firmware version
NUM_REGISTERS      = 8   # how many registers to read per poll

# ---------------------------------------------------------------------------
# Human-readable labels  (used in log messages and the future UI)
# ---------------------------------------------------------------------------
FIRE_LABELS  = {0: "NORMAL", 1: "ALARM",   2: "FAULT",   3: "DISABLED"}
POWER_LABELS = {0: "MAINS",  1: "BATTERY", 2: "FAIL",    3: "CHARGING"}
SUPER_LABELS = {0: "NORMAL", 1: "TROUBLE", 2: "SILENCED"}
