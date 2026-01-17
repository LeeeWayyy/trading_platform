"""Gate ordering tests for order submission."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from apps.execution_gateway.app_context import AppContext
from apps.execution_gateway.routes.orders import submit_order
from apps.execution_gateway.schemas import OrderRequest


@pytest.mark.asyncio()
async def test_position_reservation_happens_before_idempotency() -> None:
    """Verify reservation is executed before the idempotency check."""
    call_order: list[str] = []

    def reserve_side_effect(symbol, side, qty, max_limit, current_position=0):
        call_order.append("reserve")
        return SimpleNamespace(
            success=True,
            token="test-token-123",
            reason="",
            previous_position=0,
            new_position=qty if side == "buy" else -qty,
        )

    def idempotency_side_effect(_client_order_id: str):
        call_order.append("idempotency")
        return SimpleNamespace(
            status="submitted",
            broker_order_id="broker-123",
            symbol="AAPL",
            side="buy",
            qty=1,
            order_type="market",
            limit_price=None,
            created_at=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
        )

    position_reservation = Mock()
    position_reservation.reserve.side_effect = reserve_side_effect
    position_reservation.release = Mock()

    kill_switch = Mock()
    kill_switch.is_engaged.return_value = False

    circuit_breaker = Mock()
    circuit_breaker.is_tripped.return_value = False

    recovery_manager = Mock()
    recovery_manager.is_kill_switch_unavailable.return_value = False
    recovery_manager.is_circuit_breaker_unavailable.return_value = False
    recovery_manager.is_position_reservation_unavailable.return_value = False
    recovery_manager.kill_switch = kill_switch
    recovery_manager.circuit_breaker = circuit_breaker
    recovery_manager.position_reservation = position_reservation

    mock_db = Mock()
    mock_db.get_order_by_client_id.side_effect = idempotency_side_effect
    mock_db.get_position_by_symbol = Mock(return_value=0)

    # Mock risk_config with position_limits
    risk_config = Mock()
    risk_config.position_limits.max_position_size = 1000

    ctx = AppContext(
        db=mock_db,
        redis=None,
        alpaca=None,
        liquidity_service=None,
        reconciliation_service=None,
        recovery_manager=recovery_manager,
        risk_config=risk_config,
        fat_finger_validator=Mock(validate_order=Mock(return_value=SimpleNamespace(approved=True))),
        twap_slicer=Mock(),
        webhook_secret="",
    )

    config = SimpleNamespace(dry_run=True, strategy_id="alpha_baseline")
    order = OrderRequest(symbol="AAPL", side="buy", qty=1, order_type="market")

    await submit_order(
        order=order,
        _auth_context=Mock(),
        _rate_limit_remaining=1,
        ctx=ctx,
        config=config,
    )

    assert call_order == ["reserve", "idempotency"]
