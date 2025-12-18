from __future__ import annotations

import pytest

from apps.execution_gateway.reconciliation import ReconciliationService


@pytest.mark.asyncio()
async def test_startup_reconciliation_opens_gate():
    service = ReconciliationService(
        db_client=object(),
        alpaca_client=object(),
        redis_client=None,
        dry_run=False,
    )
    # Mock the internal _run_reconciliation so run_reconciliation_once runs its full logic
    service._run_reconciliation = lambda _mode: None  # type: ignore[assignment]

    assert service.is_startup_complete() is False
    result = await service.run_startup_reconciliation()
    assert result is True
    assert service.is_startup_complete() is True


@pytest.mark.asyncio()
async def test_periodic_reconciliation_opens_gate():
    service = ReconciliationService(
        db_client=object(),
        alpaca_client=object(),
        redis_client=None,
        dry_run=False,
    )
    service._run_reconciliation = lambda _mode: None  # type: ignore[assignment]

    assert service.is_startup_complete() is False
    await service.run_reconciliation_once("periodic")
    assert service.is_startup_complete() is True
