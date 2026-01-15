"""Centralized structured logging library.

This package provides structured JSON logging with trace ID support
for distributed request correlation across all services.

Usage:
    # At service startup
    from libs.core.common.logging import configure_logging, add_trace_id_middleware
    from fastapi import FastAPI

    app = FastAPI()
    add_trace_id_middleware(app)  # Automatic trace ID management
    logger = configure_logging(service_name="signal_service", log_level="INFO")

    # In request handlers (trace ID is automatic via middleware)
    from libs.core.common.logging import get_logger, log_with_context
    logger = get_logger(__name__)
    log_with_context(logger, "INFO", "Processing request", symbol="AAPL", qty=100)

    # For outgoing HTTP requests
    from libs.core.common.logging import get_traced_client
    async with get_traced_client() as client:
        response = await client.get("http://other-service/api")
"""

from libs.core.common.logging.config import (
    configure_logging,
    get_logger,
    log_with_context,
)
from libs.core.common.logging.context import (
    TRACE_ID_HEADER,
    LogContext,
    clear_trace_id,
    generate_trace_id,
    get_or_create_trace_id,
    get_trace_id,
    set_trace_id,
)
from libs.core.common.logging.formatter import JSONFormatter
from libs.core.common.logging.http_client import (
    TracedHTTPXClient,
    TracedHTTPXSyncClient,
    get_traced_client,
    get_traced_sync_client,
    traced_get,
    traced_post,
)
from libs.core.common.logging.middleware import (
    ASGITraceIDMiddleware,
    TraceIDMiddleware,
    add_trace_id_middleware,
)

__all__ = [
    # Configuration
    "configure_logging",
    "get_logger",
    "log_with_context",
    # Trace ID management
    "generate_trace_id",
    "get_trace_id",
    "set_trace_id",
    "clear_trace_id",
    "get_or_create_trace_id",
    "LogContext",
    "TRACE_ID_HEADER",
    # Middleware
    "TraceIDMiddleware",
    "ASGITraceIDMiddleware",
    "add_trace_id_middleware",
    # HTTP Client
    "TracedHTTPXClient",
    "TracedHTTPXSyncClient",
    "get_traced_client",
    "get_traced_sync_client",
    "traced_get",
    "traced_post",
    # Formatter (for advanced usage)
    "JSONFormatter",
]
