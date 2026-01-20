"""Unit tests for libs.platform.web_console_auth.config."""

from __future__ import annotations

from pathlib import Path

import pytest

from libs.platform.web_console_auth.config import AuthConfig


def test_defaults_are_secure() -> None:
    config = AuthConfig()

    assert config.jwt_algorithm == "RS256"
    assert config.access_token_ttl == 900
    assert config.refresh_token_ttl == 14400
    assert config.clock_skew_seconds == 30
    assert config.session_binding_strict is True
    assert config.rate_limit_enabled is True
    assert config.cookie_secure is True
    assert config.cookie_httponly is True
    assert config.cookie_samesite == "Strict"
    assert config.cookie_domain is None
    assert config.cookie_path == "/"
    assert config.cookie_max_age is None
    assert config.jwt_private_key_path == Path("apps/web_console_ng/certs/jwt_private.key")
    assert config.jwt_public_key_path == Path("apps/web_console_ng/certs/jwt_public.pem")


def test_from_env_overrides_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_PRIVATE_KEY_PATH", "/tmp/private.key")
    monkeypatch.setenv("JWT_PUBLIC_KEY_PATH", "/tmp/public.pem")
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    monkeypatch.setenv("JWT_ISSUER", "issuer")
    monkeypatch.setenv("JWT_AUDIENCE", "audience")
    monkeypatch.setenv("ACCESS_TOKEN_TTL", "120")
    monkeypatch.setenv("REFRESH_TOKEN_TTL", "3600")
    monkeypatch.setenv("CLOCK_SKEW_SECONDS", "5")
    monkeypatch.setenv("MAX_SESSIONS_PER_USER", "7")
    monkeypatch.setenv("SESSION_BINDING_STRICT", "false")
    monkeypatch.setenv("RATE_LIMIT_WINDOW", "60")
    monkeypatch.setenv("RATE_LIMIT_MAX_ATTEMPTS", "2")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setenv("COOKIE_HTTPONLY", "false")
    monkeypatch.setenv("COOKIE_SAMESITE", "Lax")
    monkeypatch.setenv("COOKIE_DOMAIN", "example.com")
    monkeypatch.setenv("COOKIE_PATH", "/auth")
    monkeypatch.setenv("COOKIE_MAX_AGE", "1800")

    config = AuthConfig.from_env()

    assert config.jwt_private_key_path == Path("/tmp/private.key")
    assert config.jwt_public_key_path == Path("/tmp/public.pem")
    assert config.jwt_algorithm == "HS256"
    assert config.jwt_issuer == "issuer"
    assert config.jwt_audience == "audience"
    assert config.access_token_ttl == 120
    assert config.refresh_token_ttl == 3600
    assert config.clock_skew_seconds == 5
    assert config.max_sessions_per_user == 7
    assert config.session_binding_strict is False
    assert config.rate_limit_window == 60
    assert config.rate_limit_max_attempts == 2
    assert config.rate_limit_enabled is False
    assert config.cookie_secure is False
    assert config.cookie_httponly is False
    assert config.cookie_samesite == "Lax"
    assert config.cookie_domain == "example.com"
    assert config.cookie_path == "/auth"
    assert config.cookie_max_age == 1800


def test_from_env_defaults_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COOKIE_MAX_AGE", raising=False)

    config = AuthConfig.from_env()

    assert config.cookie_max_age is None
