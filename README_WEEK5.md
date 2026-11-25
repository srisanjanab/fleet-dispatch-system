# Week 5 additions
- Full order lifecycle: assigned -> picked_up -> delivered (agent "Picked up" button, POST /orders/{id}/pickup)
- Order cancellation (customer or admin) before pickup; closes live offers and frees the agent
- Deadlock-safe lock ordering (offers -> order -> agent) across accept / reject / expire / cancel
- ETA on the customer tracking map (heuristic: straight-line x 1.3 at 25 km/h)
