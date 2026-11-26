# Runs the backend tests inside the running backend container (Docker Desktop must be on).
# Usage, from the project folder:   .\scripts\run_tests.ps1
docker compose cp backend/tests backend:/code/
docker compose cp db backend:/code/
docker compose exec backend sh -c "pip install -q pytest==8.3.3 && python -m pytest -v"
