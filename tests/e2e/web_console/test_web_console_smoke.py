"""Playwright end-to-end smoke tests for the web console."""

from __future__ import annotations

import os

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e


def _flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


FEATURE_MANUAL_CONTROLS = _flag("FEATURE_MANUAL_CONTROLS", False)
FEATURE_CIRCUIT_BREAKER = _flag("FEATURE_CIRCUIT_BREAKER", False)
FEATURE_STRATEGY_COMPARISON = _flag("FEATURE_STRATEGY_COMPARISON", False)
FEATURE_BACKTEST_MANAGER = _flag("FEATURE_BACKTEST_MANAGER", False)
FEATURE_ALERTS = _flag("FEATURE_ALERTS", False)
FEATURE_HEALTH_MONITOR = _flag("FEATURE_HEALTH_MONITOR", False)
FEATURE_TRADE_JOURNAL = _flag("FEATURE_TRADE_JOURNAL", False)
FEATURE_RISK_DASHBOARD = _flag("FEATURE_RISK_DASHBOARD", False)


def _assert_heading(page: Page, heading: str) -> None:
    expect(page.get_by_role("heading", name=heading)).to_be_visible(timeout=10000)


def _select_sidebar_radio(page: Page, name: str) -> bool:
    sidebar = page.locator('[data-testid="stSidebar"]')
    more_link = sidebar.locator("text=/^View \\d+ more$/")
    if more_link.count():
        more_link.first.click()
    label = sidebar.locator(f"text=/^{name}$/")
    if label.count():
        label.first.click()
        return True
    label = sidebar.get_by_text(name, exact=True)
    if label.count():
        label.first.click()
        return True
    label = sidebar.get_by_text(name.lower(), exact=True)
    if label.count():
        label.first.click()
        return True
    return False


def _open_streamlit_sidebar_page(page: Page, labels: list[str]) -> bool:
    sidebar = page.locator('[data-testid="stSidebar"]')
    more_link = sidebar.locator("text=/^View \\d+ more$/")
    if more_link.count():
        more_link.first.click()
    for label in labels:
        link = sidebar.get_by_role("link", name=label)
        if link.count():
            link.click()
            return True
        text_target = sidebar.get_by_text(label, exact=True)
        if text_target.count():
            text_target.click()
            return True
        text_target = sidebar.get_by_text(label.lower(), exact=True)
        if text_target.count():
            text_target.click()
            return True
    return False


def test_core_navigation_pages(logged_in_page: Page) -> None:
    page = logged_in_page

    core_pages = [
        ("Dashboard", "Dashboard", True),
        ("Manual Order Entry", "Manual Order Entry", True),
        ("Kill Switch", "Emergency Kill Switch", True),
        ("Audit Log", "Audit Log", True),
        ("User Management", "User Management", True),
        ("Admin Dashboard", "Admin Dashboard", True),
    ]

    optional_pages = [
        ("Manual Trade Controls", "Manual Trade Controls", FEATURE_MANUAL_CONTROLS),
        ("Circuit Breaker", "Circuit Breaker Dashboard", FEATURE_CIRCUIT_BREAKER),
        ("Strategy Comparison", "Strategy Comparison", FEATURE_STRATEGY_COMPARISON),
        ("Backtest Manager", "Backtest Manager", FEATURE_BACKTEST_MANAGER),
        ("Alerts", "Alert Configuration", FEATURE_ALERTS),
        ("System Health", "System Health Monitor", FEATURE_HEALTH_MONITOR),
    ]

    for name, heading, should_exist in core_pages + optional_pages:
        found = _select_sidebar_radio(page, name)
        if should_exist and not found:
            pytest.fail(f"Expected sidebar page '{name}' not found")
        if not found:
            continue
        _assert_heading(page, heading)


def test_streamlit_multipage_panels(logged_in_page: Page) -> None:
    page = logged_in_page

    multipage = [
        ("Performance", "Performance Dashboard", True),
        ("Risk Analytics Dashboard", "Risk Analytics Dashboard", FEATURE_RISK_DASHBOARD),
        ("Trade Journal", "Trade Journal", FEATURE_TRADE_JOURNAL),
    ]

    for label, heading, should_exist in multipage:
        opened = _open_streamlit_sidebar_page(page, [label, heading])
        if should_exist and not opened:
            sidebar_text = page.locator('[data-testid="stSidebar"]').inner_text()
            pytest.fail(
                f"Expected Streamlit sidebar page '{label}' not found. Sidebar text:\n{sidebar_text}"
            )
        if not opened:
            continue
        _assert_heading(page, heading)
