"""
Simulates a working fleet so you can watch the whole system run.

    pip install httpx websockets
    python scripts/simulate_agents.py                 # 10 agents, a new order every 8 seconds
    python scripts/simulate_agents.py 25 5            # 25 agents, a new order every 5 seconds
    python scripts/simulate_agents.py 10 0            # agents only, you place the orders yourself

Every sim agent moves around Bangalore. When an offer arrives for a sim agent it randomly:
  65% accepts (then "delivers" 10-20 seconds later), 20% rejects, 15% ignores it (so it expires).
Ctrl+C stops everything.
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


def rand_point(spread=0.04):
    return CENTER[0] + random.uniform(-spread, spread), CENTER[1] + random.uniform(-spread, spread)


async def move(agent_id: int, token: str):
    lat, lng = rand_point()
    async with websockets.connect(f"{WS}/ws/agents/{agent_id}?token={token}") as ws:
        while True:
            lat += random.uniform(-0.0006, 0.0006)
            lng += random.uniform(-0.0006, 0.0006)
            await ws.send(json.dumps({"lat": lat, "lng": lng}))
            await ws.recv()
            await asyncio.sleep(TICK_SECONDS)


async def respond(c: httpx.AsyncClient, offer: dict):
    name, oid = offer["agent_name"], offer["order_id"]
    roll = random.random()
    await asyncio.sleep(random.uniform(1, 4))  # a human needs a moment to react
    try:
        if roll < 0.65:
            r = await c.post(f"/offers/{offer['id']}/accept")
            if r.status_code != 200:
                print(f"  order #{oid}: {name} answered too late ({r.json().get('detail')})")
                return
            print(f"  order #{oid}: {name} ACCEPTED")
            await asyncio.sleep(random.uniform(10, 20))
            await c.post(f"/orders/{oid}/complete")
            print(f"  order #{oid}: DELIVERED by {name}")
        elif roll < 0.85:
            await c.post(f"/offers/{offer['id']}/reject")
            print(f"  order #{oid}: {name} REJECTED, reassigning")
        else:
            print(f"  order #{oid}: {name} is ignoring the offer, it will expire")
    except Exception as err:
        print(f"  (simulator error on order #{oid}: {err})")


async def watch_offers(c: httpx.AsyncClient, sim_ids: set):
    handled = set()
    while True:
        try:
            for offer in (await c.get("/offers/pending")).json():
                if offer["agent_id"] in sim_ids and offer["id"] not in handled:
                    handled.add(offer["id"])
                    asyncio.create_task(respond(c, offer))
        except Exception as err:
            print(f"  (poll error: {err})")
        await asyncio.sleep(1)


async def order_stream(c: httpx.AsyncClient, interval: float):
    while True:
        await asyncio.sleep(interval)
        try:
            (plat, plng), (dlat, dlng) = rand_point(), rand_point()
            r = await c.post("/orders", json={
                "customer_name": "sim-customer",
                "pickup_lat": plat, "pickup_lng": plng, "dropoff_lat": dlat, "dropoff_lng": dlng,
            })
            print(f"new order #{r.json()['id']}")
        except Exception as err:
            print(f"  (order error: {err})")


async def main(n: int, order_interval: float):
    async with httpx.AsyncClient(base_url=HTTP, timeout=30) as c:
        login = await c.post("/auth/login", json={"email": "admin@example.com", "password": "admin123"})
        login.raise_for_status()
        token = login.json()["access_token"]
        c.headers["Authorization"] = f"Bearer {token}"

        existing = {a["name"]: a["id"] for a in (await c.get("/agents")).json() if a["name"].startswith("sim-")}
        ids = []
        for i in range(n):
            name = f"sim-{i}"
            ids.append(existing.get(name) or (await c.post("/agents", json={"name": name})).json()["id"])
        sim_ids = set(ids)

        # Clean slate: finish any order a sim agent was left holding, so they aren't stuck "busy"
        for o in (await c.get("/orders")).json():
            if o["status"] == "assigned" and o["agent_id"] in sim_ids:
                await c.post(f"/orders/{o['id']}/complete")

        for agent_id in ids:
            lat, lng = rand_point()
            await c.put(f"/agents/{agent_id}/location", json={"lat": lat, "lng": lng})
            await c.patch(f"/agents/{agent_id}/status", json={"status": "available"})

        print(f"{n} agents running. Open the admin and agent screens to watch. Ctrl+C to stop.\n")
        tasks = [move(i, token) for i in ids] + [watch_offers(c, sim_ids)]
        if order_interval > 0:
            tasks.append(order_stream(c, order_interval))
        await asyncio.gather(*tasks)


try:
    n_agents = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    interval = float(sys.argv[2]) if len(sys.argv) > 2 else 8
    asyncio.run(main(n_agents, interval))
except KeyboardInterrupt:
    print("Stopped.")
