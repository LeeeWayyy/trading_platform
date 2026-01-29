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


def test_order_request_stop_requires_stop_price() -> None:
    with pytest.raises(ValueError, match="stop_price required"):
        OrderRequest(
            symbol="AAPL",
            side="buy",
            qty=1,
            order_type="stop",
        )


def test_order_request_stop_limit_requires_both_prices() -> None:
    with pytest.raises(ValueError, match="limit_price required"):
        OrderRequest(
            symbol="AAPL",
            side="buy",
            qty=1,
            order_type="stop_limit",
            stop_price=Decimal("150"),
        )

    with pytest.raises(ValueError, match="stop_price required"):
        OrderRequest(
            symbol="AAPL",
            side="buy",
            qty=1,
            order_type="stop_limit",
            limit_price=Decimal("150"),
        )


def test_order_request_stop_limit_relationship() -> None:
    with pytest.raises(ValueError, match="limit_price >= stop_price"):
        OrderRequest(
            symbol="AAPL",
            side="buy",
            qty=1,
            order_type="stop_limit",
            limit_price=Decimal("149"),
            stop_price=Decimal("150"),
        )

    with pytest.raises(ValueError, match="limit_price <= stop_price"):
        OrderRequest(
            symbol="AAPL",
            side="sell",
            qty=1,
            order_type="stop_limit",
            limit_price=Decimal("151"),
            stop_price=Decimal("150"),
        )


def test_order_request_twap_requires_duration_and_interval() -> None:
    with pytest.raises(ValueError, match="twap_duration_minutes required"):
        OrderRequest(
            symbol="AAPL",
            side="buy",
            qty=100,
            order_type="market",
            execution_style="twap",
            twap_interval_seconds=60,
        )

    with pytest.raises(ValueError, match="twap_interval_seconds required"):
        OrderRequest(
            symbol="AAPL",
            side="buy",
            qty=100,
            order_type="market",
            execution_style="twap",
            twap_duration_minutes=10,
        )


def test_order_request_twap_bounds_enforced() -> None:
    with pytest.raises(ValueError, match="twap_duration_minutes must be between"):
        OrderRequest(
            symbol="AAPL",
            side="buy",
            qty=100,
            order_type="market",
            execution_style="twap",
            twap_duration_minutes=4,
            twap_interval_seconds=60,
        )

    with pytest.raises(ValueError, match="twap_duration_minutes must be between"):
        OrderRequest(
            symbol="AAPL",
            side="buy",
            qty=100,
            order_type="market",
            execution_style="twap",
            twap_duration_minutes=500,
            twap_interval_seconds=60,
        )

    with pytest.raises(ValueError, match="twap_interval_seconds must be between"):
        OrderRequest(
            symbol="AAPL",
            side="buy",
            qty=100,
            order_type="market",
            execution_style="twap",
            twap_duration_minutes=10,
            twap_interval_seconds=20,
        )

    with pytest.raises(ValueError, match="twap_interval_seconds must be between"):
        OrderRequest(
            symbol="AAPL",
            side="buy",
            qty=100,
            order_type="market",
            execution_style="twap",
            twap_duration_minutes=10,
            twap_interval_seconds=400,
        )


def test_order_request_twap_minimum_slices_required() -> None:
    with pytest.raises(ValueError, match="TWAP requires at least 2 slices"):
        OrderRequest(
            symbol="AAPL",
            side="buy",
            qty=100,
            order_type="market",
            execution_style="twap",
            twap_duration_minutes=5,
            twap_interval_seconds=300,
        )


def test_order_request_twap_minimum_slice_quantity_required() -> None:
    with pytest.raises(ValueError, match="TWAP minimum slice size is 10 shares"):
        OrderRequest(
            symbol="AAPL",
            side="buy",
            qty=49,
            order_type="market",
            execution_style="twap",
            twap_duration_minutes=5,
            twap_interval_seconds=60,
        )


def test_order_request_twap_rejects_stop_orders() -> None:
    with pytest.raises(ValueError, match="TWAP execution not supported for order_type=stop"):
        OrderRequest(
            symbol="AAPL",
            side="buy",
            qty=100,
            order_type="stop",
            stop_price=Decimal("100"),
            execution_style="twap",
            twap_duration_minutes=10,
            twap_interval_seconds=60,
        )

    with pytest.raises(ValueError, match="TWAP execution not supported for order_type=stop_limit"):
        OrderRequest(
            symbol="AAPL",
            side="buy",
            qty=100,
            order_type="stop_limit",
            limit_price=Decimal("100"),
            stop_price=Decimal("99"),
            execution_style="twap",
            twap_duration_minutes=10,
            twap_interval_seconds=60,
        )


def test_order_request_twap_rejects_ioc_fok_time_in_force() -> None:
    with pytest.raises(ValueError, match="TWAP execution not supported for time_in_force=ioc"):
        OrderRequest(
            symbol="AAPL",
            side="buy",
            qty=100,
            order_type="market",
            time_in_force="ioc",
            execution_style="twap",
            twap_duration_minutes=10,
            twap_interval_seconds=60,
        )

    with pytest.raises(ValueError, match="TWAP execution not supported for time_in_force=fok"):
        OrderRequest(
            symbol="AAPL",
            side="buy",
            qty=100,
            order_type="market",
            time_in_force="fok",
            execution_style="twap",
            twap_duration_minutes=10,
            twap_interval_seconds=60,
        )


def test_order_request_twap_start_time_validation() -> None:
    past_time = datetime.now(UTC) - timedelta(minutes=2)
    with pytest.raises(ValueError, match="start_time cannot be in the past"):
        OrderRequest(
            symbol="AAPL",
            side="buy",
            qty=100,
            order_type="market",
            execution_style="twap",
            twap_duration_minutes=10,
            twap_interval_seconds=60,
            start_time=past_time,
        )

    future_time = datetime.now(UTC) + timedelta(days=6)
    with pytest.raises(ValueError, match="start_time cannot be more than 5 days in the future"):
        OrderRequest(
            symbol="AAPL",
            side="buy",
            qty=100,
            order_type="market",
            execution_style="twap",
            twap_duration_minutes=10,
            twap_interval_seconds=60,
            start_time=future_time,
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
