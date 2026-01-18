import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from apps.model_registry import auth
from apps.model_registry.auth import ServiceToken


@pytest.fixture(autouse=True)
def clear_auth_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in [
        auth._ADMIN_TOKEN_ENV_VAR,
        auth._READ_TOKEN_ENV_VAR,
        auth._AUTH_TOKEN_ENV_VAR,
    ]:
        monkeypatch.delenv(var, raising=False)


def test_get_expected_tokens_handles_admin_read_and_legacy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(auth._ADMIN_TOKEN_ENV_VAR, "admin-secret")
    monkeypatch.setenv(auth._READ_TOKEN_ENV_VAR, "read-secret")
    monkeypatch.setenv(auth._AUTH_TOKEN_ENV_VAR, "legacy-secret")

    tokens = auth._get_expected_tokens()

    assert tokens == {
        "admin": "admin-secret",
        "read": "read-secret",
        "legacy_read": "legacy-secret",
    }


def test_parse_token_scopes_prefers_admin_over_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(auth._ADMIN_TOKEN_ENV_VAR, "admin-secret")
    monkeypatch.setenv(auth._READ_TOKEN_ENV_VAR, "read-secret")

    assert auth._parse_token_scopes("admin-secret") == [
        "model:read",
        "model:write",
        "model:admin",
    ]
    assert auth._parse_token_scopes("read-secret") == ["model:read"]
    assert auth._parse_token_scopes("unknown") == []


@pytest.mark.asyncio()
async def test_verify_token_missing_credentials_raises_401() -> None:
    with pytest.raises(HTTPException) as excinfo:
        await auth.verify_token(None)

    assert excinfo.value.status_code == 401
    assert excinfo.value.detail == "Missing authorization header"


@pytest.mark.asyncio()
async def test_verify_token_no_configured_tokens_raises_503() -> None:
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="token")

    with pytest.raises(HTTPException) as excinfo:
        await auth.verify_token(creds)

    assert excinfo.value.status_code == 503
    assert "Authentication not configured" in str(excinfo.value.detail)


@pytest.mark.asyncio()
async def test_verify_token_invalid_token_raises_401(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(auth._READ_TOKEN_ENV_VAR, "read-secret")
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="wrong")

    with pytest.raises(HTTPException) as excinfo:
        await auth.verify_token(creds)

    assert excinfo.value.status_code == 401
    assert excinfo.value.detail == "Invalid authentication token"


@pytest.mark.asyncio()
async def test_verify_token_valid_token_returns_service_token(monkeypatch: pytest.MonkeyPatch) -> None:
    token = "svc:read-secret"
    monkeypatch.setenv(auth._READ_TOKEN_ENV_VAR, token)
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    result = await auth.verify_token(creds)

    assert isinstance(result, ServiceToken)
    assert result.token == token
    assert result.scopes == ["model:read"]
    assert result.service_name == "svc"


@pytest.mark.asyncio()
async def test_verify_read_scope_accepts_admin_scope() -> None:
    token = ServiceToken(token="admin", scopes=["model:admin"], service_name="svc")

    assert await auth.verify_read_scope(token) is token


@pytest.mark.asyncio()
async def test_verify_read_scope_rejects_missing_scope() -> None:
    token = ServiceToken(token="read", scopes=[], service_name="svc")

    with pytest.raises(HTTPException) as excinfo:
        await auth.verify_read_scope(token)

    assert excinfo.value.status_code == 403


@pytest.mark.asyncio()
async def test_verify_write_scope_rejects_read_only_scope() -> None:
    token = ServiceToken(token="read", scopes=["model:read"], service_name="svc")

    with pytest.raises(HTTPException) as excinfo:
        await auth.verify_write_scope(token)

    assert excinfo.value.status_code == 403


@pytest.mark.asyncio()
async def test_verify_admin_scope_rejects_missing_scope() -> None:
    token = ServiceToken(token="read", scopes=["model:read"], service_name="svc")

    with pytest.raises(HTTPException) as excinfo:
        await auth.verify_admin_scope(token)

    assert excinfo.value.status_code == 403
