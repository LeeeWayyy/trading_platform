import os

import pytest
from nicegui.testing import Screen
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from webdriver_manager.chrome import ChromeDriverManager


@pytest.fixture()
def selenium_driver():
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


@pytest.fixture()
def screen(selenium_driver, caplog):
    # Initialize Screen without context manager
    screen = Screen(selenium_driver, caplog)

    # Inject storage_secret for app.storage support
    # This is critical because our pages access app.storage.user
    screen.ui_run_kwargs["storage_secret"] = "test_secret_key_12345"

    screen.start_server()
    yield screen
    screen.stop_server()
