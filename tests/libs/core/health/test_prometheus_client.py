"""Unit tests for the PrometheusClient."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from libs.core.health.prometheus_client import LatencyMetrics, PrometheusClient


class MockResponse:
    def __init__(self, status_code: int, json_data: dict[str, Any]) -> None:
        self.status_code = status_code
        self._json_data = json_data

    def json(self) -> dict[str, Any]:
        return self._json_data

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=None)


class MockAsyncClient:
    """Minimal async stub for httpx.AsyncClient."""

    is_closed = False  # Mimic httpx.AsyncClient.is_closed property

    def __init__(
        self,
        queue: deque[Any],
        call_log: list[str] | None = None,
        delay: float = 0.0,
    ) -> None:
        self.queue = queue
        self.call_log = call_log
        self.delay = delay

    async def __aenter__(self) -> MockAsyncClient:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:  # type: ignore[override]
        return False

    async def get(
        self,
        url: str,
        params: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> MockResponse:
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.call_log is not None:
            self.call_log.append(url)
        if not self.queue:
            raise AssertionError("No mock responses remaining")
        item = self.queue.popleft()
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture()
def prometheus_client(monkeypatch: pytest.MonkeyPatch) -> PrometheusClient:
    queue: deque[Any] = deque()

    def factory(*_: Any, **__: Any) -> MockAsyncClient:
        return MockAsyncClient(queue)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    client = PrometheusClient("http://prom")
    client._queue = queue  # type: ignore[attr-defined]  # test helper
    return client


def test_percentile_calculation(monkeypatch: pytest.MonkeyPatch) -> None:
    queue: deque[Any] = deque(
        [
            MockResponse(
                200,
                {
                    "status": "success",
                    "data": {"result": [{"value": [123, "0.123"]}]},
                },
            )
        ]
    )

    def factory(*_: Any, **__: Any) -> MockAsyncClient:
        return MockAsyncClient(queue)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    client = PrometheusClient("http://prom")
    value = asyncio.run(client.get_latency_percentile("metric", 0.5))
    assert value == pytest.approx(123.0)  # 0.123 seconds -> 123 ms


def test_connection_failure_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    queue: deque[Any] = deque(
        [httpx.RequestError("boom", request=httpx.Request("GET", "http://prom"))]
    )

    def factory(*_: Any, **__: Any) -> MockAsyncClient:
        return MockAsyncClient(queue)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    client = PrometheusClient("http://prom")
    value = asyncio.run(client.get_latency_percentile("metric", 0.5))
    assert value is None


def test_cache_hit_avoids_query(monkeypatch: pytest.MonkeyPatch) -> None:
    # 5 services * 3 percentiles = 15 responses needed
    queue: deque[Any] = deque(
        [
            MockResponse(
                200,
                {"status": "success", "data": {"result": [{"value": [0, "0.100"]}]}},
            )
            for _ in range(15)
        ]
    )
    call_log: list[str] = []

    def factory(*_: Any, **__: Any) -> MockAsyncClient:
        return MockAsyncClient(queue, call_log=call_log)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    client = PrometheusClient("http://prom", cache_ttl_seconds=60)

    first = asyncio.run(client.get_service_latencies())
    assert first[1] is False  # not stale
    assert len(call_log) == 15  # 5 services * 3 percentiles

    second = asyncio.run(client.get_service_latencies())
    # Cache hit, no additional calls
    assert len(call_log) == 15
    assert second[1] is False


def test_cache_stale_used_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that stale cache is used when ALL Prometheus queries fail.

    P1 Fix: When all services return None latencies (indicating complete
    Prometheus failure), use the stale cache instead of empty results.
    This provides graceful degradation.
    """
    client = PrometheusClient("http://prom", cache_ttl_seconds=1)
    cached = {
        "signal_service": LatencyMetrics(
            service="signal_service",
            operation="signal_generation",
            p50_ms=1.0,
            p95_ms=2.0,
            p99_ms=3.0,
            fetched_at=datetime.now(UTC) - timedelta(seconds=2),
        )
    }
    # Cache is expired (2 seconds old, TTL is 1 second)
    client._cache["all_latencies"] = (cached, datetime.now(UTC) - timedelta(seconds=2))

    async def _raise_request_error(*_args: Any, **_kwargs: Any) -> float | None:
        raise httpx.RequestError("timeout", request=httpx.Request("GET", "http://prom"))

    monkeypatch.setattr(PrometheusClient, "get_latency_percentile", _raise_request_error)

    result, is_stale, age = asyncio.run(client.get_service_latencies())

    # P1 Fix: When ALL services return None latencies (Prometheus unavailable),
    # use stale cache instead of returning empty/None data
    assert is_stale is True
    assert len(result) == 1  # Only signal_service was in stale cache
    assert "signal_service" in result
    # Cached data preserved with staleness markers
    assert result["signal_service"].p50_ms == 1.0
    assert result["signal_service"].p95_ms == 2.0
    assert result["signal_service"].p99_ms == 3.0
    assert result["signal_service"].is_stale is True
    assert age is not None
    assert age >= 2.0


def test_parallel_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    queue: deque[Any] = deque(
        [
            MockResponse(200, {"status": "success", "data": {"result": [{"value": [0, "0.010"]}]}})
            for _ in range(15)
        ]
    )
    call_log: list[str] = []

    def factory(*_: Any, **__: Any) -> MockAsyncClient:
        return MockAsyncClient(queue, call_log=call_log, delay=0.01)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    client = PrometheusClient("http://prom")

    start = time.perf_counter()
    result, _, _ = asyncio.run(client.get_service_latencies())
    elapsed = time.perf_counter() - start

    assert len(result) == 5
    # Parallelism should keep elapsed close to single call delay, not 15x
    assert elapsed < 0.05
    assert len(call_log) == 15


def test_get_stale_latencies_or_none_returns_none_when_no_cache() -> None:
    """Test _get_stale_latencies_or_none returns None when cache key doesn't exist."""
    client = PrometheusClient("http://prom")
    now = datetime.now(UTC)
    result = client._get_stale_latencies_or_none("nonexistent_key", now)
    assert result is None


def test_verify_histograms_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test verify_histograms when histograms exist in Prometheus."""
    # Create responses for all 5 services with data
    queue: deque[Any] = deque(
        [
            MockResponse(
                200,
                {"status": "success", "data": {"result": [{"value": [0, "1"]}]}},
            )
            for _ in range(5)
        ]
    )

    def factory(*_: Any, **__: Any) -> MockAsyncClient:
        return MockAsyncClient(queue)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    client = PrometheusClient("http://prom")
    results = asyncio.run(client.verify_histograms())

    assert len(results) == 5
    for service in PrometheusClient.LATENCY_METRICS:
        assert results[service] is True


def test_verify_histograms_no_data(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test verify_histograms when histograms don't have data."""
    # Create responses with empty results
    queue: deque[Any] = deque(
        [MockResponse(200, {"status": "success", "data": {"result": []}}) for _ in range(5)]
    )

    def factory(*_: Any, **__: Any) -> MockAsyncClient:
        return MockAsyncClient(queue)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    client = PrometheusClient("http://prom")
    results = asyncio.run(client.verify_histograms())

    assert len(results) == 5
    for service in PrometheusClient.LATENCY_METRICS:
        assert results[service] is False


def test_verify_histograms_request_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test verify_histograms handles request errors gracefully."""
    queue: deque[Any] = deque(
        [
            httpx.RequestError("network error", request=httpx.Request("GET", "http://prom"))
            for _ in range(5)
        ]
    )

    def factory(*_: Any, **__: Any) -> MockAsyncClient:
        return MockAsyncClient(queue)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    client = PrometheusClient("http://prom")
    results = asyncio.run(client.verify_histograms())

    assert len(results) == 5
    for service in PrometheusClient.LATENCY_METRICS:
        assert results[service] is False


def test_verify_histograms_http_status_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test verify_histograms handles HTTP status errors."""
    queue: deque[Any] = deque(
        [MockResponse(500, {"error": "internal server error"}) for _ in range(5)]
    )

    def factory(*_: Any, **__: Any) -> MockAsyncClient:
        return MockAsyncClient(queue)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    client = PrometheusClient("http://prom")
    results = asyncio.run(client.verify_histograms())

    assert len(results) == 5
    for service in PrometheusClient.LATENCY_METRICS:
        assert results[service] is False


def test_verify_histograms_timeout_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test verify_histograms handles timeout errors."""
    queue: deque[Any] = deque([TimeoutError("timeout") for _ in range(5)])

    def factory(*_: Any, **__: Any) -> MockAsyncClient:
        return MockAsyncClient(queue)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    client = PrometheusClient("http://prom")
    results = asyncio.run(client.verify_histograms())

    assert len(results) == 5
    for service in PrometheusClient.LATENCY_METRICS:
        assert results[service] is False


def test_get_latency_percentile_returns_none_when_value_str_is_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test get_latency_percentile returns None when value string is None."""
    queue: deque[Any] = deque(
        [
            MockResponse(
                200,
                {"status": "success", "data": {"result": [{"value": [0, None]}]}},
            )
        ]
    )

    def factory(*_: Any, **__: Any) -> MockAsyncClient:
        return MockAsyncClient(queue)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    client = PrometheusClient("http://prom")
    result = asyncio.run(client.get_latency_percentile("metric", 0.5))
    assert result is None


def test_get_latency_percentile_returns_none_when_no_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test get_latency_percentile returns None when results array is empty."""
    queue: deque[Any] = deque([MockResponse(200, {"status": "success", "data": {"result": []}})])

    def factory(*_: Any, **__: Any) -> MockAsyncClient:
        return MockAsyncClient(queue)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    client = PrometheusClient("http://prom")
    result = asyncio.run(client.get_latency_percentile("metric", 0.5))
    assert result is None


def test_get_latency_percentile_returns_none_when_status_not_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test get_latency_percentile returns None when status is not success."""
    queue: deque[Any] = deque([MockResponse(200, {"status": "error", "data": {"result": []}})])

    def factory(*_: Any, **__: Any) -> MockAsyncClient:
        return MockAsyncClient(queue)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    client = PrometheusClient("http://prom")
    result = asyncio.run(client.get_latency_percentile("metric", 0.5))
    assert result is None


def test_get_latency_percentile_handles_value_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test get_latency_percentile handles ValueError during parsing."""
    queue: deque[Any] = deque(
        [
            MockResponse(
                200,
                {"status": "success", "data": {"result": [{"value": [0, "not_a_number"]}]}},
            )
        ]
    )

    def factory(*_: Any, **__: Any) -> MockAsyncClient:
        return MockAsyncClient(queue)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    client = PrometheusClient("http://prom")
    result = asyncio.run(client.get_latency_percentile("metric", 0.5))
    assert result is None


def test_get_latency_percentile_handles_key_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test get_latency_percentile handles KeyError during parsing."""
    queue: deque[Any] = deque(
        [
            MockResponse(
                200,
                {"status": "success", "data": {"result": [{"no_value_key": []}]}},
            )
        ]
    )

    def factory(*_: Any, **__: Any) -> MockAsyncClient:
        return MockAsyncClient(queue)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    client = PrometheusClient("http://prom")
    result = asyncio.run(client.get_latency_percentile("metric", 0.5))
    assert result is None


def test_get_latency_percentile_handles_index_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test get_latency_percentile handles IndexError during parsing."""
    queue: deque[Any] = deque(
        [
            MockResponse(
                200,
                {"status": "success", "data": {"result": [{"value": []}]}},
            )
        ]
    )

    def factory(*_: Any, **__: Any) -> MockAsyncClient:
        return MockAsyncClient(queue)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    client = PrometheusClient("http://prom")
    result = asyncio.run(client.get_latency_percentile("metric", 0.5))
    assert result is None


def test_get_latency_percentile_handles_type_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test get_latency_percentile handles TypeError during parsing."""
    queue: deque[Any] = deque(
        [
            MockResponse(
                200,
                {"status": "success", "data": {"result": [{"value": "not_a_list"}]}},
            )
        ]
    )

    def factory(*_: Any, **__: Any) -> MockAsyncClient:
        return MockAsyncClient(queue)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    client = PrometheusClient("http://prom")
    result = asyncio.run(client.get_latency_percentile("metric", 0.5))
    assert result is None


def test_get_latency_percentile_handles_timeout_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test get_latency_percentile handles TimeoutError."""
    queue: deque[Any] = deque([TimeoutError("timeout")])

    def factory(*_: Any, **__: Any) -> MockAsyncClient:
        return MockAsyncClient(queue)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    client = PrometheusClient("http://prom")
    result = asyncio.run(client.get_latency_percentile("metric", 0.5))
    assert result is None


def test_get_service_latencies_all_missing_data(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test get_service_latencies logs info when all data is missing (not errors)."""
    # Create responses with no errors but all None values
    queue: deque[Any] = deque(
        [MockResponse(200, {"status": "success", "data": {"result": []}}) for _ in range(15)]
    )

    def factory(*_: Any, **__: Any) -> MockAsyncClient:
        return MockAsyncClient(queue)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    client = PrometheusClient("http://prom")

    result, is_stale, _ = asyncio.run(client.get_service_latencies())

    assert len(result) == 5
    assert is_stale is False
    # All metrics should have None values but no errors
    for metrics in result.values():
        assert metrics.p50_ms is None
        assert metrics.p95_ms is None
        assert metrics.p99_ms is None
        assert metrics.error is None


def test_get_service_latencies_exception_with_stale_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test get_service_latencies uses stale cache when exception occurs in _fetch_latencies."""
    client = PrometheusClient("http://prom", cache_ttl_seconds=1)
    cached = {
        "signal_service": LatencyMetrics(
            service="signal_service",
            operation="signal_generation",
            p50_ms=10.0,
            p95_ms=20.0,
            p99_ms=30.0,
            fetched_at=datetime.now(UTC) - timedelta(seconds=5),
        )
    }
    # Cache is expired
    client._cache["all_latencies"] = (cached, datetime.now(UTC) - timedelta(seconds=5))

    async def _raise_timeout(*_args: Any, **_kwargs: Any) -> dict[str, LatencyMetrics]:
        raise TimeoutError("timeout")

    monkeypatch.setattr(PrometheusClient, "_fetch_latencies_from_prometheus", _raise_timeout)

    result, is_stale, stale_age = asyncio.run(client.get_service_latencies())

    assert is_stale is True
    assert len(result) == 1
    assert "signal_service" in result
    assert result["signal_service"].p50_ms == 10.0
    assert result["signal_service"].is_stale is True
    assert stale_age is not None
    assert stale_age >= 5.0


def test_get_service_latencies_exception_without_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test get_service_latencies returns empty dict when exception occurs and no cache."""
    client = PrometheusClient("http://prom")
    # No cache set

    async def _raise_request_error(*_args: Any, **_kwargs: Any) -> dict[str, LatencyMetrics]:
        raise httpx.RequestError("network error", request=httpx.Request("GET", "http://prom"))

    monkeypatch.setattr(PrometheusClient, "_fetch_latencies_from_prometheus", _raise_request_error)

    result, is_stale, stale_age = asyncio.run(client.get_service_latencies())

    assert result == {}
    assert is_stale is True
    assert stale_age is None


def test_get_service_latencies_http_status_error_with_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test get_service_latencies uses stale cache on HTTP status error."""
    client = PrometheusClient("http://prom", cache_ttl_seconds=1)
    cached = {
        "execution_gateway": LatencyMetrics(
            service="execution_gateway",
            operation="order_placement",
            p50_ms=5.0,
            p95_ms=15.0,
            p99_ms=25.0,
            fetched_at=datetime.now(UTC) - timedelta(seconds=10),
        )
    }
    client._cache["all_latencies"] = (cached, datetime.now(UTC) - timedelta(seconds=10))

    async def _raise_http_error(*_args: Any, **_kwargs: Any) -> dict[str, LatencyMetrics]:
        raise httpx.HTTPStatusError(
            "500 Internal Server Error",
            request=httpx.Request("GET", "http://prom"),
            response=httpx.Response(500),
        )

    monkeypatch.setattr(PrometheusClient, "_fetch_latencies_from_prometheus", _raise_http_error)

    result, is_stale, stale_age = asyncio.run(client.get_service_latencies())

    assert is_stale is True
    assert len(result) == 1
    assert "execution_gateway" in result
    assert result["execution_gateway"].p50_ms == 5.0
    assert result["execution_gateway"].is_stale is True


def test_close_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test close method properly closes the HTTP client."""
    close_called = False

    class MockAsyncClientWithClose:
        is_closed = False

        async def aclose(self) -> None:
            nonlocal close_called
            close_called = True
            self.is_closed = True

    client = PrometheusClient("http://prom")
    mock_http_client = MockAsyncClientWithClose()
    client._client = mock_http_client  # type: ignore[assignment]

    asyncio.run(client.close())

    assert close_called is True
    assert client._client is None


def test_close_client_when_already_closed() -> None:
    """Test close method handles already closed client."""

    class MockAsyncClientAlreadyClosed:
        is_closed = True

        async def aclose(self) -> None:
            raise AssertionError("Should not be called when already closed")

    client = PrometheusClient("http://prom")
    mock_http_client = MockAsyncClientAlreadyClosed()
    client._client = mock_http_client  # type: ignore[assignment]

    # Should not raise
    asyncio.run(client.close())


def test_close_client_when_none() -> None:
    """Test close method handles None client."""
    client = PrometheusClient("http://prom")
    client._client = None

    # Should not raise
    asyncio.run(client.close())


def test_get_service_latencies_all_errors_no_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test get_service_latencies when all queries return errors but no stale cache exists.

    This tests the branch where all_errors is True but _get_stale_latencies_or_none returns None.
    In this case, the code should proceed to cache the error results and return them.
    """
    client = PrometheusClient("http://prom")
    # No cache set - ensure stale_result will be None

    # Create results where all services have errors
    error_results = {
        service: LatencyMetrics(
            service=service,
            operation=config["operation"],
            p50_ms=None,
            p95_ms=None,
            p99_ms=None,
            error="Connection failed",
            fetched_at=datetime.now(UTC),
        )
        for service, config in PrometheusClient.LATENCY_METRICS.items()
    }

    async def _return_all_errors(*_args: Any, **_kwargs: Any) -> dict[str, LatencyMetrics]:
        return error_results

    monkeypatch.setattr(PrometheusClient, "_fetch_latencies_from_prometheus", _return_all_errors)

    result, is_stale, stale_age = asyncio.run(client.get_service_latencies())

    # When all errors and no stale cache, results are cached and returned as fresh
    assert len(result) == 5
    assert is_stale is False  # No stale cache used
    assert stale_age is None
    # All results should have errors
    for metrics in result.values():
        assert metrics.error == "Connection failed"
        assert metrics.p50_ms is None
