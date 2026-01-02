import os
import socket

# CRITICAL: Set DEBUG mode BEFORE importing apps.web_console_ng modules.
# The config module validates AUTH_TYPE at import time and requires explicit
# configuration in production (DEBUG=false). Tests must run in DEBUG mode.
os.environ.setdefault("WEB_CONSOLE_NG_DEBUG", "true")

import httpx
import pytest

from apps.web_console_ng.core.client import AsyncTradingClient

# Selenium fixtures are optional - only available when selenium is installed
# This allows CI to run unit tests without selenium dependency
try:
    from nicegui.testing import Screen
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service as ChromeService
    from webdriver_manager.chrome import ChromeDriverManager

    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False


@pytest.fixture(scope="module")
def selenium_driver():
    if not SELENIUM_AVAILABLE:
        pytest.skip("selenium not installed")

    options = webdriver.ChromeOptions()
    options.add_argument("headless")
    options.add_argument("no-sandbox")
    options.add_argument("disable-dev-shm-usage")

    # Point to the playwright-installed chromium binary
    chromium_path = os.path.expanduser(
        "~/Library/Caches/ms-playwright/chromium-1200/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
    )

    if os.path.exists(chromium_path):
        options.binary_location = chromium_path

    # Use ChromeDriverManager to automatically install/locate the driver
    try:
        driver_path = ChromeDriverManager(driver_version="143.0.7499.4").install()
    except Exception:
        driver_path = ChromeDriverManager().install()

    service = ChromeService(driver_path)
    driver = webdriver.Chrome(service=service, options=options)
    driver.implicitly_wait(2)
    yield driver
    driver.quit()


class _DummyCapLog:
    def __init__(self):
        self.records = []

    def clear(self) -> None:
        self.records.clear()


@pytest.fixture(scope="module")
def screen(selenium_driver):
    if not SELENIUM_AVAILABLE:
        pytest.skip("selenium not installed")

    # Initialize Screen without context manager
    screen = Screen(selenium_driver, _DummyCapLog())

    # Inject storage_secret for app.storage support
    # This is critical because our pages access app.storage.user
    screen.ui_run_kwargs["storage_secret"] = "test_secret_key_12345"
    # Use a free port per module to avoid clashes between UI test modules.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    screen.ui_run_kwargs["port"] = port
    screen.url = f"http://localhost:{port}"

    screen.start_server()
    yield screen
    screen.stop_server()


@pytest.fixture()
async def trading_client():
    """Fixture providing an AsyncTradingClient with a test httpx client."""
    client = AsyncTradingClient.get()
    # Replace the http_client with a test client pointing to testserver
    client._http_client = httpx.AsyncClient(base_url="http://testserver")
    yield client
    await client._http_client.aclose()
    client._http_client = None
