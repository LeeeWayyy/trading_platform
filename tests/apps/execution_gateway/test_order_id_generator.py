"""
Unit tests for deterministic client_order_id generation.

Tests verify:
- Idempotency: same order parameters -> same ID
- Uniqueness: different parameters -> different IDs
- Date sensitivity: same parameters, different dates -> different IDs
- Format validation: 24-character hex string
- UTC date usage: consistent IDs across timezones
- Order type differentiation: different order_type/time_in_force -> different IDs
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
    """Test client_order_id generation."""

    def test_idempotency_same_params_same_date(self):
        """Same order parameters on same date should generate same ID."""
        order1 = OrderRequest(symbol="AAPL", side="buy", qty=10, order_type="market")
        order2 = OrderRequest(symbol="AAPL", side="buy", qty=10, order_type="market")

        strategy_id = "alpha_baseline"
        today = date(2024, 10, 17)

        id1 = generate_client_order_id(order1, strategy_id, as_of_date=today)
        id2 = generate_client_order_id(order2, strategy_id, as_of_date=today)

        assert id1 == id2, "Same order parameters should generate same ID"

    def test_uniqueness_different_symbol(self):
        """Different symbols should generate different IDs."""
        order1 = OrderRequest(symbol="AAPL", side="buy", qty=10, order_type="market")
        order2 = OrderRequest(symbol="MSFT", side="buy", qty=10, order_type="market")

        strategy_id = "alpha_baseline"
        today = date(2024, 10, 17)

        id1 = generate_client_order_id(order1, strategy_id, as_of_date=today)
        id2 = generate_client_order_id(order2, strategy_id, as_of_date=today)

        assert id1 != id2, "Different symbols should generate different IDs"

    def test_uniqueness_different_side(self):
        """Different sides should generate different IDs."""
        order1 = OrderRequest(symbol="AAPL", side="buy", qty=10, order_type="market")
        order2 = OrderRequest(symbol="AAPL", side="sell", qty=10, order_type="market")

        strategy_id = "alpha_baseline"
        today = date(2024, 10, 17)

        id1 = generate_client_order_id(order1, strategy_id, as_of_date=today)
        id2 = generate_client_order_id(order2, strategy_id, as_of_date=today)

        assert id1 != id2, "Different sides should generate different IDs"

    def test_uniqueness_different_qty(self):
        """Different quantities should generate different IDs."""
        order1 = OrderRequest(symbol="AAPL", side="buy", qty=10, order_type="market")
        order2 = OrderRequest(symbol="AAPL", side="buy", qty=11, order_type="market")

        strategy_id = "alpha_baseline"
        today = date(2024, 10, 17)

        id1 = generate_client_order_id(order1, strategy_id, as_of_date=today)
        id2 = generate_client_order_id(order2, strategy_id, as_of_date=today)

        assert id1 != id2, "Different quantities should generate different IDs"

    def test_uniqueness_different_limit_price(self):
        """Different limit prices should generate different IDs."""
        order1 = OrderRequest(
            symbol="AAPL", side="buy", qty=10, order_type="limit", limit_price=Decimal("150.00")
        )
        order2 = OrderRequest(
            symbol="AAPL", side="buy", qty=10, order_type="limit", limit_price=Decimal("151.00")
        )

        strategy_id = "alpha_baseline"
        today = date(2024, 10, 17)

        id1 = generate_client_order_id(order1, strategy_id, as_of_date=today)
        id2 = generate_client_order_id(order2, strategy_id, as_of_date=today)

        assert id1 != id2, "Different limit prices should generate different IDs"

    def test_date_sensitivity(self):
        """Same order on different dates should generate different IDs."""
        order = OrderRequest(symbol="AAPL", side="buy", qty=10, order_type="market")
        strategy_id = "alpha_baseline"

        today = date(2024, 10, 17)
        tomorrow = date(2024, 10, 18)

        id_today = generate_client_order_id(order, strategy_id, as_of_date=today)
        id_tomorrow = generate_client_order_id(order, strategy_id, as_of_date=tomorrow)

        assert (
            id_today != id_tomorrow
        ), "Same order on different dates should generate different IDs"

    def test_format_24_chars_hex(self):
        """Generated ID should be 24-character hex string."""
        order = OrderRequest(symbol="AAPL", side="buy", qty=10, order_type="market")
        strategy_id = "alpha_baseline"

        client_order_id = generate_client_order_id(order, strategy_id)

        assert len(client_order_id) == 24, "ID should be 24 characters"
        assert all(c in "0123456789abcdef" for c in client_order_id), "ID should be hexadecimal"

    def test_defaults_to_utc_today(self):
        """Should default to today's UTC date if not specified."""
        order = OrderRequest(symbol="AAPL", side="buy", qty=10, order_type="market")
        strategy_id = "alpha_baseline"

        id_default = generate_client_order_id(order, strategy_id)
        # Use UTC date (not local time) for comparison
        id_explicit = generate_client_order_id(
            order, strategy_id, as_of_date=datetime.now(UTC).date()
        )

        assert id_default == id_explicit, "Should default to today's UTC date"

    def test_different_order_type_generates_different_id(self):
        """Different order types should generate different IDs (T5.5 fix)."""
        order_market = OrderRequest(symbol="AAPL", side="buy", qty=10, order_type="market")
        order_limit = OrderRequest(
            symbol="AAPL",
            side="buy",
            qty=10,
            order_type="limit",
            limit_price=Decimal("150.00"),
        )

        strategy_id = "alpha_baseline"
        today = date(2024, 10, 17)

        id_market = generate_client_order_id(order_market, strategy_id, as_of_date=today)
        id_limit = generate_client_order_id(order_limit, strategy_id, as_of_date=today)

        assert id_market != id_limit, "Different order types should generate different IDs"

    def test_different_time_in_force_generates_different_id(self):
        """Different time_in_force values should generate different IDs (T5.5 fix)."""
        order_day = OrderRequest(
            symbol="AAPL", side="buy", qty=10, order_type="market", time_in_force="day"
        )
        order_gtc = OrderRequest(
            symbol="AAPL", side="buy", qty=10, order_type="market", time_in_force="gtc"
        )

        strategy_id = "alpha_baseline"
        today = date(2024, 10, 17)

        id_day = generate_client_order_id(order_day, strategy_id, as_of_date=today)
        id_gtc = generate_client_order_id(order_gtc, strategy_id, as_of_date=today)

        assert id_day != id_gtc, "Different time_in_force should generate different IDs"

    def test_different_strategy_ids(self):
        """Different strategy IDs should generate different IDs."""
        order = OrderRequest(symbol="AAPL", side="buy", qty=10, order_type="market")
        today = date(2024, 10, 17)

        id1 = generate_client_order_id(order, "alpha_baseline", as_of_date=today)
        id2 = generate_client_order_id(order, "alpha_v2", as_of_date=today)

        assert id1 != id2, "Different strategy IDs should generate different IDs"


class TestValidateClientOrderId:
    """Test client_order_id validation."""

    def test_valid_id(self):
        """Valid 24-character hex string should pass."""
        valid_id = "a1b2c3d4e5f6789012345678"
        assert validate_client_order_id(valid_id) is True

    def test_invalid_length(self):
        """ID with wrong length should fail."""
        too_short = "abc123"
        too_long = "a1b2c3d4e5f6789012345678abcd"

        assert validate_client_order_id(too_short) is False
        assert validate_client_order_id(too_long) is False

    def test_invalid_characters(self):
        """ID with non-hex characters should fail."""
        invalid_id = "g1h2i3j4k5l6m7n8o9p0q1r2"  # Contains g-r
        assert validate_client_order_id(invalid_id) is False

    def test_invalid_type(self):
        """Non-string type should fail."""
        assert validate_client_order_id(123) is False  # type: ignore[arg-type]
        assert validate_client_order_id(None) is False  # type: ignore[arg-type]


class TestReconstructOrderParamsHash:
    """Test hash reconstruction from raw parameters."""

    def test_reconstruction_matches_generation(self):
        """Reconstructed hash should match generated ID."""
        order = OrderRequest(
            symbol="AAPL",
            side="buy",
            qty=10,
            order_type="limit",
            limit_price=Decimal("150.00"),
            time_in_force="day",
        )
        strategy_id = "alpha_baseline"
        order_date = date(2024, 10, 17)

        generated_id = generate_client_order_id(order, strategy_id, as_of_date=order_date)

        reconstructed_id = reconstruct_order_params_hash(
            symbol=order.symbol,
            side=order.side,
            qty=order.qty,
            limit_price=order.limit_price,
            stop_price=order.stop_price,
            order_type=order.order_type,
            time_in_force=order.time_in_force,
            strategy_id=strategy_id,
            order_date=order_date,
        )

        assert reconstructed_id == generated_id, "Reconstructed hash should match generated ID"

    def test_reconstruction_with_none_prices(self):
        """Reconstruction should handle None prices correctly."""
        reconstructed_id = reconstruct_order_params_hash(
            symbol="AAPL",
            side="buy",
            qty=10,
            limit_price=None,
            stop_price=None,
            order_type="market",
            time_in_force="day",
            strategy_id="alpha_baseline",
            order_date=date(2024, 10, 17),
        )

        assert len(reconstructed_id) == 24
        assert validate_client_order_id(reconstructed_id)


class TestParseDateFromTimestamp:
    """Test date extraction from timestamp."""

    def test_parse_date_from_timestamp(self):
        """Should extract date from datetime."""
        from datetime import datetime

        timestamp = datetime(2024, 10, 17, 16, 30, 45)
        extracted_date = parse_order_date_from_timestamp(timestamp)

        assert extracted_date == date(2024, 10, 17)
        assert isinstance(extracted_date, date)
