"""Tests for execution context snapshot helpers."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from apps.web_console_ng.components.execution_context import (
    EXECUTION_CONTEXT_BLOCKED,
    EXECUTION_CONTEXT_READY,
    EXECUTION_CONTEXT_STALE,
    build_execution_context_snapshot,
    compute_execution_context_gate_state,
    format_execution_context_ribbon,
)
from apps.web_console_ng.components.order_ticket import OrderTicketComponent


def test_compute_execution_context_gate_state_ready() -> None:
    state, reason = compute_execution_context_gate_state(
        strategy_status="active",
        model_status="active",
        gate_reason=None,
        data_freshness_s=2.0,
        freshness_threshold_s=30.0,
    )
    assert state == EXECUTION_CONTEXT_READY
    assert reason is None


def test_compute_execution_context_gate_state_stale() -> None:
    state, reason = compute_execution_context_gate_state(
        strategy_status="active",
        model_status="active",
        gate_reason=None,
        data_freshness_s=45.0,
        freshness_threshold_s=30.0,
    )
    assert state == EXECUTION_CONTEXT_STALE
    assert reason is not None


def test_compute_execution_context_gate_state_blocked_on_gate_reason() -> None:
    state, reason = compute_execution_context_gate_state(
        strategy_status="active",
        model_status="active",
        gate_reason="strategy unresolved",
        data_freshness_s=5.0,
        freshness_threshold_s=30.0,
    )
    assert state == EXECUTION_CONTEXT_BLOCKED
    assert reason == "strategy unresolved"


def test_format_execution_context_ribbon_from_snapshot() -> None:
    snapshot = build_execution_context_snapshot(
        symbol="AAPL",
        strategy_id="alpha_baseline",
        strategy_status="active",
        model_status="active",
        model_version="v1.2.0",
        signal_id="sig-123",
        data_freshness_s=3.0,
        gate_reason=None,
        freshness_threshold_s=30.0,
    )
    text, tone = format_execution_context_ribbon(snapshot)
    assert "READY" in text
    assert "alpha_baseline" in text
    assert tone == "normal"


def _build_order_ticket_for_gate_tests() -> OrderTicketComponent:
    return OrderTicketComponent(
        trading_client=SimpleNamespace(),
        state_manager=SimpleNamespace(),
        connection_monitor=SimpleNamespace(is_read_only=lambda: False),
        user_id="u1",
        role="admin",
        strategies=[],
    )


def test_order_ticket_blocks_risk_increase_when_context_missing() -> None:
    """Risk-increasing orders require READY execution context snapshot."""
    ticket = _build_order_ticket_for_gate_tests()
    ticket._execution_gate_enabled = True  # noqa: SLF001
    ticket._strategy_status = "active"  # noqa: SLF001
    ticket._model_status = "active"  # noqa: SLF001

    reason = ticket._get_execution_gate_block_reason()  # noqa: SLF001

    assert reason == "Execution context blocked: unavailable"


def test_order_ticket_allows_risk_reducing_when_context_missing() -> None:
    """Risk-reducing exits remain available under degraded context."""
    ticket = _build_order_ticket_for_gate_tests()
    ticket._execution_gate_enabled = True  # noqa: SLF001
    ticket._current_position = 100  # noqa: SLF001
    ticket._state.side = "sell"  # noqa: SLF001
    ticket._state.quantity = 100  # noqa: SLF001

    reason = ticket._get_execution_gate_block_reason()  # noqa: SLF001

    assert reason is None


@pytest.mark.asyncio()
async def test_order_ticket_symbol_change_seeds_pending_context_snapshot() -> None:
    """Symbol handoff should expose explicit blocked context while refresh is pending."""
    ticket = _build_order_ticket_for_gate_tests()
    ticket._execution_gate_enabled = True  # noqa: SLF001

    await ticket.on_symbol_changed("MSFT")

    snapshot = ticket._execution_context_snapshot  # noqa: SLF001
    assert snapshot is not None
    assert snapshot.symbol == "MSFT"
    assert snapshot.gate_reason == "Refreshing strategy/model execution context for selected symbol"
    assert (
        ticket._get_execution_gate_block_reason()  # noqa: SLF001
        == "Execution context blocked: Refreshing strategy/model execution context for selected symbol"
    )
