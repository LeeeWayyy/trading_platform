"""Shared workspace tab identifiers used across auth and page layers."""

from __future__ import annotations

TAB_DISCOVER = "discover"
TAB_VALIDATE = "validate"
TAB_PROMOTE = "promote"

VALID_RESEARCH_TABS = frozenset({TAB_DISCOVER, TAB_VALIDATE, TAB_PROMOTE})

BACKTEST_TAB_NEW = "new"
BACKTEST_TAB_RUNNING = "running"
BACKTEST_TAB_RESULTS = "results"

VALID_BACKTEST_TABS = {
    BACKTEST_TAB_NEW,
    BACKTEST_TAB_RUNNING,
    BACKTEST_TAB_RESULTS,
}

BACKTEST_TAB_ALIASES = {
    "compare": BACKTEST_TAB_RESULTS,
}


def normalize_backtest_tab(
    value: str | None,
    *,
    default: str | None = None,
) -> str | None:
    """Normalize backtest tab aliases and validate membership."""
    normalized = str(value or "").strip().lower()
    normalized = BACKTEST_TAB_ALIASES.get(normalized, normalized)
    if normalized in VALID_BACKTEST_TABS:
        return normalized
    return default


__all__ = [
    "TAB_DISCOVER",
    "TAB_VALIDATE",
    "TAB_PROMOTE",
    "VALID_RESEARCH_TABS",
    "BACKTEST_TAB_NEW",
    "BACKTEST_TAB_RUNNING",
    "BACKTEST_TAB_RESULTS",
    "VALID_BACKTEST_TABS",
    "BACKTEST_TAB_ALIASES",
    "normalize_backtest_tab",
]
