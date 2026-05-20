"""CLI entrypoint: spin up N simulated fire panels and an HTTP control plane."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal

import uvicorn

from .chaos import Chaos
from .control_api import make_app
from .simulator import Simulator


async def amain(args: argparse.Namespace) -> None:
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
    )
    log = logging.getLogger("fire_sim")

    sim = Simulator.create(count=args.buildings, host=args.host, base_port=args.base_port)
    await sim.start_all()
    if args.manifest_out:
        sim.write_manifest(args.manifest_out)

    chaos = Chaos(sim)
    if args.chaos:
        await chaos.start()

    app = make_app(sim, chaos)
    api_config = uvicorn.Config(
        app,
        host=args.host,
        port=args.api_port,
        log_level=args.log_level.lower(),
        access_log=False,
    )
    api_server = uvicorn.Server(api_config)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            # Windows or restricted env - fall back to default handler.
            pass

    api_task = asyncio.create_task(api_server.serve(), name="control-api")

    log.info(
        "Fire-sim ready: %d buildings on %s:%d-%d | control API http://%s:%d",
        args.buildings,
        args.host,
        args.base_port,
        args.base_port + args.buildings - 1,
        args.host,
        args.api_port,
    )
    log.info("Press Ctrl-C to stop.")

    try:
        await stop_event.wait()
    finally:
        log.info("Shutting down...")
        api_server.should_exit = True
        try:
            await asyncio.wait_for(api_task, timeout=5.0)
        except asyncio.TimeoutError:
            api_task.cancel()
        await chaos.stop()
        await sim.stop_all()
        log.info("Shutdown complete.")


def main() -> None:
    p = argparse.ArgumentParser(
        description="Fire-panel Modbus TCP simulator (load-test up to ~100 buildings on one host)",
    )
    p.add_argument("--buildings", type=int, default=100, help="How many panels to simulate")
    p.add_argument("--host", default="0.0.0.0", help="Bind address for Modbus + API")
    p.add_argument("--base-port", type=int, default=5020, help="First Modbus TCP port; sim uses [base, base+N-1]")
    p.add_argument("--api-port", type=int, default=8080, help="HTTP control plane port")
    p.add_argument("--manifest-out", default=None, help="Write JSON {id,host,port} manifest to this file")
    p.add_argument("--chaos", action="store_true", help="Enable random failure injection on startup")
    p.add_argument("--log-level", default="info", choices=["debug", "info", "warning", "error"])
    args = p.parse_args()
    asyncio.run(amain(args))


if __name__ == "__main__":
    main()
