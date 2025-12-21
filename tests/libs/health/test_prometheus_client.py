"""Unit tests for the PrometheusClient."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from libs.health.prometheus_client import LatencyMetrics, PrometheusClient


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

    # Queue 15 timeout exceptions (5 services * 3 percentiles)
    # The exceptions are caught at get_latency_percentile level, returning None
    # All services will have None latencies, triggering the stale cache fallback
    queue: deque[Any] = deque([httpx.TimeoutException("timeout") for _ in range(15)])

    def factory(*_: Any, **__: Any) -> MockAsyncClient:
        return MockAsyncClient(queue)

    monkeypatch.setattr(httpx, "AsyncClient", factory)

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
