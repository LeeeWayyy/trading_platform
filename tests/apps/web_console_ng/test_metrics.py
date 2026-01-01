from __future__ import annotations

import pytest

from apps.web_console_ng import config
from apps.web_console_ng.core import health
from apps.web_console_ng.core import metrics as metrics_module


def test_metrics_include_pod_label():
    metrics = [
        metrics_module.ws_connections,
        metrics_module.ws_connects_total,
        metrics_module.ws_disconnects_total,
        metrics_module.connections_rejected_total,
        metrics_module.auth_failures_total,
        metrics_module.sessions_created_total,
        metrics_module.api_latency_seconds,
        metrics_module.redis_latency_seconds,
        metrics_module.state_save_errors_total,
        metrics_module.circuit_breaker_state,
    ]
    for metric in metrics:
        assert "pod" in metric._labelnames


@pytest.mark.asyncio()
async def test_update_resource_metrics_sets_ws_connections():
    health.connection_counter._count = 0
    health.connection_counter.increment()
    health.connection_counter.increment()

    metrics_module.ws_connections.labels(pod=config.POD_NAME).set(0)
    await metrics_module.update_resource_metrics()

    value = metrics_module.ws_connections.labels(pod=config.POD_NAME)._value.get()
    assert value == 2


def test_state_save_error_reason_sanitized():
    metrics_module.record_state_save_error("Redis timeout: connection reset")
    label_values = list(metrics_module.state_save_errors_total._metrics.keys())
    assert label_values
    _, reason = label_values[-1]
    assert ":" not in reason
    assert " " not in reason
