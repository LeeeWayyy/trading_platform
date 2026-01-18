"""Unit tests for auth_service dependency helpers."""

from __future__ import annotations

import base64
import builtins

import pytest

from apps.auth_service import dependencies


def test_get_encryption_key_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    """Valid base64 key should decode to 32 bytes."""
    key_b64 = base64.b64encode(b"a" * 32).decode("ascii")
    monkeypatch.setenv("SESSION_ENCRYPTION_KEY", key_b64)

    assert dependencies.get_encryption_key() == b"a" * 32


def test_get_encryption_key_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing SESSION_ENCRYPTION_KEY should raise ValueError."""
    monkeypatch.delenv("SESSION_ENCRYPTION_KEY", raising=False)

    with pytest.raises(ValueError, match="SESSION_ENCRYPTION_KEY environment variable not set"):
        dependencies.get_encryption_key()


def test_get_encryption_key_invalid_base64(monkeypatch: pytest.MonkeyPatch) -> None:
    """Invalid base64 should raise ValueError."""
    monkeypatch.setenv("SESSION_ENCRYPTION_KEY", "not_base64$$")

    with pytest.raises(ValueError, match="base64-encoded"):
        dependencies.get_encryption_key()


def test_get_encryption_key_wrong_length(monkeypatch: pytest.MonkeyPatch) -> None:
    """Decoded key length must be 32 bytes."""
    key_b64 = base64.b64encode(b"short" * 3).decode("ascii")
    monkeypatch.setenv("SESSION_ENCRYPTION_KEY", key_b64)

    with pytest.raises(ValueError, match="32 bytes"):
        dependencies.get_encryption_key()


def test_get_config_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_config should populate AuthServiceConfig from environment."""
    monkeypatch.setenv("AUTH0_DOMAIN", "tenant.auth0.com")
    monkeypatch.setenv("AUTH0_CLIENT_ID", "client")
    monkeypatch.setenv("AUTH0_CLIENT_SECRET", "secret")
    monkeypatch.setenv("AUTH0_AUDIENCE", "https://api.example")
    monkeypatch.setenv("OAUTH2_REDIRECT_URI", "https://example/callback")
    monkeypatch.setenv("OAUTH2_LOGOUT_REDIRECT_URI", "https://example/logout")
    monkeypatch.setenv("COOKIE_DOMAIN", ".example")

    dependencies.get_config.cache_clear()
    config = dependencies.get_config()

    assert config.auth0_domain == "tenant.auth0.com"
    assert config.client_id == "client"
    assert config.client_secret == "secret"
    assert config.audience == "https://api.example"
    assert config.redirect_uri == "https://example/callback"
    assert config.logout_redirect_uri == "https://example/logout"
    assert config.cookie_domain == ".example"


def test_get_db_pool_no_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing DATABASE_URL should return None."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    dependencies.get_db_pool.cache_clear()

    assert dependencies.get_db_pool() is None


def test_get_db_pool_import_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """ImportError for psycopg_pool should return None."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/db")
    dependencies.get_db_pool.cache_clear()

    original_import = builtins.__import__

    def fake_import(name: str, *args, **kwargs):
        if name == "psycopg_pool":
            raise ImportError("psycopg_pool unavailable")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    assert dependencies.get_db_pool() is None
