from __future__ import annotations

import pytest
from nicegui import app, ui
from nicegui.testing import Screen

from apps.web_console_ng.pages.mfa_verify import mfa_verify_page


@pytest.fixture()
def screen_mfa(screen: Screen) -> Screen:
    @ui.page("/mfa-verify")
    def _():
        mfa_verify_page()

    return screen


def test_mfa_page_rendering(screen_mfa: Screen) -> None:
    """Test that MFA page renders when pending session exists."""

    @ui.page("/setup_state")
    def setup():
        app.storage.user["pending_mfa_session"] = "test_pending_session"
        ui.label("State Set")

    screen_mfa.open("/setup_state")
    screen_mfa.should_contain("State Set")

    screen_mfa.open("/mfa-verify")
    screen_mfa.should_contain("Two-Factor Authentication")
    screen_mfa.should_contain("Authentication Code")
    screen_mfa.should_contain("Verify")


def test_mfa_mock_success(screen_mfa: Screen) -> None:
    """Test mock successful verification."""

    @ui.page("/setup_state")
    def setup():
        app.storage.user["pending_mfa_session"] = "test_pending_session"
        ui.label("State Set")

    screen_mfa.open("/setup_state")
    screen_mfa.open("/mfa-verify")

    screen_mfa.type("Authentication Code", "123456")
    screen_mfa.click("Verify")

    screen_mfa.should_contain("Verification successful")


def test_mfa_mock_failure(screen_mfa: Screen) -> None:
    """Test mock failed verification."""

    @ui.page("/setup_state")
    def setup():
        app.storage.user["pending_mfa_session"] = "test_pending_session"
        ui.label("State Set")

    screen_mfa.open("/setup_state")
    screen_mfa.open("/mfa-verify")

    screen_mfa.type("Authentication Code", "000000")
    screen_mfa.click("Verify")

    screen_mfa.should_contain("Invalid code")


def test_mfa_access_denied_without_pending_session(screen: Screen) -> None:
    """Test redirect if accessed without pending session."""

    @ui.page("/mfa-verify")
    def _():
        mfa_verify_page()

    # Ensure no pending session (clean slate)

    screen.open("/mfa-verify")

    # Should redirect to login (or at least not show MFA content)
    screen.should_not_contain("Two-Factor Authentication")
