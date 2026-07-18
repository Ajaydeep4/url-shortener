# URL Shortener — Microservices

A production-grade URL shortener composed of four services (NGINX gateway, shortener-service, redirect-service, analytics-worker) backed by PostgreSQL and Redis. Full architecture, technology rationale, concurrency/caching/error-handling/logging design, and requirements traceability: see [DESIGN.md](DESIGN.md).

The system lives in **two repositories** (industry-standard split):

- **This repo (code monorepo)** — all four services, each with its own unit tests and Dockerfile, plus the compose stack, CI pipeline, and design doc.
- **`url-shortener-e2e-tests` (tests repo)** — end-to-end tests that drive the running system through the gateway over HTTP; cloned automatically by CI.

## Layout

| Path | What it is |
|---|---|
| [gateway/](gateway/) | NGINX API gateway — the single public entry point (port 8080) |
| [shortener-service/](shortener-service/) | Write side: create short URLs (custom alias + TTL support), metadata, owns DB schema |
| [redirect-service/](redirect-service/) | Read side: cache-accelerated redirects, expiry enforcement, click events |
| [analytics-worker/](analytics-worker/) | Consumes click events, maintains access counts |
| [docker-compose.yml](docker-compose.yml) | Brings up the entire stack |
| [.github/workflows/ci.yml](.github/workflows/ci.yml) | CI/CD: per-service tests + coverage gate, e2e, image publishing |

## Quick start (Docker)

Requires Docker with the Compose plugin.

```bash
docker compose up --build -d
```

Wait until `docker compose ps` shows all services healthy (first start: images build, Postgres initializes, migrations apply). Interactive API docs: http://localhost:8080/docs

## Quick start (without Docker — e.g. this devcontainer)

Running a Docker daemon inside a container requires privileged mode, so in devcontainer-style environments run the services natively:

```bash
# One-time: install and start infrastructure
sudo apt-get install -y postgresql redis-server nginx
sudo service postgresql start && sudo service redis-server start
sudo su postgres -c "psql -c \"CREATE ROLE shortener LOGIN PASSWORD 'shortener'\" -c 'CREATE DATABASE shortener OWNER shortener'"

# One-time: install the services into a shared virtualenv
python3 -m venv .venv
.venv/bin/pip install -e shortener-service -e redirect-service -e analytics-worker

# Every run: migrations, then the four components
export DATABASE_URL='postgresql+asyncpg://shortener:shortener@localhost:5432/shortener' \
       REDIS_URL='redis://localhost:6379/0' BASE_URL='http://localhost:8080'
(cd shortener-service && ../.venv/bin/alembic upgrade head)
.venv/bin/uvicorn shortener_service.main:app --port 8001 > /tmp/shortener-service.log 2>&1 &
.venv/bin/uvicorn redirect_service.main:app --port 8002 > /tmp/redirect-service.log 2>&1 &
.venv/bin/python -m analytics_worker.main > /tmp/analytics-worker.log 2>&1 &
nginx -c "$PWD/.local-dev/nginx-local.conf"   # gateway on :8080 (see note below)
```

The `.local-dev/nginx-local.conf` gateway config (same routing as the Docker gateway, localhost upstreams) is untracked scaffolding for this mode; recreate it from [gateway/nginx.conf](gateway/nginx.conf) by pointing the upstreams at `127.0.0.1:8001/8002` if missing.

## Using the API

Create a short URL (auto-generated alias):

```bash
curl -s -X POST http://localhost:8080/api/v1/urls \
  -H 'Content-Type: application/json' \
  -d '{"long_url": "https://en.wikipedia.org/wiki/URL_shortening"}'
```

Custom alias and optional expiry (`ttl_seconds`):

```bash
curl -s -X POST http://localhost:8080/api/v1/urls \
  -H 'Content-Type: application/json' \
  -d '{"long_url": "https://example.com/campaign", "custom_alias": "promo", "ttl_seconds": 86400}'
```

Follow a short link (302 redirect):

```bash
curl -i http://localhost:8080/promo
```

Metadata (creation time, access count, expiry):

```bash
curl -s http://localhost:8080/api/v1/urls/promo
```

```json
{"alias": "promo", "short_url": "http://localhost:8080/promo",
 "long_url": "https://example.com/campaign", "created_at": "2026-07-18T10:00:00Z",
 "expires_at": "2026-07-19T10:00:00Z", "access_count": 1, "is_custom": true}
```

All errors share one envelope with a machine-readable code:

```json
{"error": {"code": "alias_conflict", "message": "alias 'promo' is already taken"}}
```

## Testing

**Unit tests** live inside each service and run with a **100% line-coverage gate** (pytest-cov); all external systems (Postgres, Redis) are mocked:

```bash
cd shortener-service        # same for redirect-service, analytics-worker
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest            # runs with --cov --cov-fail-under=100
```

**End-to-end tests** live in the separate `url-shortener-e2e-tests` repository and require the stack to be running:

```bash
git clone <your-account>/url-shortener-e2e-tests && cd url-shortener-e2e-tests
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/pytest -v          # targets http://localhost:8080 (override: GATEWAY_URL)
```

## CI/CD

Every push/PR triggers [ci.yml](.github/workflows/ci.yml):

1. **detect-changes** — path filtering figures out which services changed.
2. **unit-tests** — matrix job per changed service; 100% coverage enforced; reports uploaded as artifacts.
3. **e2e-tests** — builds all images, boots the compose stack, clones the `url-shortener-e2e-tests` repo, runs the suite through the gateway.
4. **publish-images** — on `main`, pushes changed services' images to GitHub Container Registry (`ghcr.io/<owner>/<repo>/<service>:<sha>`).

## Deployment (DigitalOcean)

CI already produces deployable images in GHCR. To deploy on DigitalOcean: create a droplet with Docker (or use App Platform), copy `docker-compose.yml`, replace the `build:` entries with the GHCR `image:` references, point `DATABASE_URL`/`REDIS_URL` at managed Postgres/Redis, and `docker compose up -d`. (Not executed from this environment — no DigitalOcean credentials; `doctl auth init` first.)

## Operations

```bash
docker compose ps                      # health of all services
docker compose logs -f redirect-service
docker compose down                    # stop (keeps data volume)
docker compose down -v                 # stop and wipe the database
```

Key environment variables: `DATABASE_URL`, `REDIS_URL`, `BASE_URL`, `ALIAS_LENGTH`, `ALIAS_MAX_RETRIES`, `CACHE_TTL_SECONDS`, `LOG_LEVEL`.
