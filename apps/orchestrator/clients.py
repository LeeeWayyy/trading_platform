"""
HTTP clients for communicating with Signal Service and Execution Gateway.

Provides typed interfaces with automatic retries and error handling.
"""

import logging
from typing import Any, List, Optional
from datetime import date

import httpx
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log
)

from apps.orchestrator.schemas import (
    SignalServiceResponse,
    OrderRequest,
    OrderSubmission
)


logger = logging.getLogger(__name__)


# ==============================================================================
# Signal Service Client
# ==============================================================================

class SignalServiceClient:
    """
    HTTP client for Signal Service (T3).

    Fetches trading signals from ML models via REST API.

    Example:
        >>> client = SignalServiceClient("http://localhost:8001")
        >>> signals = await client.fetch_signals(
        ...     symbols=["AAPL", "MSFT", "GOOGL"],
        ...     as_of_date=date(2024, 12, 31)
        ... )
        >>> print(signals.signals[0].symbol)
        'AAPL'
    """

    def __init__(self, base_url: str, timeout: float = 30.0):
        """
        Initialize Signal Service client.

        Args:
            base_url: Base URL of Signal Service (e.g., "http://localhost:8001")
            timeout: Request timeout in seconds (default: 30.0)
        """
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout
        self.client = httpx.AsyncClient(timeout=timeout)

    async def close(self) -> None:
        """Close HTTP client."""
        await self.client.aclose()

    async def health_check(self) -> bool:
        """
        Check if Signal Service is healthy.

        Returns:
            True if service is healthy, False otherwise
        """
        try:
            response = await self.client.get(f"{self.base_url}/health")
            return response.status_code == 200
        except Exception as e:
            logger.warning(f"Signal Service health check failed: {e}")
            return False

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.ConnectError)),
        before_sleep=before_sleep_log(logger, logging.WARNING)
    )
    async def fetch_signals(
        self,
        symbols: List[str],
        as_of_date: Optional[date] = None,
        top_n: Optional[int] = None,
        bottom_n: Optional[int] = None
    ) -> SignalServiceResponse:
        """
        Fetch trading signals from Signal Service.

        Args:
            symbols: List of symbols to generate signals for
            as_of_date: Date for signal generation (defaults to today)
            top_n: Number of long positions (overrides service default)
            bottom_n: Number of short positions (overrides service default)

        Returns:
            SignalServiceResponse with signals and metadata

        Raises:
            httpx.HTTPError: If request fails after retries
            ValueError: If response is invalid

        Example:
            >>> signals = await client.fetch_signals(
            ...     symbols=["AAPL", "MSFT"],
            ...     as_of_date=date(2024, 12, 31),
            ...     top_n=1,
            ...     bottom_n=1
            ... )
            >>> print(len(signals.signals))
            2
        """
        # Build request payload
        payload: dict[str, Any] = {
            "symbols": symbols
        }

        if as_of_date:
            payload["as_of_date"] = as_of_date.isoformat()

        if top_n is not None:
            payload["top_n"] = top_n

        if bottom_n is not None:
            payload["bottom_n"] = bottom_n

        logger.info(
            f"Fetching signals from {self.base_url}",
            extra={
                "num_symbols": len(symbols),
                "as_of_date": payload.get("as_of_date"),
                "top_n": top_n,
                "bottom_n": bottom_n
            }
        )

        # Make request
        response = await self.client.post(
            f"{self.base_url}/api/v1/signals/generate",
            json=payload
        )

        # Check response
        if response.status_code != 200:
            logger.error(
                f"Signal Service returned error: {response.status_code}",
                extra={"response": response.text}
            )
            response.raise_for_status()

        # Parse response
        data = response.json()
        signals_response = SignalServiceResponse(**data)

        logger.info(
            f"Fetched {len(signals_response.signals)} signals",
            extra={
                "num_signals": len(signals_response.signals),
                "model_version": signals_response.metadata.model_version
            }
        )

        return signals_response


# ==============================================================================
# Execution Gateway Client
# ==============================================================================

class ExecutionGatewayClient:
    """
    HTTP client for Execution Gateway (T4).

    Submits orders and queries order status via REST API.

    Example:
        >>> client = ExecutionGatewayClient("http://localhost:8002")
        >>> order = OrderRequest(
        ...     symbol="AAPL",
        ...     side="buy",
        ...     qty=10,
        ...     order_type="market"
        ... )
        >>> submission = await client.submit_order(order)
        >>> print(submission.status)
        'accepted'
    """

    def __init__(self, base_url: str, timeout: float = 30.0):
        """
        Initialize Execution Gateway client.

        Args:
            base_url: Base URL of Execution Gateway (e.g., "http://localhost:8002")
            timeout: Request timeout in seconds (default: 30.0)
        """
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout
        self.client = httpx.AsyncClient(timeout=timeout)

    async def close(self) -> None:
        """Close HTTP client."""
        await self.client.aclose()

    async def health_check(self) -> bool:
        """
        Check if Execution Gateway is healthy.

        Returns:
            True if service is healthy, False otherwise
        """
        try:
            response = await self.client.get(f"{self.base_url}/health")
            return response.status_code == 200
        except Exception as e:
            logger.warning(f"Execution Gateway health check failed: {e}")
            return False

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.ConnectError)),
        before_sleep=before_sleep_log(logger, logging.WARNING)
    )
    async def submit_order(self, order: OrderRequest) -> OrderSubmission:
        """
        Submit order to Execution Gateway.

        Args:
            order: Order request with symbol, side, qty, etc.

        Returns:
            OrderSubmission with client_order_id and status

        Raises:
            httpx.HTTPError: If request fails after retries

        Example:
            >>> order = OrderRequest(
            ...     symbol="AAPL",
            ...     side="buy",
            ...     qty=10,
            ...     order_type="market"
            ... )
            >>> submission = await client.submit_order(order)
            >>> print(submission.client_order_id)
            'a1b2c3d4e5f6...'
        """
        logger.info(
            f"Submitting order: {order.symbol} {order.side} {order.qty}",
            extra={
                "symbol": order.symbol,
                "side": order.side,
                "qty": order.qty,
                "order_type": order.order_type
            }
        )

        # Make request
        response = await self.client.post(
            f"{self.base_url}/api/v1/orders",
            json=order.model_dump(mode="json", exclude_none=True)
        )

        # Check response
        if response.status_code not in (200, 201):
            logger.error(
                f"Execution Gateway returned error: {response.status_code}",
                extra={"response": response.text}
            )
            response.raise_for_status()

        # Parse response
        data = response.json()
        submission = OrderSubmission(**data)

        logger.info(
            f"Order submitted: {submission.client_order_id}",
            extra={
                "client_order_id": submission.client_order_id,
                "status": submission.status,
                "broker_order_id": submission.broker_order_id
            }
        )

        return submission

    async def get_order(self, client_order_id: str) -> dict[str, Any]:
        """
        Get order details by client_order_id.

        Args:
            client_order_id: Client order ID

        Returns:
            Order details

        Raises:
            httpx.HTTPError: If request fails
        """
        response = await self.client.get(
            f"{self.base_url}/api/v1/orders/{client_order_id}"
        )

        if response.status_code != 200:
            response.raise_for_status()

        return response.json()  # type: ignore[no-any-return]

    async def get_positions(self) -> dict[str, Any]:
        """
        Get all current positions.

        Returns:
            Positions response with list of positions and totals

        Raises:
            httpx.HTTPError: If request fails
        """
        response = await self.client.get(
            f"{self.base_url}/api/v1/positions"
        )

        if response.status_code != 200:
            response.raise_for_status()

        return response.json()  # type: ignore[no-any-return]
