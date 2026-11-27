# Fleet Dispatch System

![CI](https://github.com/srisanjanab/fleet-dispatch-system/actions/workflows/ci.yml/badge.svg)

A real-time delivery dispatch platform. Customers place orders, the system offers each order to the nearest available
driver, drivers accept or decline within a time limit, and everyone follows live positions on a map.

**Live demo: https://fleet-dispatch-web.onrender.com**

> The demo runs on free hosting, so it sleeps when unused. The first load after a pause can take about a minute.

## Try it

The admin view needs credentials that are not published here, but you can experience the full flow with two accounts:

1. Open the live demo in a normal window, click **Create one**, register as a **Delivery agent**, log in, click
   **Update location**, then **Go available**.
2. Open the demo in a **private window**, register as a **Customer**, log in and click **Place order**.
3. The agent window shows an offer with a countdown. Click **Accept**.
4. The customer window now shows a live tracking map with an ETA. The agent can then click **Picked up** and
   **Mark delivered**.

Try **Reject**, or ignore the offer for 30 seconds, to see the order move on to the next-nearest agent or wait in the queue.

## What it does

- **Three roles:** customers place and track orders, agents receive and answer offers, admins see the whole fleet.
- **Nearest-agent dispatch** using PostGIS spatial queries.
- **Offers with a 30-second timeout.** An agent can accept, reject or ignore. Rejected or expired offers automatically go
  to the next-nearest agent, and the same agent is never offered the same order twice.
- **Waiting queue.** If no agent is free, orders wait and are offered as soon as someone becomes available.
- **Full order lifecycle:** offered, assigned, picked up, delivered, with cancellation until pickup and an ETA shown to the customer.
- **Live map** of agent positions (WebSockets + Redis pub/sub). Customers only see the agent on their own order.
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

Correctness lives in the database (transactions, row locks, constraints). Speed lives in Redis, which only carries
live display updates. If Redis fails, dispatching keeps working and only the live map pauses.

## Key design decisions

**Preventing double assignment.** Two orders must never claim the same agent. Dispatch runs in one transaction using
`SELECT ... FOR UPDATE SKIP LOCKED`: if another request is already claiming an agent, that row is skipped and the
next-nearest agent is taken, so concurrent requests proceed in parallel instead of queueing.

**Fast nearest-agent lookup.** Agent positions live in a PostGIS `geography` column with a GiST index, and the query
uses `ORDER BY location <-> pickup LIMIT 1` (index-assisted nearest-neighbour search) instead of scanning every agent.

**Offers instead of instant assignment.** An order becomes an *offer* first, and only becomes an assignment when the
agent accepts. Database constraints enforce the rules: `UNIQUE (order_id, agent_id)` means an agent can never be
re-offered the same order, and a partial unique index allows at most one live offer per order.

**Accept versus expiry race.** If an agent taps Accept at the moment the timer runs out, the offer row is locked
(`FOR UPDATE`) and its status and deadline are checked using the database clock, so exactly one outcome wins.

**Deadlock avoidance.** Every code path (accept, reject, expire, cancel, dispatch) locks rows in the same order:
offers, then the order, then the agent. A consistent lock order is what prevents two transactions from waiting on each other.

**Live updates at scale.** Each location update is published once to Redis, and each WebSocket client subscribes on its
own, so the design keeps working if the API runs on several servers. The admin screen also refreshes from the database
periodically, because pub/sub does not store messages.

**Auth.** Passwords are hashed with bcrypt. Login returns a JWT carrying the role, which the server checks on every
request. Agents can only act on their own profile, and customers can only see their own orders. Wrong-email and
wrong-password responses are identical, so attackers cannot discover which emails exist.

**Safe by default in production.** With `ENVIRONMENT=production` the API refuses to start if the JWT secret or the
admin password are still the development defaults. Database tables are created automatically on first start,
guarded by an advisory lock so several servers booting at once do not collide.

## Tests and CI

23 automated tests run against a real PostgreSQL + PostGIS database (not mocks), because row locks, constraints and
spatial queries only exist in a real database. They cover authorization, nearest-agent choice, accept / reject / expiry,
the accept-versus-deadline race, the waiting queue, cancellation, the full order lifecycle, and a concurrency test
(50 simultaneous requests for 15 agents, verifying no agent is ever double-booked).

The concurrency test was validated by removing the `FOR UPDATE ... SKIP LOCKED` lock on purpose: without it, all 50
requests succeeded for 15 agents (real double-booking) and the test failed. With the lock restored it passes.

GitHub Actions runs the backend tests and a frontend build on every push (`.github/workflows/ci.yml`).

Run the tests locally with Docker Desktop running:

```bash
docker compose cp backend/tests backend:/code/
docker compose cp db backend:/code/
docker compose exec backend sh -c "pip install -q pytest==8.3.3 && python -m pytest -v"
```

## Run it locally

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
A development admin account is seeded: `admin@example.com` / `admin123` (development only; production refuses these defaults).

Watch the whole system run with simulated agents and orders:

```bash
pip install httpx websockets
python scripts/simulate_agents.py        # 10 moving agents, a new order every 8 seconds
```

## Deployment

The live demo runs on Render (API, Redis, website) with the database on Neon (Postgres with PostGIS).
`render.yaml` and [DEPLOY.md](DEPLOY.md) describe the setup step by step.

## Known limitations

- ETA is a heuristic (straight-line distance adjusted for roads, at an assumed city speed), not real road routing.
- Dispatch is greedy: each order takes the nearest free agent. Assigning several orders at once could reduce total pickup distance.
- Redis pub/sub does not keep messages, so a disconnected client misses events until the next refresh.
- Every WebSocket connection opens its own Redis subscription; at very large scale one subscription per server would be better.
- Every location ping is written to the database; at very large scale the latest position would live in Redis with batched history writes.
- There is no agent heartbeat, so an agent whose app dies keeps their last location until they go offline.
- Tokens are stored in browser localStorage and WebSocket tokens travel in the URL; a production system would use httpOnly cookies.
- The demo runs on free hosting that sleeps when unused.

## Tech stack

Python, FastAPI, asyncpg, PostgreSQL + PostGIS, Redis, WebSockets, JWT + bcrypt, React, Vite, Leaflet,
pytest, Docker Compose, GitHub Actions, Render, Neon.
