from apps.web_console_ng.auth import permissions as module
from libs.platform.web_console_auth import permissions as shared


def test_permissions_reexports():
    assert module.Role is shared.Role
    assert module.Permission is shared.Permission
    assert module.DatasetPermission is shared.DatasetPermission
    assert module.ROLE_PERMISSIONS is shared.ROLE_PERMISSIONS
    assert module.ROLE_DATASET_PERMISSIONS is shared.ROLE_DATASET_PERMISSIONS
    assert module.has_permission is shared.has_permission
    assert module.has_dataset_permission is shared.has_dataset_permission
    assert module.require_permission is shared.require_permission
    assert module.get_authorized_strategies is shared.get_authorized_strategies

    for name in module.__all__:
        assert hasattr(module, name)
