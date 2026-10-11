CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE agents (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'offline'
                CHECK (status IN ('offline', 'available', 'busy')),
    location    GEOGRAPHY(Point, 4326),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Spatial index: makes "nearest agent" a fast KNN lookup instead of a full scan
CREATE INDEX idx_agents_location ON agents USING GIST (location);
CREATE INDEX idx_agents_status ON agents (status);

CREATE TABLE orders (
    id             SERIAL PRIMARY KEY,
    customer_name  TEXT NOT NULL,
    pickup         GEOGRAPHY(Point, 4326) NOT NULL,
    dropoff        GEOGRAPHY(Point, 4326) NOT NULL,
    status         TEXT NOT NULL DEFAULT 'created'
                   CHECK (status IN ('created', 'assigned', 'picked_up', 'delivered', 'cancelled')),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE assignments (
    id           SERIAL PRIMARY KEY,
    order_id     INT NOT NULL UNIQUE REFERENCES orders(id),  -- UNIQUE = an order can never be assigned twice
    agent_id     INT NOT NULL REFERENCES agents(id),
    assigned_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE location_history (
    id           BIGSERIAL PRIMARY KEY,
    agent_id     INT NOT NULL REFERENCES agents(id),
    location     GEOGRAPHY(Point, 4326) NOT NULL,
    recorded_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_location_history_agent_time ON location_history (agent_id, recorded_at DESC);
