"""Unit tests for retry decorator."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from apps.web_console_ng.core.retry import with_retry


@pytest.mark.asyncio()
async def test_retry_idempotent_retries_on_transport_error(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts: list[int] = []

    async def flaky() -> str:
        attempts.append(1)
        if len(attempts) < 3:
            raise httpx.TransportError("boom")
        return "ok"

    async def fake_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    wrapped = with_retry(max_attempts=3, backoff_base=0.01, method="GET")(flaky)
    result = await wrapped()

    assert result == "ok"
    assert len(attempts) == 3


@pytest.mark.asyncio()
async def test_retry_idempotent_retries_on_5xx(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts: list[int] = []
    request = httpx.Request("GET", "http://example.com")
    response = httpx.Response(502, request=request)

    async def flaky() -> str:
        attempts.append(1)
        if len(attempts) < 2:
            raise httpx.HTTPStatusError("bad", request=request, response=response)
        return "ok"

    async def fake_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    wrapped = with_retry(max_attempts=2, backoff_base=0.01, method="GET")(flaky)
    result = await wrapped()

    assert result == "ok"
    assert len(attempts) == 2


@pytest.mark.asyncio()
async def test_retry_idempotent_does_not_retry_on_4xx() -> None:
    attempts: list[int] = []
    request = httpx.Request("GET", "http://example.com")
    response = httpx.Response(404, request=request)

    async def flaky() -> str:
        attempts.append(1)
        raise httpx.HTTPStatusError("not found", request=request, response=response)

    wrapped = with_retry(max_attempts=3, backoff_base=0.01, method="GET")(flaky)

    with pytest.raises(httpx.HTTPStatusError):
        await wrapped()

    assert len(attempts) == 1


@pytest.mark.asyncio()
async def test_retry_non_idempotent_does_not_retry_on_5xx() -> None:
    attempts: list[int] = []
    request = httpx.Request("POST", "http://example.com")
    response = httpx.Response(500, request=request)

    async def flaky() -> str:
        attempts.append(1)
        raise httpx.HTTPStatusError("server error", request=request, response=response)

    wrapped = with_retry(max_attempts=3, backoff_base=0.01, method="POST")(flaky)

    with pytest.raises(httpx.HTTPStatusError):
        await wrapped()

    assert len(attempts) == 1


@pytest.mark.asyncio()
async def test_retry_non_idempotent_retries_on_transport_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[int] = []

    async def flaky() -> str:
        attempts.append(1)
        if len(attempts) < 2:
            raise httpx.TransportError("boom")
        return "ok"

    async def fake_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    wrapped = with_retry(max_attempts=2, backoff_base=0.01, method="POST")(flaky)
    result = await wrapped()

    assert result == "ok"
    assert len(attempts) == 2
