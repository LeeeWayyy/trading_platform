# Backtest Worker Dockerfile - Multi-stage Build
FROM python:3.11-slim as builder
WORKDIR /build
RUN apt-get update && apt-get install -y --no-install-recommends gcc && rm -rf /var/lib/apt/lists/*
COPY apps/backtest_worker/requirements.txt .
RUN pip install --no-cache-dir --target=/build/packages -r requirements.txt

FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client curl && rm -rf /var/lib/apt/lists/*
RUN groupadd -r appuser && useradd -r -g appuser -u 1000 appuser
COPY --from=builder /build/packages /usr/local/lib/python3.11/site-packages
COPY libs /app/libs
COPY apps/backtest_worker /app/apps/backtest_worker
RUN mkdir -p /app/data/backtest_results && chown -R appuser:appuser /app
USER appuser
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import os, redis; url=os.environ.get('REDIS_URL'); r=redis.from_url(url); r.ping()" || exit 1
CMD ["python", "-m", "apps.backtest_worker.entrypoint"]
