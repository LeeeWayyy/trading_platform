"""
Alpaca API client wrapper with retry logic.

Provides a high-level interface to the Alpaca Trading API with:
- Automatic retry on transient failures (exponential backoff)
- Error classification (retryable vs non-retryable)
- Type-safe request/response handling
- Connection health checking

See ADR-0005 for design rationale.
"""

import logging
from typing import Optional, Dict, Any
from decimal import Decimal

from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    RetryError
)

try:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import (
        MarketOrderRequest,
        LimitOrderRequest,
        StopOrderRequest,
        StopLimitOrderRequest
    )
    from alpaca.trading.enums import OrderSide, TimeInForce, OrderType
    from alpaca.common.exceptions import APIError as AlpacaAPIError
    ALPACA_AVAILABLE = True
except ImportError:
    ALPACA_AVAILABLE = False
    TradingClient = None
    AlpacaAPIError = Exception

from apps.execution_gateway.schemas import OrderRequest


logger = logging.getLogger(__name__)


class AlpacaClientError(Exception):
    """Base exception for Alpaca client errors."""
    pass


class AlpacaConnectionError(AlpacaClientError):
    """Connection error to Alpaca API (retryable)."""
    pass


class AlpacaValidationError(AlpacaClientError):
    """Validation error from Alpaca API (non-retryable)."""
    pass


class AlpacaRejectionError(AlpacaClientError):
    """Order rejected by Alpaca (non-retryable)."""
    pass


class AlpacaExecutor:
    """
    Alpaca API client wrapper with retry logic.

    Handles order submission, cancellation, and status queries with automatic
    retry on transient failures.

    Attributes:
        client: Alpaca TradingClient instance
        api_key: Alpaca API key ID
        secret_key: Alpaca API secret key
        base_url: Alpaca API base URL (paper or live)
        paper: Whether using paper trading (default: True)

    Examples:
        >>> executor = AlpacaExecutor(
        ...     api_key="your_key",
        ...     secret_key="your_secret",
        ...     base_url="https://paper-api.alpaca.markets"
        ... )
        >>> # Submit market order
        >>> order = executor.submit_order(
        ...     OrderRequest(symbol="AAPL", side="buy", qty=10, order_type="market"),
        ...     client_order_id="abc123..."
        ... )
        >>> # Check order status
        >>> status = executor.get_order_by_client_id("abc123...")
    """

    def __init__(
        self,
        api_key: str,
        secret_key: str,
        base_url: str = "https://paper-api.alpaca.markets",
        paper: bool = True
    ):
        """
        Initialize Alpaca client.

        Args:
            api_key: Alpaca API key ID
            secret_key: Alpaca API secret key
            base_url: Alpaca API base URL
            paper: Whether using paper trading (default: True)

        Raises:
            ImportError: If alpaca-py package is not installed
        """
        if not ALPACA_AVAILABLE:
            raise ImportError(
                "alpaca-py package is required. Install with: pip install alpaca-py"
            )

        self.api_key = api_key
        self.secret_key = secret_key
        self.base_url = base_url
        self.paper = paper

        # Initialize Alpaca client
        self.client = TradingClient(
            api_key=api_key,
            secret_key=secret_key,
            paper=paper
        )

        logger.info(f"Initialized Alpaca client (paper={paper}, base_url={base_url})")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(AlpacaConnectionError),
        reraise=True
    )
    def submit_order(
        self,
        order: OrderRequest,
        client_order_id: str
    ) -> Dict[str, Any]:
        """
        Submit order to Alpaca with retry logic.

        Retry policy:
        - Max 3 attempts
        - Exponential backoff: 2s, 4s, 8s
        - Only retry on transient errors (connection, timeout)
        - Do NOT retry on validation errors (400, 422, 403)

        Args:
            order: Order request
            client_order_id: Deterministic client order ID

        Returns:
            Order response from Alpaca (as dict)

        Raises:
            AlpacaValidationError: Invalid order parameters (non-retryable)
            AlpacaRejectionError: Order rejected by broker (non-retryable)
            AlpacaConnectionError: Connection error (retryable)

        Examples:
            >>> executor = AlpacaExecutor(api_key="...", secret_key="...")
            >>> order = OrderRequest(
            ...     symbol="AAPL",
            ...     side="buy",
            ...     qty=10,
            ...     order_type="market"
            ... )
            >>> response = executor.submit_order(order, "abc123...")
            >>> response["id"]  # Alpaca's broker_order_id
            'f7e6d5c4-b3a2-1098-7654-3210fedcba98'
        """
        try:
            # Build order request based on order type
            alpaca_request = self._build_alpaca_request(order, client_order_id)

            # Submit to Alpaca
            logger.info(
                f"Submitting order: {order.symbol} {order.side} {order.qty} "
                f"(type={order.order_type}, client_id={client_order_id})"
            )
            alpaca_order = self.client.submit_order(alpaca_request)

            # Convert to dict for consistent return type
            order_dict = {
                "id": str(alpaca_order.id),
                "client_order_id": alpaca_order.client_order_id,
                "symbol": alpaca_order.symbol,
                "side": alpaca_order.side.value,
                "qty": float(alpaca_order.qty),
                "order_type": alpaca_order.order_type.value,
                "status": alpaca_order.status.value,
                "created_at": alpaca_order.created_at,
                "limit_price": float(alpaca_order.limit_price) if alpaca_order.limit_price else None,
                "stop_price": float(alpaca_order.stop_price) if alpaca_order.stop_price else None,
            }

            logger.info(
                f"Order submitted successfully: broker_id={order_dict['id']}, "
                f"status={order_dict['status']}"
            )

            return order_dict

        except AlpacaAPIError as e:
            # Classify error and decide whether to retry
            status_code = getattr(e, 'status_code', None)
            error_message = str(e)

            logger.error(
                f"Alpaca API error: status={status_code}, message={error_message}"
            )

            if status_code == 400:
                # Bad request - validation error (do not retry)
                raise AlpacaValidationError(f"Invalid order: {error_message}") from e

            elif status_code in (422, 403):
                # Unprocessable entity or forbidden - rejection (do not retry)
                raise AlpacaRejectionError(f"Order rejected: {error_message}") from e

            else:
                # Transient error - will retry
                raise AlpacaConnectionError(
                    f"Alpaca API connection error: {error_message}"
                ) from e

        except Exception as e:
            # Unexpected error
            logger.error(f"Unexpected error submitting order: {e}")
            raise AlpacaClientError(f"Unexpected error: {e}") from e

    def _build_alpaca_request(
        self,
        order: OrderRequest,
        client_order_id: str
    ):
        """
        Build Alpaca order request object based on order type.

        Args:
            order: Order request
            client_order_id: Deterministic client order ID

        Returns:
            Alpaca order request object (MarketOrderRequest, LimitOrderRequest, etc.)

        Raises:
            ValueError: If order type is unsupported or missing required fields
        """
        # Convert side to Alpaca enum
        side = OrderSide.BUY if order.side == "buy" else OrderSide.SELL

        # Convert time_in_force to Alpaca enum
        tif_map = {
            "day": TimeInForce.DAY,
            "gtc": TimeInForce.GTC,
            "ioc": TimeInForce.IOC,
            "fok": TimeInForce.FOK,
        }
        time_in_force = tif_map[order.time_in_force]

        # Build request based on order type
        if order.order_type == "market":
            return MarketOrderRequest(
                symbol=order.symbol,
                qty=order.qty,
                side=side,
                time_in_force=time_in_force,
                client_order_id=client_order_id
            )

        elif order.order_type == "limit":
            if order.limit_price is None:
                raise ValueError("limit_price is required for limit orders")

            return LimitOrderRequest(
                symbol=order.symbol,
                qty=order.qty,
                side=side,
                time_in_force=time_in_force,
                limit_price=float(order.limit_price),
                client_order_id=client_order_id
            )

        elif order.order_type == "stop":
            if order.stop_price is None:
                raise ValueError("stop_price is required for stop orders")

            return StopOrderRequest(
                symbol=order.symbol,
                qty=order.qty,
                side=side,
                time_in_force=time_in_force,
                stop_price=float(order.stop_price),
                client_order_id=client_order_id
            )

        elif order.order_type == "stop_limit":
            if order.limit_price is None or order.stop_price is None:
                raise ValueError(
                    "Both limit_price and stop_price are required for stop_limit orders"
                )

            return StopLimitOrderRequest(
                symbol=order.symbol,
                qty=order.qty,
                side=side,
                time_in_force=time_in_force,
                limit_price=float(order.limit_price),
                stop_price=float(order.stop_price),
                client_order_id=client_order_id
            )

        else:
            raise ValueError(f"Unsupported order type: {order.order_type}")

    def get_order_by_client_id(self, client_order_id: str) -> Optional[Dict[str, Any]]:
        """
        Get order by client_order_id.

        Args:
            client_order_id: Client order ID

        Returns:
            Order dict if found, None otherwise

        Raises:
            AlpacaConnectionError: Connection error

        Examples:
            >>> executor = AlpacaExecutor(api_key="...", secret_key="...")
            >>> order = executor.get_order_by_client_id("abc123...")
            >>> if order:
            ...     print(order["status"])
        """
        try:
            alpaca_order = self.client.get_order_by_client_id(client_order_id)

            if alpaca_order is None:
                return None

            return {
                "id": str(alpaca_order.id),
                "client_order_id": alpaca_order.client_order_id,
                "symbol": alpaca_order.symbol,
                "side": alpaca_order.side.value,
                "qty": float(alpaca_order.qty),
                "order_type": alpaca_order.order_type.value,
                "status": alpaca_order.status.value,
                "filled_qty": float(alpaca_order.filled_qty or 0),
                "filled_avg_price": float(alpaca_order.filled_avg_price) if alpaca_order.filled_avg_price else None,
                "created_at": alpaca_order.created_at,
                "updated_at": alpaca_order.updated_at,
            }

        except AlpacaAPIError as e:
            if getattr(e, 'status_code', None) == 404:
                return None
            raise AlpacaConnectionError(f"Error fetching order: {e}") from e

    def cancel_order(self, order_id: str) -> bool:
        """
        Cancel order by broker order_id.

        Args:
            order_id: Alpaca broker order ID

        Returns:
            True if cancelled successfully

        Raises:
            AlpacaConnectionError: Connection error
            AlpacaRejectionError: Order cannot be cancelled
        """
        try:
            self.client.cancel_order_by_id(order_id)
            logger.info(f"Order cancelled: {order_id}")
            return True

        except AlpacaAPIError as e:
            status_code = getattr(e, 'status_code', None)

            if status_code == 422:
                raise AlpacaRejectionError(f"Order cannot be cancelled: {e}") from e
            else:
                raise AlpacaConnectionError(f"Error cancelling order: {e}") from e

    def check_connection(self) -> bool:
        """
        Check if connection to Alpaca is healthy.

        Returns:
            True if connected, False otherwise

        Examples:
            >>> executor = AlpacaExecutor(api_key="...", secret_key="...")
            >>> if executor.check_connection():
            ...     print("Connected to Alpaca")
        """
        try:
            # Try to get account info (lightweight check)
            account = self.client.get_account()
            return account is not None

        except Exception as e:
            logger.error(f"Alpaca connection check failed: {e}")
            return False

    def get_account_info(self) -> Optional[Dict[str, Any]]:
        """
        Get account information from Alpaca.

        Returns:
            Account info dict if successful, None otherwise

        Examples:
            >>> executor = AlpacaExecutor(api_key="...", secret_key="...")
            >>> account = executor.get_account_info()
            >>> if account:
            ...     print(f"Buying power: ${account['buying_power']}")
        """
        try:
            account = self.client.get_account()

            return {
                "account_number": account.account_number,
                "status": account.status.value,
                "currency": account.currency,
                "buying_power": float(account.buying_power),
                "cash": float(account.cash),
                "portfolio_value": float(account.portfolio_value),
                "pattern_day_trader": account.pattern_day_trader,
                "trading_blocked": account.trading_blocked,
                "transfers_blocked": account.transfers_blocked,
            }

        except Exception as e:
            logger.error(f"Error fetching account info: {e}")
            return None
