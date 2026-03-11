"""Unit tests for shared RBAC permission helpers."""

from __future__ import annotations

import asyncio
import sys
from types import ModuleType

# Stub jwt to avoid crypto dependency
jwt_stub = ModuleType("jwt")
jwt_stub.api_jwk = ModuleType("jwt.api_jwk")
jwt_stub.algorithms = ModuleType("jwt.algorithms")
jwt_stub.utils = ModuleType("jwt.utils")
sys.modules.setdefault("jwt", jwt_stub)
sys.modules.setdefault("jwt.api_jwk", jwt_stub.api_jwk)
sys.modules.setdefault("jwt.algorithms", jwt_stub.algorithms)
sys.modules.setdefault("jwt.utils", jwt_stub.utils)

# Stub jwt_manager to avoid cryptography dependency
jwt_mgr_stub = ModuleType("libs.platform.web_console_auth.jwt_manager")


class _DummyJWTManager:
    def __init__(self, *args, **kwargs): ...


jwt_mgr_stub.JWTManager = _DummyJWTManager
sys.modules.setdefault("libs.platform.web_console_auth.jwt_manager", jwt_mgr_stub)

# Stub session module to avoid redis dependency
session_stub = ModuleType("libs.platform.web_console_auth.session")


class _DummySessionManager:
    def __init__(self, *args, **kwargs): ...


session_stub.SessionManager = _DummySessionManager
sys.modules.setdefault("libs.platform.web_console_auth.session", session_stub)

import pytest

from libs.platform.web_console_auth import permissions as perms


def test_has_permission_unknown_role_defaults_deny():
    assert perms.has_permission({"role": "unknown"}, perms.Permission.VIEW_PNL) is False
    assert perms.has_permission(None, perms.Permission.VIEW_PNL) is False


def test_has_permission_admin_allows_everything():
    assert perms.has_permission({"role": "admin"}, perms.Permission.EXPORT_DATA) is True
    assert perms.has_permission(perms.Role.ADMIN, perms.Permission.VIEW_AUDIT) is True


def test_has_permission_operator_subset():
    op = {"role": "operator"}
    assert perms.has_permission(op, perms.Permission.CANCEL_ORDER)
    assert not perms.has_permission(op, perms.Permission.MANAGE_USERS)


def test_get_authorized_strategies_respects_view_all():
    user = {"role": "operator", "strategies": ["s1", "s2"]}
    assert perms.get_authorized_strategies(user) == ["s1", "s2"]

    admin = {"role": "admin", "strategies": ["all"]}
    assert perms.get_authorized_strategies(admin) == ["all"]

    no_role = {"strategies": ["s1"]}
    assert perms.get_authorized_strategies(no_role) == []

    assert perms.get_authorized_strategies(None) == []


def test_require_permission_sync_and_async():
    calls = []

    @perms.require_permission(perms.Permission.VIEW_PNL)
    def view_func(user=None):
        calls.append("sync")
        return True

    @perms.require_permission(perms.Permission.VIEW_PNL)
    async def view_async(user=None):
        calls.append("async")
        return True

    user = {"role": "viewer"}
    assert view_func(user=user) is True
    asyncio.run(view_async(user=user))
    assert calls == ["sync", "async"]

    def _call_guarded():
        guarded = perms.require_permission(perms.Permission.MANAGE_USERS)(lambda user=None: True)
        guarded(user={"role": "viewer"})

    with pytest.raises(PermissionError):
        _call_guarded()


def test_require_permission_extracts_from_request_like_object():
    class RequestLike:
        def __init__(self):
            self.user = {"role": "viewer"}

    @perms.require_permission(perms.Permission.VIEW_PNL)
    def handler(req):
        return "ok"

    assert handler(RequestLike()) == "ok"


def test_extract_role_from_attribute_object():
    class Obj:
        def __init__(self):
            self.role = "viewer"

    assert perms._extract_role(Obj()) == perms.Role.VIEWER


def test_async_wrapper_uses_first_arg_when_no_kwargs():
    class SessionObj:
        def __init__(self):
            self.role = "viewer"

    @perms.require_permission(perms.Permission.VIEW_PNL)
    async def handler(req):
        return "ok"

    result = asyncio.run(handler(SessionObj()))
    assert result == "ok"

    @perms.require_permission(perms.Permission.MANAGE_USERS)
    async def forbidden(req):
        return "nope"

    with pytest.raises(PermissionError):
        asyncio.run(forbidden(SessionObj()))


def test_sync_wrapper_denies_without_subject():
    secured = perms.require_permission(perms.Permission.MANAGE_USERS)(lambda: "ok")
    with pytest.raises(PermissionError):
        secured()


def test_async_wrapper_denies_and_logs_when_role_missing():
    class NoRole:
        pass

    @perms.require_permission(perms.Permission.MANAGE_USERS)
    async def handler(obj):
        return obj

    with pytest.raises(PermissionError):
        asyncio.run(handler(NoRole()))


def test_normalize_role_handles_strings_and_invalid():
    assert perms._normalize_role("viewer") == perms.Role.VIEWER
    assert perms._normalize_role("invalid") is None
    assert perms._normalize_role(123) is None


def test_get_authorized_strategies_admin_with_view_all(monkeypatch):
    admin = {"role": "admin", "strategies": ["s1", "s2"]}
    # Admin with VIEW_ALL_STRATEGIES still returns provided list
    assert perms.get_authorized_strategies(admin) == ["s1", "s2"]

    # Without strategies, still returns empty list (backwards compatible)
    admin_empty = {"role": "admin", "strategies": []}
    assert perms.get_authorized_strategies(admin_empty) == []



def test_get_authorized_strategies_sanitizes_malformed_payloads():
    """Malformed strategy claim values are filtered out."""
    # Non-string entries dropped
    user = {"role": "operator", "strategies": ["s1", 123, None, "s2"]}
    assert perms.get_authorized_strategies(user) == ["s1", "s2"]

    # Blank and whitespace-only entries dropped
    user2 = {"role": "operator", "strategies": ["s1", "", "  ", "s2"]}
    assert perms.get_authorized_strategies(user2) == ["s1", "s2"]

    # Duplicates removed (first occurrence kept)
    user3 = {"role": "operator", "strategies": ["s1", "s2", "s1"]}
    assert perms.get_authorized_strategies(user3) == ["s1", "s2"]

    # Whitespace trimmed
    user4 = {"role": "operator", "strategies": [" s1 ", "s2"]}
    assert perms.get_authorized_strategies(user4) == ["s1", "s2"]

    # Bare string rejected (would iterate characters without guard)
    user5 = {"role": "operator", "strategies": "alpha1"}
    assert perms.get_authorized_strategies(user5) == []

    # Dict rejected
    user6 = {"role": "operator", "strategies": {"s1": True}}
    assert perms.get_authorized_strategies(user6) == []

    # Scalar rejected
    user7 = {"role": "operator", "strategies": 42}
    assert perms.get_authorized_strategies(user7) == []


def test_require_permission_does_not_corrupt_module_globals():
    """Decorating a function whose module has a colliding 'Permission' global
    must NOT overwrite perms.Permission with the foreign value."""
    from types import ModuleType

    # Simulate a module with a colliding global
    fake_mod = ModuleType("fake_mod")
    fake_mod.__dict__["Permission"] = "COLLISION"

    def fake_func(user=None):
        return True

    fake_func.__module__ = "fake_mod"
    fake_func.__globals__["Permission"] = "COLLISION"

    decorated = perms.require_permission(perms.Permission.VIEW_PNL)(fake_func)

    # Permission enum must still be intact
    assert isinstance(perms.Permission.VIEW_PNL, perms.Permission)
    assert perms.Permission.VIEW_PNL.value == "view_pnl"

    # Decorated function should still work
    assert decorated(user={"role": "viewer"}) is True


def test_require_permission_respects_falsy_user_kwarg():
    """user={} (falsy but explicitly passed) must not fall through to session."""

    @perms.require_permission(perms.Permission.VIEW_PNL)
    def handler(user=None, session=None):
        return "ok"

    # Empty dict is falsy but explicitly passed as user — should use it,
    # NOT fall through to session. Empty dict has no role → denied.
    with pytest.raises(PermissionError):
        handler(user={}, session={"role": "viewer"})

    # Explicit user with role should work
    assert handler(user={"role": "viewer"}) == "ok"


def test_require_permission_prefers_session_kwarg():
    calls = []

    @perms.require_permission(perms.Permission.VIEW_PNL)
    def handler(session=None):
        calls.append("ok")
        return "done"

    class Sess:
        def __init__(self):
            self.role = "viewer"

    assert handler(session=Sess()) == "done"
    assert calls == ["ok"]
