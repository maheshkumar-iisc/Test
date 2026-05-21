"""Owns and lifecycles a fleet of simulated buildings."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from .building import Building

log = logging.getLogger(__name__)


class Simulator:
    def __init__(self, buildings: list[Building]) -> None:
        self.buildings: dict[str, Building] = {b.id: b for b in buildings}

    @classmethod
    def create(cls, count: int, host: str = "0.0.0.0", base_port: int = 5020) -> "Simulator":
        buildings = [
            Building(building_id=f"BLDG-{i + 1:03d}", host=host, port=base_port + i)
            for i in range(count)
        ]
        return cls(buildings)

    async def start_all(self) -> None:
        # Start in parallel; each building binds its own port.
        await asyncio.gather(*(b.start() for b in self.buildings.values()))
        log.info(
            "Started %d buildings on ports %d-%d",
            len(self.buildings),
            min(b.port for b in self.buildings.values()),
            max(b.port for b in self.buildings.values()),
        )

    async def stop_all(self) -> None:
        await asyncio.gather(
            *(b.stop() for b in self.buildings.values()),
            return_exceptions=True,
        )
        log.info("Stopped %d buildings", len(self.buildings))

    def manifest(self) -> list[dict]:
        return [
            {"id": b.id, "host": b.host, "port": b.port}
            for b in self.buildings.values()
        ]

    def write_manifest(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.manifest(), indent=2))
        log.info("Wrote manifest with %d buildings to %s", len(self.buildings), path)
