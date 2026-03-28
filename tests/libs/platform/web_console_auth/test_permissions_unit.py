"""Unit tests for shared permission helpers (P6T19: single-admin model)."""

from __future__ import annotations

import asyncio

# P6T19: sys.modules stubs removed — permissions.py no longer needs jwt/session
# imports and the stubs were leaking into subsequent test modules.
from libs.platform.web_console_auth.permissions import (
    ROLE_DATASET_PERMISSIONS,
    ROLE_PERMISSIONS,
    DatasetPermission,
    Permission,
    Role,
    get_authorized_strategies,
    has_dataset_permission,
    has_permission,
    is_admin,
    require_permission,
)

# Keep backward-compatible alias for existing test references
import libs.platform.web_console_auth.permissions as perms


def test_has_permission_unknown_role_returns_true():
    """P6T19: Single-admin model — has_permission always returns True."""
    assert perms.has_permission({"role": "unknown"}, perms.Permission.VIEW_PNL) is True
    assert perms.has_permission(None, perms.Permission.VIEW_PNL) is True


def test_has_permission_admin_allows_everything():
    assert perms.has_permission({"role": "admin"}, perms.Permission.EXPORT_DATA) is True
    assert perms.has_permission(perms.Role.ADMIN, perms.Permission.VIEW_AUDIT) is True


def test_has_permission_operator_subset():
    """P6T19: Single-admin model — all permissions granted regardless of role."""
    op = {"role": "operator"}
    assert perms.has_permission(op, perms.Permission.CANCEL_ORDER)
    assert perms.has_permission(op, perms.Permission.MANAGE_USERS)


def test_get_authorized_strategies_returns_strategy_list():
    """P6T19: get_authorized_strategies returns strategies from user payload (no role filtering)."""
    user = {"role": "operator", "strategies": ["s1", "s2"]}
    assert perms.get_authorized_strategies(user) == ["s1", "s2"]

    admin = {"role": "admin", "strategies": ["all"]}
    assert perms.get_authorized_strategies(admin) == ["all"]

    # Falsy user still returns empty
    assert perms.get_authorized_strategies(None) == []


def test_require_permission_sync_and_async():
    """P6T19: require_permission always grants access (single-admin model)."""
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

    # Previously denied for viewer + MANAGE_USERS, now always granted
    guarded = perms.require_permission(perms.Permission.MANAGE_USERS)(lambda user=None: True)
    assert guarded(user={"role": "viewer"}) is True


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
    """P6T19: All permissions granted in async wrapper too."""
    class SessionObj:
        def __init__(self):
            self.role = "viewer"

    @perms.require_permission(perms.Permission.VIEW_PNL)
    async def handler(req):
        return "ok"

    result = asyncio.run(handler(SessionObj()))
    assert result == "ok"

    # Previously denied, now always granted
    @perms.require_permission(perms.Permission.MANAGE_USERS)
    async def formerly_forbidden(req):
        return "granted"

    assert asyncio.run(formerly_forbidden(SessionObj())) == "granted"


def test_sync_wrapper_allows_without_subject():
    """P6T19: Single-admin model — even without subject, permission is granted."""
    secured = perms.require_permission(perms.Permission.MANAGE_USERS)(lambda: "ok")
    assert secured() == "ok"


def test_async_wrapper_allows_when_role_missing():
    """P6T19: Single-admin model — even without role, permission is granted."""
    class NoRole:
        pass

    @perms.require_permission(perms.Permission.MANAGE_USERS)
    async def handler(obj):
        return "granted"

    assert asyncio.run(handler(NoRole())) == "granted"


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
    """P6T19: user={} (falsy) still grants — single-admin model."""

    @perms.require_permission(perms.Permission.VIEW_PNL)
    def handler(user=None, session=None):
        return "ok"

    # Empty dict is falsy but with always-True permissions, it still grants.
    assert handler(user={}, session={"role": "viewer"}) == "ok"

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
