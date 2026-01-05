"""NiceGUI entry point for the web console (C0 minimal)."""

from __future__ import annotations

import logging

from nicegui import app, ui
from starlette.middleware.trustedhost import TrustedHostMiddleware

from apps.web_console_ng import config
from apps.web_console_ng.auth.audit import AuthAuditLogger
from apps.web_console_ng.auth.middleware import AuthMiddleware, SessionMiddleware
from apps.web_console_ng.auth.session_store import get_session_store
from apps.web_console_ng.core.admission import AdmissionControlMiddleware
from apps.web_console_ng.core.client import AsyncTradingClient
from apps.web_console_ng.core.connection_events import setup_connection_handlers
from apps.web_console_ng.core.database import close_db_pool, init_db_pool
from apps.web_console_ng.core.health import setup_health_endpoint
from apps.web_console_ng.core.state_manager import get_state_manager
from apps.web_console_ng.ui.disconnect_overlay import inject_disconnect_overlay

logger = logging.getLogger(__name__)


# Initialize async DB pool via core.database (centralizes pool config and provides get_db_pool())
db_pool = init_db_pool()

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

# Static assets for AG Grid renderers and custom CSS (CSP-compliant).
app.add_static_files("/static", "apps/web_console_ng/static")
ui.add_head_html('<script src="/static/js/aggrid_renderers.js"></script>')
ui.add_head_html('<link rel="stylesheet" href="/static/css/custom.css">')

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
    from apps.web_console_ng.core.dependencies import (
        close_sync_db_pool,
        close_sync_redis_client,
    )
    from apps.web_console_ng.core.redis_ha import get_redis_store

    await trading_client.shutdown()
    await audit_logger.stop()
    await close_db_pool()  # Use centralized close to clear module-level reference
    await state_manager.close()

    # Close sync resources used by legacy services (P5T7)
    close_sync_db_pool()
    close_sync_redis_client()

    # Close Redis connections to prevent "Unclosed connection" warnings
    try:
        redis = get_redis_store()
        await redis.close()
    except (OSError, ConnectionError) as e:
        logger.warning("Failed to close Redis connection during shutdown: %s", e)


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
