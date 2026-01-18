"""
P0 Coverage Tests for AlpacaExecutor - Additional branch coverage to reach 95%+ target.

Coverage gaps addressed (67% → 95%):
- get_order_by_client_id returning None
- Unexpected response types from API
- Invalid status/limit values
- Pagination edge cases
- DateTime parsing edge cases
- Dict-type responses
- Market clock and positions edge cases
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import Mock, patch

import pytest

# Conditionally import based on availability
try:
    from alpaca.common.exceptions import APIError as RealAlpacaAPIError
    from alpaca.trading.models import Order, Position, TradeAccount
    ALPACA_AVAILABLE = True
except ImportError:
    ALPACA_AVAILABLE = False
    RealAlpacaAPIError = Exception
    Order = None
    Position = None
    TradeAccount = None


def create_mock_api_error(message: str, status_code: int | None = None):
    """Create a mock API error that inherits from the real AlpacaAPIError.

    The real AlpacaAPIError has status_code as a read-only property,
    so we dynamically create an instance with a writable _status_code.
    """
    # Create an instance of the real exception
    error = RealAlpacaAPIError(message)
    # Override the status_code by setting the private attribute
    # that the property reads from (this may vary by alpaca-py version)
    object.__setattr__(error, '_status_code', status_code)
    return error


# Helper to create test errors
class MockAlpacaAPIError(RealAlpacaAPIError):
    """Mock exception that inherits from real AlpacaAPIError for isinstance checks."""

    def __init__(self, message="", status_code=None):
        # Bypass the parent's __init__ that might be restrictive
        Exception.__init__(self, message)
        self._mock_status_code = status_code

    @property
    def status_code(self):
        return self._mock_status_code


# Use the mock for testing (it's compatible with isinstance checks in test code)
AlpacaAPIError = MockAlpacaAPIError

from apps.execution_gateway.alpaca_client import (
    AlpacaClientError,
    AlpacaConnectionError,
    AlpacaExecutor,
)


@pytest.fixture()
def mock_trading_client():
    """Create mock TradingClient."""
    return Mock()


@pytest.fixture()
def mock_data_client():
    """Create mock StockHistoricalDataClient."""
    return Mock()


@pytest.fixture()
def executor(mock_trading_client, mock_data_client):
    """Create AlpacaExecutor with mock clients."""
    with patch("apps.execution_gateway.alpaca_client.TradingClient", return_value=mock_trading_client):
        with patch("apps.execution_gateway.alpaca_client.StockHistoricalDataClient", return_value=mock_data_client):
            return AlpacaExecutor(
                api_key="test_key",
                secret_key="test_secret",
                paper=True,
            )


class TestGetOrderByClientId:
    """Tests for get_order_by_client_id edge cases."""

    def test_returns_none_when_order_not_found(self, executor, mock_trading_client):
        """Test handling when order not found (returns None)."""
        mock_trading_client.get_order_by_client_id.return_value = None

        result = executor.get_order_by_client_id("nonexistent_order")

        assert result is None

    def test_raises_on_unexpected_response_type(self, executor, mock_trading_client):
        """Test error when API returns unexpected type (not Order)."""
        # Return a dict instead of Order object
        mock_trading_client.get_order_by_client_id.return_value = {"unexpected": "dict"}

        with pytest.raises(AlpacaClientError, match="Unexpected response type"):
            executor.get_order_by_client_id("test_order")


class TestGetOrders:
    """Tests for get_orders edge cases."""

    def test_raises_on_invalid_limit(self, executor):
        """Test ValueError when limit is <= 0."""
        with pytest.raises(ValueError, match="limit must be positive"):
            executor.get_orders(limit=0)

        with pytest.raises(ValueError, match="limit must be positive"):
            executor.get_orders(limit=-5)

    def test_raises_on_invalid_status(self, executor, mock_trading_client):
        """Test ValueError when status is invalid."""
        with pytest.raises(ValueError, match="Unsupported status"):
            executor.get_orders(status="invalid_status_xyz")

    def test_handles_dict_response(self, executor, mock_trading_client):
        """Test handling when API returns dict with 'orders' key."""
        if not ALPACA_AVAILABLE:
            pytest.skip("alpaca-py not installed")

        mock_order = Mock(spec=Order)
        mock_order.id = "order_123"
        mock_order.client_order_id = "client_123"
        mock_order.symbol = "AAPL"
        mock_order.side = Mock(value="buy")
        mock_order.qty = Decimal("100")
        mock_order.order_type = Mock(value="limit")
        mock_order.status = Mock(value="filled")
        mock_order.filled_qty = Decimal("100")
        mock_order.filled_avg_price = Decimal("150.00")
        mock_order.created_at = datetime.now(UTC)
        mock_order.updated_at = datetime.now(UTC)
        mock_order.submitted_at = datetime.now(UTC)
        mock_order.limit_price = Decimal("150.00")
        mock_order.notional = None
        mock_order.filled_at = datetime.now(UTC)

        # Return dict with "orders" key instead of list
        mock_trading_client.get_orders.return_value = {"orders": [mock_order]}

        result = executor.get_orders()

        assert len(result) == 1
        assert result[0]["client_order_id"] == "client_123"

    def test_handles_duplicate_order_ids(self, executor, mock_trading_client):
        """Test deduplication when same order appears multiple times."""
        if not ALPACA_AVAILABLE:
            pytest.skip("alpaca-py not installed")

        mock_order = Mock(spec=Order)
        mock_order.id = "same_order_id"
        mock_order.client_order_id = "client_123"
        mock_order.symbol = "AAPL"
        mock_order.side = Mock(value="buy")
        mock_order.qty = Decimal("100")
        mock_order.order_type = Mock(value="limit")
        mock_order.status = Mock(value="filled")
        mock_order.filled_qty = Decimal("100")
        mock_order.filled_avg_price = Decimal("150.00")
        mock_order.created_at = datetime.now(UTC)
        mock_order.updated_at = datetime.now(UTC)
        mock_order.submitted_at = datetime.now(UTC)
        mock_order.limit_price = Decimal("150.00")
        mock_order.notional = None
        mock_order.filled_at = datetime.now(UTC)

        # Return same order twice in the list
        mock_trading_client.get_orders.return_value = [mock_order, mock_order]

        result = executor.get_orders()

        # Should deduplicate
        assert len(result) == 1

    def test_raises_on_unexpected_order_type(self, executor, mock_trading_client):
        """Test error when order list contains non-Order objects."""
        # Return a list containing a dict instead of Order
        mock_trading_client.get_orders.return_value = [{"not": "an_order"}]

        with pytest.raises(AlpacaClientError, match="Unexpected response type"):
            executor.get_orders()

    def test_pagination_breaks_on_no_created_at(self, executor, mock_trading_client):
        """Test pagination stops when orders have no created_at."""
        if not ALPACA_AVAILABLE:
            pytest.skip("alpaca-py not installed")

        mock_order = Mock(spec=Order)
        mock_order.id = "order_123"
        mock_order.client_order_id = "client_123"
        mock_order.symbol = "AAPL"
        mock_order.side = Mock(value="buy")
        mock_order.qty = Decimal("100")
        mock_order.order_type = Mock(value="limit")
        mock_order.status = Mock(value="filled")
        mock_order.filled_qty = Decimal("100")
        mock_order.filled_avg_price = Decimal("150.00")
        mock_order.created_at = None  # No created_at - pagination should stop
        mock_order.updated_at = None
        mock_order.submitted_at = None
        mock_order.limit_price = Decimal("150.00")
        mock_order.notional = None
        mock_order.filled_at = None

        mock_trading_client.get_orders.return_value = [mock_order]

        result = executor.get_orders()

        assert len(result) == 1

    def test_api_error_wrapped_as_connection_error(self, executor, mock_trading_client):
        """Test AlpacaAPIError is wrapped as AlpacaConnectionError."""
        # Create a MockAlpacaAPIError with status_code
        api_error = AlpacaAPIError("API failure", status_code=500)
        mock_trading_client.get_orders.side_effect = api_error

        with pytest.raises(AlpacaConnectionError, match="Error fetching orders"):
            executor.get_orders()


class TestParseDatetime:
    """Tests for _parse_datetime edge cases."""

    def test_parse_none_returns_none(self, executor):
        """Test None input returns None."""
        result = executor._parse_datetime(None)
        assert result is None

    def test_parse_naive_datetime_adds_utc(self, executor):
        """Test naive datetime gets UTC timezone added."""
        naive_dt = datetime(2026, 1, 15, 10, 30, 0)
        result = executor._parse_datetime(naive_dt)

        assert result is not None
        assert result.tzinfo == UTC

    def test_parse_aware_datetime_preserved(self, executor):
        """Test aware datetime is preserved."""
        aware_dt = datetime(2026, 1, 15, 10, 30, 0, tzinfo=UTC)
        result = executor._parse_datetime(aware_dt)

        assert result == aware_dt

    def test_parse_iso_string_with_z_suffix(self, executor):
        """Test ISO string with Z suffix (Zulu time)."""
        result = executor._parse_datetime("2026-01-15T10:30:00Z")

        assert result is not None
        assert result.year == 2026
        assert result.tzinfo is not None

    def test_parse_iso_string_with_offset(self, executor):
        """Test ISO string with offset."""
        result = executor._parse_datetime("2026-01-15T10:30:00+00:00")

        assert result is not None
        assert result.year == 2026

    def test_parse_invalid_string_returns_none(self, executor):
        """Test invalid string returns None with warning."""
        result = executor._parse_datetime("not_a_date")
        assert result is None

    def test_parse_unknown_type_returns_none(self, executor):
        """Test unknown type returns None with warning."""
        result = executor._parse_datetime(12345)  # int, not datetime or string
        assert result is None

    def test_parse_string_without_tz_adds_utc(self, executor):
        """Test ISO string without timezone gets UTC added."""
        result = executor._parse_datetime("2026-01-15T10:30:00")

        assert result is not None
        assert result.tzinfo == UTC


class TestGetMarketClock:
    """Tests for get_market_clock edge cases."""

    def test_handles_dict_response(self, executor, mock_trading_client):
        """Test handling when API returns dict instead of Clock object."""
        mock_trading_client.get_clock.return_value = {
            "timestamp": "2026-01-15T10:30:00Z",
            "is_open": True,
            "next_open": "2026-01-16T09:30:00Z",
            "next_close": "2026-01-15T16:00:00Z",
        }

        result = executor.get_market_clock()

        assert result["is_open"] is True
        assert result["timestamp"] is not None

    def test_handles_clock_object(self, executor, mock_trading_client):
        """Test handling when API returns Clock object."""
        mock_clock = Mock()
        mock_clock.timestamp = datetime.now(UTC)
        mock_clock.is_open = False
        mock_clock.next_open = datetime.now(UTC) + timedelta(hours=12)
        mock_clock.next_close = None

        mock_trading_client.get_clock.return_value = mock_clock

        result = executor.get_market_clock()

        assert result["is_open"] is False
        assert result["timestamp"] is not None

    def test_api_error_wrapped_as_connection_error(self, executor, mock_trading_client):
        """Test AlpacaAPIError is wrapped as AlpacaConnectionError."""
        # Create a MockAlpacaAPIError with status_code
        api_error = AlpacaAPIError("API failure", status_code=500)
        mock_trading_client.get_clock.side_effect = api_error

        with pytest.raises(AlpacaConnectionError, match="Error fetching market clock"):
            executor.get_market_clock()


class TestGetAllPositions:
    """Tests for get_all_positions edge cases."""

    def test_handles_dict_response(self, executor, mock_trading_client):
        """Test handling when API returns dict with 'positions' key."""
        if not ALPACA_AVAILABLE:
            pytest.skip("alpaca-py not installed")

        mock_position = Mock(spec=Position)
        mock_position.symbol = "AAPL"
        mock_position.qty = Decimal("100")
        mock_position.avg_entry_price = Decimal("150.00")
        mock_position.current_price = Decimal("155.00")
        mock_position.market_value = Decimal("15500.00")
        mock_position.unrealized_pl = Decimal("500.00")
        mock_position.unrealized_plpc = Decimal("0.0333")
        mock_position.side = "long"

        # Return dict with "positions" key
        mock_trading_client.get_all_positions.return_value = {"positions": [mock_position]}

        result = executor.get_all_positions()

        assert len(result) == 1
        assert result[0]["symbol"] == "AAPL"

    def test_raises_on_unexpected_position_type(self, executor, mock_trading_client):
        """Test error when positions list contains non-Position objects."""
        # Return a list containing a dict instead of Position
        mock_trading_client.get_all_positions.return_value = [{"not": "a_position"}]

        with pytest.raises(AlpacaClientError, match="Unexpected response type"):
            executor.get_all_positions()


class TestGetAccountInfo:
    """Tests for get_account_info edge cases."""

    def test_returns_none_on_non_tradeaccount_response(self, executor, mock_trading_client):
        """Test returns None when API returns non-TradeAccount type (e.g., dict)."""
        # get_account_info expects TradeAccount, returns None if it gets something else
        mock_trading_client.get_account.return_value = {
            "id": "account_123",
            "account_number": "ACC123",
            "status": "ACTIVE",
            "cash": "10000.00",
        }

        result = executor.get_account_info()

        # Returns None because the response is not a TradeAccount
        assert result is None

    def test_handles_account_object(self, executor, mock_trading_client):
        """Test handling when API returns TradeAccount object."""
        if not ALPACA_AVAILABLE:
            pytest.skip("alpaca-py not installed")

        mock_account = Mock(spec=TradeAccount)
        mock_account.account_number = "ACC123"
        mock_account.status = Mock(value="ACTIVE")
        mock_account.currency = "USD"
        mock_account.cash = Decimal("10000.00")
        mock_account.portfolio_value = Decimal("50000.00")
        mock_account.buying_power = Decimal("20000.00")
        mock_account.pattern_day_trader = False
        mock_account.trading_blocked = False
        mock_account.transfers_blocked = False

        mock_trading_client.get_account.return_value = mock_account

        result = executor.get_account_info()

        assert result is not None
        assert result["status"] == "ACTIVE"
        assert result["account_number"] == "ACC123"


class TestCancelOrder:
    """Tests for cancel_order edge cases."""

    def test_cancel_returns_true_on_success(self, executor, mock_trading_client):
        """Test cancel returns True on success."""
        mock_trading_client.cancel_order_by_id.return_value = None

        result = executor.cancel_order("order_123")

        assert result is True

    def test_cancel_raises_connection_error_on_404(self, executor, mock_trading_client):
        """Test cancel raises AlpacaConnectionError on 404 (order not found)."""
        # Create a MockAlpacaAPIError with status_code
        api_error = AlpacaAPIError("Order not found", status_code=404)
        mock_trading_client.cancel_order_by_id.side_effect = api_error

        # 404 is NOT special-cased, raises AlpacaConnectionError
        with pytest.raises(AlpacaConnectionError, match="Error cancelling order"):
            executor.cancel_order("nonexistent_order")


class TestGetLatestQuotes:
    """Tests for get_latest_quotes edge cases."""

    def test_raises_on_empty_symbols(self, executor, mock_data_client):
        """Test ValueError raised on empty symbols list."""
        with pytest.raises(ValueError, match="symbols list cannot be empty"):
            executor.get_latest_quotes([])

    def test_handles_quote_response(self, executor, mock_data_client):
        """Test handling quote response with bid/ask prices."""
        mock_quote = Mock()
        # Alpaca uses 'bp' for bid price and 'ap' for ask price
        mock_quote.bp = 150.00
        mock_quote.ap = 150.50
        mock_quote.timestamp = datetime.now(UTC)

        mock_data_client.get_stock_latest_quote.return_value = {"AAPL": mock_quote}

        result = executor.get_latest_quotes(["AAPL"])

        assert "AAPL" in result
        assert result["AAPL"]["bid_price"] == Decimal("150.00")
        assert result["AAPL"]["ask_price"] == Decimal("150.50")
        # Last price is mid-quote when trades not available
        assert result["AAPL"]["last_price"] == Decimal("150.25")

    def test_handles_missing_bid_ask(self, executor, mock_data_client):
        """Test handling when bid/ask are None."""
        mock_quote = Mock()
        # Bid/ask are None
        mock_quote.bp = None
        mock_quote.ap = None
        mock_quote.timestamp = datetime.now(UTC)

        mock_data_client.get_stock_latest_quote.return_value = {"AAPL": mock_quote}

        result = executor.get_latest_quotes(["AAPL"])

        assert "AAPL" in result
        assert result["AAPL"]["bid_price"] is None
        assert result["AAPL"]["ask_price"] is None
        assert result["AAPL"]["last_price"] is None

    def test_handles_missing_symbol_in_response(self, executor, mock_data_client):
        """Test handling when symbol not in response."""
        mock_data_client.get_stock_latest_quote.return_value = {}

        result = executor.get_latest_quotes(["AAPL"])

        # Symbol not in result
        assert "AAPL" not in result

    def test_api_error_raises_connection_error(self, executor, mock_data_client):
        """Test AlpacaAPIError is wrapped as AlpacaConnectionError."""
        api_error = AlpacaAPIError("API failure", status_code=500)
        mock_data_client.get_stock_latest_quote.side_effect = api_error

        with pytest.raises(AlpacaConnectionError, match="Failed to fetch quotes"):
            executor.get_latest_quotes(["AAPL"])


class TestGetOpenPosition:
    """Tests for get_open_position edge cases."""

    def test_returns_none_when_position_not_found(self, executor, mock_trading_client):
        """Test returns None when position is not found."""
        mock_trading_client.get_open_position.return_value = None

        result = executor.get_open_position("AAPL")

        assert result is None

    def test_returns_none_on_404(self, executor, mock_trading_client):
        """Test returns None on 404 (flat position)."""
        api_error = AlpacaAPIError("Position not found", status_code=404)
        mock_trading_client.get_open_position.side_effect = api_error

        result = executor.get_open_position("AAPL")

        assert result is None

    def test_raises_on_unexpected_type(self, executor, mock_trading_client):
        """Test raises AlpacaClientError when API returns unexpected type."""
        mock_trading_client.get_open_position.return_value = {"not": "a_position"}

        with pytest.raises(AlpacaClientError, match="Unexpected response type"):
            executor.get_open_position("AAPL")

    def test_handles_position_object(self, executor, mock_trading_client):
        """Test handling when API returns Position object."""
        if not ALPACA_AVAILABLE:
            pytest.skip("alpaca-py not installed")

        mock_position = Mock(spec=Position)
        mock_position.symbol = "AAPL"
        mock_position.qty = Decimal("100")
        mock_position.avg_entry_price = Decimal("150.00")
        mock_position.current_price = Decimal("155.00")
        mock_position.market_value = Decimal("15500.00")

        mock_trading_client.get_open_position.return_value = mock_position

        result = executor.get_open_position("AAPL")

        assert result is not None
        assert result["symbol"] == "AAPL"
        assert result["qty"] == Decimal("100")

    def test_raises_connection_error_on_non_404(self, executor, mock_trading_client):
        """Test raises AlpacaConnectionError on non-404 errors."""
        api_error = AlpacaAPIError("Server error", status_code=500)
        mock_trading_client.get_open_position.side_effect = api_error

        with pytest.raises(AlpacaConnectionError, match="Error fetching position"):
            executor.get_open_position("AAPL")


class TestCheckConnection:
    """Tests for check_connection edge cases."""

    def test_returns_true_on_success(self, executor, mock_trading_client):
        """Test returns True when connection is healthy."""
        mock_trading_client.get_account.return_value = Mock()

        result = executor.check_connection()

        assert result is True

    def test_returns_false_on_none(self, executor, mock_trading_client):
        """Test returns False when account is None."""
        mock_trading_client.get_account.return_value = None

        result = executor.check_connection()

        assert result is False

    def test_returns_false_on_api_error(self, executor, mock_trading_client):
        """Test returns False on API error."""
        api_error = AlpacaAPIError("API failure", status_code=500)
        mock_trading_client.get_account.side_effect = api_error

        result = executor.check_connection()

        assert result is False

    def test_returns_false_on_unexpected_error(self, executor, mock_trading_client):
        """Test returns False on unexpected exceptions."""
        mock_trading_client.get_account.side_effect = RuntimeError("Unexpected")

        result = executor.check_connection()

        assert result is False


class TestCancelOrderRejection:
    """Tests for cancel_order rejection handling."""

    def test_raises_rejection_error_on_422(self, executor, mock_trading_client):
        """Test raises AlpacaRejectionError on 422 (cannot cancel)."""
        from apps.execution_gateway.alpaca_client import AlpacaRejectionError

        api_error = AlpacaAPIError("Order cannot be cancelled", status_code=422)
        mock_trading_client.cancel_order_by_id.side_effect = api_error

        with pytest.raises(AlpacaRejectionError, match="Order cannot be cancelled"):
            executor.cancel_order("order_123")


class TestGetAllPositionsErrors:
    """Tests for get_all_positions error handling."""

    def test_raises_connection_error_on_api_error(self, executor, mock_trading_client):
        """Test raises AlpacaConnectionError on API error."""
        api_error = AlpacaAPIError("API failure", status_code=500)
        mock_trading_client.get_all_positions.side_effect = api_error

        with pytest.raises(AlpacaConnectionError, match="Error fetching positions"):
            executor.get_all_positions()


class TestGetAccountInfoErrors:
    """Tests for get_account_info error handling."""

    def test_returns_none_on_api_error(self, executor, mock_trading_client):
        """Test returns None on API error."""
        api_error = AlpacaAPIError("API failure", status_code=500)
        mock_trading_client.get_account.side_effect = api_error

        result = executor.get_account_info()

        assert result is None

    def test_returns_none_on_data_error(self, executor, mock_trading_client):
        """Test returns None on data validation error."""
        # Return an object that will cause AttributeError when accessed
        mock_account = Mock()
        del mock_account.account_number  # Will cause AttributeError
        mock_trading_client.get_account.return_value = mock_account

        result = executor.get_account_info()

        assert result is None


class TestActivitiesBaseUrl:
    """Tests for _activities_base_url helper."""

    def test_strips_v2_suffix(self):
        """Test stripping /v2 suffix from base URL."""
        with patch("apps.execution_gateway.alpaca_client.TradingClient"):
            with patch("apps.execution_gateway.alpaca_client.StockHistoricalDataClient"):
                executor = AlpacaExecutor(
                    api_key="test_key",
                    secret_key="test_secret",
                    base_url="https://paper-api.alpaca.markets/v2",
                    paper=True,
                )
                result = executor._activities_base_url()
                assert result == "https://paper-api.alpaca.markets"

    def test_preserves_url_without_v2(self):
        """Test preserving URL without /v2 suffix."""
        with patch("apps.execution_gateway.alpaca_client.TradingClient"):
            with patch("apps.execution_gateway.alpaca_client.StockHistoricalDataClient"):
                executor = AlpacaExecutor(
                    api_key="test_key",
                    secret_key="test_secret",
                    base_url="https://paper-api.alpaca.markets",
                    paper=True,
                )
                result = executor._activities_base_url()
                assert result == "https://paper-api.alpaca.markets"


class TestCheckConnectionNetworkError:
    """Tests for check_connection network error handling."""

    def test_returns_false_on_connect_timeout(self, executor, mock_trading_client):
        """Test returns False on connection timeout."""
        import httpx
        mock_trading_client.get_account.side_effect = httpx.ConnectTimeout("Timeout")

        result = executor.check_connection()

        assert result is False

    def test_returns_false_on_network_error(self, executor, mock_trading_client):
        """Test returns False on network error."""
        import httpx
        mock_trading_client.get_account.side_effect = httpx.NetworkError("Network down")

        result = executor.check_connection()

        assert result is False
