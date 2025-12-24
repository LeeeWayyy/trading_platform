# Stage 1: Build dependencies (service-only requirements)
FROM python:3.11-slim AS builder

WORKDIR /app
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends gcc libpq-dev && rm -rf /var/lib/apt/lists/*

# Install service dependencies
COPY apps/alert_worker/requirements.txt ./
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Stage 2: Runtime image
FROM python:3.11-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends libpq5 && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local
COPY libs /app/libs
COPY apps/alert_worker /app/apps/alert_worker

RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

CMD ["python", "-m", "apps.alert_worker.entrypoint"]
