"""Prometheus metrics for alert delivery service."""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

# Counters for delivery tracking
alert_delivery_attempts_total = Counter(
    "alert_delivery_attempts_total",
    "Total delivery attempts",
    ["channel", "status"],
)

alert_throttle_total = Counter(
    "alert_throttle_total",
    "Deliveries throttled by rate limit",
    ["channel", "limit_type"],
)

alert_dropped_total = Counter(
    "alert_dropped_total",
    "Deliveries dropped (queue full or enqueue failed)",
    ["channel", "reason"],
)

alert_queue_full_total = Counter(
    "alert_queue_full_total",
    "Queue full rejections",
)

alert_retry_total = Counter(
    "alert_retry_total",
    "Total retries scheduled",
    ["channel"],
)

# Gauges for current state
alert_poison_queue_size = Gauge(
    "alert_poison_queue_size",
    "Current poison queue size",
)

alert_queue_depth = Gauge(
    "alert_queue_depth",
    "Current queue depth (pending deliveries)",
)

# Histograms for latency tracking
alert_delivery_latency_seconds = Histogram(
    "alert_delivery_latency_seconds",
    "Delivery latency from enqueue to completion",
    ["channel"],
    buckets=[0.1, 0.5, 1, 5, 10, 30, 60],
)


__all__ = [
    "alert_delivery_attempts_total",
    "alert_throttle_total",
    "alert_dropped_total",
    "alert_queue_full_total",
    "alert_retry_total",
    "alert_poison_queue_size",
    "alert_queue_depth",
    "alert_delivery_latency_seconds",
]
