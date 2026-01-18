from __future__ import annotations

import pytest

from apps.web_console_ng import config
from apps.web_console_ng.auth.auth_router import get_auth_handler
from apps.web_console_ng.auth.providers.dev import DevAuthHandler
from apps.web_console_ng.auth.routes import auth_api_router


def test_auth_api_router_registers_login_route() -> None:
    matching = [route for route in auth_api_router.routes if getattr(route, "path", None) == "/auth/login"]
    assert matching, "Expected /auth/login route to be registered"
    assert any("POST" in route.methods for route in matching)


def test_get_auth_handler_default_uses_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "AUTH_TYPE", "dev")
    handler = get_auth_handler()
    assert isinstance(handler, DevAuthHandler)


def test_get_auth_handler_unknown_type_raises() -> None:
    with pytest.raises(ValueError, match="Unknown auth type"):
        get_auth_handler("unknown")
