FROM python:3.12-slim AS builder
WORKDIR /build
COPY pyproject.toml ./
COPY shortener_service ./shortener_service
RUN pip install --no-cache-dir --prefix=/install .

FROM python:3.12-slim
RUN useradd --create-home --uid 1000 appuser
WORKDIR /app
COPY --from=builder /install /usr/local
COPY alembic.ini ./
COPY alembic ./alembic
USER appuser
EXPOSE 8000
# Apply migrations before serving; safe to run concurrently thanks to
# Alembic's advisory locking on the version table.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn shortener_service.main:app --host 0.0.0.0 --port 8000"]
