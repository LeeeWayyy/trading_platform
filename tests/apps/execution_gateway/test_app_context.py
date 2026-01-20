from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from apps.execution_gateway.app_context import AppContext
from apps.execution_gateway.fat_finger_validator import FatFingerValidator
from apps.execution_gateway.order_slicer import TWAPSlicer
from apps.execution_gateway.schemas import FatFingerThresholds
from libs.trading.risk_management import RiskConfig


def _make_context() -> AppContext:
    return AppContext(
        db=MagicMock(),
        redis=None,
        alpaca=None,
        liquidity_service=None,
        reconciliation_service=None,
        recovery_manager=MagicMock(),
        risk_config=RiskConfig(),
        fat_finger_validator=FatFingerValidator(FatFingerThresholds()),
        twap_slicer=TWAPSlicer(),
        webhook_secret="test-secret",
    )


def test_app_context_defaults() -> None:
    ctx = _make_context()
    assert isinstance(ctx.position_metrics_lock, asyncio.Lock)
    assert ctx.tracked_position_symbols == set()


def test_app_context_default_factories_are_isolated() -> None:
    ctx_a = _make_context()
    ctx_b = _make_context()

    assert ctx_a.position_metrics_lock is not ctx_b.position_metrics_lock
    assert ctx_a.tracked_position_symbols is not ctx_b.tracked_position_symbols

    ctx_a.tracked_position_symbols.add("AAPL")
    assert ctx_b.tracked_position_symbols == set()
