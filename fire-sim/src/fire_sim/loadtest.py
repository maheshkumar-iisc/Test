"""Async Modbus client that polls every simulated building once and reports
connect rate, error rate, and p50/p99 read latency.

This stands in for a future production poller. Use it to validate the
simulator can hold up under realistic concurrency.

Examples
--------
    fire-sim-poll --buildings 100 --base-port 5020
    fire-sim-poll --buildings 100 --rounds 10 --interval 1.0
"""

from __future__ import annotations

import argparse
import asyncio
import time

from pymodbus.client import AsyncModbusTcpClient

from .registers import NUM_REGS, REG_FIRE, REG_HEARTBEAT_HIGH, REG_HEARTBEAT_LOW


async def poll_one(host: str, port: int, building_id: str, timeout: float) -> dict:
    client = AsyncModbusTcpClient(host, port=port, timeout=timeout)
    try:
        connected = await client.connect()
        if not connected:
            return {"id": building_id, "ok": False, "lat_ms": None, "err": "connect_failed"}

        t0 = time.monotonic()
        rr = await client.read_input_registers(address=0, count=NUM_REGS)
        lat_ms = (time.monotonic() - t0) * 1000.0

        if rr.isError():
            return {"id": building_id, "ok": False, "lat_ms": lat_ms, "err": str(rr)}

        regs = rr.registers
        heartbeat = (regs[REG_HEARTBEAT_HIGH] << 16) | regs[REG_HEARTBEAT_LOW]
        return {
            "id": building_id,
            "ok": True,
            "lat_ms": lat_ms,
            "fire": regs[REG_FIRE],
            "heartbeat": heartbeat,
        }
    except Exception as e:  # noqa: BLE001
        return {"id": building_id, "ok": False, "lat_ms": None, "err": repr(e)}
    finally:
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = min(len(s) - 1, max(0, int(round(p * (len(s) - 1)))))
    return s[idx]


async def run_round(host: str, base_port: int, count: int, timeout: float) -> dict:
    tasks = [
        poll_one(host, base_port + i, f"BLDG-{i + 1:03d}", timeout)
        for i in range(count)
    ]
    t0 = time.monotonic()
    results = await asyncio.gather(*tasks)
    elapsed = time.monotonic() - t0

    ok = [r for r in results if r["ok"]]
    bad = [r for r in results if not r["ok"]]
    lats = [r["lat_ms"] for r in results if r["lat_ms"] is not None]

    return {
        "elapsed_s": elapsed,
        "ok": len(ok),
        "fail": len(bad),
        "p50_ms": _percentile(lats, 0.50),
        "p95_ms": _percentile(lats, 0.95),
        "p99_ms": _percentile(lats, 0.99),
        "max_ms": max(lats) if lats else 0.0,
        "errors": bad[:5],
    }


async def amain(args: argparse.Namespace) -> None:
    print(
        f"Polling {args.buildings} buildings on {args.host}:{args.base_port}+ "
        f"({args.rounds} round(s), interval {args.interval}s, timeout {args.timeout}s)"
    )
    for i in range(args.rounds):
        summary = await run_round(args.host, args.base_port, args.buildings, args.timeout)
        print(
            f"[round {i + 1}/{args.rounds}] elapsed={summary['elapsed_s']:.2f}s "
            f"ok={summary['ok']} fail={summary['fail']} "
            f"p50={summary['p50_ms']:.1f}ms "
            f"p95={summary['p95_ms']:.1f}ms "
            f"p99={summary['p99_ms']:.1f}ms "
            f"max={summary['max_ms']:.1f}ms"
        )
        if summary["errors"]:
            for e in summary["errors"]:
                print(f"    ! {e['id']}: {e.get('err')}")
        if i < args.rounds - 1:
            await asyncio.sleep(args.interval)


def main() -> None:
    p = argparse.ArgumentParser(description="Poll all simulated fire panels and report stats")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--base-port", type=int, default=5020)
    p.add_argument("--buildings", type=int, default=100)
    p.add_argument("--rounds", type=int, default=1, help="Number of polling rounds")
    p.add_argument("--interval", type=float, default=1.0, help="Seconds between rounds")
    p.add_argument("--timeout", type=float, default=2.0, help="Per-request timeout (s)")
    args = p.parse_args()
    asyncio.run(amain(args))


if __name__ == "__main__":
    main()
