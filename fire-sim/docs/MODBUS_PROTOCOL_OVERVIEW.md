# Modbus TCP Protocol — Visual Overview

**Document ID:** FIRE-MODBUS-OVERVIEW-001
**Version:** 1.0.0
**Status:** Reference
**Audience:** Firmware engineers, poller developers, QA, on-call operators

---

## 1. The Big Picture

Modbus TCP is a **client/server, request/response** protocol carried over a normal TCP connection. There is exactly one client (the poller) initiating each transaction; the server (the STM32) only ever responds.

```
                     ┌──────────────────────────────────────────────┐
                     │                Building site                 │
                     │                                              │
                     │   Fire Panel                                 │
                     │     │ dry-contact relays (Fire / Trouble /   │
                     │     │ Mains / Battery)                       │
                     │     ▼                                        │
                     │   ┌─────────────────┐                        │
                     │   │  STM32 MCU      │                        │
                     │   │  Modbus SERVER  │  TCP :502              │
                     │   │  (input regs)   │                        │
                     │   └────────▲────────┘                        │
                     │            │                                 │
                     └────────────┼─────────────────────────────────┘
                                  │  TCP/IP over VPN/LTE/MPLS
                                  │
                     ┌────────────┼─────────────────────────────────┐
                     │            ▼                                 │
                     │   ┌─────────────────┐                        │
                     │   │  Central Poller │  (one per region,      │
                     │   │  Modbus CLIENT  │   active-active)       │
                     │   └────────┬────────┘                        │
                     │            │ events to broker (NATS/Kafka)   │
                     │            ▼                                 │
                     │      Time-series DB + UI                     │
                     │                Central data center           │
                     └──────────────────────────────────────────────┘
```

**Key properties:**
- One TCP connection per (poller ↔ panel) pair, kept alive across many polls
- Client always speaks first; server never pushes
- Polls happen every 1–2 seconds, indefinitely

---

## 2. Modbus TCP Frame Anatomy

Every Modbus TCP message has two parts: the **MBAP header** (transport metadata) and the **PDU** (the actual Modbus payload).

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Modbus TCP frame                            │
├──────────────── MBAP header (7 bytes) ──────────────┬───── PDU ─────┤
│                                                     │               │
│  ┌─────────┬─────────┬─────────┬───────┐            │ ┌───┬───────┐ │
│  │  TxID   │ ProtoID │  Length │ UnitID│            │ │FC │  Data │ │
│  │ 2 bytes │ 2 bytes │ 2 bytes │1 byte │            │ │ 1B│  N B  │ │
│  └─────────┴─────────┴─────────┴───────┘            │ └───┴───────┘ │
│       │         │         │         │                     │     │   │
│       │         │         │         │                     │     │   │
│       │         │         │         └─ Slave address      │     │   │
│       │         │         │             (1 = our panel)   │     │   │
│       │         │         └─ Bytes that follow            │     │   │
│       │         │             (UnitID + PDU)              │     │   │
│       │         └─ Always 0x0000 = "Modbus"               │     │   │
│       └─ Match request/response (any uint16)              │     │   │
│                                                           │     │   │
│             Function code (1=coil read, 3=hr,             │     │   │
│             4=ir read, 6=write single, …) ────────────────┘     │   │
│                                                                 │   │
│             Function-specific data:                             │   │
│             • Read request: starting address (2 B) + count (2 B)│   │
│             • Read response: byte count (1 B) + register data   │   │
│             • Exception: original FC | 0x80, then 1-byte code   │   │
│                                                                 │   │
└─────────────────────────────────────────────────────────────────────┘
```

| Field | Size | Purpose |
|---|---|---|
| Transaction ID | 2 B | Lets the client match a response to its request when pipelining |
| Protocol ID | 2 B | Always `0x0000` for Modbus |
| Length | 2 B | Number of bytes after this field (Unit ID + PDU) |
| Unit ID | 1 B | Slave address; for TCP servers usually `0x01` |
| Function Code | 1 B | Tells the server *what* to do |
| Data | variable | Function-specific arguments / payload |

---

## 3. A Complete Read Transaction (Sequence)

The poller wants the fire panel's status. This is what happens on the wire:

```
   POLLER (client)                                    STM32 (server)
        │                                                   │
        │  ───── TCP SYN ──────────────────────────────▶    │ \
        │  ◀──── TCP SYN+ACK ─────────────────────────      │  │ TCP handshake
        │  ───── TCP ACK ──────────────────────────────▶    │ /  (once per connection;
        │                                                   │     reused for many polls)
        │                                                   │
        │ ┌───────────────────────────────────────────────┐ │
        │ │ Modbus REQUEST: Read Input Registers (FC=04)  │ │
        │ │   TxID=0x0001  Proto=0x0000  Len=0x0006       │ │
        │ │   UnitID=0x01  FC=0x04                        │ │
        │ │   StartAddr=0x0000  Count=0x0008              │ │
        │ │   "give me 8 input registers starting at 0"   │ │
        │ └───────────────────────────────────────────────┘ │
        │  ──────────────────────────────────────────▶      │
        │                                                   │  read register
        │                                                   │  block (~50 µs)
        │                                                   │
        │ ┌───────────────────────────────────────────────┐ │
        │ │ Modbus RESPONSE                               │ │
        │ │   TxID=0x0001  Proto=0x0000  Len=0x0013       │ │
        │ │   UnitID=0x01  FC=0x04                        │ │
        │ │   ByteCount=0x10                              │ │
        │ │   Reg0..Reg7 = 16 bytes of register data      │ │
        │ └───────────────────────────────────────────────┘ │
        │  ◀──────────────────────────────────────────      │
        │                                                   │
        │  decode → emit event to broker                    │
        │                                                   │
        │  (next poll uses same TCP socket, TxID=0x0002)    │
        │                                                   │
        ▼                                                   ▼
```

**Timing budget for one read** (typical):
- Network round trip (LAN/VPN): 5–50 ms
- STM32 register lookup: < 1 ms
- Total p99 from poller: **< 200 ms** (per spec §7)

---

## 4. Byte-Level Example — Reading the Fire Panel Status

Concrete bytes for "read 8 input registers starting at address 0" (i.e., the first 8 registers from the [register map](./STM32_MODBUS_REGISTER_MAP.md)).

### 4.1 Request from poller → panel

```
Hex:        00 01  00 00  00 06  01  04  00 00  00 08
            └──┬─┘ └──┬─┘ └──┬─┘ └┬┘ └┬┘ └──┬─┘ └──┬─┘
TxID = 1 ──────┘     │     │     │   │     │      │
ProtoID = 0 ─────────┘     │     │   │     │      │
Length = 6 bytes ──────────┘     │   │     │      │   (UnitID + FC + StartAddr + Count = 6)
UnitID = 1 ──────────────────────┘   │     │      │
FC = 4 (Read Input Registers) ───────┘     │      │
StartAddr = 0 (= register 30001) ──────────┘      │
Count = 8 registers ──────────────────────────────┘
```

Total: **12 bytes on the wire**.

### 4.2 Response from panel → poller

The panel answers with the 16 bytes (8 registers × 2 bytes) representing live status:

```
Hex:        00 01  00 00  00 13  01  04  10  00 01 00 00 00 00 00 00 00 0A 01 00 00 0F 00 0A 01 00
            └──┬─┘ └──┬─┘ └──┬─┘ └┬┘ └┬┘ └┬┘  └────────────────────────────────────────────────────┘
TxID echo ─────┘     │     │     │   │   │    8 input registers, big-endian, 2 bytes each:
ProtoID = 0 ─────────┘     │     │   │   │
Length = 19 ───────────────┘     │   │   │      Reg0  FIRE_STATE         = 0x0001  → ALARM!
UnitID = 1 ──────────────────────┘   │   │      Reg1  SUPERVISORY_STATE  = 0x0000  → normal
FC = 4 echo ─────────────────────────┘   │      Reg2  POWER_STATE        = 0x0000  → mains
ByteCount = 16 (= 8 regs × 2) ───────────┘      Reg3  HEARTBEAT_HIGH     = 0x0000
                                                Reg4  HEARTBEAT_LOW      = 0x000A  → counter = 10
                                                Reg5  UPTIME_HIGH        = 0x0000
                                                Reg6  UPTIME_LOW         = 0x0010  → 16 s
                                                Reg7  FW_VERSION         = 0x0F00... wait no
```

Wait — let me redo the response cleanly so the bytes line up with the registers:

```
Response payload (16 bytes):

  00 01   00 00   00 00   00 00   00 0A   00 00   00 10   01 00
  ├───┤   ├───┤   ├───┤   ├───┤   ├───┤   ├───┤   ├───┤   ├───┤
  Reg0    Reg1    Reg2    Reg3    Reg4    Reg5    Reg6    Reg7
  FIRE    SUPER   POWER   HB_HI   HB_LO   UP_HI   UP_LO   FW_VER

  0x0001  0x0000  0x0000  0x0000  0x000A  0x0000  0x0010  0x0100

  ALARM   normal  mains   ┌─────────────┐  ┌─────────────┐  v1.0
                          │ heartbeat   │  │ uptime      │
                          │  = 10       │  │  = 16 s     │
                          └─────────────┘  └─────────────┘
```

The poller decodes this as: *"BLDG-042 has gone into fire alarm; MCU heartbeat is healthy at 10; panel has been up 16 seconds."*

---

## 5. The Exception Path — When the Server Says "No"

If the request is malformed (bad address, unsupported function), the server returns an **exception response**: same TxID, same UnitID, but the function code has bit 7 set, followed by a 1-byte exception code.

```
Normal response:     ... UnitID=01  FC=04        ByteCount  Data ...
Exception response:  ... UnitID=01  FC=84  ExCode
                                    └──┬─┘  └─┬──┘
                                       │     │
                            FC | 0x80 ─┘     └─ 01=ILLEGAL_FUNCTION
                                                02=ILLEGAL_DATA_ADDRESS
                                                03=ILLEGAL_DATA_VALUE
                                                04=SLAVE_DEVICE_FAILURE
                                                06=SLAVE_DEVICE_BUSY
```

**Important:** an exception is **not** a failure mode for the building — it just means the request was wrong. The poller should log it and **not** raise `BUILDING_OFFLINE`.

---

## 6. Function Code Cheat Sheet (used by fire-sim)

```
┌──────┬───────────────────────────────┬───────────┬─────────────────────────────┐
│  FC  │  Operation                    │ R/W       │  Used in this project for   │
├──────┼───────────────────────────────┼───────────┼─────────────────────────────┤
│  01  │  Read Coils                   │ Read      │  (not used)                 │
│  02  │  Read Discrete Inputs         │ Read      │  (not used)                 │
│  03  │  Read Holding Registers       │ Read      │  Identity / config (40001+) │
│  04  │  Read Input Registers         │ Read      │  Live status (30001+) ★     │
│  05  │  Write Single Coil            │ Write     │  (not used)                 │
│  06  │  Write Single Register        │ Write     │  Watchdog kick (40101)      │
│  15  │  Write Multiple Coils         │ Write     │  (not used)                 │
│  16  │  Write Multiple Registers     │ Write     │  (not used)                 │
└──────┴───────────────────────────────┴───────────┴─────────────────────────────┘
                                                       ★ = the hot path, every 1–2 s
```

---

## 7. Address Space Mental Model

Modbus addresses are notoriously confusing because of the historical "1-based with table prefix" convention. The simulator and our spec use **0-based offsets within each table** at the protocol level, but document **1-based, prefixed** addresses for human readers.

```
Human-friendly notation        Protocol notation (on-wire)
─────────────────────         ─────────────────────────────
Coils            0xxxx         FC 01 / 05 / 15, addr starts at 0
Discrete inputs  1xxxx         FC 02,           addr starts at 0
Input registers  3xxxx ◀─★     FC 04,           addr starts at 0
Holding regs     4xxxx         FC 03 / 06 / 16, addr starts at 0

Examples:
  "Read 30001"   →  FC=04, address=0x0000     (subtract 30001)
  "Read 30005"   →  FC=04, address=0x0004
  "Read 40101"   →  FC=06, address=0x0064 (write)
```

In code (pymodbus), you always use the **0-based offset**:
```python
# Read FIRE_STATE through FW_VERSION (registers 30001..30008)
rr = await client.read_input_registers(address=0, count=8)
```

---

## 8. State Machine of a Healthy Poller-Panel Connection

```
                    ┌────────────────┐
        startup     │ DISCONNECTED   │
        ──────────▶ │                │
                    └───────┬────────┘
                            │ open TCP
                            ▼
                    ┌────────────────┐
                    │  CONNECTING    │
                    └───┬────────┬───┘
                  fail  │        │  TCP established
              ┌─────────┘        ▼
              │           ┌────────────────┐
              │           │   CONNECTED    │◀────────────┐
              │           │   (idle)       │             │
              │           └───────┬────────┘             │
              │                   │ schedule poll        │
              │                   ▼                      │
              │           ┌────────────────┐             │
              │           │   POLLING      │             │
              │           └───┬────────┬───┘             │
              │       timeout │        │ response ok     │
              │   or exception│        └─────────────────┘
              │               │
              │               ▼
              │       ┌────────────────┐
              │       │  ERROR         │
              │       │ (count, retry) │
              │       └───┬────────┬───┘
              │           │        │
              │           │        └────────── recover ──┐
              │           ▼                              │
              │   ┌────────────────┐                     │
              │   │ CIRCUIT OPEN   │  emit               │
              │   │ (BLDG_OFFLINE) │  BUILDING_OFFLINE   │
              │   └───────┬────────┘                     │
              │           │ backoff timer                │
              └───────────┘ (1s → 2s → 4s … → 30s cap)   │
                                                         │
                  reconnect succeeds  ─────────────────  ┘
```

The poller's **circuit breaker** prevents one slow building from starving the others.

---

## 9. Where Modbus Stops and Our System Begins

Modbus is intentionally minimalist — it has **no** authentication, **no** encryption, **no** push notifications, and **no** time. Our architecture wraps it with everything it lacks:

```
┌──────────────────────────────────────────────────────────────────────┐
│                    Layer-by-layer responsibility                     │
├──────────────────────────────────────────────────────────────────────┤
│ Modbus TCP        → Just moves register values request-by-request    │
│ TLS over VPN      → Confidentiality + integrity (Modbus has none)    │
│ TCP keepalive     → Detect dead connections                          │
│ Poller heartbeat  → Detect frozen MCUs (Modbus can't tell you this)  │
│ NTP-synced poller → Authoritative timestamps (panel has no clock)    │
│ Broker (NATS)     → Push, fan-out, replay (Modbus is pull-only)      │
│ TimescaleDB       → History + queryable trends                       │
│ FastAPI + UI      → Operator workflow + acknowledgement + audit      │
└──────────────────────────────────────────────────────────────────────┘
```

This is why the register map design from §1 of the spec is as small as it is: keep the Modbus contract minimal and correct, then add reliability features in layers above.

---

## 10. See Also

- [`STM32_MODBUS_REGISTER_MAP.md`](./STM32_MODBUS_REGISTER_MAP.md) — exact register addresses and encodings
- [`fire-sim/src/fire_sim/registers.py`](../src/fire_sim/registers.py) — code constants
- [`fire-sim/src/fire_sim/loadtest.py`](../src/fire_sim/loadtest.py) — example poller using FC 04
- [Modbus.org — Application Protocol Specification v1.1b3](https://modbus.org/specs.php) (canonical reference)

---

*End of document.*
