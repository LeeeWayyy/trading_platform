"""
Comprehensive unit tests for AlpacaExecutor client.

Tests verify:
- Runtime type checking for API responses
- Retry logic and error handling
- Order submission with all order types
- Position and account queries
- Market data fetching
- Connection health checks
- Error classification (retryable vs non-retryable)

Coverage target: 85%+ branch coverage for critical trading API client.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, Mock, patch

import httpx
import pytest

from apps.execution_gateway.alpaca_client import (
    AlpacaClientError,
    AlpacaConnectionError,
    AlpacaExecutor,
    AlpacaRejectionError,
    AlpacaValidationError,
)
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


class TestAlpacaExecutorInitialization:
    """Test AlpacaExecutor initialization and client setup."""

    def test_init_requires_alpaca_package(self):
        """Should raise ImportError if alpaca-py is not available."""
        with patch("apps.execution_gateway.alpaca_client.ALPACA_AVAILABLE", False):
            with pytest.raises(ImportError, match="alpaca-py package is required"):
                AlpacaExecutor(api_key="test", secret_key="test")

    def test_init_creates_clients(self):
        """Should initialize trading and data clients."""
        with (
            patch("apps.execution_gateway.alpaca_client.ALPACA_AVAILABLE", True),
            patch("apps.execution_gateway.alpaca_client.TradingClient") as mock_trading,
            patch("apps.execution_gateway.alpaca_client.StockHistoricalDataClient") as mock_data,
        ):
            executor = AlpacaExecutor(
                api_key="test_key",
                secret_key="test_secret",
                base_url="https://paper-api.alpaca.markets",
                paper=True,
            )

            assert executor.api_key == "test_key"
            assert executor.secret_key == "test_secret"
            assert executor.paper is True
            assert executor.base_url == "https://paper-api.alpaca.markets"
            mock_trading.assert_called_once_with(
                api_key="test_key", secret_key="test_secret", paper=True
            )
            mock_data.assert_called_once_with(api_key="test_key", secret_key="test_secret")


class TestOrderSubmissionRetryLogic:
    """Test retry behavior for order submission."""

    @pytest.fixture()
    def mock_executor(self):
        """Create AlpacaExecutor with mocked dependencies."""
        with (
            patch("apps.execution_gateway.alpaca_client.ALPACA_AVAILABLE", True),
            patch("apps.execution_gateway.alpaca_client.TradingClient"),
            patch("apps.execution_gateway.alpaca_client.StockHistoricalDataClient"),
        ):
            return AlpacaExecutor(api_key="test", secret_key="test")

    def test_submit_order_retries_on_connection_error(self, mock_executor):
        """submit_order should retry up to 3 times on connection errors."""
        order_request = OrderRequest(symbol="AAPL", side="buy", qty=10, order_type="market")

        # Simulate connection timeout
        mock_executor.client.submit_order = Mock(side_effect=httpx.ConnectTimeout("Timeout"))

        # Should retry 3 times then raise AlpacaConnectionError
        with pytest.raises(AlpacaConnectionError, match="Network error"):
            mock_executor.submit_order(order_request, "client_123")

        assert mock_executor.client.submit_order.call_count == 3

    def test_submit_order_does_not_retry_on_validation_error(self, mock_executor):
        """submit_order should NOT retry on validation errors (400)."""

        class MockAPIError(Exception):
            def __init__(self, status_code: int, message: str):
                super().__init__(message)
                self.status_code = status_code

        order_request = OrderRequest(symbol="AAPL", side="buy", qty=10, order_type="market")

        with patch("apps.execution_gateway.alpaca_client.AlpacaAPIError", MockAPIError):
            mock_executor.client.submit_order = Mock(
                side_effect=MockAPIError(400, "Invalid symbol")
            )

            with pytest.raises(AlpacaValidationError, match="Invalid order"):
                mock_executor.submit_order(order_request, "client_123")

            # Should only call once (no retry)
            assert mock_executor.client.submit_order.call_count == 1

    def test_submit_order_does_not_retry_on_rejection(self, mock_executor):
        """submit_order should NOT retry on order rejection (422, 403)."""

        class MockAPIError(Exception):
            def __init__(self, status_code: int, message: str):
                super().__init__(message)
                self.status_code = status_code

        order_request = OrderRequest(symbol="AAPL", side="buy", qty=10, order_type="market")

        with patch("apps.execution_gateway.alpaca_client.AlpacaAPIError", MockAPIError):
            # Test 422 - Unprocessable entity
            mock_executor.client.submit_order = Mock(
                side_effect=MockAPIError(422, "Insufficient funds")
            )

            with pytest.raises(AlpacaRejectionError, match="Order rejected"):
                mock_executor.submit_order(order_request, "client_123")

            assert mock_executor.client.submit_order.call_count == 1

            # Test 403 - Forbidden
            mock_executor.client.submit_order = Mock(
                side_effect=MockAPIError(403, "Account suspended")
            )

            with pytest.raises(AlpacaRejectionError, match="Order rejected"):
                mock_executor.submit_order(order_request, "client_123")

    def test_submit_order_raises_on_data_validation_errors(self, mock_executor):
        """submit_order should raise AlpacaValidationError on data parsing errors."""
        order_request = OrderRequest(symbol="AAPL", side="buy", qty=10, order_type="market")

        # Simulate JSON decode error
        mock_executor.client.submit_order = Mock(side_effect=ValueError("Invalid decimal"))

        with pytest.raises(AlpacaValidationError, match="Data validation error"):
            mock_executor.submit_order(order_request, "client_123")


class TestBuildAlpacaRequest:
    """Test _build_alpaca_request for different order types."""

    @pytest.fixture()
    def mock_executor(self):
        """Create AlpacaExecutor with mocked dependencies."""
        with (
            patch("apps.execution_gateway.alpaca_client.ALPACA_AVAILABLE", True),
            patch("apps.execution_gateway.alpaca_client.TradingClient"),
            patch("apps.execution_gateway.alpaca_client.StockHistoricalDataClient"),
            patch("apps.execution_gateway.alpaca_client.MarketOrderRequest") as mock_market,
            patch("apps.execution_gateway.alpaca_client.LimitOrderRequest") as mock_limit,
            patch("apps.execution_gateway.alpaca_client.StopOrderRequest") as mock_stop,
            patch("apps.execution_gateway.alpaca_client.StopLimitOrderRequest") as mock_stop_limit,
        ):
            executor = AlpacaExecutor(api_key="test", secret_key="test")
            # Store mocks for assertions
            executor._mock_market = mock_market  # type: ignore[attr-defined]
            executor._mock_limit = mock_limit  # type: ignore[attr-defined]
            executor._mock_stop = mock_stop  # type: ignore[attr-defined]
            executor._mock_stop_limit = mock_stop_limit  # type: ignore[attr-defined]
            yield executor

    def test_build_market_order(self, mock_executor):
        """Should create MarketOrderRequest for market orders."""
        order = OrderRequest(symbol="AAPL", side="buy", qty=10, order_type="market")
        mock_executor._build_alpaca_request(order, "client_123")

        mock_executor._mock_market.assert_called_once()
        call_kwargs = mock_executor._mock_market.call_args.kwargs
        assert call_kwargs["symbol"] == "AAPL"
        assert call_kwargs["qty"] == 10
        assert call_kwargs["client_order_id"] == "client_123"

    def test_build_limit_order(self, mock_executor):
        """Should create LimitOrderRequest for limit orders."""
        order = OrderRequest(
            symbol="AAPL", side="buy", qty=10, order_type="limit", limit_price=Decimal("150.00")
        )
        mock_executor._build_alpaca_request(order, "client_123")

        mock_executor._mock_limit.assert_called_once()
        call_kwargs = mock_executor._mock_limit.call_args.kwargs
        assert call_kwargs["symbol"] == "AAPL"
        assert call_kwargs["limit_price"] == 150.00

    def test_build_limit_order_missing_price_raises(self, mock_executor):
        """Should raise ValueError if limit_price is missing for limit order."""
        order = OrderRequest(symbol="AAPL", side="buy", qty=10, order_type="limit")

        with pytest.raises(ValueError, match="limit_price is required"):
            mock_executor._build_alpaca_request(order, "client_123")

    def test_build_stop_order(self, mock_executor):
        """Should create StopOrderRequest for stop orders."""
        order = OrderRequest(
            symbol="AAPL", side="sell", qty=10, order_type="stop", stop_price=Decimal("145.00")
        )
        mock_executor._build_alpaca_request(order, "client_123")

        mock_executor._mock_stop.assert_called_once()
        call_kwargs = mock_executor._mock_stop.call_args.kwargs
        assert call_kwargs["symbol"] == "AAPL"
        assert call_kwargs["stop_price"] == 145.00

    def test_build_stop_order_missing_price_raises(self, mock_executor):
        """Should raise ValueError if stop_price is missing for stop order."""
        order = OrderRequest(symbol="AAPL", side="sell", qty=10, order_type="stop")

        with pytest.raises(ValueError, match="stop_price is required"):
            mock_executor._build_alpaca_request(order, "client_123")

    def test_build_stop_limit_order(self, mock_executor):
        """Should create StopLimitOrderRequest for stop_limit orders."""
        order = OrderRequest(
            symbol="AAPL",
            side="sell",
            qty=10,
            order_type="stop_limit",
            limit_price=Decimal("145.00"),
            stop_price=Decimal("144.00"),
        )
        mock_executor._build_alpaca_request(order, "client_123")

        mock_executor._mock_stop_limit.assert_called_once()
        call_kwargs = mock_executor._mock_stop_limit.call_args.kwargs
        assert call_kwargs["symbol"] == "AAPL"
        assert call_kwargs["limit_price"] == 145.00
        assert call_kwargs["stop_price"] == 144.00

    def test_build_stop_limit_order_missing_prices_raises(self, mock_executor):
        """Should raise ValueError if prices are missing for stop_limit order."""
        # Missing both
        order = OrderRequest(symbol="AAPL", side="sell", qty=10, order_type="stop_limit")
        with pytest.raises(ValueError, match="Both limit_price and stop_price are required"):
            mock_executor._build_alpaca_request(order, "client_123")

        # Missing limit_price
        order = OrderRequest(
            symbol="AAPL",
            side="sell",
            qty=10,
            order_type="stop_limit",
            stop_price=Decimal("144.00"),
        )
        with pytest.raises(ValueError, match="Both limit_price and stop_price are required"):
            mock_executor._build_alpaca_request(order, "client_123")

    def test_build_converts_side_correctly(self, mock_executor):
        """Should convert 'buy'/'sell' to OrderSide enum."""
        # Test buy
        order_buy = OrderRequest(symbol="AAPL", side="buy", qty=10, order_type="market")
        mock_executor._build_alpaca_request(order_buy, "client_123")
        call_kwargs_buy = mock_executor._mock_market.call_args.kwargs
        assert str(call_kwargs_buy["side"]) == "OrderSide.BUY"

        # Reset mock
        mock_executor._mock_market.reset_mock()

        # Test sell
        order_sell = OrderRequest(symbol="AAPL", side="sell", qty=10, order_type="market")
        mock_executor._build_alpaca_request(order_sell, "client_123")
        call_kwargs_sell = mock_executor._mock_market.call_args.kwargs
        assert str(call_kwargs_sell["side"]) == "OrderSide.SELL"

    def test_build_converts_time_in_force(self, mock_executor):
        """Should convert time_in_force strings to TimeInForce enum."""
        # Test different TIF values
        for tif_str in ["day", "gtc", "ioc", "fok"]:
            order = OrderRequest(
                symbol="AAPL", side="buy", qty=10, order_type="market", time_in_force=tif_str  # type: ignore[arg-type]
            )
            mock_executor._mock_market.reset_mock()
            mock_executor._build_alpaca_request(order, "client_123")
            call_kwargs = mock_executor._mock_market.call_args.kwargs
            assert call_kwargs["time_in_force"] is not None


class TestGetOrderByClientId:
    """Test get_order_by_client_id method."""

    @pytest.fixture()
    def mock_executor(self):
        """Create AlpacaExecutor with mocked dependencies."""

        class MockOrder:
            def __init__(self):
                self.id = "order_123"
                self.client_order_id = "client_123"
                self.symbol = "AAPL"
                self.side = MagicMock()
                self.side.value = "buy"
                self.qty = 10.0
                self.order_type = MagicMock()
                self.order_type.value = "market"
                self.status = MagicMock()
                self.status.value = "filled"
                self.filled_qty = 10.0
                self.filled_avg_price = 150.50
                self.created_at = datetime.now(UTC)
                self.updated_at = datetime.now(UTC)

        with (
            patch("apps.execution_gateway.alpaca_client.ALPACA_AVAILABLE", True),
            patch("apps.execution_gateway.alpaca_client.TradingClient"),
            patch("apps.execution_gateway.alpaca_client.StockHistoricalDataClient"),
            patch("apps.execution_gateway.alpaca_client.Order", MockOrder),
        ):
            executor = AlpacaExecutor(api_key="test", secret_key="test")
            executor._mock_order_class = MockOrder  # type: ignore[attr-defined]
            yield executor

    def test_get_order_by_client_id_returns_order(self, mock_executor):
        """Should return order dict when found."""
        mock_order = mock_executor._mock_order_class()
        mock_executor.client.get_order_by_client_id = Mock(return_value=mock_order)

        result = mock_executor.get_order_by_client_id("client_123")

        assert result is not None
        assert result["id"] == "order_123"
        assert result["client_order_id"] == "client_123"
        assert result["symbol"] == "AAPL"
        assert result["qty"] == Decimal("10.0")

    def test_get_order_by_client_id_returns_none_on_404(self, mock_executor):
        """Should return None when order not found (404)."""

        class MockAPIError(Exception):
            def __init__(self):
                super().__init__("Not found")
                self.status_code = 404

        with patch("apps.execution_gateway.alpaca_client.AlpacaAPIError", MockAPIError):
            mock_executor.client.get_order_by_client_id = Mock(side_effect=MockAPIError())

            result = mock_executor.get_order_by_client_id("nonexistent")
            assert result is None

    def test_get_order_by_client_id_raises_on_other_errors(self, mock_executor):
        """Should raise AlpacaConnectionError for non-404 errors."""

        class MockAPIError(Exception):
            def __init__(self):
                super().__init__("Server error")
                self.status_code = 500

        with patch("apps.execution_gateway.alpaca_client.AlpacaAPIError", MockAPIError):
            mock_executor.client.get_order_by_client_id = Mock(side_effect=MockAPIError())

            with pytest.raises(AlpacaConnectionError):
                mock_executor.get_order_by_client_id("client_123")


class TestGetOrders:
    """Test get_orders pagination and filtering."""

    @pytest.fixture()
    def mock_executor(self):
        """Create AlpacaExecutor with mocked Order class."""

        class MockOrder:
            def __init__(self, order_id: str, created_at: datetime):
                self.id = order_id
                self.client_order_id = f"client_{order_id}"
                self.symbol = "AAPL"
                self.side = MagicMock()
                self.side.value = "buy"
                self.qty = 10.0
                self.order_type = MagicMock()
                self.order_type.value = "market"
                self.status = MagicMock()
                self.status.value = "filled"
                self.filled_qty = 10.0
                self.filled_avg_price = 150.50
                self.limit_price = None
                self.notional = None
                self.created_at = created_at
                self.updated_at = created_at
                self.submitted_at = created_at
                self.filled_at = created_at

        with (
            patch("apps.execution_gateway.alpaca_client.ALPACA_AVAILABLE", True),
            patch("apps.execution_gateway.alpaca_client.TradingClient"),
            patch("apps.execution_gateway.alpaca_client.StockHistoricalDataClient"),
            patch("apps.execution_gateway.alpaca_client.Order", MockOrder),
        ):
            executor = AlpacaExecutor(api_key="test", secret_key="test")
            executor._mock_order_class = MockOrder  # type: ignore[attr-defined]
            yield executor

    def test_get_orders_handles_pagination(self, mock_executor):
        """Should paginate through multiple pages of results."""
        now = datetime.now(UTC)

        # First page - full limit
        page1 = [mock_executor._mock_order_class(f"order_{i}", now) for i in range(5)]

        # Second page - less than limit (end of results)
        page2 = [
            mock_executor._mock_order_class(f"order_{i}", now + timedelta(seconds=i))
            for i in range(5, 7)
        ]

        mock_executor.client.get_orders = Mock(side_effect=[page1, page2])

        result = mock_executor.get_orders(status="all", limit=5)

        assert len(result) == 7
        assert mock_executor.client.get_orders.call_count == 2

    def test_get_orders_deduplicates_results(self, mock_executor):
        """Should deduplicate orders with same ID across pages."""
        now = datetime.now(UTC)

        # Create orders with same ID  (duplicate)
        order_1 = mock_executor._mock_order_class("order_1", now)
        order_1_dup = mock_executor._mock_order_class("order_1", now + timedelta(seconds=1))
        order_2 = mock_executor._mock_order_class("order_2", now + timedelta(seconds=2))

        # Single page with duplicates and unique order
        page1 = [order_1, order_1_dup, order_2]

        mock_executor.client.get_orders = Mock(return_value=page1)

        result = mock_executor.get_orders(status="all", limit=5)

        # Should only have 2 unique orders (order_1 and order_2)
        assert len(result) == 2
        order_ids = [order["id"] for order in result]
        assert "order_1" in order_ids
        assert "order_2" in order_ids

    def test_get_orders_handles_dict_response(self, mock_executor):
        """Should handle response as dict with 'orders' key."""
        now = datetime.now(UTC)
        orders = [mock_executor._mock_order_class("order_1", now)]

        mock_executor.client.get_orders = Mock(return_value={"orders": orders})

        result = mock_executor.get_orders(status="all", limit=5)

        assert len(result) == 1
        assert result[0]["id"] == "order_1"

    def test_get_orders_invalid_limit_raises(self, mock_executor):
        """Should raise ValueError for invalid limit."""
        with pytest.raises(ValueError, match="limit must be positive"):
            mock_executor.get_orders(status="all", limit=0)

        with pytest.raises(ValueError, match="limit must be positive"):
            mock_executor.get_orders(status="all", limit=-1)

    def test_get_orders_invalid_status_raises(self, mock_executor):
        """Should raise ValueError for invalid status."""
        with pytest.raises(ValueError, match="Unsupported status"):
            mock_executor.get_orders(status="invalid_status", limit=100)

    def test_get_orders_handles_api_error(self, mock_executor):
        """Should raise AlpacaConnectionError on API error."""

        class MockAPIError(Exception):
            pass

        with patch("apps.execution_gateway.alpaca_client.AlpacaAPIError", MockAPIError):
            mock_executor.client.get_orders = Mock(side_effect=MockAPIError("Server error"))

            with pytest.raises(AlpacaConnectionError):
                mock_executor.get_orders(status="all", limit=100)


class TestParseDatetime:
    """Test _parse_datetime helper method."""

    @pytest.fixture()
    def mock_executor(self):
        """Create AlpacaExecutor with mocked dependencies."""
        with (
            patch("apps.execution_gateway.alpaca_client.ALPACA_AVAILABLE", True),
            patch("apps.execution_gateway.alpaca_client.TradingClient"),
            patch("apps.execution_gateway.alpaca_client.StockHistoricalDataClient"),
        ):
            return AlpacaExecutor(api_key="test", secret_key="test")

    def test_parse_datetime_returns_none_for_none(self, mock_executor):
        """Should return None for None input."""
        assert mock_executor._parse_datetime(None) is None

    def test_parse_datetime_handles_aware_datetime(self, mock_executor):
        """Should pass through timezone-aware datetime unchanged."""
        dt = datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC)
        result = mock_executor._parse_datetime(dt)

        assert result == dt
        assert result.tzinfo == UTC

    def test_parse_datetime_makes_naive_datetime_aware(self, mock_executor):
        """Should add UTC timezone to naive datetime."""
        dt_naive = datetime(2024, 1, 15, 12, 0, 0)
        result = mock_executor._parse_datetime(dt_naive)

        assert result.year == 2024
        assert result.month == 1
        assert result.day == 15
        assert result.tzinfo == UTC

    def test_parse_datetime_parses_iso_string(self, mock_executor):
        """Should parse ISO format string."""
        iso_string = "2024-01-15T12:00:00Z"
        result = mock_executor._parse_datetime(iso_string)

        assert result.year == 2024
        assert result.month == 1
        assert result.day == 15
        assert result.tzinfo == UTC

    def test_parse_datetime_parses_iso_string_with_timezone(self, mock_executor):
        """Should parse ISO format string with timezone offset."""
        iso_string = "2024-01-15T12:00:00+00:00"
        result = mock_executor._parse_datetime(iso_string)

        assert result.year == 2024
        assert result.tzinfo is not None

    def test_parse_datetime_returns_none_for_invalid_string(self, mock_executor):
        """Should return None for unparseable string."""
        result = mock_executor._parse_datetime("invalid date")
        assert result is None

    def test_parse_datetime_returns_none_for_unexpected_type(self, mock_executor):
        """Should return None for unexpected types."""
        assert mock_executor._parse_datetime(12345) is None  # type: ignore[arg-type]
        assert mock_executor._parse_datetime([]) is None  # type: ignore[arg-type]


class TestGetMarketClock:
    """Test get_market_clock method."""

    @pytest.fixture()
    def mock_executor(self):
        """Create AlpacaExecutor with mocked dependencies."""
        with (
            patch("apps.execution_gateway.alpaca_client.ALPACA_AVAILABLE", True),
            patch("apps.execution_gateway.alpaca_client.TradingClient"),
            patch("apps.execution_gateway.alpaca_client.StockHistoricalDataClient"),
        ):
            return AlpacaExecutor(api_key="test", secret_key="test")

    def test_get_market_clock_returns_clock_object(self, mock_executor):
        """Should handle Clock object response."""
        mock_clock = MagicMock()
        mock_clock.timestamp = datetime.now(UTC)
        mock_clock.is_open = True
        mock_clock.next_open = datetime.now(UTC) + timedelta(hours=1)
        mock_clock.next_close = datetime.now(UTC) + timedelta(hours=8)

        mock_executor.client.get_clock = Mock(return_value=mock_clock)

        result = mock_executor.get_market_clock()

        assert result["is_open"] is True
        assert result["timestamp"] is not None
        assert result["next_open"] is not None
        assert result["next_close"] is not None

    def test_get_market_clock_handles_dict_response(self, mock_executor):
        """Should handle dict response."""
        clock_dict = {
            "timestamp": "2024-01-15T12:00:00Z",
            "is_open": True,
            "next_open": "2024-01-16T09:30:00Z",
            "next_close": "2024-01-15T16:00:00Z",
        }

        mock_executor.client.get_clock = Mock(return_value=clock_dict)

        result = mock_executor.get_market_clock()

        assert result["is_open"] is True
        assert result["timestamp"] is not None

    def test_get_market_clock_raises_on_api_error(self, mock_executor):
        """Should raise AlpacaConnectionError on API error."""

        class MockAPIError(Exception):
            pass

        with patch("apps.execution_gateway.alpaca_client.AlpacaAPIError", MockAPIError):
            mock_executor.client.get_clock = Mock(side_effect=MockAPIError("Server error"))

            with pytest.raises(AlpacaConnectionError):
                mock_executor.get_market_clock()


class TestCancelOrder:
    """Test cancel_order method."""

    @pytest.fixture()
    def mock_executor(self):
        """Create AlpacaExecutor with mocked dependencies."""
        with (
            patch("apps.execution_gateway.alpaca_client.ALPACA_AVAILABLE", True),
            patch("apps.execution_gateway.alpaca_client.TradingClient"),
            patch("apps.execution_gateway.alpaca_client.StockHistoricalDataClient"),
        ):
            return AlpacaExecutor(api_key="test", secret_key="test")

    def test_cancel_order_success(self, mock_executor):
        """Should return True on successful cancellation."""
        mock_executor.client.cancel_order_by_id = Mock(return_value=None)

        result = mock_executor.cancel_order("order_123")

        assert result is True
        mock_executor.client.cancel_order_by_id.assert_called_once_with("order_123")

    def test_cancel_order_raises_on_rejection(self, mock_executor):
        """Should raise AlpacaRejectionError for 422 status."""

        class MockAPIError(Exception):
            def __init__(self):
                super().__init__("Order already filled")
                self.status_code = 422

        with patch("apps.execution_gateway.alpaca_client.AlpacaAPIError", MockAPIError):
            mock_executor.client.cancel_order_by_id = Mock(side_effect=MockAPIError())

            with pytest.raises(AlpacaRejectionError, match="Order cannot be cancelled"):
                mock_executor.cancel_order("order_123")

    def test_cancel_order_raises_on_connection_error(self, mock_executor):
        """Should raise AlpacaConnectionError for other errors."""

        class MockAPIError(Exception):
            def __init__(self):
                super().__init__("Server error")
                self.status_code = 500

        with patch("apps.execution_gateway.alpaca_client.AlpacaAPIError", MockAPIError):
            mock_executor.client.cancel_order_by_id = Mock(side_effect=MockAPIError())

            with pytest.raises(AlpacaConnectionError):
                mock_executor.cancel_order("order_123")


class TestCheckConnection:
    """Test check_connection health check method."""

    @pytest.fixture()
    def mock_executor(self):
        """Create AlpacaExecutor with mocked dependencies."""
        with (
            patch("apps.execution_gateway.alpaca_client.ALPACA_AVAILABLE", True),
            patch("apps.execution_gateway.alpaca_client.TradingClient"),
            patch("apps.execution_gateway.alpaca_client.StockHistoricalDataClient"),
        ):
            return AlpacaExecutor(api_key="test", secret_key="test")

    def test_check_connection_returns_true_when_healthy(self, mock_executor):
        """Should return True when account info retrieved successfully."""
        mock_executor.client.get_account = Mock(return_value=MagicMock())

        result = mock_executor.check_connection()

        assert result is True

    def test_check_connection_returns_false_on_api_error(self, mock_executor):
        """Should return False on API error."""

        class MockAPIError(Exception):
            def __init__(self):
                super().__init__("Unauthorized")
                self.status_code = 401

        with patch("apps.execution_gateway.alpaca_client.AlpacaAPIError", MockAPIError):
            mock_executor.client.get_account = Mock(side_effect=MockAPIError())

            result = mock_executor.check_connection()

            assert result is False

    def test_check_connection_returns_false_on_network_error(self, mock_executor):
        """Should return False on network errors."""
        mock_executor.client.get_account = Mock(side_effect=httpx.ConnectTimeout("Timeout"))

        result = mock_executor.check_connection()

        assert result is False

    def test_check_connection_returns_false_on_unexpected_error(self, mock_executor):
        """Should return False on unexpected errors."""
        mock_executor.client.get_account = Mock(side_effect=RuntimeError("Unexpected"))

        result = mock_executor.check_connection()

        assert result is False


class TestGetAccountActivities:
    """Test get_account_activities method with retry logic."""

    @pytest.fixture()
    def mock_executor(self):
        """Create AlpacaExecutor with mocked dependencies."""
        with (
            patch("apps.execution_gateway.alpaca_client.ALPACA_AVAILABLE", True),
            patch("apps.execution_gateway.alpaca_client.TradingClient"),
            patch("apps.execution_gateway.alpaca_client.StockHistoricalDataClient"),
        ):
            executor = AlpacaExecutor(
                api_key="test_key",
                secret_key="test_secret",
                base_url="https://paper-api.alpaca.markets",
            )
            yield executor

    def test_get_account_activities_success(self, mock_executor):
        """Should return activities list on success."""
        activities = [
            {"id": "act_1", "activity_type": "FILL", "symbol": "AAPL"},
            {"id": "act_2", "activity_type": "FILL", "symbol": "MSFT"},
        ]

        with patch("apps.execution_gateway.alpaca_client.httpx.get") as mock_get:
            mock_response = MagicMock()
            mock_response.json.return_value = activities
            mock_response.raise_for_status = Mock()
            mock_get.return_value = mock_response

            result = mock_executor.get_account_activities("FILL", page_size=100)

            assert len(result) == 2
            assert result[0]["symbol"] == "AAPL"

    def test_get_account_activities_retries_on_network_error(self, mock_executor):
        """Should retry up to 3 times on network errors."""
        with patch("apps.execution_gateway.alpaca_client.httpx.get") as mock_get:
            mock_get.side_effect = httpx.ConnectTimeout("Timeout")

            with pytest.raises(AlpacaConnectionError, match="Network error"):
                mock_executor.get_account_activities("FILL")

            assert mock_get.call_count == 3

    def test_get_account_activities_returns_empty_on_invalid_response(self, mock_executor):
        """Should return empty list for non-list responses."""
        with patch("apps.execution_gateway.alpaca_client.httpx.get") as mock_get:
            mock_response = MagicMock()
            mock_response.json.return_value = {"error": "Invalid"}
            mock_response.raise_for_status = Mock()
            mock_get.return_value = mock_response

            result = mock_executor.get_account_activities("FILL")

            assert result == []

    def test_get_account_activities_formats_datetime_params(self, mock_executor):
        """Should format datetime params to ISO with Z suffix."""
        after_dt = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
        until_dt = datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC)

        with patch("apps.execution_gateway.alpaca_client.httpx.get") as mock_get:
            mock_response = MagicMock()
            mock_response.json.return_value = []
            mock_response.raise_for_status = Mock()
            mock_get.return_value = mock_response

            mock_executor.get_account_activities("FILL", after=after_dt, until=until_dt)

            call_params = mock_get.call_args.kwargs["params"]
            assert call_params["after"] == "2024-01-01T12:00:00Z"
            assert call_params["until"] == "2024-01-15T12:00:00Z"


class TestGetLatestQuotes:
    """Test get_latest_quotes method with retry logic."""

    @pytest.fixture()
    def mock_executor(self):
        """Create AlpacaExecutor with mocked dependencies."""
        with (
            patch("apps.execution_gateway.alpaca_client.ALPACA_AVAILABLE", True),
            patch("apps.execution_gateway.alpaca_client.TradingClient"),
            patch("apps.execution_gateway.alpaca_client.StockHistoricalDataClient"),
        ):
            return AlpacaExecutor(api_key="test", secret_key="test")

    def test_get_latest_quotes_empty_symbols_raises(self, mock_executor):
        """Should raise ValueError for empty symbols list."""
        with pytest.raises(ValueError, match="symbols list cannot be empty"):
            mock_executor.get_latest_quotes([])

    def test_get_latest_quotes_returns_quotes(self, mock_executor):
        """Should return dict of quotes with Decimal prices."""
        mock_quote_aapl = MagicMock()
        mock_quote_aapl.ap = 150.75
        mock_quote_aapl.bp = 150.50
        mock_quote_aapl.timestamp = datetime.now(UTC)

        mock_quote_msft = MagicMock()
        mock_quote_msft.ap = 380.25
        mock_quote_msft.bp = 380.00
        mock_quote_msft.timestamp = datetime.now(UTC)

        quotes_data = {"AAPL": mock_quote_aapl, "MSFT": mock_quote_msft}

        mock_executor.data_client.get_stock_latest_quote = Mock(return_value=quotes_data)

        result = mock_executor.get_latest_quotes(["AAPL", "MSFT"])

        assert "AAPL" in result
        assert "MSFT" in result
        assert isinstance(result["AAPL"]["ask_price"], Decimal)
        assert isinstance(result["AAPL"]["bid_price"], Decimal)
        assert result["AAPL"]["last_price"] == (Decimal("150.75") + Decimal("150.50")) / Decimal(
            "2"
        )

    def test_get_latest_quotes_handles_missing_bid_ask(self, mock_executor):
        """Should handle None values for bid/ask prices."""
        mock_quote = MagicMock()
        mock_quote.ap = None
        mock_quote.bp = None
        mock_quote.timestamp = datetime.now(UTC)

        quotes_data = {"AAPL": mock_quote}

        mock_executor.data_client.get_stock_latest_quote = Mock(return_value=quotes_data)

        result = mock_executor.get_latest_quotes(["AAPL"])

        assert result["AAPL"]["ask_price"] is None
        assert result["AAPL"]["bid_price"] is None
        assert result["AAPL"]["last_price"] is None

    def test_get_latest_quotes_retries_on_connection_error(self, mock_executor):
        """Should retry up to 3 times on connection errors."""
        mock_executor.data_client.get_stock_latest_quote = Mock(
            side_effect=httpx.ConnectTimeout("Timeout")
        )

        with pytest.raises(AlpacaConnectionError, match="Network error"):
            mock_executor.get_latest_quotes(["AAPL"])

        assert mock_executor.data_client.get_stock_latest_quote.call_count == 3

    def test_get_latest_quotes_raises_on_api_error(self, mock_executor):
        """Should raise AlpacaConnectionError on API errors."""

        class MockAPIError(Exception):
            def __init__(self):
                super().__init__("Rate limited")
                self.status_code = 429

        with patch("apps.execution_gateway.alpaca_client.AlpacaAPIError", MockAPIError):
            mock_executor.data_client.get_stock_latest_quote = Mock(side_effect=MockAPIError())

            with pytest.raises(AlpacaConnectionError, match="Failed to fetch quotes"):
                mock_executor.get_latest_quotes(["AAPL"])

    def test_get_latest_quotes_handles_unexpected_errors(self, mock_executor):
        """Should raise AlpacaConnectionError on unexpected errors."""
        mock_executor.data_client.get_stock_latest_quote = Mock(
            side_effect=RuntimeError("Unexpected error")
        )

        with pytest.raises(AlpacaConnectionError, match="Unexpected error"):
            mock_executor.get_latest_quotes(["AAPL"])


class TestActivitiesBaseUrl:
    """Test _activities_base_url helper method."""

    @pytest.fixture()
    def mock_executor(self):
        """Create AlpacaExecutor with mocked dependencies."""
        with (
            patch("apps.execution_gateway.alpaca_client.ALPACA_AVAILABLE", True),
            patch("apps.execution_gateway.alpaca_client.TradingClient"),
            patch("apps.execution_gateway.alpaca_client.StockHistoricalDataClient"),
        ):
            return AlpacaExecutor(api_key="test", secret_key="test")

    def test_activities_base_url_strips_v2(self, mock_executor):
        """Should strip /v2 suffix from base URL."""
        mock_executor.base_url = "https://paper-api.alpaca.markets/v2"

        result = mock_executor._activities_base_url()

        assert result == "https://paper-api.alpaca.markets"

    def test_activities_base_url_strips_trailing_slash(self, mock_executor):
        """Should strip trailing slash from base URL."""
        mock_executor.base_url = "https://paper-api.alpaca.markets/"

        result = mock_executor._activities_base_url()

        assert result == "https://paper-api.alpaca.markets"

    def test_activities_base_url_handles_no_v2(self, mock_executor):
        """Should return base URL unchanged if no /v2 suffix."""
        mock_executor.base_url = "https://paper-api.alpaca.markets"

        result = mock_executor._activities_base_url()

        assert result == "https://paper-api.alpaca.markets"
