"""Unit tests for libs.platform.web_console_auth.idp_health."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from libs.platform.web_console_auth import idp_health


class _DummyResponse:
    def __init__(self, status_code: int = 200, json_data: dict | None = None):
        self.status_code = status_code
        self._json = json_data or {}
        self.request = httpx.Request("GET", "https://example.com")

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "bad",
                request=self.request,
                response=httpx.Response(self.status_code, request=self.request),
            )

    def json(self) -> dict:
        return self._json


class _DummyAsyncClient:
    def __init__(self, response: _DummyResponse | None = None, exc: Exception | None = None):
        self._response = response
        self._exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url: str):
        if self._exc:
            raise self._exc
        return self._response


def _patch_client(monkeypatch: pytest.MonkeyPatch, response=None, exc=None) -> None:
    monkeypatch.setattr(
        idp_health.httpx,
        "AsyncClient",
        lambda timeout: _DummyAsyncClient(response=response, exc=exc),
    )


@pytest.mark.asyncio
async def test_check_health_success(monkeypatch: pytest.MonkeyPatch) -> None:
    domain = "auth.example.com"
    response = _DummyResponse(
        json_data={
            "issuer": f"https://{domain}/",
            "authorization_endpoint": "x",
            "token_endpoint": "x",
            "jwks_uri": "x",
            "userinfo_endpoint": "x",
        }
    )
    _patch_client(monkeypatch, response=response)

    checker = idp_health.IdPHealthChecker(auth0_domain=domain)
    status = await checker.check_health()

    assert status.healthy is True
    assert status.consecutive_successes == 1
    assert status.fallback_mode is False


@pytest.mark.asyncio
async def test_check_health_missing_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    domain = "auth.example.com"
    response = _DummyResponse(json_data={"issuer": f"https://{domain}/"})
    _patch_client(monkeypatch, response=response)

    checker = idp_health.IdPHealthChecker(auth0_domain=domain)
    status = await checker.check_health()

    assert status.healthy is False
    assert status.consecutive_failures == 1
    assert status.error is not None


@pytest.mark.asyncio
async def test_check_health_issuer_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    domain = "auth.example.com"
    response = _DummyResponse(
        json_data={
            "issuer": "https://other/",
            "authorization_endpoint": "x",
            "token_endpoint": "x",
            "jwks_uri": "x",
            "userinfo_endpoint": "x",
        }
    )
    _patch_client(monkeypatch, response=response)

    checker = idp_health.IdPHealthChecker(auth0_domain=domain)
    status = await checker.check_health()

    assert status.healthy is False
    assert status.consecutive_failures == 1
    assert status.fallback_mode is False


@pytest.mark.asyncio
async def test_check_health_enters_fallback_after_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_client(monkeypatch, exc=httpx.TimeoutException("timeout"))

    checker = idp_health.IdPHealthChecker(auth0_domain="auth.example.com", failure_threshold=2)
    await checker.check_health()
    status = await checker.check_health()

    assert status.healthy is False
    assert status.fallback_mode is True
    assert checker.should_fallback_to_mtls() is True


@pytest.mark.asyncio
async def test_check_health_exits_fallback_after_stability(monkeypatch: pytest.MonkeyPatch) -> None:
    domain = "auth.example.com"
    response = _DummyResponse(
        json_data={
            "issuer": f"https://{domain}/",
            "authorization_endpoint": "x",
            "token_endpoint": "x",
            "jwks_uri": "x",
            "userinfo_endpoint": "x",
        }
    )
    _patch_client(monkeypatch, response=response)

    checker = idp_health.IdPHealthChecker(
        auth0_domain=domain, success_threshold=1, stable_period_seconds=1
    )
    checker._fallback_mode = True
    checker._consecutive_successes = 1
    checker._stability_start = datetime.now(UTC) - timedelta(seconds=5)

    status = await checker.check_health()

    assert status.healthy is True
    assert status.fallback_mode is False
    assert checker.is_fallback_mode() is False


def test_should_check_now_respects_intervals() -> None:
    checker = idp_health.IdPHealthChecker(auth0_domain="auth.example.com")
    assert checker.should_check_now() is True

    checker._last_check = datetime.now(UTC)
    assert checker.should_check_now() is False

    checker._last_check = datetime.now(UTC) - checker.normal_check_interval - timedelta(seconds=1)
    assert checker.should_check_now() is True

    checker._fallback_mode = True
    checker._last_check = datetime.now(UTC)
    assert checker.should_check_now() is False

    checker._last_check = datetime.now(UTC) - checker.fallback_check_interval - timedelta(
        seconds=1
    )
    assert checker.should_check_now() is True
