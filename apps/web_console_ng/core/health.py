# apps/web_console_ng/core/health.py
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from fastapi import Request, Response
from nicegui import app, core

from apps.web_console_ng import config
from apps.web_console_ng.core.client import AsyncTradingClient
from apps.web_console_ng.core.redis_ha import get_redis_store

logger = logging.getLogger(__name__)


# Thread-safe connection counter for METRICS AND HEALTH CHECKS ONLY
# PURPOSE: Track connection count for observability (NOT for admission control)
# Used by:
#   - connection_events.py: immediate metric updates on connect/disconnect
#   - health.py readiness: connection count in detailed response
#   - metrics.py update_resource_metrics: periodic metric sync
#
# IMPORTANT: Admission control uses asyncio.Semaphore (in admission.py) as authoritative source
#   - Semaphore: Authoritative for capacity enforcement (prevents race conditions)
#   - ConnectionCounter: Metrics-only, may briefly drift under error paths
#   - This separation is intentional: semaphore handles atomic admission, counter handles observability
class ConnectionCounter:
    """
    Thread-safe connection counter with guard against negative values.

    FOR METRICS AND OBSERVABILITY ONLY:
    - Prometheus metrics (ws_connections gauge)
    - Health check responses (/readyz connections field)

    NOT FOR ADMISSION CONTROL: Use asyncio.Semaphore in admission.py for capacity enforcement.
    """

    def __init__(self) -> None:
        self._count = 0
        self._lock = threading.Lock()

    def increment(self) -> int:
        with self._lock:
            self._count += 1
            return self._count

    def decrement(self) -> int:
        with self._lock:
            # Guard against double-decrement
            if self._count > 0:
                self._count -= 1
            return self._count

    @property
    def value(self) -> int:
        with self._lock:
            return self._count


connection_counter = ConnectionCounter()
is_draining = False

# Internal request detection configuration
# SECURITY NOTE: Hardcoded CIDRs are risky in k8s (pod subnets vary, externalTrafficPolicy
# can obscure source IPs). Prefer header-based check for k8s liveness/readiness probes.
INTERNAL_PROBE_TOKEN = os.getenv("INTERNAL_PROBE_TOKEN", "").strip()
# SECURITY: Set INTERNAL_PROBE_DISABLE_IP_FALLBACK=true in production to require token
INTERNAL_PROBE_DISABLE_IP_FALLBACK = os.getenv(
    "INTERNAL_PROBE_DISABLE_IP_FALLBACK", "false"
).lower() in {"1", "true", "yes", "on"}
# Restricted fallback networks (localhost only in production, broader for dev)
INTERNAL_NETWORKS_STRICT = ["127.0.0.1", "::1"]  # Production: localhost only
INTERNAL_NETWORKS_DEV = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.1", "::1"]


def is_internal_request(request: Request) -> bool:
    """
    Check if request is from internal source (k8s probes, Prometheus).

    Detection order (most secure first):
    1. X-Internal-Probe header with shared secret (REQUIRED in production)
    2. IP-based fallback (disabled in production via INTERNAL_PROBE_DISABLE_IP_FALLBACK)

    For k8s deployments, configure probes with:
      httpGet:
        httpHeaders:
        - name: X-Internal-Probe
          value: <INTERNAL_PROBE_TOKEN>
    """
    import ipaddress

    from apps.web_console_ng.auth.client_ip import extract_trusted_client_ip

    # PRIMARY: Header-based check (most reliable in k8s)
    if INTERNAL_PROBE_TOKEN:
        probe_header = request.headers.get("x-internal-probe", "")
        if probe_header == INTERNAL_PROBE_TOKEN:
            return True

    # SECURITY: In production, IP fallback should be disabled
    if INTERNAL_PROBE_DISABLE_IP_FALLBACK:
        return False  # Token required, no fallback

    # FALLBACK: IP-based check for dev/non-k8s environments only
    # Use restricted networks (localhost) unless in DEBUG mode
    client_ip = extract_trusted_client_ip(request, config.TRUSTED_PROXY_IPS)
    allowed_networks = INTERNAL_NETWORKS_DEV if config.DEBUG else INTERNAL_NETWORKS_STRICT

    for network in allowed_networks:
        try:
            if ipaddress.ip_address(client_ip) in ipaddress.ip_network(network):
                return True
        except ValueError:
            # Invalid IP format - treat as external
            return False
    return False


@app.get("/healthz")
async def liveness_check() -> Response:
    """
    Liveness probe - always returns 200 unless process is unhealthy.

    k8s uses this to determine if the pod should be restarted.
    Does NOT check dependencies (Redis, backend) to avoid unnecessary restarts
    during transient downstream failures.
    """
    payload = {
        "status": "alive",
        "timestamp": datetime.now(UTC).isoformat(),
    }
    return Response(
        content=json.dumps(payload),
        status_code=200,
        media_type="application/json",
    )


@app.get("/readyz")
async def readiness_check(request: Request) -> Response:
    """
    Readiness probe - returns 200 when ready to serve traffic.

    k8s uses this to determine if the pod should receive traffic.
    Returns 503 during:
    - Graceful shutdown (draining)
    - Redis unavailable
    - Backend API unavailable

    External requests get minimal response for security.
    """
    global is_draining

    # During drain: return 503 immediately (liveness stays 200)
    if is_draining:
        payload = {
            "status": "draining",
            "timestamp": datetime.now(UTC).isoformat(),
        }
        return Response(
            content=json.dumps(payload),
            status_code=503,
            media_type="application/json",
        )

    checks: dict[str, str] = {}

    # Run health checks concurrently to avoid cumulative timeout (Redis 1s + backend 2s = 3s)
    # With concurrent execution, max latency is max(individual timeouts) instead of sum
    async def check_redis() -> tuple[str, str]:
        try:
            redis = get_redis_store()
            await asyncio.wait_for(redis.ping(), timeout=1.0)
            return ("redis", "ok")
        except Exception as e:
            # Sanitize error: don't expose raw exception to avoid info leak
            logger.warning(f"Redis health check failed: {e}")
            return ("redis", "error: connection_failed")

    async def check_backend() -> tuple[str, str]:
        # Backend health check is opt-in via HEALTH_CHECK_BACKEND_ENABLED
        # because it depends on DEV_* credentials which may not be configured in all envs.
        # Prefer dedicated /api/v1/health endpoint in execution_gateway for production.
        if not config.HEALTH_CHECK_BACKEND_ENABLED:
            # Backend check disabled - assume ok (Redis is the critical dependency)
            return ("backend", "ok")

        # SECURITY: In production with INTERNAL_TOKEN_SECRET, this check requires proper
        # auth context (user_id, role, strategies) which health checks don't have.
        # Skip the check to avoid false negatives. Use a dedicated unauthenticated
        # /api/v1/health endpoint on the execution_gateway for production health checks.
        internal_secret = os.getenv("INTERNAL_TOKEN_SECRET", "").strip()
        if internal_secret and not config.DEBUG:
            logger.debug("Backend health check skipped: requires auth context in production")
            return ("backend", "ok")

        try:
            client = AsyncTradingClient.get()
            # Use fetch_kill_switch_status as lightweight health check (GET, no side effects)
            # NOTE: Only works in DEBUG mode where DEV_* fallbacks are available
            await asyncio.wait_for(
                client.fetch_kill_switch_status(
                    user_id="health-check",
                    role=config.DEV_ROLE,
                    strategies=list(config.DEV_STRATEGIES),
                ),
                timeout=2.0,
            )
            return ("backend", "ok")
        except Exception as e:
            # Sanitize error: don't expose raw exception to avoid info leak
            logger.warning(f"Backend health check failed: {e}")
            return ("backend", "error: connection_failed")

    # Run checks concurrently with global timeout (prevents cumulative latency)
    try:
        results = await asyncio.wait_for(
            asyncio.gather(check_redis(), check_backend()),
            timeout=3.0,  # Global timeout slightly above max individual timeout
        )
        for name, status in results:
            checks[name] = status
    except TimeoutError:
        # Global timeout exceeded - mark any missing checks as failed
        if "redis" not in checks:
            checks["redis"] = "error: timeout"
        if "backend" not in checks:
            checks["backend"] = "error: timeout"

    # Overall status
    all_ok = all(v == "ok" for v in checks.values())
    status_code = 200 if all_ok else 503

    # Security: minimal response for external, detailed for internal
    timestamp = datetime.now(UTC).isoformat()
    if is_internal_request(request):
        response_body = {
            "status": "ready" if all_ok else "not_ready",
            "checks": checks,  # Sanitized errors only
            "connections": connection_counter.value,
            "pod": config.POD_NAME,
            "timestamp": timestamp,
        }
    else:
        # External: minimal info
        response_body = {"status": "ready" if all_ok else "not_ready", "timestamp": timestamp}

    return Response(
        content=json.dumps(response_body), status_code=status_code, media_type="application/json"
    )


async def _health_startup() -> None:
    """Health endpoint startup - register SIGTERM handler for graceful drain.

    NOTE: We use NiceGUI's on_startup hook instead of a custom lifespan to avoid
    replacing NiceGUI's internal _lifespan, which is required for the Outbox loop
    that sends UI updates to browsers.
    """
    # RUNTIME ASSERTION: Verify single-process mode
    worker_count = os.getenv("WEB_WORKERS", "1")
    if worker_count != "1":
        raise RuntimeError(
            f"NiceGUI requires single-process mode (workers=1) for admission control. "
            f"Got WEB_WORKERS={worker_count}. For horizontal scaling, use multiple pods."
        )

    # Register SIGTERM handler for graceful drain
    try:
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(
            signal.SIGTERM, lambda: asyncio.create_task(start_graceful_shutdown())
        )
        logger.info("SIGTERM handler registered for graceful shutdown")
    except NotImplementedError:
        # Windows or other platforms without signal handler support
        logger.warning("Signal handler not supported on this platform")


async def start_graceful_shutdown() -> None:
    """
    Signal pod is draining - readiness returns 503, liveness stays 200.

    Called on SIGTERM (via signal handler) or during ASGI shutdown.
    This allows k8s to stop routing new requests while existing connections
    complete their work.
    """
    global is_draining
    if is_draining:
        return  # Already draining
    is_draining = True
    # Allow configurable time for existing connections to drain
    drain_seconds = float(os.getenv("GRACEFUL_SHUTDOWN_SECONDS", "30"))
    await asyncio.sleep(drain_seconds)


_health_setup_done: bool = False


def setup_health_endpoint() -> None:
    """Register health startup hook for drain support (idempotent).

    NOTE: We use on_startup hook instead of custom lifespan to preserve
    NiceGUI's internal _lifespan which handles the Outbox loop for UI updates.
    """
    global _health_setup_done
    if _health_setup_done:
        return
    app.on_startup(_health_startup)
    _health_setup_done = True
