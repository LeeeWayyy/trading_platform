"""Tests for dashboard.dispatch_trading_state_event error handling."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock

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


def test_dispatch_trading_state_event_circuit_breaker_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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


def test_metric_strip_value_update_uses_monotonic_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    metric = dashboard_module._MetricStripValue.__new__(dashboard_module._MetricStripValue)
    metric._format_fn = lambda value: str(value)
    metric._color_fn = None
    metric._last_update = None
    metric._value_label = MagicMock()
    metric._current_color = None

    monkeypatch.setattr(dashboard_module.time, "monotonic", lambda: 123.5)
    metric.update(42)

    assert metric._last_update == 123.5


def test_metric_strip_value_is_stale_uses_monotonic_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    metric = dashboard_module._MetricStripValue.__new__(dashboard_module._MetricStripValue)
    metric._last_update = 100.0

    monkeypatch.setattr(dashboard_module.time, "monotonic", lambda: 140.5)
    monkeypatch.setattr(dashboard_module.time, "time", lambda: 10_000_000.0)

    assert metric.is_stale(threshold=30.0)


def test_should_apply_strategy_context_result_true_when_generation_and_symbol_match() -> None:
    assert dashboard_module.should_apply_strategy_context_result(
        refresh_generation=4,
        active_generation=4,
        expected_symbol="AAPL",
        current_symbol="AAPL",
    )


def test_should_apply_strategy_context_result_false_when_stale() -> None:
    assert not dashboard_module.should_apply_strategy_context_result(
        refresh_generation=4,
        active_generation=5,
        expected_symbol="AAPL",
        current_symbol="AAPL",
    )
    assert not dashboard_module.should_apply_strategy_context_result(
        refresh_generation=5,
        active_generation=5,
        expected_symbol="AAPL",
        current_symbol="MSFT",
    )


def test_plan_strategy_context_refresh_request_starts_when_idle() -> None:
    generation, mark_pending, start_generation = (
        dashboard_module.plan_strategy_context_refresh_request(
            current_generation=2,
            task_running=False,
            dashboard_closing=False,
            invalidate_running=False,
        )
    )
    assert generation == 3
    assert mark_pending is False
    assert start_generation == 3


def test_plan_strategy_context_refresh_request_marks_pending_without_invalidation() -> None:
    generation, mark_pending, start_generation = (
        dashboard_module.plan_strategy_context_refresh_request(
            current_generation=5,
            task_running=True,
            dashboard_closing=False,
            invalidate_running=False,
        )
    )
    assert generation == 5
    assert mark_pending is True
    assert start_generation is None


def test_plan_strategy_context_refresh_request_marks_pending_with_invalidation() -> None:
    generation, mark_pending, start_generation = (
        dashboard_module.plan_strategy_context_refresh_request(
            current_generation=5,
            task_running=True,
            dashboard_closing=False,
            invalidate_running=True,
        )
    )
    assert generation == 6
    assert mark_pending is True
    assert start_generation is None


def test_plan_strategy_context_refresh_request_noop_when_dashboard_closing() -> None:
    generation, mark_pending, start_generation = (
        dashboard_module.plan_strategy_context_refresh_request(
            current_generation=7,
            task_running=True,
            dashboard_closing=True,
            invalidate_running=True,
        )
    )
    assert generation == 7
    assert mark_pending is False
    assert start_generation is None


def test_should_run_pending_strategy_context_refresh() -> None:
    assert dashboard_module.should_run_pending_strategy_context_refresh(
        refresh_pending=True, dashboard_closing=False
    )
    assert not dashboard_module.should_run_pending_strategy_context_refresh(
        refresh_pending=True, dashboard_closing=True
    )
    assert not dashboard_module.should_run_pending_strategy_context_refresh(
        refresh_pending=False, dashboard_closing=False
    )


def test_should_enable_strategy_context_refresh_for_execution_gating() -> None:
    assert dashboard_module.should_enable_strategy_context_refresh(
        gate_enabled=True,
        has_strategy_widget=False,
    )


def test_should_enable_strategy_context_refresh_for_workspace_widget() -> None:
    assert dashboard_module.should_enable_strategy_context_refresh(
        gate_enabled=False,
        has_strategy_widget=True,
    )


def test_should_enable_strategy_context_refresh_disabled_without_consumers() -> None:
    assert not dashboard_module.should_enable_strategy_context_refresh(
        gate_enabled=False,
        has_strategy_widget=False,
    )


def test_resolve_model_gate_inputs_enforces_when_feature_enabled() -> None:
    status, version, enforce = dashboard_module.resolve_model_gate_inputs(
        model_status="unknown",
        model_version=None,
        feature_model_registry_enabled=True,
    )
    assert status == "unknown"
    assert version is None
    assert enforce is True


def test_resolve_model_gate_inputs_disables_model_gate_without_feature() -> None:
    status, version, enforce = dashboard_module.resolve_model_gate_inputs(
        model_status="unknown",
        model_version=None,
        feature_model_registry_enabled=False,
    )
    assert status == "ready"
    assert version == "disabled"
    assert enforce is False


def test_compute_workspace_data_staleness_no_live_data() -> None:
    stale, age = dashboard_module.compute_workspace_data_staleness(
        last_live_data_at=None,
        now=200.0,
    )
    assert not stale
    assert age == 0.0


def test_compute_workspace_data_staleness_clamps_negative_age() -> None:
    stale, age = dashboard_module.compute_workspace_data_staleness(
        last_live_data_at=250.0,
        now=200.0,
        threshold_s=30.0,
    )
    assert not stale
    assert age == 0.0


def test_compute_workspace_data_staleness_marks_stale_after_threshold() -> None:
    stale, age = dashboard_module.compute_workspace_data_staleness(
        last_live_data_at=100.0,
        now=132.0,
        threshold_s=30.0,
    )
    assert stale
    assert age == 32.0


def test_determine_workspace_lock_state_read_only_precedence() -> None:
    locked, title, detail = dashboard_module.determine_workspace_lock_state(
        connection_read_only=True,
        connection_state="DISCONNECTED",
        data_stale=True,
        data_age_s=120.0,
    )
    assert locked
    assert title == "Connection DISCONNECTED"
    assert "read-only" in detail


def test_determine_workspace_lock_state_stale_when_connected() -> None:
    locked, title, detail = dashboard_module.determine_workspace_lock_state(
        connection_read_only=False,
        connection_state="CONNECTED",
        data_stale=True,
        data_age_s=45.2,
    )
    assert locked
    assert title == "Live data stale"
    assert "45s" in detail


def test_determine_workspace_lock_state_unlocked_when_healthy() -> None:
    locked, title, detail = dashboard_module.determine_workspace_lock_state(
        connection_read_only=False,
        connection_state="CONNECTED",
        data_stale=False,
        data_age_s=0.0,
    )
    assert not locked
    assert title == ""
    assert detail == ""


def test_resolve_workspace_quick_links_for_trader() -> None:
    links = dashboard_module.resolve_workspace_quick_links(
        user_role="operator",
        feature_alerts_enabled=True,
        can_view_alerts=True,
        can_view_data_quality=True,
        feature_strategy_management_enabled=True,
        can_manage_strategies=True,
        feature_model_registry_enabled=True,
        can_view_models=True,
    )
    paths = {path for _, path in links}
    assert "/manual-order" in paths
    assert "/position-management" in paths
    assert "/circuit-breaker" in paths
    assert "/alerts" in paths
    assert "/journal" in paths
    assert "/strategies" in paths
    assert "/research?tab=promote" in paths
    assert "/compare" in paths
    assert "/data/inspector" in paths


def test_resolve_workspace_quick_links_hides_restricted_entries() -> None:
    links = dashboard_module.resolve_workspace_quick_links(
        user_role="viewer",
        feature_alerts_enabled=False,
        can_view_alerts=False,
        can_view_data_quality=False,
        feature_strategy_management_enabled=False,
        can_manage_strategies=False,
        feature_model_registry_enabled=False,
        can_view_models=False,
    )
    paths = {path for _, path in links}
    assert "/position-management" not in paths
    assert "/alerts" not in paths
    assert "/strategies" not in paths
    assert "/research?tab=promote" not in paths
    assert "/data/inspector" not in paths


def test_resolve_strategy_context_banner_healthy_for_ready_states() -> None:
    banner = dashboard_module.resolve_strategy_context_banner(
        strategy_status="READY",
        model_status="ready",
        gate_reason=None,
    )
    assert banner == "Execution context healthy."


def test_resolve_strategy_context_banner_degraded_uses_reason() -> None:
    banner = dashboard_module.resolve_strategy_context_banner(
        strategy_status="inactive",
        model_status="active",
        gate_reason="strategy is INACTIVE",
    )
    assert banner == "Execution context degraded: strategy is INACTIVE"


def test_resolve_workspace_connection_pill_live() -> None:
    text, tone = dashboard_module.resolve_workspace_connection_pill(
        state="connected",
        is_read_only=False,
    )
    assert text == "CONN LIVE"
    assert tone == "normal"


def test_resolve_workspace_connection_pill_read_only_warning() -> None:
    text, tone = dashboard_module.resolve_workspace_connection_pill(
        state="connected",
        is_read_only=True,
    )
    assert text == "CONN CONNECTED"
    assert tone == "warning"


def test_resolve_workspace_kill_switch_pill_states() -> None:
    engaged = dashboard_module.resolve_workspace_kill_switch_pill("ENGAGED")
    disarmed = dashboard_module.resolve_workspace_kill_switch_pill("DISENGAGED")
    unknown = dashboard_module.resolve_workspace_kill_switch_pill(None)
    assert engaged == ("KILL ENGAGED", "danger")
    assert disarmed == ("KILL DISARMED", "muted")
    assert unknown == ("KILL UNKNOWN", "warning")


def test_resolve_workspace_circuit_breaker_pill_states() -> None:
    tripped = dashboard_module.resolve_workspace_circuit_breaker_pill("TRIPPED")
    open_state = dashboard_module.resolve_workspace_circuit_breaker_pill("OPEN")
    quiet = dashboard_module.resolve_workspace_circuit_breaker_pill("QUIET_PERIOD")
    unknown = dashboard_module.resolve_workspace_circuit_breaker_pill(None)
    assert tripped == ("CB TRIPPED", "danger")
    assert open_state == ("CB READY", "normal")
    assert quiet == ("CB QUIET", "warning")
    assert unknown == ("CB UNKNOWN", "muted")
