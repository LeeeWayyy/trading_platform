"""Tests for reconciliation pure helper functions.

These tests cover the pure functions in helpers.py with table-driven
test cases for comprehensive coverage. Target: 95%+
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from apps.execution_gateway.reconciliation.helpers import (
    calculate_synthetic_fill,
    estimate_notional,
    extract_broker_client_ids,
    generate_fill_id_from_activity,
    merge_broker_orders,
)


class TestCalculateSyntheticFill:
    """Tests for calculate_synthetic_fill function."""

    def test_no_existing_fills_creates_synthetic(self) -> None:
        """When no fills exist, create synthetic fill for full quantity."""
        result = calculate_synthetic_fill(
            client_order_id="order_123",
            filled_qty=Decimal("100"),
            filled_avg_price=Decimal("50.00"),
            timestamp=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
            existing_fills=[],
            source="recon",
        )
        assert result is not None
        assert result["fill_qty"] == 100
        assert result["fill_price"] == "50.00"
        assert result["synthetic"] is True
        assert result["source"] == "recon"
        assert "order_123" in result["fill_id"]
        assert result["_missing_qty"] == Decimal("100")

    def test_existing_fills_cover_quantity_returns_none(self) -> None:
        """When existing fills cover broker qty, no synthetic needed."""
        existing_fills = [
            {"fill_qty": 50, "synthetic": False},
            {"fill_qty": 50, "synthetic": False},
        ]
        result = calculate_synthetic_fill(
            client_order_id="order_123",
            filled_qty=Decimal("100"),
            filled_avg_price=Decimal("50.00"),
            timestamp=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
            existing_fills=existing_fills,
            source="recon",
        )
        assert result is None

    def test_partial_fills_creates_gap_fill(self) -> None:
        """When existing fills are partial, create synthetic for gap."""
        existing_fills = [
            {"fill_qty": 30, "synthetic": False},
            {"fill_qty": 20, "synthetic": False},
        ]
        result = calculate_synthetic_fill(
            client_order_id="order_123",
            filled_qty=Decimal("100"),
            filled_avg_price=Decimal("50.00"),
            timestamp=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
            existing_fills=existing_fills,
            source="recon",
        )
        assert result is not None
        assert result["fill_qty"] == 50
        assert result["_missing_qty"] == Decimal("50")

    def test_synthetic_fills_counted_separately(self) -> None:
        """Synthetic fills are counted but real fills take priority."""
        existing_fills = [
            {"fill_qty": 30, "synthetic": False},
            {"fill_qty": 70, "synthetic": True},  # Previous synthetic
        ]
        result = calculate_synthetic_fill(
            client_order_id="order_123",
            filled_qty=Decimal("100"),
            filled_avg_price=Decimal("50.00"),
            timestamp=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
            existing_fills=existing_fills,
            source="recon",
        )
        # Total is 30+70=100, so no gap
        assert result is None

    def test_superseded_fills_skipped(self) -> None:
        """Superseded fills should be skipped in calculation."""
        existing_fills = [
            {"fill_qty": 50, "synthetic": False},
            {"fill_qty": 50, "synthetic": True, "superseded": True},  # Superseded
        ]
        result = calculate_synthetic_fill(
            client_order_id="order_123",
            filled_qty=Decimal("100"),
            filled_avg_price=Decimal("50.00"),
            timestamp=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
            existing_fills=existing_fills,
            source="recon",
        )
        assert result is not None
        assert result["fill_qty"] == 50  # Missing 50 because superseded was skipped

    def test_fractional_shares_preserved_as_string(self) -> None:
        """Fractional share quantities are stored as strings."""
        result = calculate_synthetic_fill(
            client_order_id="order_123",
            filled_qty=Decimal("10.5"),
            filled_avg_price=Decimal("150.00"),
            timestamp=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
            existing_fills=[],
            source="recon",
        )
        assert result is not None
        assert result["fill_qty"] == "10.5"

    def test_whole_numbers_stored_as_int(self) -> None:
        """Whole number quantities are stored as integers."""
        result = calculate_synthetic_fill(
            client_order_id="order_123",
            filled_qty=Decimal("100"),
            filled_avg_price=Decimal("50.00"),
            timestamp=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
            existing_fills=[],
            source="recon",
        )
        assert result is not None
        assert isinstance(result["fill_qty"], int)

    def test_invalid_fill_qty_skipped(self) -> None:
        """Invalid fill quantities in existing fills are skipped."""
        existing_fills = [
            {"fill_qty": "invalid", "synthetic": False},
            {"fill_qty": None, "synthetic": False},
            {"fill_qty": 50, "synthetic": False},
        ]
        result = calculate_synthetic_fill(
            client_order_id="order_123",
            filled_qty=Decimal("100"),
            filled_avg_price=Decimal("50.00"),
            timestamp=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
            existing_fills=existing_fills,
            source="recon",
        )
        assert result is not None
        assert result["fill_qty"] == 50  # Only 50 was valid

    def test_timestamp_formatted_as_iso(self) -> None:
        """Timestamp is formatted as ISO string."""
        ts = datetime(2024, 1, 15, 14, 30, 45, tzinfo=UTC)
        result = calculate_synthetic_fill(
            client_order_id="order_123",
            filled_qty=Decimal("10"),
            filled_avg_price=Decimal("50.00"),
            timestamp=ts,
            existing_fills=[],
            source="recon",
        )
        assert result is not None
        assert result["timestamp"] == "2024-01-15T14:30:45+00:00"

    def test_different_sources_produce_different_fill_ids(self) -> None:
        """Different source values produce different fill IDs."""
        ts = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
        result1 = calculate_synthetic_fill(
            client_order_id="order_123",
            filled_qty=Decimal("100"),
            filled_avg_price=Decimal("50.00"),
            timestamp=ts,
            existing_fills=[],
            source="recon",
        )
        result2 = calculate_synthetic_fill(
            client_order_id="order_123",
            filled_qty=Decimal("100"),
            filled_avg_price=Decimal("50.00"),
            timestamp=ts,
            existing_fills=[],
            source="recon_db",
        )
        assert result1 is not None
        assert result2 is not None
        assert result1["fill_id"] != result2["fill_id"]


class TestEstimateNotional:
    """Tests for estimate_notional function."""

    def test_notional_field_present(self) -> None:
        """When notional is present, use it directly."""
        result = estimate_notional({"notional": "5000.00"})
        assert result == Decimal("5000.00")

    def test_limit_price_calculation(self) -> None:
        """Calculate notional from qty * limit_price."""
        result = estimate_notional({"qty": "100", "limit_price": "50.00"})
        assert result == Decimal("5000.00")

    def test_filled_avg_price_calculation(self) -> None:
        """Calculate notional from qty * filled_avg_price."""
        result = estimate_notional({"qty": "100", "filled_avg_price": "52.50"})
        assert result == Decimal("5250.00")

    def test_priority_notional_over_limit_price(self) -> None:
        """Notional takes priority over limit_price."""
        result = estimate_notional({
            "notional": "5000.00",
            "qty": "100",
            "limit_price": "60.00",
        })
        assert result == Decimal("5000.00")

    def test_priority_limit_price_over_filled_avg(self) -> None:
        """Limit price takes priority over filled_avg_price."""
        result = estimate_notional({
            "qty": "100",
            "limit_price": "50.00",
            "filled_avg_price": "52.00",
        })
        assert result == Decimal("5000.00")

    def test_fallback_to_zero(self) -> None:
        """Return zero when no price data available."""
        result = estimate_notional({"qty": "100"})
        assert result == Decimal("0")

    def test_empty_order(self) -> None:
        """Return zero for empty order dict."""
        result = estimate_notional({})
        assert result == Decimal("0")

    def test_zero_qty(self) -> None:
        """Return zero when qty is zero."""
        result = estimate_notional({"qty": "0", "limit_price": "50.00"})
        assert result == Decimal("0")


class TestGenerateFillIdFromActivity:
    """Tests for generate_fill_id_from_activity function."""

    def test_deterministic_generation(self) -> None:
        """Same input produces same output."""
        fill = {
            "order_id": "abc123",
            "symbol": "AAPL",
            "side": "buy",
            "qty": "100",
            "price": "150.00",
            "transaction_time": "2024-01-01T12:00:00Z",
        }
        result1 = generate_fill_id_from_activity(fill)
        result2 = generate_fill_id_from_activity(fill)
        assert result1 == result2

    def test_different_fills_produce_different_ids(self) -> None:
        """Different fills produce different IDs."""
        fill1 = {"order_id": "abc123", "symbol": "AAPL", "qty": "100"}
        fill2 = {"order_id": "abc123", "symbol": "AAPL", "qty": "200"}
        assert generate_fill_id_from_activity(fill1) != generate_fill_id_from_activity(fill2)

    def test_32_character_length(self) -> None:
        """Generated ID is 32 characters."""
        fill = {"order_id": "abc123"}
        result = generate_fill_id_from_activity(fill)
        assert len(result) == 32

    def test_handles_missing_fields(self) -> None:
        """Handles missing fields gracefully."""
        fill = {}
        result = generate_fill_id_from_activity(fill)
        assert len(result) == 32

    def test_handles_none_values(self) -> None:
        """Handles None values gracefully."""
        fill = {"order_id": None, "symbol": None}
        result = generate_fill_id_from_activity(fill)
        assert len(result) == 32


class TestMergeBrokerOrders:
    """Tests for merge_broker_orders function."""

    def test_merge_open_and_recent_orders(self) -> None:
        """Merge open and recent orders by client_order_id."""
        open_orders = [{"client_order_id": "abc", "updated_at": "2024-01-01T10:00:00Z"}]
        recent_orders = [{"client_order_id": "def", "updated_at": "2024-01-01T11:00:00Z"}]
        result = merge_broker_orders(open_orders, recent_orders)
        assert "abc" in result
        assert "def" in result
        assert len(result) == 2

    def test_prefer_newer_updated_at(self) -> None:
        """When same order in both lists, prefer newer updated_at."""
        open_orders = [{"client_order_id": "abc", "updated_at": "2024-01-01T10:00:00Z"}]
        recent_orders = [{"client_order_id": "abc", "updated_at": "2024-01-01T12:00:00Z"}]
        result = merge_broker_orders(open_orders, recent_orders)
        assert result["abc"]["updated_at"] == "2024-01-01T12:00:00Z"

    def test_fallback_to_created_at(self) -> None:
        """Use created_at when updated_at is missing."""
        open_orders = [{"client_order_id": "abc", "created_at": "2024-01-01T10:00:00Z"}]
        recent_orders = [{"client_order_id": "abc", "created_at": "2024-01-01T12:00:00Z"}]
        result = merge_broker_orders(open_orders, recent_orders)
        assert result["abc"]["created_at"] == "2024-01-01T12:00:00Z"

    def test_skip_orders_without_client_id(self) -> None:
        """Skip orders without client_order_id."""
        open_orders = [
            {"client_order_id": "abc", "status": "open"},
            {"status": "open"},  # No client_order_id
        ]
        result = merge_broker_orders(open_orders, [])
        assert len(result) == 1
        assert "abc" in result

    def test_empty_lists(self) -> None:
        """Handle empty lists."""
        result = merge_broker_orders([], [])
        assert result == {}

    def test_prefer_order_with_timestamp_over_none(self) -> None:
        """Prefer order with timestamp over order without timestamp."""
        open_orders = [{"client_order_id": "abc", "status": "open"}]  # No timestamp
        recent_orders = [{"client_order_id": "abc", "updated_at": "2024-01-01T12:00:00Z"}]
        result = merge_broker_orders(open_orders, recent_orders)
        assert result["abc"]["updated_at"] == "2024-01-01T12:00:00Z"

    def test_keep_existing_when_both_lack_timestamps(self) -> None:
        """Keep existing order when neither has timestamps."""
        open_orders = [{"client_order_id": "abc", "status": "open"}]  # No timestamp
        recent_orders = [{"client_order_id": "abc", "status": "filled"}]  # No timestamp
        result = merge_broker_orders(open_orders, recent_orders)
        # First one wins when neither has timestamps
        assert result["abc"]["status"] == "open"


class TestExtractBrokerClientIds:
    """Tests for extract_broker_client_ids function."""

    def test_extract_all_client_ids(self) -> None:
        """Extract all client_order_ids from orders."""
        orders = [
            {"client_order_id": "abc"},
            {"client_order_id": "def"},
            {"client_order_id": "ghi"},
        ]
        result = extract_broker_client_ids(orders)
        assert result == ["abc", "def", "ghi"]

    def test_skip_none_client_ids(self) -> None:
        """Skip orders with None client_order_id."""
        orders = [
            {"client_order_id": "abc"},
            {"client_order_id": None},
            {"other_field": "value"},
        ]
        result = extract_broker_client_ids(orders)
        assert result == ["abc"]

    def test_empty_list(self) -> None:
        """Handle empty list."""
        result = extract_broker_client_ids([])
        assert result == []

    def test_preserves_order(self) -> None:
        """Preserve order of extraction."""
        orders = [
            {"client_order_id": "third"},
            {"client_order_id": "first"},
            {"client_order_id": "second"},
        ]
        result = extract_broker_client_ids(orders)
        assert result == ["third", "first", "second"]
