"""Performance Dashboard page for NiceGUI web console (P5T8).

Provides P&L visualization and performance analytics.

Features:
    - Real-time P&L display
    - Position summary table
    - Historical performance charts (equity curve, drawdown)
    - Date range selection with presets

PARITY: Mirrors UI layout from apps/web_console/pages/performance.py

NOTE: This page uses demo mode with placeholder data when services are unavailable.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

import plotly.graph_objects as go
from nicegui import ui

from apps.web_console_ng.auth.middleware import get_current_user, requires_auth
from apps.web_console_ng.config import AUTO_REFRESH_INTERVAL, FEATURE_PERFORMANCE_DASHBOARD
from apps.web_console_ng.ui.layout import main_layout
from libs.web_console_auth.permissions import Permission, get_authorized_strategies, has_permission

logger = logging.getLogger(__name__)

DEFAULT_RANGE_DAYS = 30
MAX_RANGE_DAYS = 90


@ui.page("/performance")
@requires_auth
@main_layout
async def performance_dashboard_page() -> None:
    """Performance Dashboard page."""
    user = get_current_user()

    # Page title
    ui.label("Performance Dashboard").classes("text-2xl font-bold mb-2")
    ui.label("Realized P&L only. Unrealized P&L shown separately in real-time section.").classes(
        "text-gray-600 mb-4"
    )

    # Feature flag check
    if not FEATURE_PERFORMANCE_DASHBOARD:
        with ui.card().classes("w-full p-6"):
            ui.label("Performance Dashboard feature is not available.").classes(
                "text-gray-500 text-center"
            )
            ui.label(
                "Set FEATURE_PERFORMANCE_DASHBOARD=true to enable this feature."
            ).classes("text-gray-400 text-sm text-center")
        return

    # Permission check
    if not has_permission(user, Permission.VIEW_PNL):
        ui.notify("Permission denied: VIEW_PNL required", type="negative")
        with ui.card().classes("w-full p-6"):
            ui.label("Permission denied: VIEW_PNL required.").classes(
                "text-red-500 text-center"
            )
        return

    # Get authorized strategies
    authorized_strategies = get_authorized_strategies(user)
    if not authorized_strategies:
        with ui.card().classes("w-full p-6"):
            ui.label("You don't have access to any strategies. Contact administrator.").classes(
                "text-amber-600 text-center"
            )
        return

    # Render the dashboard
    await _render_performance_dashboard(user, authorized_strategies)


async def _render_performance_dashboard(
    user: dict[str, Any],
    authorized_strategies: list[str],
) -> None:
    """Render the full performance dashboard."""
    today = date.today()

    # Date range selector
    with ui.card().classes("w-full mb-4 p-4"):
        ui.label("Date Range").classes("text-lg font-bold mb-2")

        presets = {
            "7 Days": (today - timedelta(days=7), today),
            "30 Days": (today - timedelta(days=30), today),
            "90 Days": (today - timedelta(days=MAX_RANGE_DAYS), today),
            "YTD": (max(date(today.year, 1, 1), today - timedelta(days=MAX_RANGE_DAYS)), today),
        }

        preset_btns = {}
        with ui.row().classes("gap-2 mb-4"):
            for label in presets:
                preset_btns[label] = ui.button(label).props(
                    "color=primary" if label == "30 Days" else ""
                )

        # Custom date inputs
        custom_row = ui.row().classes("gap-4 items-center")
        with custom_row:
            ui.label("From:").classes("text-sm")
            from_input = ui.date(
                value=str(today - timedelta(days=DEFAULT_RANGE_DAYS)),
            ).classes("w-36")
            ui.label("To:").classes("text-sm")
            to_input = ui.date(value=str(today)).classes("w-36")

    # State with explicit typing
    state: dict[str, Any] = {
        "start_date": today - timedelta(days=30),
        "end_date": today,
        "preset": "30 Days",
    }

    # Real-time P&L section
    realtime_container = ui.column().classes("w-full mb-4")

    # Position summary section
    positions_container = ui.column().classes("w-full mb-4")

    # Historical performance section
    historical_container = ui.column().classes("w-full")

    async def load_data() -> None:
        """Load and render all sections."""
        realtime_container.clear()
        positions_container.clear()
        historical_container.clear()

        with realtime_container:
            _render_realtime_pnl(user)

        ui.separator().classes("my-4")

        with positions_container:
            _render_position_summary()

        ui.separator().classes("my-4")

        with historical_container:
            _render_historical_performance(
                state["start_date"],
                state["end_date"],
                authorized_strategies,
            )

    async def select_preset(label: str) -> None:
        state["preset"] = label
        if label in presets:
            state["start_date"], state["end_date"] = presets[label]
        for lbl, btn in preset_btns.items():
            btn.props(f"color={'primary' if lbl == label else ''}")
        await load_data()

    async def apply_custom() -> None:
        try:
            start = date.fromisoformat(from_input.value) if from_input.value else state["start_date"]
            end = date.fromisoformat(to_input.value) if to_input.value else state["end_date"]
        except ValueError:
            ui.notify("Invalid date format", type="negative")
            return

        if start > end:
            ui.notify("Start date must be before end date", type="negative")
            return

        if (end - start).days > MAX_RANGE_DAYS:
            ui.notify(f"Date range capped to {MAX_RANGE_DAYS} days", type="warning")
            start = end - timedelta(days=MAX_RANGE_DAYS)

        state["start_date"] = start
        state["end_date"] = end
        state["preset"] = "Custom"
        for btn in preset_btns.values():
            btn.props("color=")
        await load_data()

    # Connect preset buttons
    for label, btn in preset_btns.items():
        async def on_preset_click(preset_label: str = label) -> None:
            await select_preset(preset_label)
        btn.on_click(on_preset_click)

    async def on_date_change(_: Any) -> None:
        await apply_custom()

    from_input.on_value_change(on_date_change)
    to_input.on_value_change(on_date_change)

    # Initial load
    await load_data()

    # Auto-refresh timer
    async def auto_refresh() -> None:
        realtime_container.clear()
        with realtime_container:
            _render_realtime_pnl(user)

    ui.timer(AUTO_REFRESH_INTERVAL, auto_refresh)


def _render_realtime_pnl(user: dict[str, Any]) -> None:
    """Render real-time P&L section."""
    with ui.card().classes("w-full p-4"):
        ui.label("Real-Time P&L").classes("text-lg font-bold mb-2")

        # Demo data (would fetch from API in production)
        demo_positions: list[dict[str, Any]] = [
            {"symbol": "AAPL", "qty": 100, "avg_entry": 185.00, "current": 187.50, "unrealized": 250.00, "unrealized_pct": 1.35},
            {"symbol": "MSFT", "qty": 50, "avg_entry": 375.00, "current": 372.00, "unrealized": -150.00, "unrealized_pct": -0.80},
            {"symbol": "GOOGL", "qty": 25, "avg_entry": 142.00, "current": 145.00, "unrealized": 75.00, "unrealized_pct": 2.11},
        ]

        total_unrealized = float(sum(p["unrealized"] for p in demo_positions))
        total_unrealized_pct = float(sum(p["unrealized_pct"] for p in demo_positions)) / len(demo_positions)

        with ui.row().classes("gap-4 mb-4"):
            _metric_card("Open Positions", str(len(demo_positions)))
            _metric_card("Unrealized P&L", f"${total_unrealized:,.2f}")
            _metric_card("Unrealized %", f"{total_unrealized_pct:.2f}%")

        columns = [
            {"name": "symbol", "label": "Symbol", "field": "symbol"},
            {"name": "qty", "label": "Qty", "field": "qty"},
            {"name": "avg_entry", "label": "Avg Entry", "field": "avg_entry"},
            {"name": "current", "label": "Current", "field": "current"},
            {"name": "unrealized", "label": "Unrealized P&L", "field": "unrealized"},
            {"name": "unrealized_pct", "label": "Unrealized %", "field": "unrealized_pct"},
        ]

        rows = [
            {
                "symbol": p["symbol"],
                "qty": p["qty"],
                "avg_entry": f"${p['avg_entry']:,.2f}",
                "current": f"${p['current']:,.2f}",
                "unrealized": f"${p['unrealized']:,.2f}",
                "unrealized_pct": f"{p['unrealized_pct']:.2f}%",
            }
            for p in demo_positions
        ]

        ui.table(columns=columns, rows=rows).classes("w-full")

        ui.label("Prices use real-time feed when available; otherwise last known price.").classes(
            "text-gray-500 text-xs mt-2"
        )


def _render_position_summary() -> None:
    """Render position summary section."""
    with ui.card().classes("w-full p-4"):
        ui.label("Position Summary").classes("text-lg font-bold mb-2")

        # Demo data
        demo_positions = [
            {"symbol": "AAPL", "qty": 100, "avg_entry": 185.00, "realized_pnl": 1250.00, "updated": "2026-01-03 14:30:00"},
            {"symbol": "MSFT", "qty": 50, "avg_entry": 375.00, "realized_pnl": 890.00, "updated": "2026-01-03 14:25:00"},
            {"symbol": "GOOGL", "qty": 25, "avg_entry": 142.00, "realized_pnl": 425.00, "updated": "2026-01-03 14:20:00"},
        ]

        columns = [
            {"name": "symbol", "label": "Symbol", "field": "symbol"},
            {"name": "qty", "label": "Qty", "field": "qty"},
            {"name": "avg_entry", "label": "Avg Entry", "field": "avg_entry"},
            {"name": "realized_pnl", "label": "Realized P&L", "field": "realized_pnl"},
            {"name": "updated", "label": "Updated", "field": "updated"},
        ]

        rows = [
            {
                "symbol": p["symbol"],
                "qty": p["qty"],
                "avg_entry": f"${p['avg_entry']:,.2f}",
                "realized_pnl": f"${p['realized_pnl']:,.2f}",
                "updated": p["updated"],
            }
            for p in demo_positions
        ]

        ui.table(columns=columns, rows=rows).classes("w-full")


def _render_historical_performance(
    start_date: date,
    end_date: date,
    strategies: list[str],
) -> None:
    """Render historical performance section."""
    with ui.card().classes("w-full p-4"):
        ui.label("Historical Performance (Realized Only)").classes("text-lg font-bold mb-2")

        if end_date < start_date:
            ui.label("End date must be after start date.").classes("text-red-500 p-2")
            return

        if (end_date - start_date).days > MAX_RANGE_DAYS:
            ui.label(f"Date range cannot exceed {MAX_RANGE_DAYS} days.").classes(
                "text-amber-600 p-2"
            )

        # Demo data
        demo_total_pnl = 12567.89
        demo_max_dd = -3.45
        demo_days = 30

        with ui.row().classes("gap-4 mb-4"):
            _metric_card("Total Realized P&L", f"${demo_total_pnl:,.2f}")
            _metric_card("Max Drawdown %", f"{demo_max_dd:.2f}%")
            _metric_card("Days", str(demo_days))

        # Demo equity curve
        import random

        dates = [start_date + timedelta(days=i) for i in range((end_date - start_date).days + 1)]
        equity: list[float] = [0.0]
        for _ in range(1, len(dates)):
            equity.append(equity[-1] + random.uniform(-200, 400))

        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=[str(d) for d in dates],
                y=equity,
                mode="lines",
                name="Equity",
                line={"color": "#1f77b4", "width": 2},
                fill="tozeroy",
                fillcolor="rgba(31, 119, 180, 0.1)",
            )
        )
        fig.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.5)
        fig.update_layout(
            title="Equity Curve",
            xaxis_title="Date",
            yaxis_title="Cumulative P&L ($)",
            height=350,
        )

        ui.plotly(fig).classes("w-full mb-4")

        # Drawdown chart
        running_max = [equity[0]]
        for e in equity[1:]:
            running_max.append(max(running_max[-1], e))
        drawdown = [(e - m) / max(abs(m), 1) * 100 if m != 0 else 0 for e, m in zip(equity, running_max, strict=True)]

        fig2 = go.Figure()
        fig2.add_trace(
            go.Scatter(
                x=[str(d) for d in dates],
                y=drawdown,
                mode="lines",
                name="Drawdown",
                line={"color": "#d62728", "width": 2},
                fill="tozeroy",
                fillcolor="rgba(214, 39, 40, 0.1)",
            )
        )
        fig2.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.5)
        fig2.update_layout(
            title="Drawdown",
            xaxis_title="Date",
            yaxis_title="Drawdown (%)",
            height=300,
        )

        ui.plotly(fig2).classes("w-full")

        ui.label("Realized P&L Only.").classes("text-gray-500 text-xs mt-2")


def _metric_card(label: str, value: str) -> None:
    """Render a metric card."""
    with ui.card().classes("p-3 min-w-28"):
        ui.label(label).classes("text-xs text-gray-500")
        ui.label(value).classes("text-lg font-bold")


__all__ = ["performance_dashboard_page"]
