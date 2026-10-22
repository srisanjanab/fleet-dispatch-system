import os
from contextlib import asynccontextmanager

import asyncpg
import jwt
from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from .auth import (
    create_token, current_user, decode_token, ensure_self_or_admin,
    hash_password, require_roles, verify_password,
)

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://dispatch:dispatch@localhost:5432/dispatch")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@example.com")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")  # dev default only

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
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=20)
    async with app.state.pool.acquire() as conn:
        await conn.execute(USERS_DDL)  # idempotent: safe on every startup
        exists = await conn.fetchval("SELECT 1 FROM users WHERE email=$1", ADMIN_EMAIL)
        if not exists:
            await conn.execute(
                "INSERT INTO users (email, name, password_hash, role) VALUES ($1, 'Admin', $2, 'admin')",
                ADMIN_EMAIL, hash_password(ADMIN_PASSWORD),
            )
    yield
    await app.state.pool.close()


app = FastAPI(title="Fleet Dispatch System", lifespan=lifespan)

EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


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


@app.websocket("/ws/agents/{agent_id}")
async def agent_socket(ws: WebSocket, agent_id: int, token: str = ""):
    """Connect with ?token=<jwt>. Agent app sends {"lat": .., "lng": ..} every few seconds."""
    try:
        user = decode_token(token)
        ensure_self_or_admin(user, agent_id)
    except (jwt.PyJWTError, HTTPException):
        await ws.close(code=4401)  # custom close code: unauthorized
        return
    await ws.accept()
    try:
        while True:
            data = await ws.receive_json()
            loc = LocationUpdate(**data)
            await _save_location(agent_id, loc.lat, loc.lng)
            await ws.send_json({"ack": True})
    except WebSocketDisconnect:
        pass


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
    Core algorithm: find the nearest AVAILABLE agent and claim them atomically.

    - ORDER BY location <-> pickup : KNN search using the GiST index
    - FOR UPDATE SKIP LOCKED       : if another transaction is already claiming an agent,
                                     skip that row and take the next-nearest.
                                     This is what prevents double-assignment under load.
    """
    async with app.state.pool.acquire() as conn:
        async with conn.transaction():
            order = await conn.fetchrow(
                "SELECT id FROM orders WHERE id=$1 AND status='created' FOR UPDATE", order_id
            )
            if not order:
                raise HTTPException(409, "Order not found or already assigned")

            agent = await conn.fetchrow(
                """SELECT a.id, a.name
                   FROM agents a, orders o
                   WHERE o.id = $1
                     AND a.status = 'available'
                     AND a.location IS NOT NULL
                   ORDER BY a.location <-> o.pickup
                   LIMIT 1
                   FOR UPDATE OF a SKIP LOCKED""",
                order_id,
            )
            if not agent:
                raise HTTPException(409, "No available agents")

            await conn.execute("UPDATE agents SET status='busy', updated_at=now() WHERE id=$1", agent["id"])
            await conn.execute("UPDATE orders SET status='assigned' WHERE id=$1", order_id)
            await conn.execute(
                "INSERT INTO assignments (order_id, agent_id) VALUES ($1, $2)", order_id, agent["id"]
            )

    return {"order_id": order_id, "agent_id": agent["id"], "agent_name": agent["name"]}


@app.post("/orders/{order_id}/complete")
async def complete_order(order_id: int, user: dict = Depends(require_roles("admin", "agent"))):
    """Mark delivered and free the agent. Agents can only complete their own orders."""
    async with app.state.pool.acquire() as conn:
        async with conn.transaction():
            assignment = await conn.fetchrow("SELECT agent_id FROM assignments WHERE order_id=$1", order_id)
            if user["role"] == "agent" and (not assignment or assignment["agent_id"] != user["agent_id"]):
                raise HTTPException(403, "Not your order")
            row = await conn.fetchrow(
                "UPDATE orders SET status='delivered' WHERE id=$1 AND status='assigned' RETURNING id", order_id
            )
            if not row:
                raise HTTPException(409, "Order not in assigned state")
            await conn.execute(
                "UPDATE agents SET status='available', updated_at=now() WHERE id=$1", assignment["agent_id"]
            )
    return {"order_id": order_id, "status": "delivered"}
