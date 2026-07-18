FROM python:3.12-slim AS builder
WORKDIR /build
COPY pyproject.toml ./
COPY redirect_service ./redirect_service
RUN pip install --no-cache-dir --prefix=/install .

FROM python:3.12-slim
RUN useradd --create-home --uid 1000 appuser
WORKDIR /app
COPY --from=builder /install /usr/local
USER appuser
EXPOSE 8000
CMD ["uvicorn", "redirect_service.main:app", "--host", "0.0.0.0", "--port", "8000"]
