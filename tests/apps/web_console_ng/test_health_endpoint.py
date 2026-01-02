# tests/apps/web_console_ng/test_health_endpoint.py
from __future__ import annotations

import threading
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from nicegui import app

import apps.web_console_ng.core.health as health_module

client = TestClient(app)


@pytest.fixture()
def mock_redis_store():
    redis_instance = AsyncMock()
    redis_instance.ping = AsyncMock(return_value=True)
    with patch("apps.web_console_ng.core.health.get_redis_store", return_value=redis_instance):
        yield redis_instance


def test_liveness_check():
    """/healthz should always return 200 with status payload."""
    response = client.get("/healthz")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "alive"
    assert "timestamp" in payload


@pytest.mark.asyncio()
async def test_readiness_check_success(mock_redis_store):
    """/readyz returns 200 when dependencies are healthy."""
    health_module.is_draining = False
    with patch("apps.web_console_ng.config.HEALTH_CHECK_BACKEND_ENABLED", False):
        response = client.get("/readyz")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ready"
    assert "timestamp" in payload


@pytest.mark.asyncio()
async def test_readiness_check_failure(mock_redis_store):
    """/readyz returns 503 when Redis fails."""
    health_module.is_draining = False
    mock_redis_store.ping.side_effect = Exception("Redis down")
    with patch("apps.web_console_ng.config.HEALTH_CHECK_BACKEND_ENABLED", False):
        response = client.get("/readyz")
    assert response.status_code == 503
    payload = response.json()
    assert payload["status"] == "not_ready"
    assert "timestamp" in payload


def test_readiness_draining():
    """/readyz returns 503 when draining."""
    health_module.is_draining = True
    try:
        response = client.get("/readyz")
        assert response.status_code == 503
        payload = response.json()
        assert payload["status"] == "draining"
        assert "timestamp" in payload
    finally:
        health_module.is_draining = False


def test_internal_vs_external_response_details(mock_redis_store):
    """Internal requests receive detailed responses; external requests do not."""
    health_module.is_draining = False
    with patch("apps.web_console_ng.config.HEALTH_CHECK_BACKEND_ENABLED", False):
        with patch("apps.web_console_ng.core.health.INTERNAL_PROBE_TOKEN", ""):
            response = client.get("/readyz")
            payload = response.json()
            assert response.status_code == 200
            assert "checks" not in payload
            assert "connections" not in payload
            assert "pod" not in payload

        with patch("apps.web_console_ng.core.health.INTERNAL_PROBE_TOKEN", "secret-token"):
            response = client.get("/readyz", headers={"X-Internal-Probe": "secret-token"})
            payload = response.json()
            assert response.status_code == 200
            assert payload["status"] == "ready"
            assert "checks" in payload
            assert "connections" in payload
            assert "pod" in payload
            assert "timestamp" in payload


def test_connection_counter_thread_safety():
    """ConnectionCounter should remain consistent under concurrent access."""
    counter = health_module.ConnectionCounter()
    threads = []
    increments_per_thread = 500
    thread_count = 8

    def worker_increment():
        for _ in range(increments_per_thread):
            counter.increment()

    for _ in range(thread_count):
        thread = threading.Thread(target=worker_increment)
        threads.append(thread)
        thread.start()

    for thread in threads:
        thread.join()

    assert counter.value == thread_count * increments_per_thread

    def worker_decrement():
        for _ in range(increments_per_thread):
            counter.decrement()

    threads = []
    for _ in range(thread_count):
        thread = threading.Thread(target=worker_decrement)
        threads.append(thread)
        thread.start()

    for thread in threads:
        thread.join()

    assert counter.value == 0
    assert counter.decrement() == 0
