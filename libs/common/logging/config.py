"""Centralized logging configuration for all services.

This module provides standardized logging setup using structured JSON output
with trace ID support. All services should use configure_logging() to ensure
consistent log format and behavior.

Example:
    >>> from libs.common.logging.config import configure_logging
    >>> logger = configure_logging(service_name="signal_service", log_level="INFO")
    >>> logger.info("Service started", extra={"context": {"port": 8000}})
"""

import logging
import sys
from typing import Optional

from libs.common.logging.context import get_trace_id
from libs.common.logging.formatter import JSONFormatter


class TraceIDFilter(logging.Filter):
    """Logging filter that adds trace ID to log records.

    Automatically injects the current trace ID from context into every
    log record so it appears in the formatted output.

    Example:
        >>> from libs.common.logging.context import set_trace_id
        >>> set_trace_id("test-123")
        >>> logger = logging.getLogger()
        >>> logger.addFilter(TraceIDFilter())
        >>> # All logs will now include trace_id="test-123"
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """Add trace ID to the log record.

        Args:
            record: The log record to filter

        Returns:
            True (always allows the record through)
        """
        record.trace_id = get_trace_id()
        return True


def configure_logging(
    service_name: str,
    log_level: str = "INFO",
    include_context: bool = True,
) -> logging.Logger:
    """Configure structured JSON logging for a service.

    Sets up a logger with:
    - JSON formatted output to stdout
    - Trace ID injection on all records
    - Specified log level
    - Consistent schema across services

    This should be called once at service startup.

    Args:
        service_name: Name of the service (e.g., "signal_service")
        log_level: Minimum log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        include_context: Whether to include context dict in output

    Returns:
        Configured root logger instance

    Raises:
        ValueError: If log_level is invalid

    Example:
        >>> logger = configure_logging(
        ...     service_name="signal_service",
        ...     log_level="INFO"
        ... )
        >>> logger.info("Service initialized")
        {"timestamp": "2025-10-21T10:30:00.000Z", "level": "INFO", ...}
    """
    # Validate log level
    numeric_level = getattr(logging, log_level.upper(), None)
    if not isinstance(numeric_level, int):
        raise ValueError(f"Invalid log level: {log_level}")

    # Get root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)

    # Remove existing handlers to avoid duplicates
    root_logger.handlers.clear()

    # Create stdout handler with JSON formatter
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(numeric_level)

    # Set JSON formatter
    formatter = JSONFormatter(
        service_name=service_name,
        include_context=include_context,
    )
    handler.setFormatter(formatter)

    # Add trace ID filter
    trace_filter = TraceIDFilter()
    handler.addFilter(trace_filter)

    # Add handler to root logger
    root_logger.addHandler(handler)

    return root_logger


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """Get a logger instance.

    Convenience function to get a logger by name. If configure_logging()
    has been called, this logger will use the same configuration.

    Args:
        name: Logger name (typically __name__). If None, returns root logger.

    Returns:
        Logger instance

    Example:
        >>> logger = get_logger(__name__)
        >>> logger.info("Processing request")
    """
    return logging.getLogger(name)


def log_with_context(
    logger: logging.Logger,
    level: str,
    message: str,
    **context_fields: object,
) -> None:
    """Log a message with additional context fields.

    Convenience function that adds context as extra fields to the log record.
    Context fields will appear in the "context" dict in JSON output.

    Args:
        logger: Logger instance to use
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        message: Log message
        **context_fields: Key-value pairs to include in context

    Example:
        >>> logger = get_logger(__name__)
        >>> log_with_context(
        ...     logger,
        ...     "INFO",
        ...     "Order placed",
        ...     symbol="AAPL",
        ...     qty=100,
        ...     client_order_id="order-123"
        ... )
        # Output includes: "context": {"symbol": "AAPL", "qty": 100, ...}
    """
    log_method = getattr(logger, level.lower())
    log_method(message, extra={"context": context_fields})
