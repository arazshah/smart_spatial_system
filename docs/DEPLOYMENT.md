# Deployment Guide

This covers running Smart Spatial System outside of local development:
Docker images, the full environment variable reference, PostGIS setup, and
what to change before exposing the API beyond your own machine.

Read this alongside [CLAUDE.md](../CLAUDE.md) (architecture) and
[README.md](../README.md) (local dev quick start) — this doc doesn't repeat
either.

## Deployment model

This project targets **one deployer running one backend instance for their
own team** (see `api/auth.py`) — not multi-tenant SaaS. If you need per-user
accounts, that's a larger change to the auth model than what's built here
(see [CLAUDE.md](../CLAUDE.md#api-layer-api)).

## Quick start with Docker Compose

```bash
cp .env.example .env                    # fill in LLM key; see "Environment variables" below
cp frontend/.env.example frontend/.env
docker compose up --build
```

- API: http://localhost:8000 (docs at `/docs`)
- Frontend: http://localhost:8080

PostGIS is optional and off by default (the natural-language pipeline and
most plugins work against uploaded files without it). Start it too with:

```bash
docker compose --profile postgis up --build
```

Then follow [data/README.md](../data/README.md) to load the demo dataset, or
point `config/plugins/postgis_connector.yaml` at your own database.

## Building images individually

```bash
# Backend
docker build -t smart-spatial-system-api .
docker run -p 8000:8000 --env-file .env -v smart-spatial-var:/app/var \
  smart-spatial-system-api

# Frontend - VITE_* vars are baked in at build time, not container start
docker build \
  --build-arg VITE_API_BASE_URL=https://api.example.com \
  --build-arg VITE_API_KEY=<matches SMART_SPATIAL_API_KEY below> \
  -t smart-spatial-system-frontend ./frontend
docker run -p 8080:80 smart-spatial-system-frontend
```

## Environment variables

All read from `.env` (backend) / `frontend/.env` (frontend, `VITE_`-prefixed
only — Vite only exposes those to the browser bundle). See
[.env.example](../.env.example) and
[frontend/.env.example](../frontend/.env.example) for the templates.

| Variable | Where | Required | Notes |
|---|---|---|---|
| `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `LLM_*` | backend | For LLM-assisted planning | Any OpenAI-compatible API. Rule-based planning still works without it for supported query shapes. |
| `POSTGIS_PASSWORD` | backend | If using the `local` PostGIS profile | Matches `password_env` in `config/plugins/postgis_connector.yaml`. |
| `SMART_SPATIAL_RUNTIME_DIR` | backend | No (default `var/`) | Where outputs/uploads/projects/cache are written. Set to a mounted/persistent volume path in production — the Docker image already sets this to `/app/var`, mount a volume there. |
| `GEOCHAT_PLUGIN_CONFIG_DIR` | backend | No (default `config/plugins`) | Override to point at a different plugin config directory, e.g. a mounted one. |
| `SMART_SPATIAL_API_KEY` | backend | **Recommended before exposing the API beyond localhost/a trusted network** | See "Authentication" below. |
| `VITE_API_BASE_URL` | frontend (build time) | Yes | Backend URL as reachable from the *browser*, not from inside a backend container. |
| `VITE_API_KEY` | frontend (build time) | If `SMART_SPATIAL_API_KEY` is set | Sent back as `X-API-Key` on every request. |

## Authentication

The API has **no authentication by default** — this matches the project's
current MVP/development-stage status. Before running anywhere reachable
beyond your own machine or a trusted private network:

1. Set `SMART_SPATIAL_API_KEY` to a long random value (`openssl rand -hex 32`)
   in the backend's `.env`.
2. Set `VITE_API_KEY` to the same value when building the frontend image (or
   rebuild/redeploy it — Vite bakes this in, it isn't read at container
   start).
3. Restart/redeploy both.

`/` and `/health` stay open without a key (for load balancer / uptime
checks); every other endpoint then requires the matching `X-API-Key` header.
This is a single shared secret for the whole deployment, not per-user
credentials — see "Deployment model" above.

## CORS

`api/main.py`'s `APIConfig.allow_origins` defaults to local Vite/CRA dev
ports (`localhost:3000`, `localhost:5173` and their `127.0.0.1` equivalents).
For a real deployment, pass your frontend's actual origin(s) when
constructing the app, e.g.:

```python
from api.main import APIConfig, create_app

app = create_app(
    api_config=APIConfig(allow_origins=("https://spatial.example.com",))
)
```

(There's currently no environment-variable override for this — it's a
`create_app()` argument. If you need one, add it the same way `api_key` was
added to `APIConfig`, rather than hardcoding origins in `api/main.py`.)

## TLS / reverse proxy

Neither the backend nor the frontend container terminates TLS. Put both
behind a reverse proxy (nginx, Caddy, Traefik, your cloud load balancer)
that handles HTTPS and forwards to the `api` (8000) and `frontend` (80)
containers/ports. This also gives you one place to add rate limiting, which
this project does not implement itself.

## PostGIS

Two ways to point the backend at PostGIS:

1. **Bundled demo/dev instance**: `docker compose --profile postgis up`,
   then follow [data/README.md](../data/README.md).
2. **Your own database**: edit `config/plugins/postgis_connector.yaml` (or
   mount a replacement over it — see the commented volume line in
   `docker-compose.yml`) to point at it. The `production` profile in
   `config/plugins/postgis_connector.example.yaml` reads connection details
   from `POSTGIS_HOST`/`POSTGIS_DATABASE`/`POSTGIS_USER`/`POSTGIS_PASSWORD`
   env vars instead of the file, if you prefer that.

`plugins/postgis_connector.py` validates identifiers (table/schema/column
names) against a strict allowlist and filters the `where` clause against a
blocklist of statement/subquery/server-internals keywords before it ever
opens a connection — see the plugin's module docstring and
`tests/test_postgis_connector.py` for exactly what's covered. It is
defense-in-depth on top of, not a replacement for, running the database
connection with least-privilege credentials (read-only on the tables you
actually want queried, not a superuser).

## Persistent state

`SMART_SPATIAL_RUNTIME_DIR` (`/app/var` in the Docker image) holds request
outputs, uploads, project metadata, and cache — this is your data. The
Compose file mounts it as a named volume (`smart-spatial-var`); back it up
like you would a database. `config/plugins/*.yaml` (plugin settings,
including PostGIS credentials if you inline them there) is also meaningful
state if you edit it at runtime through the `/plugins/{id}/config` API
rather than only at build time.

## Health checks

`GET /health` returns service health and stays unauthenticated regardless
of `SMART_SPATIAL_API_KEY`, so it's safe to point a load balancer or
container orchestrator's liveness probe at it directly.
