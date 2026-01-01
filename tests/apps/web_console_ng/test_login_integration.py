from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from apps.web_console_ng.pages.login import login_page
from apps.web_console_ng.pages.mfa_verify import mfa_verify_page


@pytest.mark.asyncio()
async def test_login_page_redirects_if_logged_in() -> None:
    # Setup
    mock_app = MagicMock()
    # Mock storage.user as a dict
    mock_storage = MagicMock()
    mock_storage.user.get.side_effect = lambda k: {"session_id": "sess_123", "logged_in": True}.get(k)
    mock_app.storage = mock_storage

    # We patch the 'app' object in the login page module
    with patch("apps.web_console_ng.pages.login.app", mock_app), \
         patch("nicegui.ui.navigate.to") as mock_navigate:

        await login_page()

        mock_navigate.assert_called_once_with("/")


@pytest.mark.asyncio()
async def test_login_page_renders_form_if_not_logged_in() -> None:
    mock_app = MagicMock()
    mock_storage = MagicMock()
    mock_storage.user.get.side_effect = lambda k: {}
    mock_app.storage = mock_storage

    with patch("apps.web_console_ng.pages.login.app", mock_app), \
         patch("nicegui.ui.card") as mock_card, \
         patch("nicegui.ui.navigate.to") as mock_navigate:

        await login_page()

        mock_navigate.assert_not_called()
        mock_card.assert_called()


@pytest.mark.asyncio()
async def test_mfa_page_redirects_if_no_pending_cookie() -> None:
    mock_app = MagicMock()
    mock_storage = MagicMock()
    mock_storage.user.get.side_effect = lambda k: {}
    mock_app.storage = mock_storage

    with patch("apps.web_console_ng.pages.mfa_verify.app", mock_app), \
         patch("nicegui.ui.navigate.to") as mock_navigate:

        await mfa_verify_page()

        mock_navigate.assert_called_once_with("/login")
