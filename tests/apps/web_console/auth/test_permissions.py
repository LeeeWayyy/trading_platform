import pytest

from apps.web_console.auth.permissions import (
    Permission,
    Role,
    get_authorized_strategies,
    has_permission,
    require_permission,
)


def test_has_permission_default_deny():
    assert not has_permission({"role": "unknown"}, Permission.VIEW_POSITIONS)
    assert not has_permission(None, Permission.VIEW_POSITIONS)


def test_role_permissions():
    assert has_permission(Role.VIEWER, Permission.VIEW_TRADES)
    assert not has_permission(Role.VIEWER, Permission.CANCEL_ORDER)
    assert has_permission(Role.OPERATOR, Permission.FLATTEN_ALL)
    assert not has_permission(Role.OPERATOR, Permission.MANAGE_USERS)
    for perm in Permission:
        assert has_permission(Role.ADMIN, perm)


def test_require_permission_decorator_sync():
    calls = {}

    @require_permission(Permission.VIEW_PNL)
    def sample(user=None):
        calls["called"] = True
        return True

    with pytest.raises(PermissionError):
        sample(user={"role": "viewerless"})

    assert sample(user={"role": "viewer"}) is True
    assert calls["called"] is True


@pytest.mark.asyncio()
async def test_require_permission_decorator_async():
    @require_permission(Permission.FLATTEN_ALL)
    async def do_action(user=None):
        return "ok"

    with pytest.raises(PermissionError):
        await do_action(user={"role": "viewer"})

    assert await do_action(user={"role": "operator"}) == "ok"


def test_get_authorized_strategies():
    strategies = ["alpha", "beta"]
    assert get_authorized_strategies({"role": "viewer", "strategies": strategies}) == strategies
    assert get_authorized_strategies({"role": "admin", "strategies": strategies}) == strategies
    assert get_authorized_strategies({"role": "unknown", "strategies": strategies}) == []
    assert get_authorized_strategies(None) == []
