"""Compatibility redirects for retired research page routes."""

from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import urlencode

from nicegui import ui

from apps.web_console_ng.auth.middleware import requires_auth
from apps.web_console_ng.core.request_query import get_query_param_from_raw_query
from apps.web_console_ng.pages.backtest_tabs import (
    BACKTEST_TAB_NEW,
    VALID_BACKTEST_TABS,
)


def _build_backtest_redirect_target(raw_query: bytes | str | None) -> str:
    """Map legacy /backtest query params onto /research Validate tab."""
    target: dict[str, str] = {"tab": "validate"}
    raw_tab = get_query_param_from_raw_query(
        raw_query=raw_query,
        key="tab",
        default=BACKTEST_TAB_NEW,
    )
    normalized_tab = str(raw_tab or BACKTEST_TAB_NEW).strip().lower()
    if normalized_tab in VALID_BACKTEST_TABS:
        target["backtest_tab"] = normalized_tab

    signal_id = get_query_param_from_raw_query(raw_query=raw_query, key="signal_id")
    source = get_query_param_from_raw_query(raw_query=raw_query, key="source")
    if signal_id:
        cleaned_signal = str(signal_id).strip()
        if cleaned_signal:
            target["signal_id"] = cleaned_signal
    if source:
        cleaned_source = str(source).strip()
        if cleaned_source:
            target["source"] = cleaned_source

    return "/research?" + urlencode(target)


def _get_current_request_raw_query() -> bytes | str | None:
    """Extract raw query bytes from current NiceGUI request context."""
    try:
        request = ui.context.client.request
    except Exception:
        return None
    if request is None:
        return None
    scope = getattr(request, "scope", None)
    if isinstance(scope, Mapping):
        raw_query = scope.get("query_string")
        if isinstance(raw_query, bytes | str):
            return raw_query
    return None


@ui.page("/alpha-explorer")
@requires_auth
async def legacy_alpha_explorer_redirect() -> None:
    """Retired route compatibility: Alpha Explorer -> Research/Discover."""
    # Intentional: do not wrap with main_layout for instant compatibility redirect.
    ui.navigate.to("/research?tab=discover")


@ui.page("/backtest")
@requires_auth
async def legacy_backtest_redirect() -> None:
    """Retired route compatibility: Backtest -> Research/Validate."""
    # Intentional: do not wrap with main_layout for instant compatibility redirect.
    ui.navigate.to(_build_backtest_redirect_target(_get_current_request_raw_query() or b""))


@ui.page("/models")
@requires_auth
async def legacy_models_redirect() -> None:
    """Retired route compatibility: Models -> Research/Promote."""
    # Intentional: do not wrap with main_layout for instant compatibility redirect.
    ui.navigate.to("/research?tab=promote")
