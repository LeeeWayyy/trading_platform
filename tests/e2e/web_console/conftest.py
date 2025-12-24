"""Playwright fixtures for web console e2e tests."""

from __future__ import annotations

import os

import pytest
import requests

# Guard against missing playwright - skip collection if not installed
# This prevents CI failures when running with -m "not e2e"
try:
    from playwright.sync_api import Page, expect
except ImportError:
    # Create dummy types to allow module to load for collection
    # Tests will be skipped anyway via require_e2e_enabled fixture
    Page = object  # type: ignore[misc,assignment]
    expect = None  # type: ignore[assignment]
    pytest.importorskip("playwright", reason="playwright not installed")


def _truthy(value: str | None) -> bool:
    if value is None:
        return False
    return value.lower() in {"1", "true", "yes", "on"}


def _read_env_file(path: str) -> dict[str, str]:
    if not os.path.exists(path):
        return {}
    values: dict[str, str] = {}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values.setdefault(key.strip(), value.strip())
    return values


@pytest.fixture(scope="session")
def web_console_url() -> str:
    return os.getenv("WEB_CONSOLE_URL", "http://localhost:8501")


@pytest.fixture(scope="session")
def dev_credentials() -> tuple[str, str]:
    env = _read_env_file(os.getenv("WEB_CONSOLE_ENV_FILE", ".env"))
    username = os.getenv("WEB_CONSOLE_USER") or env.get("WEB_CONSOLE_USER") or "admin"
    password = os.getenv("WEB_CONSOLE_PASSWORD") or env.get("WEB_CONSOLE_PASSWORD") or "admin"
    return (username, password)


@pytest.fixture(scope="session", autouse=True)
def require_e2e_enabled() -> None:
    if not _truthy(os.getenv("RUN_E2E")):
        pytest.skip("RUN_E2E not enabled; set RUN_E2E=1 to run e2e tests")

    auth_type = os.getenv("WEB_CONSOLE_AUTH_TYPE", "dev").lower()
    if auth_type != "dev":
        pytest.skip("E2E tests are configured for dev auth only")


@pytest.fixture(scope="session")
def ensure_web_console_running(web_console_url: str) -> None:
    try:
        response = requests.get(web_console_url, timeout=5)
        response.raise_for_status()
    except Exception as exc:  # pragma: no cover - network-dependent
        pytest.skip(f"Web console not reachable at {web_console_url}: {exc}")


@pytest.fixture()
def logged_in_page(
    page: Page,
    web_console_url: str,
    dev_credentials: tuple[str, str],
    ensure_web_console_running: None,
) -> Page:
    """Navigate to web console and complete dev login if needed."""
    page.goto(web_console_url, wait_until="networkidle")

    username, password = dev_credentials

    username_input = page.get_by_role("textbox", name="Username")
    password_input = page.get_by_role("textbox", name="Password")
    login_button = page.get_by_role("button", name="Login")

    if username_input.count() and password_input.count():
        username_input.fill(username)
        password_input.fill(password)
        login_button.click()

    expect(page.get_by_role("heading", name="Trading Platform - Login")).not_to_be_visible(
        timeout=15000
    )
    expect(page.get_by_role("heading", name="Dashboard")).to_be_visible(timeout=15000)
    return page
