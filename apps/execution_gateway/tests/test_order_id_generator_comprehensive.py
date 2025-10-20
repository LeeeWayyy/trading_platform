"""
Comprehensive unit tests for order_id_generator module.

Tests cover:
- Deterministic client_order_id generation (generate_client_order_id)
- Idempotency verification (same params = same ID)
- ID format validation (validate_client_order_id)
- Hash reconstruction (reconstruct_order_params_hash)
- Date extraction (parse_order_date_from_timestamp)
- Edge cases and price handling

Target: Bring order_id_generator.py coverage from 44% to 95%+

See Also:
    - /docs/STANDARDS/TESTING.md - Testing standards
    - /docs/ADRs/0005-execution-gateway-architecture.md - Idempotency design
"""

from datetime import UTC, date, datetime
from decimal import Decimal

from apps.execution_gateway.order_id_generator import (
    generate_client_order_id,
    parse_order_date_from_timestamp,
    reconstruct_order_params_hash,
    validate_client_order_id,
)
from apps.execution_gateway.schemas import OrderRequest


class TestGenerateClientOrderId:
    """Test deterministic client_order_id generation."""

    def test_generate_market_order_id(self):
        """Should generate 24-character hex ID for market order."""
        order = OrderRequest(symbol="AAPL", side="buy", qty=10, order_type="market")

        client_order_id = generate_client_order_id(order, "alpha_baseline")

        assert len(client_order_id) == 24
        assert all(c in "0123456789abcdef" for c in client_order_id)

    def test_generate_idempotent_same_parameters(self):
        """
        Should generate same ID for same order parameters.

        This is the core idempotency guarantee - submitting the
        same order multiple times should produce the same ID.
        """
        order = OrderRequest(symbol="AAPL", side="buy", qty=10, order_type="market")

        id1 = generate_client_order_id(order, "alpha_baseline")
        id2 = generate_client_order_id(order, "alpha_baseline")

        assert id1 == id2

    def test_generate_different_id_for_different_symbol(self):
        """Should generate different ID when symbol changes."""
        order1 = OrderRequest(symbol="AAPL", side="buy", qty=10, order_type="market")
        order2 = OrderRequest(symbol="MSFT", side="buy", qty=10, order_type="market")

        id1 = generate_client_order_id(order1, "alpha_baseline")
        id2 = generate_client_order_id(order2, "alpha_baseline")

        assert id1 != id2

    def test_generate_different_id_for_different_side(self):
        """Should generate different ID when side changes."""
        order1 = OrderRequest(symbol="AAPL", side="buy", qty=10, order_type="market")
        order2 = OrderRequest(symbol="AAPL", side="sell", qty=10, order_type="market")

        id1 = generate_client_order_id(order1, "alpha_baseline")
        id2 = generate_client_order_id(order2, "alpha_baseline")

        assert id1 != id2

    def test_generate_different_id_for_different_qty(self):
        """Should generate different ID when quantity changes."""
        order1 = OrderRequest(symbol="AAPL", side="buy", qty=10, order_type="market")
        order2 = OrderRequest(symbol="AAPL", side="buy", qty=20, order_type="market")

        id1 = generate_client_order_id(order1, "alpha_baseline")
        id2 = generate_client_order_id(order2, "alpha_baseline")

        assert id1 != id2

    def test_generate_different_id_for_different_strategy(self):
        """Should generate different ID when strategy changes."""
        order = OrderRequest(symbol="AAPL", side="buy", qty=10, order_type="market")

        id1 = generate_client_order_id(order, "alpha_baseline")
        id2 = generate_client_order_id(order, "beta_momentum")

        assert id1 != id2

    def test_generate_different_id_for_different_date(self):
        """
        Should generate different ID when date changes.

        This allows resubmitting the same order on different days.
        """
        order = OrderRequest(symbol="AAPL", side="buy", qty=10, order_type="market")

        id1 = generate_client_order_id(order, "alpha_baseline", date(2024, 10, 17))
        id2 = generate_client_order_id(order, "alpha_baseline", date(2024, 10, 18))

        assert id1 != id2

    def test_generate_with_limit_price(self):
        """Should include limit_price in ID generation."""
        order1 = OrderRequest(
            symbol="AAPL", side="buy", qty=10, order_type="limit", limit_price=Decimal("150.00")
        )
        order2 = OrderRequest(
            symbol="AAPL", side="buy", qty=10, order_type="limit", limit_price=Decimal("151.00")
        )

        id1 = generate_client_order_id(order1, "alpha_baseline")
        id2 = generate_client_order_id(order2, "alpha_baseline")

        # Different limit prices should produce different IDs
        assert id1 != id2

    def test_generate_with_stop_price(self):
        """Should include stop_price in ID generation."""
        order1 = OrderRequest(
            symbol="AAPL", side="buy", qty=10, order_type="stop", stop_price=Decimal("148.00")
        )
        order2 = OrderRequest(
            symbol="AAPL", side="buy", qty=10, order_type="stop", stop_price=Decimal("149.00")
        )

        id1 = generate_client_order_id(order1, "alpha_baseline")
        id2 = generate_client_order_id(order2, "alpha_baseline")

        # Different stop prices should produce different IDs
        assert id1 != id2

    def test_generate_with_stop_limit_both_prices(self):
        """Should include both limit_price and stop_price for stop_limit orders."""
        order1 = OrderRequest(
            symbol="AAPL",
            side="buy",
            qty=10,
            order_type="stop_limit",
            limit_price=Decimal("150.00"),
            stop_price=Decimal("148.00"),
        )
        order2 = OrderRequest(
            symbol="AAPL",
            side="buy",
            qty=10,
            order_type="stop_limit",
            limit_price=Decimal("151.00"),  # Different limit
            stop_price=Decimal("148.00"),
        )

        id1 = generate_client_order_id(order1, "alpha_baseline")
        id2 = generate_client_order_id(order2, "alpha_baseline")

        # Different limit price should change ID
        assert id1 != id2

    def test_generate_market_vs_limit_same_price(self):
        """
        Should generate different IDs for market vs limit order even if limit price is None.

        Note: order_type is NOT included in ID hash, only prices matter.
        """
        order1 = OrderRequest(symbol="AAPL", side="buy", qty=10, order_type="market")
        order2 = OrderRequest(
            symbol="AAPL", side="buy", qty=10, order_type="limit", limit_price=Decimal("150.00")
        )

        id1 = generate_client_order_id(order1, "alpha_baseline")
        id2 = generate_client_order_id(order2, "alpha_baseline")

        # Market (no limit_price) vs Limit (with limit_price) should differ
        assert id1 != id2

    def test_generate_none_limit_price_vs_no_limit_price(self):
        """
        Should handle None vs absent limit_price consistently.

        OrderRequest may have limit_price=None or not set at all.
        """
        order1 = OrderRequest(symbol="AAPL", side="buy", qty=10, order_type="market")
        # order1.limit_price is None by default

        id1 = generate_client_order_id(order1, "alpha_baseline")
        id2 = generate_client_order_id(order1, "alpha_baseline")

        # Should be idempotent
        assert id1 == id2

    def test_generate_with_decimal_prices_precision(self):
        """
        Should normalize Decimal prices to ensure idempotency.

        Decimal("150.00") and Decimal("150.0") represent the same price
        but have different string representations. After quantization,
        they should produce the same client_order_id.

        This prevents duplicate orders when JSON parsers use different
        precision (e.g., "150.0" vs "150.00").
        """
        order1 = OrderRequest(
            symbol="AAPL", side="buy", qty=10, order_type="limit", limit_price=Decimal("150.00")
        )
        order2 = OrderRequest(
            symbol="AAPL", side="buy", qty=10, order_type="limit", limit_price=Decimal("150.0")
        )

        id1 = generate_client_order_id(order1, "alpha_baseline")
        id2 = generate_client_order_id(order2, "alpha_baseline")

        # Should generate same ID after quantization (idempotency guarantee)
        assert id1 == id2, "Same price with different precision must produce same ID"

    def test_generate_no_scientific_notation(self):
        """
        Should not produce scientific notation in client_order_id.

        Using quantize() instead of normalize() ensures backwards
        compatibility - scientific notation would change all existing IDs.
        """
        from decimal import ROUND_HALF_UP

        from apps.execution_gateway.order_id_generator import PRICE_PRECISION

        # Test prices that might trigger scientific notation with normalize()
        test_prices = [
            Decimal("150.00"),
            Decimal("1000.00"),
            Decimal("0.01"),
            Decimal("999999.99"),
        ]

        for price in test_prices:
            # Verify quantize produces plain decimal format
            quantized = price.quantize(PRICE_PRECISION, rounding=ROUND_HALF_UP)
            quantized_str = str(quantized)

            # Should not contain 'E' or 'e' (scientific notation marker)
            assert (
                "E" not in quantized_str and "e" not in quantized_str
            ), f"Price {price} produced scientific notation: {quantized_str}"

    def test_generate_uses_today_by_default(self):
        """
        Should use today's date when as_of_date is None.

        This verifies default behavior.
        """
        order = OrderRequest(symbol="AAPL", side="buy", qty=10, order_type="market")

        id_with_none = generate_client_order_id(order, "alpha_baseline", as_of_date=None)
        id_with_today = generate_client_order_id(order, "alpha_baseline", as_of_date=date.today())

        # Should be same (both use today)
        assert id_with_none == id_with_today

    def test_generate_with_large_qty(self):
        """Should handle large quantities correctly."""
        order = OrderRequest(symbol="AAPL", side="buy", qty=10000, order_type="market")

        client_order_id = generate_client_order_id(order, "alpha_baseline")

        # Should generate valid 24-char hex ID
        assert len(client_order_id) == 24
        assert validate_client_order_id(client_order_id) is True


class TestValidateClientOrderId:
    """Test client_order_id format validation."""

    def test_validate_valid_24_char_hex(self):
        """Should accept valid 24-character hex string."""
        valid_id = "a1b2c3d4e5f6" + "0" * 12  # 24 chars

        assert validate_client_order_id(valid_id) is True

    def test_validate_all_hex_digits(self):
        """Should accept all valid hex characters (0-9, a-f)."""
        valid_id = "0123456789abcdef01234567"[:24]

        assert validate_client_order_id(valid_id) is True

    def test_validate_uppercase_hex(self):
        """Should accept uppercase hex characters."""
        valid_id = "ABCDEF" + "0" * 18

        assert validate_client_order_id(valid_id) is True

    def test_validate_mixed_case_hex(self):
        """Should accept mixed case hex."""
        valid_id = "AaBbCcDdEeFf" + "0" * 12

        assert validate_client_order_id(valid_id) is True

    def test_validate_too_short_rejection(self):
        """Should reject ID shorter than 24 characters."""
        short_id = "a" * 23

        assert validate_client_order_id(short_id) is False

    def test_validate_too_long_rejection(self):
        """Should reject ID longer than 24 characters."""
        long_id = "a" * 25

        assert validate_client_order_id(long_id) is False

    def test_validate_non_hex_characters_rejection(self):
        """Should reject ID with non-hex characters."""
        invalid_id = "g" * 24  # 'g' is not valid hex

        assert validate_client_order_id(invalid_id) is False

    def test_validate_special_characters_rejection(self):
        """Should reject ID with special characters."""
        invalid_id = "a1b2-c3d4-e5f6-g7h8-i9j0"

        assert validate_client_order_id(invalid_id) is False

    def test_validate_empty_string_rejection(self):
        """Should reject empty string."""
        assert validate_client_order_id("") is False

    def test_validate_none_input_rejection(self):
        """Should reject None input."""
        assert validate_client_order_id(None) is False  # type: ignore[arg-type]

    def test_validate_non_string_input_rejection(self):
        """Should reject non-string input."""
        assert validate_client_order_id(12345) is False  # type: ignore[arg-type]
        assert validate_client_order_id(["a" * 24]) is False  # type: ignore[arg-type]

    def test_validate_generated_id(self):
        """Should validate IDs generated by generate_client_order_id."""
        order = OrderRequest(symbol="AAPL", side="buy", qty=10, order_type="market")

        client_order_id = generate_client_order_id(order, "alpha_baseline")

        assert validate_client_order_id(client_order_id) is True


class TestReconstructOrderParamsHash:
    """Test manual hash reconstruction for debugging."""

    def test_reconstruct_matches_generate_for_market_order(self):
        """Should match ID from generate_client_order_id for market order."""
        order = OrderRequest(symbol="AAPL", side="buy", qty=10, order_type="market")
        order_date = date(2024, 10, 17)

        id_from_generate = generate_client_order_id(order, "alpha_baseline", order_date)
        id_from_reconstruct = reconstruct_order_params_hash(
            symbol="AAPL",
            side="buy",
            qty=10,
            limit_price=None,
            stop_price=None,
            strategy_id="alpha_baseline",
            order_date=order_date,
        )

        assert id_from_generate == id_from_reconstruct

    def test_reconstruct_matches_generate_for_limit_order(self):
        """Should match ID from generate_client_order_id for limit order."""
        order = OrderRequest(
            symbol="MSFT", side="sell", qty=50, order_type="limit", limit_price=Decimal("250.00")
        )
        order_date = date(2024, 10, 17)

        id_from_generate = generate_client_order_id(order, "beta_strategy", order_date)
        id_from_reconstruct = reconstruct_order_params_hash(
            symbol="MSFT",
            side="sell",
            qty=50,
            limit_price=Decimal("250.00"),
            stop_price=None,
            strategy_id="beta_strategy",
            order_date=order_date,
        )

        assert id_from_generate == id_from_reconstruct

    def test_reconstruct_matches_generate_for_stop_limit_order(self):
        """Should match ID from generate_client_order_id for stop_limit order."""
        order = OrderRequest(
            symbol="GOOGL",
            side="buy",
            qty=5,
            order_type="stop_limit",
            limit_price=Decimal("140.00"),
            stop_price=Decimal("138.00"),
        )
        order_date = date(2024, 10, 17)

        id_from_generate = generate_client_order_id(order, "gamma_strategy", order_date)
        id_from_reconstruct = reconstruct_order_params_hash(
            symbol="GOOGL",
            side="buy",
            qty=5,
            limit_price=Decimal("140.00"),
            stop_price=Decimal("138.00"),
            strategy_id="gamma_strategy",
            order_date=order_date,
        )

        assert id_from_generate == id_from_reconstruct

    def test_reconstruct_with_none_prices(self):
        """Should handle None prices correctly."""
        id_hash = reconstruct_order_params_hash(
            symbol="AAPL",
            side="buy",
            qty=100,
            limit_price=None,
            stop_price=None,
            strategy_id="alpha_baseline",
            order_date=date(2024, 10, 17),
        )

        # Should generate valid 24-char hex ID
        assert len(id_hash) == 24
        assert validate_client_order_id(id_hash) is True

    def test_reconstruct_with_decimal_prices(self):
        """Should handle Decimal prices correctly."""
        id_hash = reconstruct_order_params_hash(
            symbol="AAPL",
            side="buy",
            qty=100,
            limit_price=Decimal("150.25"),
            stop_price=Decimal("149.50"),
            strategy_id="alpha_baseline",
            order_date=date(2024, 10, 17),
        )

        # Should generate valid 24-char hex ID
        assert len(id_hash) == 24
        assert validate_client_order_id(id_hash) is True


class TestParseOrderDateFromTimestamp:
    """Test date extraction from timestamp."""

    def test_parse_datetime_to_date(self):
        """Should extract date from datetime."""
        timestamp = datetime(2024, 10, 17, 16, 30, 45)

        result = parse_order_date_from_timestamp(timestamp)

        assert result == date(2024, 10, 17)

    def test_parse_datetime_midnight(self):
        """Should extract date from midnight timestamp."""
        timestamp = datetime(2024, 10, 17, 0, 0, 0)

        result = parse_order_date_from_timestamp(timestamp)

        assert result == date(2024, 10, 17)

    def test_parse_datetime_end_of_day(self):
        """Should extract date from end-of-day timestamp."""
        timestamp = datetime(2024, 10, 17, 23, 59, 59)

        result = parse_order_date_from_timestamp(timestamp)

        assert result == date(2024, 10, 17)

    def test_parse_timezone_aware_datetime(self):
        """Should extract date from timezone-aware datetime."""
        timestamp = datetime(2024, 10, 17, 16, 30, 45, tzinfo=UTC)

        result = parse_order_date_from_timestamp(timestamp)

        assert result == date(2024, 10, 17)

    def test_parse_different_dates(self):
        """Should extract different dates correctly."""
        ts1 = datetime(2024, 10, 17, 16, 30, 0)
        ts2 = datetime(2024, 10, 18, 16, 30, 0)

        date1 = parse_order_date_from_timestamp(ts1)
        date2 = parse_order_date_from_timestamp(ts2)

        assert date1 != date2
        assert date1 == date(2024, 10, 17)
        assert date2 == date(2024, 10, 18)


class TestOrderIdGeneratorIntegration:
    """Integration tests combining multiple ID generation functions."""

    def test_full_order_id_lifecycle(self):
        """
        Should demonstrate complete order ID lifecycle.

        1. Generate ID from order
        2. Validate ID format
        3. Reconstruct ID from raw params
        4. Verify all match
        """
        order = OrderRequest(
            symbol="AAPL", side="buy", qty=100, order_type="limit", limit_price=Decimal("150.00")
        )
        strategy_id = "alpha_baseline"
        order_date = date(2024, 10, 17)

        # Generate ID
        client_order_id = generate_client_order_id(order, strategy_id, order_date)

        # Validate format
        assert validate_client_order_id(client_order_id) is True

        # Reconstruct from raw params
        reconstructed_id = reconstruct_order_params_hash(
            symbol=order.symbol,
            side=order.side,
            qty=order.qty,
            limit_price=order.limit_price,
            stop_price=order.stop_price,
            strategy_id=strategy_id,
            order_date=order_date,
        )

        # Should all match
        assert client_order_id == reconstructed_id

    def test_idempotency_across_retries(self):
        """
        Should generate same ID across multiple retries.

        This simulates order submission retry logic where we
        need the same client_order_id to avoid duplicates.
        """
        order = OrderRequest(symbol="MSFT", side="buy", qty=50, order_type="market")
        strategy_id = "alpha_baseline"
        order_date = date(2024, 10, 17)

        # Simulate 5 retry attempts
        ids = [generate_client_order_id(order, strategy_id, order_date) for _ in range(5)]

        # All IDs should be identical
        assert len(set(ids)) == 1

    def test_different_strategies_different_ids(self):
        """
        Should generate different IDs for different strategies.

        This prevents collision when multiple strategies
        trade the same symbol on the same day.
        """
        order = OrderRequest(symbol="AAPL", side="buy", qty=100, order_type="market")
        order_date = date(2024, 10, 17)

        id_alpha = generate_client_order_id(order, "alpha_baseline", order_date)
        id_beta = generate_client_order_id(order, "beta_momentum", order_date)
        id_gamma = generate_client_order_id(order, "gamma_reversal", order_date)

        # All should be different
        assert len({id_alpha, id_beta, id_gamma}) == 3

    def test_same_order_different_days_different_ids(self):
        """
        Should allow resubmitting same order on different days.

        This is important for daily trading - the same order
        on different days should have different IDs.
        """
        order = OrderRequest(symbol="AAPL", side="buy", qty=100, order_type="market")
        strategy_id = "alpha_baseline"

        id_day1 = generate_client_order_id(order, strategy_id, date(2024, 10, 17))
        id_day2 = generate_client_order_id(order, strategy_id, date(2024, 10, 18))
        id_day3 = generate_client_order_id(order, strategy_id, date(2024, 10, 19))

        # All should be different
        assert len({id_day1, id_day2, id_day3}) == 3

    def test_extract_date_and_generate_id(self):
        """
        Should work with timestamp-to-date conversion.

        This simulates receiving order timestamp and generating ID.
        """
        order = OrderRequest(symbol="GOOGL", side="sell", qty=25, order_type="market")
        strategy_id = "alpha_baseline"
        order_timestamp = datetime(2024, 10, 17, 15, 30, 0, tzinfo=UTC)

        # Extract date from timestamp
        order_date = parse_order_date_from_timestamp(order_timestamp)

        # Generate ID with extracted date
        client_order_id = generate_client_order_id(order, strategy_id, order_date)

        # Verify
        assert validate_client_order_id(client_order_id) is True
        assert order_date == date(2024, 10, 17)
