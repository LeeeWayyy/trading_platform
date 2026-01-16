"""Tests for order helper functions.

This test suite validates order submission and validation helpers extracted from main.py,
ensuring correct:
- Idempotency race condition handling
- Webhook timestamp parsing with fallbacks
- Fat-finger context resolution (price + ADV)
- Batch price fetching with N+1 query optimization
- Error handling and graceful degradation

Target: 90%+ coverage per Phase 1 requirements.

See REFACTOR_EXECUTION_GATEWAY_TASK.md Phase 1 for design decisions.
"""

import pytest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException
from redis.exceptions import RedisError

from apps.execution_gateway.services.order_helpers import (
    batch_fetch_realtime_prices_from_redis,
    create_fat_finger_thresholds_snapshot,
    handle_idempotency_race,
    parse_webhook_timestamp,
    resolve_fat_finger_context,
)


# ============================================================================
# Test handle_idempotency_race
# ============================================================================


def test_handle_idempotency_race_returns_existing_order() -> None:
    """Test that idempotency race returns existing order."""
    from apps.execution_gateway.schemas import OrderResponse

    db_client_mock = MagicMock()
    existing_order_mock = MagicMock()
    existing_order_mock.status = "filled"
    existing_order_mock.broker_order_id = "broker123"
    existing_order_mock.symbol = "AAPL"
    existing_order_mock.side = "buy"
    existing_order_mock.qty = Decimal("100")
    existing_order_mock.order_type = "market"
    existing_order_mock.limit_price = None
    existing_order_mock.created_at = datetime.now(UTC)

    db_client_mock.get_order_by_client_id.return_value = existing_order_mock

    response = handle_idempotency_race("client_order_123", db_client_mock)

    assert isinstance(response, OrderResponse)
    assert response.client_order_id == "client_order_123"
    assert response.status == "filled"
    assert response.message == "Order already submitted (race condition resolved)"


def test_handle_idempotency_race_raises_on_missing_order() -> None:
    """Test that idempotency race raises if order not found (defensive check)."""
    db_client_mock = MagicMock()
    db_client_mock.get_order_by_client_id.return_value = None

    with pytest.raises(HTTPException) as exc_info:
        handle_idempotency_race("client_order_123", db_client_mock)

    assert exc_info.value.status_code == 500
    assert "Database inconsistency" in exc_info.value.detail


# ============================================================================
# Test parse_webhook_timestamp
# ============================================================================


def test_parse_webhook_timestamp_datetime_passthrough() -> None:
    """Test that datetime objects are returned directly."""
    now = datetime.now(UTC)
    result = parse_webhook_timestamp(now, default=datetime(2020, 1, 1, tzinfo=UTC))

    assert result == now


def test_parse_webhook_timestamp_iso_string() -> None:
    """Test parsing ISO format string."""
    result = parse_webhook_timestamp(
        "2024-01-15T10:30:00Z",
        default=datetime(2020, 1, 1, tzinfo=UTC),
    )

    assert result.year == 2024
    assert result.month == 1
    assert result.day == 15
    assert result.hour == 10
    assert result.minute == 30


def test_parse_webhook_timestamp_with_offset() -> None:
    """Test parsing timestamp with timezone offset."""
    result = parse_webhook_timestamp(
        "2024-01-15T10:30:00+05:00",
        default=datetime(2020, 1, 1, tzinfo=UTC),
    )

    assert result.year == 2024


def test_parse_webhook_timestamp_skips_none() -> None:
    """Test that None values are skipped."""
    result = parse_webhook_timestamp(
        None,
        "2024-01-15T10:30:00Z",
        default=datetime(2020, 1, 1, tzinfo=UTC),
    )

    assert result.year == 2024


def test_parse_webhook_timestamp_skips_invalid() -> None:
    """Test that invalid timestamps are skipped."""
    result = parse_webhook_timestamp(
        "invalid",
        "2024-01-15T10:30:00Z",
        default=datetime(2020, 1, 1, tzinfo=UTC),
    )

    assert result.year == 2024


def test_parse_webhook_timestamp_returns_default() -> None:
    """Test that default is returned when all candidates fail."""
    default = datetime(2020, 1, 1, tzinfo=UTC)
    result = parse_webhook_timestamp(
        None,
        "invalid",
        123,  # Not a valid timestamp
        default=default,
    )

    assert result == default


# ============================================================================
# Test resolve_fat_finger_context
# ============================================================================


@pytest.mark.asyncio
async def test_resolve_fat_finger_context_uses_limit_price() -> None:
    """Test that limit_price is preferred for fat-finger validation."""
    from apps.execution_gateway.schemas import FatFingerThresholds, OrderRequest

    order = OrderRequest(
        symbol="AAPL",
        qty=Decimal("100"),
        side="buy",
        order_type="limit",
        limit_price=Decimal("150.00"),
    )
    thresholds = FatFingerThresholds(max_notional=Decimal("100000"))

    price, adv = await resolve_fat_finger_context(
        order, thresholds, None, None, 300
    )

    assert price == Decimal("150.00")
    assert adv is None


@pytest.mark.asyncio
async def test_resolve_fat_finger_context_uses_stop_price() -> None:
    """Test that stop_price is used when limit_price not available."""
    from apps.execution_gateway.schemas import FatFingerThresholds, OrderRequest

    order = OrderRequest(
        symbol="AAPL",
        qty=Decimal("100"),
        side="buy",
        order_type="stop",
        stop_price=Decimal("145.00"),
    )
    thresholds = FatFingerThresholds(max_notional=Decimal("100000"))

    price, adv = await resolve_fat_finger_context(
        order, thresholds, None, None, 300
    )

    assert price == Decimal("145.00")
    assert adv is None


@pytest.mark.asyncio
async def test_resolve_fat_finger_context_fetches_realtime_price() -> None:
    """Test that real-time price is fetched from Redis when order price unavailable."""
    from apps.execution_gateway.schemas import FatFingerThresholds, OrderRequest

    order = OrderRequest(
        symbol="AAPL",
        qty=Decimal("100"),
        side="buy",
        order_type="market",
    )
    thresholds = FatFingerThresholds(max_notional=Decimal("100000"))

    # Mock batch_fetch to return real-time price
    with patch("apps.execution_gateway.services.order_helpers.batch_fetch_realtime_prices_from_redis") as mock_batch:
        mock_batch.return_value = {
            "AAPL": (Decimal("152.50"), datetime.now(UTC))
        }

        price, adv = await resolve_fat_finger_context(
            order, thresholds, MagicMock(), None, 300
        )

        assert price == Decimal("152.50")
        assert adv is None


@pytest.mark.asyncio
async def test_resolve_fat_finger_context_rejects_stale_price() -> None:
    """Test that stale prices are rejected."""
    from apps.execution_gateway.schemas import FatFingerThresholds, OrderRequest

    order = OrderRequest(
        symbol="AAPL",
        qty=Decimal("100"),
        side="buy",
        order_type="market",
    )
    thresholds = FatFingerThresholds(max_notional=Decimal("100000"))

    # Mock batch_fetch with stale timestamp
    stale_timestamp = datetime.now(UTC) - timedelta(seconds=400)
    with patch("apps.execution_gateway.services.order_helpers.batch_fetch_realtime_prices_from_redis") as mock_batch:
        mock_batch.return_value = {
            "AAPL": (Decimal("152.50"), stale_timestamp)
        }

        price, adv = await resolve_fat_finger_context(
            order, thresholds, MagicMock(), None, 300  # Max age: 300 seconds
        )

        assert price is None  # Stale price rejected


@pytest.mark.asyncio
async def test_resolve_fat_finger_context_rejects_missing_timestamp() -> None:
    """Test that prices without timestamps are rejected."""
    from apps.execution_gateway.schemas import FatFingerThresholds, OrderRequest

    order = OrderRequest(
        symbol="AAPL",
        qty=Decimal("100"),
        side="buy",
        order_type="market",
    )
    thresholds = FatFingerThresholds(max_notional=Decimal("100000"))

    # Mock batch_fetch with missing timestamp
    with patch("apps.execution_gateway.services.order_helpers.batch_fetch_realtime_prices_from_redis") as mock_batch:
        mock_batch.return_value = {
            "AAPL": (Decimal("152.50"), None)  # Missing timestamp
        }

        price, adv = await resolve_fat_finger_context(
            order, thresholds, MagicMock(), None, 300
        )

        assert price is None  # Missing timestamp rejected


@pytest.mark.asyncio
async def test_resolve_fat_finger_context_fetches_adv() -> None:
    """Test that ADV is fetched from liquidity service."""
    from apps.execution_gateway.schemas import FatFingerThresholds, OrderRequest

    order = OrderRequest(
        symbol="AAPL",
        qty=Decimal("100"),
        side="buy",
        order_type="market",
        limit_price=Decimal("150.00"),
    )
    thresholds = FatFingerThresholds(
        max_notional=Decimal("100000"),
        max_adv_pct=Decimal("0.1"),
    )

    liquidity_service_mock = MagicMock()
    liquidity_service_mock.get_adv.return_value = 1000000

    price, adv = await resolve_fat_finger_context(
        order, thresholds, None, liquidity_service_mock, 300
    )

    assert price == Decimal("150.00")
    assert adv == 1000000


@pytest.mark.asyncio
async def test_resolve_fat_finger_context_no_validation_needed() -> None:
    """Test that (None, None) returned when no validation needed."""
    from apps.execution_gateway.schemas import FatFingerThresholds, OrderRequest

    order = OrderRequest(
        symbol="AAPL",
        qty=Decimal("100"),
        side="buy",
        order_type="market",
    )
    thresholds = FatFingerThresholds()  # No thresholds set

    price, adv = await resolve_fat_finger_context(
        order, thresholds, None, None, 300
    )

    assert price is None
    assert adv is None


# ============================================================================
# Test create_fat_finger_thresholds_snapshot
# ============================================================================


def test_create_fat_finger_thresholds_snapshot() -> None:
    """Test snapshot creation with validator."""
    from apps.execution_gateway.schemas import FatFingerThresholds

    validator_mock = MagicMock()
    validator_mock.get_default_thresholds.return_value = FatFingerThresholds(
        max_qty=10000,
        max_notional=Decimal("100000"),
    )
    validator_mock.get_symbol_overrides.return_value = {}

    snapshot = create_fat_finger_thresholds_snapshot(validator_mock)

    assert snapshot.default_thresholds.max_qty == 10000
    assert snapshot.symbol_overrides == {}
    assert isinstance(snapshot.updated_at, datetime)


# ============================================================================
# Test batch_fetch_realtime_prices_from_redis
# ============================================================================


def test_batch_fetch_returns_empty_when_no_redis() -> None:
    """Test that empty dict returned when Redis unavailable."""
    result = batch_fetch_realtime_prices_from_redis(["AAPL", "TSLA"], None)

    assert result == {"AAPL": (None, None), "TSLA": (None, None)}


def test_batch_fetch_returns_empty_when_no_symbols() -> None:
    """Test that empty dict returned for empty symbol list."""
    redis_mock = MagicMock()
    result = batch_fetch_realtime_prices_from_redis([], redis_mock)

    assert result == {}


def test_batch_fetch_successful() -> None:
    """Test successful batch fetch with valid data."""
    redis_mock = MagicMock()
    redis_mock.mget.return_value = [
        '{"mid": "150.25", "timestamp": "2024-01-15T10:30:00+00:00"}',
        '{"mid": "200.50", "timestamp": "2024-01-15T10:30:05+00:00"}',
    ]

    result = batch_fetch_realtime_prices_from_redis(["AAPL", "TSLA"], redis_mock)

    assert result["AAPL"][0] == Decimal("150.25")
    assert result["TSLA"][0] == Decimal("200.50")
    assert isinstance(result["AAPL"][1], datetime)


def test_batch_fetch_handles_missing_symbols() -> None:
    """Test that missing symbols return (None, None)."""
    redis_mock = MagicMock()
    redis_mock.mget.return_value = [
        '{"mid": "150.25", "timestamp": "2024-01-15T10:30:00+00:00"}',
        None,  # TSLA not in cache
    ]

    result = batch_fetch_realtime_prices_from_redis(["AAPL", "TSLA"], redis_mock)

    assert result["AAPL"][0] == Decimal("150.25")
    assert result["TSLA"] == (None, None)


def test_batch_fetch_handles_invalid_json() -> None:
    """Test graceful handling of invalid JSON."""
    redis_mock = MagicMock()
    redis_mock.mget.return_value = [
        "invalid json",
        '{"mid": "200.50", "timestamp": "2024-01-15T10:30:00+00:00"}',
    ]

    result = batch_fetch_realtime_prices_from_redis(["AAPL", "TSLA"], redis_mock)

    assert result["AAPL"] == (None, None)  # Invalid JSON
    assert result["TSLA"][0] == Decimal("200.50")


def test_batch_fetch_handles_missing_fields() -> None:
    """Test graceful handling of missing required fields."""
    redis_mock = MagicMock()
    redis_mock.mget.return_value = [
        '{"timestamp": "2024-01-15T10:30:00+00:00"}',  # Missing "mid"
        '{"mid": "200.50"}',  # Missing "timestamp"
    ]

    result = batch_fetch_realtime_prices_from_redis(["AAPL", "TSLA"], redis_mock)

    assert result["AAPL"] == (None, None)  # Missing field
    assert result["TSLA"] == (None, None)  # Missing field


def test_batch_fetch_handles_redis_error() -> None:
    """Test graceful degradation when Redis errors."""
    redis_mock = MagicMock()
    redis_mock.mget.side_effect = RedisError("Connection failed")

    result = batch_fetch_realtime_prices_from_redis(["AAPL", "TSLA"], redis_mock)

    assert result == {"AAPL": (None, None), "TSLA": (None, None)}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
