# Fleet Dispatch System

![CI](https://github.com/srisanjanab/fleet-dispatch-system/actions/workflows/ci.yml/badge.svg)

A real-time delivery dispatch platform. Customers place orders, the system offers each order to the
nearest available driver, drivers accept or decline within a time limit, and everyone watches live
positions on a map.

> **Status:** core dispatch, live tracking, offers/reassignment and role-based auth are complete.
> See [What's not built yet](#whats-not-built-yet) for the honest list of what is still planned.

## What it does

- **Three roles:** customers place and track orders, agents receive and answer offers, admins see the whole fleet.
- **Nearest-agent dispatch** using PostGIS spatial queries.
- **Offers with a 30-second timeout.** An agent can accept, reject, or ignore. Rejected or expired offers
  automatically go to the next-nearest agent, and the same agent is never offered the same order twice.
- **Waiting queue.** If no agent is free, orders wait and are offered as soon as someone becomes available.
- **Full order lifecycle:** offered, assigned, picked up, delivered, with cancellation until pickup and an ETA shown to the customer.
- **Live map** of agent positions (WebSockets + Redis pub/sub). Customers see only the agent on their own order.
- **Fleet simulator** that moves agents, answers offers and creates orders, so the whole system can be watched running.

## Architecture

```
 React (Vite)  <-- HTTP/JSON + WebSocket -->  FastAPI  <--->  PostgreSQL + PostGIS
 customer / agent / admin screens                |
                                                 +--------->  Redis pub/sub  (live location + status events)
                                                 |
                                       background dispatcher loop
                                       (expires offers, offers waiting orders)
```

## Key design decisions

**Preventing double assignment.** Two orders must never claim the same agent. Dispatch runs in one
transaction using `SELECT ... FOR UPDATE SKIP LOCKED`: if another request is already claiming an agent,
that row is skipped and the next-nearest agent is taken. A script fires 100 simultaneous requests at 20 agents
and verifies zero agents are double-assigned.

**Fast nearest-agent lookup.** Agent positions live in a PostGIS `geography` column with a GiST index, and
the query uses `ORDER BY location <-> pickup LIMIT 1` (index-assisted nearest-neighbour search) instead of
scanning every agent.

**Offers instead of instant assignment.** An order becomes an *offer* first, and only becomes an assignment when
the agent accepts. Database constraints enforce the rules: `UNIQUE (order_id, agent_id)` means an agent can never
be re-offered the same order, and a partial unique index allows at most one live offer per order.

**Accept versus expiry race.** If an agent taps Accept at the same moment the timer runs out, the offer row is locked
(`FOR UPDATE`) and its status checked, so exactly one outcome wins.

**Live updates at scale.** Each location update is published once to Redis, and every WebSocket client subscribes
on its own. This keeps working if the API is later run on several servers, because a client on server A still
receives updates that arrived at server B. Publishing is best-effort: if Redis is down, dispatching still works.

**Auth.** Passwords are hashed with bcrypt. Login returns a JWT carrying the role, which the server checks on every
request. Agents can only act on their own profile, and customers can only see their own orders.

## Run it

Requirements: Docker Desktop and Node.js.

```bash
# backend, database, redis
docker compose up --build -d

# frontend (in a second terminal)
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. API docs are at http://localhost:8000/docs.
A development admin account is seeded: `admin@example.com` / `admin123` (change this for any real deployment).

### Watch the whole system run

```bash
pip install httpx websockets
python scripts/simulate_agents.py        # 10 moving agents, a new order every 8 seconds
```

### Verify the concurrency guarantee

```bash
python scripts/concurrency_test.py
```

## Tests and CI

23 automated tests run against a real PostgreSQL + PostGIS database (not mocks), because row locks, constraints and
spatial queries only exist in a real database. They cover authorization, nearest-agent choice, accept / reject / expiry,
the accept-versus-deadline race, the waiting queue, cancellation, the full order lifecycle, and a concurrency test
(50 simultaneous requests for 15 agents, verifying no agent is ever double-booked).

The concurrency test was checked by removing the `FOR UPDATE ... SKIP LOCKED` lock on purpose: without it, all 50
requests succeeded for 15 agents (real double-booking) and the test failed. With the lock restored it passes.

GitHub Actions runs the backend tests and a frontend build on every push (`.github/workflows/ci.yml`).

Run the tests locally (Docker Desktop running):

```powershell
.\scripts\run_tests.ps1
```

## Screenshots

<!-- Add your screenshots here: admin map, offer card, customer tracking map -->

## What's not built yet

- ETA based on real road routing (tracking currently shows straight-line distance)
- Batch dispatch (assigning several orders to several agents at once) and a benchmark against the current greedy approach
- Cloud deployment

## Tech stack

Python, FastAPI, asyncpg, PostgreSQL + PostGIS, Redis, WebSockets, React, Vite, Leaflet, Docker Compose.
