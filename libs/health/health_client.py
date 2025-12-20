"""Health check client for service health endpoints."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict

logger = logging.getLogger(__name__)


class ServiceHealthResponse(BaseModel):
    """Normalized health response from any service."""

    model_config = ConfigDict(extra="allow")  # Forward compatibility

    status: str  # healthy, degraded, unhealthy, stale, unreachable, unknown
    service: str
    timestamp: datetime
    response_time_ms: float
    details: dict[str, Any]  # Service-specific fields
    error: str | None = None
    # Staleness tracking for graceful degradation
    is_stale: bool = False
    stale_age_seconds: float | None = None  # How old the cached data is
    last_operation_timestamp: datetime | None = None  # Last successful operation


class HealthClient:
    """Client for checking service health endpoints.

    Supports caching for graceful degradation when services are unreachable.
    """

    def __init__(
        self,
        service_urls: dict[str, str],
        timeout_seconds: float = 5.0,
        cache_ttl_seconds: int = 30,
    ) -> None:
        """Initialize health client.

        Args:
            service_urls: Dict mapping service name to base URL
            timeout_seconds: HTTP timeout for health checks
            cache_ttl_seconds: How long to cache responses for fallback
        """
        self.service_urls = service_urls
        self.timeout = timeout_seconds
        self.cache_ttl = timedelta(seconds=cache_ttl_seconds)
        self._cache: dict[str, tuple[ServiceHealthResponse, datetime]] = {}
        # Shared client for connection reuse (created lazily)
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create shared HTTP client for connection reuse."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self.timeout,
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            )
        return self._client

    async def check_service(self, service_name: str) -> ServiceHealthResponse:
        """Check health of a single service.

        Returns cached response if fetch fails and cache is valid.
        Per-service errors are isolated - one bad response won't affect others.
        """
        url = self.service_urls.get(service_name)
        if not url:
            return ServiceHealthResponse(
                status="unknown",
                service=service_name,
                timestamp=datetime.now(UTC),
                response_time_ms=0.0,
                details={},
                error=f"Unknown service: {service_name}",
            )

        start = datetime.now(UTC)
        try:
            client = await self._get_client()
            response = await client.get(f"{url}/health")
            elapsed_ms = (datetime.now(UTC) - start).total_seconds() * 1000

            if response.status_code == 200:
                try:
                    data = response.json()
                except (json.JSONDecodeError, ValueError) as e:
                    # Malformed JSON - treat as error, don't crash entire grid
                    return self._handle_error(
                        service_name,
                        start,
                        f"Invalid JSON response: {e}",
                    )

                # Extract last operation timestamp from response
                last_op = self._extract_last_operation_timestamp(data)

                result = ServiceHealthResponse(
                    status=data.get("status", "unknown"),
                    service=data.get("service", service_name),
                    timestamp=datetime.now(UTC),
                    response_time_ms=elapsed_ms,
                    details=data,
                    last_operation_timestamp=last_op,
                )
                self._cache[service_name] = (result, datetime.now(UTC))
                return result
            else:
                # Service responded but not healthy
                return self._handle_error(
                    service_name,
                    start,
                    f"HTTP {response.status_code}",
                )

        except httpx.TimeoutException:
            return self._handle_error(service_name, start, "Timeout")
        except httpx.RequestError as e:
            return self._handle_error(service_name, start, str(e))

    def _extract_last_operation_timestamp(self, data: dict[str, Any]) -> datetime | None:
        """Extract last operation timestamp from health response.

        Priority order:
        1. Explicit last_* fields (per-service mapping from plan)
        2. Response timestamp field
        3. None if not available
        """
        # Look for explicit last operation fields - COVERS ALL 8 SERVICES
        last_op_keys = [
            # signal_service
            "last_signal_at",
            "last_signal_generated_at",
            # execution_gateway
            "last_order_at",
            "last_processed_at",
            # orchestrator
            "last_orchestration_at",
            # market_data_service
            "last_message_at",
            # reconciler (critical for state sync)
            "last_reconciliation_at",
            # risk_manager (critical for circuit breaker)
            "last_risk_check_at",
            # generic fallbacks
            "last_operation_at",
        ]
        for key in last_op_keys:
            if key in data and data[key]:
                try:
                    return datetime.fromisoformat(data[key].replace("Z", "+00:00"))
                except (ValueError, AttributeError):
                    pass

        # Fall back to response timestamp
        if "timestamp" in data:
            try:
                ts = data["timestamp"]
                if isinstance(ts, str):
                    return datetime.fromisoformat(ts.replace("Z", "+00:00"))
                elif isinstance(ts, datetime):
                    return ts
            except (ValueError, AttributeError):
                pass

        return None

    def _handle_error(
        self,
        service_name: str,
        start: datetime,
        error: str,
    ) -> ServiceHealthResponse:
        """Handle error with cache fallback and staleness tracking."""
        elapsed_ms = (datetime.now(UTC) - start).total_seconds() * 1000
        now = datetime.now(UTC)

        # Try cache fallback
        if service_name in self._cache:
            cached, cached_at = self._cache[service_name]
            cache_age = now - cached_at
            if cache_age < self.cache_ttl:
                # Return cached with staleness indicator and age
                # Note: Don't modify details dict - staleness is conveyed via is_stale/stale_age_seconds
                return cached.model_copy(
                    update={
                        "status": "stale",
                        "response_time_ms": elapsed_ms,
                        "error": f"Using cached data ({cache_age.total_seconds():.0f}s old): {error}",
                        "is_stale": True,
                        "stale_age_seconds": cache_age.total_seconds(),
                    }
                )

        return ServiceHealthResponse(
            status="unreachable",
            service=service_name,
            timestamp=now,
            response_time_ms=elapsed_ms,
            details={},
            error=error,
        )

    async def check_all(self) -> dict[str, ServiceHealthResponse]:
        """Check all services in parallel."""
        tasks = [self.check_service(name) for name in self.service_urls.keys()]
        results = await asyncio.gather(*tasks)
        return {r.service: r for r in results}

    async def close(self) -> None:
        """Close the shared HTTP client to release resources."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
