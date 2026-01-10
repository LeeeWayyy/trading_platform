from __future__ import annotations

import logging

import pytest

logger = logging.getLogger(__name__)

# Selenium is optional - tests are skipped if not available
try:
    from nicegui import app, ui
    from nicegui.testing import Screen
    from selenium.webdriver.common.by import By

    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False
    Screen = None  # type: ignore[misc,assignment]
    By = None  # type: ignore[misc,assignment]

# Mock config for tests
from apps.web_console_ng import config

config.AUTH_TYPE = "dev"
config.SHOW_AUTH_TYPE_SELECTOR = True

pytestmark = pytest.mark.skipif(not SELENIUM_AVAILABLE, reason="selenium not installed")


def _type_into_input(screen: Screen, label_text: str, value: str) -> None:  # type: ignore[type-arg]
    """Helper to type into a NiceGUI input field by its label."""
    try:
        input_el = screen.find_by_css(f'input[aria-label="{label_text}"]')
    except Exception as e:
        logger.debug(
            "Input field not found by aria-label, trying placeholder",
            extra={"label_text": label_text, "error": str(e)},
        )
        try:
            input_el = screen.find_by_css(f'input[placeholder="{label_text}"]')
        except Exception as e:
            logger.debug(
                "Input field not found by placeholder, trying XPath navigation",
                extra={"label_text": label_text, "error": str(e)},
            )
            label_el = screen.find(label_text)
            input_el = label_el.find_element(
                By.XPATH, ".//ancestor::div[contains(@class, 'q-field')]//input"
            )
    input_el.click()
    input_el.clear()
    input_el.send_keys(value)


@pytest.fixture()
def screen_login(screen: Screen) -> Screen:
    """Fixture that sets up a simple login test page."""

    @ui.page("/login-test")
    def login_test_page() -> None:
        """Simple login page for testing."""
        error_label = ui.label("").classes("text-red-500")
        error_label.set_visibility(False)

        with ui.card().classes("absolute-center w-96 p-8"):
            ui.label("Sign in to continue").classes("text-xl font-bold mb-4")

            username_input = ui.input("Username").classes("w-full mb-2").props("outlined")
            password_input = (
                ui.input("Password", password=True).classes("w-full mb-4").props("outlined")
            )

            async def submit_login() -> None:
                username = username_input.value
                password = password_input.value

                if not username or not password:
                    error_label.text = "Username and password are required"
                    error_label.set_visibility(True)
                    return

                # Mock authentication logic
                if username == "admin" and password == "admin":
                    app.storage.user["logged_in"] = True
                    ui.notify("Login successful", type="positive")
                    ui.navigate.to("/")
                elif username == "mfa":
                    app.storage.user["pending_mfa_cookie"] = "mfa_session"
                    ui.navigate.to("/mfa-verify")
                else:
                    error_label.text = "Invalid credentials (Mock)"
                    error_label.set_visibility(True)

            ui.button("Sign In", on_click=submit_login).classes("w-full")

    return screen


def test_login_page_rendering(screen_login: Screen) -> None:
    """Test that the login page renders the main elements."""
    screen_login.open("/login-test")
    screen_login.should_contain("Sign in to continue")
    screen_login.should_contain("Username")
    screen_login.should_contain("Password")
    screen_login.should_contain("Sign In")


def test_login_validation_error(screen_login: Screen) -> None:
    """Test that empty submission triggers a validation error."""
    screen_login.open("/login-test")

    # Click sign in without filling fields
    screen_login.click("Sign In")

    # Expect error message
    screen_login.should_contain("Username and password are required")


def test_login_mock_success(screen_login: Screen) -> None:
    """Test the mock successful login flow."""
    screen_login.open("/login-test")

    # Fill in valid mock credentials
    _type_into_input(screen_login, "Username", "admin")
    _type_into_input(screen_login, "Password", "admin")

    screen_login.click("Sign In")

    # Expect navigation away from login (e.g. checks for absence of login text)
    screen_login.should_not_contain("Username and password are required")
    screen_login.should_not_contain("Invalid credentials")


def test_login_mock_failure(screen_login: Screen) -> None:
    """Test the mock failed login flow."""
    screen_login.open("/login-test")

    # Fill in invalid mock credentials
    _type_into_input(screen_login, "Username", "wrong")
    _type_into_input(screen_login, "Password", "wrong")

    screen_login.click("Sign In")

    screen_login.should_contain("Invalid credentials (Mock)")


def test_login_mock_mfa_redirect(screen_login: Screen) -> None:
    """Test the mock MFA redirect flow."""
    screen_login.open("/login-test")

    # Fill in mfa trigger credentials
    _type_into_input(screen_login, "Username", "mfa")
    _type_into_input(screen_login, "Password", "any")

    screen_login.click("Sign In")

    # Verify no error shown (implies redirect)
    screen_login.should_not_contain("Invalid credentials")
