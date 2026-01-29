"""
Alpaca API client wrapper with retry logic.

Provides a high-level interface to the Alpaca Trading API with:
- Automatic retry on transient failures (exponential backoff)
- Error classification (retryable vs non-retryable)
- Type-safe request/response handling
- Connection health checking

See ADR-0014 for design rationale.
"""

import json
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol, cast

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

try:
    from alpaca.common.enums import Sort
    from alpaca.common.exceptions import APIError as AlpacaAPIError
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockLatestQuoteRequest
    from alpaca.trading.client import TradingClient
    from alpaca.trading.enums import OrderSide, QueryOrderStatus, TimeInForce
    from alpaca.trading.models import Order, Position, TradeAccount
    from alpaca.trading.requests import (
        GetOrdersRequest,
        LimitOrderRequest,
        MarketOrderRequest,
        ReplaceOrderRequest,
        StopLimitOrderRequest,
        StopOrderRequest,
    )

    ALPACA_AVAILABLE = True
except ImportError:
    ALPACA_AVAILABLE = False
    TradingClient = None  # type: ignore[assignment,misc]
    StockHistoricalDataClient = None  # type: ignore[assignment,misc]
    AlpacaAPIError = Exception  # type: ignore[assignment,misc]
    Order = None  # type: ignore[assignment,misc]
    Position = None  # type: ignore[assignment,misc]
    TradeAccount = None  # type: ignore[assignment,misc]

from apps.execution_gateway.schemas import OrderRequest

logger = logging.getLogger(__name__)


class _ClockLike(Protocol):
    timestamp: datetime
    is_open: bool
    next_open: datetime | None
    next_close: datetime | None


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
        paper: bool = True,
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
            raise ImportError("alpaca-py package is required. Install with: pip install alpaca-py")

        self.api_key = api_key
        self.secret_key = secret_key
        self.base_url = base_url
        self.paper = paper

        # Initialize Alpaca trading client
        self.client = TradingClient(api_key=api_key, secret_key=secret_key, paper=paper)

        # Initialize Alpaca data client for market data
        self.data_client = StockHistoricalDataClient(api_key=api_key, secret_key=secret_key)

        logger.info(f"Initialized Alpaca client (paper={paper}, base_url={base_url})")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(AlpacaConnectionError),
        reraise=True,
    )
    def submit_order(self, order: OrderRequest, client_order_id: str) -> dict[str, Any]:
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

            # Runtime type check for production safety (alpaca-py returns Order | dict[str, Any])
            if not isinstance(alpaca_order, Order):
                raise AlpacaClientError(
                    f"Unexpected response type from Alpaca API: {type(alpaca_order).__name__}. "
                    f"Expected Order object."
                )

            # H1 Fix: Convert to dict with Decimal for financial precision
            # Using Decimal(str(x)) pattern to avoid float precision issues
            order_dict = {
                "id": str(alpaca_order.id),
                "client_order_id": alpaca_order.client_order_id,
                "symbol": alpaca_order.symbol,
                "side": alpaca_order.side.value if alpaca_order.side is not None else None,
                "qty": Decimal(str(alpaca_order.qty or 0)),  # H1: Decimal for precision
                "order_type": (
                    alpaca_order.order_type.value if alpaca_order.order_type is not None else None
                ),
                "status": alpaca_order.status.value if alpaca_order.status is not None else None,
                "created_at": alpaca_order.created_at,
                "limit_price": (
                    Decimal(str(alpaca_order.limit_price))
                    if alpaca_order.limit_price is not None
                    else None
                ),
                "stop_price": (
                    Decimal(str(alpaca_order.stop_price))
                    if alpaca_order.stop_price is not None
                    else None
                ),
            }

            logger.info(
                f"Order submitted successfully: broker_id={order_dict['id']}, "
                f"status={order_dict['status']}"
            )

            return order_dict

        except AlpacaAPIError as e:
            # Classify error and decide whether to retry
            status_code = getattr(e, "status_code", None)
            error_message = str(e)

            logger.error(f"Alpaca API error: status={status_code}, message={error_message}")

            if status_code == 400:
                # Bad request - validation error (do not retry)
                raise AlpacaValidationError(f"Invalid order: {error_message}") from e

            elif status_code in (422, 403):
                # Unprocessable entity or forbidden - rejection (do not retry)
                raise AlpacaRejectionError(f"Order rejected: {error_message}") from e

            else:
                # Transient error - will retry
                raise AlpacaConnectionError(f"Alpaca API connection error: {error_message}") from e

        except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.NetworkError) as e:
            # Network/connection errors - retryable
            logger.error(
                "Network error submitting order",
                extra={
                    "symbol": order.symbol,
                    "side": order.side,
                    "qty": order.qty,
                    "order_type": order.order_type,
                    "client_order_id": client_order_id,
                    "error_type": type(e).__name__,
                },
                exc_info=True,
            )
            raise AlpacaConnectionError(f"Network error: {e}") from e

        except (json.JSONDecodeError, KeyError, ValueError, AttributeError) as e:
            # Data validation/parsing errors - non-retryable
            logger.error(
                "Data validation error submitting order",
                extra={
                    "symbol": order.symbol,
                    "side": order.side,
                    "qty": order.qty,
                    "order_type": order.order_type,
                    "client_order_id": client_order_id,
                    "error_type": type(e).__name__,
                },
                exc_info=True,
            )
            raise AlpacaValidationError(f"Data validation error: {e}") from e

    def _build_alpaca_request(
        self, order: OrderRequest, client_order_id: str
    ) -> MarketOrderRequest | LimitOrderRequest | StopOrderRequest | StopLimitOrderRequest:
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
                client_order_id=client_order_id,
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
                client_order_id=client_order_id,
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
                client_order_id=client_order_id,
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
                client_order_id=client_order_id,
            )

        else:
            raise ValueError(f"Unsupported order type: {order.order_type}")

    def get_order_by_client_id(self, client_order_id: str) -> dict[str, Any] | None:
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

            # Runtime type check for production safety (alpaca-py returns Order | dict[str, Any])
            if not isinstance(alpaca_order, Order):
                raise AlpacaClientError(
                    f"Unexpected response type from Alpaca API: {type(alpaca_order).__name__}. "
                    f"Expected Order object."
                )

            # H1 Fix: Use Decimal for financial precision
            return {
                "id": str(alpaca_order.id),
                "client_order_id": alpaca_order.client_order_id,
                "symbol": alpaca_order.symbol,
                "side": alpaca_order.side.value if alpaca_order.side is not None else None,
                "qty": Decimal(str(alpaca_order.qty or 0)),  # H1: Decimal for precision
                "order_type": (
                    alpaca_order.order_type.value if alpaca_order.order_type is not None else None
                ),
                "status": alpaca_order.status.value if alpaca_order.status is not None else None,
                "filled_qty": Decimal(str(alpaca_order.filled_qty or 0)),  # H1: Decimal
                "filled_avg_price": (
                    Decimal(str(alpaca_order.filled_avg_price))
                    if alpaca_order.filled_avg_price
                    else None
                ),
                "created_at": self._parse_datetime(alpaca_order.created_at),
                "updated_at": self._parse_datetime(alpaca_order.updated_at),
            }

        except AlpacaAPIError as e:
            if getattr(e, "status_code", None) == 404:
                return None
            raise AlpacaConnectionError(f"Error fetching order: {e}") from e

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(AlpacaConnectionError),
        reraise=True,
    )
    def replace_order(
        self,
        broker_order_id: str,
        *,
        qty: int | None = None,
        limit_price: Decimal | None = None,
        stop_price: Decimal | None = None,
        time_in_force: str | None = None,
        new_client_order_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Replace an existing order atomically via Alpaca.

        Args:
            broker_order_id: Alpaca broker order ID
            qty: New total quantity (optional)
            limit_price: New limit price (optional)
            stop_price: New stop price (optional)
            time_in_force: New time in force (day or gtc)
            new_client_order_id: Optional client_order_id for replacement order

        Returns:
            Order response from Alpaca (as dict)

        Raises:
            AlpacaValidationError: Invalid modification parameters (non-retryable)
            AlpacaRejectionError: Order rejected by broker (non-retryable)
            AlpacaConnectionError: Connection error (retryable)
        """
        try:
            tif_map = {
                "day": TimeInForce.DAY,
                "gtc": TimeInForce.GTC,
            }
            tif_value = tif_map.get(time_in_force) if time_in_force else None

            request = ReplaceOrderRequest(
                qty=qty,
                limit_price=float(limit_price) if limit_price is not None else None,
                stop_price=float(stop_price) if stop_price is not None else None,
                time_in_force=tif_value,
                client_order_id=new_client_order_id,
            )

            alpaca_order = self.client.replace_order_by_id(broker_order_id, request)

            if not isinstance(alpaca_order, Order):
                raise AlpacaClientError(
                    f"Unexpected response type from Alpaca API: {type(alpaca_order).__name__}. "
                    f"Expected Order object."
                )

            return {
                "id": str(alpaca_order.id),
                "client_order_id": alpaca_order.client_order_id,
                "symbol": alpaca_order.symbol,
                "side": alpaca_order.side.value if alpaca_order.side is not None else None,
                "qty": Decimal(str(alpaca_order.qty or 0)),
                "order_type": (
                    alpaca_order.order_type.value if alpaca_order.order_type is not None else None
                ),
                "status": alpaca_order.status.value if alpaca_order.status is not None else None,
                "created_at": alpaca_order.created_at,
                "limit_price": (
                    Decimal(str(alpaca_order.limit_price))
                    if alpaca_order.limit_price is not None
                    else None
                ),
                "stop_price": (
                    Decimal(str(alpaca_order.stop_price))
                    if alpaca_order.stop_price is not None
                    else None
                ),
            }

        except AlpacaAPIError as e:
            status_code = getattr(e, "status_code", None)
            error_message = str(e)
            logger.error(
                "Alpaca API error replacing order",
                extra={"status_code": status_code, "message": error_message},
            )

            if status_code == 400:
                raise AlpacaValidationError(f"Invalid modification: {error_message}") from e
            if status_code in (403, 404, 422):
                raise AlpacaRejectionError(f"Order replacement rejected: {error_message}") from e
            raise AlpacaConnectionError(f"Alpaca API connection error: {error_message}") from e

        except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.NetworkError) as e:
            logger.error(
                "Network error replacing order",
                extra={"broker_order_id": broker_order_id, "error_type": type(e).__name__},
                exc_info=True,
            )
            raise AlpacaConnectionError(f"Network error: {e}") from e

        except (json.JSONDecodeError, KeyError, ValueError, AttributeError) as e:
            logger.error(
                "Data validation error replacing order",
                extra={"broker_order_id": broker_order_id, "error_type": type(e).__name__},
                exc_info=True,
            )
            raise AlpacaValidationError(f"Data validation error: {e}") from e

    def get_orders(
        self,
        status: str = "all",
        limit: int = 500,
        after: datetime | None = None,
        symbols: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch orders with optional filtering and best-effort pagination.

        Note: Pagination uses the 'after' timestamp which is exclusive. When multiple
        orders have the same timestamp, some may be missed across page boundaries.
        This is best-effort for historical data; the seen_ids set handles same-page
        deduplication, and reconciliation will catch any missed orders later.
        """
        if limit <= 0:
            raise ValueError("limit must be positive")

        try:
            try:
                status_enum = QueryOrderStatus(status)
            except ValueError as exc:
                raise ValueError(f"Unsupported status: {status}") from exc

            results: list[dict[str, Any]] = []
            seen_ids: set[str] = set()
            cursor = after
            direction = Sort.ASC

            while True:
                request = GetOrdersRequest(
                    status=status_enum,
                    limit=limit,
                    after=cursor,
                    direction=direction,
                    symbols=symbols,
                )
                response = self.client.get_orders(request)

                if isinstance(response, dict):
                    orders = response.get("orders") or response.get("data") or []
                else:
                    orders = response

                new_orders_added = 0
                for order in orders:
                    if not isinstance(order, Order):
                        raise AlpacaClientError(
                            f"Unexpected response type from Alpaca API: {type(order).__name__}. "
                            f"Expected Order object."
                        )

                    order_id = str(order.id)
                    if order_id in seen_ids:
                        continue
                    seen_ids.add(order_id)
                    new_orders_added += 1

                    results.append(
                        {
                            "id": order_id,
                            "client_order_id": order.client_order_id,
                            "symbol": order.symbol,
                            "side": order.side.value if order.side is not None else None,
                            "qty": Decimal(str(order.qty or 0)),
                            "order_type": (
                                order.order_type.value if order.order_type is not None else None
                            ),
                            "status": order.status.value if order.status is not None else None,
                            "filled_qty": Decimal(str(order.filled_qty or 0)),
                            "filled_avg_price": (
                                Decimal(str(order.filled_avg_price))
                                if order.filled_avg_price
                                else None
                            ),
                            "limit_price": (
                                Decimal(str(order.limit_price)) if order.limit_price else None
                            ),
                            "notional": (
                                Decimal(str(order.notional)) if order.notional is not None else None
                            ),
                            "created_at": self._parse_datetime(order.created_at),
                            "updated_at": self._parse_datetime(order.updated_at),
                            "submitted_at": self._parse_datetime(order.submitted_at),
                            "filled_at": self._parse_datetime(order.filled_at),
                        }
                    )

                if not orders or len(orders) < limit:
                    break

                last_created_at = orders[-1].created_at if orders else None
                if not last_created_at:
                    break

                if cursor is None or last_created_at > cursor:
                    cursor = last_created_at
                    continue

                if new_orders_added > 0:
                    continue

                # No progress with identical timestamps; nudge cursor forward.
                cursor = cursor + timedelta(microseconds=1)

            return results

        except AlpacaAPIError as e:
            raise AlpacaConnectionError(f"Error fetching orders: {e}") from e

    def _parse_datetime(self, value: Any) -> datetime | None:
        """Parse datetime value to timezone-aware datetime.

        Handles both datetime objects and ISO format strings from Alpaca API.
        Returns None if value is None or unparseable.
        """
        if value is None:
            return None
        if isinstance(value, datetime):
            # Ensure timezone-aware
            if value.tzinfo is None:
                return value.replace(tzinfo=UTC)
            return value
        if isinstance(value, str):
            try:
                # Parse ISO format string (Alpaca returns ISO 8601 format)
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    return parsed.replace(tzinfo=UTC)
                return parsed
            except (ValueError, AttributeError) as e:
                logger.warning(
                    "Failed to parse datetime string from Alpaca",
                    extra={"value": value, "error": str(e)},
                )
                return None
        # Unknown type - log and return None
        logger.warning(
            "Unexpected datetime type from Alpaca",
            extra={"value": value, "type": type(value).__name__},
        )
        return None

    def get_market_clock(self) -> dict[str, Any]:
        """Return Alpaca market clock snapshot with normalized datetime values."""
        try:
            clock = self.client.get_clock()
            if isinstance(clock, dict):
                return {
                    "timestamp": self._parse_datetime(clock.get("timestamp")),
                    "is_open": clock.get("is_open"),
                    "next_open": self._parse_datetime(clock.get("next_open")),
                    "next_close": self._parse_datetime(clock.get("next_close")),
                }
            clock_obj = cast(_ClockLike, clock)
            return {
                "timestamp": self._parse_datetime(clock_obj.timestamp),
                "is_open": clock_obj.is_open,
                "next_open": self._parse_datetime(clock_obj.next_open),
                "next_close": self._parse_datetime(clock_obj.next_close),
            }
        except AlpacaAPIError as e:
            raise AlpacaConnectionError(f"Error fetching market clock: {e}") from e

    def get_all_positions(self) -> list[dict[str, Any]]:
        """Fetch all open positions from Alpaca."""
        try:
            positions = self.client.get_all_positions()
            if isinstance(positions, dict):
                positions_list = positions.get("positions") or positions.get("data") or []
            else:
                positions_list = positions

            result: list[dict[str, Any]] = []
            for position in positions_list:
                if not isinstance(position, Position):
                    raise AlpacaClientError(
                        f"Unexpected response type from Alpaca API: {type(position).__name__}. "
                        f"Expected Position object."
                    )

                result.append(
                    {
                        "symbol": position.symbol,
                        "qty": Decimal(str(position.qty or 0)),
                        "avg_entry_price": Decimal(str(position.avg_entry_price or 0)),
                        "current_price": (
                            Decimal(str(position.current_price))
                            if position.current_price is not None
                            else None
                        ),
                        "market_value": (
                            Decimal(str(position.market_value))
                            if position.market_value is not None
                            else None
                        ),
                    }
                )

            return result

        except AlpacaAPIError as e:
            raise AlpacaConnectionError(f"Error fetching positions: {e}") from e

    def get_open_position(self, symbol: str) -> dict[str, Any] | None:
        """Fetch open position for a symbol. Returns None when flat (404)."""
        try:
            position = self.client.get_open_position(symbol)

            if position is None:
                return None

            if not isinstance(position, Position):
                raise AlpacaClientError(
                    f"Unexpected response type from Alpaca API: {type(position).__name__}. "
                    f"Expected Position object."
                )

            return {
                "symbol": position.symbol,
                "qty": Decimal(str(position.qty or 0)),
                "avg_entry_price": Decimal(str(position.avg_entry_price or 0)),
                "current_price": (
                    Decimal(str(position.current_price))
                    if position.current_price is not None
                    else None
                ),
                "market_value": (
                    Decimal(str(position.market_value))
                    if position.market_value is not None
                    else None
                ),
            }

        except AlpacaAPIError as e:
            if getattr(e, "status_code", None) == 404:
                return None
            raise AlpacaConnectionError(f"Error fetching position: {e}") from e

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
            status_code = getattr(e, "status_code", None)

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

        except AlpacaAPIError as e:
            logger.error(
                "Alpaca API error during connection check",
                extra={
                    "status_code": getattr(e, "status_code", None),
                    "error_type": type(e).__name__,
                },
                exc_info=True,
            )
            return False

        except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.NetworkError) as e:
            logger.error(
                "Network error during connection check",
                extra={"error_type": type(e).__name__},
                exc_info=True,
            )
            return False
        except Exception as e:
            # Catch-all for unexpected errors
            logger.error(
                "Unexpected error during connection check",
                extra={"error_type": type(e).__name__, "error": str(e)},
                exc_info=True,
            )
            return False

    def _activities_base_url(self) -> str:
        base = (self.base_url or "").rstrip("/")
        if base.endswith("/v2"):
            return base[:-3]
        return base

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(AlpacaConnectionError),
        reraise=True,
    )
    def get_account_activities(
        self,
        activity_type: str,
        *,
        after: datetime | None = None,
        until: datetime | None = None,
        page_size: int = 100,
        page_token: str | None = None,
        direction: str = "desc",
    ) -> list[dict[str, Any]]:
        """Fetch account activities of one type (Trading API REST)."""
        base_url = self._activities_base_url()
        url = f"{base_url}/v2/account/activities/{activity_type}"

        params: dict[str, Any] = {
            "direction": direction,
            "page_size": page_size,
        }
        if after is not None:
            params["after"] = after.isoformat().replace("+00:00", "Z")
        if until is not None:
            params["until"] = until.isoformat().replace("+00:00", "Z")
        if page_token:
            params["page_token"] = page_token

        headers = {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.secret_key,
        }

        try:
            resp = httpx.get(url, params=params, headers=headers, timeout=15.0)
            resp.raise_for_status()
            payload = resp.json()
            if not isinstance(payload, list):
                logger.error(
                    "Unexpected activities response type",
                    extra={"activity_type": activity_type, "response_type": type(payload).__name__},
                )
                return []
            return payload
        except httpx.HTTPStatusError as e:
            status_code = getattr(e.response, "status_code", None)
            logger.error(
                "Alpaca activities HTTP error",
                extra={
                    "status_code": status_code,
                    "activity_type": activity_type,
                    "url": str(e.request.url),
                },
                exc_info=True,
            )
            raise AlpacaConnectionError(f"Alpaca activities HTTP error: {e}") from e
        except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.NetworkError) as e:
            logger.error(
                "Network error fetching account activities",
                extra={"activity_type": activity_type, "error_type": type(e).__name__},
                exc_info=True,
            )
            raise AlpacaConnectionError(f"Network error fetching account activities: {e}") from e
        except (ValueError, TypeError) as e:
            logger.error(
                "Data validation error fetching account activities",
                extra={"activity_type": activity_type, "error_type": type(e).__name__},
                exc_info=True,
            )
            return []

    def get_account_info(self) -> dict[str, Any] | None:
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

            # Runtime type check for production safety (alpaca-py returns TradeAccount | dict[str, Any])
            if not isinstance(account, TradeAccount):
                logger.error(
                    f"Unexpected response type from Alpaca API: {type(account).__name__}. "
                    f"Expected TradeAccount object."
                )
                return None

            # H1 Fix: Use Decimal for financial precision
            return {
                "account_number": account.account_number,
                "status": account.status.value,
                "currency": account.currency,
                "buying_power": Decimal(str(account.buying_power)),  # H1: Decimal
                "cash": Decimal(str(account.cash)),  # H1: Decimal
                "portfolio_value": Decimal(str(account.portfolio_value)),  # H1: Decimal
                "pattern_day_trader": account.pattern_day_trader,
                "trading_blocked": account.trading_blocked,
                "transfers_blocked": account.transfers_blocked,
            }

        except AlpacaAPIError as e:
            logger.error(
                "Alpaca API error fetching account info",
                extra={
                    "status_code": getattr(e, "status_code", None),
                    "error_type": type(e).__name__,
                },
                exc_info=True,
            )
            return None

        except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.NetworkError) as e:
            logger.error(
                "Network error fetching account info",
                extra={"error_type": type(e).__name__},
                exc_info=True,
            )
            return None

        except (KeyError, ValueError, AttributeError) as e:
            logger.error(
                "Data validation error fetching account info",
                extra={"error_type": type(e).__name__},
                exc_info=True,
            )
            return None
        except Exception as e:
            # Catch-all for unexpected errors
            logger.error(
                "Unexpected error fetching account info",
                extra={"error_type": type(e).__name__, "error": str(e)},
                exc_info=True,
            )
            return None

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(AlpacaConnectionError),
        reraise=True,
    )
    def get_latest_quotes(self, symbols: list[str]) -> dict[str, dict[str, Any]]:
        """
        Fetch latest market quotes for multiple symbols.

        Uses Alpaca's Stock Historical Data API to get latest quote data
        including bid, ask, and last trade price for each symbol.

        Args:
            symbols: List of stock symbols (e.g., ['AAPL', 'MSFT', 'GOOGL'])

        Returns:
            Dict mapping symbol -> quote data:
            {
                'AAPL': {
                    'ask_price': Decimal('152.75'),
                    'bid_price': Decimal('152.74'),
                    'last_price': Decimal('152.75'),
                    'timestamp': datetime(...)
                },
                'MSFT': {...},
                ...
            }

        Raises:
            AlpacaConnectionError: If API request fails
            ValueError: If symbols list is empty

        Examples:
            >>> executor = AlpacaExecutor(api_key="...", secret_key="...")
            >>> quotes = executor.get_latest_quotes(['AAPL', 'MSFT'])
            >>> quotes['AAPL']['last_price']
            Decimal('152.75')

        Notes:
            - Uses last trade price as primary price
            - Falls back to mid-quote (avg of bid/ask) if no trades
            - Symbols not found are omitted from results (not an error)
            - Batch request is efficient - one API call for all symbols
        """
        if not symbols:
            raise ValueError("symbols list cannot be empty")

        try:
            # Build request for latest quotes (batch)
            request = StockLatestQuoteRequest(symbol_or_symbols=symbols)

            # Fetch quotes from Alpaca
            logger.info(f"Fetching latest quotes for {len(symbols)} symbols: {symbols}")
            quotes_data = self.data_client.get_stock_latest_quote(request)

            # Convert to dict with Decimal prices
            result = {}
            for symbol in symbols:
                if symbol in quotes_data:
                    quote = quotes_data[symbol]

                    # M3 Fix: Check both attribute existence AND non-None values
                    # hasattr alone is insufficient - Decimal(str(None)) raises InvalidOperation
                    ap_value = getattr(quote, "ap", None)
                    bp_value = getattr(quote, "bp", None)

                    if ap_value is not None and bp_value is not None:
                        ask_price = Decimal(str(ap_value))
                        bid_price = Decimal(str(bp_value))
                        # Mid-quote as fallback
                        last_price = (ask_price + bid_price) / Decimal("2")
                    else:
                        # Fallback if bid/ask not available or None
                        ask_price = None
                        bid_price = None
                        last_price = None
                        logger.debug(
                            f"Missing or null bid/ask for {symbol}: "
                            f"ap={ap_value}, bp={bp_value}"
                        )

                    result[symbol] = {
                        "ask_price": ask_price,
                        "bid_price": bid_price,
                        "last_price": last_price,
                        "timestamp": quote.timestamp if hasattr(quote, "timestamp") else None,
                    }

                    logger.debug(
                        f"Quote for {symbol}: last=${last_price}, "
                        f"bid=${bid_price}, ask=${ask_price}"
                    )
                else:
                    logger.warning(f"No quote data available for symbol: {symbol}")

            logger.info(f"Successfully fetched quotes for {len(result)}/{len(symbols)} symbols")
            return result

        except AlpacaAPIError as e:
            error_message = str(e)
            logger.error(
                "Alpaca API error fetching quotes",
                extra={
                    "symbols": symbols,
                    "status_code": getattr(e, "status_code", None),
                    "error_type": type(e).__name__,
                },
                exc_info=True,
            )
            raise AlpacaConnectionError(f"Failed to fetch quotes: {error_message}") from e

        except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.NetworkError) as e:
            logger.error(
                "Network error fetching quotes",
                extra={"symbols": symbols, "error_type": type(e).__name__},
                exc_info=True,
            )
            raise AlpacaConnectionError(f"Network error fetching quotes: {e}") from e

        except (json.JSONDecodeError, KeyError, AttributeError) as e:
            logger.error(
                "Data validation error fetching quotes",
                extra={"symbols": symbols, "error_type": type(e).__name__},
                exc_info=True,
            )
            raise AlpacaConnectionError(f"Data validation error fetching quotes: {e}") from e
        except Exception as e:
            # Catch-all for unexpected errors (including ValueError from data issues)
            logger.error(
                "Unexpected error fetching quotes",
                extra={"symbols": symbols, "error_type": type(e).__name__, "error": str(e)},
                exc_info=True,
            )
            raise AlpacaConnectionError(f"Unexpected error fetching quotes: {e}") from e
