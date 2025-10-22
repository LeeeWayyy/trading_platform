"""Centralized structured logging library.

This package provides structured JSON logging with trace ID support
for distributed request correlation across all services.

Usage:
    # At service startup
    from libs.common.logging import configure_logging
    logger = configure_logging(service_name="signal_service", log_level="INFO")

    # In request handlers
    from libs.common.logging import set_trace_id, get_logger, log_with_context
    set_trace_id(request.headers.get("X-Trace-ID") or generate_trace_id())

    logger = get_logger(__name__)
    log_with_context(logger, "INFO", "Processing request", symbol="AAPL", qty=100)
"""

from libs.common.logging.config import (
    configure_logging,
    get_logger,
    log_with_context,
)
from libs.common.logging.context import (
    TRACE_ID_HEADER,
    LogContext,
    clear_trace_id,
    generate_trace_id,
    get_or_create_trace_id,
    get_trace_id,
    set_trace_id,
)
from libs.common.logging.formatter import JSONFormatter

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
    # Formatter (for advanced usage)
    "JSONFormatter",
]
