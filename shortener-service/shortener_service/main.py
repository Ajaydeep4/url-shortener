import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text

from shortener_service.api import router
from shortener_service.config import get_settings
from shortener_service.db.session import dispose_engine, get_engine
from shortener_service.exceptions import DomainError
from shortener_service.logging_config import configure_logging, request_id_var

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.service_name, settings.log_level)
    logger.info("service starting")
    yield
    await dispose_engine()
    logger.info("service stopped")


app = FastAPI(title="URL Shortener - shortener-service", lifespan=lifespan)
app.include_router(router)


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


@app.exception_handler(DomainError)
async def domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.error_code, "message": exc.message}},
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "validation_error",
                "message": "request validation failed",
                "details": jsonable_encoder(exc.errors()),
            }
        },
    )


@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled error while processing request")
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "internal_error", "message": "internal server error"}},
    )


@app.get("/healthz", include_in_schema=False)
async def healthz() -> JSONResponse:
    try:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        logger.exception("health check failed: database unreachable")
        return JSONResponse(status_code=503, content={"status": "unhealthy", "database": "error"})
    return JSONResponse(content={"status": "ok", "database": "ok"})
