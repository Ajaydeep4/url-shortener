# Changelog

All notable changes to the URL shortener platform are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project adheres to [Semantic Versioning](https://semver.org/):
MAJOR = breaking API change, MINOR = new backwards-compatible feature,
PATCH = backwards-compatible fix.

The platform is versioned as a single unit (one version spans all services),
because the Compose stack is deployed as one unit. Per-service history is
visible in the sections below via the `(service)` prefixes.

## [Unreleased]

### Added

- (platform) Deployment pipeline: GHCR image publishing per branch/tag,
  three-environment (dev/stage/prod) droplet deploys, release tooling.

## [1.1.0] - 2026-07-18

### Added

- (shortener-service) Optional `ttl_seconds` on link creation; links expire
  and stop resolving after their TTL (`expires_at` column, migration 0002).
- (redirect-service) Expired links return 404; cache entry TTL is capped at
  the link's remaining lifetime so cache can never outlive the link.

### Changed

- (platform) Redis is memory-bounded: `maxmemory 256mb` with `allkeys-lru`
  eviction, so cache size (and cost) stays capped at any scale.

## [1.0.0] - 2026-07-17

### Added

- (shortener-service) Create short URLs with auto-generated or custom
  aliases; atomic collision handling via `INSERT ... ON CONFLICT`; metadata
  endpoint (creation time, access count).
- (redirect-service) Redirects with cache-aside Redis caching; click events
  published to a Redis Stream.
- (analytics-worker) Consumes click events and applies batched, atomic
  access-count updates in PostgreSQL.
- (gateway) NGINX API gateway: routing, request-ID propagation.
- (platform) Docker Compose stack, JSON logging with request IDs, health
  checks, Alembic migrations, 100% unit-test line coverage per service,
  e2e suite in a separate repository, GitHub Actions CI.
