"""Runtime tests for metrics module initialization."""

from __future__ import annotations

import importlib
import sys
from typing import Iterable

import pytest
from prometheus_client import REGISTRY


def _clear_registry() -> None:
    collectors = list(REGISTRY._collector_to_names)  # type: ignore[attr-defined]
    for collector in collectors:
        REGISTRY.unregister(collector)


@pytest.fixture
def clean_prometheus_registry() -> Iterable[None]:
    original_collectors = list(REGISTRY._collector_to_names)  # type: ignore[attr-defined]
    _clear_registry()
    yield
    _clear_registry()
    for collector in original_collectors:
        REGISTRY.register(collector)


def test_initialize_metrics_sets_defaults(clean_prometheus_registry):
    if "apps.execution_gateway.metrics" in sys.modules:
        del sys.modules["apps.execution_gateway.metrics"]

    metrics = importlib.import_module("apps.execution_gateway.metrics")
    metrics.initialize_metrics(dry_run=True)

    assert metrics.dry_run_mode._value.get() == 1  # type: ignore[attr-defined]
    assert metrics.database_connection_status._value.get() == 0  # type: ignore[attr-defined]
    assert metrics.redis_connection_status._value.get() == 0  # type: ignore[attr-defined]
    assert metrics.alpaca_connection_status._value.get() == 0  # type: ignore[attr-defined]


def test_metric_registries_consistent(clean_prometheus_registry):
    if "apps.execution_gateway.metrics" in sys.modules:
        del sys.modules["apps.execution_gateway.metrics"]

    metrics = importlib.import_module("apps.execution_gateway.metrics")
    assert set(metrics.METRIC_NAMES) == set(metrics.METRIC_LABELS.keys())

