# Implementation Notes — URL Shortener (local working document, never pushed)

This document maps every requirement from the problem statement to its implementation, answers the key engineering questions (concurrency, collisions, race conditions, custom aliases), and details every technology used in each repository and why.

---

## Section A — How every expectation is implemented

### Functional Expectations

#### A1. Creation: accept a long URL, generate a unique shortened URL

**How**: `POST /api/v1/urls` on the shortener-service (routed through the NGINX gateway on port 8080).

- The request body is validated by Pydantic (`shortener-service/shortener_service/schemas.py`): `long_url` must be a well-formed http/https URL (`HttpUrl` type).
- If no custom alias is given, `generate_alias()` in `shortener-service/shortener_service/service.py` produces a 7-character base62 string using `secrets.choice` — cryptographically random, unguessable, no modulo bias. Keyspace is 62^7 ≈ 3.5 trillion, so collisions are astronomically rare.
- Uniqueness is guaranteed by the database, not application code: the insert is atomic against a unique index on `alias` (see Section B).
- Response: `201` with `alias`, `short_url` (built from `BASE_URL`), `long_url`, `created_at`, `expires_at`.

**How to use**:

```bash
curl -X POST http://localhost:8080/api/v1/urls \
  -H 'Content-Type: application/json' \
  -d '{"long_url": "https://en.wikipedia.org/wiki/URL_shortening"}'
```

```json
{"alias": "d4K9x2A", "short_url": "http://localhost:8080/d4K9x2A",
 "long_url": "https://en.wikipedia.org/wiki/URL_shortening",
 "created_at": "2026-07-18T05:47:27Z", "expires_at": null}
```

**Why this way**: random codes (vs sequential IDs encoded in base62) don't leak creation volume and can't be enumerated by crawlers. DB-enforced uniqueness works across any number of service replicas.

#### A2. Customization & Collision: custom aliases, simultaneous requests handled cleanly

**How**: the same endpoint accepts optional `custom_alias`.

1. Validation first (`validate_custom_alias` in `shortener-service/shortener_service/service.py`): regex `^[a-zA-Z0-9_-]{3,32}$`, plus a reserved-word list (`api`, `docs`, `healthz`, `metrics`, ...) so users can't shadow real routes. Failures return `422` with code `invalid_alias` — the database is never touched.
2. The claim is one atomic statement (`shortener-service/shortener_service/repository.py`):
   `INSERT INTO urls ... ON CONFLICT (alias) DO NOTHING RETURNING *`
   If two users request the same alias simultaneously, PostgreSQL's unique index arbitrates: exactly one insert returns a row (`201`), the other returns nothing, which the service maps to `409 Conflict` with code `alias_conflict`.
3. Auto-generated aliases use the same statement; on the rare collision the service retries with a fresh random alias (bounded at `ALIAS_MAX_RETRIES=5`), then returns `503 alias_generation_exhausted` if truly exhausted.

**How to use** — the custom alias goes in the `custom_alias` field of the POST body (same endpoint as A1, optionally combined with `ttl_seconds`):

```bash
curl -X POST http://localhost:8080/api/v1/urls \
  -H 'Content-Type: application/json' \
  -d '{"long_url": "https://example.com/campaign", "custom_alias": "promo"}'
```

First caller gets `201`:

```json
{"alias": "promo", "short_url": "http://localhost:8080/promo",
 "long_url": "https://example.com/campaign", "created_at": "2026-07-18T05:47:27Z", "expires_at": null}
```

Anyone else asking for `promo` (a second earlier or a year later) gets `409`:

```json
{"error": {"code": "alias_conflict", "message": "alias 'promo' is already taken"}}
```

Invalid alias shapes (spaces, too short, reserved words like `api`) get `422`:

```json
{"error": {"code": "invalid_alias", "message": "custom_alias must be 3-32 characters of letters, digits, hyphen or underscore"}}
```

**Proof**: `url-shortener-e2e-tests/integration/test_concurrent_custom_alias.py` fires 10 simultaneous requests for one alias against the live stack and asserts exactly one `201` and nine `409`s. Passes.

**Why this way**: see Section B — the whole design avoids check-then-insert races by construction.

#### A3. Redirection + caching for heavily accessed links

**How**: `GET /{alias}` on the redirect-service.

- Alias shape is validated with a regex before any backend work (junk paths 404 immediately).
- Cache-aside: Redis `GET url:{alias}` first. Hit → redirect immediately, zero DB work. Miss → single SQL lookup, then the cache is populated (TTL 1 hour, configurable) as a background task after the response is already sent.
- Responds `302 Found` (deliberately not `301` — browsers cache 301s permanently, which would bypass the server and silently break access counting) with `Cache-Control: no-store`.
- Redis failures degrade gracefully: a failed cache read is treated as a miss and served from Postgres; the service never errors because the cache is down (`redirect-service/redirect_service/cache.py` wraps every call).
- Cache memory is bounded regardless of scale: Redis runs with `maxmemory 256mb` + `allkeys-lru` (see `docker-compose.yml`), so at millions of requests the cap holds — least-recently-used (coldest) entries are evicted first and the hot set stays resident. Per-entry 1-hour TTLs additionally expire idle entries on their own.
- Expired links (TTL feature) are filtered in SQL (`expires_at > now()`) and the cache entry TTL is capped at the link's remaining lifetime, so an expired link can never be served from cache.

**How to use** — just open the short link (or curl it; `-i` shows the redirect headers):

```bash
curl -i http://localhost:8080/promo
# HTTP/1.1 302 Found
# location: https://example.com/campaign
# cache-control: no-store

curl -i http://localhost:8080/does-not-exist
# HTTP/1.1 404 Not Found
# {"error": {"code": "not_found", "message": "alias 'does-not-exist' does not exist"}}
```

#### A4. Metadata: creation time, access counts

**How**: `GET /api/v1/urls/{alias}` returns `alias`, `short_url`, `long_url`, `created_at`, `expires_at`, `access_count`, `is_custom`.

Access counts are maintained asynchronously: the redirect-service publishes a click event to a Redis Stream after each redirect (as a background task, so the hot path never waits); the analytics-worker consumes events in batches (up to 500), aggregates per alias, and applies atomic `UPDATE urls SET access_count = access_count + :n` statements. Counts are eventually consistent (typically < 1–2 s lag) — the deliberate trade for keeping redirects fast.

**How to use** — metadata is *returned* by a GET (never sent by the client; the server generates it):

```bash
curl http://localhost:8080/api/v1/urls/promo
```

```json
{"alias": "promo", "short_url": "http://localhost:8080/promo",
 "long_url": "https://example.com/campaign",
 "created_at": "2026-07-18T05:47:27Z", "expires_at": null,
 "access_count": 3, "is_custom": true}
```

#### A4b. Link expiration (TTL extension)

**How to use** — add `ttl_seconds` to the create request; the link 404s after it elapses:

```bash
curl -X POST http://localhost:8080/api/v1/urls \
  -H 'Content-Type: application/json' \
  -d '{"long_url": "https://example.com/flash-sale", "custom_alias": "sale", "ttl_seconds": 86400}'
# -> "expires_at": "2026-07-19T05:47:27Z" (24h from creation); omit ttl_seconds for a permanent link
```

### Engineering Expectations

#### A5. Architecture flow diagram

`DESIGN.md` section 1: system diagram, a request-lifecycle/data-flow diagram covering both the create and redirect paths, and sequence diagrams for the redirect flow and the concurrent-alias race.

#### A6. Validation, error handling, thread-safe updates

- **Input validation**: Pydantic at the API boundary (URL format, TTL bounds 1s–10y, alias length caps) + service-layer rules (alias regex, reserved words, self-referential URL rejection).
- **Error handling**: typed domain exceptions (`shortener-service/shortener_service/exceptions.py`) mapped by FastAPI handlers to one consistent envelope `{"error": {"code": ..., "message": ...}}` with correct HTTP statuses (422/409/404/503). Unexpected errors are logged with stack trace + request ID and return a generic `500` — internals never leak.
- **Thread-safe (actually process-safe) updates**: there are *no app-level locks anywhere*. Every mutation is a single atomic SQL statement (`INSERT ... ON CONFLICT`, `UPDATE ... SET count = count + n`), so correctness holds not just across threads but across processes and container replicas. See Section B.

#### A7. Testing: unit tests for core logic, integration tests verifying the redirect

- **Unit tests inside each service** (`<service>/tests/`), 93 total, **100% line coverage enforced** (`--cov-fail-under=100` via pytest-cov). All external systems are mocked: `AsyncMock` repositories + FastAPI `dependency_overrides` for Postgres; mocked Redis clients with forced exceptions to cover the degradation branches.
- **Integration (e2e) tests in the separate `url-shortener-e2e-tests` repo**, 17 tests driving the running stack over HTTP through the gateway: create→redirect→metadata round trips, the concurrency race, the full error contract, TTL expiry, count convergence.

#### A8. CI/CD: basic pipeline

`.github/workflows/ci.yml` (GitHub Actions): change detection (`dorny/paths-filter`) → per-changed-service unit test jobs with the 100% coverage gate and report artifacts → e2e job that builds all Docker images, boots the compose stack, clones the tests repo, and runs the suite → on `main`, publishes changed services' images to GitHub Container Registry tagged with the commit SHA.

#### A9. Documentation

Root `README.md`: two-repo layout, Docker and native (devcontainer) setup paths, API usage with curl examples, testing instructions (unit + e2e + coverage), CI/CD description, DigitalOcean deployment path. Per-project READMEs in the tests repo; `DESIGN.md` for architecture.

### Extensions

- **TTL expiration — implemented**: `ttl_seconds` on creation → `expires_at` column (Alembic migration `0002`); redirect lookup filters expired rows in SQL; cache TTL capped at remaining lifetime; verified by a live e2e test (2-second link expires and 404s).
- **DigitalOcean deployment — pending credentials**: CI already produces deployable GHCR images; once `doctl auth init` is done, deploy = run those images on a droplet (or App Platform) with managed Postgres/Redis.

---

## Section B — The hard questions, answered explicitly

### B1. How do we handle concurrency?

**Principle: no in-process locks; correctness is delegated to PostgreSQL.** A mutex in Python code only protects one process — it breaks the moment you run two Uvicorn workers or scale to two containers. Instead, every operation that could race is a single atomic SQL statement, so the guarantee holds across any number of replicas:

- Alias claims: `INSERT ... ON CONFLICT (alias) DO NOTHING RETURNING *` — the unique index is the arbiter.
- Count updates: `UPDATE urls SET access_count = access_count + :n WHERE alias = :alias` — the read-modify-write happens inside Postgres under row-level locking, so concurrent workers cannot lose increments.
- Within each process, all IO is async (asyncpg, redis-py asyncio); request handlers share no mutable state; connection pools are async-safe.
- Startup: Alembic migrations lock the version table, so replicas starting simultaneously can't corrupt the schema; compose healthchecks order service startup.

### B2. How do we handle collisions and race conditions?

The naive approach — `SELECT` to check availability, then `INSERT` — has a time-of-check/time-of-use window where two requests both see "available" and both insert. We never do that. The insert *is* the availability check, atomically:

```sql
INSERT INTO urls (alias, long_url, is_custom, expires_at)
VALUES (:alias, :long_url, :is_custom, :expires_at)
ON CONFLICT (alias) DO NOTHING
RETURNING *;
```

- Row returned → we won the alias → `201`.
- Nothing returned → someone else holds it (whether created a year ago or 2 microseconds ago — same code path) → `409 Conflict` for custom aliases, or a retry with a fresh random code for auto-generated ones (bounded, then `503`).

PostgreSQL guarantees exactly one winner per alias regardless of concurrency level. Verified end-to-end by the 10-way simultaneous-request test.

### B3. How do we handle custom aliases?

1. Optional `custom_alias` field on the create endpoint.
2. Validated before any DB work: `^[a-zA-Z0-9_-]{3,32}$` + reserved-word rejection (`api`, `docs`, `healthz`, ...) → `422 invalid_alias` on failure.
3. Claimed with the same atomic insert as above → exactly one winner, losers get `409 alias_conflict`.
4. Stored with `is_custom = true` (visible in metadata); afterwards behaves identically to generated aliases for redirects, caching, counting, TTL.

---

## Section C — Repositories, technologies, and why

### C1. Repo: `url-shortener` (code monorepo)

One repository, four independently deployable services (monorepo ≠ monolith: each service has its own Dockerfile, image, CI job via path filtering, and could scale independently). Monorepo chosen because: 1 developer, 4 tightly coupled services, atomic cross-service changes, single CI setup; polyrepo's benefits (team isolation, access control) don't apply at this scale.

#### gateway/ — NGINX API gateway

The single public entry point (port 8080). Routes `/api/*` + `/docs` to the shortener-service and everything else (`/{alias}`) to the redirect-service; generates and propagates `X-Request-ID`; emits JSON access logs.

| Tech | Why |
|---|---|
| NGINX 1.27 | Battle-tested reverse proxy with effectively zero overhead; declarative path routing; `$request_id` built in for cross-service log correlation. Alternatives (Traefik, Kong) add dynamic config/plugin machinery this project doesn't need. |

#### shortener-service/ — the write side

Creates short URLs (auto + custom alias, optional TTL), serves metadata, owns the database schema (only service that runs migrations).

| Tech / library | Why |
|---|---|
| Python 3.12 | Required language; async support fits IO-bound services. |
| FastAPI | Async-native framework; Pydantic validation built in; auto OpenAPI docs at `/docs`; dependency injection makes the service layer trivially unit-testable (override the repository in tests). |
| Uvicorn | The standard ASGI production server for FastAPI; multi-worker capable, graceful shutdown. |
| Pydantic v2 + pydantic-settings | Declarative request validation (`HttpUrl`, field bounds) and typed 12-factor config from env vars — misconfig fails at startup, not at request time. |
| SQLAlchemy 2 (async) + asyncpg | Typed models and explicit SQL where it matters (the `ON CONFLICT` insert is written out, not hidden); asyncpg is the fastest asyncio Postgres driver. |
| Alembic | Versioned, repeatable schema migrations (`0001` urls table, `0002` expires_at); runs at container start with advisory locking so replicas can't race. |
| pytest / pytest-asyncio / pytest-cov / httpx (dev) | Standard test stack; httpx's `ASGITransport` drives the real app in-process for route/middleware/handler tests; 100% coverage gate. |

#### redirect-service/ — the read side

The latency-critical hot path: `GET /{alias}` → Redis cache → Postgres fallback → `302`, then background cache-fill + click event. Stateless; never writes to the urls table; scales horizontally.

| Tech / library | Why |
|---|---|
| FastAPI + Uvicorn + pydantic-settings | Same rationale as above; consistency across services lowers cognitive load. |
| redis-py (asyncio) | Official Redis client; used for the cache (GET/SET with TTL, 500 ms timeouts so a slow Redis can't stall the hot path) and for publishing click events (XADD to a stream, best-effort). |
| SQLAlchemy async + asyncpg | Single read query (`SELECT long_url, expires_at ... WHERE alias = ... AND not expired`) via `text()` — no ORM models needed for one query. |

#### analytics-worker/ — the counting pipeline

Headless consumer (no HTTP). Reads click events from the Redis Stream via a consumer group, aggregates batches in memory, applies atomic count updates to Postgres, then XACKs. Crash recovery: on startup it drains its own unacknowledged events first (at-least-once delivery). If it's down, events queue in the stream (bounded MAXLEN ~1M) and counts converge on restart — redirects are never affected.

| Tech / library | Why |
|---|---|
| redis-py (asyncio) — Streams + consumer groups | XREADGROUP/XACK gives queue semantics (delivery tracking, replay of unacked messages, bounded retention) without operating a separate broker; Kafka would be justified only at far higher volumes. |
| SQLAlchemy async + asyncpg | Batched `UPDATE ... SET access_count = access_count + :n` in one transaction per batch. |
| Plain asyncio + signal handlers | Graceful SIGTERM shutdown; error backoff so one poison batch can't kill the loop. |

#### Shared infrastructure (compose stack)

| Tech | Why |
|---|---|
| PostgreSQL 16 | The correctness backbone: unique index + `ON CONFLICT` is what makes concurrent alias claims safe; durable system of record; transactions. |
| Redis 7 | Two roles: sub-millisecond cache for hot redirects, and the click-event stream. One small server, two problems solved; roles can be split later via config only. |
| Docker + Compose | Each service builds to an immutable image (multi-stage, non-root); compose declares the whole stack (versions, networking, healthcheck-based startup ordering) so `docker compose up` reproduces the system anywhere; CI runs the same compose file. |
| GitHub Actions | CI/CD native to where the code lives; path-filtered per-service jobs; service containers/Docker available on runners; GHCR publishing with the built-in token. |
| pytest-cov | The de-facto Python coverage tool (wraps coverage.py); enforces the 100% line-coverage gate in CI. |

### C2. Repo: `url-shortener-e2e-tests` (tests repo)

End-to-end tests for the URL shortener system as a whole. Kept as a separate repository because e2e tests exercise the *system* through its public API and belong to no single service (industry standard: unit tests co-located with code, cross-service e2e separate). No service code is imported — everything goes over HTTP through the gateway, exactly like a real client. CI in the code repo clones this repo automatically for its e2e job.

| Tech / library | Why |
|---|---|
| pytest + pytest-asyncio | Same test runner as the services; async tests let the concurrency test fire truly simultaneous requests with `asyncio.gather`. |
| httpx | Async HTTP client; `follow_redirects=False` so tests can assert on the raw `302` and `Location` header. |

What it proves: the concurrent custom-alias race (exactly one 201 of 10), create/redirect/metadata round trips, cache-consistency of repeated redirects, the full error contract, TTL expiry live, and access-count convergence through the async pipeline.

---

## Section D — Status vs the problem statement

| Item | Status |
|---|---|
| Creation (long URL → unique short URL) | Done, tested |
| Custom aliases | Done, tested |
| Collision/race handling (simultaneous same-alias requests) | Done, proven by 10-way concurrency e2e test |
| Redirection | Done, tested |
| Caching for heavily accessed links | Done (Redis cache-aside + graceful degradation), tested |
| Metadata (creation time, access counts) | Done, tested (incl. count convergence) |
| Architecture flow diagram (request lifecycle + data flow) | Done — DESIGN.md section 1 |
| Sensible error handling | Done — typed exceptions, consistent envelope, correct statuses |
| Input validation | Done — Pydantic + service-layer rules |
| Thread-safe DB/memory updates | Done — atomic SQL only, no app locks, replica-safe |
| Unit tests for core logic | Done — 93 tests, 100% line coverage, all deps mocked |
| Integration tests verifying the redirect | Done — 17 e2e tests against the live stack |
| CI/CD basic pipeline (GitHub Actions) | Done — ci.yml (tests + coverage gate + e2e + image publish) |
| README (setup, execution, testing) | Done |
| Extension: TTL expiration | Done (bonus) |
| Extension: DigitalOcean deployment | Pending your `doctl auth init`; images + instructions ready |
| Mandatory: push code + diagram to personal GitHub | Pending your `gh auth login` (both repos committed and ready) |
