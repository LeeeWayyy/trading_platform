"""Async HTTP client for trading API calls.

TODO(P5T6+): Add circuit breaker to prevent retry storms during backend outages.
The current implementation uses @with_retry for resilience but lacks a circuit
breaker pattern. When the Execution Gateway is unavailable, this client will
aggressively retry which could worsen cascading failures. Consider adding
pybreaker or a custom circuit breaker implementation in a future task.
See: ADR-0031-nicegui-migration for architecture decisions.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from typing import Any, cast

import httpx

from apps.web_console_ng import config
from apps.web_console_ng.core.retry import with_retry


class AsyncTradingClient:
    """Async HTTP client for trading API calls."""

    _instance: AsyncTradingClient | None = None

    def __init__(self) -> None:
        self._http_client: httpx.AsyncClient | None = None

    @classmethod
    def get(cls) -> AsyncTradingClient:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @property
    def _client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            raise RuntimeError("Client not initialized - call startup() first")
        return self._http_client

    async def startup(self) -> None:
        """Initialize client on app startup."""
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                base_url=config.EXECUTION_GATEWAY_URL,
                timeout=httpx.Timeout(5.0, connect=2.0),
                headers={"Content-Type": "application/json"},
            )

    async def shutdown(self) -> None:
        """Close client on app shutdown."""
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    def _get_auth_headers(
        self,
        user_id: str,
        role: str | None = None,
        strategies: list[str] | None = None,
    ) -> dict[str, str]:
        """Build auth headers for backend requests.

        Args:
            user_id: User ID from session (required in production).
            role: User role from session. Falls back to DEV_ROLE only in DEBUG mode.
            strategies: User strategies from session. Falls back to DEV_STRATEGIES only in DEBUG.

        Returns:
            Dict of auth headers including signature if INTERNAL_TOKEN_SECRET is set.

        Raises:
            ValueError: In production mode if required auth context is missing.
        """
        headers: dict[str, str] = {}

        # SECURITY: Only use DEV_* fallbacks in DEBUG mode
        resolved_role: str | None
        resolved_strategies: list[str]
        resolved_user_id: str
        if config.DEBUG:
            resolved_role = role if role is not None else config.DEV_ROLE
            resolved_strategies = (
                strategies if strategies is not None else list(config.DEV_STRATEGIES)
            )
            resolved_user_id = user_id or config.DEV_USER_ID
        else:
            # Production mode: require actual user context
            resolved_role = role
            resolved_strategies = strategies or []
            resolved_user_id = user_id or ""

            # Fail closed: in production, require user context when signature is needed
            internal_secret = os.getenv("INTERNAL_TOKEN_SECRET", "").strip()
            if internal_secret and not resolved_user_id:
                raise ValueError("User ID required for authenticated requests in production mode")

        if resolved_role:
            headers["X-User-Role"] = str(resolved_role)
        if resolved_user_id:
            headers["X-User-Id"] = str(resolved_user_id)
        if resolved_strategies:
            headers["X-User-Strategies"] = ",".join(sorted(resolved_strategies))

        internal_secret = os.getenv("INTERNAL_TOKEN_SECRET", "").strip()
        if internal_secret and resolved_user_id and resolved_role is not None:
            timestamp = str(int(time.time()))
            strategies_str = ",".join(sorted(resolved_strategies)) if resolved_strategies else ""
            payload_data = {
                "uid": str(resolved_user_id).strip(),
                "role": str(resolved_role).strip(),
                "strats": strategies_str,
                "ts": timestamp,
            }
            payload = json.dumps(payload_data, separators=(",", ":"), sort_keys=True)

            signature = hmac.new(
                internal_secret.encode("utf-8"),
                payload.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()

            headers["X-Request-Timestamp"] = timestamp
            headers["X-User-Signature"] = signature
        elif internal_secret and not config.DEBUG:
            # SECURITY: In production with INTERNAL_TOKEN_SECRET, require complete auth context
            # Raise instead of silently sending unauthenticated request
            raise ValueError(
                "Role required for authenticated requests in production mode "
                "(INTERNAL_TOKEN_SECRET is set but role is missing)"
            )

        return headers

    def _json_dict(self, response: httpx.Response) -> dict[str, Any]:
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Expected JSON object response")
        return cast(dict[str, Any], payload)

    @with_retry(max_attempts=3, backoff_base=1.0, method="GET")
    async def fetch_positions(
        self,
        user_id: str,
        role: str | None = None,
        strategies: list[str] | None = None,
    ) -> dict[str, Any]:
        """Fetch current positions (GET - idempotent)."""
        headers = self._get_auth_headers(user_id, role, strategies)
        resp = await self._client.get("/api/v1/positions", headers=headers)
        resp.raise_for_status()
        return self._json_dict(resp)

    @with_retry(max_attempts=3, backoff_base=1.0, method="POST")
    async def engage_kill_switch(
        self,
        user_id: str,
        reason: str,
        role: str | None = None,
        strategies: list[str] | None = None,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Engage kill switch - emergency trading halt (POST).

        Args:
            user_id: User performing the action (maps to 'operator').
            reason: Human-readable reason for engagement.
            role: User role for authorization.
            strategies: User strategies.
            details: Optional additional context.
        """
        headers = self._get_auth_headers(user_id, role, strategies)
        payload: dict[str, Any] = {
            "operator": user_id,
            "reason": reason,
        }
        if details:
            payload["details"] = details
        resp = await self._client.post(
            "/api/v1/kill-switch/engage", headers=headers, json=payload
        )
        resp.raise_for_status()
        return self._json_dict(resp)

    @with_retry(max_attempts=3, backoff_base=1.0, method="POST")
    async def disengage_kill_switch(
        self,
        user_id: str,
        role: str | None = None,
        strategies: list[str] | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        """Disengage kill switch - resume trading (POST).

        Args:
            user_id: User performing the action (maps to 'operator').
            role: User role for authorization.
            strategies: User strategies.
            notes: Optional notes about resolution.
        """
        headers = self._get_auth_headers(user_id, role, strategies)
        payload: dict[str, Any] = {"operator": user_id}
        if notes:
            payload["notes"] = notes
        resp = await self._client.post(
            "/api/v1/kill-switch/disengage", headers=headers, json=payload
        )
        resp.raise_for_status()
        return self._json_dict(resp)

    @with_retry(max_attempts=3, backoff_base=1.0, method="GET")
    async def fetch_kill_switch_status(
        self,
        user_id: str,
        role: str | None = None,
        strategies: list[str] | None = None,
    ) -> dict[str, Any]:
        """Fetch kill switch status (GET - idempotent).

        Returns dict with 'state' key: 'ENGAGED' or 'DISENGAGED'.
        """
        headers = self._get_auth_headers(user_id, role, strategies)
        resp = await self._client.get("/api/v1/kill-switch/status", headers=headers)
        resp.raise_for_status()
        payload = self._json_dict(resp)
        state = str(payload.get("state", "")).upper()
        if state == "ACTIVE":
            payload["state"] = "DISENGAGED"
        return payload

    @with_retry(max_attempts=3, backoff_base=1.0, method="GET")
    async def fetch_circuit_breaker_status(
        self,
        user_id: str,
        role: str | None = None,
        strategies: list[str] | None = None,
    ) -> dict[str, Any]:
        """Fetch circuit breaker status (GET - idempotent)."""
        headers = self._get_auth_headers(user_id, role, strategies)
        resp = await self._client.get("/api/v1/circuit-breaker/status", headers=headers)
        resp.raise_for_status()
        return self._json_dict(resp)

    @with_retry(max_attempts=3, backoff_base=1.0, method="GET")
    async def fetch_open_orders(
        self,
        user_id: str,
        role: str | None = None,
        strategies: list[str] | None = None,
    ) -> dict[str, Any]:
        """Fetch open/pending orders (GET - idempotent).

        Uses /api/v1/orders/pending endpoint from manual_controls router.
        Returns dict with 'orders' list and 'total' count.
        """
        headers = self._get_auth_headers(user_id, role, strategies)
        resp = await self._client.get("/api/v1/orders/pending", headers=headers)
        resp.raise_for_status()
        return self._json_dict(resp)

    @with_retry(max_attempts=3, backoff_base=1.0, method="GET")
    async def fetch_realtime_pnl(
        self,
        user_id: str,
        role: str | None = None,
        strategies: list[str] | None = None,
    ) -> dict[str, Any]:
        """Fetch real-time P&L summary (GET - idempotent).

        Uses /api/v1/positions/pnl/realtime endpoint.
        Returns RealtimePnLResponse with total_unrealized_pl, realized_pl_today, etc.
        """
        headers = self._get_auth_headers(user_id, role, strategies)
        resp = await self._client.get("/api/v1/positions/pnl/realtime", headers=headers)
        resp.raise_for_status()
        return self._json_dict(resp)

    @with_retry(max_attempts=3, backoff_base=1.0, method="GET")
    async def fetch_account_info(
        self,
        user_id: str,
        role: str | None = None,
        strategies: list[str] | None = None,
    ) -> dict[str, Any]:
        """Fetch account info (buying power, cash, portfolio value)."""
        headers = self._get_auth_headers(user_id, role, strategies)
        resp = await self._client.get("/api/v1/account", headers=headers)
        resp.raise_for_status()
        return self._json_dict(resp)

    @with_retry(max_attempts=3, backoff_base=1.0, method="GET")
    async def fetch_market_prices(
        self,
        user_id: str,
        role: str | None = None,
        strategies: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch market prices (GET - idempotent, requires auth)."""
        headers = self._get_auth_headers(user_id, role, strategies)
        resp = await self._client.get("/api/v1/market_prices", headers=headers)
        resp.raise_for_status()
        payload = resp.json()
        if not isinstance(payload, list):
            raise ValueError("Expected JSON array response")
        return cast(list[dict[str, Any]], payload)

    @with_retry(max_attempts=3, backoff_base=1.0, method="POST")
    async def cancel_order(
        self,
        order_id: str,
        user_id: str,
        role: str | None = None,
    ) -> dict[str, Any]:
        """Cancel an order by client_order_id (POST)."""
        headers = self._get_auth_headers(user_id, role, None)
        resp = await self._client.post(f"/api/v1/orders/{order_id}/cancel", headers=headers)
        resp.raise_for_status()
        return self._json_dict(resp)

    @with_retry(max_attempts=3, backoff_base=1.0, method="POST")
    async def submit_order(
        self,
        order_data: dict[str, Any],
        user_id: str,
        role: str | None = None,
    ) -> dict[str, Any]:
        """Submit a new order (POST)."""
        headers = self._get_auth_headers(user_id, role, None)
        resp = await self._client.post("/api/v1/orders", headers=headers, json=order_data)
        resp.raise_for_status()
        return self._json_dict(resp)

    # ===== T5.3 Manual Controls Endpoints =====

    @with_retry(max_attempts=3, backoff_base=1.0, method="POST")
    async def close_position(
        self,
        symbol: str,
        reason: str,
        requested_by: str,
        requested_at: str,
        user_id: str,
        role: str | None = None,
        qty: int | None = None,
    ) -> dict[str, Any]:
        """Close a position for a symbol (POST).

        Args:
            symbol: Symbol to close position for.
            reason: Reason for closing (min 10 chars).
            requested_by: User ID who requested the action.
            requested_at: ISO timestamp of request.
            user_id: User ID for auth headers.
            role: User role for authorization.
            qty: Optional partial close quantity.

        Returns:
            ClosePositionResponse with status, order_id, qty_to_close.
        """
        headers = self._get_auth_headers(user_id, role, None)
        payload: dict[str, Any] = {
            "reason": reason,
            "requested_by": requested_by,
            "requested_at": requested_at,
        }
        if qty is not None:
            payload["qty"] = qty
        resp = await self._client.post(
            f"/api/v1/positions/{symbol.upper()}/close", headers=headers, json=payload
        )
        resp.raise_for_status()
        return self._json_dict(resp)

    @with_retry(max_attempts=3, backoff_base=1.0, method="POST")
    async def cancel_all_orders(
        self,
        symbol: str,
        reason: str,
        requested_by: str,
        requested_at: str,
        user_id: str,
        role: str | None = None,
    ) -> dict[str, Any]:
        """Cancel all orders for a symbol (POST).

        Args:
            symbol: Symbol to cancel orders for.
            reason: Reason for cancellation (min 10 chars).
            requested_by: User ID who requested the action.
            requested_at: ISO timestamp of request.
            user_id: User ID for auth headers.
            role: User role for authorization.

        Returns:
            CancelAllOrdersResponse with cancelled_count, order_ids.
        """
        headers = self._get_auth_headers(user_id, role, None)
        payload = {
            "symbol": symbol.upper(),
            "reason": reason,
            "requested_by": requested_by,
            "requested_at": requested_at,
        }
        resp = await self._client.post(
            "/api/v1/orders/cancel-all", headers=headers, json=payload
        )
        resp.raise_for_status()
        return self._json_dict(resp)

    @with_retry(max_attempts=3, backoff_base=1.0, method="POST")
    async def flatten_all_positions(
        self,
        reason: str,
        requested_by: str,
        requested_at: str,
        id_token: str,
        user_id: str,
        role: str | None = None,
    ) -> dict[str, Any]:
        """Flatten all positions (POST) - requires MFA.

        CRITICAL: This endpoint requires MFA verification via id_token.
        The id_token must be obtained from the OAuth2 session.

        Args:
            reason: Reason for flattening (min 20 chars).
            requested_by: User ID who requested the action.
            requested_at: ISO timestamp of request.
            id_token: MFA token from auth session.
            user_id: User ID for auth headers.
            role: User role for authorization.

        Returns:
            FlattenAllResponse with positions_closed, orders_created.
        """
        headers = self._get_auth_headers(user_id, role, None)
        payload = {
            "reason": reason,
            "requested_by": requested_by,
            "requested_at": requested_at,
            "id_token": id_token,
        }
        resp = await self._client.post(
            "/api/v1/positions/flatten-all", headers=headers, json=payload
        )
        resp.raise_for_status()
        return self._json_dict(resp)
