---
id: P5T6
title: "NiceGUI Migration - Charts & Analytics"
phase: P5
task: T6
priority: P1
owner: "@development-team"
state: IN_PROGRESS
created: 2025-12-31
started: 2026-01-03
dependencies: [P5T1, P5T4]
estimated_effort: "4-6 days"
related_adrs: [ADR-0031-nicegui-migration]
related_docs: [P5_PLANNING.md, P5T1_DONE.md, P5T4_DONE.md]
features: [T6.1, T6.2]
---

# P5T6: NiceGUI Migration - Charts & Analytics

**Phase:** P5 (Web Console Modernization)
**Status:** IN_PROGRESS
**Priority:** P1 (Visual Features)
**Owner:** @development-team
**Created:** 2025-12-31
**Estimated Effort:** 4-6 days
**Track:** Phase 5 from P5_PLANNING.md
**Dependency:** P5T1 (Foundation) and P5T4 (Real-Time Dashboard) must be complete

---

## Objective

Port Plotly charts for P&L, risk, and performance visualization from Streamlit to NiceGUI.

**Success looks like:**
- All existing chart components ported to NiceGUI patterns
- Async data fetching via RiskService (same as Streamlit, not REST)
- Real-time chart updates via `ui.refreshable`
- Charts render correctly with proper sizing
- Error states handled gracefully (no crashes on missing data)
- Consistent color theming across all charts
- Feature flag gating preserved
- Permission checks preserved (VIEW_PNL required)
- Placeholder/demo data warnings preserved
- Stress test visualization ported

**Key Pattern Changes:**
| Streamlit | NiceGUI |
|-----------|---------|
| `st.plotly_chart(fig)` | `ui.plotly(fig).classes("w-full")` |
| `st.info("msg")` | `ui.label("msg").classes("text-gray-500 p-4")` |
| `st.error("msg")` | `ui.notify("msg", type="negative")` |
| `st.columns(3)` | `ui.row()` with children |
| `st.metric()` | Custom `_render_metric()` component |
| `st.dataframe()` | `ui.table()` or `ui.aggrid()` |
| `st.spinner()` | `ui.spinner()` context manager |
| `st.stop()` | Early return from async function |

---

## Acceptance Criteria

### T6.1 P&L Charts

**Data Source Clarification:**
There are TWO types of P&L charts with different data schemas:

1. **Returns-based charts** (`equity_curve_chart.py`, `drawdown_chart.py`):
   - Input: `pl.DataFrame` with columns `{date, return}`
   - Computes cumulative returns internally
   - **Data Source:** `StrategyScopedDataAccess.get_performance_returns(start_date, end_date)` → Returns `list[dict]` with `{date, return}`
   - **Conversion:** Convert list to `pl.DataFrame` before passing to chart functions
   - **Schema Contract:** Each record MUST have `date` (date) and `return` (float, e.g. 0.01 = 1%)

2. **P&L-based charts** (`pnl_chart.py`):
   - Input: `Sequence[DailyPnLLike]` with `{date, cumulative_realized_pl, drawdown_pct}`
   - Pre-computed cumulative values
   - **Data Source:** `StrategyScopedDataAccess.get_pnl_summary(start_date, end_date)` → Returns `list[dict]`
   - **Schema Contract:** Each record MUST have `date` (date), `cumulative_realized_pl` (float), `drawdown_pct` (float, negative for drawdowns)

**NOTE:** For P5T6, the P&L charts (T6.1) are NOT used in the Risk Dashboard page. They are standalone components for future Performance page (P5T7). The Risk Dashboard (T6.2) uses RiskService exclusively.

**Deliverables:**
- [ ] Returns-based equity curve (from daily returns DataFrame)
- [ ] Returns-based drawdown chart (with max DD annotation)
- [ ] P&L-based equity curve (from cumulative_realized_pl)
- [ ] P&L-based drawdown chart (from drawdown_pct)
- [ ] Data validation using existing validators
- [ ] Async data fetching for performance data
- [ ] Error handling for missing/empty data
- [ ] Responsive chart sizing (`classes("w-full")`)
- [ ] Loading states during data fetch

**Validator → Component Mapping (T6.1):**
| Validator | NiceGUI Component | Usage |
|-----------|-------------------|-------|
| Column validation (inline) | `render_equity_curve()`, `render_drawdown_chart()` | Validate `{date, return}` columns present |
| Column validation (inline) | `render_pnl_equity_curve()`, `render_pnl_drawdown_chart()` | Validate `{date, cumulative_realized_pl, drawdown_pct}` present |

**NOTE:** T6.1 charts use inline column validation (checking required columns exist) rather than dedicated validator functions because the data comes pre-validated from `StrategyScopedDataAccess`. T6.2 charts use dedicated validators because RiskService data requires more complex validation (NaN handling, range checks).

**Port from existing:**
- `apps/web_console/components/equity_curve_chart.py` - Returns-based
- `apps/web_console/components/drawdown_chart.py` - Returns-based
- `apps/web_console/components/pnl_chart.py` - P&L-based

**Implementation:**
```python
# apps/web_console_ng/components/equity_curve_chart.py
from __future__ import annotations

from typing import TYPE_CHECKING

import plotly.graph_objects as go
from nicegui import ui

if TYPE_CHECKING:
    import polars as pl


def render_equity_curve(
    daily_returns: pl.DataFrame | None,
    title: str = "Equity Curve",
    height: int = 400,
) -> None:
    """Render equity curve chart from daily returns.

    Args:
        daily_returns: DataFrame with columns: date, return
        title: Chart title
        height: Chart height in pixels

    The chart shows cumulative returns computed as:
    (1 + r1) * (1 + r2) * ... * (1 + rn) - 1
    """
    if daily_returns is None or daily_returns.height == 0:
        ui.label("No return data available for equity curve").classes(
            "text-gray-500 text-center p-4"
        )
        return

    # Validate required columns
    required_cols = {"date", "return"}
    if not required_cols.issubset(set(daily_returns.columns)):
        ui.notify(f"Missing columns: {required_cols}", type="negative")
        return

    try:
        # Sort by date and compute cumulative returns
        sorted_df = daily_returns.sort("date")
        cumulative = (1 + sorted_df["return"]).cum_prod() - 1
        chart_df = sorted_df.with_columns(cumulative.alias("cumulative_return"))
        chart_pd = chart_df.select(["date", "cumulative_return"]).to_pandas()

        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=chart_pd["date"],
                y=chart_pd["cumulative_return"] * 100,
                mode="lines",
                name="Cumulative Return",
                line={"color": "#1f77b4", "width": 2},
                fill="tozeroy",
                fillcolor="rgba(31, 119, 180, 0.1)",
            )
        )

        fig.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.5)

        fig.update_layout(
            title=title,
            xaxis_title="Date",
            yaxis_title="Cumulative Return (%)",
            height=height,
            showlegend=False,
            hovermode="x unified",
            yaxis={"tickformat": ".1f", "ticksuffix": "%"},
        )

        ui.plotly(fig).classes("w-full")

    except Exception as e:
        ui.notify(f"Failed to render equity curve: {e}", type="negative")


# apps/web_console_ng/components/drawdown_chart.py
def render_drawdown_chart(
    daily_returns: pl.DataFrame | None,
    title: str = "Drawdown",
    height: int = 300,
) -> None:
    """Render drawdown chart from daily returns.

    Drawdown is computed as:
    - cumulative = (1 + r).cumprod()
    - running_max = cumulative.cummax()
    - drawdown = (cumulative - running_max) / running_max
    """
    if daily_returns is None or daily_returns.height == 0:
        ui.label("No return data available for drawdown chart").classes(
            "text-gray-500 text-center p-4"
        )
        return

    required_cols = {"date", "return"}
    if not required_cols.issubset(set(daily_returns.columns)):
        ui.notify(f"Missing columns: {required_cols}", type="negative")
        return

    try:
        sorted_df = daily_returns.sort("date")
        cumulative = (1 + sorted_df["return"]).cum_prod()
        running_max = cumulative.cum_max()
        drawdown = (cumulative - running_max) / running_max

        chart_df = sorted_df.with_columns(drawdown.alias("drawdown"))
        chart_pd = chart_df.select(["date", "drawdown"]).to_pandas()

        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=chart_pd["date"],
                y=chart_pd["drawdown"] * 100,
                mode="lines",
                name="Drawdown",
                line={"color": "#d62728", "width": 1.5},
                fill="tozeroy",
                fillcolor="rgba(214, 39, 40, 0.3)",
            )
        )

        # Max drawdown annotation
        if not chart_pd.empty and not chart_pd["drawdown"].isnull().all():
            max_dd = chart_pd["drawdown"].min()
            max_dd_date = chart_pd.loc[chart_pd["drawdown"].idxmin(), "date"]
            fig.add_annotation(
                x=max_dd_date,
                y=max_dd * 100,
                text=f"Max DD: {max_dd * 100:.1f}%",
                showarrow=True,
                arrowhead=2,
                ax=0,
                ay=-40,
            )

        fig.update_layout(
            title=title,
            xaxis_title="Date",
            yaxis_title="Drawdown (%)",
            height=height,
            showlegend=False,
            hovermode="x unified",
            yaxis={"tickformat": ".1f", "ticksuffix": "%"},
        )

        ui.plotly(fig).classes("w-full")

    except Exception as e:
        ui.notify(f"Failed to render drawdown chart: {e}", type="negative")


# apps/web_console_ng/components/pnl_chart.py
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

import plotly.graph_objects as go
from nicegui import ui

# Type alias for P&L data - supports both dict and dataclass-like objects
DailyPnLLike = Mapping[str, Any] | Any


def _get_value(item: DailyPnLLike, key: str) -> Any:
    """Extract value from dict or object attribute."""
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
    """Extract date, cumulative P&L, and drawdown series from P&L data.

    Args:
        daily_pnl: Sequence of P&L records with:
            - date: trade date
            - cumulative_realized_pl: cumulative realized P&L
            - drawdown_pct: drawdown percentage (negative for losses)

    Returns:
        Tuple of (dates, cumulative_values, drawdown_values)
    """
    dates: list[str] = []
    cumulative: list[float] = []
    drawdowns: list[float] = []

    for entry in daily_pnl:
        dates.append(str(_get_value(entry, "date")))
        cumulative.append(_as_float(_get_value(entry, "cumulative_realized_pl")))
        drawdowns.append(_as_float(_get_value(entry, "drawdown_pct")))

    return dates, cumulative, drawdowns


def render_pnl_equity_curve(daily_pnl: Sequence[DailyPnLLike]) -> None:
    """Render cumulative realized P&L line chart.

    Args:
        daily_pnl: Sequence of P&L records from StrategyScopedDataAccess.get_pnl_summary()
            Schema: {date, cumulative_realized_pl, drawdown_pct}
    """
    if not daily_pnl:
        ui.label("No performance data available for the selected range.").classes(
            "text-gray-500 text-center p-4"
        )
        return

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

    ui.plotly(fig).classes("w-full")


def render_pnl_drawdown_chart(daily_pnl: Sequence[DailyPnLLike]) -> None:
    """Render drawdown area chart from pre-computed drawdown percentages.

    Args:
        daily_pnl: Sequence of P&L records from StrategyScopedDataAccess.get_pnl_summary()
            Schema: {date, cumulative_realized_pl, drawdown_pct}
    """
    if not daily_pnl:
        ui.label("No drawdown data available for the selected range.").classes(
            "text-gray-500 text-center p-4"
        )
        return

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

    ui.plotly(fig).classes("w-full")
```

**Testing:**
- [ ] Equity curve renders with valid data
- [ ] Equity curve shows placeholder on empty data
- [ ] Drawdown chart annotates max drawdown
- [ ] Charts resize responsively
- [ ] Error handling for missing columns
- [ ] Charts update on data change

---

### T6.2 Risk Dashboard Charts

**Data Source:** RiskService (NOT REST endpoints)
- Data fetched via `RiskService.get_risk_dashboard_data()`
- Returns `RiskDashboardData` with: risk_metrics, factor_exposures, stress_tests, var_history, is_placeholder, placeholder_reason
- Uses `StrategyScopedDataAccess` for DB/Redis access
- Same architecture as Streamlit version

**Page-Level Requirements (Parity with Streamlit):**
- [ ] Feature flag check: `FEATURE_RISK_DASHBOARD`
- [ ] Permission check: `VIEW_PNL` required
- [ ] Strategy access validation: User must have authorized strategies
- [ ] Placeholder/demo data warning (prominent red banner)
- [ ] Risk overview metrics section (total_risk, factor_risk, specific_risk)

**Chart Deliverables:**
- [ ] VaR metrics display (VaR 95%, VaR 99%, CVaR 95%)
- [ ] VaR gauge chart (risk budget utilization)
- [ ] VaR history line chart (30-day rolling)
- [ ] Factor exposure horizontal bar chart (with canonical ordering)
- [ ] Stress test results table (scenario summary)
- [ ] Stress test waterfall chart (factor contribution for worst case)
- [ ] Data validation using existing validators (`validate_risk_metrics`, `validate_var_history`, `validate_exposures`, `validate_stress_tests`)
- [ ] Async risk data fetching via RiskService
- [ ] Real-time updates via `ui.refreshable` (ALL sections)

**Port from existing:**
- `apps/web_console/components/var_chart.py`
- `apps/web_console/components/factor_exposure_chart.py`
- `apps/web_console/components/stress_test_results.py` (NEW - must port)
- `apps/web_console/pages/risk.py`
- `apps/web_console/utils/validators.py` (reuse validators)

**Validator → Component Mapping (REQUIRED):**
| Validator | NiceGUI Component | Usage |
|-----------|-------------------|-------|
| `validate_risk_metrics(data)` | `render_var_metrics()` | Validate before rendering VaR metrics |
| `validate_var_history(history)` | `render_var_history()` | Filter invalid entries before chart |
| `validate_exposures(exposures)` | `render_factor_exposure()` | Filter NaN/missing exposures |
| `validate_stress_tests(results)` | `render_stress_tests()` | Filter invalid stress test entries |

**Implementation Requirement:** Each NiceGUI chart function MUST call its corresponding validator before processing data. Tests MUST verify validator is called.

**Implementation:**
```python
# apps/web_console_ng/components/var_chart.py
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import plotly.graph_objects as go
from nicegui import ui

# Default risk budget values
DEFAULT_VAR_LIMIT = 0.05  # 5% daily VaR limit
DEFAULT_WARNING_THRESHOLD = 0.8  # Warning at 80% utilization

# Chart color constants
COLOR_RED = "#E74C3C"
COLOR_ORANGE = "#F39C12"
COLOR_GREEN = "#27AE60"
COLOR_BLUE = "#2E86DE"


def render_var_metrics(
    risk_data: dict[str, Any] | None,
    var_limit: float = DEFAULT_VAR_LIMIT,
    warning_threshold: float = DEFAULT_WARNING_THRESHOLD,
) -> None:
    """Render VaR/CVaR metrics with gauge and risk budget display."""
    if not risk_data or "var_95" not in risk_data:
        ui.label("Risk metrics not available").classes("text-gray-500 p-4")
        return

    var_95 = float(risk_data.get("var_95", 0))
    var_99 = risk_data.get("var_99")
    cvar_95 = float(risk_data.get("cvar_95", 0))

    # Display metrics in row
    with ui.row().classes("gap-8 mb-4"):
        _render_metric("VaR 95% (Daily)", f"{var_95:.2%}")
        _render_metric(
            "VaR 99% (Daily)",
            f"{float(var_99):.2%}" if var_99 is not None else "N/A",
        )
        _render_metric("CVaR 95% (Expected Shortfall)", f"{cvar_95:.2%}")

    # Render risk budget gauge
    render_var_gauge(var_95, var_limit, warning_threshold)


def _render_metric(label: str, value: str) -> None:
    """Render a single metric card."""
    with ui.card().classes("p-4"):
        ui.label(label).classes("text-sm text-gray-500")
        ui.label(value).classes("text-2xl font-bold")


def render_var_gauge(
    var_value: float,
    var_limit: float = DEFAULT_VAR_LIMIT,
    warning_threshold: float = DEFAULT_WARNING_THRESHOLD,
) -> None:
    """Render gauge chart for VaR utilization against risk budget."""
    if var_limit <= 0:
        var_limit = DEFAULT_VAR_LIMIT

    utilization = var_value / var_limit if var_limit > 0 else 0
    utilization_pct = min(utilization * 100, 120)

    # Determine gauge color
    if utilization >= 1.0:
        bar_color = COLOR_RED
    elif utilization >= warning_threshold:
        bar_color = COLOR_ORANGE
    else:
        bar_color = COLOR_GREEN

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

    ui.plotly(fig).classes("w-full")


def render_var_history(
    history: Sequence[dict[str, Any]] | None,
    var_limit: float = DEFAULT_VAR_LIMIT,
) -> None:
    """Render 30-day rolling VaR line chart."""
    if not history:
        ui.label("No VaR history available").classes("text-gray-500 p-4")
        return

    dates = [str(h.get("date", "")) for h in history]
    var_values = [float(h.get("var_95", 0)) for h in history]

    fig = go.Figure()
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


# apps/web_console_ng/components/factor_exposure_chart.py
from apps.web_console.utils.validators import validate_exposures
from libs.risk.factor_covariance import CANONICAL_FACTOR_ORDER

FACTOR_DISPLAY_NAMES = {
    "momentum_12_1": "Momentum (12-1)",
    "book_to_market": "Book-to-Market",
    "roe": "ROE (Quality)",
    "log_market_cap": "Size (Market Cap)",
    "realized_vol": "Volatility",
    "asset_growth": "Asset Growth",
}

DEFAULT_FACTOR_ORDER = [
    "log_market_cap",
    "book_to_market",
    "momentum_12_1",
    "realized_vol",
    "roe",
    "asset_growth",
]

# Merge canonical + default order (preserves new factors)
# FALLBACK BEHAVIOR: If CANONICAL_FACTOR_ORDER is empty/None, use DEFAULT_FACTOR_ORDER only
# This ensures UI never shows empty chart even if libs/risk not configured
_chart_factor_order = list(dict.fromkeys((CANONICAL_FACTOR_ORDER or []) + DEFAULT_FACTOR_ORDER))


def render_factor_exposure(
    exposures: Sequence[dict[str, Any]] | None,
) -> None:
    """Render horizontal bar chart of factor exposures."""
    # Validate exposures (handles NaN, missing fields)
    valid_exposures = validate_exposures(list(exposures or []))

    if not valid_exposures:
        ui.label("No factor exposure data available").classes("text-gray-500 p-4")
        return

    # Build lookup from validated exposures
    exposure_map = {e["factor_name"]: float(e["exposure"]) for e in valid_exposures}

    factors = []
    values = []
    colors = []

    # Use merged canonical order (handles new/missing factors)
    for factor in _chart_factor_order:
        exposure = exposure_map.get(factor, 0.0)
        display_name = FACTOR_DISPLAY_NAMES.get(factor, factor)
        factors.append(display_name)
        values.append(exposure)
        colors.append(COLOR_GREEN if exposure >= 0 else COLOR_RED)

    # Reverse for horizontal bar (top factor first)
    factors = factors[::-1]
    values = values[::-1]
    colors = colors[::-1]

    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            y=factors,
            x=values,
            orientation="h",
            marker_color=colors,
            text=[f"{v:+.2%}" for v in values],
            textposition="outside",
            hovertemplate="%{y}: %{x:.2%}<extra></extra>",
        )
    )

    fig.add_vline(x=0, line_width=1, line_color="gray", line_dash="dash")

    fig.update_layout(
        title="Factor Exposures",
        xaxis_title="Exposure (%)",
        yaxis_title="",
        hovermode="y unified",
        margin={"l": 120, "r": 80, "t": 60, "b": 40},
        xaxis={"tickformat": ".0%", "zeroline": True},
    )

    ui.plotly(fig).classes("w-full")
```

**Risk Page with Refreshable Charts (Full Parity):**
```python
# apps/web_console_ng/pages/risk.py
from nicegui import ui, Client
from apps.web_console_ng.ui.layout import main_layout
from apps.web_console_ng.auth.middleware import requires_auth, has_permission
from apps.web_console_ng.auth.permissions import Permission, get_authorized_strategies
from apps.web_console_ng.components.var_chart import render_var_metrics, render_var_history
from apps.web_console_ng.components.factor_exposure_chart import render_factor_exposure
from apps.web_console_ng.components.stress_test_results import render_stress_tests
from apps.web_console_ng.config import (
    FEATURE_RISK_DASHBOARD,
    RISK_BUDGET_VAR_LIMIT,
    RISK_BUDGET_WARNING_THRESHOLD,
)
from apps.web_console.services.risk_service import RiskService
from apps.web_console.data.strategy_scoped_queries import StrategyScopedDataAccess
from apps.web_console.utils.db_pool import get_db_pool, get_redis_client
from apps.web_console.utils.validators import validate_risk_metrics


@ui.page("/risk")
@requires_auth
@main_layout
async def risk_dashboard(client: Client) -> None:
    """Risk analytics dashboard with real-time updates."""
    user = get_current_user()
    user_id = user.get("user_id")

    # === PAGE-LEVEL GATES (Parity with Streamlit) ===

    # Feature flag check
    if not FEATURE_RISK_DASHBOARD:
        ui.label("Risk Analytics Dashboard is not currently enabled.").classes(
            "text-gray-500 text-center p-8"
        )
        return

    # Permission check
    if not has_permission(user, Permission.VIEW_PNL):
        ui.notify("Permission denied: VIEW_PNL required", type="negative")
        return

    # Strategy access check
    authorized_strategies = get_authorized_strategies(user)
    if not authorized_strategies:
        with ui.card().classes("w-full max-w-2xl mx-auto p-6"):
            ui.label("No Strategy Access").classes("text-xl font-bold text-yellow-600")
            ui.label(
                "You don't have access to any strategies. "
                "Contact your administrator to be assigned."
            ).classes("text-gray-600")
        return

    # === DATA FETCHING ===
    risk_data: dict = {}
    is_loading = True

    async def load_risk_data() -> None:
        nonlocal risk_data, is_loading
        is_loading = True
        try:
            # Use RiskService (same as Streamlit, NOT REST)
            db_pool = get_db_pool()
            scoped_access = StrategyScopedDataAccess(
                db_pool=db_pool,
                redis_client=get_redis_client(),
                user={"user_id": user_id, "role": user.get("role"), "strategies": list(authorized_strategies)},
            )
            service = RiskService(scoped_access)
            data = await service.get_risk_dashboard_data()

            risk_data = {
                "risk_metrics": data.risk_metrics,
                "factor_exposures": data.factor_exposures,
                "stress_tests": data.stress_tests,
                "var_history": data.var_history,
                "is_placeholder": data.is_placeholder,
                "placeholder_reason": data.placeholder_reason,
            }
        except Exception as e:
            ui.notify(f"Failed to load risk data: {e}", type="negative")
        finally:
            is_loading = False

    await load_risk_data()

    # === PAGE CONTENT ===
    with ui.card().classes("w-full max-w-6xl mx-auto p-6"):
        ui.label("Risk Analytics Dashboard").classes("text-2xl font-bold mb-2")
        ui.label("Portfolio risk metrics, factor exposures, and stress test analysis.").classes(
            "text-gray-500 mb-6"
        )

        # === PLACEHOLDER WARNING (CRITICAL) ===
        @ui.refreshable
        def placeholder_warning() -> None:
            if risk_data.get("is_placeholder", False):
                with ui.card().classes("w-full bg-red-100 border-red-500 border-2 p-4 mb-6"):
                    ui.label("DEMO DATA - NOT FOR TRADING DECISIONS").classes(
                        "text-red-700 font-bold text-lg"
                    )
                    ui.label(risk_data.get("placeholder_reason", "Risk model artifacts not available.")).classes(
                        "text-red-600"
                    )

        placeholder_warning()

        # Refresh button
        async def refresh() -> None:
            await load_risk_data()
            placeholder_warning.refresh()
            risk_overview_section.refresh()
            var_section.refresh()
            var_history_section.refresh()  # FIX: Was missing in Rev 1
            exposure_section.refresh()
            stress_section.refresh()

        ui.button("Refresh", on_click=refresh, icon="refresh").classes("mb-4")

        # === RISK OVERVIEW ===
        @ui.refreshable
        def risk_overview_section() -> None:
            metrics = risk_data.get("risk_metrics", {})
            if not metrics:
                ui.label("Risk metrics not available").classes("text-gray-500 p-4")
                return

            ui.label("Risk Overview").classes("text-xl font-semibold mb-4")
            with ui.row().classes("gap-8 mb-6"):
                _render_metric("Total Risk (Ann.)", f"{metrics.get('total_risk', 0):.2%}")
                _render_metric("Factor Risk", f"{metrics.get('factor_risk', 0):.2%}")
                _render_metric("Specific Risk", f"{metrics.get('specific_risk', 0):.2%}")

        risk_overview_section()

        ui.separator().classes("my-4")

        # === VAR SECTION ===
        @ui.refreshable
        def var_section() -> None:
            ui.label("Value at Risk").classes("text-xl font-semibold mb-4")
            render_var_metrics(
                risk_data.get("risk_metrics", {}),
                var_limit=RISK_BUDGET_VAR_LIMIT,
                warning_threshold=RISK_BUDGET_WARNING_THRESHOLD,
            )

        @ui.refreshable
        def var_history_section() -> None:
            var_history = risk_data.get("var_history", [])
            if var_history:
                render_var_history(var_history, var_limit=RISK_BUDGET_VAR_LIMIT)

        var_section()
        var_history_section()

        ui.separator().classes("my-4")

        # === FACTOR EXPOSURES ===
        @ui.refreshable
        def exposure_section() -> None:
            ui.label("Factor Exposures").classes("text-xl font-semibold mb-4")
            render_factor_exposure(risk_data.get("factor_exposures", []))

        exposure_section()

        ui.separator().classes("my-4")

        # === STRESS TESTS ===
        @ui.refreshable
        def stress_section() -> None:
            render_stress_tests(risk_data.get("stress_tests", []))

        stress_section()

    # === TIMER LIFECYCLE MANAGEMENT ===
    # Register timer with ClientLifecycleManager for cleanup on disconnect
    # (Same pattern as dashboard.py to prevent timer leaks and concurrent refreshes)
    lifecycle = ClientLifecycleManager.get()
    client_id = client.storage.get("client_id")

    # Prevent concurrent refresh calls with a lock
    refresh_lock = asyncio.Lock()

    async def guarded_refresh() -> None:
        """Refresh with lock to prevent overlapping RiskService calls."""
        if refresh_lock.locked():
            return  # Skip if already refreshing
        async with refresh_lock:
            await load_risk_data()
            placeholder_warning.refresh()
            risk_overview_section.refresh()
            var_section.refresh()
            var_history_section.refresh()
            exposure_section.refresh()
            stress_section.refresh()

    # Auto-refresh every 60 seconds (guarded)
    refresh_timer = ui.timer(60.0, guarded_refresh)

    # Register cleanup callback to cancel timer on client disconnect
    def cleanup_timer() -> None:
        refresh_timer.cancel()

    await lifecycle.register_cleanup_callback(client_id, cleanup_timer)


def _render_metric(label: str, value: str) -> None:
    """Render a single metric card."""
    with ui.card().classes("p-4"):
        ui.label(label).classes("text-sm text-gray-500")
        ui.label(value).classes("text-2xl font-bold")
```

**Testing:**
- [ ] VaR metrics display correctly
- [ ] VaR gauge color changes based on utilization
- [ ] VaR history shows limit line
- [ ] Factor exposure chart orders factors correctly (canonical order)
- [ ] Factor exposure validation handles NaN/invalid values
- [ ] Factor exposure colors by sign (green/red)
- [ ] Stress test scenario table renders correctly
- [ ] Stress test waterfall chart shows worst case
- [ ] Feature flag gating works (FEATURE_RISK_DASHBOARD)
- [ ] Permission check works (VIEW_PNL required)
- [ ] Strategy access check works (no strategies = blocked)
- [ ] Placeholder warning displays for demo data
- [ ] Risk overview section shows 3 metrics
- [ ] Refreshable sections ALL update on data change
- [ ] Auto-refresh timer updates ALL sections
- [ ] Error handling for missing data

---

## Prerequisites Checklist

**Must verify before starting implementation:**

- [x] **P5T1 complete:** Foundation with async patterns ✅ Verified
- [x] **P5T4 complete:** Real-Time Dashboard patterns ✅ Verified
- [x] **RiskService available:**
  - [x] `RiskService.get_risk_dashboard_data()` - Returns RiskDashboardData ✅ `apps/web_console/services/risk_service.py:92`
  - [x] `StrategyScopedDataAccess` - DB/Redis access layer ✅ `apps/web_console/data/strategy_scoped_queries.py`
  - [x] Validators: `validate_risk_metrics`, `validate_var_history`, `validate_exposures`, `validate_stress_tests` ✅ `apps/web_console/utils/validators.py`
- [ ] **Config available:** (NEEDS UPDATE - NiceGUI config.py missing these)
  - [ ] `FEATURE_RISK_DASHBOARD` feature flag - ⚠️ Must add to `apps/web_console_ng/config.py`
  - [ ] `RISK_BUDGET_VAR_LIMIT` - VaR limit constant - ⚠️ Must add to `apps/web_console_ng/config.py`
  - [ ] `RISK_BUDGET_WARNING_THRESHOLD` - Warning threshold - ⚠️ Must add to `apps/web_console_ng/config.py`
- [x] **Permissions available:**
  - [x] `Permission.VIEW_PNL` enum value ✅ `libs/web_console_auth/permissions.py:32`
  - [x] `get_authorized_strategies()` helper ✅ `libs/web_console_auth/permissions.py:328`

---

## Pre-Implementation Analysis Findings

**Analysis Date:** 2026-01-03

### Existing Streamlit Components to Port

| Component | Source File | Key Patterns |
|-----------|-------------|--------------|
| Equity Curve (returns) | `apps/web_console/components/equity_curve_chart.py` | Polars DataFrame, cumulative returns |
| Drawdown (returns) | `apps/web_console/components/drawdown_chart.py` | Max DD annotation |
| P&L Charts | `apps/web_console/components/pnl_chart.py` | DailyPnLLike sequence |
| VaR Charts | `apps/web_console/components/var_chart.py` | Gauge, metrics, history |
| Factor Exposure | `apps/web_console/components/factor_exposure_chart.py` | Canonical ordering, validation |
| Stress Tests | `apps/web_console/components/stress_test_results.py` | Table + waterfall |
| Risk Page | `apps/web_console/pages/risk.py` | Feature flag, permissions, RiskService |

### Config Updates Required

```python
# Must add to apps/web_console_ng/config.py:

# Risk Dashboard Configuration
# NOTE: Use explicit float() with try/except, NOT _safe_float (doesn't exist in NiceGUI config)
# PARITY: Defaults match apps/web_console/config.py:87-89 (Streamlit config)
def _parse_float(env_var: str, default: float) -> float:
    """Parse float from environment variable with fallback."""
    raw = os.getenv(env_var, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning(f"Invalid {env_var} value '{raw}', using default {default}")
        return default

# Defaults from Streamlit: apps/web_console/config.py:87-89
RISK_BUDGET_VAR_LIMIT = _parse_float("RISK_BUDGET_VAR_LIMIT", 0.05)  # 5% daily VaR limit (parity)
RISK_BUDGET_WARNING_THRESHOLD = _parse_float("RISK_BUDGET_WARNING_THRESHOLD", 0.8)  # 80% warning (parity)

FEATURE_RISK_DASHBOARD = os.getenv("FEATURE_RISK_DASHBOARD", "false").lower() in {
    "1", "true", "yes", "on"
}
```

### Existing NiceGUI Patterns to Follow

From `apps/web_console_ng/pages/dashboard.py`:
- `@ui.page("/")` decorator for routing
- `@requires_auth` decorator for auth
- `@main_layout` decorator for layout wrapper
- `Client` parameter for client context
- `get_current_user()` for user data
- `ClientLifecycleManager` for cleanup
- `ui.timer()` for periodic updates

From `apps/web_console_ng/components/metric_card.py`:
- `MetricCard` class for metric display (can be reused or adapted)

---

## Approach

### High-Level Plan

1. **C0: P&L Charts** (2-3 days)
   - Port equity_curve_chart.py (returns-based)
   - Port drawdown_chart.py (returns-based)
   - Port pnl_chart.py (P&L-based)
   - Add async data fetching

2. **C1: Risk Dashboard Charts** (2-3 days)
   - Port var_chart.py (metrics, gauge, history)
   - Port factor_exposure_chart.py (with validation, canonical order)
   - Port stress_test_results.py (table + waterfall)
   - Create risk.py page with full parity
   - Add ui.refreshable for ALL sections

---

## Component Breakdown

### C0: P&L Charts

**Files to Create:**
```
apps/web_console_ng/components/
├── equity_curve_chart.py      # Cumulative returns line chart
├── drawdown_chart.py          # Drawdown area chart
├── pnl_chart.py               # Daily P&L bar chart
tests/apps/web_console_ng/
└── test_pnl_charts.py
```

---

### C1: Risk Dashboard Charts

**Files to Create:**
```
apps/web_console_ng/components/
├── var_chart.py               # VaR metrics, gauge, history
├── factor_exposure_chart.py   # Factor exposure bar chart
├── stress_test_results.py     # Stress test table + waterfall (NEW)
apps/web_console_ng/pages/
├── risk.py                    # Risk analytics page (with full parity)
tests/apps/web_console_ng/
├── test_risk_charts.py
└── test_stress_tests.py       # Stress test rendering tests (NEW)
```

---

## Testing Strategy

### Unit Tests (CI - Automated)
- `test_pnl_charts.py`: Equity curve, drawdown rendering with various data
- `test_risk_charts.py`: VaR metrics, gauge colors, factor ordering, validation
- `test_stress_tests.py`: Scenario table ordering, waterfall rendering
- `test_factor_unknown_name.py`: Verify unknown factor names fall back to raw name (L-2 fix)

### Integration Tests (CI - Docker)
- `test_risk_page_integration.py`: Full risk page with RiskService
- `test_risk_page_gating.py`: Feature flag, permission, strategy access checks

### E2E Tests (CI - Playwright)
- `test_charts_e2e.py`: Charts render and update on refresh
- `test_risk_page_e2e.py`: Full risk dashboard flow

**Playwright Setup Confirmation (H-3):**
- Existing Playwright setup: `tests/e2e/conftest.py` provides NiceGUI test fixtures
- Base URL: `http://localhost:8080` (NiceGUI dev server)
- Selectors strategy: Use `data-testid` attributes for stable selectors
- Chart selectors: `.plotly-graph-div` for Plotly charts, `[data-testid="risk-gauge"]` for gauge
- Table selectors: `table[data-testid="stress-results-table"]` for stress test table
- Implementation: Add `data-testid` attributes during component implementation

---

## Dependencies

### External
- `nicegui>=2.0`: UI framework
- `plotly>=5.0`: Charting library
- `polars`: DataFrame operations
- `pandas`: Plotly data conversion

### Internal
- `apps/web_console_ng/auth/`: Auth middleware (P5T2)
- `apps/web_console_ng/ui/layout.py`: Main layout (P5T2)
- `apps/web_console/services/risk_service.py`: RiskService (reuse, NOT REST)
- `apps/web_console/data/strategy_scoped_queries.py`: StrategyScopedDataAccess
- `apps/web_console/utils/validators.py`: Data validators (reuse)
- `libs/risk/factor_covariance.py`: Factor order constants

---

## Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Chart rendering slow with large data | Medium | Medium | Implement data sampling/aggregation for large datasets |
| Plotly WebGL conflicts with NiceGUI | Low | High | Test early, fall back to SVG renderer if needed |
| Color inconsistency across themes | Low | Low | Use constants from theme.py |
| Memory leaks from chart recreation | Medium | Medium | Use ui.refreshable properly, test memory |

---

## Implementation Notes

**Address during development:**

1. **Plotly Figure Pattern:**
   - Create Plotly `go.Figure()` as normal
   - Render with `ui.plotly(fig).classes("w-full")`
   - NO `use_container_width` parameter (NiceGUI handles sizing)

2. **Error State Pattern:**
   - Replace `st.info()` with placeholder label + classes
   - Replace `st.error()` with `ui.notify(type="negative")`
   - Always render something (empty state, not nothing)

3. **Refreshable Charts:**
   - Use `@ui.refreshable` decorator for chart sections
   - Call `.refresh()` method to update
   - Avoid recreating entire page on data change

4. **Async Data Loading:**
   - Fetch data in async page function
   - Store in nonlocal variables
   - Pass to chart functions as parameters

5. **Color Constants:**
   - Define chart colors in one place
   - Use consistent theme across all charts
   - Match existing Streamlit colors for familiarity

6. **Metric Cards (L-1 Decision):** ✅ ADDRESSED (Rev 4)
   - Streamlit has `st.metric()`, NiceGUI doesn't
   - **Decision:** Create custom `_render_metric()` helper (inline function, NOT reusing `MetricCard` class)
   - **Rationale:** `MetricCard` from `metric_card.py` is designed for dashboard tiles with icons and trends. VaR/risk metrics need simpler display. Using `_render_metric()` matches Streamlit's `st.metric()` behavior more closely.
   - Style consistently with ui.card() + label classes

7. **Placeholder-Zero Metrics UX (M-2):** ✅ ADDRESSED (Rev 4)
   - When `var_99` is None/missing: Display "N/A" (not "0.00%")
   - When metric is 0.0 (valid zero): Display "0.00%"
   - Pattern: `f"{float(value):.2%}" if value is not None else "N/A"`
   - This follows Streamlit behavior from `apps/web_console/components/var_chart.py:45`

8. **Auto-Refresh Timer Interval (M-3):** ✅ ADDRESSED (Rev 4)
   - Interval: 60 seconds
   - **Justification:**
     - Matches `apps/web_console/pages/risk.py:178` Streamlit polling interval (parity)
     - Risk metrics update infrequently (portfolio rebalances are rare events)
     - 60s balances freshness vs RiskService load
     - VaR calculation is compute-intensive; shorter intervals would waste resources
   - Can be configured via `RISK_REFRESH_INTERVAL_SECONDS` if needed in future

9. **DataFrame Handling:**
   - Accept `pl.DataFrame | None` for null safety
   - Convert to pandas for Plotly: `.to_pandas()`
   - Validate required columns before processing

10. **Stress Test Rendering:** ✅ ADDRESSED IN DOCUMENT (Rev 2)
   - Port `stress_test_results.py` with scenario table + waterfall chart
   - Use predefined scenario order (SCENARIO_DISPLAY_ORDER)
   - Show worst case waterfall automatically
   - **Factor Label Mapping:** Waterfall chart uses `FACTOR_DISPLAY_NAMES` from `factor_exposure_chart.py` for human-readable factor labels. If a factor is not in the mapping, use the raw factor name as fallback (same as Streamlit behavior).
   - **Unknown Factor Name Test (L-2):** Unit test MUST verify: when `exposures` contains `{"factor_name": "unknown_factor_xyz", "exposure": 0.05}`, the chart displays "unknown_factor_xyz" as the label (raw fallback).

11. **Data Source Architecture:** ✅ ADDRESSED IN DOCUMENT (Rev 2)
   - Use RiskService directly (same as Streamlit), NOT REST endpoints
   - RiskService -> StrategyScopedDataAccess -> DB/Redis
   - Reuse existing validators for data validation

12. **Page-Level Parity Gates:** ✅ ADDRESSED IN DOCUMENT (Rev 2)
    - Feature flag check: `FEATURE_RISK_DASHBOARD`
    - Permission check: `VIEW_PNL` required
    - Strategy access: User must have authorized strategies
    - Placeholder warning: Red banner for demo data
    - Risk overview: 3 metrics (total_risk, factor_risk, specific_risk)

13. **Factor Exposure Validation:** ✅ ADDRESSED IN DOCUMENT (Rev 2)
    - Reuse `validate_exposures()` from validators.py
    - Use canonical factor order from `libs/risk/factor_covariance.py`
    - Merge canonical + default order for new factor support

14. **Refresh Logic Fix:** ✅ ADDRESSED IN DOCUMENT (Rev 2)
    - ALL refreshable sections must update on refresh
    - Include: placeholder_warning, risk_overview, var_section, var_history_section, exposure_section, stress_section
    - `var_history_section.refresh()` was missing in Rev 1

15. **Info/Error Pattern Alignment:** ✅ ADDRESSED IN DOCUMENT (Rev 2)
    - `st.info()` -> `ui.label("msg").classes("text-gray-500 p-4")`
    - `st.error()` -> `ui.notify("msg", type="negative")`
    - Consistent with P5T4/P5T5 patterns

16. **Timer Lifecycle Management:** ✅ ADDRESSED IN DOCUMENT (Rev 3)
    - Register refresh timers with `ClientLifecycleManager` for cleanup on disconnect
    - Use `asyncio.Lock()` to prevent overlapping RiskService calls during concurrent refreshes
    - Same pattern as `dashboard.py` (P5T4)
    - Tests MUST verify: timer cleanup on disconnect, no concurrent refresh execution

17. **Config Helper Pattern:** ✅ ADDRESSED IN DOCUMENT (Rev 3)
    - Use `_parse_float()` helper with try/except, NOT `_safe_float` (doesn't exist in NiceGUI config)
    - Log warning on invalid values, use defaults

18. **Validator Mapping:** ✅ ADDRESSED IN DOCUMENT (Rev 3)
    - Each chart function MUST call its corresponding validator
    - See "Validator → Component Mapping" tables in T6.1 and T6.2 sections
    - Tests MUST verify validator is invoked

---

## Definition of Done

- [ ] All acceptance criteria met
- [ ] Unit tests pass with >90% coverage
- [ ] Integration tests pass
- [ ] E2E tests pass (charts render visually)
- [ ] All existing charts ported (including stress tests)
- [ ] RiskService data fetching working (not REST)
- [ ] Error states handled gracefully
- [ ] Color theming consistent
- [ ] Feature flag gating verified
- [ ] Permission checks verified
- [ ] Placeholder warning working
- [ ] ALL refreshable sections update on refresh
- [ ] No regressions in P5T1-P5T5 tests
- [ ] Code reviewed and approved
- [ ] Merged to feature branch

---

**Last Updated:** 2026-01-03 (Rev 4)
**Status:** PLANNING - Plan Review Iteration 2 (fixing issues)
