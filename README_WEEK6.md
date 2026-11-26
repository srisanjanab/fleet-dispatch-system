# Week 6 additions
- 23 automated tests (pytest) against real Postgres + PostGIS, including a concurrency test
- GitHub Actions CI: backend tests + frontend build on every push
- scripts/run_tests.ps1 to run the tests inside Docker
- Redis event channel is configurable (EVENT_CHANNEL) so tests never mix with a running dev app
