"""Circuit Breaker Dashboard page for NiceGUI web console (P5T7).

This page provides real-time monitoring and control of the trading system's
circuit breaker. Operators can view the current state, trip/reset history,
and manually trip or reset the circuit breaker with proper authorization.

Features:
    - Real-time status display with color coding (OPEN/TRIPPED/QUIET_PERIOD)
    - Trip/reset history table
    - Manual trip control (requires TRIP_CIRCUIT permission)
    - Manual reset control with acknowledgment (requires RESET_CIRCUIT permission)
    - Rate limiting on reset (max 1 per minute globally)
    - Auto-refresh every 5 seconds with timer lifecycle cleanup

PARITY: Mirrors apps/web_console/pages/circuit_breaker.py functionality
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from nicegui import app, run, ui

from apps.web_console_ng import config
from apps.web_console_ng.auth.middleware import get_current_user, requires_auth
from apps.web_console_ng.core.client_lifecycle import ClientLifecycleManager
from apps.web_console_ng.core.dependencies import get_sync_db_pool, get_sync_redis_client
from apps.web_console_ng.ui.layout import main_layout
from libs.redis_client import RedisClient
from libs.risk_management.breaker import CircuitBreakerState
from libs.web_console_auth.permissions import Permission, has_permission

if TYPE_CHECKING:
    from apps.web_console.services.cb_service import CircuitBreakerService

logger = logging.getLogger(__name__)


def _get_cb_service() -> CircuitBreakerService | None:
    """Get CircuitBreakerService with sync infrastructure (global cache).

    ⚠️ Uses app.storage (global) for service singleton, NOT app.storage.user.
    ⚠️ FAIL CLOSED: Returns None if Redis unavailable (safety critical).
    See P5T7_TASK.md Note #16, #30.
    """
    if not hasattr(app.storage, "_cb_service"):
        from apps.web_console.services.cb_service import CircuitBreakerService

        # Get sync dependencies for legacy service
        try:
            sync_pool = get_sync_db_pool()
        except RuntimeError:
            sync_pool = None
            logger.warning("sync_db_pool_unavailable", extra={"impact": "audit logging disabled"})

        try:
            sync_redis: RedisClient = get_sync_redis_client()  # type: ignore[assignment]
        except RuntimeError:
            # FAIL CLOSED: Do not fall back to local Redis - circuit breaker is safety critical
            # Creating a local client could connect to wrong instance without TLS/auth
            logger.error(
                "redis_unavailable_fail_closed",
                extra={"impact": "circuit breaker page disabled for safety"},
            )
            return None

        setattr(app.storage, "_cb_service", CircuitBreakerService(sync_redis, sync_pool))  # noqa: B010

    service: CircuitBreakerService = getattr(app.storage, "_cb_service")  # noqa: B009
    return service


@ui.page("/circuit-breaker")
@requires_auth
@main_layout
async def circuit_breaker_page() -> None:
    """Circuit Breaker Dashboard page."""
    user = get_current_user()

    # Feature flag check
    if not config.FEATURE_CIRCUIT_BREAKER:
        ui.label("Circuit Breaker Dashboard feature is disabled.").classes("text-lg")
        ui.label("Set FEATURE_CIRCUIT_BREAKER=true to enable.").classes("text-gray-500")
        return

    # Permission check
    if not has_permission(user, Permission.VIEW_CIRCUIT_BREAKER):
        ui.label("Permission denied: VIEW_CIRCUIT_BREAKER required").classes(
            "text-red-500 text-lg"
        )
        return

    # Get service (sync - will use run.io_bound for calls)
    cb_service = _get_cb_service()

    # FAIL CLOSED: If Redis unavailable, disable entire page for safety
    if cb_service is None:
        ui.label("Circuit Breaker Unavailable").classes("text-2xl font-bold text-red-600 mb-4")
        ui.label(
            "Redis connection failed. Circuit breaker controls are disabled for safety."
        ).classes("text-red-500 text-lg")
        ui.label(
            "Contact system administrator to restore Redis connectivity before using this page."
        ).classes("text-yellow-600")
        return

    # State for UI
    status_data: dict[str, Any] = {}
    history_data: list[dict[str, Any]] = []

    async def fetch_status() -> None:
        nonlocal status_data
        try:
            # ⚠️ CircuitBreakerService is SYNC - wrap with run.io_bound (see Note #18)
            status_data = await run.io_bound(cb_service.get_status)
        except RuntimeError as e:
            status_data = {"error": str(e)}

    async def fetch_history() -> None:
        nonlocal history_data
        try:
            # ⚠️ SYNC service - wrap with run.io_bound
            history_data = await run.io_bound(cb_service.get_history, 50)
        except Exception as e:
            logger.warning("history_fetch_failed", extra={"error": str(e)})
            history_data = []

    # Initial fetch
    await fetch_status()
    await fetch_history()

    # Page title
    ui.label("Circuit Breaker Dashboard").classes("text-2xl font-bold mb-4")

    # Status section
    @ui.refreshable
    def status_section() -> None:
        if "error" in status_data:
            ui.label(f"Cannot retrieve status: {status_data['error']}").classes(
                "text-red-500 text-lg"
            )
            ui.label(
                "Circuit breaker state may be missing from Redis. "
                "Contact system administrator to initialize state."
            ).classes("text-yellow-600")
            return

        state = status_data.get("state", "UNKNOWN")

        with ui.card().classes("w-full p-4"):
            if state == CircuitBreakerState.OPEN.value:
                ui.label(f"Status: {state}").classes("text-2xl font-bold text-green-600")
                ui.label("Trading is allowed").classes("text-gray-500")
            elif state == CircuitBreakerState.TRIPPED.value:
                ui.label(f"Status: {state}").classes("text-2xl font-bold text-red-600")
                ui.label(f"Reason: {status_data.get('trip_reason', 'Unknown')}").classes(
                    "text-yellow-600 font-medium"
                )
                ui.label(f"Tripped at: {status_data.get('tripped_at', 'Unknown')}").classes(
                    "text-gray-500"
                )
                if status_data.get("trip_details"):
                    with ui.expansion("Trip Details"):
                        ui.json_editor(
                            {"content": {"json": status_data["trip_details"]}},
                            on_change=lambda e: None,
                        ).classes("w-full")
            elif state == CircuitBreakerState.QUIET_PERIOD.value:
                ui.label(f"Status: {state} (recovering)").classes(
                    "text-2xl font-bold text-yellow-600"
                )
                ui.label(f"Reset at: {status_data.get('reset_at', 'Unknown')}").classes(
                    "text-gray-500"
                )
                ui.label("System is in quiet period before returning to OPEN").classes(
                    "text-gray-500"
                )
            else:
                ui.label(f"Status: {state}").classes("text-2xl font-bold text-gray-600")

            # Trip count today
            trip_count = status_data.get("trip_count_today", 0)
            if trip_count > 0:
                with ui.row().classes("mt-2"):
                    ui.label("Trips Today:").classes("font-medium")
                    ui.label(str(trip_count)).classes("font-bold text-red-500")

    status_section()

    ui.separator().classes("my-4")

    # Controls section
    with ui.row().classes("w-full gap-4"):
        # Trip control
        with ui.card().classes("flex-1 p-4"):
            ui.label("Manual Trip").classes("text-lg font-bold")

            if has_permission(user, Permission.TRIP_CIRCUIT):
                trip_reason = ui.select(
                    label="Trip Reason",
                    options=["MANUAL", "DATA_STALE", "BROKER_ERRORS", "Other"],
                    value="MANUAL",
                ).classes("w-full")
                custom_reason = ui.input(label="Custom reason").classes("w-full")
                custom_reason.visible = False

                def on_reason_change() -> None:
                    custom_reason.visible = trip_reason.value == "Other"

                trip_reason.on_value_change(on_reason_change)

                async def do_trip() -> None:
                    from apps.web_console.services.cb_service import (
                        RBACViolation,
                        ValidationError,
                    )

                    final_reason = (
                        custom_reason.value if trip_reason.value == "Other" else trip_reason.value
                    )
                    if not final_reason:
                        ui.notify("Please provide a reason", type="negative")
                        return

                    try:
                        await run.io_bound(
                            cb_service.trip, final_reason, dict(user), acknowledged=True
                        )
                        ui.notify("Circuit breaker TRIPPED", type="positive")
                        await fetch_status()
                        await fetch_history()
                        status_section.refresh()
                        history_section.refresh()
                    except ValidationError as e:
                        ui.notify(f"Validation error: {e}", type="negative")
                    except RBACViolation as e:
                        ui.notify(f"Permission denied: {e}", type="negative")
                    except Exception as e:
                        logger.exception("circuit_breaker_trip_failed", extra={"error": str(e)})
                        ui.notify("Failed to trip circuit breaker. Please try again.", type="negative")

                ui.button("Trip Circuit Breaker", on_click=do_trip, color="red").classes("mt-2")
            else:
                ui.label("TRIP_CIRCUIT permission required").classes("text-gray-500")

        # Reset control
        with ui.card().classes("flex-1 p-4"):
            ui.label("Reset Circuit Breaker").classes("text-lg font-bold")

            if has_permission(user, Permission.RESET_CIRCUIT):
                ui.label(
                    "Resetting will enter a 5-minute quiet period before returning to normal OPEN state."
                ).classes("text-yellow-600 text-sm mb-2")

                min_len = config.MIN_CIRCUIT_BREAKER_RESET_REASON_LENGTH
                reset_reason = ui.textarea(
                    label=f"Reset Reason (minimum {min_len} characters)",
                    placeholder="Explain why it's safe to resume trading",
                ).classes("w-full")

                char_count_label = ui.label(f"0/{min_len} characters").classes(
                    "text-sm text-gray-500"
                )

                def on_reason_input() -> None:
                    count = len(reset_reason.value) if reset_reason.value else 0
                    char_count_label.text = f"{count}/{min_len} characters"
                    reset_btn.enabled = count >= min_len and acknowledged.value

                reset_reason.on_value_change(on_reason_input)

                acknowledged = ui.checkbox(
                    "I acknowledge that resetting will allow trading to resume"
                )

                def on_ack_change() -> None:
                    count = len(reset_reason.value) if reset_reason.value else 0
                    reset_btn.enabled = count >= min_len and acknowledged.value

                acknowledged.on_value_change(on_ack_change)

                async def do_reset() -> None:
                    from apps.web_console.services.cb_service import (
                        RateLimitExceeded,
                        RBACViolation,
                        ValidationError,
                    )

                    try:
                        await run.io_bound(
                            cb_service.reset,
                            reset_reason.value,
                            dict(user),
                            acknowledged=acknowledged.value,
                        )
                        ui.notify(
                            "Circuit breaker RESET - entering quiet period", type="positive"
                        )
                        await fetch_status()
                        await fetch_history()
                        status_section.refresh()
                        history_section.refresh()
                    except RateLimitExceeded as e:
                        ui.notify(f"Rate limit exceeded: {e}", type="negative")
                    except ValidationError as e:
                        ui.notify(f"Validation error: {e}", type="negative")
                    except RBACViolation as e:
                        ui.notify(f"Permission denied: {e}", type="negative")
                    except Exception as e:
                        logger.exception("circuit_breaker_reset_failed", extra={"error": str(e)})
                        ui.notify("Failed to reset circuit breaker. Please try again.", type="negative")

                reset_btn = ui.button("Confirm Reset", on_click=do_reset, color="green").classes(
                    "mt-2"
                )
                reset_btn.enabled = False
            else:
                ui.label("RESET_CIRCUIT permission required").classes("text-gray-500")

    ui.separator().classes("my-4")

    # History section
    ui.label("Trip/Reset History").classes("text-xl font-bold")

    @ui.refreshable
    def history_section() -> None:
        if not history_data:
            ui.label("No trip history recorded").classes("text-gray-500")
            return

        # Define columns for the table
        columns: list[dict[str, Any]] = [
            {"name": "tripped_at", "label": "Tripped At", "field": "tripped_at", "sortable": True},
            {"name": "reason", "label": "Reason", "field": "reason"},
            {"name": "reset_at", "label": "Reset At", "field": "reset_at"},
            {"name": "reset_by", "label": "Reset By", "field": "reset_by"},
            {"name": "reset_reason", "label": "Reset Reason", "field": "reset_reason"},
        ]

        # Convert history to rows
        rows = []
        for entry in history_data:
            rows.append({
                "tripped_at": entry.get("tripped_at", ""),
                "reason": entry.get("reason", ""),
                "reset_at": entry.get("reset_at", ""),
                "reset_by": entry.get("reset_by", ""),
                "reset_reason": entry.get("reset_reason", ""),
            })

        ui.table(columns=columns, rows=rows).classes("w-full")

    history_section()

    # Auto-refresh every 5 seconds
    async def auto_refresh() -> None:
        await fetch_status()
        await fetch_history()
        status_section.refresh()
        history_section.refresh()

    # ⚠️ Rev 19: Timer lifecycle cleanup (see Note #29)
    timer = ui.timer(config.AUTO_REFRESH_INTERVAL, auto_refresh)

    # Register cleanup on client disconnect to prevent timer leaks
    client_id = ui.context.client.storage.get("client_id")
    if client_id:
        lifecycle_mgr = ClientLifecycleManager.get()
        await lifecycle_mgr.register_cleanup_callback(client_id, lambda: timer.cancel())


__all__ = ["circuit_breaker_page"]
