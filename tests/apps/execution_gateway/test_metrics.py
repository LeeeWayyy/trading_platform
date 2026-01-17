"""Runtime tests for metrics module initialization."""

from __future__ import annotations

import importlib
import sys
from collections.abc import Iterable

import pytest
from prometheus_client import REGISTRY


@pytest.fixture(autouse=True)
def _restore_metrics_module() -> Iterable[None]:
    """Restore metrics module after tests that reload it.

    Some tests delete and re-import apps.execution_gateway.metrics to verify
    initialization behavior. We restore the original module afterward so the
    module-level metrics align with the registry for subsequent tests.
    """
    original_module = sys.modules.get("apps.execution_gateway.metrics")
    yield
    if original_module is None:
        sys.modules.pop("apps.execution_gateway.metrics", None)
    else:
        sys.modules["apps.execution_gateway.metrics"] = original_module


def _clear_registry() -> None:
    collectors = list(REGISTRY._collector_to_names)  # type: ignore[attr-defined]
    for collector in collectors:
        REGISTRY.unregister(collector)


@pytest.fixture()
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
