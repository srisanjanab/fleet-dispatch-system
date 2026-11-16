# Week 4 additions

- Offers: an order is OFFERED to the nearest available agent; they accept, reject, or let it expire (default 30s)
- Reassignment: reject/expiry sends it to the next-nearest agent, never the same agent twice (UNIQUE(order_id, agent_id))
- Waiting queue: a background dispatcher offers waiting orders as soon as an agent is free
- Race safety: row locks + SKIP LOCKED decide accept-vs-expiry, and let several servers run the dispatcher
- Agent screen: live offer card with countdown, pushed over a private WebSocket (/ws/me)
- Simulator: agents answer offers (accept / reject / ignore) and deliver orders
