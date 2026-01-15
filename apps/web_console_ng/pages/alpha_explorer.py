"""Alpha Signal Explorer page for NiceGUI web console (P5T8).

Provides browsing and analysis of alpha signals with IC visualization.

Features:
    - Signal list with filtering and pagination
    - Signal metrics display (IC, ICIR, hit rate, etc.)
    - IC time series chart
    - Decay curve visualization
    - Signal correlation matrix

PARITY: Mirrors UI layout from apps/web_console/pages/alpha_explorer.py

NOTE: This page uses demo mode with placeholder data when services are unavailable.
Backend service integration (AlphaExplorerService) is available but may require
MODEL_REGISTRY_DIR configuration.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd
from nicegui import run, ui

from apps.web_console_ng.auth.middleware import get_current_user, requires_auth
from apps.web_console_ng.components.correlation_matrix import render_correlation_matrix
from apps.web_console_ng.components.decay_curve import render_decay_curve
from apps.web_console_ng.components.ic_chart import render_ic_chart
from apps.web_console_ng.config import FEATURE_ALPHA_EXPLORER
from apps.web_console_ng.ui.layout import main_layout
from libs.platform.web_console_auth.permissions import Permission, has_permission

if TYPE_CHECKING:
    from libs.web_console_services.alpha_explorer_service import (
        AlphaExplorerService,
        SignalSummary,
    )

logger = logging.getLogger(__name__)

DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100


@lru_cache(maxsize=1)
def _get_alpha_service() -> AlphaExplorerService | None:
    """Get or create AlphaExplorerService instance.

    Uses lru_cache to avoid recreating the service on each request.
    Returns None if service initialization fails.
    """
    try:
        from libs.models.models.registry import ModelRegistry
        from libs.trading.alpha.metrics import AlphaMetricsAdapter
        from libs.web_console_services.alpha_explorer_service import AlphaExplorerService

        registry_dir = Path(os.getenv("MODEL_REGISTRY_DIR", "data/models"))
        registry = ModelRegistry(registry_dir=registry_dir)
        metrics_adapter = AlphaMetricsAdapter()
        return AlphaExplorerService(registry, metrics_adapter)
    except FileNotFoundError as e:
        logger.error(
            "Failed to initialize AlphaExplorerService - model registry directory not found",
            extra={"error": str(e), "page": "alpha_explorer"},
            exc_info=True,
        )
        return None
    except ImportError as e:
        logger.error(
            "Failed to initialize AlphaExplorerService - missing dependencies",
            extra={"error": str(e), "page": "alpha_explorer"},
            exc_info=True,
        )
        return None
    except Exception as e:
        logger.error(
            "Failed to initialize AlphaExplorerService",
            extra={"error": str(e), "page": "alpha_explorer"},
            exc_info=True,
        )
        return None


@ui.page("/alpha-explorer")
@requires_auth
@main_layout
async def alpha_explorer_page() -> None:
    """Alpha Signal Explorer page."""
    user = get_current_user()

    # Page title
    ui.label("Alpha Signal Explorer").classes("text-2xl font-bold mb-4")

    # Feature flag check
    if not FEATURE_ALPHA_EXPLORER:
        with ui.card().classes("w-full p-6"):
            ui.label("Alpha Explorer feature is disabled.").classes(
                "text-gray-500 text-center"
            )
            ui.label(
                "Set FEATURE_ALPHA_EXPLORER=true to enable this feature."
            ).classes("text-gray-400 text-sm text-center")
        return

    # Permission check
    if not has_permission(user, Permission.VIEW_ALPHA_SIGNALS):
        ui.notify("Permission denied: VIEW_ALPHA_SIGNALS required", type="negative")
        with ui.card().classes("w-full p-6"):
            ui.label("You don't have permission to view alpha signals.").classes(
                "text-red-500 text-center"
            )
        return

    # Try to get service
    service = await run.io_bound(_get_alpha_service)

    if service is None:
        # Demo mode banner
        with ui.card().classes("w-full p-3 mb-4 bg-amber-50 border border-amber-300"):
            with ui.row().classes("items-center gap-2"):
                ui.icon("info", color="amber-700")
                ui.label(
                    "Demo Mode: Service unavailable. Configure MODEL_REGISTRY_DIR."
                ).classes("text-amber-700")

        _render_demo_mode()
        return

    # Real mode with service
    await _render_alpha_explorer(service, user)


async def _render_alpha_explorer(service: AlphaExplorerService, user: dict[str, Any]) -> None:
    """Render the full alpha explorer with real service data."""
    from libs.models.models.types import ModelStatus

    # State for filters and pagination (explicit Any typing for mypy)
    state: dict[str, Any] = {
        "status_filter": "All",
        "min_ic": 0.0,
        "max_ic": 1.0,
        "page_size": DEFAULT_PAGE_SIZE,
        "page_index": 1,
        "selected_signal_idx": 0,
        "signals": [],
        "total": 0,
    }

    # Sidebar filters
    with ui.card().classes("w-full mb-4 p-4"):
        ui.label("Filters").classes("text-lg font-bold mb-2")

        with ui.row().classes("w-full gap-4 flex-wrap"):
            # Status filter
            status_options = ["All", "staged", "production", "archived", "failed"]
            status_select = ui.select(
                label="Status",
                options=status_options,
                value=state["status_filter"],
            ).classes("w-36")

            # IC range
            min_ic_input = ui.number(
                label="Min IC",
                value=state["min_ic"],
                step=0.01,
                min=0.0,
                max=1.0,
            ).classes("w-24")

            max_ic_input = ui.number(
                label="Max IC",
                value=state["max_ic"],
                step=0.01,
                min=0.0,
                max=1.0,
            ).classes("w-24")

            # Pagination
            page_size_select = ui.select(
                label="Page Size",
                options=[25, 50, 100],
                value=state["page_size"],
            ).classes("w-24")

            page_input = ui.number(
                label="Page",
                value=state["page_index"],
                min=1,
                step=1,
            ).classes("w-20")

            # Apply button
            apply_btn = ui.button("Apply Filters", icon="filter_list").classes(
                "self-end"
            )

    # Results container
    results_container = ui.column().classes("w-full")

    @ui.refreshable  # type: ignore[arg-type]
    async def render_results() -> None:
        """Render signal list and details."""
        results_container.clear()

        with results_container:
            # Fetch signals
            status_value = status_select.value
            status_filter = (
                ModelStatus(status_value) if status_value != "All" else None
            )
            min_ic = min_ic_input.value if min_ic_input.value > 0 else None
            max_ic = max_ic_input.value if max_ic_input.value < 1 else None
            page_size = min(int(page_size_select.value), MAX_PAGE_SIZE)
            offset = (int(page_input.value) - 1) * page_size

            try:
                signals, total = await run.io_bound(
                    service.list_signals,
                    status=status_filter,
                    min_ic=min_ic,
                    max_ic=max_ic,
                    limit=page_size,
                    offset=offset,
                )
            except FileNotFoundError as e:
                logger.error(
                    "Failed to fetch signals - model data not found",
                    extra={"error": str(e), "page": "alpha_explorer"},
                    exc_info=True,
                )
                ui.notify("Model data not found. Check configuration.", type="negative")
                signals, total = [], 0
            except ValueError as e:
                logger.error(
                    "Failed to fetch signals - invalid filter parameters",
                    extra={"error": str(e), "page": "alpha_explorer", "filters": {
                        "status": status_filter,
                        "min_ic": min_ic,
                        "max_ic": max_ic,
                    }},
                    exc_info=True,
                )
                ui.notify("Invalid filter parameters. Please adjust your filters.", type="negative")
                signals, total = [], 0
            except Exception as e:
                logger.error(
                    "Failed to fetch signals",
                    extra={"error": str(e), "page": "alpha_explorer"},
                    exc_info=True,
                )
                ui.notify("Failed to fetch signals. Please try again.", type="negative")
                signals, total = [], 0

            ui.label(f"Showing {len(signals)} of {total} signals").classes(
                "text-gray-600 mb-2"
            )

            if not signals:
                ui.label("No signals found matching filters.").classes(
                    "text-gray-500 p-4"
                )
                return

            # Signal selector
            signal_names = [s.display_name for s in signals]
            signal_select = ui.select(
                label="Select Signal",
                options=dict(enumerate(signal_names)),
                value=0,
            ).classes("w-full max-w-md mb-4")

            # Signal details container
            details_container = ui.column().classes("w-full")

            async def render_signal_details(idx: int) -> None:
                """Render details for selected signal."""
                details_container.clear()

                if idx < 0 or idx >= len(signals):
                    return

                selected = signals[idx]

                with details_container:
                    await _render_signal_details(service, selected, signals)

            # Initial render
            await render_signal_details(0)

            # Update on selection change
            async def on_signal_change(e: Any) -> None:
                await render_signal_details(e.value)

            signal_select.on_value_change(on_signal_change)

    # Initial load
    await render_results()

    # Apply button handler
    apply_btn.on_click(render_results.refresh)


async def _render_signal_details(
    service: AlphaExplorerService,
    selected: SignalSummary,
    all_signals: list[SignalSummary],
) -> None:
    """Render detailed metrics and charts for a selected signal."""
    # Fetch metrics
    try:
        metrics = await run.io_bound(service.get_signal_metrics, selected.signal_id)
    except FileNotFoundError as e:
        logger.error(
            "Failed to fetch signal metrics - metrics file not found",
            extra={"error": str(e), "signal_id": selected.signal_id, "page": "alpha_explorer"},
            exc_info=True,
        )
        ui.notify("Metrics not found for this signal.", type="negative")
        return
    except ValueError as e:
        logger.error(
            "Failed to fetch signal metrics - invalid signal ID",
            extra={"error": str(e), "signal_id": selected.signal_id, "page": "alpha_explorer"},
            exc_info=True,
        )
        ui.notify("Invalid signal ID.", type="negative")
        return
    except Exception as e:
        logger.error(
            "Failed to fetch signal metrics",
            extra={"error": str(e), "signal_id": selected.signal_id, "page": "alpha_explorer"},
            exc_info=True,
        )
        ui.notify("Failed to fetch signal metrics. Please try again.", type="negative")
        return

    # Two-column layout
    with ui.row().classes("w-full gap-4"):
        # Left column: Metrics
        with ui.card().classes("flex-1 p-4"):
            ui.label("Signal Metrics").classes("text-lg font-bold mb-2")

            with ui.row().classes("gap-4 flex-wrap"):
                _metric_card("Mean IC", f"{metrics.mean_ic:.3f}")
                _metric_card("ICIR", f"{metrics.icir:.2f}")
                _metric_card("Hit Rate", f"{metrics.hit_rate:.1%}")

            with ui.row().classes("gap-4 flex-wrap mt-2"):
                _metric_card("Coverage", f"{metrics.coverage:.1%}")
                _metric_card("Turnover", f"{metrics.average_turnover:.1%}")
                if metrics.decay_half_life:
                    _metric_card("Half-life", f"{metrics.decay_half_life:.1f}d")

        # Right column: Actions
        with ui.card().classes("w-64 p-4"):
            ui.label("Quick Actions").classes("text-lg font-bold mb-2")

            # Launch backtest button
            def launch_backtest() -> None:
                ui.navigate.to(f"/backtest?signal={selected.signal_id}")

            ui.button("Launch Backtest", icon="play_arrow", on_click=launch_backtest).classes(
                "w-full mb-2"
            ).props("color=primary")

            # Export metrics button
            def export_metrics() -> None:
                export_data = {
                    "signal_id": metrics.signal_id,
                    "name": metrics.name,
                    "version": metrics.version,
                    "mean_ic": metrics.mean_ic,
                    "icir": metrics.icir,
                    "hit_rate": metrics.hit_rate,
                    "coverage": metrics.coverage,
                    "average_turnover": metrics.average_turnover,
                    "decay_half_life": metrics.decay_half_life,
                    "n_days": metrics.n_days,
                    "start_date": str(metrics.start_date),
                    "end_date": str(metrics.end_date),
                }
                df = pd.DataFrame([export_data])
                csv_bytes = df.to_csv(index=False).encode("utf-8")
                ui.download(
                    csv_bytes,
                    f"alpha_signal_{metrics.signal_id}_metrics.csv",
                )

            ui.button("Export Metrics", icon="download", on_click=export_metrics).classes(
                "w-full"
            )

    ui.separator().classes("my-4")

    # Tabs for charts
    with ui.tabs().classes("w-full") as tabs:
        tab_ic = ui.tab("IC Time Series")
        tab_decay = ui.tab("Decay Curve")
        tab_corr = ui.tab("Correlation")

    with ui.tab_panels(tabs, value=tab_ic).classes("w-full"):
        with ui.tab_panel(tab_ic):
            try:
                ic_data = await run.io_bound(
                    service.get_ic_timeseries, selected.signal_id
                )
                render_ic_chart(ic_data)
            except FileNotFoundError as e:
                logger.error(
                    "Failed to fetch IC data - data file not found",
                    extra={"error": str(e), "signal_id": selected.signal_id, "page": "alpha_explorer"},
                    exc_info=True,
                )
                ui.label("IC data not found for this signal.").classes("text-red-500 p-4")
            except ValueError as e:
                logger.error(
                    "Failed to fetch IC data - invalid data format",
                    extra={"error": str(e), "signal_id": selected.signal_id, "page": "alpha_explorer"},
                    exc_info=True,
                )
                ui.label("Invalid IC data format.").classes("text-red-500 p-4")
            except Exception as e:
                logger.error(
                    "Failed to fetch IC data",
                    extra={"error": str(e), "signal_id": selected.signal_id, "page": "alpha_explorer"},
                    exc_info=True,
                )
                ui.label("Failed to load IC data.").classes("text-red-500 p-4")

        with ui.tab_panel(tab_decay):
            try:
                decay_data = await run.io_bound(
                    service.get_decay_curve, selected.signal_id
                )
                render_decay_curve(decay_data, metrics.decay_half_life)
            except FileNotFoundError as e:
                logger.error(
                    "Failed to fetch decay data - data file not found",
                    extra={"error": str(e), "signal_id": selected.signal_id, "page": "alpha_explorer"},
                    exc_info=True,
                )
                ui.label("Decay data not found for this signal.").classes("text-red-500 p-4")
            except ValueError as e:
                logger.error(
                    "Failed to fetch decay data - invalid data format",
                    extra={"error": str(e), "signal_id": selected.signal_id, "page": "alpha_explorer"},
                    exc_info=True,
                )
                ui.label("Invalid decay data format.").classes("text-red-500 p-4")
            except Exception as e:
                logger.error(
                    "Failed to fetch decay data",
                    extra={"error": str(e), "signal_id": selected.signal_id, "page": "alpha_explorer"},
                    exc_info=True,
                )
                ui.label("Failed to load decay data.").classes("text-red-500 p-4")

        with ui.tab_panel(tab_corr):
            await _render_correlation_section(service, selected, all_signals)


async def _render_correlation_section(
    service: AlphaExplorerService,
    selected: SignalSummary,
    all_signals: list[SignalSummary],
) -> None:
    """Render correlation matrix section with signal selection."""
    ui.label("Signal Correlation").classes("text-lg font-bold mb-2")

    signal_names = [s.display_name for s in all_signals]
    signal_ids = [s.signal_id for s in all_signals]

    # Multi-select for signals
    selected_indices = ui.select(
        label="Select Signals (at least 2)",
        options=dict(enumerate(signal_names)),
        value=[signal_names.index(selected.display_name)],
        multiple=True,
    ).classes("w-full max-w-md mb-4")

    corr_container = ui.column().classes("w-full")

    async def update_correlation() -> None:
        corr_container.clear()
        with corr_container:
            indices = selected_indices.value or []
            if len(indices) < 2:
                ui.label("Select at least two signals to view correlation matrix.").classes(
                    "text-gray-500 p-4"
                )
                return

            selected_ids = [signal_ids[i] for i in indices]
            try:
                corr = await run.io_bound(service.compute_correlation, selected_ids)
                render_correlation_matrix(corr)
            except FileNotFoundError as e:
                logger.error(
                    "Failed to compute correlation - signal data not found",
                    extra={"error": str(e), "signal_ids": selected_ids, "page": "alpha_explorer"},
                    exc_info=True,
                )
                ui.label("Signal data not found for correlation.").classes("text-red-500 p-4")
            except ValueError as e:
                logger.error(
                    "Failed to compute correlation - invalid signal data",
                    extra={"error": str(e), "signal_ids": selected_ids, "page": "alpha_explorer"},
                    exc_info=True,
                )
                ui.label("Invalid signal data for correlation.").classes("text-red-500 p-4")
            except Exception as e:
                logger.error(
                    "Failed to compute correlation",
                    extra={"error": str(e), "signal_ids": selected_ids, "page": "alpha_explorer"},
                    exc_info=True,
                )
                ui.label("Failed to compute correlation.").classes("text-red-500 p-4")

    # Initial render
    await update_correlation()

    # Update on selection change
    async def on_correlation_change(_: Any) -> None:
        await update_correlation()

    selected_indices.on_value_change(on_correlation_change)


def _metric_card(label: str, value: str) -> None:
    """Render a small metric card."""
    with ui.card().classes("p-2 min-w-24"):
        ui.label(label).classes("text-xs text-gray-500")
        ui.label(value).classes("text-lg font-bold")


def _render_demo_mode() -> None:
    """Render demo mode with placeholder data."""
    # Demo signal list
    with ui.card().classes("w-full mb-4 p-4"):
        ui.label("Demo Signals").classes("text-lg font-bold mb-2")

        demo_signals = [
            {"name": "momentum_v1", "ic": 0.045, "icir": 0.82, "status": "production"},
            {"name": "value_factor_v2", "ic": 0.038, "icir": 0.65, "status": "staged"},
            {"name": "quality_v1", "ic": 0.052, "icir": 0.91, "status": "production"},
        ]

        columns: list[dict[str, Any]] = [
            {"name": "name", "label": "Signal Name", "field": "name", "sortable": True},
            {"name": "ic", "label": "Mean IC", "field": "ic", "sortable": True},
            {"name": "icir", "label": "ICIR", "field": "icir", "sortable": True},
            {"name": "status", "label": "Status", "field": "status"},
        ]

        ui.table(columns=columns, rows=demo_signals).classes("w-full")

    # Demo metrics
    with ui.row().classes("w-full gap-4"):
        with ui.card().classes("flex-1 p-4"):
            ui.label("Sample Metrics").classes("text-lg font-bold mb-2")

            with ui.row().classes("gap-4 flex-wrap"):
                _metric_card("Mean IC", "0.045")
                _metric_card("ICIR", "0.82")
                _metric_card("Hit Rate", "54.2%")

            with ui.row().classes("gap-4 flex-wrap mt-2"):
                _metric_card("Coverage", "98.5%")
                _metric_card("Turnover", "12.3%")
                _metric_card("Half-life", "5.2d")

        with ui.card().classes("w-64 p-4"):
            ui.label("Quick Actions").classes("text-lg font-bold mb-2")
            ui.button("Launch Backtest", icon="play_arrow").classes("w-full mb-2").props(
                "color=primary disable"
            )
            ui.button("Export Metrics", icon="download").classes("w-full").props(
                "disable"
            )

    ui.label("Configure MODEL_REGISTRY_DIR to view real signals.").classes(
        "text-gray-500 text-center mt-4"
    )


__all__ = ["alpha_explorer_page"]
