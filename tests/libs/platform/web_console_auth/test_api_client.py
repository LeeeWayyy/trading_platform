"""Unit tests for libs.platform.web_console_auth.api_client."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from libs.platform.web_console_auth import api_client


class FakeSessionStore:
    def __init__(self, session_data: object | None) -> None:
        self.session_data = session_data
        self.calls: list[tuple[str, str, str, bool]] = []

    async def get_session(
        self,
        session_id: str,
        current_ip: str,
        current_user_agent: str,
        update_activity: bool,
    ) -> object | None:
        self.calls.append((session_id, current_ip, current_user_agent, update_activity))
        return self.session_data


class FakeAsyncClient:
    last_instance: FakeAsyncClient | None = None

    def __init__(self, timeout: float) -> None:
        self.timeout = timeout
        self.request_calls: list[tuple[str, str, dict]] = []
        FakeAsyncClient.last_instance = self

    async def __aenter__(self) -> FakeAsyncClient:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False

    async def request(self, method: str, url: str, **kwargs):
        self.request_calls.append((method, url, kwargs))
        request = httpx.Request(method, url)
        return httpx.Response(200, request=request)


@pytest.mark.asyncio()
async def test_get_access_token_from_redis_returns_token() -> None:
    session_data = SimpleNamespace(access_token="token")
    store = FakeSessionStore(session_data)

    token = await api_client.get_access_token_from_redis(
        session_id="session-1",
        session_store=store,
        client_ip="203.0.113.1",
        user_agent="Mozilla/5.0",
    )

    assert token == "token"
    assert store.calls == [("session-1", "203.0.113.1", "Mozilla/5.0", False)]


@pytest.mark.asyncio()
async def test_get_access_token_from_redis_returns_none_on_missing_session() -> None:
    store = FakeSessionStore(None)

    token = await api_client.get_access_token_from_redis(
        session_id="missing",
        session_store=store,
        client_ip="203.0.113.2",
        user_agent="Mozilla/5.0",
    )

    assert token is None


@pytest.mark.asyncio()
async def test_call_api_with_auth_requires_parameters() -> None:
    with pytest.raises(ValueError, match="Missing required parameters"):
        await api_client.call_api_with_auth(url="https://example.com")


@pytest.mark.asyncio()
async def test_call_api_with_auth_rejects_invalid_session(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_access_token(*args, **kwargs):
        return None

    monkeypatch.setattr(api_client, "get_access_token_from_redis", fake_get_access_token)

    with pytest.raises(ValueError, match="Session invalid or expired"):
        await api_client.call_api_with_auth(
            url="https://example.com",
            session_id="sess",
            session_store=FakeSessionStore(None),
            client_ip="203.0.113.3",
            user_agent="Mozilla/5.0",
        )


@pytest.mark.asyncio()
async def test_call_api_with_auth_adds_bearer_header(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_access_token(*args, **kwargs):
        return "access-123"

    monkeypatch.setattr(api_client, "get_access_token_from_redis", fake_get_access_token)
    monkeypatch.setattr(api_client.httpx, "AsyncClient", FakeAsyncClient)

    response = await api_client.call_api_with_auth(
        url="https://api.service.local/positions",
        method="POST",
        session_id="sess",
        session_store=FakeSessionStore(SimpleNamespace(access_token="ignored")),
        client_ip="203.0.113.4",
        user_agent="Mozilla/5.0",
        headers={"X-Trace": "abc"},
        json={"payload": True},
    )

    assert response.status_code == 200

    client = FakeAsyncClient.last_instance
    assert client is not None
    assert len(client.request_calls) == 1

    method, url, kwargs = client.request_calls[0]
    assert method == "POST"
    assert url == "https://api.service.local/positions"
    assert kwargs["headers"]["Authorization"] == "Bearer access-123"
    assert kwargs["headers"]["X-Trace"] == "abc"
    assert kwargs["json"] == {"payload": True}
