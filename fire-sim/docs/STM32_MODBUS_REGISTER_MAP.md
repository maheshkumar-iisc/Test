# STM32 Fire Panel — Modbus TCP Register Map Specification

**Document ID:** FIRE-REG-SPEC-001  
**Version:** 1.0.0  
**Status:** DRAFT  
**Date:** 2026-05-21  
**Author:** Fire Monitoring Engineering Team  

---

## 1. Purpose

This document defines the Modbus TCP register map exposed by the STM32 controller attached to each building's fire alarm panel. It is the **single source of truth** for:

- STM32 firmware engineers implementing the Modbus server
- Central poller software reading building status
- The simulator (`fire-sim`) mimicking real panels for testing
- Quality assurance verifying end-to-end data flow

Any change to this spec requires a version bump and coordinated update across firmware, poller, and simulator.

---

## 2. Transport

| Parameter | Value |
|---|---|
| Protocol | Modbus TCP (RFC-compliant MBAP header) |
| Default port | 502 (production) / 5020+ (simulator) |
| Unit ID (slave) | 1 (single device per connection) |
| Byte order | Big-endian (Modbus standard) |
| Word order (32-bit) | High word first (register N = MSW, register N+1 = LSW) |
| Max concurrent connections | 4 (firmware limit; poller should use 1) |
| Response timeout | ≤ 200 ms under normal operation |

---

## 3. Supported Function Codes

| FC | Name | Access | Used for |
|---|---|---|---|
| 04 | Read Input Registers | Read-only | All status and telemetry |
| 03 | Read Holding Registers | Read-only | Configuration / identity |
| 06 | Write Single Register | Write | Watchdog kick (optional) |

The controller exposes **no coils or discrete inputs**. All data is register-based.

---

## 4. Input Registers (FC 04) — Status & Telemetry

These registers are **read-only** and updated by the STM32 firmware in real time.

### 4.1 Register Table

| 1-Based Address | 0-Based Offset | Name | Data Type | Range / Encoding | Update Rate |
|---:|---:|---|---|---|---|
| 30001 | 0 | `FIRE_STATE` | UINT16 | See §4.2 | On change + every tick |
| 30002 | 1 | `SUPERVISORY_STATE` | UINT16 | See §4.3 | On change + every tick |
| 30003 | 2 | `POWER_STATE` | UINT16 | See §4.4 | On change + every tick |
| 30004 | 3 | `HEARTBEAT_HIGH` | UINT16 | MSW of 32-bit counter | Every 1 s |
| 30005 | 4 | `HEARTBEAT_LOW` | UINT16 | LSW of 32-bit counter | Every 1 s |
| 30006 | 5 | `UPTIME_HIGH` | UINT16 | MSW of 32-bit seconds | Every 1 s |
| 30007 | 6 | `UPTIME_LOW` | UINT16 | LSW of 32-bit seconds | Every 1 s |
| 30008 | 7 | `FW_VERSION` | UINT16 | `0xMMNN` = major.minor | Static |
| 30009 | 8 | `ZONE_COUNT` | UINT16 | 0–255 | Static after boot |
| 30010 | 9 | `ACTIVE_ZONE_MASK_0` | UINT16 | Bit mask zones 1–16 | On change |
| 30011 | 10 | `ACTIVE_ZONE_MASK_1` | UINT16 | Bit mask zones 17–32 | On change |
| 30012 | 11 | `DETECTOR_COUNT` | UINT16 | Total addressable detectors | Static |
| 30013 | 12 | `DETECTORS_IN_ALARM` | UINT16 | Count currently in alarm | On change |
| 30014 | 13 | `DETECTORS_IN_FAULT` | UINT16 | Count currently in fault | On change |
| 30015 | 14 | `PANEL_BATTERY_V` | UINT16 | Voltage × 100 (e.g. 2430 = 24.30 V) | Every 5 s |
| 30016 | 15 | `PANEL_SUPPLY_V` | UINT16 | Voltage × 100 | Every 5 s |
| 30017 | 16 | `MCU_TEMP_C` | INT16 | Temperature × 10 (e.g. 345 = 34.5 °C) | Every 10 s |
| 30018 | 17 | `COMM_ERROR_COUNT` | UINT16 | Cumulative since boot | On change |
| 30019 | 18 | `LAST_EVENT_CODE` | UINT16 | See §4.6 | On change |
| 30020 | 19 | `LAST_EVENT_ZONE` | UINT16 | Zone # of last event (0 = global) | On change |
| 30021–30032 | 20–31 | *RESERVED* | — | Must read as 0x0000 | — |

**Total input registers:** 32 (offsets 0–31).

### 4.2 `FIRE_STATE` Encoding

| Value | Meaning | Description |
|---:|---|---|
| 0 | `NORMAL` | No fire condition detected on any zone |
| 1 | `ALARM` | One or more zones in fire alarm state |
| 2 | `FAULT` | Panel detects a wiring/sensor fault preventing reliable detection |
| 3 | `DISABLED` | Panel or all zones deliberately disabled (maintenance mode) |

**Priority:** If both ALARM and FAULT exist simultaneously, `FIRE_STATE = 1` (alarm takes precedence).

### 4.3 `SUPERVISORY_STATE` Encoding

| Value | Meaning | Description |
|---:|---|---|
| 0 | `NORMAL` | No supervisory conditions |
| 1 | `TROUBLE` | Non-fire supervisory event (sprinkler valve tamper, pre-alarm, etc.) |
| 2 | `SILENCED` | Audible alarm silenced by local panel operator |

### 4.4 `POWER_STATE` Encoding

| Value | Meaning | Description |
|---:|---|---|
| 0 | `MAINS` | Running on mains AC supply |
| 1 | `BATTERY` | Mains lost; running on battery backup |
| 2 | `FAIL` | Both mains and battery critically low / absent |
| 3 | `CHARGING` | Mains restored; battery charging |

### 4.5 Heartbeat Counter

The heartbeat is a **free-running 32-bit unsigned counter** stored as two 16-bit registers (big-endian word order):

```
heartbeat_value = (HEARTBEAT_HIGH << 16) | HEARTBEAT_LOW
```

**Behavior:**
- Increments by exactly **1** every **1 second** (±50 ms jitter acceptable).
- **Wraps** from `0xFFFFFFFF` → `0x00000000`.
- If the MCU hangs or the firmware enters an unrecoverable state, the counter **stops advancing** while the Modbus server may still respond (this is how the central poller detects a "frozen MCU" failure).
- After a power-cycle reboot, the counter **resets to 0**.

**Poller detection logic:**
```
IF (current_heartbeat == previous_heartbeat) for N consecutive polls:
    → Mark building as BUILDING_OFFLINE / MCU_FROZEN
```

Recommended threshold: **N = 5** (i.e., 5 seconds of stale heartbeat).

### 4.6 `LAST_EVENT_CODE` Encoding

| Code | Meaning |
|---:|---|
| 0x0000 | No event since boot |
| 0x0001 | Fire alarm activated |
| 0x0002 | Fire alarm cleared |
| 0x0003 | Supervisory trouble raised |
| 0x0004 | Supervisory trouble cleared |
| 0x0005 | Mains power lost |
| 0x0006 | Mains power restored |
| 0x0007 | Battery low |
| 0x0008 | Detector fault raised |
| 0x0009 | Detector fault cleared |
| 0x000A | Panel reset by operator |
| 0x000B | Zone disabled |
| 0x000C | Zone enabled |
| 0x00FF | Watchdog reboot occurred |
| 0x8000–0xFFFF | *Vendor-specific / reserved* |

---

## 5. Holding Registers (FC 03) — Configuration & Identity

These registers are read-only from the Modbus network perspective. Configuration is done locally on the STM32 via UART/USB or flash.

| 1-Based Address | 0-Based Offset | Name | Data Type | Description |
|---:|---:|---|---|---|
| 40001 | 0 | `BUILDING_ID_0` | UINT16 | ASCII chars 1–2 of building ID |
| 40002 | 1 | `BUILDING_ID_1` | UINT16 | ASCII chars 3–4 |
| 40003 | 2 | `BUILDING_ID_2` | UINT16 | ASCII chars 5–6 |
| 40004 | 3 | `BUILDING_ID_3` | UINT16 | ASCII chars 7–8 |
| 40005 | 4 | `BUILDING_ID_4` | UINT16 | ASCII chars 9–10 |
| 40006 | 5 | `HW_REVISION` | UINT16 | PCB hardware revision (e.g. 0x0200 = rev 2.0) |
| 40007 | 6 | `SERIAL_HIGH` | UINT16 | MSW of 32-bit serial number |
| 40008 | 7 | `SERIAL_LOW` | UINT16 | LSW of 32-bit serial number |
| 40009 | 8 | `POLL_INTERVAL_MS` | UINT16 | How often firmware reads the panel (typ. 500) |
| 40010 | 9 | `BOOT_COUNT` | UINT16 | Number of reboots since factory flash |
| 40011–40016 | 10–15 | *RESERVED* | — | Must read as 0x0000 |

**Building ID encoding:**
```
"BLDG-042\0\0" → 40001=0x424C  40002=0x4447  40003=0x2D30  40004=0x3432  40005=0x0000
```
(ASCII, zero-padded, no null terminator needed if all 10 chars used.)

---

## 6. Write Registers (FC 06) — Watchdog Kick (Optional)

A single writable register allows the central poller to signal "I'm alive" back to the STM32. This is a **reverse heartbeat** and is entirely optional.

| 1-Based Address | 0-Based Offset | Name | Data Type | Description |
|---:|---:|---|---|---|
| 40101 | 100 | `WATCHDOG_KICK` | UINT16 | Write any non-zero value to reset the remote watchdog timer |

**Firmware behavior (if implemented):**
- If `WATCHDOG_KICK` is not written for > 60 seconds, the STM32 may:
  - Set `SUPERVISORY_STATE = TROUBLE` and event code `0x0003`
  - Flash a local "COMMS LOST" LED
- This lets on-site personnel know the central system has stopped watching, without affecting the fire panel's own local alarm capability.
- The poller should write this register every 10–30 seconds.

**If not implemented:** The holding register address simply returns an exception code (02 = ILLEGAL DATA ADDRESS) and is safely ignored.

---

## 7. Timing & Performance Requirements

| Parameter | Requirement | Note |
|---|---|---|
| Heartbeat increment accuracy | ±50 ms | Averaged over any 60 s window |
| Response to Modbus read | < 200 ms | 99th percentile |
| Register update after relay change | < 100 ms | Time from physical relay trip to register update |
| Concurrent TCP connections | ≥ 2 (goal 4) | One for poller, one spare for diagnostics |
| Time to first response after boot | < 5 seconds | Poller will retry if no response |
| Uptime counter overflow | ~136 years | Not a concern in practice |

---

## 8. Error Handling

| Condition | STM32 Behavior | Poller Observation |
|---|---|---|
| Panel communication loss (relay bus) | `FIRE_STATE = 2` (FAULT) | Register reads succeed but state = FAULT |
| MCU firmware hang | Heartbeat stops; Modbus server may or may not respond | Stale heartbeat / timeout |
| Hardware watchdog fires | MCU reboots; heartbeat resets to 0; `LAST_EVENT_CODE = 0x00FF` | Brief offline, then heartbeat restarts from 0 |
| Voltage sag / brownout | May reboot or enter FAULT | See `PANEL_BATTERY_V` dropping; possible offline |
| Invalid Modbus request | Standard exception response (FC + 0x80, code 01/02/03) | Poller should retry, not treat as offline |
| Network cable unplugged | No TCP response | `connect_failed` → mark `BUILDING_OFFLINE` |

---

## 9. Compliance & Safety Notes

1. **This register map is for supervisory monitoring only.** The STM32 + Modbus layer does **not** replace the certified fire panel's own local alarm logic, sounder activation, or fire brigade notification.

2. The STM32 reads the fire panel's relay outputs as a **passive observer**. It must never write to or control the fire panel.

3. All timestamps are generated at the central poller (NTP-synced), not on the STM32. The STM32 has no RTC requirement for this purpose.

4. The register map intentionally avoids any "acknowledge" or "silence" function — those actions happen only at the certified panel itself or via the authenticated central UI.

5. Firmware updates to the STM32 should increment `FW_VERSION` and reset `BOOT_COUNT`. The poller should log version mismatches across the fleet.

---

## 10. Register Map Diagram (Visual Reference)

```
Input Registers (FC 04):
┌────────────────────────────────────────────────────┐
│ Offset  Name                    Encoding           │
├────────────────────────────────────────────────────┤
│   0     FIRE_STATE              0=OK 1=ALARM 2=FLT │
│   1     SUPERVISORY_STATE       0=OK 1=TRBL 2=SIL  │
│   2     POWER_STATE             0=MAINS 1=BAT 2=FL │
│   3     HEARTBEAT_HIGH          ┐                  │
│   4     HEARTBEAT_LOW           ┘ 32-bit counter   │
│   5     UPTIME_HIGH             ┐                  │
│   6     UPTIME_LOW              ┘ seconds          │
│   7     FW_VERSION              0xMMNN             │
│   8     ZONE_COUNT              0–255              │
│   9     ACTIVE_ZONE_MASK_0      bits 1–16          │
│  10     ACTIVE_ZONE_MASK_1      bits 17–32         │
│  11     DETECTOR_COUNT          total              │
│  12     DETECTORS_IN_ALARM      count              │
│  13     DETECTORS_IN_FAULT      count              │
│  14     PANEL_BATTERY_V         V×100              │
│  15     PANEL_SUPPLY_V          V×100              │
│  16     MCU_TEMP_C              °C×10 (signed)     │
│  17     COMM_ERROR_COUNT        cumulative         │
│  18     LAST_EVENT_CODE         see table          │
│  19     LAST_EVENT_ZONE         zone # or 0        │
│ 20–31   RESERVED                0x0000             │
└────────────────────────────────────────────────────┘

Holding Registers (FC 03):
┌────────────────────────────────────────────────────┐
│   0–4   BUILDING_ID             ASCII, 2 chars/reg │
│   5     HW_REVISION             0xMMNN             │
│   6–7   SERIAL_NUMBER           32-bit             │
│   8     POLL_INTERVAL_MS        typ. 500           │
│   9     BOOT_COUNT              reboots since flash│
│ 10–15   RESERVED                0x0000             │
└────────────────────────────────────────────────────┘

Write Register (FC 06):
┌────────────────────────────────────────────────────┐
│  100    WATCHDOG_KICK           any non-zero value │
└────────────────────────────────────────────────────┘
```

---

## 11. Versioning & Change Log

| Version | Date | Changes |
|---|---|---|
| 1.0.0 | 2026-05-21 | Initial draft — core registers, heartbeat, event codes |

---

## 12. Cross-References

| Artefact | Relationship |
|---|---|
| `fire-sim/src/fire_sim/registers.py` | Simulator implementation of this spec (subset) |
| `fire-sim/src/fire_sim/building.py` | Simulator server exercising the register layout |
| `fire-sim/src/fire_sim/loadtest.py` | Poller client reading these registers |
| Central Poller (future) | Production consumer of this register map |
| STM32 Firmware (future) | Production producer of this register map |

---

*End of document.*
