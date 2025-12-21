"""
FastAPI metrics sidecar for Prometheus metrics collection.

This service runs alongside the Streamlit web_console app to expose
Prometheus metrics at /metrics endpoint with multiprocess support.

Component: P2T3-Phase3-Component6+7
"""

import logging
import os
import threading

import redis.exceptions
from fastapi import FastAPI, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    generate_latest,
    multiprocess,
)

from apps.web_console.services.cb_metrics import (
    CB_VERIFICATION_FAILED_SENTINEL,
    cb_staleness_seconds,
    update_cb_staleness_metric,
)
from libs.redis_client import RedisClient, RedisConnectionError

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Web Console Metrics",
    description="Prometheus metrics endpoint for OAuth2/mTLS authentication system",
    version="1.0.0",
)

# Cache Redis client at module level (reused across scrapes)
_metrics_redis_client: RedisClient | None = None
_client_lock = threading.Lock()  # Thread-safe client initialization


def _get_redis_client() -> RedisClient | None:
    """Get Redis client for metrics collection with retry on failure.

    Unlike one-shot init, this retries on each call if client is None,
    allowing recovery after transient Redis outages at startup.

    Thread-safe: Uses lock to prevent race conditions during concurrent
    requests in FastAPI's thread pool.
    """
    global _metrics_redis_client

    with _client_lock:
        # Return cached client if available and healthy
        if _metrics_redis_client is not None:
            try:
                if _metrics_redis_client.health_check():
                    return _metrics_redis_client
                _metrics_redis_client = None  # Reset for retry
            except Exception:
                _metrics_redis_client = None  # Reset for retry

        # Try to create/reconnect
        try:
            _metrics_redis_client = RedisClient(
                host=os.getenv("REDIS_HOST", "localhost"),
                port=int(os.getenv("REDIS_PORT", "6379")),
                db=int(os.getenv("REDIS_DB", "0")),
                password=os.getenv("REDIS_PASSWORD"),
            )
            return _metrics_redis_client
        except (
            redis.exceptions.RedisError,
            RedisConnectionError,
            ConnectionError,
            TimeoutError,
        ) as exc:
            logger.warning("Failed to create Redis client for metrics: %s", exc)
            return None


@app.get("/metrics")
def metrics() -> Response:
    """
    Prometheus metrics endpoint with multiprocess support.

    Collects metrics from all Streamlit worker processes via shared
    prometheus_multiproc_data volume (production), or from default
    REGISTRY in single-process/dev mode.

    Updates CB staleness metric before each collection.

    Returns:
        Response with Prometheus text format metrics
    """
    # Update CB staleness BEFORE collecting metrics
    # CRITICAL: If Redis is unavailable, set sentinel to trigger alerts
    redis_client = _get_redis_client()
    if redis_client:
        update_cb_staleness_metric(redis_client)
    else:
        # Redis unavailable - set sentinel to trigger CBVerificationFailed alert
        logger.warning("Redis unavailable for CB staleness check - setting failure sentinel")
        cb_staleness_seconds.set(CB_VERIFICATION_FAILED_SENTINEL)

    multiproc_dir = os.getenv("PROMETHEUS_MULTIPROC_DIR")

    try:
        if multiproc_dir:
            # Production: Collect metrics from all worker processes
            registry = CollectorRegistry()
            multiprocess.MultiProcessCollector(registry)  # type: ignore[no-untyped-call]
        else:
            # Dev/single-process: Use default registry
            from prometheus_client import REGISTRY as registry

        return Response(
            content=generate_latest(registry),
            media_type=CONTENT_TYPE_LATEST,
        )
    except Exception as exc:
        # Graceful degradation: log error and return 503 so Prometheus can alert
        logger.exception("Failed to collect metrics: %s", exc)
        return Response(
            content=b"# Error collecting metrics\n",
            media_type=CONTENT_TYPE_LATEST,
            status_code=503,
        )


@app.get("/health")
def health() -> dict[str, str]:
    """
    Health check endpoint for Docker/k8s readiness/liveness probes.

    Returns:
        Dict with status
    """
    return {"status": "healthy"}
