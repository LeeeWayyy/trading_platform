"""FastAPI middleware for trace ID extraction and injection.

This module provides middleware that automatically extracts trace IDs from
incoming requests, generates new ones if missing, and injects them into
responses and logging context.

Example:
    >>> from fastapi import FastAPI
    >>> from libs.common.logging.middleware import add_trace_id_middleware
    >>>
    >>> app = FastAPI()
    >>> add_trace_id_middleware(app)
"""

from typing import Callable

from fastapi import FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from libs.common.logging.context import (
    TRACE_ID_HEADER,
    clear_trace_id,
    generate_trace_id,
    get_trace_id,
    set_trace_id,
)


class TraceIDMiddleware(BaseHTTPMiddleware):
    """Middleware that manages trace IDs for request correlation.

    Extracts trace IDs from incoming request headers (X-Trace-ID),
    generates new ones if missing, sets them in logging context,
    and injects them into response headers.

    This ensures all logs for a request share the same trace ID,
    and the ID is propagated to downstream services.

    Example:
        >>> app = FastAPI()
        >>> app.add_middleware(TraceIDMiddleware)
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Process request and inject trace ID.

        Args:
            request: Incoming HTTP request
            call_next: Next middleware/handler in chain

        Returns:
            HTTP response with trace ID header added

        Example:
            This is called automatically by FastAPI middleware stack.
            Trace ID flow:
            1. Extract from request header (if present)
            2. Generate new ID (if not present)
            3. Set in logging context
            4. Process request
            5. Add to response header
            6. Clear from context
        """
        # Extract trace ID from request header or generate new one
        trace_id = request.headers.get(TRACE_ID_HEADER)
        if not trace_id:
            trace_id = generate_trace_id()

        # Set trace ID in context for logging
        set_trace_id(trace_id)

        try:
            # Process request
            response = await call_next(request)

            # Add trace ID to response headers
            response.headers[TRACE_ID_HEADER] = trace_id

            return response
        finally:
            # Always clear trace ID from context after request
            clear_trace_id()


def add_trace_id_middleware(app: FastAPI) -> None:
    """Add trace ID middleware to FastAPI application.

    Uses ASGITraceIDMiddleware (low-level ASGI) instead of BaseHTTPMiddleware
    to ensure trace IDs are injected even on error responses.

    Should be called during application setup.

    Args:
        app: FastAPI application instance

    Example:
        >>> from fastapi import FastAPI
        >>> from libs.common.logging.middleware import add_trace_id_middleware
        >>>
        >>> app = FastAPI()
        >>> add_trace_id_middleware(app)
        >>>
        >>> # Now all requests will have trace IDs automatically managed
        >>> # Works correctly on both success and error responses
    """
    # Wrap the app's ASGI handler with our ASGI middleware
    # This works at a lower level than BaseHTTPMiddleware and can
    # inject headers into error responses from FastAPI's exception handlers
    app.add_middleware(ASGITraceIDMiddleware)


class ASGITraceIDMiddleware:
    """ASGI middleware for trace ID management.

    Lower-level ASGI middleware that can be used with any ASGI application,
    not just FastAPI. Provides the same trace ID extraction and injection
    functionality.

    Example:
        >>> from starlette.applications import Starlette
        >>> app = Starlette()
        >>> app = ASGITraceIDMiddleware(app)
    """

    def __init__(self, app: ASGIApp) -> None:
        """Initialize ASGI middleware.

        Args:
            app: ASGI application to wrap
        """
        self.app = app

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        """Handle ASGI request.

        Args:
            scope: ASGI connection scope
            receive: Callable to receive messages
            send: Callable to send messages
        """
        if scope["type"] != "http":
            # Only handle HTTP requests
            await self.app(scope, receive, send)
            return

        # Extract trace ID from headers
        headers = dict(scope.get("headers", []))
        trace_id_bytes = headers.get(TRACE_ID_HEADER.lower().encode())
        trace_id = trace_id_bytes.decode() if trace_id_bytes else generate_trace_id()

        # Set in context
        set_trace_id(trace_id)

        async def send_with_trace_id(message: dict) -> None:
            """Send response with trace ID header injected."""
            if message["type"] == "http.response.start":
                # Add trace ID to response headers
                headers = list(message.get("headers", []))
                headers.append((TRACE_ID_HEADER.lower().encode(), trace_id.encode()))
                message["headers"] = headers

            await send(message)

        try:
            await self.app(scope, receive, send_with_trace_id)
        finally:
            clear_trace_id()
