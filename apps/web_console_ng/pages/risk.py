"""Risk Analytics Dashboard page for NiceGUI.

Ported from apps/web_console/pages/risk.py (Streamlit).

This page displays portfolio risk analytics including:
- Factor exposures
- VaR/CVaR metrics with risk budget monitoring
- Stress test results

Data flows: risk.py -> RiskService -> StrategyScopedDataAccess -> libs/risk/
No HTTP/API calls - all data fetched via RiskService (parity with Streamlit).
"""

from __future__ import annotations

import asyncio
import logging
import math
from typing import Any

from nicegui import Client, ui

from apps.web_console.data.strategy_scoped_queries import StrategyScopedDataAccess
from apps.web_console.services.risk_service import RiskService
from apps.web_console.utils.db_pool import get_db_pool, get_redis_client
from apps.web_console.utils.validators import validate_overview_metrics
from apps.web_console_ng.auth.middleware import get_current_user, requires_auth
from apps.web_console_ng.components.factor_exposure_chart import render_factor_exposure
from apps.web_console_ng.components.stress_test_results import render_stress_tests
from apps.web_console_ng.components.var_chart import render_var_history, render_var_metrics
from apps.web_console_ng.config import (
    FEATURE_RISK_DASHBOARD,
    RISK_BUDGET_VAR_LIMIT,
    RISK_BUDGET_WARNING_THRESHOLD,
)
from apps.web_console_ng.core.client_lifecycle import ClientLifecycleManager
from apps.web_console_ng.ui.layout import main_layout
from libs.web_console_auth.permissions import Permission, get_authorized_strategies, has_permission

logger = logging.getLogger(__name__)

# Auto-refresh interval (M-3: 60s matches Streamlit parity for risk data freshness)
RISK_REFRESH_INTERVAL_SECONDS = 60.0


def _render_risk_metric(label: str, value: str, help_text: str | None = None) -> None:
    """Render a single risk overview metric card.

    Decision (L-1): Custom _render_risk_metric() for overview metrics,
    separate from var_chart._render_metric() to allow different styling.
    """
    with ui.card().classes("p-4"):
        ui.label(label).classes("text-sm text-gray-500")
        # M-2: Show N/A for None values, formatted value for valid values
        ui.label(value).classes("text-2xl font-bold")
        if help_text:
            ui.icon("help_outline", size="xs").tooltip(help_text).classes("text-gray-400")


@ui.page("/risk")
@requires_auth
@main_layout
async def risk_dashboard(client: Client) -> None:
    """Risk analytics dashboard with real-time updates."""
    user = get_current_user()
    user_id = user.get("user_id") if isinstance(user, dict) else getattr(user, "user_id", None)
    user_role = user.get("role") if isinstance(user, dict) else getattr(user, "role", None)

    # === PAGE-LEVEL GATES (Parity with Streamlit) ===

    # Feature flag check
    if not FEATURE_RISK_DASHBOARD:
        ui.label("Risk Analytics Dashboard is not currently enabled.").classes(
            "text-gray-500 text-center p-8"
        )
        return

    # Permission check (generic message to avoid exposing internal permission names)
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

    # === DATA STATE ===
    risk_data: dict[str, Any] = {}
    error_state: str | None = None  # Persistent error message for inline display
    prev_error_state: str | None = None  # Track previous error to avoid notification spam

    async def load_risk_data() -> None:
        """Fetch risk data via RiskService (same as Streamlit, NOT REST)."""
        nonlocal risk_data, error_state, prev_error_state

        def set_error(msg: str) -> None:
            """Set error state and notify only on state transition."""
            nonlocal error_state, prev_error_state
            risk_data.clear()  # Clear stale data on error
            if error_state != msg:  # Only notify on state change (avoid spam)
                error_state = msg
                ui.notify(msg, type="negative")
            prev_error_state = error_state

        try:
            db_pool = get_db_pool()
            if db_pool is None:
                set_error("Database connection not configured")
                return

            scoped_access = StrategyScopedDataAccess(
                db_pool=db_pool,
                redis_client=get_redis_client(),
                user={
                    "user_id": str(user_id),
                    "role": str(user_role or ""),
                    "strategies": list(authorized_strategies),
                },
            )
            service = RiskService(scoped_access)
            data = await service.get_risk_dashboard_data()

            risk_data.clear()
            risk_data.update({
                "risk_metrics": data.risk_metrics,
                "factor_exposures": data.factor_exposures,
                "stress_tests": data.stress_tests,
                "var_history": data.var_history,
                "is_placeholder": data.is_placeholder,
                "placeholder_reason": data.placeholder_reason,
            })
            error_state = None  # Clear error on success
            prev_error_state = None
        except PermissionError as e:
            logger.warning("risk_permission_denied", extra={"user_id": user_id, "error": str(e)})
            set_error("Access denied. Please contact an administrator.")
        except TimeoutError:
            logger.warning("risk_data_timeout", extra={"user_id": user_id})
            set_error("Request timed out. Please try again.")
        except Exception:
            logger.exception("risk_dashboard_error", extra={"user_id": user_id})
            set_error("Failed to load risk data. Please try again later.")

    # Initial data load
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
                    ui.label(
                        risk_data.get("placeholder_reason", "Risk model artifacts not available.")
                    ).classes("text-red-600")

        placeholder_warning()

        # === PERSISTENT ERROR STATE ===
        @ui.refreshable
        def error_banner() -> None:
            """Display persistent error message when data load fails."""
            if error_state and not risk_data:
                with ui.card().classes("w-full bg-yellow-100 border-yellow-500 border-2 p-4 mb-6"):
                    ui.label("Unable to Load Risk Data").classes(
                        "text-yellow-800 font-bold text-lg"
                    )
                    ui.label(error_state).classes("text-yellow-700")
                    ui.label(
                        "The dashboard is empty due to a backend error. "
                        "Use the Refresh button to retry."
                    ).classes("text-yellow-600 text-sm mt-2")

        error_banner()

        # === RISK OVERVIEW ===
        # Safe float conversion helper for risk metrics
        def _safe_float(value: Any, default: float | None = None) -> float | None:
            """Safely convert value to float, returning default on failure or NaN/inf."""
            if value is None:
                return default
            try:
                result = float(value)
                if not math.isfinite(result):
                    return default  # Reject NaN/inf
                return result
            except (ValueError, TypeError):
                return default

        @ui.refreshable
        def risk_overview_section() -> None:
            metrics = risk_data.get("risk_metrics", {})
            if not metrics:
                ui.label("Risk metrics not available").classes("text-gray-500 p-4")
                return

            ui.label("Risk Overview").classes("text-xl font-semibold mb-4")

            # Schema validation - use section-specific validator
            if not validate_overview_metrics(metrics):
                ui.label("Risk overview data incomplete. Some values may show N/A.").classes(
                    "text-yellow-600 mb-2"
                )

            with ui.row().classes("gap-8 mb-6"):
                # M-2: N/A for None/invalid values, formatted value for valid values
                # Safe float conversion to handle non-numeric data gracefully
                total_risk = _safe_float(metrics.get("total_risk"))
                _render_risk_metric(
                    "Total Risk (Ann.)",
                    f"{total_risk:.2%}" if total_risk is not None else "N/A",
                    "Annualized portfolio volatility",
                )

                factor_risk = _safe_float(metrics.get("factor_risk"))
                _render_risk_metric(
                    "Factor Risk",
                    f"{factor_risk:.2%}" if factor_risk is not None else "N/A",
                    "Systematic risk from factor exposures",
                )

                specific_risk = _safe_float(metrics.get("specific_risk"))
                _render_risk_metric(
                    "Specific Risk",
                    f"{specific_risk:.2%}" if specific_risk is not None else "N/A",
                    "Idiosyncratic risk from individual positions",
                )

        risk_overview_section()

        ui.separator().classes("my-4")

        # === VAR SECTION ===
        @ui.refreshable
        def var_section() -> None:
            ui.label("Value at Risk").classes("text-xl font-semibold mb-4")
            render_var_metrics(
                risk_data.get("risk_metrics"),
                var_limit=RISK_BUDGET_VAR_LIMIT,
                warning_threshold=RISK_BUDGET_WARNING_THRESHOLD,
            )

        @ui.refreshable
        def var_history_section() -> None:
            # Always call render_var_history to show empty state placeholder (Streamlit parity)
            render_var_history(risk_data.get("var_history"), var_limit=RISK_BUDGET_VAR_LIMIT)

        var_section()
        var_history_section()

        ui.separator().classes("my-4")

        # === FACTOR EXPOSURES ===
        @ui.refreshable
        def exposure_section() -> None:
            ui.label("Factor Exposures").classes("text-xl font-semibold mb-4")
            render_factor_exposure(risk_data.get("factor_exposures"))

        exposure_section()

        ui.separator().classes("my-4")

        # === STRESS TESTS ===
        @ui.refreshable
        def stress_section() -> None:
            render_stress_tests(risk_data.get("stress_tests"))

        stress_section()

        # === REFRESH LOGIC (must be defined before button) ===
        # Prevent concurrent refresh calls with a lock (Rev 3 requirement)
        refresh_lock = asyncio.Lock()

        async def guarded_refresh() -> None:
            """Refresh with lock to prevent overlapping RiskService calls."""
            if refresh_lock.locked():
                return  # Skip if already refreshing
            async with refresh_lock:
                await load_risk_data()
                placeholder_warning.refresh()
                error_banner.refresh()
                risk_overview_section.refresh()
                var_section.refresh()
                var_history_section.refresh()
                exposure_section.refresh()
                stress_section.refresh()

        # === REFRESH BUTTON ===
        ui.button("Refresh", on_click=guarded_refresh, icon="refresh").classes("mt-4")

    # === TIMER LIFECYCLE MANAGEMENT ===
    # Register timer with ClientLifecycleManager for cleanup on disconnect
    # (Same pattern as dashboard.py to prevent timer leaks and concurrent refreshes)
    lifecycle = ClientLifecycleManager.get()
    client_id = client.storage.get("client_id")

    # Guard: Require valid client_id before starting timers to prevent leaks
    if not client_id:
        logger.warning("risk_dashboard_no_client_id", extra={"user_id": user_id})
        ui.notify("Session error: missing client ID. Auto-refresh disabled.", type="warning")
        return

    # Auto-refresh every 60 seconds (M-3: justified interval, parity with Streamlit)
    refresh_timer = ui.timer(RISK_REFRESH_INTERVAL_SECONDS, guarded_refresh)

    # Register cleanup callback to cancel timer on client disconnect
    def cleanup_timer() -> None:
        refresh_timer.cancel()

    await lifecycle.register_cleanup_callback(client_id, cleanup_timer)


__all__ = ["risk_dashboard"]
