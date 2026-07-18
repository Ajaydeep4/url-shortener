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
| [docker-compose.yml](docker-compose.yml) | Brings up the entire stack (local, builds from source) |
| [docker-compose.deploy.yml](docker-compose.deploy.yml) | Deployment override: pulls versioned GHCR images, env-file config |
| [deploy/](deploy/) | Per-environment env-file templates (dev/stage/prod) |
| [scripts/](scripts/) | `release.sh` (version bump) and `provision-droplet.sh` (DigitalOcean setup) |
| [.github/workflows/ci.yml](.github/workflows/ci.yml) | CI: per-service tests + coverage gate, e2e, image publishing |
| [.github/workflows/deploy.yml](.github/workflows/deploy.yml) | CD: deploys dev/stage/prod droplets after green CI |
| [CHANGELOG.md](CHANGELOG.md) / [VERSION](VERSION) | Platform version history (Keep a Changelog + semver) |

## Quick start (Docker)

Requires Docker with the Compose plugin.

```bash
docker compose up --build -d
```

Wait until `docker compose ps` shows all services healthy (first start: images build, Postgres initializes, migrations apply). Interactive API docs: http://localhost:8080/docs

## Quick start (without Docker — e.g. this devcontainer)

Running a Docker daemon inside a container requires privileged mode, so in devcontainer-style environments run the services natively.

**1. One-time setup** — install infrastructure and the services:

```bash
# Infrastructure: Postgres, Redis, NGINX
sudo apt-get install -y postgresql redis-server nginx
sudo service postgresql start && sudo service redis-server start
sudo su postgres -c "psql -c \"CREATE ROLE shortener LOGIN PASSWORD 'shortener'\" -c 'CREATE DATABASE shortener OWNER shortener'"

# The three Python services, in one shared virtualenv
python3 -m venv .venv
.venv/bin/pip install -e shortener-service -e redirect-service -e analytics-worker
```

**2. Start the stack** (migrations, three services, gateway):

```bash
export DATABASE_URL='postgresql+asyncpg://shortener:shortener@localhost:5432/shortener' \
       REDIS_URL='redis://localhost:6379/0' BASE_URL='http://localhost:8080'
(cd shortener-service && ../.venv/bin/alembic upgrade head)
.venv/bin/uvicorn shortener_service.main:app --host 127.0.0.1 --port 8001 > /tmp/shortener-service.log 2>&1 &
.venv/bin/uvicorn redirect_service.main:app --host 127.0.0.1 --port 8002 > /tmp/redirect-service.log 2>&1 &
.venv/bin/python -m analytics_worker.main > /tmp/analytics-worker.log 2>&1 &
nginx -c "$PWD/.local-dev/nginx-local.conf"   # gateway on :8080
```

[.local-dev/nginx-local.conf](.local-dev/nginx-local.conf) is the tracked local variant of [gateway/nginx.conf](gateway/nginx.conf): identical routing, localhost upstreams, `/tmp` paths so no system directories are touched.

**3. Verify it's up** — each service reports its health and running version:

```bash
curl http://localhost:8080/healthz          # gateway         -> ok
curl http://127.0.0.1:8001/healthz          # shortener       -> {"status":"ok","database":"ok","version":"1.1.0"}
curl http://127.0.0.1:8002/healthz          # redirect        -> {"status":"ok","version":"1.1.0","database":"ok","cache":"ok"}
tail -1 /tmp/analytics-worker.log           # worker startup log line
```

Then exercise it end to end with the API calls in [Using the API](#using-the-api) below. Service logs: `/tmp/shortener-service.log`, `/tmp/redirect-service.log`, `/tmp/analytics-worker.log`.

**4. Stop everything**:

```bash
pkill -f 'uvicorn (shortener|redirect)_service' ; pkill -f 'analytics_worker.main'
nginx -c "$PWD/.local-dev/nginx-local.conf" -s quit
```

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

## Branching model (GitLab Flow with environment branches)

| Branch | Role | Deploys to |
|---|---|---|
| `feature/*` | Day-to-day work; PRs into `dev` | — (CI only) |
| `dev` | Integration branch | dev droplet, automatically |
| `stage` | Pre-production; promoted from `dev` by PR | stage droplet, automatically |
| `main` | Production history; promoted from `stage` by a release PR | prod droplet, on `vX.Y.Z` tag + manual approval |
| `hotfix/*` | Urgent fixes cut from `main`, tagged as a patch release, back-merged to `dev`/`stage` | prod (via tag) |

Commits follow [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `chore:` ...) so changelog entries map one-to-one to commits.

## Versioning and releases

The platform is versioned **as a single unit** (one semver spans all services, because the compose stack deploys as one unit): source of truth is [VERSION](VERSION) + the `vX.Y.Z` git tag; history lives in [CHANGELOG.md](CHANGELOG.md). Every service reports its version at runtime via `/healthz`, so any environment can be asked what it is running.

Cutting a release:

```bash
git checkout stage && git pull
./scripts/release.sh 1.2.0        # bumps VERSION, pyprojects, CHANGELOG
git checkout -b release/v1.2.0 && git add -A && git commit -m "chore(release): v1.2.0"
# open PR release/v1.2.0 -> main; after it merges:
git tag v1.2.0 main && git push origin v1.2.0    # triggers the prod deploy
```

## CI/CD

**CI** ([ci.yml](.github/workflows/ci.yml)) runs on every PR and push to `dev`/`stage`/`main`:

1. **detect-changes** — path filtering figures out which services changed.
2. **unit-tests** — matrix job per changed service; 100% coverage enforced; reports uploaded as artifacts.
3. **e2e-tests** — builds all images, boots the compose stack, clones the `url-shortener-e2e-tests` repo, runs the suite through the gateway.
4. **publish-images** — on `dev`/`stage` pushes and `v*` tags, pushes **all** images to GHCR tagged `:<sha>` plus `:dev`/`:stage` (moving) or `:vX.Y.Z` + `:latest` (immutable).

**CD** ([deploy.yml](.github/workflows/deploy.yml)) runs after a green CI run: `dev` push → dev droplet, `stage` push → stage droplet, `vX.Y.Z` tag → prod droplet **after a human approves** (GitHub Environment protection). Each deploy SSHes to the droplet, pins `IMAGE_TAG`, runs `compose pull && up -d`, then smoke-tests from outside (health check + create-and-follow a real short link).

## Deployment (DigitalOcean)

Three droplets (dev/stage/prod), each running the same compose stack from GHCR images. One-time setup per environment, once you have credentials (`doctl auth init`):

```bash
export GHCR_USER=<github-user> GHCR_TOKEN=<PAT with read:packages>
./scripts/provision-droplet.sh dev      # also: stage, prod
```

The script creates the droplet (Docker marketplace image), a cloud firewall (22/80/443), a non-root `deploy` user, lays out `/opt/url-shortener`, and logs in to GHCR. It prints the three secrets (`DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_SSH_KEY`) to set on the matching GitHub Environment — after that, deploys are fully automatic.

**Rollback**: prod images are immutable version tags, so rolling back is redeploying the previous version — on the droplet: `sed -i 's/^IMAGE_TAG=.*/IMAGE_TAG=v1.1.0/' deploy/app.env && docker compose --env-file deploy/app.env -f docker-compose.yml -f docker-compose.deploy.yml up -d` (or re-run the deploy workflow for the previous tag).

**Scale path** (no code changes required): resize the droplet → move Postgres/Redis to DO Managed databases (edit `DATABASE_URL`/`REDIS_URL` in the env file) → add a second droplet behind a DO Load Balancer → DOKS if multi-node autoscaling ever becomes necessary. Rationale in [DESIGN.md](DESIGN.md).

## Operations

```bash
docker compose ps                      # health of all services
docker compose logs -f redirect-service
docker compose down                    # stop (keeps data volume)
docker compose down -v                 # stop and wipe the database
```

Key environment variables: `DATABASE_URL`, `REDIS_URL`, `BASE_URL`, `ALIAS_LENGTH`, `ALIAS_MAX_RETRIES`, `CACHE_TTL_SECONDS`, `LOG_LEVEL`.
