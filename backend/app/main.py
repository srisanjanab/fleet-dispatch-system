import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager

import asyncpg
import jwt
import redis.asyncio as aioredis
from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from .auth import (
    create_token, current_user, decode_token, ensure_self_or_admin,
    hash_password, require_roles, verify_password,
)

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://dispatch:dispatch@localhost:5432/dispatch")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@example.com")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")  # dev default only
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
CHANNEL = os.getenv("EVENT_CHANNEL", "agent_events")  # Redis pub/sub channel for live agent updates
logger = logging.getLogger("dispatch")
OFFER_TIMEOUT = int(os.getenv("OFFER_TIMEOUT_SECONDS", "30"))   # how long an agent has to accept
DISPATCH_INTERVAL = float(os.getenv("DISPATCH_INTERVAL_SECONDS", "2"))
AUTO_DISPATCH = os.getenv("AUTO_DISPATCH", "true").lower() == "true"

USERS_DDL = """
CREATE TABLE IF NOT EXISTS users (
    id            SERIAL PRIMARY KEY,
    email         TEXT NOT NULL UNIQUE,
    name          TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('customer', 'agent', 'admin')),
    agent_id      INT REFERENCES agents(id),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE orders ADD COLUMN IF NOT EXISTS customer_id INT REFERENCES users(id);

-- Week 4: an order is first OFFERED to an agent; it becomes an assignment only when they accept.
ALTER TABLE orders DROP CONSTRAINT IF EXISTS orders_status_check;
ALTER TABLE orders ADD CONSTRAINT orders_status_check
    CHECK (status IN ('created', 'offered', 'assigned', 'picked_up', 'delivered', 'cancelled'));

CREATE TABLE IF NOT EXISTS offers (
    id          SERIAL PRIMARY KEY,
    order_id    INT NOT NULL REFERENCES orders(id),
    agent_id    INT NOT NULL REFERENCES agents(id),
    status      TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'accepted', 'rejected', 'expired')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ NOT NULL,
    UNIQUE (order_id, agent_id)  -- the same agent is never offered the same order twice
);
-- At most one live offer per order, enforced by the database itself
CREATE UNIQUE INDEX IF NOT EXISTS one_pending_offer_per_order ON offers (order_id) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_offers_pending_expiry ON offers (expires_at) WHERE status = 'pending';
"""


async def connect_with_retry():
    """Wait for Postgres instead of crashing if it is still starting (common right after Docker boots)."""
    for attempt in range(1, 31):
        try:
            return await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=20)
        except (OSError, asyncpg.PostgresError) as err:
            logger.warning("Database not ready (attempt %d/30): %s", attempt, err)
            await asyncio.sleep(2)
    raise RuntimeError("Could not connect to the database after 30 attempts")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pool = await connect_with_retry()
    app.state.redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    async with app.state.pool.acquire() as conn:
        await conn.execute(USERS_DDL)  # idempotent: safe on every startup
        exists = await conn.fetchval("SELECT 1 FROM users WHERE email=$1", ADMIN_EMAIL)
        if not exists:
            await conn.execute(
                "INSERT INTO users (email, name, password_hash, role) VALUES ($1, 'Admin', $2, 'admin')",
                ADMIN_EMAIL, hash_password(ADMIN_PASSWORD),
            )
    dispatcher = asyncio.create_task(dispatcher_loop()) if AUTO_DISPATCH else None
    yield
    if dispatcher:
        dispatcher.cancel()
    await app.state.redis.aclose()
    await app.state.pool.close()


app = FastAPI(title="Fleet Dispatch System", lifespan=lifespan)


async def publish(event: dict) -> None:
    """Best-effort live update. A Redis hiccup must never break dispatching itself."""
    try:
        await app.state.redis.publish(CHANNEL, json.dumps(event))
    except Exception:
        logger.warning("Failed to publish live event", exc_info=True)


async def stream_events(ws: WebSocket, allow) -> None:
    """
    Forward Redis pub/sub events to one WebSocket client until either side goes away.
    `allow(event)` decides whether this client is permitted to see an event.
    Each connection gets its own Redis subscription, so this works across several API servers.
    """
    pubsub = app.state.redis.pubsub()
    await pubsub.subscribe(CHANNEL)

    async def forward():
        try:
            async for msg in pubsub.listen():
                if msg["type"] != "message":
                    continue
                event = json.loads(msg["data"])
                if allow(event):
                    await ws.send_json(event)
        except Exception:
            pass  # client went away mid-send; the other task ends the session

    async def watch_disconnect():
        try:
            while True:
                await ws.receive_text()  # we ignore content; this just notices the disconnect
        except WebSocketDisconnect:
            pass

    tasks = [asyncio.create_task(forward()), asyncio.create_task(watch_disconnect())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
        await pubsub.unsubscribe(CHANNEL)
        await pubsub.aclose()

EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


# ---------- Dispatch engine ----------
async def dispatch_order(conn, order_id: int):
    """
    Offer one waiting order to the nearest available agent who has not already been offered it.
    MUST run inside a transaction. Returns an offer dict, or "not_waiting" / "no_agent".

    - FOR UPDATE SKIP LOCKED on the order : if another server is already dispatching it, skip it
    - ORDER BY location <-> pickup        : nearest-first using the GiST index
    - FOR UPDATE SKIP LOCKED on the agent : two orders can never claim the same agent at once
    - NOT EXISTS (offers ...)             : never offer the same order to the same agent twice
    """
    order = await conn.fetchrow(
        "SELECT id FROM orders WHERE id=$1 AND status='created' FOR UPDATE SKIP LOCKED", order_id
    )
    if not order:
        return "not_waiting"

    agent = await conn.fetchrow(
        """SELECT a.id, a.name
           FROM agents a, orders o
           WHERE o.id = $1
             AND a.status = 'available'
             AND a.location IS NOT NULL
             AND NOT EXISTS (SELECT 1 FROM offers f WHERE f.order_id = o.id AND f.agent_id = a.id)
           ORDER BY a.location <-> o.pickup
           LIMIT 1
           FOR UPDATE OF a SKIP LOCKED""",
        order_id,
    )
    if not agent:
        return "no_agent"

    await conn.execute("UPDATE agents SET status='busy', updated_at=now() WHERE id=$1", agent["id"])
    await conn.execute("UPDATE orders SET status='offered' WHERE id=$1", order_id)
    offer_id = await conn.fetchval(
        """INSERT INTO offers (order_id, agent_id, expires_at)
           VALUES ($1, $2, now() + make_interval(secs => $3)) RETURNING id""",
        order_id, agent["id"], float(OFFER_TIMEOUT),
    )
    return {"offer_id": offer_id, "order_id": order_id, "agent_id": agent["id"], "agent_name": agent["name"]}


async def release_offer(conn, offer, new_status: str) -> None:
    """Close an offer (rejected / expired): the order goes back in the queue and the agent is free again."""
    await conn.execute("UPDATE offers SET status=$2 WHERE id=$1", offer["id"], new_status)
    await conn.execute("UPDATE orders SET status='created' WHERE id=$1 AND status='offered'", offer["order_id"])
    await conn.execute(
        "UPDATE agents SET status='available', updated_at=now() WHERE id=$1 AND status='busy'",
        offer["agent_id"],
    )


async def announce_offer(o: dict) -> None:
    await publish({"type": "status", "agent_id": o["agent_id"], "status": "busy"})
    await publish({"type": "order", "order_id": o["order_id"], "status": "offered"})
    await publish({"type": "offer", "agent_id": o["agent_id"], "offer_id": o["offer_id"],
                   "order_id": o["order_id"], "state": "pending"})


async def announce_release(offer, new_status: str) -> None:
    await publish({"type": "status", "agent_id": offer["agent_id"], "status": "available"})
    await publish({"type": "order", "order_id": offer["order_id"], "status": "created"})
    await publish({"type": "offer", "agent_id": offer["agent_id"], "offer_id": offer["id"],
                   "order_id": offer["order_id"], "state": new_status})


async def dispatch_in_tx(order_id: int):
    async with app.state.pool.acquire() as conn:
        async with conn.transaction():
            result = await dispatch_order(conn, order_id)
    if isinstance(result, dict):
        await announce_offer(result)
    return result


async def expire_offers() -> None:
    """Offers nobody answered in time go back to the queue. SKIP LOCKED lets several servers run this safely."""
    async with app.state.pool.acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                """SELECT id, order_id, agent_id FROM offers
                   WHERE status='pending' AND expires_at <= now()
                   FOR UPDATE SKIP LOCKED"""
            )
            for r in rows:
                await release_offer(conn, r, "expired")
    for r in rows:
        await announce_release(r, "expired")


async def dispatch_waiting() -> None:
    """Try to offer every waiting order (oldest first). This is the waiting queue."""
    free = await app.state.pool.fetchval(
        "SELECT count(*) FROM agents WHERE status='available' AND location IS NOT NULL"
    )
    if not free:
        return
    waiting = await app.state.pool.fetch("SELECT id FROM orders WHERE status='created' ORDER BY id LIMIT 50")
    for row in waiting:
        await dispatch_in_tx(row["id"])


async def dispatcher_loop() -> None:
    while True:
        try:
            await expire_offers()
            await dispatch_waiting()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Dispatcher tick failed; will retry")
        await asyncio.sleep(DISPATCH_INTERVAL)


# ---------- Schemas ----------
class RegisterBody(BaseModel):
    email: str = Field(pattern=EMAIL_PATTERN)
    name: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=6, max_length=72)  # bcrypt only reads the first 72 bytes
    role: str = Field(pattern="^(customer|agent)$")     # admins are seeded, never self-registered


class LoginBody(BaseModel):
    email: str
    password: str


class AgentCreate(BaseModel):
    name: str


class StatusUpdate(BaseModel):
    status: str = Field(pattern="^(offline|available|busy)$")


class LocationUpdate(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


class OrderCreate(BaseModel):
    customer_name: str | None = None
    pickup_lat: float = Field(ge=-90, le=90)
    pickup_lng: float = Field(ge=-180, le=180)
    dropoff_lat: float = Field(ge=-90, le=90)
    dropoff_lng: float = Field(ge=-180, le=180)


# NOTE: PostGIS points are (lng, lat) -- x first, y second. Classic bug source.
def point(lng_param: str, lat_param: str) -> str:
    return f"ST_SetSRID(ST_MakePoint({lng_param}, {lat_param}), 4326)::geography"


# ---------- Health ----------
@app.get("/health")
async def health():
    async with app.state.pool.acquire() as conn:
        await conn.fetchval("SELECT 1")
    return {"status": "ok"}


# ---------- Auth ----------
@app.post("/auth/register", status_code=201)
async def register(body: RegisterBody):
    async with app.state.pool.acquire() as conn:
        async with conn.transaction():
            agent_id = None
            if body.role == "agent":
                agent_id = await conn.fetchval("INSERT INTO agents (name) VALUES ($1) RETURNING id", body.name)
            try:
                user_id = await conn.fetchval(
                    """INSERT INTO users (email, name, password_hash, role, agent_id)
                       VALUES ($1, $2, $3, $4, $5) RETURNING id""",
                    body.email.lower(), body.name, hash_password(body.password), body.role, agent_id,
                )
            except asyncpg.UniqueViolationError:
                raise HTTPException(409, "An account with this email already exists")
    return {"id": user_id, "role": body.role}


@app.post("/auth/login")
async def login(body: LoginBody):
    row = await app.state.pool.fetchrow(
        "SELECT id, name, password_hash, role, agent_id FROM users WHERE email=$1", body.email.lower()
    )
    # Same message for "no such user" and "wrong password" so attackers can't probe which emails exist
    if not row or not verify_password(body.password, row["password_hash"]):
        raise HTTPException(401, "Wrong email or password")
    return {
        "access_token": create_token(row["id"], row["role"], row["agent_id"]),
        "token_type": "bearer",
        "role": row["role"],
        "name": row["name"],
        "agent_id": row["agent_id"],
    }


@app.get("/auth/me")
async def me(user: dict = Depends(current_user)):
    return user


# ---------- Agents ----------
@app.post("/agents", status_code=201)
async def create_agent(body: AgentCreate, user: dict = Depends(require_roles("admin"))):
    row = await app.state.pool.fetchrow(
        "INSERT INTO agents (name) VALUES ($1) RETURNING id, name, status", body.name
    )
    return dict(row)


@app.get("/agents")
async def list_agents(user: dict = Depends(require_roles("admin"))):
    rows = await app.state.pool.fetch(
        """SELECT id, name, status,
                  ST_Y(location::geometry) AS lat, ST_X(location::geometry) AS lng
           FROM agents ORDER BY id"""
    )
    return [dict(r) for r in rows]


@app.get("/agents/me")
async def my_agent(user: dict = Depends(require_roles("agent"))):
    row = await app.state.pool.fetchrow(
        """SELECT id, name, status,
                  ST_Y(location::geometry) AS lat, ST_X(location::geometry) AS lng
           FROM agents WHERE id=$1""",
        user["agent_id"],
    )
    return dict(row)


@app.patch("/agents/{agent_id}/status")
async def set_agent_status(agent_id: int, body: StatusUpdate, user: dict = Depends(current_user)):
    ensure_self_or_admin(user, agent_id)
    row = await app.state.pool.fetchrow(
        "UPDATE agents SET status=$2, updated_at=now() WHERE id=$1 RETURNING id, name, status",
        agent_id, body.status,
    )
    if not row:
        raise HTTPException(404, "Agent not found")
    await publish({"type": "status", "agent_id": agent_id, "status": body.status})
    return dict(row)


@app.put("/agents/{agent_id}/location")
async def update_location(agent_id: int, body: LocationUpdate, user: dict = Depends(current_user)):
    """HTTP fallback for location updates. The WebSocket route below is the real-time path."""
    ensure_self_or_admin(user, agent_id)
    await _save_location(agent_id, body.lat, body.lng)
    return {"ok": True}


async def _save_location(agent_id: int, lat: float, lng: float):
    async with app.state.pool.acquire() as conn:
        async with conn.transaction():
            updated = await conn.fetchval(
                f"UPDATE agents SET location = {point('$3', '$2')}, updated_at=now() "
                "WHERE id=$1 RETURNING id",
                agent_id, lat, lng,
            )
            if updated is None:
                raise HTTPException(404, "Agent not found")
            await conn.execute(
                f"INSERT INTO location_history (agent_id, location) VALUES ($1, {point('$3', '$2')})",
                agent_id, lat, lng,
            )
    await publish({"type": "location", "agent_id": agent_id, "lat": lat, "lng": lng, "ts": time.time()})


@app.websocket("/ws/agents/{agent_id}")
async def agent_socket(ws: WebSocket, agent_id: int, token: str = ""):
    """Connect with ?token=<jwt>. Agent app sends {"lat": .., "lng": ..} every few seconds."""
    await ws.accept()  # accept first: a close before accept becomes a bare HTTP 403 in the browser
    try:
        user = decode_token(token)
        ensure_self_or_admin(user, agent_id)
    except (jwt.PyJWTError, HTTPException):
        await ws.close(code=4401)  # custom close code: unauthorized
        return
    try:
        while True:
            data = await ws.receive_json()
            loc = LocationUpdate(**data)
            await _save_location(agent_id, loc.lat, loc.lng)
            await ws.send_json({"ack": True})
    except WebSocketDisconnect:
        pass


# ---------- Live feeds ----------
@app.websocket("/ws/live")
async def live_feed(ws: WebSocket, token: str = ""):
    """Admin-only firehose: every agent's location and status changes."""
    await ws.accept()
    try:
        user = decode_token(token)
    except jwt.PyJWTError:
        await ws.close(code=4401)
        return
    if user["role"] != "admin":
        await ws.close(code=4403)
        return
    await stream_events(ws, lambda event: True)


@app.websocket("/ws/orders/{order_id}/track")
async def track_order(ws: WebSocket, order_id: int, token: str = ""):
    """A customer (or admin) follows the one agent assigned to their order. Nobody else's agents leak through."""
    await ws.accept()
    try:
        user = decode_token(token)
    except jwt.PyJWTError:
        await ws.close(code=4401)
        return

    row = await app.state.pool.fetchrow(ORDER_SELECT + " WHERE o.id = $1", order_id)
    allowed = row is not None and (
        user["role"] == "admin" or (user["role"] == "customer" and row["customer_id"] == user["id"])
    )
    if not allowed:
        await ws.close(code=4403)
        return
    agent_id = row["agent_id"]
    if agent_id is None:
        await ws.close(code=4404)  # nobody assigned yet
        return

    # Send the last known position right away so the map isn't empty until the next ping
    start = await app.state.pool.fetchrow(
        "SELECT ST_Y(location::geometry) AS lat, ST_X(location::geometry) AS lng FROM agents WHERE id=$1",
        agent_id,
    )
    if start and start["lat"] is not None:
        await ws.send_json({"type": "location", "agent_id": agent_id, "lat": start["lat"], "lng": start["lng"]})

    await stream_events(ws, lambda event: event.get("agent_id") == agent_id)


# ---------- Orders ----------
@app.post("/orders", status_code=201)
async def create_order(body: OrderCreate, user: dict = Depends(require_roles("customer", "admin"))):
    customer_id = user["id"] if user["role"] == "customer" else None
    name = body.customer_name
    if user["role"] == "customer" or not name:
        name = await app.state.pool.fetchval("SELECT name FROM users WHERE id=$1", user["id"])
    row = await app.state.pool.fetchrow(
        f"""INSERT INTO orders (customer_name, customer_id, pickup, dropoff)
            VALUES ($1, $2, {point('$4', '$3')}, {point('$6', '$5')})
            RETURNING id, customer_name, status""",
        name, customer_id, body.pickup_lat, body.pickup_lng, body.dropoff_lat, body.dropoff_lng,
    )
    return dict(row)


ORDER_SELECT = """
    SELECT o.id, o.customer_name, o.status, o.created_at, o.customer_id, a.agent_id,
           ST_Y(o.pickup::geometry)  AS pickup_lat,  ST_X(o.pickup::geometry)  AS pickup_lng,
           ST_Y(o.dropoff::geometry) AS dropoff_lat, ST_X(o.dropoff::geometry) AS dropoff_lng
    FROM orders o LEFT JOIN assignments a ON a.order_id = o.id
"""


@app.get("/orders")
async def list_orders(user: dict = Depends(current_user)):
    """Admin sees everything, customers see their own orders, agents see orders assigned to them."""
    if user["role"] == "admin":
        rows = await app.state.pool.fetch(ORDER_SELECT + " ORDER BY o.id DESC LIMIT 100")
    elif user["role"] == "customer":
        rows = await app.state.pool.fetch(
            ORDER_SELECT + " WHERE o.customer_id=$1 ORDER BY o.id DESC LIMIT 100", user["id"])
    else:
        rows = await app.state.pool.fetch(
            ORDER_SELECT + " WHERE a.agent_id=$1 ORDER BY o.id DESC LIMIT 100", user["agent_id"])
    return [dict(r) for r in rows]


@app.get("/orders/{order_id}")
async def get_order(order_id: int, user: dict = Depends(current_user)):
    row = await app.state.pool.fetchrow(ORDER_SELECT + " WHERE o.id = $1", order_id)
    if not row:
        raise HTTPException(404, "Order not found")
    allowed = (
        user["role"] == "admin"
        or (user["role"] == "customer" and row["customer_id"] == user["id"])
        or (user["role"] == "agent" and row["agent_id"] == user["agent_id"])
    )
    if not allowed:
        raise HTTPException(403, "Not your order")
    return dict(row)


@app.post("/orders/{order_id}/assign")
async def assign_order(order_id: int, user: dict = Depends(require_roles("admin"))):
    """
    Manual "dispatch now" for admins. The background dispatcher does this automatically.
    The order is OFFERED to the nearest available agent, who then has OFFER_TIMEOUT seconds to accept.
    """
    result = await dispatch_in_tx(order_id)
    if result == "not_waiting":
        raise HTTPException(409, "Order not found, or it is already offered or assigned")
    if result == "no_agent":
        raise HTTPException(409, "No available agents")
    return {
        "order_id": order_id,
        "agent_id": result["agent_id"],
        "agent_name": result["agent_name"],
        "offer_id": result["offer_id"],
        "status": "offered",
        "expires_in_seconds": OFFER_TIMEOUT,
    }


@app.post("/orders/{order_id}/complete")
async def complete_order(order_id: int, user: dict = Depends(require_roles("admin", "agent"))):
    """Mark delivered and free the agent. Agents can only complete their own orders."""
    async with app.state.pool.acquire() as conn:
        async with conn.transaction():
            assignment = await conn.fetchrow("SELECT agent_id FROM assignments WHERE order_id=$1", order_id)
            if user["role"] == "agent" and (not assignment or assignment["agent_id"] != user["agent_id"]):
                raise HTTPException(403, "Not your order")
            row = await conn.fetchrow(
                "UPDATE orders SET status='delivered' WHERE id=$1 AND status IN ('assigned', 'picked_up') RETURNING id", order_id
            )
            if not row:
                raise HTTPException(409, "Order is not in progress")
            await conn.execute(
                "UPDATE agents SET status='available', updated_at=now() WHERE id=$1", assignment["agent_id"]
            )
    await publish({"type": "status", "agent_id": assignment["agent_id"], "status": "available"})
    await publish({"type": "order", "order_id": order_id, "status": "delivered"})
    return {"order_id": order_id, "status": "delivered"}


@app.post("/orders/{order_id}/pickup")
async def pickup_order(order_id: int, user: dict = Depends(require_roles("admin", "agent"))):
    """Agent confirms they have collected the parcel: assigned -> picked_up."""
    async with app.state.pool.acquire() as conn:
        async with conn.transaction():
            assignment = await conn.fetchrow("SELECT agent_id FROM assignments WHERE order_id=$1", order_id)
            if user["role"] == "agent" and (not assignment or assignment["agent_id"] != user["agent_id"]):
                raise HTTPException(403, "Not your order")
            row = await conn.fetchrow(
                "UPDATE orders SET status='picked_up' WHERE id=$1 AND status='assigned' RETURNING id", order_id
            )
            if not row:
                raise HTTPException(409, "Order is not waiting for pickup")
    await publish({"type": "order", "order_id": order_id, "status": "picked_up"})
    return {"order_id": order_id, "status": "picked_up"}


@app.post("/orders/{order_id}/cancel")
async def cancel_order(order_id: int, user: dict = Depends(require_roles("customer", "admin"))):
    """
    Cancel an order that has not been picked up yet. Any live offer is closed and any agent holding
    the order is freed.

    Lock order matters: every code path in this app locks OFFERS first, then the ORDER, then the AGENT.
    Taking locks in the same order everywhere is what prevents deadlocks (accept-vs-cancel, for example).
    """
    async with app.state.pool.acquire() as conn:
        async with conn.transaction():
            offers = await conn.fetch(
                "SELECT id, agent_id FROM offers WHERE order_id=$1 AND status='pending' FOR UPDATE", order_id
            )
            order = await conn.fetchrow(
                "SELECT id, status, customer_id FROM orders WHERE id=$1 FOR UPDATE", order_id
            )
            if not order:
                raise HTTPException(404, "Order not found")
            if user["role"] == "customer" and order["customer_id"] != user["id"]:
                raise HTTPException(403, "Not your order")
            if order["status"] not in ("created", "offered", "assigned"):
                raise HTTPException(409, "This order can no longer be cancelled")

            agent_ids = {o["agent_id"] for o in offers}
            assignment = await conn.fetchrow("SELECT agent_id FROM assignments WHERE order_id=$1", order_id)
            if assignment:
                agent_ids.add(assignment["agent_id"])

            await conn.execute("UPDATE offers SET status='expired' WHERE order_id=$1 AND status='pending'", order_id)
            for agent_id in agent_ids:
                await conn.execute(
                    "UPDATE agents SET status='available', updated_at=now() WHERE id=$1 AND status='busy'", agent_id
                )
            await conn.execute("UPDATE orders SET status='cancelled' WHERE id=$1", order_id)

    for o in offers:
        await publish({"type": "offer", "agent_id": o["agent_id"], "offer_id": o["id"],
                       "order_id": order_id, "state": "expired"})
    for agent_id in agent_ids:
        await publish({"type": "status", "agent_id": agent_id, "status": "available"})
    await publish({"type": "order", "order_id": order_id, "status": "cancelled"})
    return {"order_id": order_id, "status": "cancelled"}


# ---------- Offers ----------
OFFER_SELECT = """
    SELECT f.id, f.order_id, f.agent_id, a.name AS agent_name, o.customer_name,
           GREATEST(EXTRACT(EPOCH FROM (f.expires_at - now())), 0)::float AS seconds_left,
           ST_Y(o.pickup::geometry)  AS pickup_lat,  ST_X(o.pickup::geometry)  AS pickup_lng,
           ST_Y(o.dropoff::geometry) AS dropoff_lat, ST_X(o.dropoff::geometry) AS dropoff_lng
    FROM offers f
    JOIN orders o ON o.id = f.order_id
    JOIN agents a ON a.id = f.agent_id
    WHERE f.status = 'pending' AND f.expires_at > now()
"""


@app.get("/offers/mine")
async def my_offers(user: dict = Depends(require_roles("agent"))):
    rows = await app.state.pool.fetch(OFFER_SELECT + " AND f.agent_id = $1 ORDER BY f.id", user["agent_id"])
    return [dict(r) for r in rows]


@app.get("/offers/pending")
async def pending_offers(user: dict = Depends(require_roles("admin"))):
    rows = await app.state.pool.fetch(OFFER_SELECT + " ORDER BY f.id")
    return [dict(r) for r in rows]


async def _lock_offer(conn, offer_id: int, user: dict):
    offer = await conn.fetchrow(
        """SELECT id, order_id, agent_id, status, (expires_at > now()) AS live
           FROM offers WHERE id=$1 FOR UPDATE""",
        offer_id,
    )
    if not offer:
        raise HTTPException(404, "Offer not found")
    if user["role"] == "agent" and offer["agent_id"] != user["agent_id"]:
        raise HTTPException(403, "This offer is not yours")
    return offer


@app.post("/offers/{offer_id}/accept")
async def accept_offer(offer_id: int, user: dict = Depends(require_roles("agent", "admin"))):
    async with app.state.pool.acquire() as conn:
        async with conn.transaction():
            # The row lock decides races: if the expiry job got here first we see status='expired'
            offer = await _lock_offer(conn, offer_id, user)
            if offer["status"] != "pending" or not offer["live"]:
                raise HTTPException(409, "This offer is no longer available (it expired or was already answered)")
            await conn.execute("UPDATE offers SET status='accepted' WHERE id=$1", offer_id)
            await conn.execute("UPDATE orders SET status='assigned' WHERE id=$1", offer["order_id"])
            await conn.execute(
                "INSERT INTO assignments (order_id, agent_id) VALUES ($1, $2)", offer["order_id"], offer["agent_id"]
            )
    await publish({"type": "order", "order_id": offer["order_id"], "status": "assigned"})
    await publish({"type": "offer", "agent_id": offer["agent_id"], "offer_id": offer_id,
                   "order_id": offer["order_id"], "state": "accepted"})
    return {"order_id": offer["order_id"], "status": "assigned"}


@app.post("/offers/{offer_id}/reject")
async def reject_offer(offer_id: int, user: dict = Depends(require_roles("agent", "admin"))):
    async with app.state.pool.acquire() as conn:
        async with conn.transaction():
            offer = await _lock_offer(conn, offer_id, user)
            if offer["status"] != "pending":
                raise HTTPException(409, "This offer was already answered or has expired")
            await release_offer(conn, offer, "rejected")
    await announce_release(offer, "rejected")
    await dispatch_in_tx(offer["order_id"])  # straight to the next-nearest agent, no waiting for the next tick
    return {"order_id": offer["order_id"], "status": "rejected"}


@app.websocket("/ws/me")
async def my_feed(ws: WebSocket, token: str = ""):
    """An agent's private feed: only offer events addressed to them."""
    await ws.accept()
    try:
        user = decode_token(token)
    except jwt.PyJWTError:
        await ws.close(code=4401)
        return
    if user["role"] != "agent":
        await ws.close(code=4403)
        return
    agent_id = user["agent_id"]
    await stream_events(ws, lambda e: e.get("type") == "offer" and e.get("agent_id") == agent_id)
