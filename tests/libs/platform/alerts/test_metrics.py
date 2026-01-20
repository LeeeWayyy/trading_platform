"""Tests for alert metrics definitions."""

from prometheus_client import Counter, Gauge, Histogram

from libs.platform.alerts import metrics


def test_metric_types_and_labels() -> None:
    assert isinstance(metrics.alert_delivery_attempts_total, Counter)
    assert metrics.alert_delivery_attempts_total._labelnames == ("channel", "status")

    assert isinstance(metrics.alert_throttle_total, Counter)
    assert metrics.alert_throttle_total._labelnames == ("channel", "limit_type")

    assert isinstance(metrics.alert_dropped_total, Counter)
    assert metrics.alert_dropped_total._labelnames == ("channel", "reason")

    assert isinstance(metrics.alert_queue_full_total, Counter)
    assert metrics.alert_queue_full_total._labelnames == ()

    assert isinstance(metrics.alert_retry_total, Counter)
    assert metrics.alert_retry_total._labelnames == ("channel",)

    assert isinstance(metrics.alert_poison_queue_size, Gauge)
    assert metrics.alert_poison_queue_size._labelnames == ()

    assert isinstance(metrics.alert_queue_depth, Gauge)
    assert metrics.alert_queue_depth._labelnames == ()

    assert isinstance(metrics.alert_delivery_latency_seconds, Histogram)
    assert metrics.alert_delivery_latency_seconds._labelnames == ("channel",)


def test_latency_histogram_buckets() -> None:
    bounds = list(metrics.alert_delivery_latency_seconds._upper_bounds)

    assert bounds[:-1] == [0.1, 0.5, 1, 5, 10, 30, 60]
    assert bounds[-1] == float("inf")
