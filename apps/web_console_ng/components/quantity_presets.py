"""One-click quantity preset buttons for order entry.

Provides configurable presets (100, 500, 1000) and MAX button with
buying power and position limit awareness.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING

from nicegui import ui

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


class QuantityPresetsComponent:
    """One-click quantity preset buttons with MAX calculation.

    CRITICAL: MAX respects both buying power AND position limits.
    Uses the most restrictive of all applicable limits for safety.
    """

    DEFAULT_PRESETS = [100, 500, 1000]
    # Safety margin for MAX calculation (95% to avoid edge cases)
    MAX_SAFETY_MARGIN = Decimal("0.95")

    def __init__(
        self,
        on_preset_selected: Callable[[int], None],
        presets: list[int] | None = None,
    ) -> None:
        """Initialize quantity presets.

        Args:
            on_preset_selected: Callback when a preset is clicked.
            presets: Custom preset values (default: [100, 500, 1000]).
        """
        self._on_preset_selected = on_preset_selected
        self._presets = presets or self.DEFAULT_PRESETS
        self._preset_buttons: list[ui.button] = []
        self._max_button: ui.button | None = None

        # Context for MAX calculation
        self._buying_power: Decimal | None = None
        self._current_price: Decimal | None = None
        self._current_position: int = 0
        self._max_position_per_symbol: int | None = None
        self._max_notional_per_order: Decimal | None = None
        self._side: str = "buy"
        self._effective_price: Decimal | None = None  # Limit/stop price

    def create(self) -> ui.row:
        """Create preset buttons row."""
        self._preset_buttons = []
        with ui.row().classes("gap-2 items-center") as row:
            for preset in self._presets:
                btn = ui.button(
                    str(preset),
                    on_click=lambda p=preset: self._on_preset_selected(p),
                ).classes("w-16 h-8 text-sm")
                self._preset_buttons.append(btn)

            # MAX button with dynamic calculation
            self._max_button = ui.button(
                "MAX",
                on_click=self._calculate_and_select_max,
            ).classes("w-16 h-8 text-sm bg-blue-600")

        return row

    def set_enabled(self, enabled: bool) -> None:
        """Enable or disable all preset buttons."""
        for btn in self._preset_buttons:
            btn.set_enabled(enabled)
        if self._max_button:
            self._max_button.set_enabled(enabled)

    def _calculate_and_select_max(self) -> None:
        """Calculate max affordable quantity based on buying power AND position limits.

        CRITICAL: MAX must respect both buying power and risk limits.
        Uses the most restrictive of all applicable limits.

        NOTE: For limit/stop orders, uses effective_price (the order price) instead
        of current_price for more accurate calculations.
        """
        # Use effective price (limit/stop) if available, otherwise market price
        calc_price = self._effective_price or self._current_price

        # Use `is None` checks to handle 0 correctly
        # 0 buying power is a valid state (no trading allowed), not "unavailable"
        if calc_price is None:
            ui.notify("Cannot calculate MAX: price unavailable", type="warning")
            return

        if self._buying_power is None:
            ui.notify("Cannot calculate MAX: buying power unavailable", type="warning")
            return

        if self._buying_power <= 0:
            ui.notify("Insufficient buying power (0)", type="warning")
            return

        if calc_price <= 0:
            return

        # Calculate max by buying power (using effective price)
        max_by_buying_power = int(self._buying_power // calc_price)

        # Calculate max by per-symbol position limit (if configured)
        max_by_position_limit: int | None = None
        if self._max_position_per_symbol is not None:
            if self._side == "buy":
                # Buying: max = limit - current_position
                max_by_position_limit = self._max_position_per_symbol - self._current_position
            else:
                # Selling: max = limit + current_position (can go short up to limit)
                max_by_position_limit = self._max_position_per_symbol + self._current_position
            max_by_position_limit = max(0, max_by_position_limit)  # Can't be negative

        # Calculate max by per-order notional limit (using effective price)
        max_by_notional: int | None = None
        if self._max_notional_per_order is not None:
            max_by_notional = int(self._max_notional_per_order // calc_price)

        # Use the most restrictive limit (minimum of all applicable limits)
        applicable_limits = [max_by_buying_power]
        if max_by_position_limit is not None:
            applicable_limits.append(max_by_position_limit)
        if max_by_notional is not None:
            applicable_limits.append(max_by_notional)

        max_qty = min(applicable_limits)

        # Apply safety margin (95% to avoid edge cases)
        safe_max = int(max_qty * self.MAX_SAFETY_MARGIN)

        if safe_max > 0:
            self._on_preset_selected(safe_max)
        else:
            # Provide specific feedback on which limit is the constraint
            if max_by_position_limit is not None and max_by_position_limit == 0:
                ui.notify("Position limit reached", type="warning")
            elif max_by_notional is not None and max_by_notional == 0:
                ui.notify("Order notional limit reached", type="warning")
            else:
                ui.notify("Insufficient buying power", type="warning")

    def update_context(
        self,
        buying_power: Decimal | None,
        current_price: Decimal | None,
        current_position: int = 0,
        max_position_per_symbol: int | None = None,
        max_notional_per_order: Decimal | None = None,
        side: str = "buy",
        effective_price: Decimal | None = None,
    ) -> None:
        """Update context for MAX calculation.

        Args:
            buying_power: Available buying power in dollars.
            current_price: Current market price of selected symbol.
            current_position: Current position in selected symbol (shares).
            max_position_per_symbol: Maximum allowed position per symbol (shares).
            max_notional_per_order: Maximum allowed notional per order ($).
            side: Order side ('buy' or 'sell') - affects position limit calc.
            effective_price: Limit/stop price for non-market orders. If provided,
                used for buying power and notional calculations instead of current_price.
        """
        self._buying_power = buying_power
        self._current_price = current_price
        self._current_position = current_position
        self._effective_price = effective_price
        self._max_position_per_symbol = max_position_per_symbol
        self._max_notional_per_order = max_notional_per_order
        self._side = side


__all__ = ["QuantityPresetsComponent"]
