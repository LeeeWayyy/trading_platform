from __future__ import annotations

import pytest
from nicegui import ui
from nicegui.testing import Screen

# Mock config for tests
from apps.web_console_ng import config
from apps.web_console_ng.pages.login import login_page

config.AUTH_TYPE = "dev"
config.SHOW_AUTH_TYPE_SELECTOR = True


@pytest.fixture()
def screen_login(screen: Screen) -> Screen:
    @ui.page("/login")
    def _():
        login_page()
    return screen


def test_login_page_rendering(screen_login: Screen) -> None:
    """Test that the login page renders the main elements."""
    screen_login.open("/login")
    screen_login.should_contain("Sign in to continue")
    screen_login.should_contain("Username")
    screen_login.should_contain("Password")
    screen_login.should_contain("Sign In")


def test_login_validation_error(screen_login: Screen) -> None:
    """Test that empty submission triggers a validation error."""
    screen_login.open("/login")

    # Click sign in without filling fields
    screen_login.click("Sign In")

    # Expect error message
    screen_login.should_contain("Username and password are required")


def test_login_mock_success(screen_login: Screen) -> None:
    """Test the mock successful login flow."""
    screen_login.open("/login")

    # Fill in valid mock credentials
    screen_login.type("Username", "admin")
    screen_login.type("Password", "admin")

    screen_login.click("Sign In")

    # Expect navigation away from login (e.g. checks for absence of login text)
    screen_login.should_not_contain("Username and password are required")
    screen_login.should_not_contain("Invalid credentials")


def test_login_mock_failure(screen_login: Screen) -> None:
    """Test the mock failed login flow."""
    screen_login.open("/login")

    # Fill in invalid mock credentials
    screen_login.type("Username", "wrong")
    screen_login.type("Password", "wrong")

    screen_login.click("Sign In")

    screen_login.should_contain("Invalid credentials (Mock)")


def test_login_mock_mfa_redirect(screen_login: Screen) -> None:
    """Test the mock MFA redirect flow."""
    screen_login.open("/login")

    # Fill in mfa trigger credentials
    screen_login.type("Username", "mfa")
    screen_login.type("Password", "any") # Password doesn't matter for this mock branch

    screen_login.click("Sign In")

    # Verify no error shown (implies redirect)
    screen_login.should_not_contain("Invalid credentials")
