# =============================================================================
# main.py  —  Wires everything together and runs the polling loop.
#
# WHAT THIS FILE DOES:
#   1. Sets up logging.
#   2. Creates one BuildingState object per building (from config.BUILDINGS).
#   3. Every POLL_INTERVAL_S seconds, polls every building concurrently
#      using asyncio.
#   4. Logs every alarm, fault, power change, or offline event.
#   5. (Placeholder) Shows where to add a database write or NATS publish.
#
# HOW TO RUN:
#   pip install pymodbus fastapi uvicorn
#   python main.py
#
# HOW TO RUN AGAINST THE SIMULATOR:
#   # Terminal 1 — start the simulator
#   fire-sim --buildings 3 --base-port 5020 --api-port 9090
#
#   # Terminal 2 — run the poller
#   python main.py
# =============================================================================

import asyncio
import logging
import signal

import config
from poller import BuildingState, poll_building

# ---------------------------------------------------------------------------
# Logging setup  (one line — readable in terminal, easy to redirect to a file)
# ---------------------------------------------------------------------------
logging.basicConfig(level=config.LOG_LEVEL, format=config.LOG_FORMAT)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Async poll loop for ONE building
# ---------------------------------------------------------------------------

async def poll_loop(state: BuildingState) -> None:
    """
    Infinite loop for one building.
    Polls on every POLL_INTERVAL_S tick, independently of all others.
    """
    log.info("[%s] Poll loop started on %s:%d", state.building_id, state.host, state.port)

    while True:
        try:
            # poll_building() is synchronous (uses pymodbus sync client).
            # run_in_executor() stops it from blocking the event loop,
            # so all 100 buildings really do poll at the same time.
            loop   = asyncio.get_running_loop()
            result = await loop.run_in_executor(None, poll_building, state)

            if result:
                _handle_result(result)

        except asyncio.CancelledError:
            log.info("[%s] Poll loop stopped.", state.building_id)
            break

        except Exception as exc:
            # Safety net — loop must never die silently
            log.exception("[%s] Unexpected error: %s", state.building_id, exc)

        await asyncio.sleep(config.POLL_INTERVAL_S)


# ---------------------------------------------------------------------------
# Result handler  —  called after every successful poll
# ---------------------------------------------------------------------------

def _handle_result(result: dict) -> None:
    """
    Decide what to do with each reading.

    Right now it just logs noteworthy events.
    Later you will add:
        _save_to_database(result)
        _publish_to_nats(result)
    """
    bid = result["building_id"]

    # ── Log transitions ───────────────────────────────────────────────── #
    if result["fire_changed"]:
        log.warning("[%s] >>> FIRE: %s", bid, result["fire_label"])

    if result["power_changed"]:
        log.warning("[%s] >>> POWER: %s", bid, result["power_label"])

    if result["heartbeat_frozen"]:
        log.error("[%s] >>> HEARTBEAT FROZEN — MCU may be hung", bid)

    # ── Periodic status line (every 10 polls, approx 10 s) ────────────── #
    # (Replace with a real dashboard later.)
    hb = result["heartbeat"]
    if hb % 10 == 0:
        log.info(
            "[%s] status | fire=%-8s power=%-7s hb=%d uptime=%ds lat=%.1fms",
            bid,
            result["fire_label"],
            result["power_label"],
            hb,
            result["uptime_s"],
            result["latency_ms"],
        )

    # ── TODO: save to database ────────────────────────────────────────── #
    # _save_to_database(result)

    # ── TODO: publish to message broker (NATS / Kafka) ───────────────── #
    # await publisher.publish(result)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    # Build one state object per building
    states = [
        BuildingState(b["id"], b["host"], b["port"])
        for b in config.BUILDINGS
    ]

    log.info("Starting fire poller — %d building(s)", len(states))

    # Launch all poll loops concurrently
    tasks = [asyncio.create_task(poll_loop(s), name=s.building_id) for s in states]

    # Graceful shutdown on Ctrl-C or SIGTERM
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass   # Windows doesn't support add_signal_handler

    await stop.wait()

    log.info("Shutting down — cancelling poll loops...")
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    log.info("Shutdown complete.")


if __name__ == "__main__":
    asyncio.run(main())
