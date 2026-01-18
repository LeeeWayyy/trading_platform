from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock

import httpx
import pytest

import apps.web_console_ng.auth.providers.oauth2 as oauth2_module
from apps.web_console_ng import config


class _MockAsyncResponse:
    def __init__(self, status_code: int, payload: dict[str, object] | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> dict[str, object]:
        return dict(self._payload)


class _MockAsyncClient:
    def __init__(self, post_response: _MockAsyncResponse, get_response: _MockAsyncResponse) -> None:
        self._post_response = post_response
        self._get_response = get_response
        self.post_calls: list[tuple[str, dict[str, object]]] = []
        self.get_calls: list[tuple[str, dict[str, object]]] = []

    async def __aenter__(self) -> _MockAsyncClient:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def post(self, url: str, data: dict[str, object]) -> _MockAsyncResponse:
        self.post_calls.append((url, data))
        return self._post_response

    async def get(self, url: str, headers: dict[str, str]) -> _MockAsyncResponse:
        self.get_calls.append((url, headers))
        return self._get_response


@pytest.fixture()
def session_store() -> AsyncMock:
    store = AsyncMock()
    store.create_session.return_value = ("cookie-value", "csrf-token")
    return store


@pytest.fixture()
def redis_client() -> AsyncMock:
    client = AsyncMock()
    client.get.return_value = None
    return client


def _set_oauth2_config_debug(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "DEBUG", True)
    # Set mock values for debug mode (matches defaults in OAuth2AuthHandler)
    monkeypatch.setattr(config, "OAUTH2_CLIENT_ID", "mock_client_id")
    monkeypatch.setattr(config, "OAUTH2_CLIENT_SECRET", "mock_secret")
    monkeypatch.setattr(config, "OAUTH2_AUTHORIZE_URL", "https://mock.auth0.com/authorize")
    monkeypatch.setattr(config, "OAUTH2_TOKEN_URL", "https://mock.auth0.com/oauth/token")
    monkeypatch.setattr(config, "OAUTH2_USERINFO_URL", "https://mock.auth0.com/userinfo")
    monkeypatch.setattr(config, "OAUTH2_CALLBACK_URL", "http://localhost:8080/auth/callback")
    monkeypatch.setattr(config, "OAUTH2_ISSUER", "https://mock.auth0.com/")


def _set_oauth2_config_prod(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "DEBUG", False)
    monkeypatch.setattr(config, "OAUTH2_CLIENT_ID", "client")
    monkeypatch.setattr(config, "OAUTH2_CLIENT_SECRET", "secret")
    monkeypatch.setattr(config, "OAUTH2_AUTHORIZE_URL", "https://auth.example/authorize")
    monkeypatch.setattr(config, "OAUTH2_TOKEN_URL", "https://auth.example/token")
    monkeypatch.setattr(config, "OAUTH2_USERINFO_URL", "https://auth.example/userinfo")
    monkeypatch.setattr(config, "OAUTH2_CALLBACK_URL", "https://app.example/auth/callback")
    monkeypatch.setattr(config, "OAUTH2_ISSUER", "https://auth.example/")


def test_init_requires_config_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "DEBUG", False)
    monkeypatch.setattr(config, "OAUTH2_CLIENT_ID", "")

    with pytest.raises(ValueError, match="OAUTH2_CLIENT_ID must be set"):
        oauth2_module.OAuth2AuthHandler()


def test_init_defaults_in_debug(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_oauth2_config_debug(monkeypatch)

    handler = oauth2_module.OAuth2AuthHandler()

    assert handler.client_id == "mock_client_id"
    assert handler.authorize_url == "https://mock.auth0.com/authorize"
    assert handler.callback_url == "http://localhost:8080/auth/callback"


@pytest.mark.asyncio()
async def test_get_authorization_url_stores_flow(monkeypatch: pytest.MonkeyPatch, redis_client: AsyncMock) -> None:
    _set_oauth2_config_debug(monkeypatch)

    handler = oauth2_module.OAuth2AuthHandler()
    handler._redis = redis_client

    url = await handler.get_authorization_url()

    assert url.startswith(handler.authorize_url)
    redis_client.setex.assert_called_once()

    args = redis_client.setex.call_args.args
    assert args[0].startswith(handler.OAUTH2_STATE_PREFIX)
    flow_data = json.loads(args[2])
    assert "code_verifier" in flow_data
    assert "nonce" in flow_data
    assert flow_data["redirect_uri"] == handler.callback_url


@pytest.mark.asyncio()
async def test_handle_callback_invalid_state(
    monkeypatch: pytest.MonkeyPatch, redis_client: AsyncMock
) -> None:
    _set_oauth2_config_debug(monkeypatch)

    handler = oauth2_module.OAuth2AuthHandler()
    handler._redis = redis_client

    result = await handler.handle_callback(code="code", state="missing")

    assert result.success is False
    assert result.error_message == "Invalid or expired state"


@pytest.mark.asyncio()
async def test_handle_callback_invalid_redirect_uri(
    monkeypatch: pytest.MonkeyPatch, redis_client: AsyncMock
) -> None:
    _set_oauth2_config_prod(monkeypatch)

    handler = oauth2_module.OAuth2AuthHandler()
    flow_data = {
        "code_verifier": "verifier",
        "nonce": "nonce",
        "created_at": time.time(),
        "redirect_uri": "https://app.example/auth/callback",
    }
    redis_client.get.return_value = json.dumps(flow_data)
    handler._redis = redis_client

    result = await handler.handle_callback(
        code="code",
        state="state",
        redirect_uri="https://evil.example/callback",
    )

    assert result.success is False
    assert result.error_message == "Invalid redirect URI"


@pytest.mark.asyncio()
async def test_handle_callback_token_exchange_failure(
    monkeypatch: pytest.MonkeyPatch, redis_client: AsyncMock
) -> None:
    _set_oauth2_config_debug(monkeypatch)

    handler = oauth2_module.OAuth2AuthHandler()
    flow_data = {
        "code_verifier": "verifier",
        "nonce": "nonce",
        "created_at": time.time(),
        "redirect_uri": handler.callback_url,
    }
    redis_client.get.return_value = json.dumps(flow_data)
    handler._redis = redis_client

    mock_client = _MockAsyncClient(_MockAsyncResponse(400), _MockAsyncResponse(200))
    monkeypatch.setattr(oauth2_module.httpx, "AsyncClient", lambda: mock_client)

    result = await handler.handle_callback(code="code", state="state")

    assert result.success is False
    assert result.error_message == "Token exchange failed"


@pytest.mark.asyncio()
async def test_handle_callback_provider_unreachable(
    monkeypatch: pytest.MonkeyPatch, redis_client: AsyncMock
) -> None:
    _set_oauth2_config_debug(monkeypatch)

    handler = oauth2_module.OAuth2AuthHandler()
    flow_data = {
        "code_verifier": "verifier",
        "nonce": "nonce",
        "created_at": time.time(),
        "redirect_uri": handler.callback_url,
    }
    redis_client.get.return_value = json.dumps(flow_data)
    handler._redis = redis_client

    class _FailingClient(_MockAsyncClient):
        async def post(self, url: str, data: dict[str, object]) -> _MockAsyncResponse:  # type: ignore[override]
            raise httpx.RequestError("boom", request=httpx.Request("POST", url))

    monkeypatch.setattr(
        oauth2_module.httpx,
        "AsyncClient",
        lambda: _FailingClient(_MockAsyncResponse(200), _MockAsyncResponse(200)),
    )

    result = await handler.handle_callback(code="code", state="state")

    assert result.success is False
    assert result.error_message == "OAuth2 provider unreachable"


@pytest.mark.asyncio()
async def test_handle_callback_invalid_id_token(
    monkeypatch: pytest.MonkeyPatch, redis_client: AsyncMock
) -> None:
    _set_oauth2_config_debug(monkeypatch)

    handler = oauth2_module.OAuth2AuthHandler()
    flow_data = {
        "code_verifier": "verifier",
        "nonce": "nonce",
        "created_at": time.time(),
        "redirect_uri": handler.callback_url,
    }
    redis_client.get.return_value = json.dumps(flow_data)
    handler._redis = redis_client

    mock_client = _MockAsyncClient(
        _MockAsyncResponse(200, {"access_token": "token", "id_token": "id-token"}),
        _MockAsyncResponse(200, {"sub": "user"}),
    )
    monkeypatch.setattr(oauth2_module.httpx, "AsyncClient", lambda: mock_client)
    monkeypatch.setattr(handler, "_validate_id_token", AsyncMock(return_value=(False, "bad token")))

    result = await handler.handle_callback(code="code", state="state")

    assert result.success is False
    assert result.error_message == "bad token"


@pytest.mark.asyncio()
async def test_handle_callback_userinfo_failure(
    monkeypatch: pytest.MonkeyPatch, redis_client: AsyncMock
) -> None:
    _set_oauth2_config_debug(monkeypatch)

    handler = oauth2_module.OAuth2AuthHandler()
    flow_data = {
        "code_verifier": "verifier",
        "nonce": "nonce",
        "created_at": time.time(),
        "redirect_uri": handler.callback_url,
    }
    redis_client.get.return_value = json.dumps(flow_data)
    handler._redis = redis_client

    mock_client = _MockAsyncClient(
        _MockAsyncResponse(200, {"access_token": "token", "id_token": "id-token"}),
        _MockAsyncResponse(403, {}),
    )
    monkeypatch.setattr(oauth2_module.httpx, "AsyncClient", lambda: mock_client)
    monkeypatch.setattr(handler, "_validate_id_token", AsyncMock(return_value=(True, None)))

    result = await handler.handle_callback(code="code", state="state")

    assert result.success is False
    assert result.error_message == "Failed to fetch user info"


@pytest.mark.asyncio()
async def test_handle_callback_missing_sub(
    monkeypatch: pytest.MonkeyPatch, redis_client: AsyncMock
) -> None:
    _set_oauth2_config_debug(monkeypatch)

    handler = oauth2_module.OAuth2AuthHandler()
    flow_data = {
        "code_verifier": "verifier",
        "nonce": "nonce",
        "created_at": time.time(),
        "redirect_uri": handler.callback_url,
    }
    redis_client.get.return_value = json.dumps(flow_data)
    handler._redis = redis_client

    mock_client = _MockAsyncClient(
        _MockAsyncResponse(200, {"access_token": "token", "id_token": "id-token"}),
        _MockAsyncResponse(200, {"email": "user@example.com"}),
    )
    monkeypatch.setattr(oauth2_module.httpx, "AsyncClient", lambda: mock_client)
    monkeypatch.setattr(handler, "_validate_id_token", AsyncMock(return_value=(True, None)))

    result = await handler.handle_callback(code="code", state="state")

    assert result.success is False
    assert result.error_message == "Identity provider did not return valid user identifier"


@pytest.mark.asyncio()
async def test_handle_callback_success(
    monkeypatch: pytest.MonkeyPatch,
    redis_client: AsyncMock,
    session_store: AsyncMock,
) -> None:
    _set_oauth2_config_debug(monkeypatch)

    handler = oauth2_module.OAuth2AuthHandler()
    flow_data = {
        "code_verifier": "verifier",
        "nonce": "nonce",
        "created_at": time.time(),
        "redirect_uri": handler.callback_url,
    }
    redis_client.get.return_value = json.dumps(flow_data)
    handler._redis = redis_client

    mock_client = _MockAsyncClient(
        _MockAsyncResponse(200, {"access_token": "token", "id_token": "id-token"}),
        _MockAsyncResponse(200, {"sub": "user-1", "email": "user@example.com", "roles": ["admin"]}),
    )
    monkeypatch.setattr(oauth2_module.httpx, "AsyncClient", lambda: mock_client)
    monkeypatch.setattr(handler, "_validate_id_token", AsyncMock(return_value=(True, None)))
    monkeypatch.setattr(oauth2_module, "get_session_store", lambda: session_store)

    result = await handler.handle_callback(
        code="code",
        state="state",
        client_ip="10.0.0.1",
        user_agent="pytest",
    )

    assert result.success is True
    assert result.cookie_value == "cookie-value"
    assert result.csrf_token == "csrf-token"
    assert result.user_data
    assert result.user_data["user_id"] == "user-1"
    assert result.user_data["role"] == "admin"
    assert result.user_data["auth_method"] == "oauth2"

    session_store.create_session.assert_awaited_once()
    _, kwargs = session_store.create_session.await_args
    assert kwargs.get("client_ip") == "10.0.0.1"
    assert kwargs.get("device_info", {}).get("user_agent") == "pytest"


@pytest.mark.asyncio()
async def test_authenticate_requires_code_and_state(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_oauth2_config_debug(monkeypatch)

    handler = oauth2_module.OAuth2AuthHandler()
    result = await handler.authenticate(code=None, state=None)

    assert result.success is False
    assert result.error_message == "Missing code or state"


@pytest.mark.asyncio()
async def test_get_logout_url(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_oauth2_config_debug(monkeypatch)
    monkeypatch.setattr(config, "OAUTH2_LOGOUT_URL", "https://auth.example/logout")
    monkeypatch.setattr(config, "OAUTH2_POST_LOGOUT_REDIRECT_URL", "https://app.example/login")

    handler = oauth2_module.OAuth2AuthHandler()
    url = await handler.get_logout_url(id_token="token")

    assert url
    assert "post_logout_redirect_uri=https%3A%2F%2Fapp.example%2Flogin" in url
    assert "id_token_hint=token" in url


@pytest.mark.asyncio()
async def test_get_logout_url_missing_config(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_oauth2_config_debug(monkeypatch)
    monkeypatch.setattr(config, "OAUTH2_LOGOUT_URL", "")

    handler = oauth2_module.OAuth2AuthHandler()

    assert await handler.get_logout_url() is None


def test_map_role(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_oauth2_config_debug(monkeypatch)
    handler = oauth2_module.OAuth2AuthHandler()

    assert handler._map_role({"roles": ["admin"]}) == "admin"
    assert handler._map_role({"roles": ["trader"]}) == "trader"
    assert handler._map_role({"roles": []}) == "viewer"


def test_normalize_redirect_uri(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_oauth2_config_prod(monkeypatch)
    handler = oauth2_module.OAuth2AuthHandler()
    normalized = handler._normalize_redirect_uri("http://example.com/callback?foo=1#frag")

    assert normalized == "https://example.com/callback"


@pytest.mark.asyncio()
async def test_validate_id_token_missing_token(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_oauth2_config_debug(monkeypatch)
    handler = oauth2_module.OAuth2AuthHandler()

    valid, message = await handler._validate_id_token(None, expected_nonce="nonce")

    assert valid is False
    assert message == "Missing id_token"


@pytest.mark.asyncio()
async def test_validate_id_token_missing_kid(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_oauth2_config_debug(monkeypatch)
    handler = oauth2_module.OAuth2AuthHandler()

    monkeypatch.setattr(handler, "_get_jwks", AsyncMock(return_value={"keys": [{"kid": "kid"}]}))
    monkeypatch.setattr(oauth2_module.jwt, "get_unverified_header", lambda _: {"alg": "RS256"})

    valid, message = await handler._validate_id_token("token", expected_nonce="nonce")

    assert valid is False
    assert message == "id_token missing kid"


@pytest.mark.asyncio()
async def test_validate_id_token_success(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_oauth2_config_debug(monkeypatch)
    handler = oauth2_module.OAuth2AuthHandler()

    monkeypatch.setattr(handler, "_get_jwks", AsyncMock(return_value={"keys": [{"kid": "kid"}]}))
    monkeypatch.setattr(
        oauth2_module.jwt,
        "get_unverified_header",
        lambda _: {"kid": "kid", "alg": "RS256"},
    )
    monkeypatch.setattr(
        oauth2_module.jwt,
        "decode",
        lambda *_args, **_kwargs: {"exp": time.time() + 3600, "nonce": "nonce"},
    )

    valid, message = await handler._validate_id_token("token", expected_nonce="nonce")

    assert valid is True
    assert message is None


@pytest.mark.asyncio()
async def test_validate_id_token_expired(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_oauth2_config_debug(monkeypatch)
    handler = oauth2_module.OAuth2AuthHandler()

    monkeypatch.setattr(handler, "_get_jwks", AsyncMock(return_value={"keys": [{"kid": "kid"}]}))
    monkeypatch.setattr(
        oauth2_module.jwt,
        "get_unverified_header",
        lambda _: {"kid": "kid", "alg": "RS256"},
    )
    monkeypatch.setattr(
        oauth2_module.jwt,
        "decode",
        lambda *_args, **_kwargs: {"exp": time.time() - 10, "nonce": "nonce"},
    )

    valid, message = await handler._validate_id_token("token", expected_nonce="nonce")

    assert valid is False
    assert message == "Expired id_token"


@pytest.mark.asyncio()
async def test_validate_id_token_invalid_nonce(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_oauth2_config_debug(monkeypatch)
    handler = oauth2_module.OAuth2AuthHandler()

    monkeypatch.setattr(handler, "_get_jwks", AsyncMock(return_value={"keys": [{"kid": "kid"}]}))
    monkeypatch.setattr(
        oauth2_module.jwt,
        "get_unverified_header",
        lambda _: {"kid": "kid", "alg": "RS256"},
    )
    monkeypatch.setattr(
        oauth2_module.jwt,
        "decode",
        lambda *_args, **_kwargs: {"exp": time.time() + 10, "nonce": "other"},
    )

    valid, message = await handler._validate_id_token("token", expected_nonce="nonce")

    assert valid is False
    assert message == "Invalid nonce"
