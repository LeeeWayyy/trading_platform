"""NiceGUI entry point for the web console (C0 minimal)."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from nicegui import app, ui
from starlette.middleware.trustedhost import TrustedHostMiddleware

from apps.web_console_ng import config
from apps.web_console_ng.auth.audit import AuthAuditLogger
from apps.web_console_ng.auth.middleware import AuthMiddleware, SessionMiddleware
from apps.web_console_ng.auth.session_store import get_session_store
from apps.web_console_ng.core.admission import AdmissionControlMiddleware
from apps.web_console_ng.core.client import AsyncTradingClient
from apps.web_console_ng.core.connection_events import setup_connection_handlers
from apps.web_console_ng.core.health import setup_health_endpoint
from apps.web_console_ng.core.state_manager import get_state_manager
from apps.web_console_ng.ui.disconnect_overlay import inject_disconnect_overlay

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool


def _init_db_pool() -> AsyncConnectionPool | None:
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        return None
    try:
        import psycopg_pool
    except ImportError:
        return None

    min_size = int(os.getenv("DB_POOL_MIN_SIZE", "1"))
    max_size = int(os.getenv("DB_POOL_MAX_SIZE", "5"))
    timeout = float(os.getenv("DB_POOL_TIMEOUT", "10.0"))
    return psycopg_pool.AsyncConnectionPool(
        dsn,
        min_size=min_size,
        max_size=max_size,
        timeout=timeout,
        open=False,
    )


db_pool = _init_db_pool()
audit_logger = AuthAuditLogger.get(
    db_enabled=config.AUDIT_LOG_DB_ENABLED,
    db_pool=db_pool,
)
session_store = get_session_store(audit_logger=audit_logger)
state_manager = get_state_manager()
trading_client = AsyncTradingClient.get()

# Import routes after audit logger + session store are configured.
from apps.web_console_ng.auth import routes as auth_routes  # noqa: E402,F401

# Middleware added in LIFO order: TrustedHost -> Admission -> Session -> Auth.
# AdmissionControlMiddleware MUST run before Session/Auth to enforce capacity limits
# at the ASGI level before WebSocket upgrade completes.
app.add_middleware(AuthMiddleware)
app.add_middleware(
    SessionMiddleware,
    session_store=session_store,
    trusted_proxies=config.TRUSTED_PROXY_IPS,
)
app.add_middleware(AdmissionControlMiddleware)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=config.ALLOWED_HOSTS)

# Register lifespan handler BEFORE startup hooks run.
# This ensures graceful-drain and single-worker checks are active when the app starts.
setup_health_endpoint()


async def startup() -> None:
    """Startup hook wiring C3 connection recovery components."""
    # Register NiceGUI lifecycle hooks for client ID generation, metrics, handshake coordination
    setup_connection_handlers()
    if db_pool is not None:
        await db_pool.open()
    await trading_client.startup()
    await audit_logger.start()
    inject_disconnect_overlay()


async def shutdown() -> None:
    """Shutdown hook for graceful cleanup.

    Note: Graceful drain (503 on readiness, SIGTERM handling) is managed by
    the lifespan context in health.py. This hook handles app-specific resource
    cleanup only, avoiding duplication between lifespan and on_shutdown.
    """
    from apps.web_console_ng.core.redis_ha import get_redis_store

    await trading_client.shutdown()
    await audit_logger.stop()
    if db_pool is not None:
        await db_pool.close()
    await state_manager.close()
    # Close Redis connections to prevent "Unclosed connection" warnings
    try:
        redis = get_redis_store()
        await redis.close()
    except Exception:
        pass  # Best-effort cleanup


app.on_startup(startup)
app.on_shutdown(shutdown)


if __name__ == "__main__":
    ui.run(
        host=config.HOST,
        port=config.PORT,
        title=config.PAGE_TITLE,
        reload=config.DEBUG,
        show=False,
        reconnect_timeout=3.0,
    )
