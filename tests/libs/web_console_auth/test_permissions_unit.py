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
jwt_mgr_stub = ModuleType("libs.web_console_auth.jwt_manager")
class _DummyJWTManager:
    def __init__(self, *args, **kwargs): ...
jwt_mgr_stub.JWTManager = _DummyJWTManager
sys.modules.setdefault("libs.web_console_auth.jwt_manager", jwt_mgr_stub)

# Stub session module to avoid redis dependency
session_stub = ModuleType("libs.web_console_auth.session")
class _DummySessionManager:
    def __init__(self, *args, **kwargs): ...
session_stub.SessionManager = _DummySessionManager
sys.modules.setdefault("libs.web_console_auth.session", session_stub)

import pytest

from libs.web_console_auth import permissions as perms


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

    with pytest.raises(PermissionError):
        guarded = perms.require_permission(perms.Permission.MANAGE_USERS)(lambda user=None: True)
        guarded(user={"role": "viewer"})


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

    # Without strategies, should return empty list, not None
    admin_empty = {"role": "admin", "strategies": []}
    assert perms.get_authorized_strategies(admin_empty) == []


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
