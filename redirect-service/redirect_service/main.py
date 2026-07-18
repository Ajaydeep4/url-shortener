import logging
import re
import time
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from importlib.metadata import version as pkg_version

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import text

from redirect_service import cache, events
from redirect_service.config import get_settings
from redirect_service.db import dispose_engine, fetch_url, get_engine
from redirect_service.logging_config import configure_logging, request_id_var

logger = logging.getLogger(__name__)

# Single platform version, stamped into pyproject.toml by scripts/release.sh;
# surfaced via /healthz so any environment can be asked what it is running.
SERVICE_VERSION = pkg_version("redirect-service")

ALIAS_PATH_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.service_name, settings.log_level)
    logger.info("service starting")
    yield
    await dispose_engine()
    await cache.close()
    logger.info("service stopped")


app = FastAPI(title="URL Shortener - redirect-service", lifespan=lifespan)


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
    request_id_var.set(request_id)
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = round((time.perf_counter() - start) * 1000, 2)
    response.headers["x-request-id"] = request_id
    logger.info(
        "request handled",
        extra={
            "extra_fields": {
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            }
        },
    )
    return response


@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled error while processing request")
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "internal_error", "message": "internal server error"}},
    )


def _not_found(alias: str) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"error": {"code": "not_found", "message": f"alias '{alias}' does not exist"}},
    )


@app.get("/healthz", include_in_schema=False)
async def healthz() -> JSONResponse:
    status: dict[str, str] = {"version": SERVICE_VERSION}
    try:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        status["database"] = "ok"
    except Exception:
        logger.exception("health check failed: database unreachable")
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "database": "error", "version": SERVICE_VERSION},
        )
    # Redis being down only degrades performance (cache misses, lost click
    # events), it does not stop redirects, so it never fails the health check.
    try:
        await cache.get_client().ping()
        status["cache"] = "ok"
    except Exception:
        status["cache"] = "unavailable"
    return JSONResponse(content={"status": "ok", **status})


@app.get("/{alias}")
async def redirect(alias: str, background_tasks: BackgroundTasks):
    if not ALIAS_PATH_PATTERN.match(alias):
        return _not_found(alias)

    long_url = await cache.get_long_url(alias)
    cache_hit = long_url is not None
    if long_url is None:
        row = await fetch_url(alias)
        if row is None:
            return _not_found(alias)
        long_url, expires_at = row
        # Cap the cache entry's TTL at the link's remaining lifetime so an
        # expiring link can never be served from cache past its expiry.
        ttl = get_settings().cache_ttl_seconds
        if expires_at is not None:
            remaining = int((expires_at - datetime.now(UTC)).total_seconds())
            ttl = max(1, min(ttl, remaining))
        background_tasks.add_task(cache.set_long_url, alias, long_url, ttl)

    # Counting happens after the response is sent so hot links stay fast.
    background_tasks.add_task(events.publish_click, alias)

    logger.info(
        "redirect served",
        extra={"extra_fields": {"alias": alias, "cache_hit": cache_hit}},
    )
    # 302 (not 301) so browsers keep coming back and clicks stay countable;
    # no-store stops intermediaries from replaying the redirect silently.
    return RedirectResponse(
        long_url, status_code=302, headers={"Cache-Control": "no-store"}
    )
