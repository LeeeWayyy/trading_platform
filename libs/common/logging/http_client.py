"""HTTP client with automatic trace ID propagation.

This module provides HTTP client wrappers that automatically inject
trace IDs into outgoing requests, enabling distributed tracing across
service boundaries.

Example:
    >>> import httpx
    >>> from libs.common.logging.http_client import get_traced_client
    >>>
    >>> async with get_traced_client() as client:
    ...     response = await client.get("http://api.example.com/data")
    ...     # Request includes X-Trace-ID header automatically
"""

from typing import Any, Optional

import httpx

from libs.common.logging.context import TRACE_ID_HEADER, get_trace_id


class TracedHTTPXClient(httpx.AsyncClient):
    """HTTP client that automatically adds trace IDs to requests.

    Extends httpx.AsyncClient to inject the current trace ID from
    context into all outgoing requests. This enables request correlation
    across service boundaries.

    Example:
        >>> from libs.common.logging.context import set_trace_id
        >>> from libs.common.logging.http_client import TracedHTTPXClient
        >>>
        >>> set_trace_id("request-123")
        >>> async with TracedHTTPXClient() as client:
        ...     # This request will include X-Trace-ID: request-123
        ...     response = await client.get("http://api.example.com/data")
    """

    async def request(
        self,
        method: str,
        url: httpx.URL | str,
        **kwargs: Any,
    ) -> httpx.Response:
        """Send HTTP request with trace ID header.

        Overrides the base request method to inject trace ID header
        from logging context into all requests.

        Args:
            method: HTTP method (GET, POST, etc.)
            url: Request URL
            **kwargs: Additional request parameters

        Returns:
            HTTP response

        Example:
            >>> async with TracedHTTPXClient() as client:
            ...     response = await client.request("GET", "http://example.com")
        """
        # Get current trace ID from context
        trace_id = get_trace_id()

        # Add trace ID to headers if present
        if trace_id:
            headers = dict(kwargs.get("headers") or {})
            headers[TRACE_ID_HEADER] = trace_id
            kwargs["headers"] = headers

        return await super().request(method, url, **kwargs)


class TracedHTTPXSyncClient(httpx.Client):
    """Synchronous HTTP client with trace ID propagation.

    Synchronous version of TracedHTTPXClient for use in non-async code.

    Example:
        >>> from libs.common.logging.http_client import TracedHTTPXSyncClient
        >>>
        >>> with TracedHTTPXSyncClient() as client:
        ...     response = client.get("http://api.example.com/data")
    """

    def request(
        self,
        method: str,
        url: httpx.URL | str,
        **kwargs: Any,
    ) -> httpx.Response:
        """Send HTTP request with trace ID header.

        Args:
            method: HTTP method (GET, POST, etc.)
            url: Request URL
            **kwargs: Additional request parameters

        Returns:
            HTTP response
        """
        # Get current trace ID from context
        trace_id = get_trace_id()

        # Add trace ID to headers if present
        if trace_id:
            headers = dict(kwargs.get("headers") or {})
            headers[TRACE_ID_HEADER] = trace_id
            kwargs["headers"] = headers

        return super().request(method, url, **kwargs)


def get_traced_client(
    base_url: Optional[str] = None,
    timeout: float = 10.0,
    **kwargs: Any,
) -> TracedHTTPXClient:
    """Create a traced async HTTP client.

    Convenience function to create a TracedHTTPXClient with common
    configuration.

    Args:
        base_url: Base URL for all requests (optional)
        timeout: Request timeout in seconds
        **kwargs: Additional httpx.AsyncClient parameters

    Returns:
        Configured TracedHTTPXClient instance

    Example:
        >>> async with get_traced_client(base_url="http://api.example.com") as client:
        ...     response = await client.get("/users")
    """
    client_kwargs = {"timeout": timeout, **kwargs}
    if base_url is not None:
        client_kwargs["base_url"] = base_url

    return TracedHTTPXClient(**client_kwargs)


def get_traced_sync_client(
    base_url: Optional[str] = None,
    timeout: float = 10.0,
    **kwargs: Any,
) -> TracedHTTPXSyncClient:
    """Create a traced synchronous HTTP client.

    Convenience function to create a TracedHTTPXSyncClient with common
    configuration.

    Args:
        base_url: Base URL for all requests (optional)
        timeout: Request timeout in seconds
        **kwargs: Additional httpx.Client parameters

    Returns:
        Configured TracedHTTPXSyncClient instance

    Example:
        >>> with get_traced_sync_client(base_url="http://api.example.com") as client:
        ...     response = client.get("/users")
    """
    client_kwargs = {"timeout": timeout, **kwargs}
    if base_url is not None:
        client_kwargs["base_url"] = base_url

    return TracedHTTPXSyncClient(**client_kwargs)


async def traced_get(url: str, **kwargs: Any) -> httpx.Response:
    """Convenience function for traced GET request.

    Args:
        url: Request URL
        **kwargs: Additional request parameters

    Returns:
        HTTP response

    Example:
        >>> response = await traced_get("http://api.example.com/users")
    """
    async with get_traced_client() as client:
        return await client.get(url, **kwargs)


async def traced_post(url: str, **kwargs: Any) -> httpx.Response:
    """Convenience function for traced POST request.

    Args:
        url: Request URL
        **kwargs: Additional request parameters (json, data, etc.)

    Returns:
        HTTP response

    Example:
        >>> response = await traced_post(
        ...     "http://api.example.com/users",
        ...     json={"name": "Alice"}
        ... )
    """
    async with get_traced_client() as client:
        return await client.post(url, **kwargs)
