"""Tests for reconciliation gating logic.

Note: The detailed reduce-only gating logic (_enforce_reduce_only_order) was simplified
during the Phase 2B refactoring. The current implementation blocks ALL orders during
reconciliation rather than implementing per-order reduce-only checks.

These tests verify the simplified reconciliation gating behavior via the
_require_reconciliation_ready_or_reduce_only function in routes/orders.py.
"""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from apps.execution_gateway.routes.orders import _require_reconciliation_ready_or_reduce_only
from apps.execution_gateway.schemas import OrderRequest


@pytest.mark.asyncio()
async def test_reconciliation_gate_blocks_during_startup():
    """Verify orders are blocked when reconciliation is not complete."""
    order = OrderRequest(symbol="AAPL", side="buy", qty=5, order_type="market")

    # Mock reconciliation service that is NOT complete
    reconciliation_service = Mock()
    reconciliation_service.is_startup_complete.return_value = False
    reconciliation_service.override_active.return_value = False
    reconciliation_service.startup_timed_out.return_value = False
    reconciliation_service.startup_elapsed_seconds.return_value = 10

    ctx = SimpleNamespace(reconciliation_service=reconciliation_service)
    config = SimpleNamespace(dry_run=False)

    with pytest.raises(HTTPException) as exc_info:
        await _require_reconciliation_ready_or_reduce_only(
            order, ctx, config, "test-order-id"
        )

    assert exc_info.value.status_code == 503
    assert "Reconciliation in progress" in exc_info.value.detail["error"]


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
