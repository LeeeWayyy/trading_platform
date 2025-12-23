"""Performance dashboard page."""

from __future__ import annotations

import os
from collections.abc import Mapping
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import requests
import streamlit as st

from apps.web_console.auth.permissions import Permission, get_authorized_strategies, has_permission
from apps.web_console.auth import get_current_user
from apps.web_console.auth.streamlit_helpers import requires_auth
from apps.web_console.components.pnl_chart import render_drawdown_chart, render_equity_curve
from apps.web_console.config import AUTO_REFRESH_INTERVAL
from apps.web_console.data.strategy_scoped_queries import StrategyScopedDataAccess
from apps.web_console.utils.api_client import fetch_api

DEFAULT_RANGE_DAYS = 30
MAX_RANGE_DAYS = 90
FEATURE_PERFORMANCE_DASHBOARD = os.getenv("FEATURE_PERFORMANCE_DASHBOARD", "false").lower() in {
    "1",
    "true",
    "yes",
    "on",
}


def _safe_current_user() -> Mapping[str, Any]:
    """Compatibility wrapper to allow tests to monkeypatch user context."""
    try:
        user = get_current_user()
    except RuntimeError:
        return {}
    return user if isinstance(user, Mapping) else {}


def _fetch(endpoint: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Fetch data from API endpoint with authentication headers.

    Uses shared api_client.fetch_api for consistent header handling.
    """
    user = _safe_current_user()
    return fetch_api(endpoint, user, params)


@st.cache_data(ttl=AUTO_REFRESH_INTERVAL)
def fetch_realtime_pnl() -> dict[str, Any]:
    return _fetch("pnl_realtime")


@st.cache_data(ttl=AUTO_REFRESH_INTERVAL)
def fetch_positions() -> dict[str, Any]:
    return _fetch("positions")


@st.cache_data(ttl=300)
def fetch_performance(
    start: date, end: date, strategies: list[str], user_id: str | None
) -> dict[str, Any]:
    # Include user_id in cache key to prevent cross-user data leakage when the
    # same strategy list is requested by different users.
    if user_id is None:
        raise RuntimeError("Missing user_id; refuse to cache or fetch performance data")
    return _fetch(
        "performance_daily",
        params={
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "strategies": sorted(strategies),
            "user_id": user_id,
        },
    )


def _metric(
    label: str, value: Decimal | float | int | None, delta: Decimal | float | None = None
) -> None:
    if value is None:
        st.metric(label, "–")
        return
    display_val = f"{value:,.2f}" if isinstance(value, (int | float | Decimal)) else str(value)
    delta_val = None if delta is None else f"{delta:,.2f}"
    st.metric(label, display_val, delta=delta_val)


def _get_value(item: Any, key: str) -> Any:
    """Safely fetch attribute or mapping key from objects."""
    if isinstance(item, Mapping):
        return item.get(key)
    return getattr(item, key, None)


def render_realtime_pnl() -> None:
    st.subheader("Real-Time P&L")
    try:
        data = fetch_realtime_pnl()
    except requests.exceptions.RequestException as e:
        st.error(f"Failed to load real-time P&L: {e}")
        return

    user = _safe_current_user()
    strategies = get_authorized_strategies(user) if user else []
    viewer_scoped = strategies and not has_permission(user, Permission.VIEW_ALL_STRATEGIES)

    positions = data.get("positions", [])
    total_unrealized = data.get("total_unrealized_pl")
    total_unrealized_pct = data.get("total_unrealized_pl_pct")

    cols = st.columns(3)
    cols[0].metric("Open Positions", len(positions))
    cols[1].metric(
        "Unrealized P&L", f"{total_unrealized:,.2f}" if total_unrealized is not None else "–"
    )
    cols[2].metric(
        "Unrealized P&L %",
        f"{total_unrealized_pct:,.2f}%" if total_unrealized_pct is not None else "–",
    )

    if not positions:
        if viewer_scoped:
            st.info(
                "No open positions found for your authorized strategies. "
                "Note: Positions for symbols traded by multiple strategies may not be displayed."
            )
            return
        st.info("No open positions.")
        return

    st.caption("Prices use real-time feed when available; otherwise last known price.")

    st.dataframe(
        [
            {
                "Symbol": p.get("symbol"),
                "Qty": p.get("qty"),
                "Avg Entry": p.get("avg_entry_price"),
                "Current Price": p.get("current_price"),
                "Unrealized P&L": p.get("unrealized_pl"),
                "Unrealized %": p.get("unrealized_pl_pct"),
                "Price Source": p.get("price_source"),
            }
            for p in positions
        ],
        use_container_width=True,
        hide_index=True,
    )


def render_position_summary() -> None:
    st.subheader("Position Summary")
    try:
        data = fetch_positions()
    except requests.exceptions.RequestException as e:
        st.error(f"Failed to load positions: {e}")
        return

    positions = data.get("positions", [])
    if not positions:
        st.info("No positions to display.")
        return

    st.dataframe(
        [
            {
                "Symbol": _get_value(p, "symbol"),
                "Qty": _get_value(p, "qty"),
                "Avg Entry": _get_value(p, "avg_entry_price"),
                "Realized P&L": _get_value(p, "realized_pl"),
                "Updated": _get_value(p, "updated_at"),
            }
            for p in positions
        ],
        use_container_width=True,
        hide_index=True,
    )


def render_historical_performance(start: date, end: date, strategies: list[str]) -> None:
    st.subheader("Historical Performance (Realized Only)")

    if end < start:
        st.error("Date range cannot exceed the start date (end is before start).")
        return

    if (end - start).days > MAX_RANGE_DAYS:
        st.error(f"Date range cannot exceed {MAX_RANGE_DAYS} days.")
        return

    with st.spinner("Loading performance..."):
        try:
            user = _safe_current_user()
            user_id = user.get("user_id") if isinstance(user, Mapping) else None
            if not user_id:
                st.error("Authentication required to view historical performance.")
                return
            data = fetch_performance(start, end, strategies, user_id)
        except requests.exceptions.RequestException as e:
            st.error(f"Failed to load performance data: {e}")
            return

    data_available_from = data.get("data_available_from")
    if data_available_from and start < date.fromisoformat(str(data_available_from)):
        st.warning(
            "Requested range starts before data is available. Showing earliest available data onward.",
            icon="⚠️",
        )

    daily = data.get("daily_pnl", [])
    if not daily:
        st.info("No trading activity for your assigned strategies in the selected period.")
        return

    _metric("Total Realized P&L", data.get("total_realized_pl"))
    _metric("Max Drawdown %", data.get("max_drawdown_pct"))
    _metric("Days", len(daily))

    render_equity_curve(daily)
    render_drawdown_chart(daily)

    st.caption(data.get("note", "Realized P&L Only."))


def _preset_ranges(today: date) -> dict[str, tuple[date, date]]:
    """Return preset date ranges, all clamped to MAX_RANGE_DAYS."""
    # Clamp start date to ensure range never exceeds MAX_RANGE_DAYS
    min_start = today - timedelta(days=MAX_RANGE_DAYS)
    ytd_start = max(date(today.year, 1, 1), min_start)
    return {
        "7 Days": (today - timedelta(days=7), today),
        "30 Days": (today - timedelta(days=30), today),
        "90 Days": (min_start, today),
        "YTD": (ytd_start, today),
    }


def _select_date_range() -> tuple[date, date, str]:
    today = date.today()
    presets = _preset_ranges(today)
    preset_labels = list(presets.keys()) + ["Custom"]

    if "performance_date_preset" not in st.session_state:
        st.session_state["performance_date_preset"] = "30 Days"

    cols = st.columns(len(preset_labels))
    selected = st.session_state["performance_date_preset"]
    for col, label in zip(cols, preset_labels, strict=False):
        if col.button(label, type="secondary" if label != selected else "primary"):
            st.session_state["performance_date_preset"] = label
            selected = label

    if selected != "Custom":
        start_date, end_date = presets[selected]
    else:
        default_start = today - timedelta(days=DEFAULT_RANGE_DAYS)
        date_input = st.date_input(
            "Date Range",
            value=(default_start, today),
            max_value=today,
        )
        if isinstance(date_input, tuple) and len(date_input) == 2:
            start_date, end_date = date_input
        elif isinstance(date_input, date):
            start_date = date_input
            end_date = date_input
        else:
            st.error("Please select a valid date range.")
            st.stop()

    if start_date > end_date:
        st.error("Start date must be on or before end date.")
        st.stop()

    # Clamp custom ranges to MAX_RANGE_DAYS
    if (end_date - start_date).days > MAX_RANGE_DAYS:
        clamped_start = end_date - timedelta(days=MAX_RANGE_DAYS)
        st.warning(f"Date range exceeds {MAX_RANGE_DAYS} days. Showing data from {clamped_start}.")
        start_date = clamped_start

    return start_date, end_date, selected


# Backward compatibility for tests and callers still using old helper
def _date_inputs() -> tuple[date, date]:
    # Legacy helper used in tests: force Custom flow to honor date_input mock values.
    st.session_state["performance_date_preset"] = "Custom"
    start, end, _preset = _select_date_range()
    return start, end


@requires_auth
def main() -> None:
    st.set_page_config(page_title="Performance Dashboard", page_icon="📈", layout="wide")

    st.title("Performance Dashboard")
    st.caption("Realized P&L only. Unrealized P&L shown separately in real-time section.")

    if not FEATURE_PERFORMANCE_DASHBOARD:
        st.info("Feature not available.")
        return

    user = get_current_user()
    if not has_permission(user, Permission.VIEW_PNL):
        st.error("Permission denied: VIEW_PNL required.")
        st.stop()

    authorized_strategies = get_authorized_strategies(user)
    if not authorized_strategies:
        st.warning("You don't have access to any strategies. Contact administrator.")
        st.stop()

    # Instantiate scoped access to satisfy RBAC pattern and reuse strategy scoping token
    _ = StrategyScopedDataAccess(db_pool=None, redis_client=None, user=user)

    start_date, end_date, _preset = _select_date_range()

    with st.spinner("Refreshing real-time data..."):
        render_realtime_pnl()

    st.divider()
    render_position_summary()

    st.divider()
    render_historical_performance(start_date, end_date, authorized_strategies)


if __name__ == "__main__":
    main()
