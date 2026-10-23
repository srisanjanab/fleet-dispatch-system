# Week 3 additions

- Redis pub/sub fan-out: every location/status change is published once, each WebSocket client gets its own subscription
- /ws/live (admin firehose), /ws/orders/{id}/track (customer sees only their assigned agent)
- React live map (Leaflet + OpenStreetMap), auto-reconnecting WebSocket hook
- Agent screen: stream real GPS over WebSocket
- scripts/simulate_agents.py: N fake agents moving around Bangalore
