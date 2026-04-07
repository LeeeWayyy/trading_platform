import logging

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


def test_authenticate_token_returns_correct_scopes_and_roles(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(auth._ADMIN_TOKEN_ENV_VAR, "admin-secret")
    monkeypatch.setenv(auth._READ_TOKEN_ENV_VAR, "read-secret")

    result = auth._authenticate_token("admin-secret")
    assert result is not None
    scopes, role = result
    assert scopes == ["model:read", "model:write", "model:admin"]
    assert role == "admin"

    result = auth._authenticate_token("read-secret")
    assert result is not None
    scopes, role = result
    assert scopes == ["model:read"]
    assert role == "read"

    assert auth._authenticate_token("unknown") is None


def test_authenticate_token_admin_takes_precedence_over_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When admin and read tokens are the same, admin scopes take precedence."""
    shared_token = "shared-secret"
    monkeypatch.setenv(auth._ADMIN_TOKEN_ENV_VAR, shared_token)
    monkeypatch.setenv(auth._READ_TOKEN_ENV_VAR, shared_token)

    result = auth._authenticate_token(shared_token)
    assert result is not None
    scopes, role = result
    # Admin is checked first, so admin scopes should be returned
    assert scopes == ["model:read", "model:write", "model:admin"]
    assert role == "admin"


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
async def test_verify_token_invalid_token_does_not_log_token_material(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Ensure auth failure logs never contain token material (repo rule)."""
    secret_token = "super-secret-credential-value"
    monkeypatch.setenv(auth._READ_TOKEN_ENV_VAR, "read-secret")
    creds = HTTPAuthorizationCredentials(
        scheme="Bearer", credentials=secret_token
    )

    with caplog.at_level(logging.WARNING, logger="apps.model_registry.auth"):
        with pytest.raises(HTTPException):
            await auth.verify_token(creds)

    # Filter to the specific auth failure warning by message content
    auth_warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING
        and "token not recognized" in r.getMessage()
    ]
    assert len(auth_warnings) >= 1, "Expected auth failure WARNING log record"

    for record in auth_warnings:
        # No token material should appear in the log message
        assert secret_token not in record.getMessage()
        assert secret_token[:4] not in record.getMessage()
        # token_length must be present instead of token content
        assert hasattr(record, "token_length"), (
            "Expected 'token_length' in log record extras"
        )
        assert record.token_length == len(secret_token)


@pytest.mark.asyncio()
async def test_verify_token_valid_token_returns_service_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "read-secret"
    monkeypatch.setenv(auth._READ_TOKEN_ENV_VAR, token)
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    result = await auth.verify_token(creds)

    assert isinstance(result, ServiceToken)
    assert result.scopes == ["model:read"]
    # auth_role is derived from the role key, not token content (fixes #174)
    assert result.auth_role == "read"


@pytest.mark.asyncio()
async def test_verify_token_admin_token_returns_admin_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "admin-secret"
    monkeypatch.setenv(auth._ADMIN_TOKEN_ENV_VAR, token)
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    result = await auth.verify_token(creds)

    assert isinstance(result, ServiceToken)
    assert result.scopes == ["model:read", "model:write", "model:admin"]
    assert result.auth_role == "admin"


@pytest.mark.asyncio()
async def test_verify_token_legacy_token_returns_legacy_read_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "legacy-secret"
    monkeypatch.setenv(auth._AUTH_TOKEN_ENV_VAR, token)
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    result = await auth.verify_token(creds)

    assert isinstance(result, ServiceToken)
    assert result.scopes == ["model:read"]
    assert result.auth_role == "legacy_read"


def test_authenticate_token_returns_role_not_token_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure _authenticate_token never leaks token content (fixes #174)."""
    monkeypatch.setenv(auth._ADMIN_TOKEN_ENV_VAR, "secretprefix:admin-key")
    monkeypatch.setenv(auth._READ_TOKEN_ENV_VAR, "othersecret:read-key")

    result = auth._authenticate_token("secretprefix:admin-key")
    assert result is not None
    _, role = result
    assert role == "admin"

    result = auth._authenticate_token("othersecret:read-key")
    assert result is not None
    _, role = result
    assert role == "read"

    assert auth._authenticate_token("unknown-token") is None


@pytest.mark.asyncio()
async def test_verify_read_scope_accepts_admin_scope() -> None:
    token = ServiceToken(scopes=["model:admin"], auth_role="svc")

    assert await auth.verify_read_scope(token) is token


@pytest.mark.asyncio()
async def test_verify_read_scope_rejects_missing_scope() -> None:
    token = ServiceToken(scopes=[], auth_role="svc")

    with pytest.raises(HTTPException) as excinfo:
        await auth.verify_read_scope(token)

    assert excinfo.value.status_code == 403


@pytest.mark.asyncio()
async def test_verify_write_scope_rejects_read_only_scope() -> None:
    token = ServiceToken(scopes=["model:read"], auth_role="svc")

    with pytest.raises(HTTPException) as excinfo:
        await auth.verify_write_scope(token)

    assert excinfo.value.status_code == 403


@pytest.mark.asyncio()
async def test_verify_admin_scope_rejects_missing_scope() -> None:
    token = ServiceToken(scopes=["model:read"], auth_role="svc")

    with pytest.raises(HTTPException) as excinfo:
        await auth.verify_admin_scope(token)

    assert excinfo.value.status_code == 403
