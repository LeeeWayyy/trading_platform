"""Strategy Comparison Tool page for NiceGUI web console (P5T8).

Compares strategy performance, correlations, and simulates combined portfolios.

Features:
    - Multi-strategy selection (2-4 strategies)
    - Date range picker
    - Performance metrics comparison table
    - Equity curve comparison chart
    - Correlation heatmap
    - Portfolio simulator with weight sliders

PARITY: Mirrors UI layout from apps/web_console/pages/compare.py

NOTE: This page uses demo mode with placeholder data when services are unavailable.
Backend service integration (ComparisonService) requires database configuration.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

import pandas as pd
import plotly.graph_objects as go
from nicegui import run, ui

from apps.web_console_ng.auth.middleware import get_current_user, requires_auth
from apps.web_console_ng.config import FEATURE_STRATEGY_COMPARISON
from apps.web_console_ng.core.database import get_db_pool
from apps.web_console_ng.ui.layout import main_layout
from libs.platform.web_console_auth.permissions import (
    Permission,
    get_authorized_strategies,
    has_permission,
)

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

logger = logging.getLogger(__name__)

DEFAULT_LOOKBACK_DAYS = 30
MAX_STRATEGIES = 4
MIN_STRATEGIES = 2


def _default_date_range() -> tuple[date, date]:
    """Get default date range (last 30 days)."""
    today = date.today()
    return today - timedelta(days=DEFAULT_LOOKBACK_DAYS), today


@ui.page("/compare")
@requires_auth
@main_layout
async def strategy_comparison_page() -> None:
    """Strategy Comparison Tool page."""
    user = get_current_user()

    # Page title
    ui.label("Strategy Comparison").classes("text-2xl font-bold mb-2")
    ui.label("Compare strategy performance, correlations, and simulate combined portfolios.").classes(
        "text-gray-600 mb-4"
    )

    # Feature flag check
    if not FEATURE_STRATEGY_COMPARISON:
        with ui.card().classes("w-full p-6"):
            ui.label("Strategy Comparison is not currently enabled.").classes(
                "text-gray-500 text-center"
            )
            ui.label(
                "Set FEATURE_STRATEGY_COMPARISON=true to enable this feature."
            ).classes("text-gray-400 text-sm text-center")
        return

    # Permission check
    if not has_permission(user, Permission.VIEW_PNL):
        ui.notify("Permission denied: VIEW_PNL required", type="negative")
        with ui.card().classes("w-full p-6"):
            ui.label("Permission denied: VIEW_PNL is required to access this page.").classes(
                "text-red-500 text-center"
            )
        return

    # Get authorized strategies
    authorized_strategies = get_authorized_strategies(user)
    if len(authorized_strategies) < MIN_STRATEGIES:
        with ui.card().classes("w-full p-6"):
            ui.label("You need access to at least two strategies to compare performance.").classes(
                "text-amber-600 text-center"
            )
        return

    # Get async db pool
    async_pool = get_db_pool()

    if async_pool is None:
        # Demo mode banner
        with ui.card().classes("w-full p-3 mb-4 bg-amber-50 border border-amber-300"):
            with ui.row().classes("items-center gap-2"):
                ui.icon("info", color="amber-700")
                ui.label(
                    "Demo Mode: Database not configured. Configure DATABASE_URL."
                ).classes("text-amber-700")

        _render_demo_mode(authorized_strategies)
        return

    # Real mode with database
    await _render_comparison_tool(user, authorized_strategies, async_pool)


async def _render_comparison_tool(
    user: dict[str, Any],
    authorized_strategies: list[str],
    db_pool: AsyncConnectionPool,
) -> None:
    """Render the full comparison tool with real service data."""
    # Input controls
    with ui.card().classes("w-full mb-4 p-4"):
        ui.label("Inputs").classes("text-lg font-bold mb-2")

        with ui.row().classes("w-full gap-4 flex-wrap items-end"):
            # Strategy multi-select
            default_selection = authorized_strategies[:MAX_STRATEGIES]
            strategy_select = ui.select(
                label=f"Select {MIN_STRATEGIES}-{MAX_STRATEGIES} strategies",
                options=authorized_strategies,
                value=default_selection,
                multiple=True,
            ).classes("w-64")

            # Date range
            date_from, date_to = _default_date_range()
            ui.label("From:").classes("text-sm")
            from_input = ui.date(value=str(date_from)).classes("w-40")
            ui.label("To:").classes("text-sm")
            to_input = ui.date(value=str(date_to)).classes("w-40")

            # Compare button
            compare_btn = ui.button("Compare", icon="compare_arrows").classes(
                "self-end"
            ).props("color=primary")

    # Results container
    results_container = ui.column().classes("w-full")

    async def run_comparison() -> None:
        results_container.clear()

        selected = strategy_select.value or []
        if len(selected) < MIN_STRATEGIES:
            with results_container:
                ui.label(f"Select at least {MIN_STRATEGIES} strategies to run the comparison.").classes(
                    "text-amber-600 p-4"
                )
            return

        if len(selected) > MAX_STRATEGIES:
            with results_container:
                ui.label(f"Select at most {MAX_STRATEGIES} strategies.").classes(
                    "text-amber-600 p-4"
                )
            return

        # Parse dates
        try:
            date_from = date.fromisoformat(from_input.value) if from_input.value else _default_date_range()[0]
            date_to = date.fromisoformat(to_input.value) if to_input.value else _default_date_range()[1]
        except ValueError:
            with results_container:
                ui.label("Invalid date format.").classes("text-red-500 p-4")
            return

        if date_from > date_to:
            with results_container:
                ui.label("Start date cannot be after end date.").classes("text-red-500 p-4")
            return

        with results_container:
            # Loading indicator
            loading = ui.spinner("dots", size="lg")

            try:
                # Fetch comparison data
                data = await _fetch_comparison_data(user, selected, date_from, date_to, db_pool)
                loading.delete()

                if data.get("truncation_warning"):
                    ui.label(data["truncation_warning"]).classes(
                        "text-amber-600 p-2 mb-2 bg-amber-50 rounded"
                    )

                # Render results
                await _render_comparison_results(selected, data)

            except PermissionError:
                loading.delete()
                ui.label("You do not have permission to access one or more selected strategies.").classes(
                    "text-red-500 p-4"
                )
            except (ConnectionError, OSError) as exc:
                loading.delete()
                logger.error(
                    "comparison_data_db_connection_failed",
                    extra={
                        "user_id": user.get("user_id"),
                        "strategies": selected,
                        "error": str(exc),
                    },
                    exc_info=True,
                )
                ui.label("Failed to load comparison data: Database connection error").classes(
                    "text-red-500 p-4"
                )
                ui.notify("Database connection error", type="negative")
            except (ValueError, KeyError, TypeError) as exc:
                loading.delete()
                logger.error(
                    "comparison_data_data_error",
                    extra={
                        "user_id": user.get("user_id"),
                        "strategies": selected,
                        "error": str(exc),
                    },
                    exc_info=True,
                )
                ui.label("Failed to load comparison data: Data processing error").classes(
                    "text-red-500 p-4"
                )
                ui.notify("Data processing error", type="negative")

    compare_btn.on_click(run_comparison)

    # Initial comparison
    await run_comparison()


async def _fetch_comparison_data(
    user: dict[str, Any],
    strategy_ids: list[str],
    date_from: date,
    date_to: date,
    db_pool: AsyncConnectionPool,
) -> dict[str, Any]:
    """Fetch comparison data using ComparisonService."""
    from libs.web_console_data.strategy_scoped_queries import StrategyScopedDataAccess
    from libs.web_console_services.comparison_service import ComparisonService

    # Create scoped access
    scoped_access = StrategyScopedDataAccess(
        db_pool=db_pool,
        redis_client=None,  # Redis is optional
        user=dict(user),
    )

    comparison_service = ComparisonService(scoped_access)

    # Fetch data (this is async)
    data = await comparison_service.get_comparison_data(strategy_ids, date_from, date_to)
    return data


async def _render_comparison_results(
    strategy_ids: list[str],
    data: dict[str, Any],
) -> None:
    """Render comparison results (metrics, charts, simulator)."""
    metrics = data.get("metrics", {})
    equity_curves = data.get("equity_curves", [])
    correlation_matrix = data.get("correlation_matrix")
    default_weights = data.get("default_weights", {})
    pnl_frame = data.get("pnl_frame", pd.DataFrame())

    # Metrics table
    _render_metrics_table(metrics)

    # Equity comparison chart
    _render_equity_comparison(equity_curves)

    # Correlation heatmap
    if correlation_matrix is not None and not correlation_matrix.empty:
        _render_correlation_heatmap(correlation_matrix)

    # Portfolio simulator
    await _render_portfolio_simulator(strategy_ids, default_weights, pnl_frame)


def _render_metrics_table(metrics: dict[str, dict[str, float]]) -> None:
    """Render metrics comparison table."""
    with ui.card().classes("w-full mb-4 p-4"):
        ui.label("Performance Metrics").classes("text-lg font-bold mb-2")

        if not metrics:
            ui.label("No metrics available.").classes("text-gray-500 p-4")
            return

        # Build table
        columns = [
            {"name": "strategy", "label": "Strategy", "field": "strategy", "sortable": True},
            {"name": "total_return", "label": "Total Return", "field": "total_return", "sortable": True},
            {"name": "volatility", "label": "Volatility", "field": "volatility", "sortable": True},
            {"name": "sharpe", "label": "Sharpe", "field": "sharpe", "sortable": True},
            {
                "name": "max_drawdown",
                "label": "Max Drawdown %",
                "field": "max_drawdown",
                "sortable": True,
            },
        ]

        rows = []
        for strategy_id, m in metrics.items():
            rows.append({
                "strategy": strategy_id,
                "total_return": f"${m.get('total_return', 0):,.2f}",
                "volatility": f"${m.get('volatility', 0):,.2f}",
                "sharpe": f"{m.get('sharpe', 0):.2f}",
                "max_drawdown": f"${m.get('max_drawdown', 0):,.2f}",
            })

        ui.table(columns=columns, rows=rows).classes("w-full")


def _render_equity_comparison(equity_curves: list[dict[str, Any]]) -> None:
    """Render equity curve comparison chart."""
    with ui.card().classes("w-full mb-4 p-4"):
        ui.label("Equity Curves").classes("text-lg font-bold mb-2")

        if not equity_curves:
            ui.label("No equity data available.").classes("text-gray-500 p-4")
            return

        fig = go.Figure()

        colors = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728"]
        for i, curve in enumerate(equity_curves):
            strategy_id = curve.get("strategy_id", f"Strategy {i+1}")
            equity_data = curve.get("equity", [])

            if equity_data:
                dates = [e["date"] for e in equity_data]
                values = [e["equity"] for e in equity_data]

                fig.add_trace(
                    go.Scatter(
                        x=dates,
                        y=values,
                        name=strategy_id,
                        mode="lines",
                        line={"color": colors[i % len(colors)], "width": 2},
                    )
                )

        fig.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.5)

        fig.update_layout(
            title="Cumulative P&L Comparison",
            xaxis_title="Date",
            yaxis_title="Equity ($)",
            legend={"orientation": "h", "yanchor": "bottom", "y": 1.02},
            height=400,
            hovermode="x unified",
        )

        ui.plotly(fig).classes("w-full")


def _render_correlation_heatmap(corr_matrix: pd.DataFrame) -> None:
    """Render correlation matrix heatmap."""
    with ui.card().classes("w-full mb-4 p-4"):
        ui.label("Correlation Matrix").classes("text-lg font-bold mb-2")

        if corr_matrix.empty:
            ui.label("Insufficient data for correlation.").classes("text-gray-500 p-4")
            return

        fig = go.Figure(
            data=go.Heatmap(
                z=corr_matrix.values,
                x=corr_matrix.columns.tolist(),
                y=corr_matrix.index.tolist(),
                colorscale="RdYlGn",
                zmin=-1,
                zmax=1,
                text=[[f"{v:.2f}" for v in row] for row in corr_matrix.values],
                texttemplate="%{text}",
                hovertemplate="Strategy: %{x}<br>Strategy: %{y}<br>Correlation: %{z:.2f}<extra></extra>",
            )
        )

        fig.update_layout(
            title="Daily P&L Correlation",
            height=400,
            margin={"l": 80, "r": 40, "t": 60, "b": 80},
        )

        ui.plotly(fig).classes("w-full")


async def _render_portfolio_simulator(
    strategy_ids: list[str],
    default_weights: dict[str, float],
    pnl_frame: pd.DataFrame,
) -> None:
    """Render portfolio simulator with weight sliders."""
    from libs.web_console_services.comparison_service import ComparisonService

    with ui.card().classes("w-full mb-4 p-4"):
        ui.label("Portfolio Simulator").classes("text-lg font-bold mb-2")
        ui.label("Adjust weights to simulate a combined portfolio.").classes(
            "text-gray-600 text-sm mb-4"
        )

        # Weight sliders
        sliders: dict[str, ui.slider] = {}
        with ui.row().classes("w-full gap-4 flex-wrap"):
            for strategy_id in strategy_ids:
                default_weight = default_weights.get(strategy_id, 1.0 / len(strategy_ids))
                with ui.column().classes("w-48"):
                    ui.label(strategy_id).classes("text-sm font-medium")
                    slider = ui.slider(
                        min=0,
                        max=1,
                        step=0.05,
                        value=default_weight,
                    ).classes("w-full")
                    weight_label = ui.label(f"{default_weight:.0%}").classes("text-xs text-gray-500")
                    slider.on_value_change(
                        lambda e, lbl=weight_label: lbl.set_text(f"{e.value:.0%}")
                    )
                    sliders[strategy_id] = slider

        # Simulate button
        simulate_btn = ui.button("Simulate Portfolio", icon="calculate").classes("mt-4")

        # Results container
        sim_results = ui.column().classes("w-full mt-4")

        async def simulate() -> None:
            sim_results.clear()

            # Gather weights
            weights = {sid: slider.value for sid, slider in sliders.items()}

            # Validate weights
            valid, msg = ComparisonService.validate_weights(weights)

            with sim_results:
                if not valid:
                    ui.label(msg).classes("text-red-500 p-2")
                    return

                # Compute combined portfolio (offload CPU-intensive work)
                try:
                    service = ComparisonService(None)  # Service methods are static
                    combined = await run.cpu_bound(
                        service.compute_combined_portfolio, weights, pnl_frame
                    )

                    # Display results
                    with ui.row().classes("gap-4 mb-4"):
                        _metric_card("Total Return", f"${combined.get('total_return', 0):,.2f}")
                        _metric_card("Volatility", f"${combined.get('volatility', 0):,.2f}")
                        _metric_card("Max Drawdown", f"${combined.get('max_drawdown', 0):,.2f}")

                    # Equity curve
                    equity_curve = combined.get("equity_curve", [])
                    if equity_curve:
                        fig = go.Figure()
                        fig.add_trace(
                            go.Scatter(
                                x=[e["date"] for e in equity_curve],
                                y=[e["equity"] for e in equity_curve],
                                name="Combined Portfolio",
                                mode="lines",
                                line={"color": "#1f77b4", "width": 2},
                                fill="tozeroy",
                                fillcolor="rgba(31, 119, 180, 0.1)",
                            )
                        )
                        fig.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.5)
                        fig.update_layout(
                            title="Combined Portfolio Equity",
                            xaxis_title="Date",
                            yaxis_title="Equity ($)",
                            height=350,
                        )
                        ui.plotly(fig).classes("w-full")
                    else:
                        ui.label("No equity data available for the selected inputs.").classes(
                            "text-gray-500 p-4"
                        )

                except (ConnectionError, OSError) as exc:
                    logger.error(
                        "portfolio_computation_db_connection_failed",
                        extra={
                            "weights": weights,
                            "error": str(exc),
                        },
                        exc_info=True,
                    )
                    ui.label("Failed to compute portfolio: Database connection error").classes("text-red-500 p-2")
                    ui.notify("Database connection error", type="negative")
                except (ValueError, KeyError, TypeError) as exc:
                    logger.error(
                        "portfolio_computation_data_error",
                        extra={
                            "weights": weights,
                            "error": str(exc),
                        },
                        exc_info=True,
                    )
                    ui.label("Failed to compute portfolio: Data processing error").classes("text-red-500 p-2")
                    ui.notify("Data processing error", type="negative")

        simulate_btn.on_click(simulate)

        ui.label("Weights must sum to 1.0.").classes("text-gray-500 text-xs mt-2")


def _metric_card(label: str, value: str) -> None:
    """Render a small metric card."""
    with ui.card().classes("p-3 min-w-32"):
        ui.label(label).classes("text-xs text-gray-500")
        ui.label(value).classes("text-lg font-bold")


def _render_demo_mode(authorized_strategies: list[str]) -> None:
    """Render demo mode with placeholder data."""
    with ui.card().classes("w-full mb-4 p-4"):
        ui.label("Inputs").classes("text-lg font-bold mb-2")

        with ui.row().classes("w-full gap-4 flex-wrap items-end"):
            ui.select(
                label="Select 2-4 strategies",
                options=authorized_strategies,
                value=authorized_strategies[:2],
                multiple=True,
            ).classes("w-64")

            date_from, date_to = _default_date_range()
            ui.label("From:").classes("text-sm")
            ui.date(value=str(date_from)).classes("w-40")
            ui.label("To:").classes("text-sm")
            ui.date(value=str(date_to)).classes("w-40")

            ui.button("Compare", icon="compare_arrows").classes("self-end").props(
                "color=primary disable"
            )

    # Demo metrics
    with ui.card().classes("w-full mb-4 p-4"):
        ui.label("Performance Metrics").classes("text-lg font-bold mb-2")

        demo_metrics = [
            {
                "strategy": "strategy_a",
                "total_return": "$12,500.00",
                "volatility": "$850.00",
                "sharpe": "1.45",
                "max_drawdown": "-3.20%",
            },
            {
                "strategy": "strategy_b",
                "total_return": "$8,200.00",
                "volatility": "$620.00",
                "sharpe": "1.12",
                "max_drawdown": "-2.10%",
            },
        ]

        columns = [
            {"name": "strategy", "label": "Strategy", "field": "strategy"},
            {"name": "total_return", "label": "Total Return", "field": "total_return"},
            {"name": "volatility", "label": "Volatility", "field": "volatility"},
            {"name": "sharpe", "label": "Sharpe", "field": "sharpe"},
            {"name": "max_drawdown", "label": "Max Drawdown %", "field": "max_drawdown"},
        ]

        ui.table(columns=columns, rows=demo_metrics).classes("w-full")

    ui.label("Configure DATABASE_URL to enable real comparisons.").classes(
        "text-gray-500 text-center mt-4"
    )


__all__ = ["strategy_comparison_page"]
