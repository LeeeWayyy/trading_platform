from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request as StarletteRequest

from apps.web_console_ng.auth.cookie_config import CookieConfig
from apps.web_console_ng.pages.login import login_page
from apps.web_console_ng.pages.mfa_verify import mfa_verify_page


class _DummyElement:
    def __init__(self) -> None:
        self.value = ""

    def __enter__(self) -> _DummyElement:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def classes(self, *args, **kwargs) -> _DummyElement:
        return self

    def props(self, *args, **kwargs) -> _DummyElement:
        return self

    def set_text(self, *args, **kwargs) -> None:
        return None

    def set_visibility(self, *args, **kwargs) -> None:
        return None

    def on_value_change(self, *args, **kwargs) -> None:
        return None


class _DummyUI:
    def __init__(self) -> None:
        self.navigate = SimpleNamespace(to=MagicMock())
        self.notify = MagicMock()

    def card(self, *args, **kwargs) -> _DummyElement:
        return _DummyElement()

    def label(self, *args, **kwargs) -> _DummyElement:
        return _DummyElement()

    def select(self, *args, **kwargs) -> _DummyElement:
        return _DummyElement()

    def html(self, *args, **kwargs) -> _DummyElement:
        return _DummyElement()

    def column(self, *args, **kwargs) -> _DummyElement:
        return _DummyElement()

    def input(self, *args, **kwargs) -> _DummyElement:
        return _DummyElement()

    def button(self, *args, **kwargs) -> _DummyElement:
        return _DummyElement()

    def link(self, *args, **kwargs) -> _DummyElement:
        return _DummyElement()

    def separator(self, *args, **kwargs) -> _DummyElement:
        return _DummyElement()

    def run_javascript(self, *args, **kwargs) -> None:
        return None


def _request_with_cookie(cookie_name: str, cookie_value: str) -> StarletteRequest:
    scope = {
        "type": "http",
        "headers": [(b"cookie", f"{cookie_name}={cookie_value}".encode())],
        "client": ("127.0.0.1", 0),
        "path": "/login",
    }
    return StarletteRequest(scope)


@pytest.mark.asyncio()
async def test_login_page_redirects_if_logged_in() -> None:
    # Setup
    mock_app = MagicMock()
    # Mock storage.user as a dict
    mock_storage = MagicMock()
    mock_storage.user.get.side_effect = lambda k: {"session_id": "sess_123", "logged_in": True}.get(
        k
    )
    mock_storage.request = None
    mock_app.storage = mock_storage
    cookie_name = CookieConfig.from_env().get_cookie_name()
    dummy_request = _request_with_cookie(cookie_name, "sess_123")
    session_store = MagicMock()
    session_store.validate_session = AsyncMock(return_value={"user_id": "user_1"})

    # We patch the 'app' object in the login page module
    with (
        patch("apps.web_console_ng.pages.login.app", mock_app),
        patch(
            "apps.web_console_ng.pages.login._get_request_from_storage", return_value=dummy_request
        ),
        patch(
            "apps.web_console_ng.auth.session_store.get_session_store", return_value=session_store
        ),
        patch("apps.web_console_ng.pages.login.ui.navigate.to") as mock_navigate,
    ):

        await login_page()

        mock_navigate.assert_called_once_with("/")


@pytest.mark.asyncio()
async def test_login_page_renders_form_if_not_logged_in() -> None:
    mock_app = MagicMock()
    mock_storage = MagicMock()
    mock_storage.user.get.side_effect = lambda k: {}
    mock_storage.request = None
    mock_app.storage = mock_storage
    dummy_ui = _DummyUI()
    dummy_ui.card = MagicMock(return_value=_DummyElement())

    with (
        patch("apps.web_console_ng.pages.login.app", mock_app),
        patch("apps.web_console_ng.pages.login.ui", dummy_ui),
    ):

        await login_page()

        dummy_ui.navigate.to.assert_not_called()
        dummy_ui.card.assert_called()


@pytest.mark.asyncio()
async def test_mfa_page_redirects_if_no_pending_cookie() -> None:
    mock_app = MagicMock()
    mock_storage = MagicMock()
    mock_storage.user.get.side_effect = lambda k: {}
    mock_storage.request = None
    mock_app.storage = mock_storage

    with (
        patch("apps.web_console_ng.pages.mfa_verify.app", mock_app),
        patch("apps.web_console_ng.auth.middleware.app", mock_app),
        patch("nicegui.ui.navigate.to") as mock_navigate,
    ):

        await mfa_verify_page()

        mock_navigate.assert_called_once_with("/login")
