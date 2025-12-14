"""Strategy Comparison Tool page (T6.4b)."""

from __future__ import annotations

from datetime import date, timedelta

import streamlit as st

from apps.web_console.auth.permissions import Permission, get_authorized_strategies, has_permission
from apps.web_console.auth.session_manager import get_current_user, require_auth
from apps.web_console.components.comparison_charts import (
    render_equity_comparison,
    render_metrics_table,
    render_portfolio_simulator,
)
from apps.web_console.components.correlation_matrix import render_correlation_heatmap
from apps.web_console.config import FEATURE_STRATEGY_COMPARISON
from apps.web_console.data.strategy_scoped_queries import StrategyScopedDataAccess
from apps.web_console.services.comparison_service import ComparisonService
from apps.web_console.utils.async_helpers import run_async
from apps.web_console.utils.db_pool import get_db_pool, get_redis_client

DEFAULT_LOOKBACK_DAYS = 30
MAX_STRATEGIES = 4
MIN_STRATEGIES = 2


def _default_date_range() -> tuple[date, date]:
    today = date.today()
    return today - timedelta(days=DEFAULT_LOOKBACK_DAYS), today


@require_auth
def main() -> None:
    st.set_page_config(page_title="Strategy Comparison", page_icon="🆚", layout="wide")
    st.title("Strategy Comparison")
    st.caption("Compare strategy performance, correlations, and simulate combined portfolios.")

    if not FEATURE_STRATEGY_COMPARISON:
        st.info("Strategy Comparison is not currently enabled.")
        st.stop()

    user = get_current_user()
    if not has_permission(user, Permission.VIEW_PNL):
        st.error("Permission denied: VIEW_PNL is required to access this page.")
        st.stop()

    authorized_strategies = get_authorized_strategies(user)
    if len(authorized_strategies) < MIN_STRATEGIES:
        st.warning("You need access to at least two strategies to compare performance.")
        st.stop()

    st.subheader("Inputs")
    col1, col2 = st.columns([2, 1])
    with col1:
        default_selection = authorized_strategies[:MAX_STRATEGIES]
        selected = st.multiselect(
            "Select 2-4 strategies",
            options=authorized_strategies,
            default=default_selection,
            max_selections=MAX_STRATEGIES,
        )
    with col2:
        date_range = st.date_input(
            "Date Range",
            value=_default_date_range(),
        )
        # st.date_input with range returns tuple, extract both dates
        if isinstance(date_range, tuple) and len(date_range) == 2:
            date_from, date_to = date_range
        else:
            st.error("Please select both start and end dates.")
            st.stop()

    if len(selected) < MIN_STRATEGIES:
        st.info("Select at least two strategies to run the comparison.")
        st.stop()

    if date_from > date_to:
        st.error("Start date cannot be after end date.")
        st.stop()

    # Config guard: check DB/Redis availability before proceeding
    db_pool = get_db_pool()
    if db_pool is None:
        st.warning(
            "Database connection not configured. "
            "Please ensure DATABASE_URL is set to enable strategy comparison."
        )
        st.stop()

    scoped_access = StrategyScopedDataAccess(
        db_pool=db_pool,
        redis_client=get_redis_client(),  # Redis is optional for caching
        user=dict(user),
    )
    comparison_service = ComparisonService(scoped_access)

    with st.spinner("Loading comparison data..."):
        try:
            data = run_async(
                comparison_service.get_comparison_data(selected, date_from, date_to),
                timeout=30,
            )
        except PermissionError:
            st.error("You do not have permission to access one or more selected strategies.")
            st.stop()
        except Exception as exc:  # pragma: no cover - defensive
            st.error(f"Failed to load comparison data: {exc}")
            st.stop()

    # Warn user if data may be truncated due to long date range
    if data.get("truncation_warning"):
        st.warning(data["truncation_warning"])

    render_metrics_table(data.get("metrics", {}))
    render_equity_comparison(data.get("equity_curves", []))

    # Correlation matrix may be empty DataFrame if insufficient data
    correlation_matrix = data.get("correlation_matrix")
    if correlation_matrix is not None:
        render_correlation_heatmap(correlation_matrix)

    # Combined portfolio simulator
    weights = render_portfolio_simulator(selected, data.get("default_weights", {}))
    if not weights:
        st.stop()

    valid, validation_msg = ComparisonService.validate_weights(weights)
    if not valid:
        st.error(validation_msg)
        st.stop()

    # pnl_frame is always present in data (may be empty DataFrame)
    pnl_frame = data.get("pnl_frame")
    if pnl_frame is None:
        st.error("No P&L data available for the selected strategies.")
        st.stop()

    combined = comparison_service.compute_combined_portfolio(weights, pnl_frame)
    st.subheader("Combined Portfolio Equity")
    if combined.get("equity_curve"):
        import pandas as pd

        df = pd.DataFrame(combined["equity_curve"])
        st.line_chart(df.set_index("date")["equity"])
    else:
        st.info("No combined equity data available for the selected inputs.")

    st.caption(
        "Weights must sum to 1.0. Correlations are computed from daily P&L over the selected range."
    )


if __name__ == "__main__":
    main()
