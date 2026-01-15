"""Health monitoring library for service health checks and metrics.

This module provides:
- HealthClient: HTTP client for checking service /health endpoints
- PrometheusClient: Client for querying latency metrics from Prometheus
- Models: Pydantic models for health responses and metrics

All clients support graceful degradation with caching and staleness tracking.
"""

from __future__ import annotations

from libs.core.health.health_client import HealthClient, ServiceHealthResponse
from libs.core.health.prometheus_client import LatencyMetrics, PrometheusClient

__all__ = [
    "HealthClient",
    "ServiceHealthResponse",
    "PrometheusClient",
    "LatencyMetrics",
]
