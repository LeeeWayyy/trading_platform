"""
Unit tests for AlpacaClient runtime type guards.

Tests verify production safety improvements from mypy --strict migration:
- isinstance() checks for Order objects (submit_order)
- isinstance() checks for TradeAccount objects (get_account_info)
- Proper error handling for unexpected API response types

These tests lock in the safety improvements from commit #35.
"""

from unittest.mock import MagicMock, patch

import pytest

from apps.execution_gateway.alpaca_client import AlpacaClientError, AlpacaExecutor
from apps.execution_gateway.schemas import OrderRequest


class TestAlpacaClientTypeGuards:
    """Test runtime type checking for Alpaca API responses."""

    @pytest.fixture()
    def alpaca_client(self):
        """Create AlpacaExecutor with mocked dependencies."""

        # Create mock types for isinstance() checks
        # These must be actual types, not None, to avoid TypeError in isinstance()
        class MockOrder:
            """Mock Order type for isinstance() checks."""

            def __init__(self):
                self.id = None
                self.client_order_id = None
                self.symbol = None
                self.side = MagicMock()
                self.qty = None
                self.order_type = MagicMock()
                self.status = MagicMock()
                self.created_at = None
                self.limit_price = None
                self.stop_price = None

        class MockTradeAccount:
            """Mock TradeAccount type for isinstance() checks."""

            def __init__(self):
                self.account_number = None
                self.status = MagicMock()
                self.currency = None
                self.buying_power = None
                self.cash = None
                self.portfolio_value = None
                self.pattern_day_trader = None
                self.trading_blocked = None
                self.transfers_blocked = None

        class MockPosition:
            """Mock Position type for isinstance() checks."""

            def __init__(self):
                self.symbol = None
                self.qty = None
                self.avg_entry_price = None
                self.current_price = None
                self.market_value = None

        # Mock all dependencies including the model classes
        with (
            patch("apps.execution_gateway.alpaca_client.ALPACA_AVAILABLE", True),
            patch("apps.execution_gateway.alpaca_client.TradingClient"),
            patch("apps.execution_gateway.alpaca_client.StockHistoricalDataClient"),
            patch("apps.execution_gateway.alpaca_client.Order", MockOrder),
            patch("apps.execution_gateway.alpaca_client.Position", MockPosition),
            patch("apps.execution_gateway.alpaca_client.TradeAccount", MockTradeAccount),
        ):
            # Create client with mocked dependencies
            client = AlpacaExecutor(api_key="test_key", secret_key="test_secret", paper=True)

            # Store mock classes on client for test access
            # These are test-only attributes, not part of the production class
            client._mock_order_class = MockOrder  # type: ignore[attr-defined]
            client._mock_account_class = MockTradeAccount  # type: ignore[attr-defined]
            client._mock_position_class = MockPosition  # type: ignore[attr-defined]

            yield client

    def test_submit_order_happy_path_with_order_object(self, alpaca_client):
        """
        submit_order should accept proper Order object from Alpaca API.

        This is the happy path - Alpaca API returns an Order object
        with all expected attributes.
        """
        # Create mock Order object using the mocked class from fixture
        mock_order = alpaca_client._mock_order_class()
        mock_order.id = "order_123"
        mock_order.client_order_id = "test_client_id"
        mock_order.symbol = "AAPL"
        mock_order.side.value = "buy"
        mock_order.qty = 10.0
        mock_order.order_type.value = "market"
        mock_order.status.value = "accepted"
        mock_order.created_at = "2024-10-19T12:00:00Z"
        mock_order.limit_price = None
        mock_order.stop_price = None

        # Configure client to return our mock order
        alpaca_client.client.submit_order = MagicMock(return_value=mock_order)

        order_request = OrderRequest(symbol="AAPL", side="buy", qty=10, order_type="market")

        # Should succeed without raising AlpacaClientError
        result = alpaca_client.submit_order(order=order_request, client_order_id="test_client_id")

        # Verify result is dict with expected fields
        assert result["id"] == "order_123"
        assert result["client_order_id"] == "test_client_id"
        assert result["symbol"] == "AAPL"
        assert result["side"] == "buy"
        assert result["qty"] == 10.0
        assert result["status"] == "accepted"

    def test_submit_order_failure_path_with_unexpected_type(self, alpaca_client):
        """
        submit_order should raise AlpacaClientError when API returns unexpected type.

        This is the failure path - Alpaca API returns dict instead of Order object.
        The isinstance() check (line 200) should catch this and raise AlpacaClientError.
        """
        # Configure mock to return dict instead of Order object
        unexpected_response = {
            "id": "order_123",
            "client_order_id": "test_client_id",
            "symbol": "AAPL",
        }
        alpaca_client.client.submit_order = MagicMock(return_value=unexpected_response)

        order_request = OrderRequest(symbol="AAPL", side="buy", qty=10, order_type="market")

        # Should raise AlpacaClientError due to isinstance() check failure
        with pytest.raises(AlpacaClientError) as exc_info:
            alpaca_client.submit_order(order=order_request, client_order_id="test_client_id")

        # Verify error message mentions unexpected type
        assert "Unexpected response type from Alpaca API" in str(exc_info.value)
        assert "Expected Order object" in str(exc_info.value)

    def test_get_account_info_happy_path_with_account_object(self, alpaca_client):
        """
        get_account_info should accept proper TradeAccount object from Alpaca API.

        This is the happy path - Alpaca API returns a TradeAccount object
        with all expected attributes.
        """
        # Create mock TradeAccount object using the mocked class from fixture
        mock_account = alpaca_client._mock_account_class()
        mock_account.account_number = "ACC123"
        mock_account.status.value = "ACTIVE"
        mock_account.currency = "USD"
        mock_account.buying_power = 100000.00
        mock_account.cash = 50000.00
        mock_account.portfolio_value = 100000.00
        mock_account.pattern_day_trader = False
        mock_account.trading_blocked = False
        mock_account.transfers_blocked = False

        # Configure client to return our mock account
        alpaca_client.client.get_account = MagicMock(return_value=mock_account)

        # Should succeed without raising error
        result = alpaca_client.get_account_info()

        # Verify result is dict with expected fields
        assert result is not None
        assert result["account_number"] == "ACC123"
        assert result["status"] == "ACTIVE"
        assert result["currency"] == "USD"
        assert result["buying_power"] == 100000.00
        assert result["cash"] == 50000.00
        assert result["portfolio_value"] == 100000.00
        assert result["pattern_day_trader"] is False
        assert result["trading_blocked"] is False
        assert result["transfers_blocked"] is False

    def test_get_account_info_failure_path_with_unexpected_type(self, alpaca_client):
        """
        get_account_info should return None when API returns unexpected type.

        This is the failure path - Alpaca API returns dict instead of TradeAccount.
        The isinstance() check (line 456) should catch this and return None.

        Note: Unlike submit_order, this method returns None instead of raising,
        allowing graceful degradation.
        """
        # Configure mock to return dict instead of TradeAccount object
        unexpected_response = {"account_number": "ACC123", "status": "ACTIVE"}
        alpaca_client.client.get_account = MagicMock(return_value=unexpected_response)

        # Should return None due to isinstance() check failure
        result = alpaca_client.get_account_info()

        assert result is None, "Should return None for unexpected response type"

    def test_get_orders_happy_path(self, alpaca_client):
        """get_orders should return list of order dicts."""
        mock_order = alpaca_client._mock_order_class()
        mock_order.id = "order_123"
        mock_order.client_order_id = "client_123"
        mock_order.symbol = "AAPL"
        mock_order.side.value = "buy"
        mock_order.qty = 10.0
        mock_order.order_type.value = "market"
        mock_order.status.value = "accepted"
        mock_order.filled_qty = 0.0
        mock_order.filled_avg_price = None
        mock_order.limit_price = None
        mock_order.notional = None
        mock_order.created_at = None
        mock_order.updated_at = None
        mock_order.submitted_at = None
        mock_order.filled_at = None

        alpaca_client.client.get_orders = MagicMock(return_value=[mock_order])

        result = alpaca_client.get_orders(status="open", limit=100, after=None)
        assert len(result) == 1
        assert result[0]["client_order_id"] == "client_123"
        assert result[0]["symbol"] == "AAPL"
        assert result[0]["side"] == "buy"

    def test_get_orders_unexpected_type_raises(self, alpaca_client):
        """get_orders should raise AlpacaClientError on unexpected response type."""
        alpaca_client.client.get_orders = MagicMock(return_value=[{"id": "bad"}])

        with pytest.raises(AlpacaClientError):
            alpaca_client.get_orders(status="open", limit=100, after=None)

    def test_get_all_positions_happy_path(self, alpaca_client):
        """get_all_positions should return list of position dicts."""
        mock_position = alpaca_client._mock_position_class()
        mock_position.symbol = "AAPL"
        mock_position.qty = 5
        mock_position.avg_entry_price = 150.0
        mock_position.current_price = 151.0
        mock_position.market_value = 755.0

        alpaca_client.client.get_all_positions = MagicMock(return_value=[mock_position])

        result = alpaca_client.get_all_positions()
        assert len(result) == 1
        assert result[0]["symbol"] == "AAPL"
        assert result[0]["qty"] == 5

    def test_get_open_position_returns_none_on_404(self, alpaca_client):
        """get_open_position returns None when Alpaca responds 404 (flat)."""

        class MockAPIError(Exception):
            def __init__(self, status_code: int):
                super().__init__("Not Found")
                self.status_code = status_code

        with patch("apps.execution_gateway.alpaca_client.AlpacaAPIError", MockAPIError):
            alpaca_client.client.get_open_position = MagicMock(
                side_effect=MockAPIError(status_code=404)
            )
            assert alpaca_client.get_open_position("AAPL") is None


class TestAlpacaClientTypeGuardsDocumentation:
    """
    Documentation tests explaining why these type guards are important.

    Background:
    -----------
    During the mypy --strict migration (commits #1-#33), we discovered that
    alpaca-py library returns union types:
    - submit_order() returns Order | dict[str, Any]
    - get_account() returns TradeAccount | dict[str, Any]

    The Problem:
    ------------
    Initial implementation used type: ignore[union-attr] to suppress mypy errors,
    which masked potential runtime failures:

        order = client.submit_order(request)
        return order.id  # type: ignore[union-attr]
        # ^ Would fail with AttributeError if order is dict!

    The Solution (Commit #35):
    --------------------------
    Added isinstance() checks before accessing attributes:

        order = client.submit_order(request)
        if not isinstance(order, Order):
            raise AlpacaClientError(...)
        return order.id  # Safe - mypy knows order is Order type

    Benefits:
    ---------
    1. Production safety: Catches API contract violations early
    2. Type safety: Eliminates need for type: ignore suppression
    3. Debugging: Clear error messages when API behavior changes
    4. Reliability: Prevents AttributeError crashes in production

    See Also:
    ---------
    - docs/LESSONS_LEARNED/mypy-strict-migration.md (Challenge 2: Union Types)
    - docs/CONCEPTS/python-testing-tools.md (mypy section)
    """

    def test_documentation_exists(self):
        """Verify production safety documentation exists."""
        import os

        # Verify lessons learned doc exists
        lessons_learned_path = os.path.join(
            os.path.dirname(__file__), "../../../docs/LESSONS_LEARNED/mypy-strict-migration.md"
        )
        assert os.path.exists(
            lessons_learned_path
        ), "mypy-strict-migration.md documentation should exist"
