# Deploying Fleet Dispatch (Render + Neon)

The API, Redis and website run on Render. The database runs on Neon, a separate free Postgres service, so it does not
expire together with the app host and you can move the API to another provider without touching your data.
Plan on about 30 to 40 minutes.

**Know before you start (free tiers change, so check each pricing page):**
- Free Render web services sleep when unused. The first visit after a pause can take about a minute.
- I could not test Render or Neon from here. The app side (empty-database setup, production safety check, CORS,
  frontend build) is tested.

## Step 1: Push the deployment files
```
git add .
git commit -m "Deployment configuration"
git push
```

## Step 2: Create the database on Neon
1. Sign in at https://neon.tech and create a project (any name, any region close to you).
2. Open the project's **Connection details** and choose the **direct** connection, not the pooled one. The pooled host
   contains `-pooler`. Turn the "Connection pooling" switch **off** so the host has no `-pooler`.
3. Copy the connection string. It looks like `postgresql://user:password@ep-something.region.aws.neon.tech/neondb?sslmode=require`.
4. Keep **only** `?sslmode=require` at the end. If the string has extra options such as `&channel_binding=require`, delete them,
   because the database driver reads unknown options as server settings and fails.
5. You do not need to create any tables. The API creates them on first start.

## Step 3: Create everything else with the Blueprint
1. Sign in at https://render.com with your GitHub account.
2. Click **New**, then **Blueprint**, and pick the `fleet-dispatch-system` repository.
3. Render reads `render.yaml` and lists three services: Redis, the API and the website.
4. It asks for four values. Fill them like this for now:
   - `DATABASE_URL`: the Neon string from Step 2.
   - `ADMIN_PASSWORD`: a strong password you will remember (your admin login).
   - `CORS_ORIGINS`: type `https://placeholder.example.com` (fixed in Step 4).
   - `VITE_API_URL`: type `https://placeholder.example.com` (fixed in Step 4).
5. Click **Apply** and wait for the first deploy.

## Step 4: Connect the website to the API
Render gives each service an address like `https://fleet-dispatch-api.onrender.com`. Note both addresses (API and website).
1. Open the **API** service, then **Environment**, and set `CORS_ORIGINS` to your **website** address, exactly as shown
   in the browser, starting with `https://`, with **no slash at the end**.
2. Open the **website** service, then **Environment**, and set `VITE_API_URL` to your **API** address (also no slash at the end).
3. On the website service click **Manual Deploy**, then **Deploy latest commit**. The address is baked in when the site is built.
4. The API restarts by itself after you change its variable.

## Step 5: Check it works
1. Open `https://<your-api>.onrender.com/health`. You should see `{"status":"ok"}` (give it a minute if it was asleep).
2. Open your website address and log in as `admin@example.com` with the password from Step 3.
3. Register an agent and a customer in two browsers and repeat the demo flow.

## Step 6: Show it off with the simulator (optional)
```
$env:FLEET_API = "https://<your-api>.onrender.com"
$env:ADMIN_PASSWORD = "<your admin password>"
python scripts/simulate_agents.py
```
Free-tier Redis has usage limits, so run the simulator for demos and stop it afterwards with Ctrl+C.

## If something goes wrong
| What you see | Likely cause and fix |
|---|---|
| Browser console shows a CORS error on login | `CORS_ORIGINS` on the API does not exactly match the website address (https, no trailing slash) |
| The site calls `placeholder.example.com` | You skipped the rebuild in Step 4. Manually redeploy the website |
| The live map says Reconnecting | Check the API address for typos, and wait for the API to wake up |
| API log: `Set JWT_SECRET and ADMIN_PASSWORD` | `ADMIN_PASSWORD` is empty or still `admin123` |
| API log: unrecognized configuration parameter | The Neon string has extra options. Keep only `?sslmode=require` |
| API log: permission denied to create extension postgis | Tell me the exact message |
| Startup hangs or lock errors with Neon | You used the pooled connection. Use the direct one (no `-pooler` in the host) |
| Render says the Blueprint is invalid | Tell me the exact message. The manual route below always works |

## Manual route (if the Blueprint will not work)
Create these in the Render dashboard yourself, using the same settings as `render.yaml`:
1. **Key Value** store, named `fleet-dispatch-redis`.
2. **Web Service** from your repo: Runtime Docker, Dockerfile path `./backend/Dockerfile`, Docker context `./backend`, health check path `/health`.
   Environment: `ENVIRONMENT=production`, `DATABASE_URL` (the Neon string), `REDIS_URL` (the key-value internal URL),
   `JWT_SECRET` (any long random string), `ADMIN_PASSWORD`, `CORS_ORIGINS`.
3. **Static Site** from your repo: build command `cd frontend && npm install && npm run build`, publish directory `frontend/dist`,
   a rewrite rule from `/*` to `/index.html`, environment `VITE_API_URL`.

## Security checklist
- Secrets live only in the Render and Neon dashboards, never in Git.
- Use a strong, unique admin password, and turn on two-factor login for GitHub, Render and Neon.
- If a provider reports an incident, change `ADMIN_PASSWORD` and `JWT_SECRET` and redeploy. Changing the JWT secret logs everyone out.
- The app is a Docker container with standard Postgres and Redis, so it can move to another host without code changes.
