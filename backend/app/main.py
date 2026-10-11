import os
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://dispatch:dispatch@localhost:5432/dispatch")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=20)
    yield
    await app.state.pool.close()


app = FastAPI(title="Fleet Dispatch System", lifespan=lifespan)


# ---------- Schemas ----------
class AgentCreate(BaseModel):
    name: str


class StatusUpdate(BaseModel):
    status: str = Field(pattern="^(offline|available|busy)$")


class LocationUpdate(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


class OrderCreate(BaseModel):
    customer_name: str
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


# ---------- Agents ----------
@app.post("/agents", status_code=201)
async def create_agent(body: AgentCreate):
    row = await app.state.pool.fetchrow(
        "INSERT INTO agents (name) VALUES ($1) RETURNING id, name, status", body.name
    )
    return dict(row)


@app.get("/agents")
async def list_agents():
    rows = await app.state.pool.fetch(
        """SELECT id, name, status,
                  ST_Y(location::geometry) AS lat, ST_X(location::geometry) AS lng
           FROM agents ORDER BY id"""
    )
    return [dict(r) for r in rows]


@app.patch("/agents/{agent_id}/status")
async def set_agent_status(agent_id: int, body: StatusUpdate):
    row = await app.state.pool.fetchrow(
        "UPDATE agents SET status=$2, updated_at=now() WHERE id=$1 RETURNING id, name, status",
        agent_id, body.status,
    )
    if not row:
        raise HTTPException(404, "Agent not found")
    return dict(row)


@app.put("/agents/{agent_id}/location")
async def update_location(agent_id: int, body: LocationUpdate):
    """HTTP fallback for location updates. The WebSocket route below is the real-time path."""
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
async def agent_socket(ws: WebSocket, agent_id: int):
    """Agent app sends {"lat": .., "lng": ..} every few seconds."""
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
async def create_order(body: OrderCreate):
    row = await app.state.pool.fetchrow(
        f"""INSERT INTO orders (customer_name, pickup, dropoff)
            VALUES ($1, {point('$3', '$2')}, {point('$5', '$4')})
            RETURNING id, customer_name, status""",
        body.customer_name, body.pickup_lat, body.pickup_lng, body.dropoff_lat, body.dropoff_lng,
    )
    return dict(row)


@app.get("/orders/{order_id}")
async def get_order(order_id: int):
    row = await app.state.pool.fetchrow(
        """SELECT o.id, o.customer_name, o.status, a.agent_id
           FROM orders o LEFT JOIN assignments a ON a.order_id = o.id
           WHERE o.id = $1""",
        order_id,
    )
    if not row:
        raise HTTPException(404, "Order not found")
    return dict(row)


@app.post("/orders/{order_id}/assign")
async def assign_order(order_id: int):
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
async def complete_order(order_id: int):
    """Mark delivered and free the agent."""
    async with app.state.pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "UPDATE orders SET status='delivered' WHERE id=$1 AND status='assigned' RETURNING id", order_id
            )
            if not row:
                raise HTTPException(409, "Order not in assigned state")
            await conn.execute(
                """UPDATE agents SET status='available', updated_at=now()
                   WHERE id = (SELECT agent_id FROM assignments WHERE order_id=$1)""",
                order_id,
            )
    return {"order_id": order_id, "status": "delivered"}
