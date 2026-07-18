FROM python:3.12-slim AS builder
WORKDIR /build
COPY pyproject.toml ./
COPY analytics_worker ./analytics_worker
RUN pip install --no-cache-dir --prefix=/install .

FROM python:3.12-slim
RUN useradd --create-home --uid 1000 appuser
WORKDIR /app
COPY --from=builder /install /usr/local
USER appuser
CMD ["python", "-m", "analytics_worker.main"]
