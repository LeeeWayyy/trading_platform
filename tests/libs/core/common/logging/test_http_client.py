"""Tests for HTTP client with trace ID propagation.

Tests verify:
- Trace ID injection into outgoing requests
- Async and sync client functionality
- Convenience functions (traced_get, traced_post)
- Client factory functions
"""

import pytest
import respx
from httpx import Response

from libs.core.common.logging.context import (
    TRACE_ID_HEADER,
    clear_trace_id,
    set_trace_id,
)
from libs.core.common.logging.http_client import (
    TracedHTTPXClient,
    TracedHTTPXSyncClient,
    get_traced_client,
    get_traced_sync_client,
    traced_get,
    traced_post,
)


class TestTracedHTTPXClient:
    """Test suite for async traced HTTP client."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_adds_trace_id_to_request(self) -> None:
        """Test that client adds trace ID header to requests."""
        route = respx.get("http://test.example.com/api").mock(
            return_value=Response(200, json={"status": "ok"})
        )

        test_trace_id = "async-test-123"
        set_trace_id(test_trace_id)

        async with TracedHTTPXClient() as client:
            await client.get("http://test.example.com/api")

        # Verify request had trace ID header
        assert route.called
        request = route.calls.last.request
        assert request.headers[TRACE_ID_HEADER] == test_trace_id

    @pytest.mark.asyncio()
    @respx.mock
    async def test_no_trace_id_when_not_set(self) -> None:
        """Test that client doesn't add header when no trace ID in context."""
        route = respx.get("http://test.example.com/api").mock(
            return_value=Response(200, json={"status": "ok"})
        )

        clear_trace_id()

        async with TracedHTTPXClient() as client:
            await client.get("http://test.example.com/api")

        # Verify request had no trace ID header
        assert route.called
        request = route.calls.last.request
        assert TRACE_ID_HEADER not in request.headers

    @pytest.mark.asyncio()
    @respx.mock
    async def test_preserves_existing_headers(self) -> None:
        """Test that client preserves existing headers."""
        route = respx.get("http://test.example.com/api").mock(
            return_value=Response(200, json={"status": "ok"})
        )

        set_trace_id("test-456")

        async with TracedHTTPXClient() as client:
            await client.get(
                "http://test.example.com/api",
                headers={"Authorization": "Bearer token123", "Custom-Header": "value"},
            )

        assert route.called
        request = route.calls.last.request
        assert request.headers["Authorization"] == "Bearer token123"
        assert request.headers["Custom-Header"] == "value"
        assert request.headers[TRACE_ID_HEADER] == "test-456"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_post_request_with_trace_id(self) -> None:
        """Test POST request includes trace ID."""
        route = respx.post("http://test.example.com/api/create").mock(
            return_value=Response(200, json={"created": True})
        )

        set_trace_id("post-test-789")

        async with TracedHTTPXClient() as client:
            await client.post("http://test.example.com/api/create", json={"name": "test"})

        assert route.called
        request = route.calls.last.request
        assert request.headers[TRACE_ID_HEADER] == "post-test-789"
        assert request.method == "POST"


class TestTracedHTTPXSyncClient:
    """Test suite for sync traced HTTP client."""

    @respx.mock
    def test_adds_trace_id_to_sync_request(self) -> None:
        """Test that sync client adds trace ID header."""
        route = respx.get("http://test.example.com/api").mock(
            return_value=Response(200, json={"status": "ok"})
        )

        test_trace_id = "sync-test-123"
        set_trace_id(test_trace_id)

        with TracedHTTPXSyncClient() as client:
            client.get("http://test.example.com/api")

        assert route.called
        request = route.calls.last.request
        assert request.headers[TRACE_ID_HEADER] == test_trace_id

    @respx.mock
    def test_sync_no_trace_id_when_not_set(self) -> None:
        """Test sync client without trace ID in context."""
        route = respx.get("http://test.example.com/api").mock(
            return_value=Response(200, json={"status": "ok"})
        )

        clear_trace_id()

        with TracedHTTPXSyncClient() as client:
            client.get("http://test.example.com/api")

        assert route.called
        request = route.calls.last.request
        assert TRACE_ID_HEADER not in request.headers

    @respx.mock
    def test_sync_post_with_trace_id(self) -> None:
        """Test sync POST request includes trace ID."""
        route = respx.post("http://test.example.com/api/create").mock(
            return_value=Response(200, json={"created": True})
        )

        set_trace_id("sync-post-456")

        with TracedHTTPXSyncClient() as client:
            client.post("http://test.example.com/api/create", json={"data": "value"})

        assert route.called
        request = route.calls.last.request
        assert request.headers[TRACE_ID_HEADER] == "sync-post-456"
        assert request.method == "POST"


class TestClientFactoryFunctions:
    """Test suite for client factory functions."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_get_traced_client(self) -> None:
        """Test get_traced_client factory function."""
        route = respx.get("http://api.example.com/users").mock(
            return_value=Response(200, json={"status": "ok"})
        )

        set_trace_id("factory-test")

        async with get_traced_client(base_url="http://api.example.com") as client:
            await client.get("/users")

        assert route.called
        request = route.calls.last.request
        assert request.url == "http://api.example.com/users"
        assert request.headers[TRACE_ID_HEADER] == "factory-test"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_get_traced_client_with_timeout(self) -> None:
        """Test factory function with custom timeout."""
        route = respx.get("http://test.example.com/api").mock(
            return_value=Response(200, json={"status": "ok"})
        )

        set_trace_id("timeout-test")

        async with get_traced_client(timeout=5.0) as client:
            assert client.timeout.read == 5.0
            await client.get("http://test.example.com/api")

        assert route.called
        request = route.calls.last.request
        assert request.headers[TRACE_ID_HEADER] == "timeout-test"

    @respx.mock
    def test_get_traced_sync_client(self) -> None:
        """Test get_traced_sync_client factory function."""
        route = respx.get("http://api.example.com/data").mock(
            return_value=Response(200, json={"status": "ok"})
        )

        set_trace_id("sync-factory-test")

        with get_traced_sync_client(base_url="http://api.example.com") as client:
            client.get("/data")

        assert route.called
        request = route.calls.last.request
        assert request.url == "http://api.example.com/data"
        assert request.headers[TRACE_ID_HEADER] == "sync-factory-test"


class TestConvenienceFunctions:
    """Test suite for convenience functions."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_traced_get(self) -> None:
        """Test traced_get convenience function."""
        route = respx.get("http://test.example.com/api/data").mock(
            return_value=Response(200, json={"data": "value"})
        )

        set_trace_id("convenience-get")

        response = await traced_get("http://test.example.com/api/data")

        assert response.json() == {"data": "value"}
        assert route.called
        request = route.calls.last.request
        assert request.headers[TRACE_ID_HEADER] == "convenience-get"
        assert request.method == "GET"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_traced_post(self) -> None:
        """Test traced_post convenience function."""
        route = respx.post("http://test.example.com/api/create").mock(
            return_value=Response(200, json={"created": True, "id": 123})
        )

        set_trace_id("convenience-post")

        response = await traced_post(
            "http://test.example.com/api/create",
            json={"name": "test", "value": 42},
        )

        assert response.json() == {"created": True, "id": 123}
        assert route.called
        request = route.calls.last.request
        assert request.headers[TRACE_ID_HEADER] == "convenience-post"
        assert request.method == "POST"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_traced_get_with_params(self) -> None:
        """Test traced_get with query parameters."""
        route = respx.get("http://test.example.com/api/search").mock(
            return_value=Response(200, json={"results": []})
        )

        set_trace_id("get-with-params")

        response = await traced_get(
            "http://test.example.com/api/search",
            params={"q": "test", "limit": 10},
        )

        assert response.json() == {"results": []}
        assert route.called
        request = route.calls.last.request
        assert request.headers[TRACE_ID_HEADER] == "get-with-params"
        assert "q=test" in str(request.url)
        assert "limit=10" in str(request.url)


@pytest.fixture(autouse=True)
def _cleanup_trace_context():
    """Ensure trace context is clean before and after each test."""
    clear_trace_id()
    yield
    clear_trace_id()
