"""
Proves no agent is double-assigned under concurrent load.
Run after `docker compose up`:   python scripts/concurrency_test.py
Needs: pip install httpx
"""
import asyncio
import random
from collections import Counter

import httpx

BASE = "http://localhost:8000"
N_AGENTS = 20
N_ORDERS = 100  # more orders than agents on purpose -> some must get 409


async def main():
    async with httpx.AsyncClient(base_url=BASE, timeout=30) as c:
        # Bangalore-ish coordinates
        for i in range(N_AGENTS):
            a = (await c.post("/agents", json={"name": f"agent-{i}"})).json()
            await c.put(f"/agents/{a['id']}/location",
                        json={"lat": 12.9 + random.uniform(-0.05, 0.05),
                              "lng": 77.6 + random.uniform(-0.05, 0.05)})
            await c.patch(f"/agents/{a['id']}/status", json={"status": "available"})

        order_ids = []
        for i in range(N_ORDERS):
            o = (await c.post("/orders", json={
                "customer_name": f"cust-{i}",
                "pickup_lat": 12.9 + random.uniform(-0.05, 0.05),
                "pickup_lng": 77.6 + random.uniform(-0.05, 0.05),
                "dropoff_lat": 12.95, "dropoff_lng": 77.65,
            })).json()
            order_ids.append(o["id"])

        # Fire ALL assignments at the same time
        results = await asyncio.gather(*[c.post(f"/orders/{oid}/assign") for oid in order_ids])

    ok = [r.json() for r in results if r.status_code == 200]
    rejected = [r for r in results if r.status_code == 409]
    per_agent = Counter(r["agent_id"] for r in ok)
    doubles = {a: n for a, n in per_agent.items() if n > 1}

    print(f"Assigned: {len(ok)}  |  Rejected (no agent free): {len(rejected)}")
    print(f"Agents with >1 order (should be none): {doubles or 'NONE'}")
    assert not doubles, "DOUBLE ASSIGNMENT DETECTED"
    print("PASS: zero double-assignments under concurrent load")


asyncio.run(main())
