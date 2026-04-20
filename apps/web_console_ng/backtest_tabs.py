"""Backtest tab compatibility exports."""

from __future__ import annotations

from apps.web_console_ng.core.workspace_tabs import (
    BACKTEST_TAB_ALIASES,
    BACKTEST_TAB_NEW,
    BACKTEST_TAB_RESULTS,
    BACKTEST_TAB_RUNNING,
    VALID_BACKTEST_TABS,
    normalize_backtest_tab,
)

__all__ = [
    "BACKTEST_TAB_NEW",
    "BACKTEST_TAB_RUNNING",
    "BACKTEST_TAB_RESULTS",
    "BACKTEST_TAB_ALIASES",
    "VALID_BACKTEST_TABS",
    "normalize_backtest_tab",
]
