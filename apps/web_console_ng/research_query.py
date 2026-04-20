"""Shared research-query sanitization helpers."""

from __future__ import annotations

from apps.web_console_ng.core.workspace_tabs import (
    TAB_VALIDATE,
    VALID_RESEARCH_TABS,
    normalize_backtest_tab,
)

RESEARCH_ALLOWED_QUERY_KEYS = frozenset(
    {
        "tab",
        "backtest_tab",
        "signal_id",
        "source",
        "model_id",
        "strategy_id",
        "backtest_job_id",
        "id",
        "view",
    }
)

RESEARCH_VALIDATE_ONLY_QUERY_KEYS = frozenset({"backtest_tab", "backtest_job_id", "id"})


def normalize_research_tab(value: str | None, *, default: str | None = None) -> str | None:
    """Normalize research tab string and validate membership."""
    normalized = str(value or "").strip().lower()
    if normalized in VALID_RESEARCH_TABS:
        return normalized
    return default


def sanitize_research_query_items(
    query_items: list[tuple[str, str]],
    *,
    selected_tab: str | None,
    include_tab: bool = False,
) -> list[tuple[str, str]]:
    """Filter/normalize research query items for the given effective tab."""
    sanitized_items: list[tuple[str, str]] = []
    tab_emitted = False
    backtest_tab_emitted = False
    for key, value in query_items:
        if key not in RESEARCH_ALLOWED_QUERY_KEYS:
            continue
        if key == "tab":
            if not include_tab or selected_tab is None or tab_emitted:
                continue
            sanitized_items.append(("tab", selected_tab))
            tab_emitted = True
            continue
        if key in RESEARCH_VALIDATE_ONLY_QUERY_KEYS and selected_tab != TAB_VALIDATE:
            continue
        if key == "backtest_tab":
            if backtest_tab_emitted:
                continue
            normalized_backtest_tab = normalize_backtest_tab(value, default=None)
            if normalized_backtest_tab is None:
                continue
            sanitized_items.append(("backtest_tab", normalized_backtest_tab))
            backtest_tab_emitted = True
            continue
        sanitized_items.append((key, value))
    return sanitized_items


__all__ = [
    "RESEARCH_ALLOWED_QUERY_KEYS",
    "RESEARCH_VALIDATE_ONLY_QUERY_KEYS",
    "normalize_research_tab",
    "sanitize_research_query_items",
]
