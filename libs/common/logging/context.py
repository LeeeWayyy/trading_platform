"""Trace ID generation and context propagation for distributed tracing.

This module provides utilities for generating unique trace IDs and propagating
them across service boundaries to enable request correlation in logs.

Trace IDs are UUIDv4 strings that follow requests through the entire system,
allowing all logs for a single request to be grouped together.

Example:
    >>> from libs.common.logging.context import generate_trace_id, get_trace_id
    >>> trace_id = generate_trace_id()
    >>> set_trace_id(trace_id)
    >>> current_id = get_trace_id()
    >>> current_id == trace_id
    True
"""

import contextvars
import uuid
from types import TracebackType

# Context variable for storing trace ID in async contexts
_trace_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("trace_id", default=None)

# HTTP header name for trace ID propagation
TRACE_ID_HEADER = "X-Trace-ID"


def generate_trace_id() -> str:
    """Generate a new unique trace ID.

    Creates a UUID v4 and returns it as a string. Trace IDs are used
    to correlate logs across service boundaries for a single request.

    Returns:
        A unique trace ID string (UUID v4 format)

    Example:
        >>> trace_id = generate_trace_id()
        >>> len(trace_id)
        36
        >>> "-" in trace_id
        True
    """
    return str(uuid.uuid4())


def get_trace_id() -> str | None:
    """Get the current trace ID from context.

    Retrieves the trace ID for the current async context. Returns None
    if no trace ID has been set.

    Returns:
        Current trace ID if set, None otherwise

    Example:
        >>> set_trace_id("test-trace-123")
        >>> get_trace_id()
        'test-trace-123'
        >>> clear_trace_id()
        >>> get_trace_id() is None
        True
    """
    return _trace_id_var.get()


def set_trace_id(trace_id: str) -> None:
    """Set the trace ID for the current context.

    Stores the trace ID in a context variable so it's available to all
    logging calls within the same async context.

    Args:
        trace_id: The trace ID to set

    Raises:
        ValueError: If trace_id is empty or None

    Example:
        >>> set_trace_id("abc-123")
        >>> get_trace_id()
        'abc-123'
    """
    if not trace_id:
        raise ValueError("Trace ID cannot be empty")
    _trace_id_var.set(trace_id)


def clear_trace_id() -> None:
    """Clear the trace ID from the current context.

    Removes the trace ID from context, causing get_trace_id() to return None.
    Useful for cleanup in tests or between requests.

    Example:
        >>> set_trace_id("test-123")
        >>> clear_trace_id()
        >>> get_trace_id() is None
        True
    """
    _trace_id_var.set(None)


def get_or_create_trace_id() -> str:
    """Get existing trace ID or generate a new one.

    Convenience function that returns the current trace ID if set,
    otherwise generates and sets a new one. Ensures a trace ID is
    always available.

    Returns:
        Current or newly generated trace ID

    Example:
        >>> clear_trace_id()
        >>> trace_id = get_or_create_trace_id()
        >>> trace_id == get_trace_id()
        True
        >>> # Second call returns same ID
        >>> trace_id2 = get_or_create_trace_id()
        >>> trace_id == trace_id2
        True
    """
    trace_id = get_trace_id()
    if trace_id is None:
        trace_id = generate_trace_id()
        set_trace_id(trace_id)
    return trace_id


class LogContext:
    """Context manager for scoped trace ID management.

    Provides a clean way to set a trace ID for a block of code and
    automatically restore the previous value when done.

    Args:
        trace_id: The trace ID to set for this context. If None, generates new ID.

    Example:
        >>> with LogContext("request-123"):
        ...     print(get_trace_id())
        request-123
        >>> # Original trace ID restored after exiting context
    """

    def __init__(self, trace_id: str | None = None) -> None:
        """Initialize the log context.

        Args:
            trace_id: Trace ID to use. If None, generates a new one.
        """
        self.trace_id = trace_id or generate_trace_id()
        self.previous_trace_id: str | None = None

    def __enter__(self) -> str:
        """Enter the context and set the trace ID.

        Returns:
            The trace ID for this context
        """
        self.previous_trace_id = get_trace_id()
        set_trace_id(self.trace_id)
        return self.trace_id

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Exit the context and restore previous trace ID."""
        if self.previous_trace_id is not None:
            set_trace_id(self.previous_trace_id)
        else:
            clear_trace_id()
