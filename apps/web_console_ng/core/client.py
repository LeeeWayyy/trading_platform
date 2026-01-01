"""Async HTTP client for trading API calls."""

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

    def _get_auth_headers(self, user_id: str) -> dict[str, str]:
        headers: dict[str, str] = {}

        role = config.DEV_ROLE
        strategies = list(config.DEV_STRATEGIES)
        resolved_user_id = user_id or config.DEV_USER_ID

        if role:
            headers["X-User-Role"] = str(role)
        if resolved_user_id:
            headers["X-User-Id"] = str(resolved_user_id)
        if strategies:
            headers["X-User-Strategies"] = ",".join(sorted(strategies))

        internal_secret = os.getenv("INTERNAL_TOKEN_SECRET", "").strip()
        if internal_secret and resolved_user_id and role is not None:
            timestamp = str(int(time.time()))
            strategies_str = ",".join(sorted(strategies)) if strategies else ""
            payload_data = {
                "uid": str(resolved_user_id).strip(),
                "role": str(role).strip(),
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

        return headers

    def _json_dict(self, response: httpx.Response) -> dict[str, Any]:
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Expected JSON object response")
        return cast(dict[str, Any], payload)

    @with_retry(max_attempts=3, backoff_base=1.0, method="GET")
    async def fetch_positions(self, user_id: str) -> dict[str, Any]:
        """Fetch current positions (GET - idempotent)."""
        headers = self._get_auth_headers(user_id)
        resp = await self._client.get("/api/v1/positions", headers=headers)
        resp.raise_for_status()
        return self._json_dict(resp)

    @with_retry(max_attempts=3, backoff_base=1.0, method="POST")
    async def trigger_kill_switch(self, user_id: str) -> dict[str, Any]:
        """Trigger kill switch (POST - non-idempotent, no 5xx retry)."""
        headers = self._get_auth_headers(user_id)
        resp = await self._client.post("/api/v1/kill-switch", headers=headers)
        resp.raise_for_status()
        return self._json_dict(resp)

    @with_retry(max_attempts=3, backoff_base=1.0, method="GET")
    async def get_circuit_breaker_state(self, user_id: str) -> dict[str, Any]:
        """Fetch circuit breaker state (GET - idempotent)."""
        headers = self._get_auth_headers(user_id)
        resp = await self._client.get("/api/v1/circuit-breaker/status", headers=headers)
        resp.raise_for_status()
        return self._json_dict(resp)

    @with_retry(max_attempts=3, backoff_base=1.0, method="GET")
    async def fetch_kill_switch_status(self, user_id: str) -> dict[str, Any]:
        """Fetch kill switch status (GET - idempotent).

        Returns dict with 'state' key: 'ENGAGED' or 'DISENGAGED'.
        """
        headers = self._get_auth_headers(user_id)
        resp = await self._client.get("/api/v1/kill-switch/status", headers=headers)
        resp.raise_for_status()
        return self._json_dict(resp)
