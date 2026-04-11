"""Tests for strategy/model context gate status helpers."""

from __future__ import annotations

from apps.web_console_ng.components.strategy_context import resolve_execution_gate_state


def test_resolve_execution_gate_state_clear_when_safe() -> None:
    text, tone, banner = resolve_execution_gate_state(
        strategy_status="active",
        model_status="testing",
        gate_enabled=True,
    )
    assert text == "GATE CLEAR"
    assert tone == "positive"
    assert banner == "Execution context healthy."


def test_resolve_execution_gate_state_blocked_with_reason() -> None:
    text, tone, banner = resolve_execution_gate_state(
        strategy_status="active",
        model_status="failed",
        gate_enabled=True,
        gate_reason="model feed stale",
    )
    assert text == "GATE BLOCKED"
    assert tone == "negative"
    assert "model feed stale" in banner


def test_resolve_execution_gate_state_blocked_from_strategy_status() -> None:
    text, tone, banner = resolve_execution_gate_state(
        strategy_status="inactive",
        model_status="active",
        gate_enabled=True,
    )
    assert text == "GATE BLOCKED"
    assert tone == "negative"
    assert banner == "Execution gated: strategy is INACTIVE"


def test_resolve_execution_gate_state_off_when_gate_disabled() -> None:
    text, tone, banner = resolve_execution_gate_state(
        strategy_status="unknown",
        model_status="unknown",
        gate_enabled=False,
    )
    assert text == "GATE OFF"
    assert tone == "warning"
    assert "disabled" in banner
