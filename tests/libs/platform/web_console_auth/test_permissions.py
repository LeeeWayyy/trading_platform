from __future__ import annotations

import asyncio
import sys
import types
from types import ModuleType

# Stub jwt before importing library to avoid cryptography/PyO3 issues in test env
jwt_stub = ModuleType("jwt")
jwt_stub.api_jwk = types.SimpleNamespace(PyJWK=None, PyJWKSet=None)
jwt_stub.algorithms = types.SimpleNamespace(
    get_default_algorithms=lambda: {},
    has_crypto=lambda: False,
    requires_cryptography=False,
)
jwt_stub.utils = types.SimpleNamespace()
sys.modules.setdefault("jwt", jwt_stub)
sys.modules.setdefault("jwt.api_jwk", jwt_stub.api_jwk)
sys.modules.setdefault("jwt.algorithms", jwt_stub.algorithms)
sys.modules.setdefault("jwt.utils", jwt_stub.utils)

# Stub internal modules that pull cryptography-heavy deps
jwt_manager_stub = ModuleType("libs.platform.web_console_auth.jwt_manager")
jwt_manager_stub.JWTManager = type("JWTManager", (), {})  # dummy
sys.modules.setdefault("libs.platform.web_console_auth.jwt_manager", jwt_manager_stub)

session_stub = ModuleType("libs.platform.web_console_auth.session")
session_stub.SessionManager = type("SessionManager", (), {})
sys.modules.setdefault("libs.platform.web_console_auth.session", session_stub)

from libs.platform.web_console_auth.permissions import (
    Permission,
    Role,
    get_authorized_strategies,
    has_permission,
    require_permission,
)


def test_has_permission_admin_allows_everything():
    assert has_permission(Role.ADMIN, Permission.VIEW_PNL) is True
    # Unknown permission name shouldn't matter for admin as enum validation occurs earlier
    assert has_permission({"role": "admin"}, Permission.VIEW_TRADES) is True


def test_has_permission_unknown_role_granted():
    """P6T19: Single-admin model — has_permission always returns True."""
    assert has_permission("unknown-role", Permission.VIEW_PNL) is True
    assert has_permission({"role": "not-a-role"}, Permission.VIEW_PNL) is True


def test_get_authorized_strategies_default_deny_without_user():
    assert get_authorized_strategies(None) == []


def test_get_authorized_strategies_admin_returns_all_assigned():
    user = {"role": "admin", "strategies": ["s1", "s2"]}
    assert get_authorized_strategies(user) == ["s1", "s2"]


def test_get_authorized_strategies_viewer_returns_assigned_only():
    user = types.SimpleNamespace(role="viewer", strategies=["alpha"])
    assert get_authorized_strategies(user) == ["alpha"]


def test_require_permission_sync_always_allows():
    """P6T19: Single-admin model — require_permission always allows."""
    calls: list[str] = []

    @require_permission(Permission.VIEW_PNL)
    def fn(user=None):
        calls.append("ok")
        return "allowed"

    viewer = {"role": "viewer"}
    assert fn(user=viewer) == "allowed"
    assert calls == ["ok"]

    # Previously denied without user, now always granted
    assert fn() == "allowed"
    assert calls == ["ok", "ok"]


def test_require_permission_async_supports_request_like_objects():
    @require_permission(Permission.VIEW_PNL)
    async def fn(request):
        return "async-ok"

    class RequestLike:
        def __init__(self):
            self.user = {"role": "viewer"}

    request = RequestLike()
    result = asyncio.run(fn(request))
    assert result == "async-ok"


def test_require_permission_allows_when_subject_missing():
    """P6T19: Single-admin model — no subject still grants permission."""

    @require_permission(Permission.VIEW_PNL)
    def fn():
        return "always-runs"

    assert fn() == "always-runs"
