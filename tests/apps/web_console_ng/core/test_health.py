from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request

import apps.web_console_ng.core.health as health_module
from apps.web_console_ng import config


def _make_request(headers: dict[str, str] | None = None) -> Request:
    raw_headers = []
    if headers:
        raw_headers = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    scope = {
        "type": "http",
        "headers": raw_headers,
        "method": "GET",
        "path": "/readyz",
        "scheme": "http",
        "query_string": b"",
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
    }
    return Request(scope)


def test_is_internal_request_with_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health_module, "INTERNAL_PROBE_TOKEN", "token")
    monkeypatch.setattr(health_module, "INTERNAL_PROBE_DISABLE_IP_FALLBACK", True)

    request = _make_request({"X-Internal-Probe": "token"})
    assert health_module.is_internal_request(request) is True


def test_is_internal_request_ip_fallback_debug(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health_module, "INTERNAL_PROBE_TOKEN", "")
    monkeypatch.setattr(health_module, "INTERNAL_PROBE_DISABLE_IP_FALLBACK", False)
    monkeypatch.setattr(config, "DEBUG", True)

    with patch("apps.web_console_ng.auth.client_ip.extract_trusted_client_ip") as extract_ip:
        extract_ip.return_value = "127.0.0.1"
        request = _make_request()
        assert health_module.is_internal_request(request) is True


def test_is_internal_request_fallback_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health_module, "INTERNAL_PROBE_TOKEN", "")
    monkeypatch.setattr(health_module, "INTERNAL_PROBE_DISABLE_IP_FALLBACK", True)
    monkeypatch.setattr(config, "DEBUG", True)

    with patch("apps.web_console_ng.auth.client_ip.extract_trusted_client_ip") as extract_ip:
        extract_ip.return_value = "127.0.0.1"
        request = _make_request()
        assert health_module.is_internal_request(request) is False


def test_setup_health_endpoint_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health_module, "_health_setup_done", False)
    on_startup = MagicMock()
    monkeypatch.setattr(health_module.app, "on_startup", on_startup)

    health_module.setup_health_endpoint()
    health_module.setup_health_endpoint()

    assert on_startup.call_count == 1


@pytest.mark.asyncio()
async def test_start_graceful_shutdown_sets_draining(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRACEFUL_SHUTDOWN_SECONDS", "0")
    monkeypatch.setattr(health_module, "is_draining", False)
    sleep_mock = AsyncMock()
    monkeypatch.setattr(asyncio, "sleep", sleep_mock)

    await health_module.start_graceful_shutdown()
    assert health_module.is_draining is True
    sleep_mock.assert_awaited_once()

    await health_module.start_graceful_shutdown()
    assert sleep_mock.await_count == 1
