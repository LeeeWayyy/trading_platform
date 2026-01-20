"""Unit tests for metrics helpers."""

from __future__ import annotations

import types

import pytest
from starlette.requests import Request

from apps.web_console_ng.core import metrics


class DummyCounter:
    def __init__(self) -> None:
        self.labels_calls: list[dict[str, str]] = []
        self.inc_calls = 0

    def labels(self, **kwargs: str) -> DummyCounter:
        self.labels_calls.append(kwargs)
        return self

    def inc(self) -> None:
        self.inc_calls += 1


class DummyGauge:
    def __init__(self) -> None:
        self.labels_calls: list[dict[str, str]] = []
        self.set_calls: list[int] = []

    def labels(self, **kwargs: str) -> DummyGauge:
        self.labels_calls.append(kwargs)
        return self

    def set(self, value: int) -> None:
        self.set_calls.append(value)


class DummyHistogram:
    def __init__(self) -> None:
        self.labels_calls: list[dict[str, str]] = []
        self.observe_calls: list[float] = []

    def labels(self, **kwargs: str) -> DummyHistogram:
        self.labels_calls.append(kwargs)
        return self

    def observe(self, value: float) -> None:
        self.observe_calls.append(value)


def _make_request() -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/metrics",
        "headers": [],
    }
    return Request(scope)


def test_sanitize_label_value() -> None:
    assert metrics._sanitize_label_value("  Timeout Error! ") == "timeout_error"
    assert metrics._sanitize_label_value("___") == "unknown"
    assert metrics._sanitize_label_value("") == "unknown"
    assert metrics._sanitize_label_value("ok-value") == "ok-value"


def test_record_state_save_error_sanitizes_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    dummy = DummyCounter()
    monkeypatch.setattr(metrics, "state_save_errors_total", dummy)
    monkeypatch.setattr(metrics, "POD_NAME", "pod-1")

    metrics.record_state_save_error("Bad Thing!")

    assert dummy.inc_calls == 1
    assert dummy.labels_calls == [{"pod": "pod-1", "reason": "bad_thing"}]


def test_record_auth_failure_sanitizes_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    dummy = DummyCounter()
    monkeypatch.setattr(metrics, "auth_failures_total", dummy)
    monkeypatch.setattr(metrics, "POD_NAME", "pod-9")

    metrics.record_auth_failure("oauth", "Token Expired")

    assert dummy.inc_calls == 1
    assert dummy.labels_calls == [{"pod": "pod-9", "auth_type": "oauth", "reason": "token_expired"}]


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (True, 1),
        (False, 0),
        (1, 1),
        (0, 0),
        ("tripped", 1),
        ("open", 1),
        ("engaged", 1),
        ("on", 1),
        ("normal", 0),
    ],
)
def test_set_circuit_breaker_state(
    monkeypatch: pytest.MonkeyPatch, state: object, expected: int
) -> None:
    dummy = DummyGauge()
    monkeypatch.setattr(metrics, "circuit_breaker_state", dummy)
    monkeypatch.setattr(metrics, "POD_NAME", "pod-2")

    metrics.set_circuit_breaker_state(state)  # type: ignore[arg-type]

    assert dummy.labels_calls == [{"pod": "pod-2"}]
    assert dummy.set_calls == [expected]


@pytest.mark.asyncio()
async def test_time_redis_operation_records_latency(monkeypatch: pytest.MonkeyPatch) -> None:
    dummy = DummyHistogram()
    monkeypatch.setattr(metrics, "redis_latency_seconds", dummy)
    monkeypatch.setattr(metrics, "POD_NAME", "pod-3")

    times = iter([1.0, 1.25])
    monkeypatch.setattr(metrics.time, "perf_counter", lambda: next(times))

    @metrics.time_redis_operation("get")
    async def do_work() -> str:
        return "ok"

    result = await do_work()

    assert result == "ok"
    assert dummy.labels_calls == [{"pod": "pod-3", "operation": "get"}]
    assert dummy.observe_calls == [0.25]


@pytest.mark.asyncio()
async def test_time_api_call_records_latency_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    dummy = DummyHistogram()
    monkeypatch.setattr(metrics, "api_latency_seconds", dummy)
    monkeypatch.setattr(metrics, "POD_NAME", "pod-4")

    times = iter([2.0, 2.75])
    monkeypatch.setattr(metrics.time, "perf_counter", lambda: next(times))

    @metrics.time_api_call("/endpoint")
    async def fail() -> None:
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        await fail()

    assert dummy.labels_calls == [{"pod": "pod-4", "endpoint": "/endpoint"}]
    assert dummy.observe_calls == [0.75]


@pytest.mark.asyncio()
async def test_update_resource_metrics_sets_gauge(monkeypatch: pytest.MonkeyPatch) -> None:
    dummy = DummyGauge()
    monkeypatch.setattr(metrics, "ws_connections", dummy)
    monkeypatch.setattr(metrics, "POD_NAME", "pod-5")

    fake_counter = types.SimpleNamespace(value=42)
    from apps.web_console_ng.core import health

    monkeypatch.setattr(health, "connection_counter", fake_counter)

    await metrics.update_resource_metrics()

    assert dummy.labels_calls == [{"pod": "pod-5"}]
    assert dummy.set_calls == [42]


@pytest.mark.asyncio()
async def test_metrics_endpoint_rejects_external(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(metrics.config, "METRICS_INGRESS_PROTECTED", False)
    monkeypatch.setattr(metrics, "is_internal_request", lambda _: False)

    request = _make_request()
    response = await metrics.metrics_endpoint(request)

    assert response.status_code == 403
    assert response.body == b"Forbidden"


@pytest.mark.asyncio()
async def test_metrics_endpoint_allows_internal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(metrics.config, "METRICS_INGRESS_PROTECTED", False)
    monkeypatch.setattr(metrics, "is_internal_request", lambda _: True)
    monkeypatch.setattr(metrics, "generate_latest", lambda: b"metrics")

    request = _make_request()
    response = await metrics.metrics_endpoint(request)

    assert response.status_code == 200
    assert response.body == b"metrics"
    assert response.media_type == metrics.CONTENT_TYPE_LATEST


@pytest.mark.asyncio()
async def test_metrics_endpoint_allows_when_ingress_protected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(metrics.config, "METRICS_INGRESS_PROTECTED", True)
    monkeypatch.setattr(metrics, "is_internal_request", lambda _: False)
    monkeypatch.setattr(metrics, "generate_latest", lambda: b"metrics")

    request = _make_request()
    response = await metrics.metrics_endpoint(request)

    assert response.status_code == 200
    assert response.body == b"metrics"
    assert response.media_type == metrics.CONTENT_TYPE_LATEST
