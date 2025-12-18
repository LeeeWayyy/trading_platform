"""Backtest results display component.

Combines metrics summary, visualization charts, and export functionality.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pandas as pd
import streamlit as st

from apps.web_console.auth.permissions import Permission, has_permission
from apps.web_console.components.drawdown_chart import render_drawdown_chart
from apps.web_console.components.equity_curve_chart import render_equity_curve
from apps.web_console.components.ic_timeseries_chart import render_ic_timeseries

if TYPE_CHECKING:
    from libs.alpha.research_platform import BacktestResult


def render_metrics_summary(result: BacktestResult) -> None:
    """Render key metrics in a summary grid.

    Args:
        result: BacktestResult containing computed metrics
    """
    col1, col2, col3, col4, col5 = st.columns(5)

    with col1:
        # Use explicit None check to avoid hiding legitimate zero values
        st.metric(
            "Mean IC",
            f"{result.mean_ic:.4f}" if result.mean_ic is not None else "N/A",
            help="Average Information Coefficient across all days",
        )

    with col2:
        st.metric(
            "ICIR",
            f"{result.icir:.2f}" if result.icir is not None else "N/A",
            help="Information Coefficient / Std(IC) - signal consistency",
        )

    with col3:
        hit_rate_pct = (
            f"{result.hit_rate * 100:.1f}%" if result.hit_rate is not None else "N/A"
        )
        st.metric(
            "Hit Rate",
            hit_rate_pct,
            help="Percentage of days with positive IC",
        )

    with col4:
        coverage_pct = (
            f"{result.coverage * 100:.1f}%" if result.coverage is not None else "N/A"
        )
        st.metric(
            "Coverage",
            coverage_pct,
            help="Average percentage of universe with valid signals",
        )

    with col5:
        turnover = result.turnover_result.average_turnover if result.turnover_result else None
        st.metric(
            "Avg Turnover",
            f"{turnover:.2%}" if turnover is not None else "N/A",
            help="Average daily portfolio turnover",
        )


def render_export_buttons(result: BacktestResult, user_info: dict[str, Any]) -> None:
    """Render export buttons if user has EXPORT_DATA permission.

    Args:
        result: BacktestResult to export
        user_info: User info dict with role for permission check
    """
    if not has_permission(user_info, Permission.EXPORT_DATA):
        st.info("Export requires EXPORT_DATA permission (Operator or Admin role).")
        return

    st.subheader("Export Data")

    col1, col2, col3 = st.columns(3)

    with col1:
        # Signals CSV
        if result.daily_signals is not None and result.daily_signals.height > 0:
            signals_csv = result.daily_signals.write_csv()
            st.download_button(
                "Download Signals CSV",
                signals_csv,
                f"signals_{result.backtest_id}.csv",
                "text/csv",
                help="Daily signals: date, permno, signal",
            )

    with col2:
        # IC Series CSV
        if result.daily_ic is not None and result.daily_ic.height > 0:
            ic_csv = result.daily_ic.write_csv()
            st.download_button(
                "Download IC Series CSV",
                ic_csv,
                f"ic_series_{result.backtest_id}.csv",
                "text/csv",
                help="Daily IC: date, ic, rank_ic",
            )

    with col3:
        # Metrics Summary JSON
        metrics_dict = {
            "backtest_id": result.backtest_id,
            "alpha_name": result.alpha_name,
            "start_date": str(result.start_date),
            "end_date": str(result.end_date),
            "mean_ic": result.mean_ic,
            "icir": result.icir,
            "hit_rate": result.hit_rate,
            "coverage": result.coverage,
            "n_days": result.n_days,
            "n_symbols_avg": result.n_symbols_avg,
            "snapshot_id": result.snapshot_id,
            "dataset_version_ids": result.dataset_version_ids,
        }
        if result.turnover_result:
            metrics_dict["average_turnover"] = result.turnover_result.average_turnover

        metrics_json = json.dumps(metrics_dict, indent=2, default=str)
        st.download_button(
            "Download Metrics JSON",
            metrics_json,
            f"metrics_{result.backtest_id}.json",
            "application/json",
            help="Full metrics summary with reproducibility metadata",
        )


def render_backtest_result(
    result: BacktestResult,
    user_info: dict[str, Any] | None = None,
    show_export: bool = True,
) -> None:
    """Render complete backtest result with metrics and charts.

    Args:
        result: BacktestResult to display
        user_info: User info for permission checks (for export)
        show_export: Whether to show export buttons
    """
    # Header with basic info
    st.subheader(f"Backtest: {result.alpha_name}")
    st.caption(
        f"Period: {result.start_date} to {result.end_date} | "
        f"Days: {result.n_days} | "
        f"Avg Symbols: {result.n_symbols_avg:.0f}"
    )

    # Metrics summary
    render_metrics_summary(result)

    st.divider()

    # Charts
    tab1, tab2, tab3 = st.tabs(["Equity Curve", "Drawdown", "IC Analysis"])

    with tab1:
        if result.daily_portfolio_returns is not None:
            render_equity_curve(result.daily_portfolio_returns)
        else:
            st.info("No portfolio returns data available")

    with tab2:
        if result.daily_portfolio_returns is not None:
            render_drawdown_chart(result.daily_portfolio_returns)
        else:
            st.info("No portfolio returns data available")

    with tab3:
        if result.daily_ic is not None:
            render_ic_timeseries(result.daily_ic)
        else:
            st.info("No IC data available")

    # Export buttons (if permitted)
    if show_export and user_info is not None:
        st.divider()
        render_export_buttons(result, user_info)


def render_comparison_table(
    results: list[BacktestResult],
    selected_ids: list[str],
) -> None:
    """Render side-by-side comparison of multiple backtests.

    Args:
        results: List of BacktestResult objects
        selected_ids: List of backtest IDs to compare
    """
    if len(selected_ids) < 2:
        st.info("Select at least 2 backtests to compare")
        return

    # Filter to selected results
    selected_results = [r for r in results if r.backtest_id in selected_ids]

    if len(selected_results) < 2:
        st.warning("Not enough backtests found for comparison")
        return

    # Build comparison data
    comparison_data = []
    for result in selected_results:
        turnover = (
            result.turnover_result.average_turnover if result.turnover_result else None
        )
        comparison_data.append(
            {
                "Alpha": result.alpha_name,
                "Period": f"{result.start_date} - {result.end_date}",
                "Mean IC": result.mean_ic,
                "ICIR": result.icir,
                "Hit Rate": result.hit_rate,
                "Coverage": result.coverage,
                "Avg Turnover": turnover,
                "Days": result.n_days,
            }
        )

    df = pd.DataFrame(comparison_data)

    # Format for display
    st.dataframe(
        df.style.format(
            {
                "Mean IC": "{:.4f}",
                "ICIR": "{:.2f}",
                "Hit Rate": "{:.1%}",
                "Coverage": "{:.1%}",
                "Avg Turnover": "{:.2%}",
            },
            na_rep="N/A",
        ),
        use_container_width=True,
        hide_index=True,
    )


__all__ = [
    "render_metrics_summary",
    "render_export_buttons",
    "render_backtest_result",
    "render_comparison_table",
]
