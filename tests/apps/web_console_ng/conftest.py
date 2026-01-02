import importlib
import os
import socket
from collections.abc import Generator
from copy import copy

import httpx
import pytest

# CRITICAL: Set DEBUG mode BEFORE importing apps.web_console_ng modules.
# The config module validates AUTH_TYPE at import time and requires explicit
# configuration in production (DEBUG=false). Tests must run in DEBUG mode.
os.environ.setdefault("WEB_CONSOLE_NG_DEBUG", "true")

from apps.web_console_ng.core.client import AsyncTradingClient

# Selenium fixtures are optional - only available when selenium is installed
# This allows CI to run unit tests without selenium dependency
try:
    from nicegui import Client, app, binding, core, run, ui
    from nicegui.page import page
    from nicegui.testing import Screen
    from nicegui.testing.general_fixtures import prepare_simulation
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service as ChromeService
    from starlette.routing import Route
    from webdriver_manager.chrome import ChromeDriverManager

    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False


@pytest.fixture()
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


def _find_all_subclasses(cls: type) -> list[type]:
    subclasses: list[type] = []
    for subclass in cls.__subclasses__():
        subclasses.append(subclass)
        subclasses.extend(_find_all_subclasses(subclass))
    return subclasses


@pytest.fixture()
def nicegui_reset_globals() -> Generator[None, None, None]:
    if not SELENIUM_AVAILABLE:
        pytest.skip("selenium not installed")

    for route in app.routes:
        if isinstance(route, Route) and route.path.startswith("/_nicegui/auto/static/"):
            app.remove_route(route.path)
    for path in {"/"}.union(Client.page_routes.values()):
        app.remove_route(path)
    app.openapi_schema = None
    app.middleware_stack = None
    app.user_middleware.clear()
    app.urls.clear()
    core.air = None
    for route in app.routes:
        if isinstance(route, Route) and route.path.endswith("/favicon.ico"):
            app.routes.remove(route)
    importlib.reload(core)
    importlib.reload(run)

    element_types: list[type[ui.element]] = [ui.element, *_find_all_subclasses(ui.element)]
    default_classes = {
        t: copy(t._default_classes) for t in element_types
    }  # pylint: disable=protected-access
    default_styles = {
        t: copy(t._default_style) for t in element_types
    }  # pylint: disable=protected-access
    default_props = {
        t: copy(t._default_props) for t in element_types
    }  # pylint: disable=protected-access

    Client.instances.clear()
    Client.page_routes.clear()
    app.reset()
    Client.auto_index_client = Client(
        page("/"), request=None
    ).__enter__()  # pylint: disable=unnecessary-dunder-call
    app.get("/")(Client.auto_index_client.build_response)
    binding.reset()

    yield

    app.reset()
    for t in element_types:
        t._default_classes = default_classes[t]  # pylint: disable=protected-access
        t._default_style = default_styles[t]  # pylint: disable=protected-access
        t._default_props = default_props[t]  # pylint: disable=protected-access


@pytest.fixture()
def screen(nicegui_reset_globals, selenium_driver, request):  # noqa: F811
    if not SELENIUM_AVAILABLE:
        pytest.skip("selenium not installed")

    prepare_simulation(request)

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

    # Start the server before yielding - required for screen.open() to connect
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
