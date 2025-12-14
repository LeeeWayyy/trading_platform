"""VaR/CVaR visualization component for risk dashboard."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import plotly.graph_objects as go
import streamlit as st

from apps.web_console.utils.validators import validate_risk_metrics, validate_var_history

# Default risk budget values (can be overridden by config)
DEFAULT_VAR_LIMIT = 0.05  # 5% daily VaR limit
DEFAULT_WARNING_THRESHOLD = 0.8  # Warning at 80% utilization

# Chart color constants for consistent theming
COLOR_RED = "#E74C3C"  # Over limit / negative
COLOR_ORANGE = "#F39C12"  # Warning threshold
COLOR_GREEN = "#27AE60"  # Healthy / positive
COLOR_BLUE = "#2E86DE"  # Neutral / data lines


def render_var_metrics(
    risk_data: dict[str, Any],
    var_limit: float = DEFAULT_VAR_LIMIT,
    warning_threshold: float = DEFAULT_WARNING_THRESHOLD,
) -> None:
    """Render VaR/CVaR metrics with gauge and risk budget display.

    Displays:
    - Current VaR 95% and 99%
    - CVaR 95% (expected shortfall)
    - Risk budget utilization gauge

    Args:
        risk_data: Dict with var_95, var_99, cvar_95 keys
        var_limit: Maximum allowed VaR (for utilization calculation)
        warning_threshold: Threshold (0-1) to show warning color
    """
    if not validate_risk_metrics(risk_data):
        st.info("Risk metrics not available.")
        return

    var_95 = float(risk_data.get("var_95", 0))
    var_99 = risk_data.get("var_99")
    cvar_95 = float(risk_data.get("cvar_95", 0))

    # Display metrics in columns
    cols = st.columns(3)

    with cols[0]:
        st.metric(
            label="VaR 95% (Daily)",
            value=f"{var_95:.2%}",
            help="95% confidence: daily loss unlikely to exceed this",
        )

    with cols[1]:
        if var_99 is not None:
            st.metric(
                label="VaR 99% (Daily)",
                value=f"{float(var_99):.2%}",
                help="99% confidence: daily loss unlikely to exceed this",
            )
        else:
            st.metric(label="VaR 99% (Daily)", value="N/A")

    with cols[2]:
        st.metric(
            label="CVaR 95% (Expected Shortfall)",
            value=f"{cvar_95:.2%}",
            help="Expected loss when VaR 95% is breached",
        )

    # Render risk budget gauge
    render_var_gauge(var_95, var_limit, warning_threshold)


def render_var_gauge(
    var_value: float,
    var_limit: float = DEFAULT_VAR_LIMIT,
    warning_threshold: float = DEFAULT_WARNING_THRESHOLD,
) -> go.Figure:
    """Render gauge chart for VaR utilization against risk budget.

    Args:
        var_value: Current VaR value
        var_limit: Maximum allowed VaR
        warning_threshold: Threshold (0-1) to show warning color

    Returns:
        Plotly gauge Figure
    """
    if var_limit <= 0:
        var_limit = DEFAULT_VAR_LIMIT

    utilization = var_value / var_limit if var_limit > 0 else 0
    utilization_pct = min(utilization * 100, 120)  # Cap at 120% for display

    # Determine gauge color based on utilization
    if utilization >= 1.0:
        bar_color = COLOR_RED  # Over limit
    elif utilization >= warning_threshold:
        bar_color = COLOR_ORANGE  # Warning
    else:
        bar_color = COLOR_GREEN  # Healthy

    fig = go.Figure(
        go.Indicator(
            mode="gauge+number+delta",
            value=utilization_pct,
            number={"suffix": "%", "valueformat": ".1f"},
            delta={
                "reference": warning_threshold * 100,
                "valueformat": ".1f",
                "increasing": {"color": COLOR_RED},
                "decreasing": {"color": COLOR_GREEN},
            },
            title={"text": "Risk Budget Utilization"},
            gauge={
                "axis": {"range": [0, 120], "ticksuffix": "%"},
                "bar": {"color": bar_color},
                "steps": [
                    {"range": [0, warning_threshold * 100], "color": "#E8F6E9"},
                    {"range": [warning_threshold * 100, 100], "color": "#FEF5E7"},
                    {"range": [100, 120], "color": "#FDEDEC"},
                ],
                "threshold": {
                    "line": {"color": COLOR_RED, "width": 4},
                    "thickness": 0.75,
                    "value": 100,
                },
            },
        )
    )

    fig.update_layout(
        height=250,
        margin={"l": 20, "r": 20, "t": 50, "b": 20},
    )

    st.plotly_chart(fig, use_container_width=True, theme="streamlit")
    return fig


def render_var_history(
    history: Sequence[dict[str, Any]],
    var_limit: float = DEFAULT_VAR_LIMIT,
) -> go.Figure | None:
    """Render 30-day rolling VaR line chart with risk budget threshold.

    Args:
        history: List of {date, var_95} dicts
        var_limit: Risk budget limit line

    Returns:
        Plotly Figure or None if no data
    """
    valid_history = validate_var_history(list(history))

    if not valid_history:
        st.info("No VaR history available.")
        return None

    dates = [str(h.get("date", "")) for h in valid_history]
    var_values = [float(h.get("var_95", 0)) for h in valid_history]

    fig = go.Figure()

    # VaR line
    fig.add_trace(
        go.Scatter(
            x=dates,
            y=var_values,
            mode="lines+markers",
            name="VaR 95%",
            line={"color": COLOR_BLUE, "width": 2},
            marker={"size": 4},
            hovertemplate="%{x}<br>VaR: %{y:.2%}<extra></extra>",
        )
    )

    # Risk budget limit line
    fig.add_hline(
        y=var_limit,
        line_width=2,
        line_dash="dash",
        line_color=COLOR_RED,
        annotation_text=f"Limit ({var_limit:.1%})",
        annotation_position="right",
    )

    fig.update_layout(
        title="30-Day VaR History",
        xaxis_title="Date",
        yaxis_title="VaR 95%",
        yaxis={"tickformat": ".1%"},
        hovermode="x unified",
        margin={"l": 50, "r": 80, "t": 60, "b": 40},
        showlegend=True,
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02},
    )

    st.plotly_chart(fig, use_container_width=True, theme="streamlit")
    return fig


__all__ = [
    "render_var_metrics",
    "render_var_gauge",
    "render_var_history",
    "DEFAULT_VAR_LIMIT",
    "DEFAULT_WARNING_THRESHOLD",
    "COLOR_RED",
    "COLOR_ORANGE",
    "COLOR_GREEN",
    "COLOR_BLUE",
]
