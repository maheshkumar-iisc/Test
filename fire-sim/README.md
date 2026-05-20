# fire-sim

Modbus TCP simulator that mimics 100 (or N) STM32-backed fire panels on a
single host, so you can develop and load-test the central poller / time-series
pipeline / dashboard without 100 real buildings.

Each simulated building runs its own Modbus TCP server on its own port,
exposes the same input-register map as the real firmware, ticks a heartbeat
counter once per second, and can be told to fail in realistic ways
(alarm, fault, MCU freeze, network offline).

---

## Register map (Modbus input registers, FC=4)

| 1-based | 0-based | Name              | Notes                                  |
|--------:|--------:|-------------------|----------------------------------------|
| 30001   | 0       | Fire state        | 0=normal, 1=alarm, 2=fault             |
| 30002   | 1       | Supervisory state | 0=normal, 1=trouble                    |
| 30003   | 2       | Power state       | 0=mains, 1=battery, 2=fail             |
| 30004   | 3       | Heartbeat low 16  | Increments every 1 s (when not frozen) |
| 30005   | 4       | Firmware version  | e.g. 0x0100 = v1.0                     |
| 30006   | 5       | Uptime low 16     | seconds                                |
| 30007   | 6       | Uptime high 16    | seconds                                |
| 30008   | 7       | Heartbeat high 16 | combined: 32-bit counter               |

---

## Quickstart (local)

```bash
cd fire-sim
python -m venv .venv && source .venv/bin/activate
pip install -e .

# Start 100 simulated panels (ports 5020-5119) + control API on :8080
fire-sim --buildings 100 --base-port 5020 --api-port 8080
```

In another terminal, poll all 100 with the bundled load-test client:

```bash
fire-sim-poll --buildings 100 --rounds 5 --interval 1.0
# [round 1/5] elapsed=0.18s ok=100 fail=0 p50=2.1ms p95=4.7ms p99=6.2ms max=8.0ms
```

---

## Docker

```bash
docker compose up --build
# control API:   http://localhost:8080
# Modbus panels: tcp://localhost:5020 .. tcp://localhost:5119
```

---

## Driving scenarios via the control API

```bash
# Trigger a fire alarm on building 42
curl -X POST localhost:8080/buildings/BLDG-042/fire \
     -H 'content-type: application/json' \
     -d '{"state":"alarm"}'

# Take a building offline (server stops listening)
curl -X POST localhost:8080/buildings/BLDG-007/offline \
     -H 'content-type: application/json' \
     -d '{"enabled":true}'

# Freeze an MCU (server up, heartbeat stops)
curl -X POST localhost:8080/buildings/BLDG-013/frozen \
     -H 'content-type: application/json' \
     -d '{"enabled":true}'

# Alarm storm: 25 random alarms at once
curl -X POST localhost:8080/scenarios/storm \
     -H 'content-type: application/json' -d '{"count":25}'

# Toggle continuous chaos mode (random failures + auto-recovery)
curl -X POST localhost:8080/scenarios/chaos \
     -H 'content-type: application/json' -d '{"enabled":true}'

# Reset the whole fleet to a healthy baseline
curl -X POST localhost:8080/scenarios/reset

# Discovery
curl localhost:8080/manifest
curl localhost:8080/buildings | jq '.[0]'
```

Full endpoint list:

| Method | Path                              | Body                       |
|-------:|-----------------------------------|----------------------------|
| GET    | `/health`                         | -                          |
| GET    | `/manifest`                       | -                          |
| GET    | `/buildings`                      | -                          |
| GET    | `/buildings/{id}`                 | -                          |
| POST   | `/buildings/{id}/fire`            | `{"state":"normal\|alarm\|fault"}` |
| POST   | `/buildings/{id}/supervisory`     | `{"state":"normal\|trouble"}`      |
| POST   | `/buildings/{id}/power`           | `{"state":"mains\|battery\|fail"}` |
| POST   | `/buildings/{id}/frozen`          | `{"enabled":true\|false}`         |
| POST   | `/buildings/{id}/offline`         | `{"enabled":true\|false}`         |
| POST   | `/scenarios/storm`                | `{"count":N}`              |
| POST   | `/scenarios/random-offline`       | `{"count":N}`              |
| POST   | `/scenarios/random-freeze`        | `{"count":N}`              |
| POST   | `/scenarios/chaos`                | `{"enabled":true\|false}`         |
| POST   | `/scenarios/reset`                | -                          |

---

## Failure modes you can exercise

| Real-world failure         | How to simulate                            | What the poller should do                                |
|----------------------------|--------------------------------------------|----------------------------------------------------------|
| Fire detected              | `POST .../fire {"state":"alarm"}`          | Emit `ALARM_STARTED`, alert ops                          |
| Panel trouble              | `POST .../supervisory {"state":"trouble"}` | Raise supervisory event (non-fire)                       |
| Mains lost / on battery    | `POST .../power {"state":"battery"}`       | Surface as warning, escalate after grace period          |
| MCU frozen / firmware hang | `POST .../frozen {"enabled":true}`         | Detect via stale heartbeat, mark `BUILDING_OFFLINE`      |
| Building network down      | `POST .../offline {"enabled":true}`        | Connect/read fails, mark `BUILDING_OFFLINE`              |
| Mass event                 | `POST /scenarios/storm {"count":25}`       | Handle without alarm-storm UI lockup                     |
| Soak-test                  | `POST /scenarios/chaos {"enabled":true}`   | Validate alert dedup, recovery, dashboard responsiveness |

---

## Tips

- Spin up 200 buildings? `--buildings 200 --base-port 5020`. You'll need
  to widen the Docker port range, or run without Docker.
- Need a building list for your poller? Use `--manifest-out manifest.json`
  or `GET /manifest`.
- The simulator answers Modbus reads even during chaos; only `offline`
  events stop the listening socket.
