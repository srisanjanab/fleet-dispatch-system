"""
Moves N fake agents around Bangalore so you can watch the live map.

    pip install httpx websockets
    python scripts/simulate_agents.py        # 10 agents
    python scripts/simulate_agents.py 25     # 25 agents

Logs in as admin, creates (or reuses) agents named sim-0, sim-1, ..., marks them available,
then streams a random walk over WebSocket every 2 seconds. Press Ctrl+C to stop.
"""
import asyncio
import json
import random
import sys

import httpx
import websockets

HTTP = "http://localhost:8000"
WS = "ws://localhost:8000"
CENTER = (12.9716, 77.5946)
TICK_SECONDS = 2


async def move(agent_id: int, token: str):
    lat = CENTER[0] + random.uniform(-0.04, 0.04)
    lng = CENTER[1] + random.uniform(-0.04, 0.04)
    async with websockets.connect(f"{WS}/ws/agents/{agent_id}?token={token}") as ws:
        while True:
            lat += random.uniform(-0.0006, 0.0006)
            lng += random.uniform(-0.0006, 0.0006)
            await ws.send(json.dumps({"lat": lat, "lng": lng}))
            await ws.recv()  # wait for the server's ack
            await asyncio.sleep(TICK_SECONDS)


async def main(n: int):
    async with httpx.AsyncClient(base_url=HTTP, timeout=30) as c:
        login = await c.post("/auth/login", json={"email": "admin@example.com", "password": "admin123"})
        login.raise_for_status()
        token = login.json()["access_token"]
        c.headers["Authorization"] = f"Bearer {token}"

        existing = {a["name"]: a["id"] for a in (await c.get("/agents")).json() if a["name"].startswith("sim-")}
        ids = []
        for i in range(n):
            name = f"sim-{i}"
            agent_id = existing.get(name) or (await c.post("/agents", json={"name": name})).json()["id"]
            ids.append(agent_id)

        # Give every agent a starting position, then make them available
        for agent_id in ids:
            await c.put(f"/agents/{agent_id}/location",
                        json={"lat": CENTER[0] + random.uniform(-0.04, 0.04),
                              "lng": CENTER[1] + random.uniform(-0.04, 0.04)})
            await c.patch(f"/agents/{agent_id}/status", json={"status": "available"})

    print(f"{n} agents moving. Open the admin screen to watch. Ctrl+C to stop.")
    await asyncio.gather(*[move(i, token) for i in ids])


try:
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 10))
except KeyboardInterrupt:
    print("Stopped.")
