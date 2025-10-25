"""
Comprehensive unit tests for AlpacaExecutor.

Tests cover:
- Order submission (all order types: market, limit, stop, stop_limit)
- Error handling and classification (retryable vs non-retryable)
- Retry logic with exponential backoff
- Order status queries
- Order cancellation
- Account information retrieval
- Latest quotes fetching
- Connection health checks
- Edge cases and error conditions

Target: Bring alpaca_client.py coverage from 18% to 90%+

See Also:
    - /docs/STANDARDS/TESTING.md - Testing standards
    - /docs/IMPLEMENTATION_GUIDES/p0t4-execution-gateway.md - Execution gateway design
    - /docs/ADRs/0005-execution-gateway-architecture.md - Architecture decisions
"""

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import Mock, patch

import pytest

from apps.execution_gateway.alpaca_client import (
    ALPACA_AVAILABLE,
    AlpacaClientError,
    AlpacaConnectionError,
    AlpacaExecutor,
    AlpacaRejectionError,
    AlpacaValidationError,
)
from apps.execution_gateway.schemas import OrderRequest


def create_mock_alpaca_error(message: str, status_code: int):
    """
    Create a mock AlpacaAPIError with status_code property.

    Since AlpacaAPIError has status_code as a read-only property, we need
    to create a subclass that properly sets it.

    Args:
        message: Error message
        status_code: HTTP status code (400, 403, 422, 500, etc.)

    Returns:
        Exception instance with status_code property
    """
    try:
        from alpaca.common.exceptions import APIError as AlpacaAPIError

        # Create a subclass that allows setting status_code
        class TestAlpacaAPIError(AlpacaAPIError):
            def __init__(self, msg: str, code: int):
                # AlpacaAPIError might have complex initialization
                # Try to initialize it simply
                super().__init__(msg)  # type: ignore[no-untyped-call]
                # Override the status_code property by setting the underlying attribute
                # This is a hack but necessary for testing
                object.__setattr__(self, "_status_code", code)

            @property
            def status_code(self) -> int:
                return getattr(self, "_status_code", 0)

        return TestAlpacaAPIError(message, status_code)

    except (ImportError, TypeError):
        # Fallback if alpaca-py not available or initialization fails
        class MockAlpacaAPIError(Exception):
            def __init__(self, msg: str, code: int):
                super().__init__(msg)
                self._status_code = code

            @property
            def status_code(self) -> int:
                return self._status_code

        return MockAlpacaAPIError(message, status_code)


class TestAlpacaExecutorInitialization:
    """Test AlpacaExecutor initialization and configuration."""

    def test_initialization_success_with_paper_trading(self):
        """
        Should initialize successfully with valid credentials for paper trading.

        Paper trading is the default mode for development and testing.
        """
        if not ALPACA_AVAILABLE:
            pytest.skip("alpaca-py not installed")

        with (
            patch("apps.execution_gateway.alpaca_client.TradingClient") as mock_trading_client,
            patch(
                "apps.execution_gateway.alpaca_client.StockHistoricalDataClient"
            ) as mock_data_client,
        ):
            executor = AlpacaExecutor(
                api_key="test_key",
                secret_key="test_secret",
                base_url="https://paper-api.alpaca.markets",
                paper=True,
            )

            assert executor.api_key == "test_key"
            assert executor.secret_key == "test_secret"
            assert executor.base_url == "https://paper-api.alpaca.markets"
            assert executor.paper is True

            # Verify clients were initialized
            mock_trading_client.assert_called_once_with(
                api_key="test_key", secret_key="test_secret", paper=True
            )
            mock_data_client.assert_called_once_with(api_key="test_key", secret_key="test_secret")

    def test_initialization_with_live_trading_mode(self):
        """Should initialize with live trading mode when paper=False."""
        if not ALPACA_AVAILABLE:
            pytest.skip("alpaca-py not installed")

        with (
            patch("apps.execution_gateway.alpaca_client.TradingClient") as mock_trading_client,
            patch("apps.execution_gateway.alpaca_client.StockHistoricalDataClient"),
        ):
            executor = AlpacaExecutor(
                api_key="live_key",
                secret_key="live_secret",
                base_url="https://api.alpaca.markets",
                paper=False,
            )

            assert executor.paper is False
            mock_trading_client.assert_called_once_with(
                api_key="live_key", secret_key="live_secret", paper=False
            )

    def test_initialization_failure_when_alpaca_not_installed(self):
        """Should raise ImportError when alpaca-py package is not installed."""
        with patch("apps.execution_gateway.alpaca_client.ALPACA_AVAILABLE", False):
            with pytest.raises(ImportError, match="alpaca-py package is required"):
                AlpacaExecutor(api_key="test", secret_key="test")




class TestAlpacaExecutorOrderSubmission:
    """Test order submission with different order types and scenarios."""

    @pytest.fixture()
    def executor(self):
        """Create AlpacaExecutor with mocked Alpaca clients."""
        if not ALPACA_AVAILABLE:
            pytest.skip("alpaca-py not installed")

        with (
            patch("apps.execution_gateway.alpaca_client.TradingClient") as mock_trading,
            patch("apps.execution_gateway.alpaca_client.StockHistoricalDataClient"),
        ):
            executor = AlpacaExecutor(api_key="test_key", secret_key="test_secret", paper=True)
            executor.client = mock_trading.return_value
            yield executor

    @pytest.fixture()
    def mock_order_response(self):
        """Create mock Order object for successful responses."""
        mock_order = Mock()
        mock_order.id = "broker_order_123"
        mock_order.client_order_id = "client_order_abc"
        mock_order.symbol = "AAPL"
        mock_order.side = Mock(value="buy")
        mock_order.qty = 100
        mock_order.order_type = Mock(value="market")
        mock_order.status = Mock(value="accepted")
        mock_order.created_at = datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC)
        mock_order.limit_price = None
        mock_order.stop_price = None
        return mock_order

    def test_submit_market_order_success(self, executor, mock_order_response):
        """Should successfully submit market order and return order dict."""
        # Mock the Order class for isinstance check
        with patch("apps.execution_gateway.alpaca_client.Order", type(mock_order_response)):
            executor.client.submit_order = Mock(return_value=mock_order_response)

            order_request = OrderRequest(symbol="AAPL", side="buy", qty=100, order_type="market")
            result = executor.submit_order(order_request, "client_order_abc")

            # Verify result structure
            assert result["id"] == "broker_order_123"
            assert result["client_order_id"] == "client_order_abc"
            assert result["symbol"] == "AAPL"
            assert result["side"] == "buy"
            assert result["qty"] == 100.0
            assert result["order_type"] == "market"
            assert result["status"] == "accepted"
            assert result["limit_price"] is None
            assert result["stop_price"] is None

            # Verify Alpaca API was called
            executor.client.submit_order.assert_called_once()

    def test_submit_limit_order_success(self, executor, mock_order_response):
        """Should successfully submit limit order with limit price."""
        mock_order_response.order_type.value = "limit"
        mock_order_response.limit_price = 150.50

        with patch("apps.execution_gateway.alpaca_client.Order", type(mock_order_response)):
            executor.client.submit_order = Mock(return_value=mock_order_response)

            order_request = OrderRequest(
                symbol="AAPL",
                side="buy",
                qty=100,
                order_type="limit",
                limit_price=Decimal("150.50"),
            )
            result = executor.submit_order(order_request, "client_order_abc")

            assert result["order_type"] == "limit"
            assert result["limit_price"] == 150.50
            assert result["stop_price"] is None

    def test_submit_stop_order_success(self, executor, mock_order_response):
        """Should successfully submit stop order with stop price."""
        mock_order_response.order_type.value = "stop"
        mock_order_response.stop_price = 145.00

        with patch("apps.execution_gateway.alpaca_client.Order", type(mock_order_response)):
            executor.client.submit_order = Mock(return_value=mock_order_response)

            order_request = OrderRequest(
                symbol="AAPL", side="sell", qty=100, order_type="stop", stop_price=Decimal("145.00")
            )
            result = executor.submit_order(order_request, "client_order_abc")

            assert result["order_type"] == "stop"
            assert result["stop_price"] == 145.00
            assert result["limit_price"] is None

    def test_submit_stop_limit_order_success(self, executor, mock_order_response):
        """Should successfully submit stop-limit order with both prices."""
        mock_order_response.order_type.value = "stop_limit"
        mock_order_response.limit_price = 148.00
        mock_order_response.stop_price = 145.00

        with patch("apps.execution_gateway.alpaca_client.Order", type(mock_order_response)):
            executor.client.submit_order = Mock(return_value=mock_order_response)

            order_request = OrderRequest(
                symbol="AAPL",
                side="sell",
                qty=100,
                order_type="stop_limit",
                limit_price=Decimal("148.00"),
                stop_price=Decimal("145.00"),
            )
            result = executor.submit_order(order_request, "client_order_abc")

            assert result["order_type"] == "stop_limit"
            assert result["limit_price"] == 148.00
            assert result["stop_price"] == 145.00

    def test_submit_order_with_different_time_in_force(self, executor, mock_order_response):
        """Should handle different time_in_force values (day, gtc, ioc, fok)."""
        with patch("apps.execution_gateway.alpaca_client.Order", type(mock_order_response)):
            executor.client.submit_order = Mock(return_value=mock_order_response)

            # Test GTC (Good Till Cancelled)
            order_request = OrderRequest(
                symbol="AAPL", side="buy", qty=100, order_type="market", time_in_force="gtc"
            )
            executor.submit_order(order_request, "client_order_abc")

            # Verify order was submitted (time_in_force handled in _build_alpaca_request)
            executor.client.submit_order.assert_called()

    def test_submit_order_validation_error_400(self, executor):
        """
        Should raise AlpacaValidationError for 400 Bad Request (non-retryable).

        400 errors indicate invalid order parameters (e.g., negative qty,
        invalid symbol). These should not be retried.
        """
        api_error = create_mock_alpaca_error("Invalid quantity: must be positive", 400)
        executor.client.submit_order = Mock(side_effect=api_error)

        order_request = OrderRequest(symbol="AAPL", side="buy", qty=100, order_type="market")

        with pytest.raises(AlpacaValidationError, match="Invalid order"):
            executor.submit_order(order_request, "client_order_abc")


        assert "must be positive" in str(exc_info.value)

    def test_submit_order_rejection_error_422(self, executor):
        """
        Should raise AlpacaRejectionError for 422 Unprocessable Entity (non-retryable).

        422 errors indicate order was rejected by broker (e.g., insufficient funds,
        pattern day trader violation). These should not be retried.
        """
        api_error = create_mock_alpaca_error("Insufficient buying power", 422)
        executor.client.submit_order = Mock(side_effect=api_error)

        order_request = OrderRequest(symbol="AAPL", side="buy", qty=1000000, order_type="market")

        with pytest.raises(AlpacaRejectionError, match="Order rejected"):
            executor.submit_order(order_request, "client_order_abc")


        assert "Insufficient buying power" in str(exc_info.value)

    def test_submit_order_rejection_error_403(self, executor):
        """
        Should raise AlpacaRejectionError for 403 Forbidden (non-retryable).

        403 errors indicate permission denied (e.g., account restricted,
        trading halted for symbol). These should not be retried.
        """
        api_error = create_mock_alpaca_error("Account is restricted from trading", 403)
        executor.client.submit_order = Mock(side_effect=api_error)

        order_request = OrderRequest(symbol="AAPL", side="buy", qty=100, order_type="market")

        with pytest.raises(AlpacaRejectionError, match="Order rejected"):
            executor.submit_order(order_request, "client_order_abc")


        assert "restricted" in str(exc_info.value)

    def test_submit_order_connection_error_retryable(self, executor, mock_order_response):
        """
        Should retry on connection errors (500, 503) with exponential backoff.

        Connection errors are transient and should be retried up to 3 times
        with exponential backoff (2s, 4s, 8s).
        """
        api_error = create_mock_alpaca_error("Service temporarily unavailable", 503)

        # Fail twice, then succeed on third attempt
        executor.client.submit_order = Mock(side_effect=[api_error, api_error, mock_order_response])

        # Stub sleep to avoid 6s delay in tests
        with (
            patch("apps.execution_gateway.alpaca_client.Order", type(mock_order_response)),
            patch("time.sleep"),
        ):
            order_request = OrderRequest(symbol="AAPL", side="buy", qty=100, order_type="market")

            # Should succeed after retries
            result = executor.submit_order(order_request, "client_order_abc")

            assert result["id"] == "broker_order_123"
            assert executor.client.submit_order.call_count == 3  # 2 failures + 1 success

    def test_submit_order_connection_error_max_retries_exceeded(self, executor):
        """
        Should raise AlpacaConnectionError after 3 failed retry attempts.

        If all 3 retry attempts fail, the error should be propagated to caller.
        """
        api_error = create_mock_alpaca_error("Connection timeout", 500)
        executor.client.submit_order = Mock(side_effect=api_error)

        order_request = OrderRequest(symbol="AAPL", side="buy", qty=100, order_type="market")

        # Stub sleep to avoid 14s delay in tests (2s + 4s + 8s)
        with patch("time.sleep"):
            with pytest.raises(AlpacaConnectionError, match="Connection timeout"):
                executor.submit_order(order_request, "client_order_abc")


        assert executor.client.submit_order.call_count == 3  # Max retries reached

    def test_submit_order_unexpected_exception(self, executor):
        """
        Should raise AlpacaClientError for unexpected exceptions.

        Unexpected errors (not AlpacaAPIError) should be wrapped in
        AlpacaClientError for consistent error handling.
        """
        executor.client.submit_order = Mock(side_effect=ValueError("Unexpected error"))

        order_request = OrderRequest(symbol="AAPL", side="buy", qty=100, order_type="market")

        with pytest.raises(AlpacaClientError, match="Unexpected error"):
            executor.submit_order(order_request, "client_order_abc")



    def test_submit_order_handles_qty_none_from_alpaca(self, executor):
        """
        Should handle qty=None from Alpaca API response gracefully.

        Alpaca SDK types qty as str|float|None, so we need to handle None case.
        """
        mock_order = Mock()
        mock_order.id = "order_123"
        mock_order.client_order_id = "client_abc"
        mock_order.symbol = "AAPL"
        mock_order.side = Mock(value="buy")
        mock_order.qty = None  # Edge case: qty is None
        mock_order.order_type = Mock(value="market")
        mock_order.status = Mock(value="pending")
        mock_order.created_at = datetime.now(UTC)
        mock_order.limit_price = None
        mock_order.stop_price = None

        with patch("apps.execution_gateway.alpaca_client.Order", type(mock_order)):
            executor.client.submit_order = Mock(return_value=mock_order)

            order_request = OrderRequest(symbol="AAPL", side="buy", qty=100, order_type="market")
            result = executor.submit_order(order_request, "client_abc")

            # qty=None should be converted to 0.0
            assert result["qty"] == 0.0


class TestAlpacaExecutorBuildRequest:
    """Test _build_alpaca_request helper method for different order types."""

    @pytest.fixture()
    def executor(self):
        """Create AlpacaExecutor with mocked clients."""
        if not ALPACA_AVAILABLE:
            pytest.skip("alpaca-py not installed")

        with (
            patch("apps.execution_gateway.alpaca_client.TradingClient"),
            patch("apps.execution_gateway.alpaca_client.StockHistoricalDataClient"),
        ):
            yield AlpacaExecutor(api_key="test", secret_key="test", paper=True)

    def test_build_market_order_request(self, executor):
        """Should build MarketOrderRequest with correct parameters."""
        order = OrderRequest(symbol="AAPL", side="buy", qty=100, order_type="market")

        with (
            patch("apps.execution_gateway.alpaca_client.MarketOrderRequest") as mock_market,
            patch("apps.execution_gateway.alpaca_client.OrderSide") as mock_side,
            patch("apps.execution_gateway.alpaca_client.TimeInForce") as mock_tif,
        ):
            mock_side.BUY = "BUY"
            mock_tif.DAY = "DAY"

            executor._build_alpaca_request(order, "client_123")

            mock_market.assert_called_once()

    def test_build_limit_order_request_missing_limit_price(self, executor):
        """Should raise ValueError when limit_price missing for limit order."""
        order = OrderRequest(
            symbol="AAPL",
            side="buy",
            qty=100,
            order_type="limit",
            limit_price=None,  # Missing required field
        )

        with pytest.raises(ValueError, match="limit_price is required for limit orders"):
            executor._build_alpaca_request(order, "client_123")



    def test_build_stop_order_request_missing_stop_price(self, executor):
        """Should raise ValueError when stop_price missing for stop order."""
        order = OrderRequest(
            symbol="AAPL",
            side="sell",
            qty=100,
            order_type="stop",
            stop_price=None,  # Missing required field
        )

        with pytest.raises(ValueError, match="stop_price is required for stop orders"):
            executor._build_alpaca_request(order, "client_123")



    def test_build_stop_limit_order_request_missing_prices(self, executor):
        """Should raise ValueError when prices missing for stop_limit order."""
        order = OrderRequest(
            symbol="AAPL",
            side="sell",
            qty=100,
            order_type="stop_limit",
            limit_price=None,
            stop_price=None,  # Both missing
        )

        with pytest.raises(ValueError, match="Both limit_price and stop_price are required"):
            executor._build_alpaca_request(order, "client_123")



    def test_build_request_unsupported_order_type(self, executor):
        """
        Should raise ValueError for unsupported order type.

        Note: We need to bypass Pydantic validation to test the runtime check,
        since Pydantic validates order_type at construction time.
        """
        # Create order with valid type first, then modify directly to bypass validation
        order = OrderRequest(symbol="AAPL", side="buy", qty=100, order_type="market")

        # Bypass Pydantic by modifying __dict__ directly (for testing only)
        order.__dict__["order_type"] = "trailing_stop"

        with pytest.raises(ValueError, match="Unsupported order type"):
            executor._build_alpaca_request(order, "client_123")




class TestAlpacaExecutorOrderQuery:
    """Test order status query methods."""

    @pytest.fixture()
    def executor(self):
        """Create AlpacaExecutor with mocked clients."""
        if not ALPACA_AVAILABLE:
            pytest.skip("alpaca-py not installed")

        with (
            patch("apps.execution_gateway.alpaca_client.TradingClient") as mock_trading,
            patch("apps.execution_gateway.alpaca_client.StockHistoricalDataClient"),
        ):
            executor = AlpacaExecutor(api_key="test", secret_key="test", paper=True)
            executor.client = mock_trading.return_value
            yield executor

    def test_get_order_by_client_id_success(self, executor):
        """Should return order dict when order found."""
        mock_order = Mock()
        mock_order.id = "broker_123"
        mock_order.client_order_id = "client_abc"
        mock_order.symbol = "AAPL"
        mock_order.side = Mock(value="buy")
        mock_order.qty = 100
        mock_order.order_type = Mock(value="market")
        mock_order.status = Mock(value="filled")
        mock_order.filled_qty = 100
        mock_order.filled_avg_price = 150.50
        mock_order.created_at = datetime.now(UTC)
        mock_order.updated_at = datetime.now(UTC)

        with patch("apps.execution_gateway.alpaca_client.Order", type(mock_order)):
            executor.client.get_order_by_client_id = Mock(return_value=mock_order)

            result = executor.get_order_by_client_id("client_abc")

            assert result is not None
            assert result["id"] == "broker_123"
            assert result["client_order_id"] == "client_abc"
            assert result["status"] == "filled"
            assert result["filled_qty"] == 100.0
            assert result["filled_avg_price"] == 150.50

    def test_get_order_by_client_id_not_found_404(self, executor):
        """Should return None when order not found (404)."""
        api_error = create_mock_alpaca_error("Order not found", 404)
        executor.client.get_order_by_client_id = Mock(side_effect=api_error)

        result = executor.get_order_by_client_id("nonexistent_id")

        assert result is None

    def test_get_order_by_client_id_connection_error(self, executor):
        """Should raise AlpacaConnectionError for connection failures."""
        api_error = create_mock_alpaca_error("Connection timeout", 500)
        executor.client.get_order_by_client_id = Mock(side_effect=api_error)

        with pytest.raises(AlpacaConnectionError):
            executor.get_order_by_client_id("client_abc")

    def test_get_order_by_client_id_handles_filled_qty_none(self, executor):
        """Should handle filled_qty=None gracefully (converts to 0)."""
        mock_order = Mock()
        mock_order.id = "broker_123"
        mock_order.client_order_id = "client_abc"
        mock_order.symbol = "AAPL"
        mock_order.side = Mock(value="buy")
        mock_order.qty = 100
        mock_order.order_type = Mock(value="market")
        mock_order.status = Mock(value="pending")
        mock_order.filled_qty = None  # Not filled yet
        mock_order.filled_avg_price = None
        mock_order.created_at = datetime.now(UTC)
        mock_order.updated_at = datetime.now(UTC)

        with patch("apps.execution_gateway.alpaca_client.Order", type(mock_order)):
            executor.client.get_order_by_client_id = Mock(return_value=mock_order)

            result = executor.get_order_by_client_id("client_abc")

            assert result["filled_qty"] == 0.0
            assert result["filled_avg_price"] is None


class TestAlpacaExecutorOrderCancellation:
    """Test order cancellation."""

    @pytest.fixture()
    def executor(self):
        """Create AlpacaExecutor with mocked clients."""
        if not ALPACA_AVAILABLE:
            pytest.skip("alpaca-py not installed")

        with (
            patch("apps.execution_gateway.alpaca_client.TradingClient") as mock_trading,
            patch("apps.execution_gateway.alpaca_client.StockHistoricalDataClient"),
        ):
            executor = AlpacaExecutor(api_key="test", secret_key="test", paper=True)
            executor.client = mock_trading.return_value
            yield executor

    def test_cancel_order_success(self, executor):
        """Should successfully cancel order and return True."""
        executor.client.cancel_order_by_id = Mock()

        result = executor.cancel_order("broker_order_123")

        assert result is True
        executor.client.cancel_order_by_id.assert_called_once_with("broker_order_123")

    def test_cancel_order_already_filled_422(self, executor):
        """Should raise AlpacaRejectionError when order already filled (422)."""
        api_error = create_mock_alpaca_error("Order already filled, cannot cancel", 422)
        executor.client.cancel_order_by_id = Mock(side_effect=api_error)

        with pytest.raises(AlpacaRejectionError, match="cannot be cancelled"):
            executor.cancel_order("broker_order_123")



    def test_cancel_order_connection_error(self, executor):
        """Should raise AlpacaConnectionError for connection failures."""
        api_error = create_mock_alpaca_error("Connection timeout", 500)
        executor.client.cancel_order_by_id = Mock(side_effect=api_error)

        with pytest.raises(AlpacaConnectionError):
            executor.cancel_order("broker_order_123")


class TestAlpacaExecutorConnectionHealth:
    """Test connection health check."""

    @pytest.fixture()
    def executor(self):
        """Create AlpacaExecutor with mocked clients."""
        if not ALPACA_AVAILABLE:
            pytest.skip("alpaca-py not installed")

        with (
            patch("apps.execution_gateway.alpaca_client.TradingClient") as mock_trading,
            patch("apps.execution_gateway.alpaca_client.StockHistoricalDataClient"),
        ):
            executor = AlpacaExecutor(api_key="test", secret_key="test", paper=True)
            executor.client = mock_trading.return_value
            yield executor

    def test_check_connection_success(self, executor):
        """Should return True when connection is healthy."""
        mock_account = Mock()
        executor.client.get_account = Mock(return_value=mock_account)

        result = executor.check_connection()

        assert result is True

    def test_check_connection_failure(self, executor):
        """Should return False when connection fails."""
        executor.client.get_account = Mock(side_effect=Exception("Network error"))

        result = executor.check_connection()

        assert result is False


class TestAlpacaExecutorAccountInfo:
    """Test account information retrieval."""

    @pytest.fixture()
    def executor(self):
        """Create AlpacaExecutor with mocked clients."""
        if not ALPACA_AVAILABLE:
            pytest.skip("alpaca-py not installed")

        with (
            patch("apps.execution_gateway.alpaca_client.TradingClient") as mock_trading,
            patch("apps.execution_gateway.alpaca_client.StockHistoricalDataClient"),
        ):
            executor = AlpacaExecutor(api_key="test", secret_key="test", paper=True)
            executor.client = mock_trading.return_value
            yield executor

    def test_get_account_info_success(self, executor):
        """Should return account info dict when successful."""
        mock_account = Mock()
        mock_account.account_number = "ACC123"
        mock_account.status = Mock(value="ACTIVE")
        mock_account.currency = "USD"
        mock_account.buying_power = 100000.00
        mock_account.cash = 50000.00
        mock_account.portfolio_value = 100000.00
        mock_account.pattern_day_trader = False
        mock_account.trading_blocked = False
        mock_account.transfers_blocked = False

        with patch("apps.execution_gateway.alpaca_client.TradeAccount", type(mock_account)):
            executor.client.get_account = Mock(return_value=mock_account)

            result = executor.get_account_info()

            assert result is not None
            assert result["account_number"] == "ACC123"
            assert result["status"] == "ACTIVE"
            assert result["buying_power"] == 100000.00
            assert result["pattern_day_trader"] is False

    def test_get_account_info_exception(self, executor):
        """Should return None when exception occurs."""
        executor.client.get_account = Mock(side_effect=Exception("API error"))

        result = executor.get_account_info()

        assert result is None


class TestAlpacaExecutorLatestQuotes:
    """Test latest market quotes fetching."""

    @pytest.fixture()
    def executor(self):
        """Create AlpacaExecutor with mocked clients."""
        if not ALPACA_AVAILABLE:
            pytest.skip("alpaca-py not installed")

        with (
            patch("apps.execution_gateway.alpaca_client.TradingClient"),
            patch("apps.execution_gateway.alpaca_client.StockHistoricalDataClient") as mock_data,
        ):
            executor = AlpacaExecutor(api_key="test", secret_key="test", paper=True)
            executor.data_client = mock_data.return_value
            yield executor

    def test_get_latest_quotes_success(self, executor):
        """Should return quotes dict with bid, ask, last prices."""
        mock_quote = Mock()
        mock_quote.ap = 152.75  # Ask price
        mock_quote.bp = 152.74  # Bid price
        mock_quote.timestamp = datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC)

        mock_quotes_data = {"AAPL": mock_quote, "MSFT": mock_quote}

        executor.data_client.get_stock_latest_quote = Mock(return_value=mock_quotes_data)

        result = executor.get_latest_quotes(["AAPL", "MSFT"])

        assert "AAPL" in result
        assert result["AAPL"]["ask_price"] == Decimal("152.75")
        assert result["AAPL"]["bid_price"] == Decimal("152.74")
        assert result["AAPL"]["last_price"] == Decimal("152.745")  # Mid-quote
        assert result["AAPL"]["timestamp"] is not None

    def test_get_latest_quotes_empty_symbols_list(self, executor):
        """Should raise ValueError when symbols list is empty."""
        with pytest.raises(ValueError, match="symbols list cannot be empty"):
            executor.get_latest_quotes([])



    def test_get_latest_quotes_missing_bid_ask(self, executor):
        """Should handle missing bid/ask prices gracefully."""
        mock_quote = Mock()
        # Remove bid/ask attributes
        del mock_quote.ap
        del mock_quote.bp
        mock_quote.timestamp = datetime.now(UTC)

        mock_quotes_data = {"AAPL": mock_quote}

        executor.data_client.get_stock_latest_quote = Mock(return_value=mock_quotes_data)

        result = executor.get_latest_quotes(["AAPL"])

        assert result["AAPL"]["ask_price"] is None
        assert result["AAPL"]["bid_price"] is None
        assert result["AAPL"]["last_price"] is None

    def test_get_latest_quotes_symbol_not_found(self, executor):
        """Should handle symbols not found in response."""
        mock_quote = Mock()
        mock_quote.ap = 152.75
        mock_quote.bp = 152.74
        mock_quote.timestamp = datetime.now(UTC)

        # Only AAPL in response, TSLA missing
        mock_quotes_data = {"AAPL": mock_quote}

        executor.data_client.get_stock_latest_quote = Mock(return_value=mock_quotes_data)

        result = executor.get_latest_quotes(["AAPL", "TSLA"])

        assert "AAPL" in result
        assert "TSLA" not in result  # Missing symbol omitted

    def test_get_latest_quotes_api_error(self, executor):
        """Should raise AlpacaConnectionError on API error."""
        api_error = create_mock_alpaca_error("Rate limit exceeded", 429)
        executor.data_client.get_stock_latest_quote = Mock(side_effect=api_error)

        with pytest.raises(AlpacaConnectionError, match="Failed to fetch quotes"):
            executor.get_latest_quotes(["AAPL"])



    def test_get_latest_quotes_unexpected_exception(self, executor):
        """Should raise AlpacaConnectionError on unexpected exception."""
        executor.data_client.get_stock_latest_quote = Mock(side_effect=ValueError("Unexpected"))

        with pytest.raises(AlpacaConnectionError, match="Unexpected error fetching quotes"):
            executor.get_latest_quotes(["AAPL"])



    def test_get_latest_quotes_retry_on_connection_error(self, executor):
        """Should retry up to 3 times on connection errors."""
        mock_quote = Mock()
        mock_quote.ap = 152.75
        mock_quote.bp = 152.74
        mock_quote.timestamp = datetime.now(UTC)

        api_error = create_mock_alpaca_error("Temporary error", 503)

        # Fail twice, succeed on third attempt
        executor.data_client.get_stock_latest_quote = Mock(
            side_effect=[api_error, api_error, {"AAPL": mock_quote}]
        )

        # Stub sleep to avoid 6s delay in tests
        with patch("time.sleep"):
            result = executor.get_latest_quotes(["AAPL"])

        assert "AAPL" in result
        assert executor.data_client.get_stock_latest_quote.call_count == 3
