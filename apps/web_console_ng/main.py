"""NiceGUI entry point for the web console (C0 minimal)."""

from __future__ import annotations

import logging
import traceback
from collections.abc import Awaitable, Callable
from typing import Any, cast

from nicegui import app, ui
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from apps.web_console_ng import config

app.config.title = config.PAGE_TITLE
app.config.viewport = "width=device-width, initial-scale=1"
app.config.favicon = None
app.config.dark = None
app.config.language = "en-US"
app.config.tailwind = True
app.config.prod_js = not config.DEBUG
app.config.reconnect_timeout = 3.0
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


def _patch_nicegui_request_tracking_middleware() -> None:
    """Patch NiceGUI request tracking middleware to ignore disconnect sentinel."""
    try:
        from nicegui import storage as nicegui_storage
    except (ImportError, AttributeError):
        return

    dispatch = cast(
        Callable[[Any, Request, Callable[[Request], Awaitable[Response]]], Awaitable[Response]],
        nicegui_storage.RequestTrackingMiddleware.dispatch,
    )
    if getattr(dispatch, "_no_response_patch_applied", False):
        return

    async def _dispatch_with_no_response_guard(
        self: Any, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        try:
            return await dispatch(self, request, call_next)
        except RuntimeError as exc:
            if str(exc) == "No response returned.":
                if await request.is_disconnected():
                    logger.debug("suppressing_no_response_returned_in_nicegui_storage")
                    return Response(status_code=204)
            raise

    _dispatch_with_no_response_guard._no_response_patch_applied = True  # type: ignore[attr-defined]
    nicegui_storage.RequestTrackingMiddleware.dispatch = _dispatch_with_no_response_guard  # type: ignore[method-assign]


class SuppressNoResponseReturnedMiddleware:
    """Swallow Starlette disconnect sentinel RuntimeError for HTTP requests."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    @staticmethod
    def _is_no_response_returned(exc: BaseException) -> bool:
        if isinstance(exc, RuntimeError):
            return str(exc) == "No response returned."
        if isinstance(exc, BaseExceptionGroup):
            return all(
                SuppressNoResponseReturnedMiddleware._is_no_response_returned(sub_exc)
                for sub_exc in exc.exceptions
            )
        return False

    @staticmethod
    async def _is_client_disconnected(scope: Scope, receive: Receive) -> bool:
        if scope.get("type") != "http":
            return False
        request = Request(scope, receive)
        return await request.is_disconnected()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await self.app(scope, receive, send)
        except Exception as exc:
            if self._is_no_response_returned(exc) and await self._is_client_disconnected(
                scope, receive
            ):
                logger.debug("suppressing_no_response_returned_runtime_error_at_asgi")
                return
            raise


# Initialize async DB pool via core.database (centralizes pool config and provides get_db_pool())
_patch_nicegui_request_tracking_middleware()
db_pool = init_db_pool()

audit_logger = AuthAuditLogger.get(
    db_enabled=config.AUDIT_LOG_DB_ENABLED,
    db_pool=db_pool,
)
session_store = get_session_store(audit_logger=audit_logger)
state_manager = get_state_manager()
trading_client = AsyncTradingClient.get()

# Import routes after audit logger + session store are configured.
from apps.web_console_ng.api.workspace import router as workspace_router  # noqa: E402
from apps.web_console_ng.auth import logout as auth_logout  # noqa: E402,F401
from apps.web_console_ng.auth import routes as auth_routes  # noqa: E402,F401
from apps.web_console_ng.auth.routes import auth_api_router  # noqa: E402

# Register FastAPI router for HTTP-only endpoints (login POST for cookie setting)
app.include_router(auth_api_router)
app.include_router(workspace_router)

# Import pages to trigger @ui.page decorator registration (including /login, /dashboard, etc.)
from apps.web_console_ng import pages  # noqa: E402,F401

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
app.add_middleware(SuppressNoResponseReturnedMiddleware)


@app.exception_handler(Exception)
async def log_unhandled_exception(request: Request, exc: Exception) -> PlainTextResponse:
    """Log unhandled exceptions with full traceback for debug."""
    if (
        isinstance(exc, RuntimeError)
        and str(exc) == "No response returned."
        and await request.is_disconnected()
    ):
        # Starlette emits this sentinel RuntimeError when the client disconnects
        # before any downstream response can be produced.
        logger.debug("suppressing_no_response_returned_runtime_error")
        return PlainTextResponse("", status_code=204)

    logger.error(
        "unhandled_exception path=%s type=%s message=%s",
        str(request.url.path),
        type(exc).__name__,
        str(exc),
    )
    traceback.print_exception(type(exc), exc, exc.__traceback__)
    return PlainTextResponse("Server error", status_code=500)


# Static assets for AG Grid renderers (CSP-compliant). CSS loaded per-page in layout.py.
app.add_static_files("/static", "apps/web_console_ng/static")
ui.add_head_html('<script src="/static/js/aggrid_renderers.js"></script>')

setup_health_endpoint()
setup_connection_handlers()


# Compatibility redirect: NiceGUI 2.x uses /_nicegui_ws/socket.io/ for WebSocket,
# but old cached browser JavaScript may try /socket.io/ directly.
# Return a clear error message to prompt browser cache refresh.
@app.get("/socket.io/{path:path}")
@app.post("/socket.io/{path:path}")
async def socket_io_redirect(path: str = "") -> dict[str, str]:
    """Handle requests to old socket.io path with helpful error message."""
    return {
        "error": "socket.io path changed",
        "message": "Please refresh your browser (Ctrl+Shift+R) to clear cache",
        "new_path": "/_nicegui_ws/socket.io/",
    }


async def startup() -> None:
    """Startup hook for async resource initialization."""
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


if __name__ in {"__main__", "__mp_main__"}:
    ui.run(
        host=config.HOST,
        port=config.PORT,
        title=config.PAGE_TITLE,
        reload=config.DEBUG,
        show=False,
        reconnect_timeout=3.0,
        storage_secret=config.STORAGE_SECRET,
    )
