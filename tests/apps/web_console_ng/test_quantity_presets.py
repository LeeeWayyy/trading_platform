"""Tests for QuantityPresetsComponent."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch


class TestQuantityPresetsComponent:
    """Tests for QuantityPresetsComponent."""

    def test_init_default_presets(self) -> None:
        """Default presets are [100, 500, 1000]."""
        from apps.web_console_ng.components.quantity_presets import QuantityPresetsComponent

        callback = MagicMock()
        comp = QuantityPresetsComponent(on_preset_selected=callback)

        assert comp._presets == [100, 500, 1000]

    def test_init_custom_presets(self) -> None:
        """Custom presets are used when provided."""
        from apps.web_console_ng.components.quantity_presets import QuantityPresetsComponent

        callback = MagicMock()
        comp = QuantityPresetsComponent(on_preset_selected=callback, presets=[50, 200])

        assert comp._presets == [50, 200]

    def test_update_context_sets_values(self) -> None:
        """update_context sets all context values."""
        from apps.web_console_ng.components.quantity_presets import QuantityPresetsComponent

        callback = MagicMock()
        comp = QuantityPresetsComponent(on_preset_selected=callback)

        comp.update_context(
            buying_power=Decimal("10000"),
            current_price=Decimal("100"),
            current_position=50,
            max_position_per_symbol=200,
            max_notional_per_order=Decimal("5000"),
            side="buy",
            effective_price=Decimal("105"),
            qty_step=100,
            min_qty=100,
        )

        assert comp._buying_power == Decimal("10000")
        assert comp._current_price == Decimal("100")
        assert comp._current_position == 50
        assert comp._max_position_per_symbol == 200
        assert comp._max_notional_per_order == Decimal("5000")
        assert comp._side == "buy"
        assert comp._effective_price == Decimal("105")
        assert comp._qty_step == 100
        assert comp._min_qty == 100


class TestQuantityPresetsMaxCalculation:
    """Tests for MAX quantity calculation logic."""

    def test_max_by_buying_power(self) -> None:
        """MAX respects buying power limit."""
        from apps.web_console_ng.components.quantity_presets import QuantityPresetsComponent

        callback = MagicMock()
        comp = QuantityPresetsComponent(on_preset_selected=callback)
        comp.update_context(
            buying_power=Decimal("10000"),
            current_price=Decimal("100"),
            current_position=0,
        )

        # 10000 / 100 = 100 shares, * 0.95 safety = 95
        with patch.object(comp, "_on_preset_selected") as mock_callback:
            comp._calculate_and_select_max()
            mock_callback.assert_called_once_with(95)

    def test_max_by_position_limit_buy(self) -> None:
        """MAX respects position limit for buy orders."""
        from apps.web_console_ng.components.quantity_presets import QuantityPresetsComponent

        callback = MagicMock()
        comp = QuantityPresetsComponent(on_preset_selected=callback)
        comp.update_context(
            buying_power=Decimal("100000"),  # High BP
            current_price=Decimal("100"),
            current_position=80,
            max_position_per_symbol=100,  # Only 20 more shares allowed
            side="buy",
        )

        # min(1000 by BP, 20 by limit) = 20, * 0.95 = 19
        with patch.object(comp, "_on_preset_selected") as mock_callback:
            comp._calculate_and_select_max()
            mock_callback.assert_called_once_with(19)

    def test_max_by_position_limit_sell(self) -> None:
        """MAX respects position limit for sell orders (short)."""
        from apps.web_console_ng.components.quantity_presets import QuantityPresetsComponent

        callback = MagicMock()
        comp = QuantityPresetsComponent(on_preset_selected=callback)
        comp.update_context(
            buying_power=Decimal("100000"),  # High BP
            current_price=Decimal("100"),
            current_position=50,  # Long 50
            max_position_per_symbol=100,  # Can go short 100
            side="sell",
        )

        # Sell: max = limit + current = 100 + 50 = 150
        # min(1000 by BP, 150 by limit) = 150, * 0.95 = 142
        with patch.object(comp, "_on_preset_selected") as mock_callback:
            comp._calculate_and_select_max()
            mock_callback.assert_called_once_with(142)

    def test_max_by_notional_limit(self) -> None:
        """MAX respects per-order notional limit."""
        from apps.web_console_ng.components.quantity_presets import QuantityPresetsComponent

        callback = MagicMock()
        comp = QuantityPresetsComponent(on_preset_selected=callback)
        comp.update_context(
            buying_power=Decimal("100000"),  # High BP
            current_price=Decimal("100"),
            current_position=0,
            max_notional_per_order=Decimal("5000"),  # Max 50 shares
        )

        # min(1000 by BP, 50 by notional) = 50, * 0.95 = 47
        with patch.object(comp, "_on_preset_selected") as mock_callback:
            comp._calculate_and_select_max()
            mock_callback.assert_called_once_with(47)

    def test_max_uses_effective_price_for_limit_orders(self) -> None:
        """MAX uses effective_price (limit price) instead of current price."""
        from apps.web_console_ng.components.quantity_presets import QuantityPresetsComponent

        callback = MagicMock()
        comp = QuantityPresetsComponent(on_preset_selected=callback)
        comp.update_context(
            buying_power=Decimal("10000"),
            current_price=Decimal("100"),  # Market price
            current_position=0,
            effective_price=Decimal("200"),  # Limit price (higher)
        )

        # Uses effective_price: 10000 / 200 = 50, * 0.95 = 47
        with patch.object(comp, "_on_preset_selected") as mock_callback:
            comp._calculate_and_select_max()
            mock_callback.assert_called_once_with(47)

    def test_max_zero_buying_power(self) -> None:
        """MAX notifies when buying power is zero."""
        from apps.web_console_ng.components.quantity_presets import QuantityPresetsComponent

        callback = MagicMock()
        comp = QuantityPresetsComponent(on_preset_selected=callback)
        comp.update_context(
            buying_power=Decimal("0"),
            current_price=Decimal("100"),
            current_position=0,
        )

        with patch("apps.web_console_ng.components.quantity_presets.ui.notify") as mock_notify:
            comp._calculate_and_select_max()
            mock_notify.assert_called_once_with("Insufficient buying power (0)", type="warning")
            callback.assert_not_called()

    def test_max_no_buying_power(self) -> None:
        """MAX notifies when buying power is unavailable."""
        from apps.web_console_ng.components.quantity_presets import QuantityPresetsComponent

        callback = MagicMock()
        comp = QuantityPresetsComponent(on_preset_selected=callback)
        comp.update_context(
            buying_power=None,
            current_price=Decimal("100"),
            current_position=0,
        )

        with patch("apps.web_console_ng.components.quantity_presets.ui.notify") as mock_notify:
            comp._calculate_and_select_max()
            mock_notify.assert_called_once_with(
                "Cannot calculate MAX: buying power unavailable", type="warning"
            )

    def test_max_no_price(self) -> None:
        """MAX notifies when price is unavailable."""
        from apps.web_console_ng.components.quantity_presets import QuantityPresetsComponent

        callback = MagicMock()
        comp = QuantityPresetsComponent(on_preset_selected=callback)
        comp.update_context(
            buying_power=Decimal("10000"),
            current_price=None,
            current_position=0,
        )

        with patch("apps.web_console_ng.components.quantity_presets.ui.notify") as mock_notify:
            comp._calculate_and_select_max()
            mock_notify.assert_called_once_with(
                "Cannot calculate MAX: price unavailable", type="warning"
            )

    def test_max_position_limit_reached(self) -> None:
        """MAX notifies when position limit is reached."""
        from apps.web_console_ng.components.quantity_presets import QuantityPresetsComponent

        callback = MagicMock()
        comp = QuantityPresetsComponent(on_preset_selected=callback)
        comp.update_context(
            buying_power=Decimal("100000"),
            current_price=Decimal("100"),
            current_position=100,
            max_position_per_symbol=100,  # Already at limit
            side="buy",
        )

        with patch("apps.web_console_ng.components.quantity_presets.ui.notify") as mock_notify:
            comp._calculate_and_select_max()
            mock_notify.assert_called_once_with("Position limit reached", type="warning")

    def test_max_respects_qty_step_and_min_qty(self) -> None:
        """MAX is quantized to configured quantity step/minimum."""
        from apps.web_console_ng.components.quantity_presets import QuantityPresetsComponent

        callback = MagicMock()
        comp = QuantityPresetsComponent(on_preset_selected=callback)
        comp.update_context(
            buying_power=Decimal("20000"),  # 200 shares at $100
            current_price=Decimal("100"),
            current_position=0,
            qty_step=100,
            min_qty=100,
        )

        # max=200, safety max=190, quantized to step=100
        with patch.object(comp, "_on_preset_selected") as mock_callback:
            comp._calculate_and_select_max()
            mock_callback.assert_called_once_with(100)

    def test_max_does_not_bypass_safety_margin_after_quantization(self) -> None:
        """MAX does not fallback to raw max when safety margin cannot fit one lot."""
        from apps.web_console_ng.components.quantity_presets import QuantityPresetsComponent

        callback = MagicMock()
        comp = QuantityPresetsComponent(on_preset_selected=callback)
        comp.update_context(
            buying_power=Decimal("10000"),  # max_qty = 100 at $100
            current_price=Decimal("100"),
            current_position=0,
            qty_step=100,
            min_qty=100,
        )

        # safe_max_qty=95, quantized=0, so no MAX selection should occur.
        with (
            patch.object(comp, "_on_preset_selected") as mock_callback,
            patch("apps.web_console_ng.components.quantity_presets.ui.notify") as mock_notify,
        ):
            comp._calculate_and_select_max()
            mock_callback.assert_not_called()
            mock_notify.assert_called_once_with("Insufficient buying power", type="warning")


class TestQuantityPresetsSetEnabled:
    """Tests for QuantityPresetsComponent.set_enabled()."""

    def test_set_enabled_false_disables_all_buttons(self) -> None:
        """set_enabled(False) disables all preset buttons and MAX button."""
        from apps.web_console_ng.components.quantity_presets import QuantityPresetsComponent

        callback = MagicMock()
        comp = QuantityPresetsComponent(on_preset_selected=callback)

        # Mock the button list and max button
        mock_btn1 = MagicMock()
        mock_btn2 = MagicMock()
        mock_max = MagicMock()
        comp._preset_buttons = [mock_btn1, mock_btn2]
        comp._max_button = mock_max

        comp.set_enabled(False)

        mock_btn1.set_enabled.assert_called_once_with(False)
        mock_btn2.set_enabled.assert_called_once_with(False)
        mock_max.set_enabled.assert_called_once_with(False)

    def test_set_enabled_true_enables_all_buttons(self) -> None:
        """set_enabled(True) enables all preset buttons and MAX button."""
        from apps.web_console_ng.components.quantity_presets import QuantityPresetsComponent

        callback = MagicMock()
        comp = QuantityPresetsComponent(on_preset_selected=callback)

        # Mock the button list and max button
        mock_btn1 = MagicMock()
        mock_btn2 = MagicMock()
        mock_max = MagicMock()
        comp._preset_buttons = [mock_btn1, mock_btn2]
        comp._max_button = mock_max

        comp.set_enabled(True)

        mock_btn1.set_enabled.assert_called_once_with(True)
        mock_btn2.set_enabled.assert_called_once_with(True)
        mock_max.set_enabled.assert_called_once_with(True)


class TestQuantityPresetsProfiles:
    """Tests for dynamic preset profile updates."""

    def test_set_presets_updates_profile(self) -> None:
        """set_presets replaces quick-size values."""
        from apps.web_console_ng.components.quantity_presets import QuantityPresetsComponent

        callback = MagicMock()
        comp = QuantityPresetsComponent(on_preset_selected=callback)

        comp.set_presets([1, 5, 10])

        assert comp._presets == [1, 5, 10]

    def test_set_presets_normalizes_and_falls_back(self) -> None:
        """Invalid/empty values are normalized with default fallback."""
        from apps.web_console_ng.components.quantity_presets import QuantityPresetsComponent

        callback = MagicMock()
        comp = QuantityPresetsComponent(on_preset_selected=callback)

        comp.set_presets([0, -3, 1, 1])  # dedupe + drop non-positive
        assert comp._presets == [1]

        comp.set_presets([])
        assert comp._presets == [100, 500, 1000]
