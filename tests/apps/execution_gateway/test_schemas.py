from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from apps.execution_gateway.schemas import (
    FatFingerThresholds,
    FatFingerThresholdsResponse,
    HealthResponse,
    OrderEventData,
    OrderRequest,
    PerformanceRequest,
    SlicingRequest,
    WebhookEvent,
)


def test_order_request_uppercases_symbol() -> None:
    order = OrderRequest(symbol="aapl", side="buy", qty=1, order_type="market")
    assert order.symbol == "AAPL"


def test_order_request_rejects_non_positive_prices() -> None:
    with pytest.raises(ValueError, match="Price must be positive"):
        OrderRequest(
            symbol="AAPL",
            side="buy",
            qty=1,
            order_type="limit",
            limit_price=Decimal("-1"),
        )

    with pytest.raises(ValueError, match="Price must be positive"):
        OrderRequest(
            symbol="AAPL",
            side="buy",
            qty=1,
            order_type="stop",
            stop_price=Decimal("0"),
        )


def test_slicing_request_enforces_minimum_qty_per_slice() -> None:
    with pytest.raises(ValueError, match="must be >= number of slices"):
        SlicingRequest(
            symbol="aapl",
            side="buy",
            qty=4,
            duration_minutes=5,
            interval_seconds=60,
            order_type="market",
        )

    request = SlicingRequest(
        symbol="aapl",
        side="buy",
        qty=5,
        duration_minutes=5,
        interval_seconds=60,
        order_type="market",
    )
    assert request.symbol == "AAPL"


def test_performance_request_rejects_invalid_ranges(monkeypatch: pytest.MonkeyPatch) -> None:
    today = date.today()

    with pytest.raises(ValueError, match="start_date must be <= end_date"):
        PerformanceRequest(start_date=today, end_date=today - timedelta(days=1))

    with pytest.raises(ValueError, match="end_date cannot be in the future"):
        PerformanceRequest(start_date=today, end_date=today + timedelta(days=1))

    monkeypatch.setenv("MAX_PERFORMANCE_DAYS", "5")
    with pytest.raises(ValueError, match="Date range cannot exceed 5 days"):
        PerformanceRequest(start_date=today - timedelta(days=6), end_date=today)


def test_fat_finger_thresholds_response_serializes_updated_at() -> None:
    thresholds = FatFingerThresholds(max_qty=10)
    naive_dt = datetime(2025, 1, 1, 12, 0, 0)
    response = FatFingerThresholdsResponse(
        default_thresholds=thresholds,
        symbol_overrides={},
        updated_at=naive_dt,
    )
    serialized = response.model_dump(mode="json")
    assert serialized["updated_at"].endswith("Z")

    utc_dt = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
    response = FatFingerThresholdsResponse(
        default_thresholds=thresholds,
        symbol_overrides={},
        updated_at=utc_dt,
    )
    serialized = response.model_dump(mode="json")
    assert serialized["updated_at"].endswith("Z")

    offset_dt = datetime(2025, 1, 1, 7, 0, 0, tzinfo=timezone(timedelta(hours=-5)))
    response = FatFingerThresholdsResponse(
        default_thresholds=thresholds,
        symbol_overrides={},
        updated_at=offset_dt,
    )
    serialized = response.model_dump(mode="json")
    assert serialized["updated_at"].endswith("-05:00")


def test_webhook_event_alias_population() -> None:
    data = OrderEventData(
        event="new",
        order={},
        timestamp=datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC),
    )
    event = WebhookEvent(event="order_update", data=data)
    assert event.event_type == "order_update"
    dumped = event.model_dump(by_alias=True)
    assert dumped["event"] == "order_update"


def test_health_response_serializes_timestamp() -> None:
    ts = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
    response = HealthResponse(
        status="healthy",
        version="0.1.0",
        dry_run=True,
        database_connected=True,
        alpaca_connected=True,
        timestamp=ts,
    )
    dumped = response.model_dump(mode="json")
    assert dumped["timestamp"].endswith("Z")
