"""Plotly charts for performance dashboard."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

import plotly.graph_objects as go  # type: ignore[import-untyped]
import streamlit as st

DailyPnLLike = Mapping[str, Any] | Any


def _get_value(item: DailyPnLLike, key: str) -> Any:
    if isinstance(item, Mapping):
        return item.get(key)
    return getattr(item, key, None)


def _as_float(value: Any) -> float:
    """Convert to float with safe fallback for None/unknown types."""

    if value is None:
        return 0.0
    try:
        return float(cast(float, value))
    except (TypeError, ValueError):
        return 0.0


def _prepare_series(
    daily_pnl: Sequence[DailyPnLLike],
) -> tuple[list[str], list[float], list[float]]:
    dates: list[str] = []
    cumulative: list[float] = []
    drawdowns: list[float] = []

    for entry in daily_pnl:
        dates.append(str(_get_value(entry, "date")))
        cumulative.append(_as_float(_get_value(entry, "cumulative_realized_pl")))
        drawdowns.append(_as_float(_get_value(entry, "drawdown_pct")))

    return dates, cumulative, drawdowns


def render_equity_curve(daily_pnl: Sequence[DailyPnLLike]) -> go.Figure | None:
    """Render cumulative realized P&L line chart."""

    if not daily_pnl:
        st.info("No performance data available for the selected range.")
        return None

    dates, cumulative, _ = _prepare_series(daily_pnl)

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=dates,
            y=cumulative,
            mode="lines+markers",
            name="Cumulative Realized P&L",
            line={"color": "#2E86DE", "width": 3},
            marker={"size": 6},
        )
    )

    fig.update_layout(
        title="Equity Curve (Realized P&L Only)",
        xaxis_title="Date",
        yaxis_title="Cumulative Realized P&L",
        hovermode="x unified",
        margin={"l": 50, "r": 30, "t": 60, "b": 40},
    )

    st.plotly_chart(fig, use_container_width=True, theme="streamlit")
    return fig


def render_drawdown_chart(daily_pnl: Sequence[DailyPnLLike]) -> go.Figure | None:
    """Render drawdown area chart."""

    if not daily_pnl:
        st.info("No drawdown data available for the selected range.")
        return None

    dates, _, drawdowns = _prepare_series(daily_pnl)

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=dates,
            y=drawdowns,
            mode="lines",
            name="Drawdown %",
            line={"color": "#C0392B", "width": 2},
            fill="tozeroy",
            fillcolor="rgba(192, 57, 43, 0.2)",
        )
    )

    fig.update_layout(
        title="Drawdown (Realized P&L Only)",
        xaxis_title="Date",
        yaxis_title="Drawdown %",
        hovermode="x unified",
        margin={"l": 50, "r": 30, "t": 60, "b": 40},
    )

    st.plotly_chart(fig, use_container_width=True, theme="streamlit")
    return fig
