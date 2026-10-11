# Fleet Dispatch System

Real-time delivery dispatch: live agent tracking, nearest-agent assignment (PostGIS),
and concurrency-safe order claiming.

## Run
```bash
docker compose up --build
```
API docs: http://localhost:8000/docs

## Prove the core guarantee
```bash
pip install httpx
python scripts/concurrency_test.py
```

## Roadmap
- [x] Week 1: schema, infra, orders/agents API, nearest-agent assignment, concurrency test
- [ ] Week 2: JWT auth + roles, React frontend skeleton
- [ ] Week 3: live map (Leaflet) fed by WebSocket location stream, Redis pub/sub
- [ ] Week 4: agent accept/reject, reassignment on timeout
- [ ] Week 5: customer tracking page + ETA
- [ ] Week 6-8: admin dashboard, tests + CI, load testing (k6), deploy
