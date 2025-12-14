"""UI helpers for strategy comparison charts (T6.4b)."""

from __future__ import annotations

from typing import Any

import pandas as pd
import plotly.express as px
import streamlit as st


def render_equity_comparison(equity_curves: list[dict[str, Any]]) -> None:
    """Render overlaid equity curves for each strategy."""
    st.subheader("Equity Curve Comparison")
    if not equity_curves:
        st.info("No equity data available for the selected range.")
        return

    records: list[dict[str, Any]] = []
    for curve in equity_curves:
        for point in curve.get("equity", []):
            records.append(
                {
                    "strategy": curve.get("strategy_id"),
                    "date": point.get("date"),
                    "equity": point.get("equity", 0.0),
                }
            )

    if not records:
        st.info("No equity data available for the selected range.")
        return

    df = pd.DataFrame(records)
    fig = px.line(
        df,
        x="date",
        y="equity",
        color="strategy",
        title="Equity Curves",
    )
    fig.update_layout(legend_title_text="Strategy")
    st.plotly_chart(fig, use_container_width=True)


def render_metrics_table(metrics: dict[str, dict[str, float]]) -> None:
    """Render side-by-side metrics table."""
    st.subheader("Performance Metrics")
    if not metrics:
        st.info("No metrics available.")
        return

    df = (
        pd.DataFrame(metrics)
        .rename(
            index={
                "total_return": "Total Return",
                "volatility": "Volatility",
                "sharpe": "Sharpe Ratio",
                "max_drawdown": "Max Drawdown",
            }
        )
        .T
    )
    st.dataframe(df, use_container_width=True)


def render_portfolio_simulator(
    strategies: list[str], default_weights: dict[str, float]
) -> dict[str, float]:
    """Render weight sliders and return the selected weights."""
    st.subheader("Combined Portfolio Simulator")
    if not strategies:
        st.info("Select at least one strategy to simulate a combined portfolio.")
        return {}

    weights: dict[str, float] = {}
    cols = st.columns(min(4, len(strategies)))
    for idx, strategy_id in enumerate(strategies):
        col = cols[idx % len(cols)]
        with col:
            weights[strategy_id] = st.slider(
                label=f"{strategy_id} weight",
                min_value=0.0,
                max_value=1.0,
                value=float(default_weights.get(strategy_id, 0.0)),
                step=0.05,
            )

    total = sum(weights.values())
    st.caption(f"Total weight: {total:.2f} (must equal 1.0)")
    return weights


__all__ = [
    "render_equity_comparison",
    "render_metrics_table",
    "render_portfolio_simulator",
]
