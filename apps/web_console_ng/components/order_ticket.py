"""Always-visible order entry widget for dashboard.

CRITICAL SAFETY PATTERNS:
- FAIL-CLOSED: All safety checks default to blocking until confirmed safe
- Two-phase confirmation: Preview → Confirm with fresh data refresh
- Triple defense: Cached limits → Confirm-time fresh limits → Server validation
- Kill switch verification via callback to OrderEntryContext (no direct Redis access)

SUBSCRIPTION OWNERSHIP: OrderTicket does NOT subscribe to Redis channels.
All state updates come via callbacks from OrderEntryContext.
"""

from __future__ import annotations

import asyncio
import html
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any, Literal

from nicegui import ui

from apps.web_console_ng import config
from apps.web_console_ng.components.action_button import ActionButton
from apps.web_console_ng.components.execution_gate import (
    is_model_execution_safe,
    is_strategy_execution_safe,
    normalize_execution_status,
)
from apps.web_console_ng.components.quantity_presets import QuantityPresetsComponent
from apps.web_console_ng.utils.time import parse_iso_timestamp, validate_and_normalize_symbol

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from apps.web_console_ng.core.client import AsyncTradingClient
    from apps.web_console_ng.core.connection_monitor import ConnectionMonitor
    from apps.web_console_ng.core.state_manager import UserStateManager

logger = logging.getLogger(__name__)

# Data freshness thresholds (trading safety)
POSITION_STALE_THRESHOLD_S = 30  # Block if position data > 30s old
PRICE_STALE_THRESHOLD_S = 30  # Block if market price > 30s old
BUYING_POWER_STALE_THRESHOLD_S = 60  # Block if buying power > 60s old
LIMITS_STALE_THRESHOLD_S = 300  # Block if risk limits > 5min old


@dataclass
class OrderTicketState:
    """Order ticket form state."""

    symbol: str | None = None
    side: Literal["buy", "sell"] = "buy"
    quantity: int | None = None
    order_type: Literal["market", "limit", "stop", "stop_limit"] = "market"
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    time_in_force: Literal["day", "gtc", "ioc", "fok"] = "day"


class OrderTicketComponent:
    """Always-visible order entry widget for dashboard.

    CRITICAL SAFETY PATTERNS:
    - FAIL-CLOSED: Defaults to UNSAFE state until confirmed otherwise
    - No direct Redis access - all state via callbacks from OrderEntryContext
    - Two-phase confirmation with fresh data refresh at confirm time
    """

    # Configuration
    QUANTITY_PRESETS = [100, 500, 1000]
    UNIT_PRESET_PROFILES: dict[str, list[int]] = {
        "shares": [100, 500, 1000],
        "lots": [1, 5, 10],
        "contracts": [1, 5, 10],
    }
    DEFAULT_QTY_STEP = 1
    DEFAULT_MIN_QTY = 1
    DEFAULT_QTY_UNIT = "shares"
    DEFAULT_LOT_SIZE = 100
    POSITION_DISPLAY_UNIT = "shares"
    IMPACT_WARNING_RATIO = Decimal(str(config.WORKSPACE_BP_IMPACT_WARNING_RATIO))
    IMPACT_DANGER_RATIO = Decimal(str(config.WORKSPACE_BP_IMPACT_DANGER_RATIO))

    def __init__(
        self,
        trading_client: AsyncTradingClient,
        state_manager: UserStateManager,
        connection_monitor: ConnectionMonitor,
        user_id: str,
        role: str,
        strategies: list[str],
        on_symbol_selected: Callable[[str | None], Awaitable[None]] | None = None,
        verify_circuit_breaker: Callable[[], Awaitable[bool]] | None = None,
        verify_kill_switch: Callable[[], Awaitable[bool]] | None = None,
    ) -> None:
        """Initialize OrderTicket.

        Args:
            trading_client: HTTP client for API calls.
            state_manager: Redis state manager for form recovery.
            connection_monitor: Connection health monitor.
            user_id: User ID for API calls.
            role: User role for authorization.
            strategies: Strategies for position filtering.
            on_symbol_selected: Async callback when symbol changes (None clears).
            verify_circuit_breaker: Callback to verify CB state (returns True if safe).
            verify_kill_switch: Callback to verify KS state (returns True if safe).
        """
        self._client = trading_client
        self._state_manager = state_manager
        self._connection_monitor = connection_monitor
        self._user_id = user_id
        self._role = role
        self._strategies = strategies
        self._on_symbol_selected = on_symbol_selected
        self._verify_circuit_breaker = verify_circuit_breaker
        self._verify_kill_switch = verify_kill_switch

        # UI elements (bound after create())
        self._symbol_input: ui.input | None = None
        self._side_toggle: ui.toggle | None = None
        self._buy_action_button: ui.button | None = None
        self._sell_action_button: ui.button | None = None
        self._quantity_input: ui.number | None = None
        self._quantity_label: ui.label | None = None
        self._order_type_select: ui.select | None = None
        self._limit_price_input: ui.number | None = None
        self._stop_price_input: ui.number | None = None
        self._time_in_force_select: ui.select | None = None
        self._submit_button: ActionButton | None = None
        self._clear_button: ui.button | None = None
        self._disabled_banner: ui.label | None = None
        self._position_label: ui.label | None = None
        self._buying_power_label: ui.label | None = None
        self._impact_label: ui.label | None = None
        self._impact_status_label: ui.label | None = None
        self._impact_bar_fill: ui.element | None = None
        self._quantity_presets: QuantityPresetsComponent | None = None

        # State
        self._state = OrderTicketState()
        self._current_position: int = 0
        self._buying_power: Decimal | None = None
        self._last_price: Decimal | None = None
        self._pending_client_order_id: str | None = None
        self._qty_step: int = self.DEFAULT_QTY_STEP
        self._min_qty: int = self.DEFAULT_MIN_QTY
        self._qty_unit: str = self.DEFAULT_QTY_UNIT
        self._qty_unit_size: int = 1

        # Safety state (FAIL-CLOSED defaults)
        self._kill_switch_engaged: bool = True  # Default: engaged (unsafe)
        self._circuit_breaker_tripped: bool = True  # Default: tripped (unsafe)
        self._connection_read_only: bool = True  # Default: read-only (unsafe)
        self._safety_state_loaded: bool = False  # Track if initial safety state loaded

        # Timestamp tracking for staleness checks (fail-closed)
        self._position_last_updated: datetime | None = None
        self._price_last_updated: datetime | None = None
        self._buying_power_last_updated: datetime | None = None

        # Position/risk limits (cached from risk manager, fail-closed defaults)
        self._max_position_per_symbol: int | None = None
        self._max_notional_per_order: Decimal | None = None
        self._max_total_exposure: Decimal | None = None
        self._current_total_exposure: Decimal | None = None
        self._limits_last_updated: datetime | None = None
        self._limits_loaded: bool = False

        # Timer references for cleanup
        self._position_timer: ui.timer | None = None
        self._buying_power_timer: ui.timer | None = None
        self._timer_tracker: Callable[[ui.timer], None] | None = None

        # Task tracking for periodic refresh
        self._position_refresh_task: asyncio.Task[None] | None = None
        self._buying_power_refresh_task: asyncio.Task[None] | None = None
        self._price_pulse_timer: ui.timer | None = None
        self._dom_settle_timer: ui.timer | None = None
        self._disposed: bool = False

        # Tab session ID for cross-tab isolation
        self._tab_session_id: str = uuid.uuid4().hex[:16]

        # Order snapshot for idempotency validation (set at preview time)
        self._preview_snapshot: dict[str, Any] | None = None

        # Strategy/model execution context (default monitor-only)
        self._execution_gate_enabled: bool = False
        self._strategy_status: str = "unknown"
        self._model_status: str = "unknown"
        self._execution_gate_reason: str | None = None

    async def initialize(self, timer_tracker: Callable[[ui.timer], None]) -> None:
        """Initialize with timer tracking.

        Args:
            timer_tracker: Callback to register timers for lifecycle management.
        """
        self._timer_tracker = timer_tracker
        self._start_data_refresh_timers(timer_tracker)
        await self._restore_pending_form()

        # Seed initial connection state from ConnectionMonitor
        # This ensures we don't start in false fail-closed state if already connected
        self._connection_read_only = self._connection_monitor.is_read_only()

    def create(self) -> ui.card:
        """Create and return the order ticket UI."""
        with ui.card().classes("workspace-v2-panel workspace-v2-ticket") as card:
            self._disabled_banner = ui.label("").classes(
                "hidden workspace-v2-banner workspace-v2-banner-negative"
            )

            with ui.row().classes("w-full items-center justify-between gap-2"):
                ui.label("Order Ticket").classes("workspace-v2-panel-title")
                ui.label("EXECUTION").classes("workspace-v2-kv workspace-v2-data-mono")

            with ui.row().classes("w-full gap-2 items-end"):
                with ui.column().classes("flex-1 gap-1"):
                    ui.label("SYMBOL").classes("workspace-v2-field-label")
                    self._symbol_input = ui.input(
                        placeholder="e.g., AAPL",
                        on_change=self._on_symbol_input_changed,
                    ).classes("workspace-v2-input")

                # Keep hidden toggle for compatibility with existing state sync/tests.
                self._side_toggle = ui.toggle(
                    ["buy", "sell"],
                    value="buy",
                    on_change=lambda e: self._on_side_changed(e.value),
                ).classes("hidden")

            with ui.row().classes("w-full gap-2 mt-2 items-center"):
                with ui.column().classes("flex-1 gap-0"):
                    ui.label("POSITION").classes("workspace-v2-field-label")
                    self._position_label = ui.label("--").classes("workspace-v2-data-mono text-sm")
                with ui.column().classes("flex-1 gap-0"):
                    ui.label("BUYING POWER").classes("workspace-v2-field-label")
                    self._buying_power_label = ui.label("--").classes("workspace-v2-data-mono text-sm")

            with ui.row().classes("w-full gap-2 mt-2 items-end"):
                with ui.column().classes("w-[132px] gap-1"):
                    self._quantity_label = ui.label(self._format_quantity_label()).classes(
                        "workspace-v2-field-label"
                    )
                    self._quantity_input = ui.number(
                        value=None,
                        min=self._min_qty,
                        step=self._qty_step,
                        on_change=lambda e: self._on_quantity_changed(e.value),
                    ).classes("workspace-v2-input workspace-v2-input-qty")
                with ui.column().classes("flex-1 gap-1"):
                    ui.label("QUICK SIZE").classes("workspace-v2-field-label")
                    self._quantity_presets = QuantityPresetsComponent(
                        on_preset_selected=self._on_preset_selected,
                        presets=self._resolve_quantity_presets(),
                        on_close_selected=self._on_close_preset_selected,
                        show_close=True,
                    )
                    self._quantity_presets.create()

            with ui.row().classes("w-full gap-2 mt-2 items-end"):
                with ui.column().classes("flex-1 gap-1"):
                    ui.label("ORDER TYPE").classes("workspace-v2-field-label")
                    self._order_type_select = ui.select(
                        ["market", "limit", "stop", "stop_limit"],
                        value="market",
                        on_change=lambda e: self._on_order_type_changed(e.value),
                    ).classes("workspace-v2-input")
                with ui.column().classes("w-[120px] gap-1"):
                    ui.label("TIF").classes("workspace-v2-field-label")
                    self._time_in_force_select = ui.select(
                        ["day", "gtc", "ioc", "fok"],
                        value="day",
                        on_change=lambda e: setattr(self._state, "time_in_force", e.value),
                    ).classes("workspace-v2-input")

            with ui.row().classes("w-full gap-2 mt-2 items-end"):
                self._limit_price_input = ui.number(
                    label="Limit Price",
                    format="%.2f",
                    min=0.01,
                    step=0.01,
                    on_change=lambda e: self._on_limit_price_changed(e.value),
                ).classes("workspace-v2-input workspace-v2-price-input hidden")

                self._stop_price_input = ui.number(
                    label="Stop Price",
                    format="%.2f",
                    min=0.01,
                    step=0.01,
                    on_change=lambda e: self._on_stop_price_changed(e.value),
                ).classes("workspace-v2-input hidden")

            with ui.row().classes("w-full items-center justify-between mt-2"):
                self._impact_label = ui.label("--").classes("workspace-v2-kv workspace-v2-data-mono")
                self._impact_status_label = ui.label("UNAVAILABLE").classes(
                    "workspace-v2-pill workspace-v2-pill-warning"
                )

            with ui.element("div").classes("workspace-v2-impact-track"):
                self._impact_bar_fill = ui.element("div").classes(
                    "workspace-v2-impact-fill workspace-v2-impact-fill-unavailable"
                )

            with ui.row().classes("w-full gap-2 mt-3"):
                async def _preview_buy() -> None:
                    await self._handle_side_preview("buy")

                async def _preview_sell() -> None:
                    await self._handle_side_preview("sell")

                self._buy_action_button = ui.button(
                    "BUY",
                    on_click=_preview_buy,
                ).classes("workspace-v2-action-btn workspace-v2-action-buy")
                self._sell_action_button = ui.button(
                    "SELL",
                    on_click=_preview_sell,
                ).classes("workspace-v2-action-btn workspace-v2-action-sell")
                self._clear_button = ui.button("Clear", on_click=self._clear_form).classes(
                    "workspace-v2-clear-btn"
                )

            # Keep ActionButton object for backward compatibility with tests/mocks.
            self._submit_button = ActionButton(
                label="Preview Order",
                on_click=self._handle_submit,
                icon="send",
                color="primary",
                manual_lifecycle=True,
            )
            self._update_side_action_styles()

        return card

    # ================= UI Event Handlers =================

    async def apply_dom_price_click(self, symbol: str, side: str, price: Any) -> None:
        """Prefill order form from DOM ladder price click.

        SECURITY: Validates that clicked symbol matches current selection to prevent
        stale price injection attacks via browser DevTools.
        """
        if side not in {"buy", "sell"}:
            ui.notify("Invalid side from order book", type="warning")
            return

        try:
            normalized = validate_and_normalize_symbol(symbol)
        except ValueError:
            ui.notify("Invalid symbol from order book", type="warning")
            return

        # SECURITY: Validate clicked symbol matches current symbol in order ticket
        # Prevents attack where user switches symbols but clicks on stale DOM ladder
        if self._state.symbol and self._state.symbol != normalized:
            logger.warning(
                "dom_price_click_symbol_mismatch",
                extra={
                    "clicked_symbol": normalized,
                    "current_symbol": self._state.symbol,
                    "user_id": self._user_id,
                },
            )
            ui.notify(
                f"Price click ignored: DOM shows {normalized} but ticket has {self._state.symbol}",
                type="warning",
            )
            return

        try:
            price_value = Decimal(str(price))
        except (InvalidOperation, ValueError, TypeError):
            ui.notify("Invalid price from order book", type="warning")
            return

        if not price_value.is_finite() or price_value <= 0:
            ui.notify("Invalid price from order book", type="warning")
            return

        if self._symbol_input is not None:
            self._symbol_input.set_value(normalized)
        await self._on_symbol_input_changed(type("Event", (), {"value": normalized})())

        if self._side_toggle is not None:
            self._side_toggle.set_value(side)
        self._on_side_changed(side)

        if self._order_type_select is not None:
            self._order_type_select.set_value("limit")
        self._on_order_type_changed("limit")

        if self._limit_price_input is not None:
            self._limit_price_input.set_value(float(price_value))
        self._on_limit_price_changed(float(price_value))
        self._run_dom_price_pulse(side)
        self._start_dom_settle_window()

    async def _on_symbol_input_changed(self, e: Any) -> None:
        """Handle symbol input change."""
        raw_symbol = e.value if e.value else None

        if raw_symbol:
            try:
                normalized = validate_and_normalize_symbol(raw_symbol)
                self._state.symbol = normalized
                if self._on_symbol_selected:
                    await self._on_symbol_selected(normalized)
            except ValueError:
                # Invalid symbol - clear state and notify to keep components consistent
                self._state.symbol = None
                if self._on_symbol_selected:
                    await self._on_symbol_selected(None)
        else:
            self._state.symbol = None
            if self._on_symbol_selected:
                await self._on_symbol_selected(None)

        self._update_buying_power_impact()

    def _on_side_changed(self, side: str) -> None:
        """Handle side toggle change."""
        if side in ("buy", "sell"):
            self._state.side = side  # type: ignore[assignment]
            self._update_side_action_styles()
            self._update_buying_power_impact()
            self._update_quantity_presets_context()

    def _on_quantity_changed(self, value: float | None) -> None:
        """Handle quantity input change."""
        if value is not None and value > 0:
            # Keep raw typed value during editing; enforce rule compatibility on submit.
            self._state.quantity = int(value)
        else:
            self._state.quantity = None
        self._update_buying_power_impact()

    def _on_preset_selected(self, preset: int) -> None:
        """Handle quantity preset selection."""
        normalized_qty = self._normalize_quantity(preset)
        self._state.quantity = normalized_qty
        if self._quantity_input:
            self._quantity_input.set_value(normalized_qty)
        self._update_buying_power_impact()

    def set_quantity_rules(
        self,
        *,
        qty_step: int | None,
        min_qty: int | None,
        qty_unit: str | None,
        qty_unit_size: int | None = None,
    ) -> None:
        """Update quantity stepping/minimum rules for selected symbol."""
        next_qty_step = max(1, int(qty_step)) if qty_step is not None else self.DEFAULT_QTY_STEP
        raw_min = max(1, int(min_qty)) if min_qty is not None else self.DEFAULT_MIN_QTY
        next_min_qty = self._align_min_qty_to_step(raw_min, next_qty_step)
        next_qty_unit = self._normalize_qty_unit(qty_unit)
        next_qty_unit_size = self._resolve_qty_unit_size(
            qty_unit=next_qty_unit,
            qty_step=next_qty_step,
            min_qty=next_min_qty,
            qty_unit_size=qty_unit_size,
        )

        if (
            next_qty_step == self._qty_step
            and next_min_qty == self._min_qty
            and next_qty_unit == self._qty_unit
            and next_qty_unit_size == self._qty_unit_size
        ):
            return

        self._qty_step = next_qty_step
        self._min_qty = next_min_qty
        self._qty_unit = next_qty_unit
        self._qty_unit_size = next_qty_unit_size
        self._apply_quantity_rules_to_ui()
        self._refresh_quantity_preset_profile()

        if self._state.quantity is not None:
            normalized_qty = self._normalize_quantity(self._state.quantity)
            self._state.quantity = normalized_qty
            if self._quantity_input:
                self._quantity_input.set_value(normalized_qty)

        self._update_buying_power_impact()
        self._update_quantity_presets_context()

    def reset_quantity_rules(self) -> None:
        """Reset quantity rules to safe defaults."""
        self.set_quantity_rules(qty_step=None, min_qty=None, qty_unit=None, qty_unit_size=None)

    def _on_close_preset_selected(self) -> None:
        """Prefill quantity/side to close open position (never auto-submit)."""
        if self._is_position_data_stale():
            ui.notify("Cannot prefill CLOSE: position data stale", type="warning")
            return

        try:
            current_position = int(self._current_position)
        except (TypeError, ValueError):
            current_position = 0

        if current_position == 0:
            ui.notify("No open position to close", type="warning")
            return

        close_side = "sell" if current_position > 0 else "buy"
        raw_close_qty = abs(current_position)
        qty_unit_size = max(1, self._qty_unit_size)
        qty_step = max(1, self._qty_step)
        min_qty = max(qty_step, self._min_qty)
        close_qty = raw_close_qty
        if qty_unit_size > 1:
            close_qty = raw_close_qty // qty_unit_size
            if close_qty <= 0:
                ui.notify(
                    (
                        "CLOSE prefill unavailable: position is smaller than one "
                        f"{self._qty_unit} unit"
                    ),
                    type="warning",
                )
                return
        unit_label = self._qty_unit

        if close_qty <= 0:
            return

        rule_notes: list[str] = []
        if close_qty < min_qty:
            rule_notes.append(f"below symbol minimum {min_qty} {unit_label}")
        if close_qty % qty_step != 0:
            rule_notes.append(f"off-step for {qty_step} {unit_label} increments")
        if qty_unit_size > 1:
            residual = raw_close_qty - (close_qty * qty_unit_size)
            if residual > 0:
                rule_notes.append(f"leaves residual {residual} {self.POSITION_DISPLAY_UNIT}")

        if rule_notes:
            ui.notify(
                (
                    f"CLOSE prefill uses exact {close_qty} {unit_label} "
                    f"({'; '.join(rule_notes)})"
                ),
                type="warning",
            )

        self._state.quantity = close_qty
        self._state.side = close_side  # type: ignore[assignment]

        if self._quantity_input:
            self._quantity_input.set_value(close_qty)
        if self._side_toggle:
            self._side_toggle.set_value(close_side)

        self._update_side_action_styles()
        self._update_buying_power_impact()
        self._update_quantity_presets_context()
        ui.notify(
            f"CLOSE prefill ready: {close_side.upper()} {close_qty} {unit_label} (preview required)",
            type="info",
        )

    async def _handle_side_preview(self, side: str) -> None:
        """Set side from action button and open preview flow."""
        if side not in {"buy", "sell"}:
            return
        if self._side_toggle:
            self._side_toggle.set_value(side)
        self._on_side_changed(side)
        await self._handle_submit()

    def _update_side_action_styles(self) -> None:
        """Reflect selected side in action button emphasis."""
        if self._buy_action_button is not None:
            if self._state.side == "buy":
                self._buy_action_button.classes(add="workspace-v2-action-active")
            else:
                self._buy_action_button.classes(remove="workspace-v2-action-active")
        if self._sell_action_button is not None:
            if self._state.side == "sell":
                self._sell_action_button.classes(add="workspace-v2-action-active")
            else:
                self._sell_action_button.classes(remove="workspace-v2-action-active")

    def _on_order_type_changed(self, order_type: str) -> None:
        """Handle order type selection change."""
        if order_type in ("market", "limit", "stop", "stop_limit"):
            self._state.order_type = order_type  # type: ignore[assignment]

        # Determine which price fields to show
        show_limit = order_type in ("limit", "stop_limit")
        show_stop = order_type in ("stop", "stop_limit")

        # Update visibility
        if self._limit_price_input:
            if show_limit:
                self._limit_price_input.classes(remove="hidden")
            else:
                self._limit_price_input.classes(add="hidden")
                self._limit_price_input.set_value(None)
                self._state.limit_price = None

        if self._stop_price_input:
            if show_stop:
                self._stop_price_input.classes(remove="hidden")
            else:
                self._stop_price_input.classes(add="hidden")
                self._stop_price_input.set_value(None)
                self._state.stop_price = None

        self._update_buying_power_impact()

    def _on_limit_price_changed(self, value: float | None) -> None:
        """Handle limit price input change."""
        if value is not None:
            try:
                dec_value = Decimal(str(value))
                if dec_value.is_finite() and dec_value > 0:
                    self._state.limit_price = dec_value
                else:
                    self._state.limit_price = None
            except (InvalidOperation, ValueError, TypeError):
                self._state.limit_price = None
        else:
            self._state.limit_price = None
        self._update_buying_power_impact()

    def _on_stop_price_changed(self, value: float | None) -> None:
        """Handle stop price input change."""
        if value is not None:
            try:
                dec_value = Decimal(str(value))
                if dec_value.is_finite() and dec_value > 0:
                    self._state.stop_price = dec_value
                else:
                    self._state.stop_price = None
            except (InvalidOperation, ValueError, TypeError):
                self._state.stop_price = None
        else:
            self._state.stop_price = None
        self._update_buying_power_impact()

    def _run_dom_price_pulse(self, side: str) -> None:
        """Apply short pulse animation on limit price input after DOM click."""
        if self._limit_price_input is None:
            return
        pulse_class = (
            "workspace-v2-price-pulse-buy" if side == "buy" else "workspace-v2-price-pulse-sell"
        )
        self._limit_price_input.classes(
            add=f"workspace-v2-price-input {pulse_class}",
            remove="workspace-v2-price-pulse-buy workspace-v2-price-pulse-sell",
        )

        if self._price_pulse_timer is not None:
            self._price_pulse_timer.cancel()
        self._price_pulse_timer = ui.timer(
            0.4,
            lambda: self._limit_price_input
            and self._limit_price_input.classes(
                remove="workspace-v2-price-pulse-buy workspace-v2-price-pulse-sell"
            ),
            once=True,
        )
        if self._timer_tracker and self._price_pulse_timer is not None:
            self._timer_tracker(self._price_pulse_timer)

    def _start_dom_settle_window(self) -> None:
        """Temporarily disable trade action buttons after rapid DOM click updates."""
        for button in (self._buy_action_button, self._sell_action_button):
            if button is not None:
                button.set_enabled(False)

        if self._dom_settle_timer is not None:
            self._dom_settle_timer.cancel()
        self._dom_settle_timer = ui.timer(0.15, self._finish_dom_settle, once=True)
        if self._timer_tracker and self._dom_settle_timer is not None:
            self._timer_tracker(self._dom_settle_timer)

    def _finish_dom_settle(self) -> None:
        """Re-evaluate action button availability after DOM settle debounce."""
        disabled, _reason = self._should_disable_submission()
        enabled = not disabled
        for button in (self._buy_action_button, self._sell_action_button):
            if button is not None:
                button.set_enabled(enabled)

    def _is_trade_action_locked(self) -> bool:
        """Return whether action buttons must stay disabled due global safety state."""
        return self._connection_read_only or self._kill_switch_engaged or self._circuit_breaker_tripped

    # ================= Safety State Callbacks =================

    def set_connection_state(self, state: str, is_read_only: bool) -> None:
        """Called by OrderEntryContext when connection state changes."""
        self._connection_read_only = is_read_only
        if is_read_only:
            normalized_state = str(state).upper()
            known_states = {"CONNECTED", "DISCONNECTED", "RECONNECTING", "DEGRADED", "UNKNOWN"}
            safe_state = normalized_state if normalized_state in known_states else "UNKNOWN"
            self._set_ui_disabled(True, f"Connection: {safe_state}")
        else:
            if not self._kill_switch_engaged and not self._circuit_breaker_tripped:
                self._set_ui_disabled(False, "")

    def set_kill_switch_state(self, engaged: bool, reason: str | None) -> None:
        """Called by OrderEntryContext when kill switch state changes."""
        self._kill_switch_engaged = engaged
        self._safety_state_loaded = True
        if engaged:
            safe_reason = html.escape(reason or "Trading halted")
            self._set_ui_disabled(True, f"Kill switch: {safe_reason}")
        else:
            if not self._connection_read_only and not self._circuit_breaker_tripped:
                self._set_ui_disabled(False, "")

    def set_circuit_breaker_state(self, tripped: bool, reason: str | None) -> None:
        """Called by OrderEntryContext when circuit breaker state changes."""
        self._circuit_breaker_tripped = tripped
        if tripped:
            safe_reason = html.escape(reason or "Trading halted")
            self._set_ui_disabled(True, f"Circuit breaker: {safe_reason}")
        else:
            if not self._connection_read_only and not self._kill_switch_engaged:
                self._set_ui_disabled(False, "")

    def set_price_data(
        self, symbol: str, price: Decimal | None, timestamp: datetime | None
    ) -> None:
        """Called by OrderEntryContext when price data updates."""
        if symbol != self._state.symbol:
            return

        self._last_price = price
        self._price_last_updated = timestamp
        self._update_buying_power_impact()
        self._update_quantity_presets_context()

    def set_position_data(self, symbol: str, qty: int, timestamp: datetime | None) -> None:
        """Called by OrderEntryContext when position data updates."""
        if symbol != self._state.symbol:
            return

        self._current_position = qty
        self._position_last_updated = timestamp
        self._update_position_display()
        self._update_quantity_presets_context()

    def set_buying_power(self, buying_power: Decimal | None, timestamp: datetime | None) -> None:
        """Called by OrderEntryContext when buying power updates."""
        self._buying_power = buying_power
        self._buying_power_last_updated = timestamp
        self._update_buying_power_display()
        self._update_buying_power_impact()
        self._update_quantity_presets_context()

    def set_risk_limits(
        self,
        max_position_per_symbol: int | None,
        max_notional_per_order: Decimal | None,
        max_total_exposure: Decimal | None,
        timestamp: datetime | None,
    ) -> None:
        """Called by OrderEntryContext when risk limits update."""
        self._max_position_per_symbol = max_position_per_symbol
        self._max_notional_per_order = max_notional_per_order
        self._max_total_exposure = max_total_exposure
        self._limits_last_updated = timestamp
        self._limits_loaded = True
        self._update_quantity_presets_context()

    def set_total_exposure(self, current_total_exposure: Decimal | None) -> None:
        """Called by OrderEntryContext when portfolio total exposure updates.

        CRITICAL: Required for max_total_exposure limit enforcement.
        If max_total_exposure is configured but set_total_exposure is never called,
        _check_position_limits will return "Cannot verify exposure limit" and
        block all submissions.

        Args:
            current_total_exposure: Sum of absolute notional values across all positions.
                None indicates exposure data is unavailable (fail-closed: blocks submission).
        """
        self._current_total_exposure = current_total_exposure

    def set_strategy_model_context(
        self,
        *,
        strategy_status: str | None,
        model_status: str | None,
        gate_enabled: bool,
        gate_reason: str | None = None,
    ) -> None:
        """Update strategy/model execution context used by submit safety gate.

        Args:
            strategy_status: Strategy runtime status (active/idle/ready/inactive/unknown).
            model_status: Model runtime status (active/testing/ready/failed/inactive/unknown).
            gate_enabled: Whether strategy/model gating should be enforced.
            gate_reason: Optional reason produced by upstream context resolver.
        """
        self._strategy_status = normalize_execution_status(strategy_status)
        self._model_status = normalize_execution_status(model_status)
        self._execution_gate_enabled = bool(gate_enabled)
        self._execution_gate_reason = str(gate_reason) if gate_reason else None

    async def on_symbol_changed(self, symbol: str | None) -> None:
        """Called by OrderEntryContext when selected symbol changes externally."""
        self._state.symbol = symbol
        self.reset_quantity_rules()

        # Reset symbol-scoped state only - NOT risk limits
        # Risk limits are global (not per-symbol), so keep them intact
        self._last_price = None
        self._price_last_updated = None
        self._current_position = 0
        self._position_last_updated = None
        # Fail-closed symbol handoff: clear prior strategy/model state so
        # submissions cannot inherit authorization from the previous symbol.
        should_fail_closed_gate = (
            self._execution_gate_enabled
            or self._strategy_status != "unknown"
            or self._model_status != "unknown"
        )
        self._execution_gate_enabled = should_fail_closed_gate
        self._strategy_status = "unknown"
        self._model_status = "unknown"
        self._execution_gate_reason = (
            "Refreshing strategy/model execution context for selected symbol"
            if should_fail_closed_gate
            else None
        )
        # DO NOT reset _limits_loaded/_limits_last_updated - limits are global

        if self._symbol_input and self._symbol_input.value != symbol:
            self._symbol_input.set_value(symbol or "")

        self._update_ui_from_state()

    # ================= UI Updates =================

    def _format_quantity_label(self) -> str:
        """Return uppercase quantity label based on unit."""
        return f"QTY ({self._qty_unit.upper()})"

    def _normalize_qty_unit(self, qty_unit: str | None) -> str:
        """Normalize quantity unit to safe labels."""
        candidate = str(qty_unit or "").strip().lower()
        if candidate in {"shares", "lots", "contracts"}:
            return candidate
        return self.DEFAULT_QTY_UNIT

    def _resolve_qty_unit_size(
        self,
        *,
        qty_unit: str,
        qty_step: int,
        min_qty: int,
        qty_unit_size: int | None,
    ) -> int:
        """Resolve canonical-unit multiplier for entered quantity units."""
        if qty_unit_size is not None:
            return max(1, int(qty_unit_size))
        if qty_unit == "lots" and qty_step <= 10 and min_qty <= 10:
            return self.DEFAULT_LOT_SIZE
        return 1

    def _normalize_quantity(self, qty: int) -> int:
        """Clamp quantity to configured min/step constraints."""
        if qty <= 0:
            return self._min_qty
        clamped = max(self._min_qty, qty)
        if self._qty_step <= 1:
            return clamped
        return self._min_qty + ((clamped - self._min_qty) // self._qty_step) * self._qty_step

    def _raw_quantity(self, qty: int | None) -> int | None:
        """Parse positive quantity in the currently displayed ticket unit."""
        if qty is None:
            return None
        raw = int(qty)
        if raw <= 0:
            return None
        return raw

    def _canonical_quantity(self, qty: int | None) -> int | None:
        """Return quantity in canonical position units (shares/contracts)."""
        raw_quantity = self._raw_quantity(qty)
        if raw_quantity is None:
            return None
        canonical = raw_quantity * max(1, self._qty_unit_size)
        if canonical <= 0:
            return None
        return canonical

    def _format_quantity_limit(self, qty: int) -> str:
        """Format quantity limits with canonical units and optional ticket-unit hint."""
        if self._qty_unit == self.POSITION_DISPLAY_UNIT:
            return f"{qty} {self.POSITION_DISPLAY_UNIT}"
        return f"{qty} {self.POSITION_DISPLAY_UNIT} ({self._qty_unit})"

    def _align_min_qty_to_step(self, min_qty: int, qty_step: int) -> int:
        """Align minimum quantity upward to the nearest step-compatible value."""
        if qty_step <= 1:
            return max(1, min_qty)
        return max(qty_step, ((min_qty + qty_step - 1) // qty_step) * qty_step)

    def _apply_quantity_rules_to_ui(self) -> None:
        """Apply quantity rules to UI controls."""
        if self._quantity_label:
            self._quantity_label.set_text(self._format_quantity_label())
        if self._quantity_input:
            self._quantity_input.props(f"min={self._min_qty} step={self._qty_step}")

    def _resolve_quantity_presets(self) -> list[int]:
        """Resolve contextual quick-size presets for current unit/rules."""
        base_presets = self.UNIT_PRESET_PROFILES.get(self._qty_unit, self.QUANTITY_PRESETS)
        resolved: list[int] = []
        seen: set[int] = set()
        for preset in base_presets:
            normalized = self._normalize_quantity(int(preset))
            if normalized <= 0 or normalized in seen:
                continue
            seen.add(normalized)
            resolved.append(normalized)

        if not resolved:
            return [self._min_qty]
        return resolved

    def _refresh_quantity_preset_profile(self) -> None:
        """Recompute and apply contextual quick-size presets."""
        if self._quantity_presets:
            self._quantity_presets.set_presets(self._resolve_quantity_presets())

    def _update_position_display(self) -> None:
        """Update position label."""
        if self._position_label:
            if self._state.symbol:
                self._position_label.set_text(
                    f"{self._current_position:+d} {self.POSITION_DISPLAY_UNIT}"
                )
            else:
                self._position_label.set_text("--")

    def _update_buying_power_display(self) -> None:
        """Update buying power label."""
        if self._buying_power_label:
            if self._buying_power is not None:
                self._buying_power_label.set_text(f"${self._buying_power:,.2f}")
            else:
                self._buying_power_label.set_text("--")

    def _update_buying_power_impact(self) -> None:
        """Update buying power impact display."""
        impact = self._calculate_buying_power_impact()
        if self._impact_label:
            if impact["notional"] is not None and impact["percentage"] is not None:
                self._impact_label.set_text(
                    f"${impact['notional']:,.2f} ({impact['percentage']:.1f}% of limit)"
                )
            else:
                self._impact_label.set_text("-- (risk limit unavailable)")

        status = str(impact.get("status") or "unavailable").upper()
        if self._impact_status_label:
            self._impact_status_label.text = status
            self._impact_status_label.classes(
                remove=(
                    "workspace-v2-pill-positive "
                    "workspace-v2-pill-warning "
                    "workspace-v2-pill-negative "
                    "workspace-v2-pill-muted"
                )
            )
            if status == "NORMAL":
                self._impact_status_label.classes(add="workspace-v2-pill-positive")
            elif status == "WARNING":
                self._impact_status_label.classes(add="workspace-v2-pill-warning")
            elif status == "DANGER":
                self._impact_status_label.classes(add="workspace-v2-pill-negative")
            else:
                self._impact_status_label.classes(add="workspace-v2-pill-muted")

        self._update_impact_gauge(impact)

    def _update_impact_gauge(self, impact: dict[str, Any]) -> None:
        """Update visual gauge width and severity class."""
        if self._impact_bar_fill is None:
            return

        ratio = impact.get("ratio")
        if not isinstance(ratio, Decimal):
            self._impact_bar_fill.style("width: 0%")
            self._impact_bar_fill.classes(
                remove="workspace-v2-impact-fill-normal workspace-v2-impact-fill-warning workspace-v2-impact-fill-danger",
                add="workspace-v2-impact-fill-unavailable",
            )
            return

        clamped = max(Decimal("0"), min(Decimal("1"), ratio))
        pct = float(clamped * Decimal(100))
        self._impact_bar_fill.style(f"width: {pct:.1f}%")
        status = str(impact.get("status") or "unavailable")
        self._impact_bar_fill.classes(
            remove="workspace-v2-impact-fill-unavailable workspace-v2-impact-fill-normal workspace-v2-impact-fill-warning workspace-v2-impact-fill-danger"
        )
        if status == "normal":
            self._impact_bar_fill.classes(add="workspace-v2-impact-fill-normal")
        elif status == "warning":
            self._impact_bar_fill.classes(add="workspace-v2-impact-fill-warning")
        else:
            self._impact_bar_fill.classes(add="workspace-v2-impact-fill-danger")

    def _update_quantity_presets_context(self) -> None:
        """Update quantity presets with current context."""
        if self._quantity_presets:
            qty_unit_size = max(1, self._qty_unit_size)
            current_price = self._last_price
            effective_price = self._state.limit_price or self._state.stop_price
            current_position = self._current_position
            max_position_per_symbol = self._max_position_per_symbol
            if qty_unit_size > 1:
                unit_size_decimal = Decimal(qty_unit_size)
                current_price = (
                    (self._last_price * unit_size_decimal)
                    if self._last_price is not None
                    else None
                )
                effective_price = (
                    effective_price * unit_size_decimal
                    if effective_price is not None
                    else None
                )
                current_position = int(self._current_position / qty_unit_size)
                if self._max_position_per_symbol is not None:
                    max_position_per_symbol = int(self._max_position_per_symbol / qty_unit_size)
            self._quantity_presets.update_context(
                buying_power=self._buying_power,
                current_price=current_price,
                current_position=current_position,
                max_position_per_symbol=max_position_per_symbol,
                max_notional_per_order=self._max_notional_per_order,
                side=self._state.side,
                effective_price=effective_price,
                qty_step=self._qty_step,
                min_qty=self._min_qty,
            )

    def _update_ui_from_state(self) -> None:
        """Update all UI elements from internal state."""
        self._update_side_action_styles()
        self._update_position_display()
        self._update_buying_power_display()
        self._update_buying_power_impact()
        self._update_quantity_presets_context()

    def _sync_inputs_from_state(self) -> None:
        """Sync all input controls from internal state.

        CRITICAL: Must be called after restoring state to ensure UI matches state.
        Prevents silent mismatch where user sees default values but submits restored values.
        """
        if self._symbol_input:
            self._symbol_input.set_value(self._state.symbol or "")

        if self._side_toggle:
            self._side_toggle.set_value(self._state.side)

        if self._quantity_input:
            self._quantity_input.set_value(self._state.quantity)

        if self._order_type_select:
            self._order_type_select.set_value(self._state.order_type)

        # Handle price input visibility based on order type
        show_limit = self._state.order_type in ("limit", "stop_limit")
        show_stop = self._state.order_type in ("stop", "stop_limit")

        if self._limit_price_input:
            if show_limit:
                self._limit_price_input.classes(remove="hidden")
                if self._state.limit_price is not None:
                    self._limit_price_input.set_value(float(self._state.limit_price))
            else:
                self._limit_price_input.classes(add="hidden")
                self._limit_price_input.set_value(None)

        if self._stop_price_input:
            if show_stop:
                self._stop_price_input.classes(remove="hidden")
                if self._state.stop_price is not None:
                    self._stop_price_input.set_value(float(self._state.stop_price))
            else:
                self._stop_price_input.classes(add="hidden")
                self._stop_price_input.set_value(None)

        if self._time_in_force_select:
            self._time_in_force_select.set_value(self._state.time_in_force)

    def _set_ui_disabled(self, disabled: bool, reason: str) -> None:
        """Set UI elements to disabled state.

        CRITICAL: Disables ALL form inputs to prevent error-spam UX when
        trading is disabled. Users can't submit anyway, so inputs should
        be visually disabled to indicate this.
        """
        # Disable all input elements
        if self._symbol_input:
            self._symbol_input.set_enabled(not disabled)

        if self._side_toggle:
            self._side_toggle.set_enabled(not disabled)

        if self._quantity_input:
            self._quantity_input.set_enabled(not disabled)

        if self._order_type_select:
            self._order_type_select.set_enabled(not disabled)

        if self._limit_price_input:
            self._limit_price_input.set_enabled(not disabled)

        if self._stop_price_input:
            self._stop_price_input.set_enabled(not disabled)

        if self._time_in_force_select:
            self._time_in_force_select.set_enabled(not disabled)

        # Disable quantity presets
        if self._quantity_presets:
            self._quantity_presets.set_enabled(not disabled)

        # Disable submit button via ActionButton's underlying button
        if self._submit_button and self._submit_button._button:
            self._submit_button._button.set_enabled(not disabled)

        # Disable split action buttons
        if self._buy_action_button:
            self._buy_action_button.set_enabled(not disabled)
        if self._sell_action_button:
            self._sell_action_button.set_enabled(not disabled)

        # Disable Clear button
        if self._clear_button:
            self._clear_button.set_enabled(not disabled)

        if disabled and reason:
            self._show_disabled_banner(reason)
        else:
            self._hide_disabled_banner()

    def _show_disabled_banner(self, reason: str) -> None:
        """Display prominent banner explaining why trading is disabled."""
        if self._disabled_banner:
            self._disabled_banner.set_text(reason)
            self._disabled_banner.classes(remove="hidden")

    def _hide_disabled_banner(self) -> None:
        """Hide the disabled reason banner."""
        if self._disabled_banner:
            self._disabled_banner.classes(add="hidden")

    # ================= Safety Checks =================

    def _should_disable_submission(self) -> tuple[bool, str]:
        """Check if order submission should be disabled.

        FAIL-CLOSED: Returns (True, reason) if ANY safety condition fails.
        """
        if not self._safety_state_loaded:
            return (True, "Safety state loading...")

        if self._connection_read_only:
            return (True, "Connection unavailable")

        if self._kill_switch_engaged:
            return (True, "Kill switch engaged")

        if self._circuit_breaker_tripped:
            return (True, "Circuit breaker tripped")

        if not self._state.symbol:
            return (True, "Select a symbol")

        raw_quantity = self._raw_quantity(self._state.quantity)
        quantity = self._canonical_quantity(raw_quantity)
        if raw_quantity is None or quantity is None:
            return (True, "Enter quantity")

        # Safety policy: permit strict risk-reducing exits even below lot min/step
        # so operators can flatten residual risk during stressed conditions.
        if not self._is_risk_reducing_order():
            if raw_quantity < self._min_qty:
                minimum_display_qty = self._canonical_quantity(self._min_qty) or self._min_qty
                return (True, f"Minimum quantity is {self._format_quantity_limit(minimum_display_qty)}")

            normalized_quantity = self._normalize_quantity(raw_quantity)
            if normalized_quantity != raw_quantity:
                step_display_qty = self._canonical_quantity(self._qty_step) or self._qty_step
                return (
                    True,
                    f"Quantity must increment by {self._format_quantity_limit(step_display_qty)}",
                )

        if self._is_position_data_stale():
            return (True, "Position data stale")

        if self._is_price_data_stale():
            return (True, "Price data stale")

        if self._is_buying_power_stale():
            return (True, "Buying power data stale")

        gate_block_reason = self._get_execution_gate_block_reason()
        if gate_block_reason:
            return (True, gate_block_reason)

        price_error = self._validate_order_type_prices()
        if price_error:
            return (True, price_error)

        if not self._limits_loaded:
            return (True, "Risk limits loading...")

        if self._is_limits_stale():
            return (True, "Risk limits stale")

        limit_violation = self._check_position_limits()
        if limit_violation:
            return (True, limit_violation)

        return (False, "")

    def _get_execution_gate_block_reason(self) -> str | None:
        """Return strategy/model gate block reason for risk-increasing orders."""
        if not self._execution_gate_enabled:
            return None

        strategy_safe = is_strategy_execution_safe(self._strategy_status)
        model_safe = is_model_execution_safe(self._model_status)
        if strategy_safe and model_safe:
            return None

        # Safety policy: allow risk-reducing orders even when context is unsafe.
        if self._is_risk_reducing_order():
            return None

        if self._execution_gate_reason:
            return f"Execution gated: {self._execution_gate_reason}"
        if not strategy_safe:
            return f"Execution gated: strategy is {self._strategy_status.upper()}"
        if not model_safe:
            return f"Execution gated: model is {self._model_status.upper()}"
        return "Execution gated: strategy/model context unavailable"

    def _is_risk_reducing_order(self) -> bool:
        """Return True only when order strictly reduces current open exposure."""
        qty = self._canonical_quantity(self._state.quantity)
        if qty is None:
            return False

        if self._current_position > 0 and self._state.side == "sell":
            return qty <= self._current_position
        if self._current_position < 0 and self._state.side == "buy":
            return qty <= abs(self._current_position)
        return False

    def _is_position_data_stale(self) -> bool:
        """Check if position data is too old for safe trading."""
        if self._position_last_updated is None:
            return True
        age_s = (datetime.now(UTC) - self._position_last_updated).total_seconds()
        return age_s > POSITION_STALE_THRESHOLD_S

    def _is_price_data_stale(self) -> bool:
        """Check if market price is too old for safe trading."""
        if self._price_last_updated is None:
            return True
        age_s = (datetime.now(UTC) - self._price_last_updated).total_seconds()
        return age_s > PRICE_STALE_THRESHOLD_S

    def _is_buying_power_stale(self) -> bool:
        """Check if buying power data is too old for safe trading."""
        if self._buying_power_last_updated is None:
            return True
        age_s = (datetime.now(UTC) - self._buying_power_last_updated).total_seconds()
        return age_s > BUYING_POWER_STALE_THRESHOLD_S

    def _is_limits_stale(self) -> bool:
        """Check if position/risk limits are too old for safe trading."""
        if self._limits_last_updated is None:
            return True
        age_s = (datetime.now(UTC) - self._limits_last_updated).total_seconds()
        return age_s > LIMITS_STALE_THRESHOLD_S

    def _validate_preview_snapshot(self) -> bool:
        """Validate current form state matches the preview snapshot.

        CRITICAL: Prevents idempotency mismatch where client_order_id was minted
        for one order but user edited the form while preview dialog was open.
        """
        if self._preview_snapshot is None:
            return False

        current = {
            "symbol": self._state.symbol,
            "side": self._state.side,
            "quantity": self._state.quantity,
            "order_type": self._state.order_type,
            "limit_price": str(self._state.limit_price or ""),
            "stop_price": str(self._state.stop_price or ""),
            "time_in_force": self._state.time_in_force,
        }

        return current == self._preview_snapshot

    def _validate_order_type_prices(self) -> str | None:
        """Validate required prices based on order type."""
        order_type = self._state.order_type
        limit_price = self._state.limit_price
        stop_price = self._state.stop_price

        if order_type == "market":
            return None

        if order_type == "limit":
            if limit_price is None:
                return "Limit orders require a limit price"
            if limit_price <= 0:
                return "Limit price must be positive"

        elif order_type == "stop":
            if stop_price is None:
                return "Stop orders require a stop price"
            if stop_price <= 0:
                return "Stop price must be positive"

        elif order_type == "stop_limit":
            if limit_price is None:
                return "Stop-limit orders require a limit price"
            if limit_price <= 0:
                return "Limit price must be positive"
            if stop_price is None:
                return "Stop-limit orders require a stop price"
            if stop_price <= 0:
                return "Stop price must be positive"

            # Validate stop-limit price relationship
            side = self._state.side
            if side == "buy" and limit_price > stop_price:
                return (
                    f"Buy stop-limit: limit (${limit_price:.2f}) must be at or below "
                    f"stop (${stop_price:.2f})"
                )
            if side == "sell" and limit_price < stop_price:
                return (
                    f"Sell stop-limit: limit (${limit_price:.2f}) must be at or above "
                    f"stop (${stop_price:.2f})"
                )

        return None

    def _check_position_limits(self) -> str | None:
        """Check if proposed order violates position limits."""
        if not self._state.symbol:
            return None
        proposed_qty = self._canonical_quantity(self._state.quantity)
        if proposed_qty is None:
            return None
        if self._state.side == "sell":
            proposed_position = self._current_position - proposed_qty
        else:
            proposed_position = self._current_position + proposed_qty

        if self._max_position_per_symbol is not None:
            if abs(proposed_position) > self._max_position_per_symbol:
                return (
                    "Order exceeds position limit "
                    f"({self._format_quantity_limit(self._max_position_per_symbol)})"
                )

        effective_price = self._get_effective_order_price()
        order_notional: Decimal | None = None
        if effective_price is not None:
            order_notional = Decimal(proposed_qty) * effective_price

        if self._max_notional_per_order is not None and order_notional is not None:
            if order_notional > self._max_notional_per_order:
                return f"Order exceeds max notional (${self._max_notional_per_order:,.0f})"

        if self._max_total_exposure is not None and order_notional is not None:
            if self._current_total_exposure is None:
                return "Cannot verify exposure limit"

            # Fail-closed: require valid price to calculate current exposure
            if self._last_price is None and self._current_position != 0:
                return "Cannot verify exposure limit (price unavailable)"

            current_symbol_notional = abs(
                Decimal(self._current_position) * (self._last_price or Decimal(0))
            )

            if self._state.side == "buy":
                proposed_symbol_pos = self._current_position + proposed_qty
            else:
                proposed_symbol_pos = self._current_position - proposed_qty

            proposed_symbol_notional = abs(
                Decimal(proposed_symbol_pos) * (effective_price or self._last_price or Decimal(0))
            )

            projected_exposure = (
                self._current_total_exposure - current_symbol_notional + proposed_symbol_notional
            )

            if projected_exposure > self._max_total_exposure:
                return f"Order exceeds total exposure limit (${self._max_total_exposure:,.0f})"

        return None

    def _get_effective_order_price(self) -> Decimal | None:
        """Get effective price for order calculations.

        For stop orders, uses side-aware worst-case pricing:
        - Buy stop: max(stop, last) - assumes buy at higher price
        - Sell stop: min(stop, last) - assumes sell at lower price

        This ensures notional estimates are conservative in the correct direction
        and avoids blocking risk-reducing sell stops with overstated notional.
        """
        order_type = self._state.order_type

        if order_type in ("limit", "stop_limit"):
            return self._state.limit_price or self._last_price

        if order_type == "stop":
            stop_price = self._state.stop_price
            if stop_price and self._last_price:
                # Side-aware worst-case: buy=max, sell=min
                if self._state.side == "buy":
                    return max(stop_price, self._last_price)
                else:
                    return min(stop_price, self._last_price)
            return stop_price or self._last_price

        return self._last_price

    def _calculate_buying_power_impact(self) -> dict[str, Any]:
        """Calculate order's impact on buying power."""
        quantity = self._canonical_quantity(self._state.quantity)
        if quantity is None:
            return {
                "notional": None,
                "percentage": None,
                "remaining": None,
                "warning": False,
                "ratio": None,
                "status": "unavailable",
                "effective_limit": None,
            }

        effective_price = self._get_effective_order_price()
        if effective_price is None:
            return {
                "notional": None,
                "percentage": None,
                "remaining": None,
                "warning": False,
                "ratio": None,
                "status": "unavailable",
                "effective_limit": None,
            }

        notional = effective_price * Decimal(quantity)
        effective_limits: list[Decimal] = []

        if self._buying_power is not None and self._buying_power > 0:
            effective_limits.append(self._buying_power)
        if self._max_notional_per_order is not None and self._max_notional_per_order > 0:
            effective_limits.append(self._max_notional_per_order)

        if self._max_total_exposure is not None and self._max_total_exposure > 0:
            if self._current_total_exposure is None:
                return {
                    "notional": notional,
                    "percentage": None,
                    "remaining": None,
                    "warning": True,
                    "ratio": None,
                    "status": "unavailable",
                    "effective_limit": None,
                }
            headroom = self._max_total_exposure - self._current_total_exposure
            effective_limits.append(max(Decimal("0"), headroom))

        if not effective_limits:
            return {
                "notional": notional,
                "percentage": None,
                "remaining": None,
                "warning": True,
                "ratio": None,
                "status": "unavailable",
                "effective_limit": None,
            }

        effective_limit = min(effective_limits)
        if effective_limit <= 0:
            return {
                "notional": notional,
                "percentage": None,
                "remaining": Decimal("0"),
                "warning": True,
                "ratio": Decimal("1"),
                "status": "danger",
                "effective_limit": effective_limit,
            }

        ratio = notional / effective_limit
        percentage = ratio * 100
        remaining = effective_limit - notional

        warning_ratio = self.IMPACT_WARNING_RATIO
        danger_ratio = self.IMPACT_DANGER_RATIO

        status = "normal"
        if ratio >= danger_ratio:
            status = "danger"
        elif ratio >= warning_ratio:
            status = "warning"

        return {
            "notional": notional,
            "percentage": percentage,
            "remaining": remaining,
            "warning": ratio >= warning_ratio,
            "ratio": ratio,
            "status": status,
            "effective_limit": effective_limit,
        }

    # ================= Order Submission =================

    async def _handle_submit(self) -> bool | None:
        """Handle order submission with two-phase confirmation."""
        disabled, reason = self._should_disable_submission()
        if disabled:
            ui.notify(f"Cannot submit: {reason}", type="negative")
            return False

        self._pending_client_order_id = await self._get_or_create_client_order_id()

        # Snapshot form state at preview time for idempotency validation
        self._preview_snapshot = {
            "symbol": self._state.symbol,
            "side": self._state.side,
            "quantity": self._state.quantity,
            "order_type": self._state.order_type,
            "limit_price": str(self._state.limit_price or ""),
            "stop_price": str(self._state.stop_price or ""),
            "time_in_force": self._state.time_in_force,
        }

        await self._show_preview_dialog()
        return None

    async def _show_preview_dialog(self) -> None:
        """Show order preview dialog for confirmation."""
        with ui.dialog() as dialog, ui.card().classes("p-4"):
            ui.label("Order Preview").classes("text-lg font-bold mb-2")

            ui.label(f"Symbol: {self._state.symbol}")
            ui.label(f"Side: {self._state.side.upper()}")
            ui.label(f"Quantity: {self._state.quantity}")
            ui.label(f"Type: {self._state.order_type}")

            if self._state.limit_price:
                ui.label(f"Limit Price: ${self._state.limit_price:.2f}")
            if self._state.stop_price:
                ui.label(f"Stop Price: ${self._state.stop_price:.2f}")

            impact = self._calculate_buying_power_impact()
            if impact["notional"]:
                ui.label(f"Estimated Value: ${impact['notional']:,.2f}")

            with ui.row().classes("gap-4 mt-4"):

                async def confirm() -> None:
                    result = await self._confirm_and_submit()
                    if result:
                        dialog.close()
                    else:
                        dialog.close()

                ui.button("Confirm Order", on_click=confirm).classes("bg-green-600")
                ui.button("Cancel", on_click=dialog.close)

        dialog.open()

    async def _confirm_and_submit(self) -> bool:
        """Phase 2: Fresh API check and submit with idempotency.

        CRITICAL: Re-validates ALL safety conditions at submit time, not just kill switch/CB.
        Data staleness can occur while the confirmation dialog is open.
        """
        # Validate form hasn't changed since preview (idempotency guard)
        # If user edited form while dialog was open, client_order_id would be for wrong order
        if not self._validate_preview_snapshot():
            ui.notify("Order details changed. Please close and preview again.", type="warning")
            return False

        # Re-check ALL safety conditions (data may have gone stale during confirm dialog)
        # This includes: connection, kill switch, circuit breaker, staleness, limits
        disabled, reason = self._should_disable_submission()
        if disabled:
            ui.notify(f"Cannot submit: {reason}", type="negative")
            return False

        # Kill switch verification (authoritative, not cached)
        if self._verify_kill_switch:
            try:
                is_safe = await self._verify_kill_switch()
                if not is_safe:
                    ui.notify("Cannot submit: Kill switch engaged", type="negative")
                    return False
            except Exception as exc:
                logger.warning(f"Kill switch verification failed: {exc}")
                ui.notify("Cannot submit: Unable to verify kill switch", type="negative")
                return False
        else:
            logger.error("Kill switch verification callback not configured")
            ui.notify("Cannot submit: Kill switch verification unavailable", type="negative")
            return False

        # Circuit breaker verification
        if self._verify_circuit_breaker:
            try:
                is_safe = await self._verify_circuit_breaker()
                if not is_safe:
                    ui.notify("Cannot submit: Circuit breaker tripped", type="negative")
                    return False
            except Exception as exc:
                logger.warning(f"Circuit breaker verification failed: {exc}")
                ui.notify("Cannot submit: Unable to verify circuit breaker", type="negative")
                return False
        else:
            logger.warning("Circuit breaker verification callback not configured")
            ui.notify("Cannot submit: Circuit breaker verification unavailable", type="negative")
            return False

        # Validate symbol
        try:
            normalized_symbol = validate_and_normalize_symbol(self._state.symbol or "")
        except ValueError as exc:
            logger.warning(f"Invalid symbol at submit: {self._state.symbol!r} - {exc}")
            ui.notify("Cannot submit: Invalid symbol format", type="negative")
            return False

        # Build order data
        canonical_quantity = self._canonical_quantity(self._state.quantity)
        if canonical_quantity is None:
            ui.notify("Cannot submit: Enter quantity", type="negative")
            return False

        order_data: dict[str, Any] = {
            "symbol": normalized_symbol,
            "side": self._state.side,
            "qty": canonical_quantity,
            "order_type": self._state.order_type,
            "time_in_force": self._state.time_in_force,
            "client_order_id": self._pending_client_order_id,
        }
        if self._state.limit_price is not None:
            order_data["limit_price"] = str(self._state.limit_price)
        if self._state.stop_price is not None:
            order_data["stop_price"] = str(self._state.stop_price)

        # Final connection check
        if self._connection_read_only:
            ui.notify("Cannot submit: Connection lost", type="negative")
            return False

        # Submit order
        try:
            response = await self._client.submit_manual_order(
                order_data=order_data,
                user_id=self._user_id,
                role=self._role,
            )

            if response.get("status") in ("pending_new", "new", "accepted"):
                ui.notify(f"Order submitted: {response.get('client_order_id')}", type="positive")
                await self._clear_form()
                form_key = f"order_entry:{self._tab_session_id}"
                await self._state_manager.clear_pending_form(form_key)
                return True
            else:
                ui.notify(
                    f"Order failed: {response.get('message', 'Unknown error')}", type="negative"
                )
                return False
        except Exception as exc:
            logger.error(f"Order submission failed: {exc}")
            ui.notify(f"Order failed: {exc}", type="negative")
            return False

    # ================= Idempotency =================

    def _generate_intent_id(self) -> str:
        """Generate a new unique intent ID."""
        intent_id: str = uuid.uuid4().hex
        return intent_id

    async def _get_or_create_client_order_id(self) -> str:
        """Get existing intent ID or create new one for idempotent submission."""
        form_key = f"order_entry:{self._tab_session_id}"

        try:
            state = await self._state_manager.restore_state()
            pending_entry = state.get("pending_forms", {}).get(form_key, {})
            pending_form = pending_entry.get("data", {})
        except Exception as exc:
            logger.warning(f"Failed to restore state for client_order_id: {exc}")
            # Fail-safe: generate new ID if state restoration fails
            return self._generate_intent_id()

        stored_intent_raw = pending_form.get("client_order_id")
        if stored_intent_raw and isinstance(stored_intent_raw, str):
            stored_intent: str = stored_intent_raw
            if (
                pending_form.get("symbol") == self._state.symbol
                and pending_form.get("side") == self._state.side
                and pending_form.get("quantity") == self._state.quantity
                and pending_form.get("order_type") == self._state.order_type
                and pending_form.get("limit_price") == str(self._state.limit_price or "")
                and pending_form.get("stop_price") == str(self._state.stop_price or "")
                and pending_form.get("time_in_force") == self._state.time_in_force
            ):
                return stored_intent

        new_intent = self._generate_intent_id()

        try:
            await self._state_manager.save_pending_form(
                form_id=form_key,
                form_data={
                    "client_order_id": new_intent,
                    "symbol": self._state.symbol,
                    "side": self._state.side,
                    "quantity": self._state.quantity,
                    "order_type": self._state.order_type,
                    "limit_price": str(self._state.limit_price or ""),
                    "stop_price": str(self._state.stop_price or ""),
                    "time_in_force": self._state.time_in_force,
                },
            )
        except Exception as exc:
            logger.warning(f"Failed to persist pending form state: {exc}")
            # Continue anyway - the intent ID is still valid for this session

        return new_intent

    # ================= Form Recovery =================

    async def _restore_pending_form(self) -> None:
        """Restore form state after reconnection."""
        form_key = f"order_entry:{self._tab_session_id}"
        try:
            state = await self._state_manager.restore_state()
            pending = state.get("pending_forms", {}).get(form_key)
        except Exception as exc:
            logger.warning(f"Failed to restore pending form state: {exc}")
            return  # Cannot restore, start with empty form

        if pending:
            form_data = pending.get("data", {})

            def safe_parse_decimal(key: str) -> Decimal | None:
                raw = form_data.get(key)
                if raw is None or raw == "":
                    return None
                try:
                    dec = Decimal(str(raw))
                    if not dec.is_finite():
                        return None
                    return dec
                except (InvalidOperation, ValueError, TypeError):
                    return None

            def safe_parse_int(key: str) -> int | None:
                raw = form_data.get(key)
                if raw is None:
                    return None
                try:
                    val = int(raw)
                    return val if val > 0 else None
                except (ValueError, TypeError):
                    return None

            def safe_parse_enum(key: str, allowed: set[str], default: str) -> str:
                raw = form_data.get(key, default)
                return raw if raw in allowed else default

            def safe_parse_symbol(key: str) -> str | None:
                raw = form_data.get(key)
                if not raw or not isinstance(raw, str):
                    return None
                try:
                    normalized = validate_and_normalize_symbol(raw)
                    return str(normalized)
                except ValueError:
                    return None

            try:
                self._state = OrderTicketState(
                    symbol=safe_parse_symbol("symbol"),
                    side=safe_parse_enum("side", {"buy", "sell"}, "buy"),  # type: ignore
                    quantity=safe_parse_int("quantity"),
                    order_type=safe_parse_enum(  # type: ignore
                        "order_type", {"market", "limit", "stop", "stop_limit"}, "market"
                    ),
                    limit_price=safe_parse_decimal("limit_price"),
                    stop_price=safe_parse_decimal("stop_price"),
                    time_in_force=safe_parse_enum(  # type: ignore
                        "time_in_force", {"day", "gtc", "ioc", "fok"}, "day"
                    ),
                )
                self._pending_client_order_id = form_data.get("client_order_id")
                self._sync_inputs_from_state()
                self._update_ui_from_state()
                ui.notify("Order form restored from previous session", type="info")
            except Exception as exc:
                logger.warning(f"Failed to restore pending form: {exc}")
                await self._state_manager.clear_pending_form(form_key)
                self._state = OrderTicketState()
                self._pending_client_order_id = None

    async def _clear_form(self) -> None:
        """Clear the order form."""
        self._state = OrderTicketState()
        self._pending_client_order_id = None

        if self._symbol_input:
            self._symbol_input.set_value("")
        if self._side_toggle:
            self._side_toggle.set_value("buy")
        if self._quantity_input:
            self._quantity_input.set_value(None)
        if self._order_type_select:
            self._order_type_select.set_value("market")
        if self._limit_price_input:
            self._limit_price_input.set_value(None)
            self._limit_price_input.classes(add="hidden")
        if self._stop_price_input:
            self._stop_price_input.set_value(None)
            self._stop_price_input.classes(add="hidden")
        if self._time_in_force_select:
            self._time_in_force_select.set_value("day")

        self._update_ui_from_state()

    # ================= Data Refresh =================

    def _start_data_refresh_timers(self, tracker: Callable[[ui.timer], None]) -> None:
        """Start periodic data refresh timers."""

        def _spawn_position_refresh() -> None:
            if self._disposed:
                return
            if self._position_refresh_task and not self._position_refresh_task.done():
                return
            self._position_refresh_task = asyncio.create_task(self._refresh_position_data())

        def _spawn_buying_power_refresh() -> None:
            if self._disposed:
                return
            if self._buying_power_refresh_task and not self._buying_power_refresh_task.done():
                return
            self._buying_power_refresh_task = asyncio.create_task(self._refresh_buying_power())

        self._position_timer = ui.timer(5.0, _spawn_position_refresh)
        tracker(self._position_timer)

        self._buying_power_timer = ui.timer(10.0, _spawn_buying_power_refresh)
        tracker(self._buying_power_timer)

    async def _refresh_position_data(self) -> None:
        """Periodic position refresh."""
        if self._disposed or not self._state.symbol:
            return

        try:
            positions_resp = await self._client.fetch_positions(
                user_id=self._user_id,
                role=self._role,
                strategies=self._strategies,
            )
            positions = positions_resp.get("positions", [])

            server_timestamp = self._parse_position_timestamp(positions_resp, positions)

            for pos in positions:
                if pos.get("symbol") == self._state.symbol:
                    self._current_position = int(pos.get("qty", 0))
                    break
            else:
                self._current_position = 0

            self._position_last_updated = server_timestamp
            self._update_ui_from_state()

        except Exception as exc:
            logger.debug(f"Position refresh failed: {exc}")
            self._position_last_updated = None

    async def _refresh_buying_power(self) -> None:
        """Periodic buying power refresh."""
        if self._disposed:
            return

        try:
            account_resp = await self._client.fetch_account_info(
                user_id=self._user_id,
                role=self._role,
            )

            server_timestamp = self._parse_account_timestamp(account_resp)

            raw_buying_power = account_resp.get("buying_power")
            if raw_buying_power is None:
                self._buying_power = None
                self._buying_power_last_updated = None
                return

            try:
                parsed_bp = Decimal(str(raw_buying_power))
                if not parsed_bp.is_finite():
                    raise ValueError(f"Non-finite buying power: {parsed_bp}")
                self._buying_power = parsed_bp
            except (InvalidOperation, ValueError, TypeError) as exc:
                logger.warning(f"Invalid buying power: {raw_buying_power!r} - {exc}")
                self._buying_power = None
                self._buying_power_last_updated = None
                return

            self._buying_power_last_updated = server_timestamp
            self._update_ui_from_state()

        except Exception as exc:
            logger.debug(f"Buying power refresh failed: {exc}")
            self._buying_power = None
            self._buying_power_last_updated = None

    def _parse_position_timestamp(
        self, positions_resp: dict[str, Any], positions: list[dict[str, Any]]
    ) -> datetime | None:
        """Parse server timestamp from position response."""
        timestamp_str = positions_resp.get("timestamp")
        if timestamp_str:
            try:
                parsed = parse_iso_timestamp(timestamp_str)
                if isinstance(parsed, datetime):
                    return parsed
            except (ValueError, TypeError):
                pass

        # Fallback: use newest position updated_at
        newest_updated_at = None
        for pos in positions:
            updated_str = pos.get("updated_at")
            if updated_str:
                try:
                    pos_ts = parse_iso_timestamp(str(updated_str))
                    if newest_updated_at is None or pos_ts > newest_updated_at:
                        newest_updated_at = pos_ts
                except (ValueError, TypeError):
                    continue
        return newest_updated_at

    def _parse_account_timestamp(self, account_resp: dict[str, Any]) -> datetime | None:
        """Parse server timestamp from account response."""
        timestamp_str = account_resp.get("timestamp")
        if timestamp_str:
            try:
                parsed = parse_iso_timestamp(timestamp_str)
                if isinstance(parsed, datetime):
                    return parsed
            except (ValueError, TypeError):
                pass

        # Fallback: try alternate fields
        for alt_field in ("last_equity_change", "updated_at", "as_of"):
            alt_timestamp = account_resp.get(alt_field)
            if alt_timestamp:
                try:
                    parsed_alt = parse_iso_timestamp(str(alt_timestamp))
                    if isinstance(parsed_alt, datetime):
                        return parsed_alt
                except (ValueError, TypeError):
                    continue
        return None

    # ================= Cleanup =================

    async def dispose(self) -> None:
        """Clean up component resources."""
        self._disposed = True

        if self._position_timer:
            self._position_timer.cancel()
        if self._buying_power_timer:
            self._buying_power_timer.cancel()
        if self._price_pulse_timer:
            self._price_pulse_timer.cancel()
        if self._dom_settle_timer:
            self._dom_settle_timer.cancel()

        for task in [self._position_refresh_task, self._buying_power_refresh_task]:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass


__all__ = ["OrderTicketComponent", "OrderTicketState"]
