"""Tests for shared execution-gate status helpers."""

from __future__ import annotations

from apps.web_console_ng.components.execution_gate import (
    is_model_execution_safe,
    is_strategy_execution_safe,
    normalize_execution_status,
)


def test_normalize_execution_status_handles_none_and_whitespace() -> None:
    assert normalize_execution_status(None) == "unknown"
    assert normalize_execution_status(" READY ") == "ready"


def test_strategy_and_model_safe_sets_include_ready() -> None:
    assert is_strategy_execution_safe("READY")
    assert is_model_execution_safe("ready")
    assert not is_strategy_execution_safe("inactive")
    assert not is_model_execution_safe("failed")
