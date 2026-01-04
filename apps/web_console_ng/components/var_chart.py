"""VaR/CVaR visualization component for NiceGUI risk dashboard.

Ported from apps/web_console/components/var_chart.py (Streamlit).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import plotly.graph_objects as go
from nicegui import ui

from apps.web_console.utils.validators import validate_var_history, validate_var_metrics


def _parse_date_for_sort(date_str: str) -> datetime:
    """Parse date string to datetime for proper chronological sorting.

    Handles ISO format dates (YYYY-MM-DD) and datetime strings.
    Converts timezone-aware datetimes to UTC before stripping tzinfo to
    ensure correct ordering across different timezones.
    Falls back to datetime.min if parsing fails, placing invalid dates first.
    """

    try:
        # Try ISO date format first (most common)
        if len(date_str) == 10 and date_str[4] == "-" and date_str[7] == "-":
            return datetime.strptime(date_str, "%Y-%m-%d")
        # Try ISO datetime format (may be timezone-aware)
        if "T" in date_str:
            parsed = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            # Convert to UTC then strip tzinfo for consistent naive comparison
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone(UTC).replace(tzinfo=None)
            return parsed
        # Fallback: try parsing as date
        parsed = datetime.strptime(date_str[:10], "%Y-%m-%d")
        return parsed
    except (ValueError, TypeError):
        # If parsing fails, use epoch to place invalid dates first
        return datetime.min

# Default risk budget values (parity with Streamlit: apps/web_console/config.py:87-89)
DEFAULT_VAR_LIMIT = 0.05  # 5% daily VaR limit
DEFAULT_WARNING_THRESHOLD = 0.8  # Warning at 80% utilization

# Chart color constants for consistent theming
COLOR_RED = "#E74C3C"  # Over limit / negative
COLOR_ORANGE = "#F39C12"  # Warning threshold
COLOR_GREEN = "#27AE60"  # Healthy / positive
COLOR_BLUE = "#2E86DE"  # Neutral / data lines


def _safe_float(value: Any, default: float | None = None) -> float | None:
    """Safely convert value to float, returning default on failure.

    Used for converting risk metrics from API responses that may contain
    None, invalid strings, NaN/inf, or other non-numeric values.
    """
    if value is None:
        return default
    try:
        result = float(value)
        if not math.isfinite(result):
            return default  # Reject NaN/inf as invalid
        return result
    except (ValueError, TypeError):
        return default


def _render_metric(label: str, value: str, help_text: str | None = None) -> None:
    """Render a single metric card.

    Decision (L-1): Custom _render_metric() instead of MetricCard class.
    MetricCard is for dashboard tiles with icons/trends; VaR metrics need simpler display.
    """
    with ui.card().classes("p-4"):
        ui.label(label).classes("text-sm text-gray-500")
        ui.label(value).classes("text-2xl font-bold")
        if help_text:
            ui.icon("help_outline", size="xs").tooltip(help_text).classes("text-gray-400")


def render_var_metrics(
    risk_data: dict[str, Any] | None,
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
    if not risk_data or not validate_var_metrics(risk_data):
        ui.label("VaR metrics not available.").classes("text-gray-500 p-4")
        return

    # risk_data is validated non-None at this point - safe conversion for robustness
    # Default to None (not 0.0) to avoid masking invalid data with "healthy" metrics
    var_95 = _safe_float(risk_data.get("var_95"))
    var_99 = _safe_float(risk_data.get("var_99"))
    cvar_95 = _safe_float(risk_data.get("cvar_95"))

    # Display metrics in row (M-2: N/A for None, 0.00% for valid zero)
    with ui.row().classes("gap-8 mb-4"):
        _render_metric(
            "VaR 95% (Daily)",
            f"{var_95:.2%}" if var_95 is not None else "N/A",
            "95% confidence: daily loss unlikely to exceed this",
        )
        _render_metric(
            "VaR 99% (Daily)",
            f"{var_99:.2%}" if var_99 is not None else "N/A",
            "99% confidence: daily loss unlikely to exceed this",
        )
        _render_metric(
            "CVaR 95% (Expected Shortfall)",
            f"{cvar_95:.2%}" if cvar_95 is not None else "N/A",
            "Expected loss when VaR 95% is breached",
        )

    # Render risk budget gauge (only if var_95 is valid)
    if var_95 is not None:
        render_var_gauge(var_95, var_limit, warning_threshold)


def render_var_gauge(
    var_value: float,
    var_limit: float = DEFAULT_VAR_LIMIT,
    warning_threshold: float = DEFAULT_WARNING_THRESHOLD,
) -> None:
    """Render gauge chart for VaR utilization against risk budget.

    Args:
        var_value: Current VaR value
        var_limit: Maximum allowed VaR
        warning_threshold: Threshold (0-1) to show warning color
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

    # data-testid for E2E testing (H-3)
    ui.plotly(fig).classes("w-full").props('data-testid="risk-gauge"')


def render_var_history(
    history: Sequence[dict[str, Any]] | None,
    var_limit: float = DEFAULT_VAR_LIMIT,
) -> None:
    """Render 30-day rolling VaR line chart with risk budget threshold.

    Args:
        history: List of {date, var_95} dicts
        var_limit: Risk budget limit line
    """
    valid_history = validate_var_history(list(history or []))

    if not valid_history:
        ui.label("No VaR history available.").classes("text-gray-500 p-4")
        return

    # Safe float conversion for var_values
    # L-1 Fix: Skip entries without var_95 instead of defaulting to 0.0
    dates: list[str] = []
    var_values: list[float] = []
    for h in valid_history:
        # Skip entries without valid date (would distort x-axis)
        date_val = h.get("date")
        if date_val is None or str(date_val).strip() == "":
            continue

        raw_var = h.get("var_95")
        if raw_var is None:
            continue  # Skip entries without var_95 data
        try:
            var_val = float(raw_var)
            if not math.isfinite(var_val):
                continue  # Skip NaN/inf values
            dates.append(str(date_val))
            var_values.append(var_val)
        except (TypeError, ValueError):
            continue  # Skip entries with invalid var_95

    # Normalize var_limit for history (same as gauge)
    if var_limit <= 0:
        var_limit = DEFAULT_VAR_LIMIT

    if not var_values:
        ui.label("No valid VaR history data.").classes("text-gray-500 p-4")
        return

    # Sort by date for correct time-series ordering (DB may return out of order)
    # Use datetime parsing for proper sort order (handles non-ISO formats)
    sorted_pairs = sorted(
        zip(dates, var_values, strict=False), key=lambda x: _parse_date_for_sort(x[0])
    )
    dates = [d for d, _ in sorted_pairs]
    var_values = [v for _, v in sorted_pairs]

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

    ui.plotly(fig).classes("w-full")


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
