"""Unit tests for health page."""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

os.environ["WEB_CONSOLE_NG_DEBUG"] = "true"
os.environ.setdefault("NICEGUI_STORAGE_SECRET", "test-secret")

from apps.web_console_ng.pages import health as health_module
from tests.apps.web_console_ng.pages.ui_test_utils import DummyUI


class DummyHealthService:
    async def get_all_services_status(self):
        return {"svc": SimpleNamespace(status="healthy", checked_at=datetime.now(UTC), is_stale=False)}

    async def get_connectivity(self):
        return SimpleNamespace(redis_connected=True, postgres_connected=True, is_stale=False)

    async def get_latency_metrics(self):
        metrics = SimpleNamespace(p50_ms=1.0, p95_ms=2.0, p99_ms=3.0)
        return {"svc": metrics}, False, None


@pytest.fixture()
def dummy_ui(monkeypatch: pytest.MonkeyPatch) -> DummyUI:
    ui = DummyUI()
    monkeypatch.setattr(health_module, "ui", ui)
    return ui


def test_format_relative_time_variants() -> None:
    now = datetime.now(UTC)
    assert "s ago" in health_module._format_relative_time(now - timedelta(seconds=30))
    assert "m ago" in health_module._format_relative_time(now - timedelta(minutes=5))
    assert "h ago" in health_module._format_relative_time(now - timedelta(hours=2))


def test_get_health_service_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    dummy_app = SimpleNamespace(storage=SimpleNamespace())
    monkeypatch.setattr(health_module, "app", dummy_app)
    monkeypatch.setattr(health_module, "get_db_pool", lambda: None)
    dummy_service_module = SimpleNamespace(
        HealthMonitorService=lambda **_kwargs: DummyHealthService()
    )
    dummy_health_client_module = SimpleNamespace(HealthClient=lambda *_args, **_kwargs: object())
    dummy_prom_module = SimpleNamespace(PrometheusClient=lambda *_args, **_kwargs: object())
    dummy_redis_module = SimpleNamespace(RedisClient=lambda **_kwargs: object())

    monkeypatch.setitem(sys.modules, "libs.web_console_services.health_service", dummy_service_module)
    monkeypatch.setitem(sys.modules, "libs.core.health.health_client", dummy_health_client_module)
    monkeypatch.setitem(sys.modules, "libs.core.health.prometheus_client", dummy_prom_module)
    monkeypatch.setitem(sys.modules, "libs.core.redis_client", dummy_redis_module)

    first = health_module._get_health_service()
    second = health_module._get_health_service()

    assert first is second


@pytest.mark.asyncio()
async def test_health_page_renders_sections(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(health_module.config, "FEATURE_HEALTH_MONITOR", True)
    monkeypatch.setattr(health_module, "get_current_user", lambda: {"user_id": "u1"})
    monkeypatch.setattr(health_module, "has_permission", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(health_module, "_get_health_service", lambda: DummyHealthService())

    class DummyLifecycle:
        async def register_cleanup_callback(self, _client_id, _cb):
            return None

    monkeypatch.setattr(health_module.ClientLifecycleManager, "get", lambda: DummyLifecycle())

    dummy_ui.context.client.storage["client_id"] = "client-1"

    page_fn = health_module.health_page
    while hasattr(page_fn, "__wrapped__"):
        page_fn = page_fn.__wrapped__

    await page_fn()

    labels = [text for text, _ in dummy_ui.labels]
    assert "System Health Monitor" in labels
    assert "Service Status" in labels
    assert "Infrastructure Connectivity" in labels
    assert "Service Latency Metrics" in labels
