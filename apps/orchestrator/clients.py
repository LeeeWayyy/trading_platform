"""
HTTP clients for communicating with Signal Service and Execution Gateway.

Provides typed interfaces with automatic retries and error handling.
Includes S2S (service-to-service) authentication via HMAC-signed internal tokens.
"""

import hashlib
import hmac
import json
import logging
import os
import time
import uuid
from datetime import date
from typing import Any

import httpx
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from apps.orchestrator.schemas import OrderRequest, OrderSubmission, SignalServiceResponse

logger = logging.getLogger(__name__)


# ==============================================================================
# S2S Authentication Helpers
# ==============================================================================


def _get_service_secret() -> str:
    """Get the secret for this service (orchestrator).

    SECURITY: Supports per-service secrets for defense in depth.
    If INTERNAL_TOKEN_SECRET_ORCHESTRATOR is set, use it.
    Otherwise, fall back to global INTERNAL_TOKEN_SECRET.
    """
    # Try per-service secret first
    per_service_secret = os.getenv("INTERNAL_TOKEN_SECRET_ORCHESTRATOR", "")
    if per_service_secret:
        return per_service_secret

    # Fall back to global secret
    return os.getenv("INTERNAL_TOKEN_SECRET", "")


def _get_internal_auth_headers(
    method: str,
    path: str,
    query: str | None = None,
    body: bytes | str | None = None,
    user_id: str | None = None,
    strategy_id: str | None = None,
) -> dict[str, str]:
    """Generate HMAC-signed internal auth headers with replay protection and body integrity.

    This function creates S2S authentication headers for internal service calls.
    The signature includes service_id, method, path, query, timestamp, nonce, user_id,
    strategy_id, and body_hash to prevent tampering, replay attacks, and payload modification.

    SECURITY:
    - Query string included in signature to prevent parameter tampering
    - Body hash prevents payload tampering (e.g., changing order quantities)
    - Per-service secrets limit blast radius if one service is compromised
    - Supports INTERNAL_TOKEN_SECRET_ORCHESTRATOR for isolated secret

    Args:
        method: HTTP method (e.g., "POST", "GET")
        path: Request path (e.g., "/api/v1/signals/generate")
        query: Query string without leading '?' (e.g., "limit=10&offset=0")
        body: Request body content (bytes or str) for integrity verification
        user_id: Optional acting user ID for audit trail
        strategy_id: Optional strategy context

    Returns:
        Dictionary of headers to include in the request

    Raises:
        RuntimeError: If INTERNAL_TOKEN_SECRET is not configured (fail-closed)
    """
    secret = _get_service_secret()
    if not secret:
        # SECURITY: Fail-closed - refuse to make unauthenticated requests
        logger.error("INTERNAL_TOKEN_SECRET not set - refusing to make unauthenticated S2S call")
        raise RuntimeError(
            "INTERNAL_TOKEN_SECRET is required for S2S authentication. "
            "Set INTERNAL_TOKEN_SECRET or INTERNAL_TOKEN_SECRET_ORCHESTRATOR environment variable."
        )

    service_id = "orchestrator"
    timestamp = str(int(time.time()))
    nonce = str(uuid.uuid4())

    # Compute body hash for payload integrity
    # SECURITY: Always compute hash, even for empty body (required for POST/PUT/PATCH/DELETE)
    if body is None:
        body_hash = hashlib.sha256(b"").hexdigest()
    elif isinstance(body, str):
        body_hash = hashlib.sha256(body.encode()).hexdigest()
    else:
        body_hash = hashlib.sha256(body).hexdigest()

    # Normalize query string (empty string if None)
    query_str = query or ""

    # Sign: service_id|method|path|query|timestamp|nonce|user_id|strategy_id|body_hash
    # SECURITY: Include query string to prevent parameter tampering
    payload = f"{service_id}|{method}|{path}|{query_str}|{timestamp}|{nonce}|{user_id or ''}|{strategy_id or ''}|{body_hash}"
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()

    headers: dict[str, str] = {
        "X-Internal-Token": signature,
        "X-Internal-Timestamp": timestamp,
        "X-Internal-Nonce": nonce,
        "X-Service-ID": service_id,
        "X-Body-Hash": body_hash,
        # Note: X-Query header removed - server uses request.url.query directly for tamper-proof verification
    }
    if user_id:
        headers["X-User-ID"] = user_id
    if strategy_id:
        headers["X-Strategy-ID"] = strategy_id

    return headers


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
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.client = httpx.AsyncClient(timeout=timeout)

    async def close(self) -> None:
        """Close HTTP client."""
        await self.client.aclose()

    async def health_check(self) -> bool:
        """
        Check if Signal Service is ready for traffic.

        Uses the /ready endpoint which returns 503 when service is degraded
        (e.g., during feature hydration). This prevents the orchestrator from
        proceeding with trading while the signal service is still initializing.

        Returns:
            True if service is fully ready, False otherwise
        """
        try:
            response = await self.client.get(f"{self.base_url}/ready")
            return response.status_code == 200
        except Exception as e:
            logger.warning(f"Signal Service readiness check failed: {e}")
            return False

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.ConnectError)),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    async def fetch_signals(
        self,
        symbols: list[str],
        as_of_date: date | None = None,
        top_n: int | None = None,
        bottom_n: int | None = None,
        user_id: str | None = None,
        strategy_id: str | None = None,
    ) -> SignalServiceResponse:
        """
        Fetch trading signals from Signal Service.

        Args:
            symbols: List of symbols to generate signals for
            as_of_date: Date for signal generation (defaults to today)
            top_n: Number of long positions (overrides service default)
            bottom_n: Number of short positions (overrides service default)
            user_id: Optional acting user ID for audit trail (C6 S2S auth)
            strategy_id: Optional strategy context (C6 S2S auth)

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
        payload: dict[str, Any] = {"symbols": symbols}

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
                "bottom_n": bottom_n,
            },
        )

        # Get S2S auth headers with body hash (C6 - CRITICAL security fix)
        # IMPORTANT: Serialize once and use same bytes for hashing AND sending
        # to ensure body hash matches what server receives
        path = "/api/v1/signals/generate"
        body_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        auth_headers = _get_internal_auth_headers(
            "POST", path, body=body_bytes, user_id=user_id, strategy_id=strategy_id
        )
        auth_headers["Content-Type"] = "application/json"

        # Make request with exact bytes we hashed
        response = await self.client.post(
            f"{self.base_url}{path}", content=body_bytes, headers=auth_headers
        )

        # Check response
        if response.status_code != 200:
            logger.error(
                f"Signal Service returned error: {response.status_code}",
                extra={"response": response.text},
            )
            response.raise_for_status()

        # Parse response
        data = response.json()
        signals_response = SignalServiceResponse(**data)

        logger.info(
            f"Fetched {len(signals_response.signals)} signals",
            extra={
                "num_signals": len(signals_response.signals),
                "model_version": signals_response.metadata.model_version,
            },
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
        self.base_url = base_url.rstrip("/")
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
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    async def submit_order(
        self,
        order: OrderRequest,
        user_id: str | None = None,
        strategy_id: str | None = None,
    ) -> OrderSubmission:
        """
        Submit order to Execution Gateway.

        Args:
            order: Order request with symbol, side, qty, etc.
            user_id: Optional acting user ID for audit trail (C6 S2S auth)
            strategy_id: Optional strategy context (C6 S2S auth)

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
                "order_type": order.order_type,
            },
        )

        # Get S2S auth headers with body hash (C6 - CRITICAL security fix)
        # IMPORTANT: Serialize once and use same bytes for hashing AND sending
        # to ensure body hash matches what server receives
        path = "/api/v1/orders"
        order_payload = order.model_dump(mode="json", exclude_none=True)
        body_bytes = json.dumps(order_payload, separators=(",", ":"), sort_keys=True).encode()
        auth_headers = _get_internal_auth_headers(
            "POST", path, body=body_bytes, user_id=user_id, strategy_id=strategy_id
        )
        auth_headers["Content-Type"] = "application/json"

        # Make request with exact bytes we hashed
        response = await self.client.post(
            f"{self.base_url}{path}",
            content=body_bytes,
            headers=auth_headers,
        )

        # Check response
        if response.status_code not in (200, 201):
            logger.error(
                f"Execution Gateway returned error: {response.status_code}",
                extra={"response": response.text},
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
                "broker_order_id": submission.broker_order_id,
            },
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
        path = f"/api/v1/orders/{client_order_id}"
        auth_headers = _get_internal_auth_headers("GET", path)

        response = await self.client.get(f"{self.base_url}{path}", headers=auth_headers)

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
        path = "/api/v1/positions"
        auth_headers = _get_internal_auth_headers("GET", path)

        response = await self.client.get(f"{self.base_url}{path}", headers=auth_headers)

        if response.status_code != 200:
            response.raise_for_status()

        return response.json()  # type: ignore[no-any-return]
