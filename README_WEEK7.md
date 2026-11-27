# Week 7 additions: deployment readiness
- Database tables are created automatically on first start (idempotent, guarded by an advisory lock for multi-server boots)
- ENVIRONMENT=production refuses to start with the default JWT secret or admin password
- Configurable CORS (CORS_ORIGINS) for a website hosted on a different domain
- Frontend API address configurable with VITE_API_URL; WebSocket URLs derived from it
- Dockerfile listens on the platform's PORT
- render.yaml Blueprint and DEPLOY.md step-by-step guide
- Simulator can target a deployed server (FLEET_API, ADMIN_PASSWORD)
