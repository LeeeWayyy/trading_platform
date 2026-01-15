"""Unit tests for the HealthClient.

Covers cache behaviour, staleness tracking, error handling, timestamp
extraction, and parallel execution of `check_all` using mocked httpx
clients.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from libs.core.health.health_client import HealthClient, ServiceHealthResponse


class MockResponse:
    def __init__(self, status_code: int, json_data: dict[str, Any]) -> None:
        self.status_code = status_code
        self._json_data = json_data

    def json(self) -> dict[str, Any]:
        return self._json_data


class MockAsyncClient:
    """Minimal async client stub for httpx.AsyncClient."""

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

    async def get(self, url: str, timeout: float | None = None) -> MockResponse:
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
def service_urls() -> dict[str, str]:
    return {"svc": "http://svc", "svc2": "http://svc2"}


@pytest.fixture()
def request_obj() -> httpx.Request:
    return httpx.Request("GET", "http://svc/health")


def _make_client_with_queue(
    queue: deque[Any],
    monkeypatch: pytest.MonkeyPatch,
    delay: float = 0.0,
    call_log: list[str] | None = None,
) -> None:
    """Patch httpx.AsyncClient to use the provided queue."""

    def factory(*_: Any, **__: Any) -> MockAsyncClient:
        return MockAsyncClient(queue, call_log=call_log, delay=delay)

    monkeypatch.setattr(httpx, "AsyncClient", factory)


def test_cache_hit_returns_stale_on_error(
    monkeypatch: pytest.MonkeyPatch, service_urls: dict[str, str], request_obj: httpx.Request
) -> None:
    client = HealthClient(service_urls, cache_ttl_seconds=30)
    queue: deque[Any] = deque(
        [
            MockResponse(
                200, {"status": "healthy", "service": "svc", "timestamp": "2025-12-20T00:00:00Z"}
            ),
            httpx.RequestError("boom", request=request_obj),
            httpx.RequestError("boom", request=request_obj),
        ]
    )
    _make_client_with_queue(queue, monkeypatch)

    first = asyncio.run(client.check_service("svc"))
    second = asyncio.run(client.check_service("svc"))

    assert first.status == "healthy"
    assert second.status == "stale"
    assert second.is_stale is True
    assert second.stale_age_seconds is not None
    assert second.last_operation_timestamp is not None


def test_cache_miss_fetches_fresh(
    monkeypatch: pytest.MonkeyPatch, service_urls: dict[str, str]
) -> None:
    client = HealthClient(service_urls, cache_ttl_seconds=30)
    queue: deque[Any] = deque(
        [
            MockResponse(
                200, {"status": "healthy", "service": "svc", "timestamp": "2025-12-20T00:00:00Z"}
            )
        ]
    )
    _make_client_with_queue(queue, monkeypatch)

    result = asyncio.run(client.check_service("svc"))

    assert result.status == "healthy"
    assert "svc" in client._cache  # cache populated


def test_staleness_age_calculated_from_cache(
    monkeypatch: pytest.MonkeyPatch, service_urls: dict[str, str]
) -> None:
    client = HealthClient(service_urls, cache_ttl_seconds=30)
    cached = ServiceHealthResponse(
        status="healthy",
        service="svc",
        timestamp=datetime.now(UTC) - timedelta(seconds=5),
        response_time_ms=10.0,
        details={},
    )
    client._cache["svc"] = (cached, datetime.now(UTC) - timedelta(seconds=5))

    queue: deque[Any] = deque([httpx.TimeoutException("timeout"), httpx.TimeoutException("timeout")])
    _make_client_with_queue(queue, monkeypatch)

    result = asyncio.run(client.check_service("svc"))

    assert result.status == "stale"
    assert result.is_stale is True
    assert result.stale_age_seconds is not None
    assert result.stale_age_seconds >= 5


def test_error_without_cache_marks_unreachable(
    monkeypatch: pytest.MonkeyPatch, service_urls: dict[str, str], request_obj: httpx.Request
) -> None:
    client = HealthClient(service_urls, cache_ttl_seconds=30)
    queue: deque[Any] = deque(
        [
            httpx.RequestError("boom", request=request_obj),
            httpx.RequestError("boom", request=request_obj),
        ]
    )
    _make_client_with_queue(queue, monkeypatch)

    result = asyncio.run(client.check_service("svc"))

    assert result.status == "unreachable"
    assert result.error is not None
    assert result.is_stale is False


def test_extract_last_operation_timestamp_primary_field(
    monkeypatch: pytest.MonkeyPatch, service_urls: dict[str, str]
) -> None:
    client = HealthClient(service_urls)
    data = {"last_order_at": "2025-12-20T10:00:00Z"}
    ts = client._extract_last_operation_timestamp(data)
    assert ts is not None
    assert ts.tzinfo == UTC


def test_extract_last_operation_timestamp_fallback_timestamp(
    monkeypatch: pytest.MonkeyPatch, service_urls: dict[str, str]
) -> None:
    client = HealthClient(service_urls)
    data = {"timestamp": "2025-12-20T09:00:00Z"}
    ts = client._extract_last_operation_timestamp(data)
    assert ts is not None
    assert ts.tzinfo == UTC


def test_extract_last_operation_timestamp_invalid_returns_none(
    service_urls: dict[str, str],
) -> None:
    client = HealthClient(service_urls)
    data = {"last_order_at": "not-a-date"}
    assert client._extract_last_operation_timestamp(data) is None


def test_check_all_runs_in_parallel(monkeypatch: pytest.MonkeyPatch) -> None:
    urls = {"svc": "http://svc", "svc2": "http://svc2"}
    client = HealthClient(urls, cache_ttl_seconds=30)
    queue: deque[Any] = deque(
        [
            MockResponse(
                200, {"status": "healthy", "service": "svc", "timestamp": "2025-12-20T00:00:00Z"}
            ),
            MockResponse(
                200, {"status": "healthy", "service": "svc2", "timestamp": "2025-12-20T00:00:00Z"}
            ),
        ]
    )
    call_log: list[str] = []
    _make_client_with_queue(queue, monkeypatch, delay=0.01, call_log=call_log)

    start = time.perf_counter()
    result = asyncio.run(client.check_all())
    elapsed = time.perf_counter() - start

    assert set(result.keys()) == {"svc", "svc2"}
    assert elapsed < 0.02  # should be closer to single request duration due to parallelism
    assert len(call_log) == 2
