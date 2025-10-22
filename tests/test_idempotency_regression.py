"""
Idempotency regression tests for order submission.

Tests ensure that duplicate order submissions are handled correctly
and the idempotency mechanism cannot regress.
"""

import pytest
from datetime import date
from decimal import Decimal
from apps.execution_gateway.order_id_generator import generate_client_order_id
from apps.execution_gateway.schemas import OrderRequest


class TestIdempotencyRegression:
    """Regression tests for idempotency mechanism."""

    def test_same_parameters_same_day_generates_same_id(self):
        """Test that identical orders on same day get same client_order_id."""
        # Same parameters
        order = OrderRequest(
            symbol="AAPL",
            side="buy",
            qty=10,
            order_type="market",
        )
        strategy_id = "alpha_baseline"
        as_of_date = date.today()

        id1 = generate_client_order_id(order, strategy_id, as_of_date)
        id2 = generate_client_order_id(order, strategy_id, as_of_date)

        assert id1 == id2, "Same parameters must generate same client_order_id"
        assert len(id1) == 24, f"client_order_id should be 24 chars, got: {len(id1)}"

    def test_different_symbol_generates_different_id(self):
        """Test that different symbols generate different IDs."""
        order1 = OrderRequest(symbol="AAPL", side="buy", qty=10, order_type="market")
        order2 = OrderRequest(symbol="MSFT", side="buy", qty=10, order_type="market")
        strategy_id = "alpha_baseline"
        as_of_date = date.today()

        id1 = generate_client_order_id(order1, strategy_id, as_of_date)
        id2 = generate_client_order_id(order2, strategy_id, as_of_date)

        assert id1 != id2, "Different symbols must generate different IDs"

    def test_different_side_generates_different_id(self):
        """Test that different sides (buy/sell) generate different IDs."""
        order1 = OrderRequest(symbol="AAPL", side="buy", qty=10, order_type="market")
        order2 = OrderRequest(symbol="AAPL", side="sell", qty=10, order_type="market")
        strategy_id = "alpha_baseline"
        as_of_date = date.today()

        id1 = generate_client_order_id(order1, strategy_id, as_of_date)
        id2 = generate_client_order_id(order2, strategy_id, as_of_date)

        assert id1 != id2, "Different sides must generate different IDs"

    def test_different_qty_generates_different_id(self):
        """Test that different quantities generate different IDs."""
        order1 = OrderRequest(symbol="AAPL", side="buy", qty=10, order_type="market")
        order2 = OrderRequest(symbol="AAPL", side="buy", qty=20, order_type="market")
        strategy_id = "alpha_baseline"
        as_of_date = date.today()

        id1 = generate_client_order_id(order1, strategy_id, as_of_date)
        id2 = generate_client_order_id(order2, strategy_id, as_of_date)

        assert id1 != id2, "Different quantities must generate different IDs"

    def test_different_price_generates_different_id(self):
        """Test that different prices generate different IDs."""
        order1 = OrderRequest(
            symbol="AAPL",
            side="buy",
            qty=10,
            order_type="limit",
            limit_price=Decimal("150.00"),
        )
        order2 = OrderRequest(
            symbol="AAPL",
            side="buy",
            qty=10,
            order_type="limit",
            limit_price=Decimal("151.00"),
        )
        strategy_id = "alpha_baseline"
        as_of_date = date.today()

        id1 = generate_client_order_id(order1, strategy_id, as_of_date)
        id2 = generate_client_order_id(order2, strategy_id, as_of_date)

        assert id1 != id2, "Different prices must generate different IDs"

    def test_different_strategy_generates_different_id(self):
        """Test that different strategies generate different IDs."""
        order = OrderRequest(symbol="AAPL", side="buy", qty=10, order_type="market")
        as_of_date = date.today()

        id1 = generate_client_order_id(order, "alpha_baseline", as_of_date)
        id2 = generate_client_order_id(order, "momentum", as_of_date)

        assert id1 != id2, "Different strategies must generate different IDs"

    def test_different_date_generates_different_id(self):
        """Test that different dates generate different IDs."""
        from datetime import datetime, timedelta

        today = date.today()
        yesterday = (datetime.now() - timedelta(days=1)).date()

        order = OrderRequest(symbol="AAPL", side="buy", qty=10, order_type="market")
        strategy_id = "alpha_baseline"

        id1 = generate_client_order_id(order, strategy_id, today)
        id2 = generate_client_order_id(order, strategy_id, yesterday)

        assert id1 != id2, "Different dates must generate different IDs"

    def test_client_order_id_deterministic_across_calls(self):
        """Test that client_order_id generation is deterministic."""
        order = OrderRequest(symbol="AAPL", side="buy", qty=10, order_type="market")
        strategy_id = "alpha_baseline"
        as_of_date = date.today()

        # Generate ID 10 times
        ids = [generate_client_order_id(order, strategy_id, as_of_date) for _ in range(10)]

        # All IDs must be identical
        assert len(set(ids)) == 1, f"IDs not deterministic: {set(ids)}"

    def test_client_order_id_alphanumeric(self):
        """Test that client_order_id contains only alphanumeric characters (hex)."""
        order = OrderRequest(symbol="AAPL", side="buy", qty=10, order_type="market")
        strategy_id = "alpha_baseline"
        as_of_date = date.today()

        client_order_id = generate_client_order_id(order, strategy_id, as_of_date)

        # Should only contain hexadecimal characters (0-9, a-f)
        assert all(c in "0123456789abcdef" for c in client_order_id), (
            f"client_order_id contains invalid characters: {client_order_id}"
        )

    def test_client_order_id_not_empty(self):
        """Test that client_order_id is never empty."""
        order = OrderRequest(symbol="AAPL", side="buy", qty=10, order_type="market")
        strategy_id = "alpha_baseline"
        as_of_date = date.today()

        client_order_id = generate_client_order_id(order, strategy_id, as_of_date)

        assert len(client_order_id) > 0, "client_order_id must not be empty"
        assert client_order_id.strip() != "", "client_order_id must not be whitespace"
        assert len(client_order_id) == 24, "client_order_id should be exactly 24 characters"
