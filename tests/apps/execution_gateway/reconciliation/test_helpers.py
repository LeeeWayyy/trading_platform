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


class TestCalculateSyntheticFillEdgeCases:
    """Additional edge case tests for calculate_synthetic_fill function."""

    def test_real_fills_exceed_broker_quantity(self) -> None:
        """When real fills exceed broker qty, no synthetic needed."""
        existing_fills = [
            {"fill_qty": 60, "synthetic": False},
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

    def test_multiple_synthetic_fills_with_gap(self) -> None:
        """Multiple synthetic fills with remaining gap."""
        existing_fills = [
            {"fill_qty": 20, "synthetic": False},
            {"fill_qty": 30, "synthetic": True},
            {"fill_qty": 20, "synthetic": True},
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
        assert result["fill_qty"] == 30  # 100 - 20 real - 30 synthetic - 20 synthetic

    def test_zero_filled_qty_returns_none(self) -> None:
        """Zero filled quantity should not create synthetic fill."""
        result = calculate_synthetic_fill(
            client_order_id="order_123",
            filled_qty=Decimal("0"),
            filled_avg_price=Decimal("50.00"),
            timestamp=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
            existing_fills=[],
            source="recon",
        )
        assert result is None

    def test_negative_missing_qty_returns_none(self) -> None:
        """Negative missing quantity (over-filled) returns None."""
        existing_fills = [
            {"fill_qty": 60, "synthetic": False},
            {"fill_qty": 50, "synthetic": True},
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

    def test_mixed_superseded_and_active_fills(self) -> None:
        """Mix of superseded and active fills."""
        existing_fills = [
            {"fill_qty": 30, "synthetic": False},
            {"fill_qty": 40, "synthetic": True, "superseded": True},
            {"fill_qty": 20, "synthetic": True},
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
        assert result["fill_qty"] == 50  # 100 - 30 real - 20 synthetic

    def test_fill_qty_missing_defaults_to_zero(self) -> None:
        """Missing fill_qty field defaults to zero."""
        existing_fills = [
            {},  # No fill_qty field
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
        assert result["fill_qty"] == 50

    def test_very_small_fractional_quantity(self) -> None:
        """Very small fractional quantities preserved."""
        result = calculate_synthetic_fill(
            client_order_id="order_123",
            filled_qty=Decimal("0.001"),
            filled_avg_price=Decimal("1000.00"),
            timestamp=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
            existing_fills=[],
            source="recon",
        )
        assert result is not None
        assert result["fill_qty"] == "0.001"

    def test_large_decimal_price(self) -> None:
        """Large decimal prices handled correctly."""
        result = calculate_synthetic_fill(
            client_order_id="order_123",
            filled_qty=Decimal("100"),
            filled_avg_price=Decimal("99999.999999"),
            timestamp=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
            existing_fills=[],
            source="recon",
        )
        assert result is not None
        assert result["fill_price"] == "99999.999999"

    def test_realized_pl_always_zero(self) -> None:
        """Synthetic fills always have zero realized P&L."""
        result = calculate_synthetic_fill(
            client_order_id="order_123",
            filled_qty=Decimal("100"),
            filled_avg_price=Decimal("50.00"),
            timestamp=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
            existing_fills=[],
            source="recon",
        )
        assert result is not None
        assert result["realized_pl"] == "0"

    def test_fill_id_includes_both_filled_and_missing_qty(self) -> None:
        """Fill ID includes both filled_qty and missing_qty for uniqueness."""
        existing_fills = [{"fill_qty": 40, "synthetic": False}]
        result = calculate_synthetic_fill(
            client_order_id="order_123",
            filled_qty=Decimal("100"),
            filled_avg_price=Decimal("50.00"),
            timestamp=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
            existing_fills=existing_fills,
            source="recon",
        )
        assert result is not None
        # Should contain filled_qty (100) and missing_qty (60)
        assert "100" in result["fill_id"]
        assert "60" in result["fill_id"]
        assert "order_123" in result["fill_id"]
        assert "recon" in result["fill_id"]

    def test_arithmetic_error_in_existing_fills(self) -> None:
        """ArithmeticError (InvalidOperation) in existing fills is handled."""
        existing_fills = [
            {"fill_qty": "not_a_number", "synthetic": False},  # Will cause Decimal error
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
        assert result["fill_qty"] == 50  # Only valid fill counted


class TestEstimateNotionalEdgeCases:
    """Additional edge case tests for estimate_notional function."""

    def test_negative_qty_with_limit_price(self) -> None:
        """Negative quantity (short position) handled correctly."""
        result = estimate_notional({"qty": "-100", "limit_price": "50.00"})
        assert result == Decimal("-5000.00")

    def test_zero_notional_field(self) -> None:
        """Explicit zero notional field."""
        result = estimate_notional({"notional": "0"})
        assert result == Decimal("0")

    def test_none_qty_defaults_to_zero(self) -> None:
        """None qty value defaults to zero."""
        result = estimate_notional({"qty": None, "limit_price": "50.00"})
        assert result == Decimal("0")

    def test_missing_qty_with_limit_price(self) -> None:
        """Missing qty field defaults to zero."""
        result = estimate_notional({"limit_price": "50.00"})
        assert result == Decimal("0")

    def test_very_large_notional(self) -> None:
        """Very large notional values handled correctly."""
        result = estimate_notional({"qty": "1000000", "limit_price": "999999.99"})
        assert result == Decimal("999999990000.00")

    def test_fractional_share_notional(self) -> None:
        """Fractional shares with fractional price."""
        result = estimate_notional({"qty": "10.5", "limit_price": "123.456"})
        assert result == Decimal("1296.288")

    def test_zero_limit_price(self) -> None:
        """Zero limit price results in zero notional."""
        result = estimate_notional({"qty": "100", "limit_price": "0"})
        assert result == Decimal("0")

    def test_negative_price(self) -> None:
        """Negative prices handled (edge case for options)."""
        result = estimate_notional({"qty": "100", "limit_price": "-5.00"})
        assert result == Decimal("-500.00")


class TestGenerateFillIdFromActivityEdgeCases:
    """Additional edge case tests for generate_fill_id_from_activity function."""

    def test_all_fields_present(self) -> None:
        """Generate ID with all fields present."""
        fill = {
            "order_id": "broker_123",
            "symbol": "AAPL",
            "side": "buy",
            "qty": "100",
            "price": "150.00",
            "transaction_time": "2024-01-01T12:00:00Z",
            "activity_time": "2024-01-01T12:00:01Z",
            "id": "hint_456",
        }
        result = generate_fill_id_from_activity(fill)
        assert len(result) == 32
        assert result.isalnum()

    def test_special_characters_in_fields(self) -> None:
        """Special characters in fields don't cause errors."""
        fill = {
            "order_id": "order@123#special",
            "symbol": "TEST.A",
            "qty": "10.5",
            "price": "$150.00",
        }
        result = generate_fill_id_from_activity(fill)
        assert len(result) == 32

    def test_unicode_characters(self) -> None:
        """Unicode characters handled correctly."""
        fill = {
            "order_id": "订单123",
            "symbol": "特斯拉",
        }
        result = generate_fill_id_from_activity(fill)
        assert len(result) == 32

    def test_very_long_field_values(self) -> None:
        """Very long field values don't cause issues."""
        fill = {
            "order_id": "x" * 1000,
            "symbol": "y" * 500,
            "price": "z" * 200,
        }
        result = generate_fill_id_from_activity(fill)
        assert len(result) == 32

    def test_empty_string_values(self) -> None:
        """Empty string values handled gracefully."""
        fill = {
            "order_id": "",
            "symbol": "",
            "qty": "",
        }
        result = generate_fill_id_from_activity(fill)
        assert len(result) == 32

    def test_whitespace_values(self) -> None:
        """Whitespace-only values handled correctly."""
        fill = {
            "order_id": "   ",
            "symbol": "\t\n",
            "qty": "  100  ",
        }
        result = generate_fill_id_from_activity(fill)
        assert len(result) == 32

    def test_numeric_types_not_strings(self) -> None:
        """Numeric types converted to strings correctly."""
        fill = {
            "order_id": 12345,
            "qty": 100,
            "price": 150.50,
        }
        result = generate_fill_id_from_activity(fill)
        assert len(result) == 32

    def test_boolean_values(self) -> None:
        """Boolean values converted to strings."""
        fill = {
            "order_id": True,
            "symbol": False,
        }
        result = generate_fill_id_from_activity(fill)
        assert len(result) == 32


class TestMergeBrokerOrdersEdgeCases:
    """Additional edge case tests for merge_broker_orders function."""

    def test_duplicate_orders_in_same_list(self) -> None:
        """Duplicate orders in open_orders list (later wins)."""
        open_orders = [
            {"client_order_id": "abc", "updated_at": "2024-01-01T10:00:00Z", "qty": 100},
            {"client_order_id": "abc", "updated_at": "2024-01-01T11:00:00Z", "qty": 200},
        ]
        result = merge_broker_orders(open_orders, [])
        assert result["abc"]["qty"] == 200
        assert result["abc"]["updated_at"] == "2024-01-01T11:00:00Z"

    def test_many_orders_same_client_id(self) -> None:
        """Many orders with same client_id (keeps newest)."""
        orders = [
            {"client_order_id": "abc", "updated_at": f"2024-01-01T{i:02d}:00:00Z"}
            for i in range(24)
        ]
        result = merge_broker_orders(orders, [])
        assert result["abc"]["updated_at"] == "2024-01-01T23:00:00Z"

    def test_mixed_timestamp_types(self) -> None:
        """Orders with updated_at vs created_at timestamps."""
        open_orders = [
            {"client_order_id": "abc", "created_at": "2024-01-01T10:00:00Z"},
        ]
        recent_orders = [
            {"client_order_id": "abc", "updated_at": "2024-01-01T11:00:00Z"},
        ]
        # Newer timestamp wins regardless of field name
        result = merge_broker_orders(open_orders, recent_orders)
        assert "updated_at" in result["abc"]
        assert result["abc"]["updated_at"] == "2024-01-01T11:00:00Z"

    def test_empty_client_order_id_string(self) -> None:
        """Empty string client_order_id is skipped."""
        open_orders = [
            {"client_order_id": "", "status": "open"},
            {"client_order_id": "valid", "status": "filled"},
        ]
        result = merge_broker_orders(open_orders, [])
        assert len(result) == 1
        assert "valid" in result

    def test_large_number_of_unique_orders(self) -> None:
        """Handle large number of unique orders efficiently."""
        open_orders = [
            {"client_order_id": f"order_{i}", "status": "open"}
            for i in range(1000)
        ]
        recent_orders = [
            {"client_order_id": f"recent_{i}", "status": "filled"}
            for i in range(1000)
        ]
        result = merge_broker_orders(open_orders, recent_orders)
        assert len(result) == 2000

    def test_timestamp_equal_keeps_existing(self) -> None:
        """When timestamps are equal, keep existing order."""
        open_orders = [{"client_order_id": "abc", "updated_at": "2024-01-01T10:00:00Z", "field": "first"}]
        recent_orders = [{"client_order_id": "abc", "updated_at": "2024-01-01T10:00:00Z", "field": "second"}]
        result = merge_broker_orders(open_orders, recent_orders)
        # Equal timestamps: neither is "newer", so first stays
        assert result["abc"]["field"] == "first"

    def test_none_client_order_id_explicit(self) -> None:
        """Explicit None client_order_id is skipped."""
        orders = [
            {"client_order_id": None, "status": "test"},
            {"other": "data"},
        ]
        result = merge_broker_orders(orders, [])
        assert len(result) == 0


class TestExtractBrokerClientIdsEdgeCases:
    """Additional edge case tests for extract_broker_client_ids function."""

    def test_mixed_types_in_client_order_id(self) -> None:
        """Non-None values of various types are included."""
        orders = [
            {"client_order_id": "string_id"},
            {"client_order_id": 12345},  # int
            {"client_order_id": 0},  # zero int
            {"client_order_id": ""},  # empty string
            {"client_order_id": None},  # None (should be excluded)
        ]
        result = extract_broker_client_ids(orders)
        assert len(result) == 4
        assert "string_id" in result
        assert 12345 in result
        assert 0 in result
        assert "" in result
        assert None not in result

    def test_duplicate_client_ids(self) -> None:
        """Duplicate client_order_ids are preserved."""
        orders = [
            {"client_order_id": "abc"},
            {"client_order_id": "abc"},
            {"client_order_id": "def"},
        ]
        result = extract_broker_client_ids(orders)
        assert result == ["abc", "abc", "def"]

    def test_orders_with_extra_fields(self) -> None:
        """Extract works with orders containing many fields."""
        orders = [
            {
                "client_order_id": "abc",
                "broker_order_id": "xyz",
                "status": "filled",
                "qty": 100,
                "price": 50.0,
                "symbol": "AAPL",
            }
        ]
        result = extract_broker_client_ids(orders)
        assert result == ["abc"]

    def test_very_large_list(self) -> None:
        """Handle very large order lists efficiently."""
        orders = [
            {"client_order_id": f"order_{i}"}
            for i in range(10000)
        ]
        result = extract_broker_client_ids(orders)
        assert len(result) == 10000
        assert result[0] == "order_0"
        assert result[9999] == "order_9999"
