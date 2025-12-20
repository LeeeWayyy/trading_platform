"""Prometheus client for latency metrics."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

import httpx
from pydantic import BaseModel, ConfigDict

logger = logging.getLogger(__name__)


class LatencyMetrics(BaseModel):
    """Latency percentiles for a service operation."""

    model_config = ConfigDict(extra="allow")  # Forward compatibility

    service: str
    operation: str
    p50_ms: float | None
    p95_ms: float | None
    p99_ms: float | None
    error: str | None = None
    # Staleness tracking (graceful degradation)
    is_stale: bool = False
    stale_age_seconds: float | None = None
    fetched_at: datetime | None = None


class PrometheusClient:
    """Client for querying Prometheus metrics."""

    # Latency query map for services with histograms
    LATENCY_METRICS = {
        "signal_service": {
            "metric": "signal_generation_duration_seconds",
            "operation": "signal_generation",
        },
        "execution_gateway": {
            "metric": "order_placement_duration_seconds",
            "operation": "order_placement",
        },
        "orchestrator": {
            "metric": "orchestration_duration_seconds",
            "operation": "orchestration",
        },
        "market_data_service": {
            "metric": "market_data_processing_duration_seconds",
            "operation": "market_data_processing",
        },
        "reconciler": {
            "metric": "reconciliation_duration_seconds",
            "operation": "reconciliation",
        },
    }

    def __init__(
        self,
        prometheus_url: str,
        timeout_seconds: float = 5.0,
        cache_ttl_seconds: int = 10,
    ) -> None:
        """Initialize Prometheus client with per-refresh caching."""
        self.base_url = prometheus_url.rstrip("/")
        self.timeout = timeout_seconds
        self.cache_ttl = timedelta(seconds=cache_ttl_seconds)
        self._cache: dict[str, tuple[dict[str, LatencyMetrics], datetime]] = {}
        # Shared client for connection reuse (created lazily)
        self._client: httpx.AsyncClient | None = None

    def _get_stale_latencies_or_none(
        self,
        cache_key: str,
        now: datetime,
    ) -> tuple[dict[str, LatencyMetrics], bool, float] | None:
        """Return stale cached latencies if available, else None.

        Used for graceful degradation when Prometheus queries fail.

        Returns:
            Tuple of (stale_results, is_stale=True, stale_age) or None if no cache.
        """
        if cache_key not in self._cache:
            return None
        cached_result, cached_at = self._cache[cache_key]
        stale_age = (now - cached_at).total_seconds()
        stale_results = {
            key: value.model_copy(
                update={
                    "is_stale": True,
                    "stale_age_seconds": stale_age,
                    "fetched_at": cached_at,
                }
            )
            for key, value in cached_result.items()
        }
        return stale_results, True, stale_age

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create shared HTTP client for connection reuse."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self.timeout,
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            )
        return self._client

    async def verify_histograms(self) -> dict[str, bool]:
        """Verify which configured histograms exist in Prometheus."""
        results: dict[str, bool] = {}
        for service, config in self.LATENCY_METRICS.items():
            metric = config["metric"]
            query = f'{metric}_bucket{{le=\"1\"}}'
            try:
                client = await self._get_client()
                response = await client.get(
                    f"{self.base_url}/api/v1/query",
                    params={"query": query},
                )
                response.raise_for_status()
                data = response.json()
                has_data = bool(data.get("data", {}).get("result", []))
                results[service] = has_data
                if not has_data:
                    logger.info(
                        "Latency histogram not instrumented: %s (%s)",
                        service,
                        metric,
                    )
            except (TimeoutError, httpx.RequestError, httpx.HTTPStatusError) as exc:
                logger.debug("Histogram verification failed for %s: %s", service, exc)
                results[service] = False
        return results

    async def get_latency_percentile(
        self,
        metric_name: str,
        percentile: float,
        range_minutes: int = 5,
    ) -> float | None:
        """Query a latency percentile from Prometheus."""
        # Use sum(...) by (le) to aggregate across all label sets before percentile calculation
        query = f"histogram_quantile({percentile}, sum(rate({metric_name}_bucket[{range_minutes}m])) by (le))"
        try:
            client = await self._get_client()
            response = await client.get(
                f"{self.base_url}/api/v1/query",
                params={"query": query},
            )
            response.raise_for_status()
            data = response.json()
            if data.get("status") == "success":
                results = data.get("data", {}).get("result", [])
                if results:
                    value_str = results[0].get("value", [None, None])[1]
                    if value_str is None:
                        return None
                    return float(value_str) * 1000  # seconds to ms
            return None
        except (TimeoutError, httpx.RequestError, httpx.HTTPStatusError) as exc:
            # Network/HTTP errors - Prometheus may be unavailable
            logger.warning("Prometheus query failed for %s: %s", metric_name, exc)
            return None
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            # Parsing errors - unexpected response format
            logger.warning("Failed to parse Prometheus response for %s: %s", metric_name, exc)
            return None

    async def get_service_latencies(self) -> tuple[dict[str, LatencyMetrics], bool, float | None]:
        """Get latency metrics for all tracked services with caching."""
        now = datetime.now(UTC)
        cache_key = "all_latencies"

        if cache_key in self._cache:
            cached_result, cached_at = self._cache[cache_key]
            cache_age = now - cached_at
            if cache_age < self.cache_ttl:
                return cached_result, False, None

        try:
            results = await self._fetch_latencies_from_prometheus()

            # Check if all results are errors (all services have None latencies)
            # This indicates Prometheus is completely unavailable
            all_failed = all(
                m.p50_ms is None and m.p95_ms is None and m.p99_ms is None
                for m in results.values()
            ) if results else True

            if all_failed:
                # Use stale cache instead of empty results
                stale_result = self._get_stale_latencies_or_none(cache_key, now)
                if stale_result:
                    logger.warning(
                        "All Prometheus queries failed, using stale cache (%.0fs old)",
                        stale_result[2],  # stale_age
                    )
                    return stale_result

            # Fresh results (at least some succeeded)
            self._cache[cache_key] = (results, now)
            return results, False, None
        except (TimeoutError, httpx.RequestError, httpx.HTTPStatusError) as exc:
            logger.warning("Prometheus unavailable, using stale cache: %s", exc)
            stale_result = self._get_stale_latencies_or_none(cache_key, now)
            if stale_result:
                return stale_result
            return {}, True, None

    async def _fetch_latencies_from_prometheus(self) -> dict[str, LatencyMetrics]:
        """Fetch fresh latency data from Prometheus with parallelized queries."""
        now = datetime.now(UTC)

        async def fetch_service_latencies(
            service: str, config: dict[str, str]
        ) -> tuple[str, LatencyMetrics]:
            metric = config["metric"]
            try:
                p50, p95, p99 = await asyncio.gather(
                    self.get_latency_percentile(metric, 0.50),
                    self.get_latency_percentile(metric, 0.95),
                    self.get_latency_percentile(metric, 0.99),
                )
                return service, LatencyMetrics(
                    service=service,
                    operation=config["operation"],
                    p50_ms=p50,
                    p95_ms=p95,
                    p99_ms=p99,
                    fetched_at=now,
                )
            except (TimeoutError, httpx.RequestError, httpx.HTTPStatusError) as exc:
                # Network/HTTP errors - service may be unavailable
                logger.warning("Failed to fetch latencies for %s: %s", service, exc)
                return service, LatencyMetrics(
                    service=service,
                    operation=config["operation"],
                    p50_ms=None,
                    p95_ms=None,
                    p99_ms=None,
                    error=str(exc),
                    fetched_at=now,
                )

        tasks = [
            fetch_service_latencies(service, config)
            for service, config in self.LATENCY_METRICS.items()
        ]
        results_list = await asyncio.gather(*tasks)
        return dict(results_list)

    async def close(self) -> None:
        """Close the shared HTTP client to release resources."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
