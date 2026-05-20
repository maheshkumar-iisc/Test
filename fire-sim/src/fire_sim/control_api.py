"""HTTP control plane for the simulator.

Lets you script scenarios from any client (curl, requests, k6, etc.):

    POST /buildings/BLDG-042/fire        {"state": "alarm"}
    POST /buildings/BLDG-042/offline     {"enabled": true}
    POST /scenarios/storm                {"count": 25}
    POST /scenarios/chaos                {"enabled": true}
    POST /scenarios/reset

The simulator is single-process so all state changes are immediate.
"""

from __future__ import annotations

import random

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .chaos import Chaos
from .registers import (
    FIRE_FROM_STR,
    POWER_FROM_STR,
    SUPER_FROM_STR,
    FireState,
    PowerState,
    SupervisoryState,
)
from .simulator import Simulator


class FireBody(BaseModel):
    state: str = Field(..., description="normal | alarm | fault")


class SuperBody(BaseModel):
    state: str = Field(..., description="normal | trouble")


class PowerBody(BaseModel):
    state: str = Field(..., description="mains | battery | fail")


class FlagBody(BaseModel):
    enabled: bool


class CountBody(BaseModel):
    count: int = Field(1, ge=1)


def make_app(sim: Simulator, chaos: Chaos) -> FastAPI:
    app = FastAPI(title="Fire-Sim Control Plane", version="0.1.0")

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------
    @app.get("/health")
    async def health():
        return {"status": "ok", "buildings": len(sim.buildings), "chaos": chaos.enabled}

    @app.get("/manifest")
    async def manifest():
        return {"buildings": sim.manifest()}

    @app.get("/buildings")
    async def list_buildings():
        return [b.snapshot() for b in sim.buildings.values()]

    @app.get("/buildings/{bid}")
    async def get_building(bid: str):
        b = sim.buildings.get(bid)
        if not b:
            raise HTTPException(status_code=404, detail=f"unknown building {bid}")
        return b.snapshot()

    # ------------------------------------------------------------------
    # Per-building state mutations
    # ------------------------------------------------------------------
    @app.post("/buildings/{bid}/fire")
    async def set_fire(bid: str, body: FireBody):
        b = sim.buildings.get(bid)
        if not b:
            raise HTTPException(404, f"unknown building {bid}")
        if body.state not in FIRE_FROM_STR:
            raise HTTPException(400, "state must be one of: normal, alarm, fault")
        await b.set_fire(FIRE_FROM_STR[body.state])
        return b.snapshot()

    @app.post("/buildings/{bid}/supervisory")
    async def set_supervisory(bid: str, body: SuperBody):
        b = sim.buildings.get(bid)
        if not b:
            raise HTTPException(404, f"unknown building {bid}")
        if body.state not in SUPER_FROM_STR:
            raise HTTPException(400, "state must be one of: normal, trouble")
        await b.set_supervisory(SUPER_FROM_STR[body.state])
        return b.snapshot()

    @app.post("/buildings/{bid}/power")
    async def set_power(bid: str, body: PowerBody):
        b = sim.buildings.get(bid)
        if not b:
            raise HTTPException(404, f"unknown building {bid}")
        if body.state not in POWER_FROM_STR:
            raise HTTPException(400, "state must be one of: mains, battery, fail")
        await b.set_power(POWER_FROM_STR[body.state])
        return b.snapshot()

    @app.post("/buildings/{bid}/frozen")
    async def set_frozen(bid: str, body: FlagBody):
        b = sim.buildings.get(bid)
        if not b:
            raise HTTPException(404, f"unknown building {bid}")
        await b.set_frozen(body.enabled)
        return b.snapshot()

    @app.post("/buildings/{bid}/offline")
    async def set_offline(bid: str, body: FlagBody):
        b = sim.buildings.get(bid)
        if not b:
            raise HTTPException(404, f"unknown building {bid}")
        await b.set_offline(body.enabled)
        return b.snapshot()

    # ------------------------------------------------------------------
    # Bulk scenarios
    # ------------------------------------------------------------------
    @app.post("/scenarios/storm")
    async def storm(body: CountBody):
        """Trigger fire alarms on N random buildings simultaneously."""
        ids = random.sample(
            list(sim.buildings.keys()),
            min(body.count, len(sim.buildings)),
        )
        for bid in ids:
            await sim.buildings[bid].set_fire(FireState.ALARM)
        return {"alarmed": ids, "count": len(ids)}

    @app.post("/scenarios/random-offline")
    async def random_offline(body: CountBody):
        """Take N random buildings offline (drop their Modbus servers)."""
        ids = random.sample(
            list(sim.buildings.keys()),
            min(body.count, len(sim.buildings)),
        )
        for bid in ids:
            await sim.buildings[bid].set_offline(True)
        return {"offline": ids, "count": len(ids)}

    @app.post("/scenarios/random-freeze")
    async def random_freeze(body: CountBody):
        """Freeze N random MCUs (server up, heartbeat stops)."""
        ids = random.sample(
            list(sim.buildings.keys()),
            min(body.count, len(sim.buildings)),
        )
        for bid in ids:
            await sim.buildings[bid].set_frozen(True)
        return {"frozen": ids, "count": len(ids)}

    @app.post("/scenarios/chaos")
    async def toggle_chaos(body: FlagBody):
        if body.enabled:
            await chaos.start()
        else:
            await chaos.stop()
        return {"chaos": chaos.enabled}

    @app.post("/scenarios/reset")
    async def reset_all():
        """Restore every building to a clean baseline."""
        await chaos.stop()
        for b in sim.buildings.values():
            await b.set_offline(False)
            await b.set_frozen(False)
            await b.set_fire(FireState.NORMAL)
            await b.set_supervisory(SupervisoryState.NORMAL)
            await b.set_power(PowerState.MAINS)
        return {"reset": len(sim.buildings)}

    return app
