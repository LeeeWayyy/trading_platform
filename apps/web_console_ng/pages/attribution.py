"""Factor Attribution Analytics page for NiceGUI.

Visualizes Fama-French factor attribution for portfolio returns.
P6T10: Track 10 - Quantile & Attribution Analytics

This page displays:
- Attribution summary (alpha, t-stat, R-squared)
- Factor loadings chart (betas with t-stats)
- Factor statistics table with significance

Data flows: attribution.py -> AttributionService -> StrategyScopedDataAccess -> FactorAttribution
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Literal

from nicegui import Client, ui

from apps.web_console_ng.auth.middleware import get_current_user, requires_auth
from apps.web_console_ng.components.factor_contribution_chart import (
    render_attribution_summary,
    render_factor_loadings_chart,
    render_factor_table,
)
from apps.web_console_ng.core.client_lifecycle import ClientLifecycleManager
from apps.web_console_ng.core.database import get_db_pool
from apps.web_console_ng.core.redis_ha import get_redis_store
from apps.web_console_ng.ui.layout import main_layout
from config.settings import get_settings
from libs.data.data_providers.fama_french_local_provider import FamaFrenchLocalProvider
from libs.platform.analytics.attribution import (
    AttributionResult,
    DataMismatchError,
    InsufficientObservationsError,
)
from libs.platform.web_console_auth.permissions import (
    Permission,
    get_authorized_strategies,
    has_permission,
)
from libs.web_console_data.strategy_scoped_queries import StrategyScopedDataAccess
from libs.web_console_services.attribution_service import AttributionService

logger = logging.getLogger(__name__)

# Minimum observations for attribution (Fama-French convention)
MIN_OBSERVATIONS = 60  # ~3 months of daily data


@ui.page("/attribution")
@requires_auth
@main_layout
async def attribution_page(client: Client) -> None:
    """Factor attribution analytics page."""
    user = get_current_user()
    user_id = user.get("user_id") if isinstance(user, dict) else getattr(user, "user_id", None)
    user_role = user.get("role") if isinstance(user, dict) else getattr(user, "role", None)

    # === PAGE-LEVEL GATES ===

    # Permission check
    if not has_permission(user, Permission.VIEW_PNL):
        ui.notify("Access denied. Please contact an administrator.", type="negative")
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

    # Validate user_id
    if not user_id:
        ui.notify("Session error: missing user ID. Please re-authenticate.", type="negative")
        ui.navigate.to("/login")
        return

    # Check FF data directory exists
    settings = get_settings()
    # Guard against None/empty ff_data_dir before Path() construction
    if not settings.ff_data_dir:
        with ui.card().classes("w-full max-w-2xl mx-auto p-6"):
            ui.label("Fama-French Data Not Configured").classes("text-xl font-bold text-yellow-600")
            ui.label("Factor data directory not configured. Set FF_DATA_DIR in settings.").classes(
                "text-gray-600"
            )
        return

    ff_data_path = Path(settings.ff_data_dir)
    if not ff_data_path.exists():
        with ui.card().classes("w-full max-w-2xl mx-auto p-6"):
            ui.label("Fama-French Data Not Available").classes("text-xl font-bold text-yellow-600")
            ui.label(
                f"Factor data directory not found: {ff_data_path}. "
                "Please run the Fama-French sync command."
            ).classes("text-gray-600")
        return

    # === DATA STATE ===
    attribution_result: AttributionResult | None = None
    error_state: str | None = None
    is_loading: bool = False

    # Default date range (1 year lookback)
    # Use most recent likely trading day to avoid weekend/holiday errors
    today = date.today()
    default_end = today - timedelta(days=1)  # Last completed day
    # Adjust if default_end lands on weekend (0=Mon, 5=Sat, 6=Sun)
    if default_end.weekday() == 5:  # Saturday
        default_end = default_end - timedelta(days=1)  # Back to Friday
    elif default_end.weekday() == 6:  # Sunday
        default_end = default_end - timedelta(days=2)  # Back to Friday
    default_start = default_end - timedelta(days=365)

    # UI state
    selected_strategy = list(authorized_strategies)[0] if authorized_strategies else ""
    selected_model: Literal["ff3", "ff5", "ff6"] = "ff5"
    start_date = default_start
    end_date = default_end

    async def run_attribution() -> None:
        """Execute attribution analysis with current parameters."""
        nonlocal attribution_result, error_state, is_loading

        # Validate date range before running
        if start_date > end_date:
            ui.notify("Start date must be before end date", type="negative")
            return

        is_loading = True
        error_state = None
        attribution_result = None
        loading_indicator.refresh()

        try:
            db_pool = get_db_pool()
            if db_pool is None:
                error_state = "Database connection not configured"
                return

            scoped_access = StrategyScopedDataAccess(
                db_pool=db_pool,
                redis_client=get_redis_store().get_master_client(),
                user={
                    "user_id": str(user_id),
                    "role": str(user_role or ""),
                    "strategies": list(authorized_strategies),
                },
            )

            # Create FF provider
            ff_provider = FamaFrenchLocalProvider(storage_path=ff_data_path)

            # Create attribution service
            service = AttributionService(
                data_access=scoped_access,
                ff_provider=ff_provider,
            )

            # Run attribution
            attribution_result = await service.run_attribution(
                strategy_id=selected_strategy,
                start_date=start_date,
                end_date=end_date,
                model=selected_model,
            )
            error_state = None

        except InsufficientObservationsError as e:
            logger.warning(
                "attribution_insufficient_data",
                extra={"user_id": user_id, "strategy": selected_strategy, "error": str(e)},
            )
            error_state = (
                f"Insufficient data: {e}. Requires at least {MIN_OBSERVATIONS} observations."
            )
        except DataMismatchError as e:
            logger.warning(
                "attribution_date_mismatch",
                extra={"user_id": user_id, "strategy": selected_strategy, "error": str(e)},
            )
            error_state = f"Date mismatch: {e}. Ensure portfolio dates overlap with factor data."
        except PermissionError as e:
            logger.warning(
                "attribution_permission_denied",
                extra={"user_id": user_id, "strategy": selected_strategy, "error": str(e)},
            )
            error_state = "Access denied. You don't own this strategy."
        except FileNotFoundError as e:
            logger.error(
                "attribution_ff_data_missing",
                extra={"user_id": user_id, "error": str(e)},
                exc_info=True,
            )
            error_state = "Fama-French data files not found. Please run sync."
        except Exception as e:
            logger.error(
                "attribution_error",
                extra={"user_id": user_id, "strategy": selected_strategy, "error": str(e)},
                exc_info=True,
            )
            error_state = "Failed to run attribution. Please try again."
        finally:
            is_loading = False
            loading_indicator.refresh()
            results_section.refresh()

    # === PAGE CONTENT ===
    with ui.card().classes("w-full max-w-6xl mx-auto p-6"):
        ui.label("Factor Attribution Analytics").classes("text-2xl font-bold mb-2")
        ui.label(
            "Fama-French factor attribution for portfolio returns. "
            "Analyzes alpha generation and factor exposures."
        ).classes("text-gray-500 mb-6")

        # === CONTROLS ===
        with ui.row().classes("gap-4 mb-6 items-end flex-wrap"):
            # Strategy selector
            with ui.column().classes("gap-1"):
                ui.label("Strategy").classes("text-sm text-gray-600")
                strategy_select = ui.select(
                    options=list(authorized_strategies),
                    value=selected_strategy,
                    label="",
                ).classes("w-48")

                def on_strategy_change(value: Any) -> None:
                    nonlocal selected_strategy
                    selected_strategy = str(value) if value else selected_strategy

                strategy_select.on("update:model-value", on_strategy_change)

            # Model selector
            with ui.column().classes("gap-1"):
                ui.label("Factor Model").classes("text-sm text-gray-600")
                model_select = ui.select(
                    options=[
                        {"label": "3-Factor (Mkt, SMB, HML)", "value": "ff3"},
                        {"label": "5-Factor (+ RMW, CMA)", "value": "ff5"},
                        {"label": "6-Factor (+ Momentum)", "value": "ff6"},
                    ],
                    value=selected_model,
                    label="",
                ).classes("w-56")

                def on_model_change(value: Any) -> None:
                    nonlocal selected_model
                    # Extract model value (NiceGUI may return dict or raw value)
                    new_model = value.get("value") if isinstance(value, dict) else value
                    if new_model in ("ff3", "ff5", "ff6"):
                        selected_model = new_model

                model_select.on("update:model-value", on_model_change)

            # Date range
            with ui.column().classes("gap-1"):
                ui.label("Start Date").classes("text-sm text-gray-600")
                start_input = ui.input(
                    value=start_date.isoformat(),
                    placeholder="YYYY-MM-DD",
                ).classes("w-36")

                def on_start_change(value: Any) -> None:
                    nonlocal start_date
                    try:
                        start_date = date.fromisoformat(str(value))
                    except ValueError:
                        ui.notify("Invalid date format. Use YYYY-MM-DD.", type="warning")
                        # Reset input to last valid value to keep UI consistent
                        start_input.set_value(start_date.isoformat())

                start_input.on("update:model-value", on_start_change)

            with ui.column().classes("gap-1"):
                ui.label("End Date").classes("text-sm text-gray-600")
                end_input = ui.input(
                    value=end_date.isoformat(),
                    placeholder="YYYY-MM-DD",
                ).classes("w-36")

                def on_end_change(value: Any) -> None:
                    nonlocal end_date
                    try:
                        end_date = date.fromisoformat(str(value))
                    except ValueError:
                        ui.notify("Invalid date format. Use YYYY-MM-DD.", type="warning")
                        # Reset input to last valid value to keep UI consistent
                        end_input.set_value(end_date.isoformat())

                end_input.on("update:model-value", on_end_change)

            # Run button
            ui.button("Run Attribution", on_click=run_attribution, icon="analytics").classes(
                "bg-blue-600 text-white"
            )

        # === LOADING INDICATOR ===
        @ui.refreshable
        def loading_indicator() -> None:
            if is_loading:
                with ui.row().classes("gap-2 items-center text-blue-600"):
                    ui.spinner(size="sm")
                    ui.label("Running attribution analysis...")

        loading_indicator()

        ui.separator().classes("my-4")

        # === RESULTS SECTION ===
        @ui.refreshable
        def results_section() -> None:
            # Error state
            if error_state:
                with ui.card().classes("w-full bg-yellow-100 border-yellow-500 border-2 p-4 mb-6"):
                    ui.label("Attribution Failed").classes("text-yellow-800 font-bold")
                    ui.label(error_state).classes("text-yellow-700")
                return

            # No result yet
            if attribution_result is None:
                ui.label(
                    "Select a strategy and date range, then click 'Run Attribution' to analyze."
                ).classes("text-gray-500 text-center p-8")
                return

            # === ATTRIBUTION SUMMARY ===
            render_attribution_summary(attribution_result)

            ui.separator().classes("my-4")

            # === FACTOR LOADINGS CHART ===
            with ui.column().classes("w-full"):
                ui.label("Factor Loadings").classes("text-xl font-semibold mb-2")
                render_factor_loadings_chart(attribution_result, height=350)

            ui.separator().classes("my-4")

            # === FACTOR STATISTICS TABLE ===
            with ui.column().classes("w-full"):
                ui.label("Factor Statistics").classes("text-xl font-semibold mb-2")
                render_factor_table(attribution_result)

        results_section()

    # === LIFECYCLE MANAGEMENT ===
    lifecycle = ClientLifecycleManager.get()
    client_id = client.storage.get("client_id")
    if not isinstance(client_id, str) or not client_id:
        client_id = lifecycle.generate_client_id()
        client.storage["client_id"] = client_id


__all__ = ["attribution_page"]
