"""
Shared test setup.

These tests run against a REAL Postgres + PostGIS database, not mocks, because the behaviour worth
testing here (row locks, constraints, spatial queries) only exists in a real database.
A throwaway database called dispatch_test is created fresh on every test run, so your dev data is never touched.
"""
import asyncio
import os
import uuid
from pathlib import Path

import asyncpg
import pytest
from fastapi.testclient import TestClient

BASE_URL = os.environ.get("DATABASE_URL", "postgresql://dispatch:dispatch@localhost:5432/dispatch")
SERVER_URL = BASE_URL.rpartition("/")[0]
TEST_DB = "dispatch_test"
TEST_URL = f"{SERVER_URL}/{TEST_DB}"

# These MUST be set before the app is imported, because the app reads its settings at import time.
os.environ["DATABASE_URL"] = TEST_URL
os.environ["AUTO_DISPATCH"] = "false"              # tests trigger dispatching themselves, so results are deterministic
os.environ["EVENT_CHANNEL"] = "agent_events_test"  # never mix test events into a running dev app
os.environ.setdefault("REDIS_URL", "redis://localhost:6379")

CLEAN_SQL = """
DELETE FROM offers;
DELETE FROM assignments;
DELETE FROM location_history;
DELETE FROM orders;
DELETE FROM users WHERE role <> 'admin';
DELETE FROM agents;
"""


def _find_schema() -> Path:
    here = Path(__file__).resolve()
    for folder in (here.parents[1], here.parents[2]):
        candidate = folder / "db" / "schema.sql"
        if candidate.exists():
            return candidate
    raise FileNotFoundError("db/schema.sql not found")


async def _create_database() -> None:
    conn = await asyncpg.connect(f"{SERVER_URL}/postgres")
    try:
        await conn.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
        await conn.execute(f"CREATE DATABASE {TEST_DB}")
    finally:
        await conn.close()
    conn = await asyncpg.connect(TEST_URL)
    try:
        await conn.execute(_find_schema().read_text())
    finally:
        await conn.close()


@pytest.fixture(scope="session")
def client():
    asyncio.run(_create_database())
    from app.main import app

    with TestClient(app) as c:  # runs the app's startup, which creates the remaining tables
        yield c


class Api:
    """Small helpers so each test reads like a story instead of a pile of HTTP calls."""

    def __init__(self, client):
        self.c = client
        self._admin = None

    # ---- identity ----
    @property
    def admin(self):
        if self._admin is None:
            r = self.c.post("/auth/login", json={"email": "admin@example.com", "password": "admin123"})
            assert r.status_code == 200, r.text
            self._admin = {"Authorization": f"Bearer {r.json()['access_token']}"}
        return self._admin

    def register(self, role, name="Tester"):
        email = f"{uuid.uuid4().hex[:10]}@test.com"
        r = self.c.post("/auth/register", json={"email": email, "name": name, "password": "secret123", "role": role})
        assert r.status_code == 201, r.text
        login = self.c.post("/auth/login", json={"email": email, "password": "secret123"}).json()
        return {"Authorization": f"Bearer {login['access_token']}"}, login

    # ---- building blocks ----
    def agent(self, lat, lng, status="available"):
        r = self.c.post("/agents", json={"name": f"agent-{uuid.uuid4().hex[:6]}"}, headers=self.admin)
        agent_id = r.json()["id"]
        if lat is not None:
            self.c.put(f"/agents/{agent_id}/location", json={"lat": lat, "lng": lng}, headers=self.admin)
        self.c.patch(f"/agents/{agent_id}/status", json={"status": status}, headers=self.admin)
        return agent_id

    def order(self, lat=12.9716, lng=77.5946, headers=None):
        body = {"customer_name": "t", "pickup_lat": lat, "pickup_lng": lng, "dropoff_lat": 12.9, "dropoff_lng": 77.6}
        r = self.c.post("/orders", json=body, headers=headers or self.admin)
        assert r.status_code == 201, r.text
        return r.json()["id"]

    # ---- actions ----
    def assign(self, order_id):
        return self.c.post(f"/orders/{order_id}/assign", headers=self.admin)

    def accept(self, offer_id, headers=None):
        return self.c.post(f"/offers/{offer_id}/accept", headers=headers or self.admin)

    def reject(self, offer_id, headers=None):
        return self.c.post(f"/offers/{offer_id}/reject", headers=headers or self.admin)

    def post(self, path, headers=None):
        return self.c.post(path, headers=headers or self.admin)

    # ---- reading state ----
    def pending(self):
        return self.c.get("/offers/pending", headers=self.admin).json()

    def order_status(self, order_id):
        return self.c.get(f"/orders/{order_id}", headers=self.admin).json()["status"]

    def agent_status(self, agent_id):
        rows = self.c.get("/agents", headers=self.admin).json()
        return next(a["status"] for a in rows if a["id"] == agent_id)

    # ---- direct database access, run on the app's own event loop ----
    def sql(self, query, *args):
        from app.main import app
        return self.c.portal.call(app.state.pool.execute, query, *args)

    def sql_val(self, query, *args):
        from app.main import app
        return self.c.portal.call(app.state.pool.fetchval, query, *args)

    def run(self, coroutine_function):
        return self.c.portal.call(coroutine_function)


@pytest.fixture()
def api(client):
    helper = Api(client)
    helper.sql(CLEAN_SQL)  # every test starts from an empty fleet and no orders
    return helper
