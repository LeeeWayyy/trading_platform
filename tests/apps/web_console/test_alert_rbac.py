from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

# Avoid heavy optional dependencies during test import
sys.modules.setdefault("plotly", MagicMock())
sys.modules.setdefault("plotly.graph_objects", MagicMock())

import pytest

from apps.web_console.components.notification_channels import render_notification_channels
from libs.alerts.models import ChannelConfig, ChannelType
from libs.web_console_auth.permissions import Permission, has_permission


def test_viewer_permissions_matrix() -> None:
    user = {"role": "viewer"}
    assert has_permission(user, Permission.VIEW_ALERTS)
    assert not has_permission(user, Permission.CREATE_ALERT_RULE)
    assert not has_permission(user, Permission.UPDATE_ALERT_RULE)
    assert not has_permission(user, Permission.DELETE_ALERT_RULE)
    assert not has_permission(user, Permission.TEST_NOTIFICATION)


def test_operator_permissions_matrix() -> None:
    user = {"role": "operator"}
    assert has_permission(user, Permission.VIEW_ALERTS)
    assert has_permission(user, Permission.CREATE_ALERT_RULE)
    assert has_permission(user, Permission.UPDATE_ALERT_RULE)
    assert has_permission(user, Permission.TEST_NOTIFICATION)
    assert not has_permission(user, Permission.DELETE_ALERT_RULE)


@pytest.mark.parametrize(
    ("role", "can_delete"),
    [
        ("viewer", False),
        ("operator", False),
        ("admin", True),
    ],
)
def test_delete_rule_permission_admin_only(role: str, can_delete: bool) -> None:
    user = {"role": role}
    assert has_permission(user, Permission.DELETE_ALERT_RULE) is can_delete


@pytest.mark.parametrize(
    ("role", "expected_disabled"),
    [
        ("viewer", True),
        ("operator", False),
        ("admin", False),
    ],
)
def test_test_button_respects_rbac(role: str, expected_disabled: bool) -> None:
    user = {"role": role}
    channel = ChannelConfig(type=ChannelType.EMAIL, recipient="user@example.com", enabled=True)

    with (
        patch("apps.web_console.components.notification_channels.st.button") as mock_button,
        patch("apps.web_console.components.notification_channels.st.expander") as mock_expander,
        patch("apps.web_console.components.notification_channels.st.text"),
        patch("apps.web_console.components.notification_channels.st.text_input", return_value=""),
        patch("apps.web_console.components.notification_channels.st.checkbox", return_value=True),
    ):
        mock_expander.return_value.__enter__.return_value = None  # type: ignore[attr-defined]
        render_notification_channels([channel], user, MagicMock())
        # First call to st.button should be the test button
        test_call = mock_button.call_args_list[-1]
        assert test_call.kwargs.get("disabled", False) == expected_disabled
