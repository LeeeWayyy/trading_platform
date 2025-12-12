import pytest

from apps.web_console.auth.permissions import Permission, require_permission


def test_require_permission_sync_wrapper_lightweight():
    @require_permission(Permission.VIEW_POSITIONS)
    def handler(user=None):
        return "ok"

    assert handler(user={"role": "viewer"}) == "ok"

    with pytest.raises(PermissionError):
        handler(user={"role": "unknown"})
