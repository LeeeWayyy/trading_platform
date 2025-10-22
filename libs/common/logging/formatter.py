"""JSON log formatter for structured logging.

This module provides a custom logging formatter that outputs logs in JSON format
with a standardized schema for centralized log aggregation and analysis.

Example log output:
    {
        "timestamp": "2025-10-21T10:30:00.000Z",
        "level": "INFO",
        "service": "signal_service",
        "trace_id": "abc123-def456",
        "message": "Generated signals for 10 symbols",
        "context": {
            "strategy": "alpha_baseline",
            "symbol_count": 10
        }
    }
"""

import json
import logging
import traceback
from datetime import UTC, datetime
from types import TracebackType
from typing import Any


class JSONFormatter(logging.Formatter):
    """Custom formatter that outputs logs as JSON.

    Formats log records into structured JSON with a consistent schema
    for all services. Includes timestamp, level, service name, trace ID,
    message, and optional context data.

    Attributes:
        service_name: Name of the service emitting logs
        include_context: Whether to include extra context fields

    Example:
        >>> formatter = JSONFormatter(service_name="signal_service")
        >>> handler = logging.StreamHandler()
        >>> handler.setFormatter(formatter)
        >>> logger = logging.getLogger(__name__)
        >>> logger.addHandler(handler)
        >>> logger.info("Processing signals", extra={"context": {"symbol_count": 10}})
    """

    def __init__(
        self, service_name: str, include_context: bool = True, *args: Any, **kwargs: Any
    ) -> None:
        """Initialize the JSON formatter.

        Args:
            service_name: Name of the service (e.g., "signal_service")
            include_context: Whether to include context dict in output
            *args: Additional args passed to parent Formatter
            **kwargs: Additional kwargs passed to parent Formatter
        """
        super().__init__(*args, **kwargs)
        self.service_name = service_name
        self.include_context = include_context

    def format(self, record: logging.LogRecord) -> str:
        """Format a log record as JSON.

        Converts a LogRecord into a JSON string with standardized fields.
        Extracts trace_id and context from the record's extra dict if present.

        Args:
            record: The log record to format

        Returns:
            JSON string representation of the log record

        Example:
            >>> record = logging.LogRecord(...)
            >>> formatter = JSONFormatter(service_name="test")
            >>> json_str = formatter.format(record)
            >>> log_dict = json.loads(json_str)
            >>> log_dict["service"]
            'test'
        """
        # Build base log entry
        log_entry: dict[str, Any] = {
            "timestamp": self._format_timestamp(record.created),
            "level": record.levelname,
            "service": self.service_name,
            "trace_id": self._extract_trace_id(record),
            "message": record.getMessage(),
        }

        # Add context if present and enabled
        if self.include_context:
            context = self._extract_context(record)
            if context:
                log_entry["context"] = context

        # Add exception info if present
        if record.exc_info:
            log_entry["exception"] = {
                "type": record.exc_info[0].__name__ if record.exc_info[0] else None,
                "message": str(record.exc_info[1]) if record.exc_info[1] else None,
                "traceback": self._format_exception(record.exc_info),
            }

        # Add source location
        log_entry["source"] = {
            "file": record.pathname,
            "line": record.lineno,
            "function": record.funcName,
        }

        return json.dumps(log_entry, default=str)

    def _format_timestamp(self, created: float) -> str:
        """Format timestamp as ISO 8601 in UTC.

        Args:
            created: Unix timestamp from LogRecord

        Returns:
            ISO 8601 formatted timestamp string in UTC

        Example:
            >>> formatter = JSONFormatter(service_name="test")
            >>> formatter._format_timestamp(1697896200.0)
            '2023-10-21T10:30:00.000Z'
        """
        dt = datetime.fromtimestamp(created, tz=UTC)
        return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    def _extract_trace_id(self, record: logging.LogRecord) -> str | None:
        """Extract trace ID from log record.

        Looks for trace_id in the record's extra dict. Falls back to None
        if not present.

        Args:
            record: The log record

        Returns:
            Trace ID string if present, None otherwise

        Example:
            >>> record = logging.LogRecord(...)
            >>> record.trace_id = "abc123"
            >>> formatter = JSONFormatter(service_name="test")
            >>> formatter._extract_trace_id(record)
            'abc123'
        """
        return getattr(record, "trace_id", None)

    def _extract_context(self, record: logging.LogRecord) -> dict[str, Any] | None:
        """Extract context dict from log record.

        Looks for context in the record's extra dict. Filters out internal
        logging fields to avoid duplication.

        Args:
            record: The log record

        Returns:
            Context dictionary if present, None otherwise

        Example:
            >>> record = logging.LogRecord(...)
            >>> record.context = {"symbol": "AAPL", "qty": 100}
            >>> formatter = JSONFormatter(service_name="test")
            >>> formatter._extract_context(record)
            {'symbol': 'AAPL', 'qty': 100}
        """
        # Get context from extra dict if present
        context = getattr(record, "context", None)
        if context and isinstance(context, dict):
            return dict(context)

        # Otherwise, extract all extra fields (excluding internal ones)
        reserved_fields = {
            "name",
            "msg",
            "args",
            "created",
            "filename",
            "funcName",
            "levelname",
            "levelno",
            "lineno",
            "module",
            "msecs",
            "pathname",
            "process",
            "processName",
            "relativeCreated",
            "thread",
            "threadName",
            "trace_id",
            "context",
            "exc_info",
            "exc_text",
            "stack_info",
        }

        extra = {key: value for key, value in record.__dict__.items() if key not in reserved_fields}

        return extra if extra else None

    def _format_exception(
        self,
        exc_info: tuple[type[BaseException] | None, BaseException | None, TracebackType | None],
    ) -> str:
        """Format exception traceback.

        Args:
            exc_info: Exception info tuple from LogRecord

        Returns:
            Formatted traceback string

        Example:
            >>> import sys
            >>> try:
            ...     raise ValueError("test error")
            ... except:
            ...     exc_info = sys.exc_info()
            ...     formatter = JSONFormatter(service_name="test")
            ...     traceback_str = formatter._format_exception(exc_info)
            ...     "ValueError" in traceback_str
            True
        """
        return "".join(traceback.format_exception(*exc_info))
