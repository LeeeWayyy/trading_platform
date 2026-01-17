"""Tests for reconciliation gating logic.

Per ADR-0020 (Startup Gating with Reduce-Only Mode):
- During gating, compute effective_position from LIVE Alpaca data (not stale DB)
- Allow risk-reducing orders (orders that decrease position exposure)
- Reject all orders if broker API unavailable (fail closed)

These tests verify the reconciliation gating behavior via the
_require_reconciliation_ready_or_reduce_only function in routes/orders.py.
"""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from apps.execution_gateway.routes.orders import (
    _is_reduce_only_order,
    _require_reconciliation_ready_or_reduce_only,
)
from apps.execution_gateway.schemas import OrderRequest


class TestIsReduceOnlyOrder:
    """Tests for the _is_reduce_only_order helper function."""

    def test_no_position_is_not_reduce_only(self):
        """Any order with no position is position-increasing."""
        order = OrderRequest(symbol="AAPL", side="buy", qty=5, order_type="market")
        assert _is_reduce_only_order(order, None) is False

    def test_flat_position_is_not_reduce_only(self):
        """Any order with flat position (qty=0) is position-increasing."""
        order = OrderRequest(symbol="AAPL", side="buy", qty=5, order_type="market")
        assert _is_reduce_only_order(order, {"qty": 0}) is False

    def test_long_position_sell_is_reduce_only(self):
        """Sell order on long position is reduce-only."""
        order = OrderRequest(symbol="AAPL", side="sell", qty=5, order_type="market")
        assert _is_reduce_only_order(order, {"qty": 10}) is True

    def test_long_position_buy_is_not_reduce_only(self):
        """Buy order on long position increases exposure."""
        order = OrderRequest(symbol="AAPL", side="buy", qty=5, order_type="market")
        assert _is_reduce_only_order(order, {"qty": 10}) is False

    def test_short_position_buy_is_reduce_only(self):
        """Buy order on short position is reduce-only."""
        order = OrderRequest(symbol="AAPL", side="buy", qty=5, order_type="market")
        assert _is_reduce_only_order(order, {"qty": -10}) is True

    def test_short_position_sell_is_not_reduce_only(self):
        """Sell order on short position increases exposure."""
        order = OrderRequest(symbol="AAPL", side="sell", qty=5, order_type="market")
        assert _is_reduce_only_order(order, {"qty": -10}) is False


@pytest.mark.asyncio()
async def test_reconciliation_gate_blocks_position_increasing_during_startup():
    """Verify position-increasing orders are blocked when reconciliation is not complete."""
    order = OrderRequest(symbol="AAPL", side="buy", qty=5, order_type="market")

    # Mock reconciliation service that is NOT complete
    reconciliation_service = Mock()
    reconciliation_service.is_startup_complete.return_value = False
    reconciliation_service.override_active.return_value = False
    reconciliation_service.startup_timed_out.return_value = False
    reconciliation_service.startup_elapsed_seconds.return_value = 10

    # Mock Alpaca client - no position (flat)
    alpaca_client = Mock()
    alpaca_client.get_open_position.return_value = None

    ctx = SimpleNamespace(reconciliation_service=reconciliation_service, alpaca=alpaca_client)
    config = SimpleNamespace(dry_run=False)

    with pytest.raises(HTTPException) as exc_info:
        await _require_reconciliation_ready_or_reduce_only(
            order, ctx, config, "test-order-id"
        )

    assert exc_info.value.status_code == 503
    assert "Reconciliation in progress" in exc_info.value.detail["error"]


@pytest.mark.asyncio()
async def test_reconciliation_gate_allows_reduce_only_during_startup():
    """Verify reduce-only orders are allowed during reconciliation per ADR-0020."""
    # Sell order when long is reduce-only
    order = OrderRequest(symbol="AAPL", side="sell", qty=5, order_type="market")

    # Mock reconciliation service that is NOT complete
    reconciliation_service = Mock()
    reconciliation_service.is_startup_complete.return_value = False
    reconciliation_service.override_active.return_value = False
    reconciliation_service.startup_timed_out.return_value = False
    reconciliation_service.startup_elapsed_seconds.return_value = 10

    # Mock Alpaca client - long position
    alpaca_client = Mock()
    alpaca_client.get_open_position.return_value = {"qty": 100, "symbol": "AAPL"}

    ctx = SimpleNamespace(reconciliation_service=reconciliation_service, alpaca=alpaca_client)
    config = SimpleNamespace(dry_run=False)

    # Should NOT raise because it's a reduce-only order
    await _require_reconciliation_ready_or_reduce_only(
        order, ctx, config, "test-order-id"
    )


@pytest.mark.asyncio()
async def test_reconciliation_gate_blocks_when_broker_unavailable():
    """Verify orders are blocked (fail-closed) when broker is unavailable."""
    order = OrderRequest(symbol="AAPL", side="sell", qty=5, order_type="market")

    # Mock reconciliation service that is NOT complete
    reconciliation_service = Mock()
    reconciliation_service.is_startup_complete.return_value = False
    reconciliation_service.override_active.return_value = False
    reconciliation_service.startup_timed_out.return_value = False
    reconciliation_service.startup_elapsed_seconds.return_value = 10

    # Mock Alpaca client that fails
    alpaca_client = Mock()
    alpaca_client.get_open_position.side_effect = Exception("Connection failed")

    ctx = SimpleNamespace(reconciliation_service=reconciliation_service, alpaca=alpaca_client)
    config = SimpleNamespace(dry_run=False)

    with pytest.raises(HTTPException) as exc_info:
        await _require_reconciliation_ready_or_reduce_only(
            order, ctx, config, "test-order-id"
        )

    assert exc_info.value.status_code == 503
    assert "Broker unavailable" in exc_info.value.detail["error"]


@pytest.mark.asyncio()
async def test_reconciliation_gate_blocks_when_alpaca_client_none():
    """Verify orders are blocked (fail-closed) when Alpaca client is None."""
    order = OrderRequest(symbol="AAPL", side="sell", qty=5, order_type="market")

    # Mock reconciliation service that is NOT complete
    reconciliation_service = Mock()
    reconciliation_service.is_startup_complete.return_value = False
    reconciliation_service.override_active.return_value = False
    reconciliation_service.startup_timed_out.return_value = False

    ctx = SimpleNamespace(reconciliation_service=reconciliation_service, alpaca=None)
    config = SimpleNamespace(dry_run=False)

    with pytest.raises(HTTPException) as exc_info:
        await _require_reconciliation_ready_or_reduce_only(
            order, ctx, config, "test-order-id"
        )

    assert exc_info.value.status_code == 503
    assert "Broker unavailable" in exc_info.value.detail["error"]


@pytest.mark.asyncio()
async def test_reconciliation_gate_allows_after_completion():
    """Verify orders are allowed when reconciliation is complete."""
    order = OrderRequest(symbol="AAPL", side="buy", qty=5, order_type="market")

    # Mock reconciliation service that IS complete
    reconciliation_service = Mock()
    reconciliation_service.is_startup_complete.return_value = True

    ctx = SimpleNamespace(reconciliation_service=reconciliation_service)
    config = SimpleNamespace(dry_run=False)

    # Should not raise
    await _require_reconciliation_ready_or_reduce_only(
        order, ctx, config, "test-order-id"
    )


@pytest.mark.asyncio()
async def test_reconciliation_gate_allows_in_dry_run():
    """Verify orders are allowed in dry run mode regardless of reconciliation state."""
    order = OrderRequest(symbol="AAPL", side="buy", qty=5, order_type="market")

    # Mock reconciliation service that is NOT complete
    reconciliation_service = Mock()
    reconciliation_service.is_startup_complete.return_value = False

    ctx = SimpleNamespace(reconciliation_service=reconciliation_service)
    config = SimpleNamespace(dry_run=True)  # Dry run mode

    # Should not raise because dry_run=True
    await _require_reconciliation_ready_or_reduce_only(
        order, ctx, config, "test-order-id"
    )


@pytest.mark.asyncio()
async def test_reconciliation_gate_allows_with_override():
    """Verify orders are allowed when reconciliation override is active."""
    order = OrderRequest(symbol="AAPL", side="buy", qty=5, order_type="market")

    # Mock reconciliation service with override active
    reconciliation_service = Mock()
    reconciliation_service.is_startup_complete.return_value = False
    reconciliation_service.override_active.return_value = True
    reconciliation_service.override_context.return_value = {"reason": "manual_override"}

    ctx = SimpleNamespace(reconciliation_service=reconciliation_service)
    config = SimpleNamespace(dry_run=False)

    # Should not raise because override is active
    await _require_reconciliation_ready_or_reduce_only(
        order, ctx, config, "test-order-id"
    )
