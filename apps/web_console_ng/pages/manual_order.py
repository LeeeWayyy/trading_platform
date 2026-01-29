"""Manual Order Entry page for NiceGUI web console."""

from __future__ import annotations

import logging
import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from nicegui import Client, app, ui

from apps.web_console_ng.auth.middleware import get_current_user, requires_auth
from apps.web_console_ng.components.action_button import ActionButton, ButtonState
from apps.web_console_ng.components.execution_style_selector import ExecutionStyleSelector
from apps.web_console_ng.components.fat_finger_validator import (
    FatFingerValidationResult,
    FatFingerValidator,
    parse_thresholds,
)
from apps.web_console_ng.components.twap_config import TWAPConfig
from apps.web_console_ng.core.audit import audit_log
from apps.web_console_ng.core.client import AsyncTradingClient
from apps.web_console_ng.core.client_lifecycle import ClientLifecycleManager
from apps.web_console_ng.core.hotkey_manager import HotkeyManager
from apps.web_console_ng.core.realtime import (
    RealtimeUpdater,
    circuit_breaker_channel,
    kill_switch_channel,
)
from apps.web_console_ng.auth.permissions import get_authorized_strategies
from apps.web_console_ng.ui.layout import main_layout
from apps.web_console_ng.utils.time import parse_iso_timestamp

logger = logging.getLogger(__name__)
STOP_PRICE_STALE_THRESHOLD_S = 60
FAT_FINGER_PRICE_STALE_THRESHOLD_S = 60
FAT_FINGER_ADV_FRESHNESS_SECONDS = 3600


@ui.page("/manual-order")
@requires_auth
@main_layout
async def manual_order_page(client: Client) -> None:
    """Manual order entry page with kill switch safety checks."""
    trading_client = AsyncTradingClient.get()
    user = get_current_user()
    user_id = str(user.get("user_id") or user.get("username") or "").strip()
    user_role = str(user.get("role") or "viewer")
    user_timezone = str(user.get("timezone") or "UTC")
    authorized_strategies = get_authorized_strategies(user)

    if not user_id:
        logger.warning("manual_order_missing_user_id")
        ui.notify("Session expired - please log in again", type="negative")
        ui.navigate.to("/login")
        return

    # Check permission
    if user_role == "viewer":
        ui.notify("Viewers cannot submit orders", type="negative")
        ui.navigate.to("/")
        return

    lifecycle = ClientLifecycleManager.get()

    # Get or generate client_id (may not be set yet if WebSocket hasn't connected)
    client_id = client.storage.get("client_id")
    if not isinstance(client_id, str) or not client_id:
        client_id = lifecycle.generate_client_id()
        client.storage["client_id"] = client_id

    realtime = RealtimeUpdater(client_id, client)
    kill_switch_engaged: bool | None = None  # Real-time cached state for instant UI response
    circuit_breaker_tripped: bool | None = None  # Real-time cached state for instant UI response
    preview_dialog_open = False  # Guards hotkey from bypassing disabled preview state
    submit_action_button: ActionButton | None = None
    fat_finger_validator: FatFingerValidator | None = None
    fat_finger_validation: FatFingerValidationResult | None = None
    fat_finger_thresholds_loaded = False
    fat_finger_adv: int | None = None
    fat_finger_adv_stale = False
    fat_finger_adv_cached_at: datetime | None = None
    fat_finger_adv_data_date: str | None = None
    fat_finger_adv_warning: str | None = None
    fat_finger_price_warning: str | None = None
    fat_finger_blocked = False
    fat_finger_task: asyncio.Task[None] | None = None
    adv_task: asyncio.Task[None] | None = None
    execution_style_selector: ExecutionStyleSelector | None = None
    twap_config: TWAPConfig | None = None
    twap_preview_task: asyncio.Task[None] | None = None
    twap_preview_data: dict[str, Any] | None = None
    twap_preview_errors: list[str] | None = None
    twap_notional_warning: str | None = None
    twap_notional_acknowledged = False

    with ui.card().classes("w-full max-w-lg mx-auto").props("data-order-form"):
        ui.label("Manual Order Entry").classes("text-2xl font-bold mb-4")

        # Form fields
        symbol_input = ui.input(
            "Symbol",
            placeholder="e.g. AAPL",
            validation={"Required": lambda v: bool(v and v.strip())},
        ).classes("w-full mb-2")

        with ui.row().classes("w-full gap-4 mb-2"):
            side_select = ui.select(
                options=["buy", "sell"],
                label="Side",
                value="buy",
            ).classes("flex-1")

            qty_input = ui.number(
                "Quantity",
                value=0,
                min=1,
                step=1,
                format="%d",
                validation={"Min 1": lambda v: v is not None and v >= 1},
            ).classes("flex-1")

        with ui.row().classes("w-full gap-4 mb-2"):
            order_type_select = ui.select(
                options=["market", "limit", "stop", "stop_limit"],
                label="Order Type",
                value="market",
            ).classes("flex-1")

            tif_select = ui.select(
                options=["day", "gtc", "ioc", "fok"],
                label="Time in Force",
                value="day",
            ).classes("flex-1")

        def on_execution_style_change(value: str) -> None:
            if twap_config:
                twap_config.set_visibility(value == "twap")
            _schedule_twap_preview()

        execution_style_selector = ExecutionStyleSelector(on_change=on_execution_style_change)
        execution_style_selector.create().classes("w-full mb-2")

        # Limit price - only visible for limit orders
        limit_price_container = ui.column().classes("w-full mb-2")
        with limit_price_container:
            limit_price_input = ui.number(
                "Limit Price",
                value=None,
                min=0.01,
                step=0.01,
                format="%.2f",
            ).classes("w-full")
        limit_price_container.set_visibility(False)

        # Stop price - only visible for stop orders
        stop_price_container = ui.column().classes("w-full mb-2")
        with stop_price_container:
            stop_price_input = ui.number(
                "Stop Price",
                value=None,
                min=0.01,
                step=0.01,
                format="%.2f",
            ).classes("w-full")
        stop_price_container.set_visibility(False)

        twap_config = TWAPConfig(
            on_change=_schedule_twap_preview,
            on_ack_change=lambda acknowledged: _set_twap_acknowledged(acknowledged),
        )
        twap_config.create().classes("w-full mb-2")
        twap_config.set_visibility(False)

        def on_order_type_change(e: Any) -> None:
            show_limit = e.value in ("limit", "stop_limit")
            show_stop = e.value in ("stop", "stop_limit")
            limit_price_container.set_visibility(show_limit)
            stop_price_container.set_visibility(show_stop)
            if execution_style_selector:
                if e.value in ("stop", "stop_limit"):
                    execution_style_selector.set_disabled(
                        True, "TWAP unavailable for stop orders"
                    )
                    if twap_config:
                        twap_config.set_visibility(False)
                else:
                    execution_style_selector.set_disabled(False)
            _schedule_fat_finger_validation()
            _schedule_twap_preview()

        order_type_select.on_value_change(on_order_type_change)

        def on_form_change(_e: Any) -> None:
            _schedule_fat_finger_validation()
            _schedule_twap_preview()

        async def _handle_symbol_change(value: str) -> None:
            symbol = value.strip().upper()
            if not symbol:
                return
            await _fetch_adv_for_symbol(symbol)
            await _validate_fat_finger_from_form()

        def on_symbol_change(e: Any) -> None:
            nonlocal adv_task
            if adv_task and not adv_task.done():
                adv_task.cancel()
            adv_task = asyncio.create_task(_handle_symbol_change(str(e.value or "")))
            _schedule_fat_finger_validation()
            _schedule_twap_preview()

        symbol_input.on_value_change(on_symbol_change)
        qty_input.on_value_change(on_form_change)
        side_select.on_value_change(on_form_change)
        tif_select.on_value_change(on_form_change)
        limit_price_input.on_value_change(on_form_change)
        stop_price_input.on_value_change(on_form_change)

        # Fat-finger validation panel
        fat_finger_container = ui.column().classes("w-full mb-4")
        with fat_finger_container:
            ui.label("Risk Checks").classes("text-sm font-semibold")
            fat_finger_status = ui.label("Loading risk thresholds...").classes(
                "text-gray-500 text-sm"
            )
            fat_finger_details = ui.column().classes("gap-1")
            fat_finger_capacity = ui.column().classes("gap-1")

        reason_input = ui.textarea(
            "Reason (required)",
            placeholder="Why are you placing this order? (min 10 chars)",
            validation={"Min 10 characters": lambda v: bool(v and len(v.strip()) >= 10)},
        ).classes("w-full mb-4")

        submit_container = ui.row().classes("w-full")

    def is_read_only_mode() -> bool:
        return bool(app.storage.user.get("read_only"))

    def _set_fat_finger_status(message: str, *, is_error: bool = False) -> None:
        fat_finger_status.set_text(message)
        if is_error:
            fat_finger_status.classes(add="text-red-600", remove="text-gray-500")
        else:
            fat_finger_status.classes(add="text-gray-500", remove="text-red-600")

    def _clear_fat_finger_display() -> None:
        if hasattr(fat_finger_details, "clear"):
            fat_finger_details.clear()
        if hasattr(fat_finger_capacity, "clear"):
            fat_finger_capacity.clear()

    def _render_fat_finger(result: FatFingerValidationResult | None) -> None:
        nonlocal fat_finger_blocked
        _clear_fat_finger_display()

        if result is None:
            fat_finger_blocked = False
            _set_fat_finger_status(
                "Risk checks unavailable; backend validation will apply.",
                is_error=False,
            )
            return

        fat_finger_blocked = result.blocked
        status_text = "Risk checks passed" if not result.blocked else "Risk limits breached"
        _set_fat_finger_status(status_text, is_error=result.blocked)

        if fat_finger_price_warning:
            with fat_finger_details:
                ui.label(f"WARNING: {fat_finger_price_warning}").classes(
                    "text-amber-600 text-sm"
                )

        if fat_finger_adv_warning:
            with fat_finger_details:
                ui.label(f"WARNING: {fat_finger_adv_warning}").classes(
                    "text-amber-600 text-sm"
                )

        if result.warnings:
            for warning in result.warnings:
                css = "text-red-600" if warning.severity == "error" else "text-amber-600"
                with fat_finger_details:
                    ui.label(f"{warning.severity.upper()}: {warning.message}").classes(
                        f"{css} text-sm"
                    )
        else:
            with fat_finger_details:
                ui.label("No threshold breaches detected.").classes("text-green-600 text-sm")

        with fat_finger_capacity:
            if result.remaining_qty is not None:
                remaining_qty = max(result.remaining_qty, 0)
                ui.label(f"Remaining qty: {remaining_qty:,} shares").classes("text-xs")
            if result.remaining_notional is not None:
                remaining_notional = max(result.remaining_notional, Decimal("0"))
                ui.label(
                    f"Remaining notional: ${remaining_notional:,.2f}"
                ).classes("text-xs")
            if result.remaining_adv_shares is not None:
                remaining_adv = max(result.remaining_adv_shares, 0)
                ui.label(f"Remaining ADV capacity: {remaining_adv:,} shares").classes(
                    "text-xs"
                )

    async def check_kill_switch(*, use_cache: bool = False) -> bool:
        """Check if kill switch is engaged. Returns True if safe to proceed.

        Args:
            use_cache: If True, use cached real-time state for instant response.
                       If False (default), perform blocking API check.

        Fails closed: unknown/missing state blocks action (safety first).
        """
        # Use cached state for instant pre-check (e.g., before showing dialog)
        if use_cache:
            if kill_switch_engaged is True:
                ui.notify("Cannot submit: Kill Switch is ENGAGED", type="negative")
                return False
            elif kill_switch_engaged is False:
                return True
            # Fall through to API check if cache is None (not yet initialized)

        # Blocking API check for confirmation or when cache unavailable
        try:
            ks_status = await trading_client.fetch_kill_switch_status(user_id, role=user_role)
            state = str(ks_status.get("state", "")).upper()
            if state == "ENGAGED":
                ui.notify("Cannot submit: Kill Switch is ENGAGED", type="negative")
                return False
            if state == "DISENGAGED":
                return True
            # Unknown state - fail closed for safety
            logger.warning(
                "kill_switch_unknown_state",
                extra={"user_id": user_id, "state": state},
            )
            ui.notify("Cannot verify kill switch: unknown state", type="negative")
            return False
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "kill_switch_check_failed",
                extra={"user_id": user_id, "status": exc.response.status_code},
            )
            ui.notify(
                f"Cannot verify kill switch: HTTP {exc.response.status_code}", type="negative"
            )
            return False
        except httpx.RequestError as exc:
            logger.warning(
                "kill_switch_check_failed",
                extra={"user_id": user_id, "error": type(exc).__name__},
            )
            ui.notify("Cannot verify kill switch: network error", type="negative")
            return False

    async def check_circuit_breaker(*, use_cache: bool = False) -> bool:
        """Check if circuit breaker allows trading. Returns True if safe to proceed.

        Args:
            use_cache: If True, use cached real-time state for instant response.
                       If False (default), perform blocking API check.

        Fails closed: unknown/missing state blocks action (safety first).
        """
        if use_cache:
            if circuit_breaker_tripped is True:
                ui.notify("Cannot submit: Circuit breaker is TRIPPED", type="negative")
                return False
            elif circuit_breaker_tripped is False:
                return True

        try:
            cb_status = await trading_client.fetch_circuit_breaker_status(
                user_id, role=user_role
            )
            state = str(cb_status.get("state", "")).upper()
            if state == "OPEN":
                return True
            if state in ("TRIPPED", "QUIET_PERIOD"):
                ui.notify("Cannot submit: Circuit breaker is TRIPPED", type="negative")
                return False
            logger.warning(
                "circuit_breaker_unknown_state",
                extra={"user_id": user_id, "state": state},
            )
            ui.notify("Cannot verify circuit breaker: unknown state", type="negative")
            return False
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "circuit_breaker_check_failed",
                extra={"user_id": user_id, "status": exc.response.status_code},
            )
            ui.notify(
                f"Cannot verify circuit breaker: HTTP {exc.response.status_code}",
                type="negative",
            )
            return False
        except httpx.RequestError as exc:
            logger.warning(
                "circuit_breaker_check_failed",
                extra={"user_id": user_id, "error": type(exc).__name__},
            )
            ui.notify("Cannot verify circuit breaker: network error", type="negative")
            return False

    async def _fetch_current_price(symbol: str) -> tuple[Decimal | None, datetime | None]:
        """Fetch current price for stop validation (best-effort)."""
        try:
            prices = await trading_client.fetch_market_prices(user_id, role=user_role)
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "stop_price_fetch_failed",
                extra={"user_id": user_id, "status": exc.response.status_code},
            )
            return None, None
        except httpx.RequestError as exc:
            logger.warning(
                "stop_price_fetch_failed",
                extra={"user_id": user_id, "error": type(exc).__name__},
            )
            return None, None

        for point in prices:
            if str(point.get("symbol", "")).upper() != symbol:
                continue
            raw_price = point.get("mid")
            if raw_price is None:
                return None, None
            try:
                price = Decimal(str(raw_price))
            except (InvalidOperation, TypeError):
                return None, None

            ts = point.get("timestamp")
            if isinstance(ts, datetime):
                ts_value = ts
            elif isinstance(ts, str):
                try:
                    ts_value = parse_iso_timestamp(ts)
                except ValueError:
                    ts_value = None
            else:
                ts_value = None
            return price, ts_value

        return None, None

    async def _resolve_fat_finger_price(
        order_data: dict[str, Any],
    ) -> tuple[Decimal | None, str | None]:
        order_type = order_data.get("order_type")
        if order_type in ("limit", "stop_limit"):
            limit_price = order_data.get("limit_price")
            return (Decimal(str(limit_price)) if limit_price is not None else None, None)
        if order_type in ("stop",):
            stop_price = order_data.get("stop_price")
            return (Decimal(str(stop_price)) if stop_price is not None else None, None)

        current_price, last_updated = await _fetch_current_price(order_data["symbol"])
        if current_price is None or last_updated is None:
            return None, "Price unavailable for notional check"

        price_age = (datetime.now(UTC) - last_updated).total_seconds()
        if price_age > FAT_FINGER_PRICE_STALE_THRESHOLD_S:
            return None, f"Price data is {int(price_age)}s old; notional check skipped"

        return current_price, None

    async def _fetch_fat_finger_thresholds() -> None:
        nonlocal fat_finger_validator, fat_finger_thresholds_loaded
        try:
            payload = await trading_client.fetch_fat_finger_thresholds(user_id, role=user_role)
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "fat_finger_thresholds_fetch_failed",
                extra={"user_id": user_id, "status": exc.response.status_code},
            )
            _set_fat_finger_status(
                "Risk checks unavailable; backend validation will apply.",
                is_error=False,
            )
            fat_finger_thresholds_loaded = False
            fat_finger_validator = None
            return
        except httpx.RequestError as exc:
            logger.warning(
                "fat_finger_thresholds_fetch_failed",
                extra={"user_id": user_id, "error": type(exc).__name__},
            )
            _set_fat_finger_status(
                "Risk checks unavailable; backend validation will apply.",
                is_error=False,
            )
            fat_finger_thresholds_loaded = False
            fat_finger_validator = None
            return

        defaults, overrides = parse_thresholds(payload)
        fat_finger_validator = FatFingerValidator(defaults, overrides)
        fat_finger_thresholds_loaded = True
        _set_fat_finger_status("Risk checks ready", is_error=False)

    async def _fetch_adv_for_symbol(symbol: str) -> None:
        nonlocal fat_finger_adv, fat_finger_adv_stale, fat_finger_adv_cached_at
        nonlocal fat_finger_adv_warning, fat_finger_adv_data_date
        fat_finger_adv = None
        fat_finger_adv_stale = False
        fat_finger_adv_cached_at = None
        fat_finger_adv_data_date = None
        fat_finger_adv_warning = None

        try:
            payload = await trading_client.fetch_adv(symbol, user_id, role=user_role)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 403:
                fat_finger_adv_warning = "ADV check skipped: permission denied"
            elif exc.response.status_code == 404:
                fat_finger_adv_warning = "ADV check skipped: data unavailable"
            else:
                fat_finger_adv_warning = "ADV check skipped: provider unavailable"
            logger.warning(
                "fat_finger_adv_fetch_failed",
                extra={"user_id": user_id, "symbol": symbol, "status": exc.response.status_code},
            )
            return
        except httpx.RequestError as exc:
            fat_finger_adv_warning = "ADV check skipped: network error"
            logger.warning(
                "fat_finger_adv_fetch_failed",
                extra={"user_id": user_id, "symbol": symbol, "error": type(exc).__name__},
            )
            return

        adv_value = payload.get("adv")
        if adv_value is None:
            fat_finger_adv_warning = "ADV check skipped: data unavailable"
            return
        try:
            fat_finger_adv = int(adv_value)
        except (TypeError, ValueError):
            fat_finger_adv_warning = "ADV check skipped: invalid data"
            fat_finger_adv = None
            return

        fat_finger_adv_stale = bool(payload.get("stale"))
        fat_finger_adv_data_date = str(payload.get("data_date"))

        cached_at_raw = payload.get("cached_at")
        if isinstance(cached_at_raw, str):
            try:
                fat_finger_adv_cached_at = parse_iso_timestamp(cached_at_raw)
            except ValueError:
                fat_finger_adv_cached_at = None

        if fat_finger_adv_stale:
            fat_finger_adv_warning = "ADV data stale (>5 trading days); check skipped"
            return

        if fat_finger_adv_cached_at is not None:
            age_seconds = (datetime.now(UTC) - fat_finger_adv_cached_at).total_seconds()
            if age_seconds > FAT_FINGER_ADV_FRESHNESS_SECONDS:
                fat_finger_adv_warning = "ADV data may be outdated (cached >1h)"

    async def _run_fat_finger_validation(order_data: dict[str, Any]) -> None:
        nonlocal fat_finger_validation, fat_finger_price_warning

        if not fat_finger_thresholds_loaded or fat_finger_validator is None:
            fat_finger_validation = None
            fat_finger_price_warning = None
            _render_fat_finger(None)
            return

        price, price_warning = await _resolve_fat_finger_price(order_data)
        adv_for_validation = None if fat_finger_adv_stale else fat_finger_adv
        fat_finger_price_warning = price_warning

        result = fat_finger_validator.validate(
            symbol=order_data["symbol"],
            qty=order_data["qty"],
            price=price,
            adv=adv_for_validation,
        )

        fat_finger_validation = result
        _render_fat_finger(result)

    async def _validate_fat_finger_from_form() -> None:
        order_data = validate_form()
        if order_data is None:
            return
        await _run_fat_finger_validation(order_data)

    async def _debounced_fat_finger_validation() -> None:
        await asyncio.sleep(0.3)
        await _validate_fat_finger_from_form()

    def _schedule_fat_finger_validation() -> None:
        nonlocal fat_finger_task
        if not fat_finger_thresholds_loaded:
            return
        symbol_value = str(symbol_input.value or "").strip()
        if not symbol_value:
            return
        if fat_finger_task and not fat_finger_task.done():
            fat_finger_task.cancel()
        fat_finger_task = asyncio.create_task(_debounced_fat_finger_validation())

    def _set_twap_acknowledged(acknowledged: bool) -> None:
        nonlocal twap_notional_acknowledged
        twap_notional_acknowledged = acknowledged

    def _is_twap_selected() -> bool:
        return bool(
            execution_style_selector
            and execution_style_selector.value() == "twap"
        )

    def _basic_twap_order_data() -> dict[str, Any] | None:
        symbol = (symbol_input.value or "").strip().upper()
        if not symbol:
            return None
        qty = qty_input.value
        if qty is None or qty < 1 or qty != int(qty):
            return None

        order_type = order_type_select.value
        if order_type not in ("market", "limit"):
            return None

        data: dict[str, Any] = {
            "symbol": symbol,
            "side": side_select.value,
            "qty": int(qty),
            "order_type": order_type,
            "time_in_force": tif_select.value,
        }

        if order_type == "limit":
            limit_price = limit_price_input.value
            if limit_price is None or limit_price <= 0:
                return None
            data["limit_price"] = float(limit_price)

        return data

    async def _run_twap_preview() -> None:
        nonlocal twap_preview_data, twap_preview_errors, twap_notional_warning

        if not _is_twap_selected() or twap_config is None:
            return

        order_data = _basic_twap_order_data()
        if order_data is None:
            return

        if not authorized_strategies:
            twap_preview_errors = ["No authorized strategies available for preview"]
            if twap_config:
                twap_config.set_preview_errors(twap_preview_errors)
            return

        state = twap_config.get_state(user_timezone)
        twap_config.set_start_time_error(state.start_time_error)
        if state.start_time_error:
            return

        if state.duration_minutes is None or state.interval_seconds is None:
            return

        payload: dict[str, Any] = {
            "symbol": order_data["symbol"],
            "side": order_data["side"],
            "qty": order_data["qty"],
            "order_type": order_data["order_type"],
            "time_in_force": "day",
            "duration_minutes": state.duration_minutes,
            "interval_seconds": state.interval_seconds,
            "strategy_id": authorized_strategies[0],
            "timezone": user_timezone,
        }
        if "limit_price" in order_data:
            payload["limit_price"] = order_data["limit_price"]
        if state.start_time is not None:
            payload["start_time"] = state.start_time.isoformat()

        try:
            response = await trading_client.fetch_twap_preview(
                payload, user_id, role=user_role
            )
        except httpx.HTTPStatusError as exc:
            twap_preview_data = None
            twap_notional_warning = None
            if exc.response.status_code == 422:
                try:
                    body = exc.response.json()
                except ValueError:
                    body = {}
                twap_preview_errors = list(body.get("errors", []))
                if twap_config:
                    twap_config.set_preview_errors(twap_preview_errors)
                    twap_config.set_preview(None)
                return
            logger.warning(
                "twap_preview_failed",
                extra={"user_id": user_id, "status": exc.response.status_code},
            )
            return
        except httpx.RequestError as exc:
            logger.warning(
                "twap_preview_failed",
                extra={"user_id": user_id, "error": type(exc).__name__},
            )
            return

        twap_preview_data = response
        twap_preview_errors = response.get("validation_errors") or []
        twap_notional_warning = response.get("notional_warning")

        if twap_config:
            twap_config.set_preview(response)
            twap_config.set_preview_errors(twap_preview_errors)
            twap_config.set_notional_warning(twap_notional_warning)

    async def _debounced_twap_preview() -> None:
        await asyncio.sleep(0.3)
        await _run_twap_preview()

    def _schedule_twap_preview() -> None:
        nonlocal twap_preview_task
        if not _is_twap_selected():
            return
        if twap_preview_task and not twap_preview_task.done():
            twap_preview_task.cancel()
        twap_preview_task = asyncio.create_task(_debounced_twap_preview())

    async def validate_stop_price_vs_current(order_data: dict[str, Any]) -> bool:
        """Validate stop price against current market price (best-effort)."""
        if order_data.get("order_type") not in ("stop", "stop_limit"):
            return True

        stop_price = order_data.get("stop_price")
        if stop_price is None:
            return True

        current_price, last_updated = await _fetch_current_price(order_data["symbol"])
        if current_price is None:
            ui.notify(
                "Current price unavailable. Stop price cannot be validated.",
                type="warning",
            )
            return True

        if last_updated is None:
            ui.notify(
                "Current price timestamp unavailable. Stop price validation may be stale.",
                type="warning",
            )
            return True

        price_age = (datetime.now(UTC) - last_updated).total_seconds()
        if price_age > STOP_PRICE_STALE_THRESHOLD_S:
            ui.notify(
                f"Price data is {int(price_age)}s old. Stop validation may be stale.",
                type="warning",
            )
            return True

        try:
            stop_price_value = Decimal(str(stop_price))
        except (InvalidOperation, TypeError):
            return True

        side = order_data.get("side", "buy")
        if side == "buy" and stop_price_value <= current_price:
            ui.notify(
                f"Stop price must be above current price (${current_price:.2f})",
                type="negative",
            )
            return False
        if side == "sell" and stop_price_value >= current_price:
            ui.notify(
                f"Stop price must be below current price (${current_price:.2f})",
                type="negative",
            )
            return False

        return True

    def validate_form() -> dict[str, Any] | None:
        """Validate form and return order data if valid."""
        symbol = (symbol_input.value or "").strip().upper()
        if not symbol:
            ui.notify("Symbol is required", type="warning")
            return None

        qty = qty_input.value
        if qty is None or qty < 1:
            ui.notify("Quantity must be at least 1", type="warning")
            return None
        if qty != int(qty):
            ui.notify("Quantity must be a whole number", type="warning")
            return None

        reason = (reason_input.value or "").strip()
        if len(reason) < 10:
            ui.notify("Reason must be at least 10 characters", type="warning")
            return None

        order_data: dict[str, Any] = {
            "symbol": symbol,
            "side": side_select.value,
            "qty": int(qty),
            "order_type": order_type_select.value,
            "time_in_force": tif_select.value,
        }

        if order_type_select.value in ("limit", "stop_limit"):
            limit_price = limit_price_input.value
            if limit_price is None or limit_price <= 0:
                ui.notify("Limit price is required for limit orders", type="warning")
                return None
            order_data["limit_price"] = float(limit_price)

        if order_type_select.value in ("stop", "stop_limit"):
            stop_price = stop_price_input.value
            if stop_price is None or stop_price <= 0:
                ui.notify("Stop price is required for stop orders", type="warning")
                return None
            order_data["stop_price"] = float(stop_price)

        return order_data

    def reset_form() -> None:
        symbol_input.value = ""
        qty_input.value = 0
        reason_input.value = ""
        order_type_select.value = "market"
        side_select.value = "buy"
        tif_select.value = "day"
        limit_price_input.value = None
        limit_price_container.set_visibility(False)
        stop_price_input.value = None
        stop_price_container.set_visibility(False)
        if execution_style_selector:
            execution_style_selector.set_disabled(False)
            execution_style_selector.set_value("instant")
        if twap_config:
            twap_config.set_visibility(False)
            twap_config.set_notional_warning(None)

    async def show_preview() -> None:
        # Check if already processing (CONFIRMING state from ActionButton)
        if submit_action_button and submit_action_button.state == ButtonState.CONFIRMING:
            return

        if is_read_only_mode():
            ui.notify("Read-only mode: connection lost", type="warning")
            if submit_action_button:
                submit_action_button.reset()
            return

        order_data = validate_form()
        if order_data is None:
            if submit_action_button:
                submit_action_button.reset()
            return

        # Check kill switch BEFORE showing preview using cached real-time state
        # This provides instant UI response; fresh check happens at confirmation
        if not await check_kill_switch(use_cache=True):
            if submit_action_button:
                submit_action_button.reset()
            return
        if not await check_circuit_breaker(use_cache=True):
            if submit_action_button:
                submit_action_button.reset()
            return

        if _is_twap_selected():
            if order_data["order_type"] in ("stop", "stop_limit"):
                ui.notify("TWAP is not supported for stop orders", type="negative")
                if submit_action_button:
                    submit_action_button.reset()
                return
            if tif_select.value != "day":
                ui.notify("TWAP requires time in force = DAY", type="negative")
                if submit_action_button:
                    submit_action_button.reset()
                return
            if twap_config:
                state = twap_config.get_state(user_timezone)
                twap_config.set_start_time_error(state.start_time_error)
                if state.start_time_error:
                    ui.notify(state.start_time_error, type="negative")
                    if submit_action_button:
                        submit_action_button.reset()
                    return
            await _run_twap_preview()
            if twap_preview_errors:
                ui.notify("TWAP parameters invalid. Review preview errors.", type="negative")
                if submit_action_button:
                    submit_action_button.reset()
                return
            if twap_notional_warning and not twap_notional_acknowledged:
                ui.notify(
                    "TWAP requires acknowledgement for missing notional validation.",
                    type="warning",
                )
                if submit_action_button:
                    submit_action_button.reset()
                return

        if not await validate_stop_price_vs_current(order_data):
            if submit_action_button:
                submit_action_button.reset()
            return
        await _run_fat_finger_validation(order_data)
        if fat_finger_blocked:
            ui.notify(
                "Order blocked by fat-finger limits. Adjust quantity or price.",
                type="negative",
            )
            if submit_action_button:
                submit_action_button.reset()
            return

        reason = (reason_input.value or "").strip()

        # Transition to CONFIRMING state for visual feedback
        if submit_action_button:
            submit_action_button.set_external_state(ButtonState.CONFIRMING)

        with ui.dialog() as dialog, ui.card().classes("p-6 min-w-[400px]"):

            def on_dialog_close() -> None:
                nonlocal preview_dialog_open
                preview_dialog_open = False
                # Reset to DEFAULT if still in CONFIRMING state (user cancelled)
                if submit_action_button and submit_action_button.state == ButtonState.CONFIRMING:
                    submit_action_button.reset()

            dialog.on("close", on_dialog_close)
            ui.label("Order Preview").classes("text-xl font-bold mb-4")

            with ui.column().classes("gap-2 mb-4"):
                ui.label(f"Symbol: {order_data['symbol']}").classes("font-mono")
                side_color = "text-green-600" if order_data["side"] == "buy" else "text-red-600"
                ui.label(f"Side: {order_data['side'].upper()}").classes(f"font-bold {side_color}")
                ui.label(f"Quantity: {order_data['qty']:,}").classes("font-mono")
                ui.label(f"Type: {order_data['order_type'].upper()}").classes("font-mono")
                if "limit_price" in order_data:
                    ui.label(f"Limit Price: ${order_data['limit_price']:.2f}").classes("font-mono")
                if "stop_price" in order_data:
                    ui.label(f"Stop Price: ${order_data['stop_price']:.2f}").classes("font-mono")
                if _is_twap_selected() and twap_config:
                    twap_state = twap_config.get_state(user_timezone)
                    ui.label("Execution: TWAP").classes("font-mono")
                    ui.label(
                        f"Duration: {twap_state.duration_minutes} min"
                    ).classes("font-mono")
                    ui.label(
                        f"Interval: {twap_state.interval_seconds} sec"
                    ).classes("font-mono")
                ui.label(f"Time in Force: {order_data['time_in_force'].upper()}").classes(
                    "font-mono"
                )
                ui.label(f"Reason: {reason}").classes("text-gray-600 text-sm")

            confirming = False

            with ui.row().classes("gap-4 justify-end"):

                async def confirm_order() -> None:
                    nonlocal confirming
                    # Prevent double-click while submitting
                    if confirming:
                        return
                    confirming = True
                    confirm_btn.disable()

                    try:
                        if is_read_only_mode():
                            ui.notify("Read-only mode: connection lost", type="warning")
                            confirming = False
                            confirm_btn.enable()
                            return
                        # FRESH kill switch check at confirmation time
                        if not await check_kill_switch():
                            confirming = False
                            confirm_btn.enable()
                            return
                        if not await check_circuit_breaker():
                            confirming = False
                            confirm_btn.enable()
                            return
                        if not await validate_stop_price_vs_current(order_data):
                            confirming = False
                            confirm_btn.enable()
                            return
                        if _is_twap_selected():
                            if twap_config:
                                twap_state = twap_config.get_state(user_timezone)
                                twap_config.set_start_time_error(twap_state.start_time_error)
                                if twap_state.start_time_error:
                                    ui.notify(twap_state.start_time_error, type="negative")
                                    confirming = False
                                    confirm_btn.enable()
                                    return
                            if twap_notional_warning and not twap_notional_acknowledged:
                                ui.notify(
                                    "TWAP requires acknowledgement for missing notional validation.",
                                    type="warning",
                                )
                                confirming = False
                                confirm_btn.enable()
                                return
                        await _run_fat_finger_validation(order_data)
                        if fat_finger_blocked:
                            ui.notify(
                                "Order blocked by fat-finger limits. Adjust quantity or price.",
                                type="negative",
                            )
                            confirming = False
                            confirm_btn.enable()
                            return

                        # Submit order - backend generates deterministic client_order_id
                        # for idempotency based on order params + date
                        payload = {
                            **order_data,
                            "reason": reason,
                            "requested_by": user_id,
                            "requested_at": datetime.now(UTC).isoformat(),
                        }
                        if _is_twap_selected() and twap_config:
                            twap_state = twap_config.get_state(user_timezone)
                            payload["execution_style"] = "twap"
                            payload["twap_duration_minutes"] = twap_state.duration_minutes
                            payload["twap_interval_seconds"] = twap_state.interval_seconds
                            if twap_state.start_time is not None:
                                payload["start_time"] = twap_state.start_time.isoformat()
                        result = await trading_client.submit_manual_order(
                            payload,
                            user_id,
                            role=user_role,
                        )

                        order_id = result.get("client_order_id", "")
                        display_id = order_id[:12] + "..." if len(order_id) > 12 else order_id

                        audit_log(
                            action="manual_order_submitted",
                            user_id=user_id,
                            details={
                                "symbol": order_data["symbol"],
                                "side": order_data["side"],
                                "qty": order_data["qty"],
                                "order_type": order_data["order_type"],
                                "client_order_id": order_id,
                                "reason": reason,
                            },
                        )

                        # Show success state with visual feedback
                        if submit_action_button:
                            submit_action_button.set_external_state(ButtonState.SUCCESS)

                        ui.notify(f"Order submitted: {display_id}", type="positive")
                        dialog.close()

                        # Reset form
                        reset_form()

                    except httpx.HTTPStatusError as exc:
                        error_detail = ""
                        try:
                            resp_payload = exc.response.json()
                            detail = (
                                resp_payload.get("detail", resp_payload)
                                if isinstance(resp_payload, dict)
                                else resp_payload
                            )
                            if isinstance(detail, dict):
                                error_detail = detail.get("message") or detail.get("error") or ""
                        except (ValueError, TypeError):
                            error_detail = ""
                        logger.warning(
                            "manual_order_failed",
                            extra={
                                "user_id": user_id,
                                "symbol": order_data["symbol"],
                                "status": exc.response.status_code,
                                "detail": error_detail or None,
                            },
                        )
                        audit_log(
                            action="manual_order_failed",
                            user_id=user_id,
                            details={
                                "symbol": order_data["symbol"],
                                "error": f"HTTP {exc.response.status_code}",
                                "detail": error_detail or None,
                            },
                        )
                        # Show failed state with visual feedback
                        if submit_action_button:
                            submit_action_button.set_external_state(ButtonState.FAILED)
                        if error_detail:
                            ui.notify(
                                f"Order failed: {error_detail} (HTTP {exc.response.status_code})",
                                type="negative",
                            )
                        else:
                            ui.notify(
                                f"Order failed: HTTP {exc.response.status_code}",
                                type="negative",
                            )
                        confirming = False
                        confirm_btn.enable()
                    except httpx.RequestError as exc:
                        logger.warning(
                            "manual_order_failed",
                            extra={
                                "user_id": user_id,
                                "symbol": order_data["symbol"],
                                "error": type(exc).__name__,
                            },
                        )
                        audit_log(
                            action="manual_order_failed",
                            user_id=user_id,
                            details={
                                "symbol": order_data["symbol"],
                                "error": type(exc).__name__,
                            },
                        )
                        # Show failed state with visual feedback
                        if submit_action_button:
                            submit_action_button.set_external_state(ButtonState.FAILED)
                        ui.notify("Order failed: network error", type="negative")
                        confirming = False
                        confirm_btn.enable()

                confirm_btn = ui.button("Confirm", on_click=confirm_order, color="primary")
                ui.button("Cancel", on_click=dialog.close)

        nonlocal preview_dialog_open
        preview_dialog_open = True
        dialog.open()

    submit_action_button = ActionButton(
        "Preview Order",
        show_preview,
        color="primary",
        manual_lifecycle=True,
    )
    with submit_container:
        submit_action_button.create().classes("w-full").props(
            "data-readonly-disable=true data-readonly-tooltip='Connection lost - read-only mode'"
        )

    await _fetch_fat_finger_thresholds()

    def _register_order_form_hotkeys(
        hotkey_manager: HotkeyManager,
        quantity_input: ui.number,
        side_selector: ui.select,
        submit_button: ActionButton,
        clear_form_callback: Callable[[], None],
    ) -> None:
        """Register order form hotkey handlers."""

        def _focus_buy() -> None:
            side_selector.value = "buy"
            quantity_input.run_method("focus")

        def _focus_sell() -> None:
            side_selector.value = "sell"
            quantity_input.run_method("focus")

        hotkey_manager.register_handler("focus_buy", _focus_buy)
        hotkey_manager.register_handler("focus_sell", _focus_sell)

        async def submit_via_hotkey() -> None:
            # Guard against hotkey bypassing disabled preview state when dialog is open
            if preview_dialog_open:
                return
            if submit_button.state == ButtonState.DEFAULT:
                await submit_button.trigger()

        hotkey_manager.register_handler("submit_order", submit_via_hotkey)
        hotkey_manager.register_handler("cancel_form", clear_form_callback)

    hotkey_manager = app.storage.client.get("hotkey_manager")
    if isinstance(hotkey_manager, HotkeyManager):
        _register_order_form_hotkeys(
            hotkey_manager,
            qty_input,
            side_select,
            submit_action_button,
            reset_form,
        )
    else:
        logger.warning("hotkey_manager_not_available", extra={"user_id": user_id})

    # Subscribe to real-time kill switch updates for instant UI responses
    async def on_kill_switch_update(data: dict[str, Any]) -> None:
        nonlocal kill_switch_engaged
        state = str(data.get("state", "")).upper()
        # Fail-closed: only mark as safe if explicitly DISENGAGED
        kill_switch_engaged = state != "DISENGAGED"

    await realtime.subscribe(kill_switch_channel(), on_kill_switch_update)

    async def on_circuit_breaker_update(data: dict[str, Any]) -> None:
        nonlocal circuit_breaker_tripped
        state = str(data.get("state", "")).upper()
        if state == "OPEN":
            circuit_breaker_tripped = False
        elif state in ("TRIPPED", "QUIET_PERIOD"):
            circuit_breaker_tripped = True
        else:
            logger.warning(
                "manual_order_invalid_circuit_breaker_payload",
                extra={"user_id": user_id, "state": state},
            )
            circuit_breaker_tripped = True

    await realtime.subscribe(circuit_breaker_channel(), on_circuit_breaker_update)

    # Fetch initial kill switch status
    async def check_initial_kill_switch() -> None:
        nonlocal kill_switch_engaged
        try:
            ks_status = await trading_client.fetch_kill_switch_status(user_id, role=user_role)
            state = str(ks_status.get("state", "")).upper()
            kill_switch_engaged = state != "DISENGAGED"
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            logger.warning(
                "kill_switch_initial_check_failed",
                extra={"user_id": user_id, "error": type(exc).__name__},
            )
            # Fail-closed: treat API failure as engaged
            kill_switch_engaged = True

    await check_initial_kill_switch()

    async def check_initial_circuit_breaker() -> None:
        nonlocal circuit_breaker_tripped
        try:
            cb_status = await trading_client.fetch_circuit_breaker_status(
                user_id, role=user_role
            )
            state = str(cb_status.get("state", "")).upper()
            circuit_breaker_tripped = state != "OPEN"
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            logger.warning(
                "circuit_breaker_initial_check_failed",
                extra={"user_id": user_id, "error": type(exc).__name__},
            )
            # Fail-closed: treat API failure as tripped
            circuit_breaker_tripped = True

    await check_initial_circuit_breaker()

    # Register cleanup for realtime subscriptions
    await lifecycle.register_cleanup_callback(client_id, realtime.cleanup)

    async def _cleanup_fat_finger_tasks() -> None:
        if fat_finger_task and not fat_finger_task.done():
            fat_finger_task.cancel()
        if adv_task and not adv_task.done():
            adv_task.cancel()
        if twap_preview_task and not twap_preview_task.done():
            twap_preview_task.cancel()

    await lifecycle.register_cleanup_callback(client_id, _cleanup_fat_finger_tasks)


__all__ = ["manual_order_page"]
