"""Shared Backtest tab identifiers for Research/Validate surfaces."""

from __future__ import annotations

BACKTEST_TAB_NEW = "new"
BACKTEST_TAB_RUNNING = "running"
BACKTEST_TAB_RESULTS = "results"

VALID_BACKTEST_TABS = {
    BACKTEST_TAB_NEW,
    BACKTEST_TAB_RUNNING,
    BACKTEST_TAB_RESULTS,
}

__all__ = [
    "BACKTEST_TAB_NEW",
    "BACKTEST_TAB_RUNNING",
    "BACKTEST_TAB_RESULTS",
    "VALID_BACKTEST_TABS",
]
