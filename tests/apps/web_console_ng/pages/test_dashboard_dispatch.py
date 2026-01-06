"""Tests for dashboard.dispatch_trading_state_event error handling."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from apps.web_console_ng.pages import dashboard as dashboard_module


def test_dispatch_trading_state_event_logs_on_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(dashboard_module.ui, "run_javascript", boom)

    with caplog.at_level(logging.WARNING):
        dashboard_module.dispatch_trading_state_event("cid", {"killSwitch": True})

    assert "trading_state_dispatch_failed" in caplog.text
    assert any(getattr(record, "client_id", None) == "cid" for record in caplog.records)
    assert any(getattr(record, "error", None) == "RuntimeError" for record in caplog.records)


def test_dispatch_trading_state_event_success_calls_js(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def record_js(js: str, *args: Any, **kwargs: Any) -> None:
        calls.append(js)

    monkeypatch.setattr(dashboard_module.ui, "run_javascript", record_js)

    dashboard_module.dispatch_trading_state_event("cid", {"killSwitchState": "ENGAGED"})

    assert len(calls) == 1
    assert "CustomEvent" in calls[0]
    assert "killSwitchState" in calls[0]


def test_dispatch_trading_state_event_circuit_breaker_state(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def record_js(js: str, *args: Any, **kwargs: Any) -> None:
        calls.append(js)

    monkeypatch.setattr(dashboard_module.ui, "run_javascript", record_js)

    dashboard_module.dispatch_trading_state_event("cid", {"circuitBreakerState": "OPEN"})

    assert len(calls) == 1
    assert "circuitBreakerState" in calls[0]


def test_dispatch_trading_state_event_handles_unserializable_payload(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    called = False

    def record_js(*_args: object, **_kwargs: object) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(dashboard_module.ui, "run_javascript", record_js)

    with caplog.at_level(logging.WARNING):
        dashboard_module.dispatch_trading_state_event("cid", {"bad": object()})

    assert "trading_state_dispatch_failed" in caplog.text
    assert called is False
