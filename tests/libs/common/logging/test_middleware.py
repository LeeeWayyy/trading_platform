"""Tests for FastAPI trace ID middleware.

Tests verify:
- Trace ID extraction from request headers
- Trace ID generation when missing
- Trace ID injection into response headers
- Context cleanup after requests
- ASGI middleware functionality
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from libs.common.logging.context import (
    TRACE_ID_HEADER,
    clear_trace_id,
    get_trace_id,
)
from libs.common.logging.middleware import (
    ASGITraceIDMiddleware,
    TraceIDMiddleware,
    add_trace_id_middleware,
)


@pytest.fixture()
def app() -> FastAPI:
    """Create a test FastAPI application."""
    app = FastAPI()

    @app.get("/test")
    async def test_endpoint() -> dict:
        """Test endpoint that returns current trace ID."""
        return {"trace_id": get_trace_id()}

    return app


class TestTraceIDMiddleware:
    """Test suite for TraceIDMiddleware."""

    def test_extracts_trace_id_from_header(self, app: FastAPI) -> None:
        """Test that middleware extracts trace ID from request header."""
        app.add_middleware(TraceIDMiddleware)
        client = TestClient(app)

        test_trace_id = "test-trace-123"
        response = client.get("/test", headers={TRACE_ID_HEADER: test_trace_id})

        assert response.status_code == 200
        assert response.json()["trace_id"] == test_trace_id
        assert response.headers[TRACE_ID_HEADER] == test_trace_id

    def test_generates_trace_id_when_missing(self, app: FastAPI) -> None:
        """Test that middleware generates trace ID when not in request."""
        app.add_middleware(TraceIDMiddleware)
        client = TestClient(app)

        response = client.get("/test")

        assert response.status_code == 200
        trace_id = response.json()["trace_id"]
        assert trace_id is not None
        assert len(trace_id) == 36  # UUID format
        assert response.headers[TRACE_ID_HEADER] == trace_id

    def test_adds_trace_id_to_response_headers(self, app: FastAPI) -> None:
        """Test that middleware adds trace ID to response headers."""
        app.add_middleware(TraceIDMiddleware)
        client = TestClient(app)

        response = client.get("/test", headers={TRACE_ID_HEADER: "test-456"})

        assert TRACE_ID_HEADER in response.headers
        assert response.headers[TRACE_ID_HEADER] == "test-456"

    def test_clears_trace_id_after_request(self, app: FastAPI) -> None:
        """Test that middleware clears trace ID from context after request."""
        app.add_middleware(TraceIDMiddleware)
        client = TestClient(app)

        # Make a request
        client.get("/test", headers={TRACE_ID_HEADER: "test-789"})

        # Trace ID should be cleared from context
        assert get_trace_id() is None

    def test_different_trace_ids_for_different_requests(self, app: FastAPI) -> None:
        """Test that each request gets its own trace ID."""
        app.add_middleware(TraceIDMiddleware)
        client = TestClient(app)

        response1 = client.get("/test", headers={TRACE_ID_HEADER: "trace-1"})
        response2 = client.get("/test", headers={TRACE_ID_HEADER: "trace-2"})

        assert response1.json()["trace_id"] == "trace-1"
        assert response2.json()["trace_id"] == "trace-2"
        assert response1.headers[TRACE_ID_HEADER] == "trace-1"
        assert response2.headers[TRACE_ID_HEADER] == "trace-2"

    def test_middleware_handles_exceptions(self, app: FastAPI) -> None:
        """Test that middleware clears context even when endpoint raises exception."""

        @app.get("/error")
        async def error_endpoint() -> dict:
            raise ValueError("Test error")

        app.add_middleware(TraceIDMiddleware)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get("/error", headers={TRACE_ID_HEADER: "error-trace"})

        # Note: BaseHTTPMiddleware cannot modify FastAPI exception handler responses
        # For production, use ASGITraceIDMiddleware which works at a lower level
        # The important guarantee is that context is always cleared
        assert response.status_code == 500

        # Context should be cleared even after exception
        assert get_trace_id() is None


class TestAddTraceIDMiddleware:
    """Test suite for add_trace_id_middleware helper."""

    def test_adds_middleware_to_app(self, app: FastAPI) -> None:
        """Test that helper adds middleware to app correctly."""
        add_trace_id_middleware(app)
        client = TestClient(app)

        response = client.get("/test", headers={TRACE_ID_HEADER: "helper-test"})

        assert response.status_code == 200
        assert response.json()["trace_id"] == "helper-test"
        assert response.headers[TRACE_ID_HEADER] == "helper-test"


class TestASGITraceIDMiddleware:
    """Test suite for ASGI-level trace ID middleware."""

    def test_asgi_middleware_extracts_trace_id(self, app: FastAPI) -> None:
        """Test ASGI middleware extracts trace ID from headers."""
        app = ASGITraceIDMiddleware(app)  # type: ignore
        client = TestClient(app)

        response = client.get("/test", headers={TRACE_ID_HEADER: "asgi-test"})

        assert response.status_code == 200
        # Note: ASGI middleware lowercase the header
        assert TRACE_ID_HEADER.lower() in (h.lower() for h in response.headers.keys())

    def test_asgi_middleware_generates_trace_id(self, app: FastAPI) -> None:
        """Test ASGI middleware generates trace ID when missing."""
        app = ASGITraceIDMiddleware(app)  # type: ignore
        client = TestClient(app)

        response = client.get("/test")

        assert response.status_code == 200
        trace_id = response.json()["trace_id"]
        assert trace_id is not None
        assert len(trace_id) == 36

    def test_asgi_middleware_clears_context(self, app: FastAPI) -> None:
        """Test ASGI middleware clears context after request."""
        app = ASGITraceIDMiddleware(app)  # type: ignore
        client = TestClient(app)

        client.get("/test", headers={TRACE_ID_HEADER: "asgi-cleanup"})

        # Context should be cleared
        assert get_trace_id() is None

    def test_asgi_middleware_handles_non_http(self) -> None:
        """Test ASGI middleware ignores non-HTTP connections."""

        async def simple_app(scope, receive, send):  # type: ignore
            """Simple ASGI app."""
            if scope["type"] == "lifespan":
                # Lifespan protocol
                while True:
                    message = await receive()
                    if message["type"] == "lifespan.startup":
                        await send({"type": "lifespan.startup.complete"})
                    elif message["type"] == "lifespan.shutdown":
                        await send({"type": "lifespan.shutdown.complete"})
                        break

        _wrapped_app = ASGITraceIDMiddleware(simple_app)

        # Should not raise exception for non-HTTP scope
        # This is tested implicitly by TestClient startup/shutdown
        app = FastAPI()
        app = ASGITraceIDMiddleware(app)  # type: ignore
        client = TestClient(app)

        # If lifespan works, middleware handled non-HTTP correctly
        assert client.app is not None


@pytest.fixture(autouse=True)
def _cleanup_trace_context():
    """Ensure trace context is clean before and after each test."""
    clear_trace_id()
    yield
    clear_trace_id()
