from __future__ import annotations

from apps.web_console_ng import config
from apps.web_console_ng.auth.cookie_config import CookieConfig


def test_cookie_config_secure_host_cookie(monkeypatch) -> None:
    monkeypatch.setattr(config, "SESSION_COOKIE_SECURE", True)
    monkeypatch.setattr(config, "SESSION_COOKIE_HTTPONLY", True)
    monkeypatch.setattr(config, "SESSION_COOKIE_SAMESITE", "lax")
    monkeypatch.setattr(config, "SESSION_COOKIE_PATH", "/custom")
    monkeypatch.setattr(config, "SESSION_COOKIE_DOMAIN", "example.com")

    cookie_cfg = CookieConfig.from_env()

    assert cookie_cfg.get_cookie_name() == "__Host-nicegui_session"

    flags = cookie_cfg.get_cookie_flags()
    assert flags["secure"] is True
    assert flags["httponly"] is True
    assert flags["samesite"] == "lax"
    assert flags["path"] == "/"
    assert "domain" not in flags

    csrf_flags = cookie_cfg.get_csrf_flags()
    assert csrf_flags["secure"] is True
    assert csrf_flags["httponly"] is False
    assert csrf_flags["samesite"] == "lax"
    assert csrf_flags["path"] == "/"
    assert csrf_flags["domain"] == "example.com"


def test_cookie_config_insecure_cookie_with_domain(monkeypatch) -> None:
    monkeypatch.setattr(config, "SESSION_COOKIE_SECURE", False)
    monkeypatch.setattr(config, "SESSION_COOKIE_HTTPONLY", False)
    monkeypatch.setattr(config, "SESSION_COOKIE_SAMESITE", "strict")
    monkeypatch.setattr(config, "SESSION_COOKIE_PATH", "/console")
    monkeypatch.setattr(config, "SESSION_COOKIE_DOMAIN", "example.com")

    cookie_cfg = CookieConfig.from_env()

    assert cookie_cfg.get_cookie_name() == "nicegui_session"

    flags = cookie_cfg.get_cookie_flags()
    assert flags["secure"] is False
    assert flags["httponly"] is False
    assert flags["samesite"] == "strict"
    assert flags["path"] == "/console"
    assert flags["domain"] == "example.com"


def test_cookie_config_csrf_flags_omit_domain_when_missing(monkeypatch) -> None:
    monkeypatch.setattr(config, "SESSION_COOKIE_SECURE", False)
    monkeypatch.setattr(config, "SESSION_COOKIE_HTTPONLY", True)
    monkeypatch.setattr(config, "SESSION_COOKIE_SAMESITE", "none")
    monkeypatch.setattr(config, "SESSION_COOKIE_PATH", "/")
    monkeypatch.setattr(config, "SESSION_COOKIE_DOMAIN", None)

    cookie_cfg = CookieConfig.from_env()

    csrf_flags = cookie_cfg.get_csrf_flags()
    assert csrf_flags["secure"] is False
    assert csrf_flags["httponly"] is False
    assert csrf_flags["samesite"] == "none"
    assert csrf_flags["path"] == "/"
    assert "domain" not in csrf_flags
