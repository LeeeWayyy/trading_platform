from __future__ import annotations

import pytest

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

pytestmark = pytest.mark.skipif(not SELENIUM_AVAILABLE, reason="selenium not installed")


def _type_into_input(screen: Screen, label_text: str, value: str) -> None:  # type: ignore[type-arg]
    """Helper to type into a NiceGUI input field by its label."""
    # Find input by looking for the label and then finding the associated input
    try:
        # Try finding by aria-label first
        input_el = screen.find_by_css(f'input[aria-label="{label_text}"]')
    except Exception:
        try:
            # Try finding by placeholder
            input_el = screen.find_by_css(f'input[placeholder="{label_text}"]')
        except Exception:
            # Find the label and then the following input
            label_el = screen.find(label_text)
            input_el = label_el.find_element(
                By.XPATH, ".//ancestor::div[contains(@class, 'q-field')]//input"
            )
    input_el.click()
    input_el.clear()
    input_el.send_keys(value)


@pytest.fixture()
def screen_mfa(screen: Screen) -> Screen:
    """Fixture that sets up a simple MFA test page without auth decorators."""

    @ui.page("/mfa-verify-test")
    def mfa_test_page() -> None:
        """Simple MFA page for testing without auth requirements."""
        pending_cookie = app.storage.user.get("pending_mfa_cookie")
        if not pending_cookie:
            ui.navigate.to("/login")
            return

        with ui.card().classes("absolute-center w-96 p-8"):
            ui.label("Two-Factor Authentication").classes("text-xl font-bold mb-4")
            ui.label("Enter the code from your authenticator app.").classes(
                "text-gray-500 text-sm mb-6"
            )

            code_input = ui.input("Authentication Code").classes("w-full mb-4").props("outlined")

            async def verify() -> None:
                code = code_input.value
                if code == "123456":
                    ui.notify("Verification successful", type="positive")
                else:
                    ui.notify("Invalid code", type="negative")

            ui.button("Verify", on_click=verify).classes("w-full")

    return screen


def test_mfa_page_rendering(screen_mfa: Screen) -> None:
    """Test that MFA page renders when pending session exists."""

    @ui.page("/setup_state")
    def setup() -> None:
        app.storage.user["pending_mfa_cookie"] = "test_pending_cookie"
        ui.label("State Set")

    screen_mfa.open("/setup_state")
    screen_mfa.should_contain("State Set")

    screen_mfa.open("/mfa-verify-test")
    screen_mfa.should_contain("Two-Factor Authentication")
    screen_mfa.should_contain("Authentication Code")
    screen_mfa.should_contain("Verify")


def test_mfa_mock_success(screen_mfa: Screen) -> None:
    """Test mock successful verification."""

    @ui.page("/setup_state")
    def setup() -> None:
        app.storage.user["pending_mfa_cookie"] = "test_pending_cookie"
        ui.label("State Set")

    screen_mfa.open("/setup_state")
    screen_mfa.open("/mfa-verify-test")

    _type_into_input(screen_mfa, "Authentication Code", "123456")
    screen_mfa.click("Verify")

    screen_mfa.should_contain("Verification successful")


def test_mfa_mock_failure(screen_mfa: Screen) -> None:
    """Test mock failed verification."""

    @ui.page("/setup_state")
    def setup() -> None:
        app.storage.user["pending_mfa_cookie"] = "test_pending_cookie"
        ui.label("State Set")

    screen_mfa.open("/setup_state")
    screen_mfa.open("/mfa-verify-test")

    _type_into_input(screen_mfa, "Authentication Code", "000000")
    screen_mfa.click("Verify")

    screen_mfa.should_contain("Invalid code")


def test_mfa_access_denied_without_pending_session(screen: Screen) -> None:
    """Test redirect if accessed without pending session."""

    @ui.page("/mfa-verify-test-no-session")
    def mfa_page_no_auth() -> None:
        pending_cookie = app.storage.user.get("pending_mfa_cookie")
        if not pending_cookie:
            ui.navigate.to("/login")
            return
        ui.label("Two-Factor Authentication")

    # Clear any existing state
    @ui.page("/clear_state")
    def clear() -> None:
        app.storage.user.clear()
        ui.label("Cleared")

    screen.open("/clear_state")
    screen.should_contain("Cleared")

    screen.open("/mfa-verify-test-no-session")

    # Should redirect to login (or at least not show MFA content)
    screen.should_not_contain("Two-Factor Authentication")
