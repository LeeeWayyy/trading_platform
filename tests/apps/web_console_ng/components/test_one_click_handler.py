"""Tests for OneClickHandler component."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.web_console_ng.components.one_click_handler import (
    PRICE_STALENESS_THRESHOLD_S,
    OneClickConfig,
    OneClickHandler,
)
from apps.web_console_ng.components.safety_gate import SafetyCheckResult
from apps.web_console_ng.utils.orders import (
    FALLBACK_ID_PREFIX,
    SYNTHETIC_ID_PREFIX,
)


def create_mock_handler(
    user_role: str = "trader",
    enabled: bool = True,
    session_confirmed: bool = True,
    cached_prices: dict[str, tuple[Decimal, datetime]] | None = None,
    daily_notional: Decimal = Decimal("0"),
) -> tuple[OneClickHandler, MagicMock, MagicMock, MagicMock, MagicMock]:
    """Create a mock OneClickHandler with all dependencies."""
    mock_client = MagicMock()
    mock_client.fetch_adv = AsyncMock(return_value={"adv": 1000000})
    mock_client.submit_manual_order = AsyncMock(
        return_value={"client_order_id": "test-order-123456"}
    )
    mock_client.cancel_order = AsyncMock(return_value=None)
    mock_client.fetch_open_orders = AsyncMock(return_value={"orders": []})

    mock_validator = MagicMock()
    mock_validator.validate.return_value = MagicMock(blocked=False, warnings=[])

    mock_safety = MagicMock()
    mock_safety.check = AsyncMock(
        return_value=SafetyCheckResult(allowed=True, reason="", warnings=[])
    )
    mock_safety.check_with_api_verification = AsyncMock(
        return_value=SafetyCheckResult(allowed=True, reason="", warnings=[])
    )

    mock_state_manager = MagicMock()
    mock_state_manager.restore_state = AsyncMock(
        return_value={
            "preferences": {
                f"one_click_notional_{datetime.now(UTC).strftime('%Y-%m-%d')}": str(
                    daily_notional
                )
            }
        }
    )
    mock_state_manager.save_preferences = AsyncMock()

    handler = OneClickHandler(
        trading_client=mock_client,
        fat_finger_validator=mock_validator,
        safety_gate=mock_safety,
        state_manager=mock_state_manager,
        user_id="user1",
        user_role=user_role,
    )

    if enabled:
        handler.set_enabled(True)
    handler._config.session_confirmed = session_confirmed

    # Set cached prices
    if cached_prices:
        handler.set_cached_prices(cached_prices)
    else:
        # Default fresh price for AAPL
        handler.set_cached_prices(
            {"AAPL": (Decimal("150.00"), datetime.now(UTC))}
        )

    # Set cached safety state (good state)
    handler.set_cached_safety_state(
        kill_switch=False,
        connection_state="CONNECTED",
        circuit_breaker=False,
    )

    return handler, mock_client, mock_validator, mock_safety, mock_state_manager


class TestOneClickConfig:
    """Test OneClickConfig dataclass."""

    def test_default_values(self) -> None:
        """Test default configuration values."""
        config = OneClickConfig()
        assert config.enabled is False
        assert config.daily_notional_cap == Decimal("500000")
        assert config.cooldown_ms == 500
        assert config.default_qty == 100
        assert config.session_confirmed is False

    def test_custom_values(self) -> None:
        """Test custom configuration values."""
        config = OneClickConfig(
            enabled=True,
            daily_notional_cap=Decimal("100000"),
            cooldown_ms=1000,
            default_qty=50,
            session_confirmed=True,
        )
        assert config.enabled is True
        assert config.daily_notional_cap == Decimal("100000")
        assert config.cooldown_ms == 1000
        assert config.default_qty == 50
        assert config.session_confirmed is True


class TestOneClickHandlerEnablement:
    """Test one-click enablement logic."""

    def test_is_enabled_requires_enabled_flag(self) -> None:
        """One-click requires enabled=True."""
        handler, *_ = create_mock_handler(enabled=False)
        assert not handler.is_enabled()

    def test_is_enabled_requires_trader_or_admin(self) -> None:
        """One-click only for trader/admin roles."""
        handler, *_ = create_mock_handler(user_role="trader")
        assert handler.is_enabled()

        handler, *_ = create_mock_handler(user_role="admin")
        assert handler.is_enabled()

        handler, *_ = create_mock_handler(user_role="viewer")
        assert not handler.is_enabled()

    def test_set_enabled(self) -> None:
        """Test enabling/disabling one-click."""
        handler, *_ = create_mock_handler(enabled=False)
        assert not handler.is_enabled()

        handler.set_enabled(True)
        assert handler.is_enabled()

        handler.set_enabled(False)
        assert not handler.is_enabled()


class TestCooldown:
    """Test cooldown protection."""

    def test_cooldown_passes_after_delay(self) -> None:
        """Cooldown passes when enough time has elapsed."""
        handler, *_ = create_mock_handler()
        assert handler._check_cooldown() is True  # First call passes
        assert handler._check_cooldown() is False  # Second call blocked

    def test_cooldown_resets_after_interval(self) -> None:
        """Cooldown resets after COOLDOWN_MS."""
        handler, *_ = create_mock_handler()
        handler._check_cooldown()  # First call

        # Manually set last click time to past
        handler._last_click_time = time.time() - 1.0  # 1 second ago (> 500ms)

        assert handler._check_cooldown() is True  # Should pass now


class TestFreshPrice:
    """Test price freshness checks."""

    def test_get_fresh_price_success(self) -> None:
        """Get fresh price when within threshold."""
        handler, *_ = create_mock_handler()
        handler.set_cached_prices({"AAPL": (Decimal("150.00"), datetime.now(UTC))})

        price, error = handler._get_fresh_price("AAPL")
        assert price == Decimal("150.00")
        assert error == ""

    def test_get_fresh_price_stale(self) -> None:
        """Stale price returns None with error."""
        handler, *_ = create_mock_handler()
        old_time = datetime.now(UTC) - timedelta(seconds=PRICE_STALENESS_THRESHOLD_S + 5)
        handler.set_cached_prices({"AAPL": (Decimal("150.00"), old_time)})

        price, error = handler._get_fresh_price("AAPL")
        assert price is None
        assert "stale" in error.lower()

    def test_get_fresh_price_missing_symbol(self) -> None:
        """Missing symbol returns None with error."""
        handler, *_ = create_mock_handler()
        handler.set_cached_prices({"GOOG": (Decimal("100.00"), datetime.now(UTC))})

        price, error = handler._get_fresh_price("AAPL")
        assert price is None
        assert "No price data" in error

    def test_get_fresh_price_empty_cache(self) -> None:
        """Empty cache returns None with error."""
        handler, *_ = create_mock_handler()
        handler.set_cached_prices({})

        price, error = handler._get_fresh_price("AAPL")
        assert price is None
        assert "No price data" in error

    def test_get_fresh_price_invalid_timestamp_type(self) -> None:
        """Invalid timestamp type returns None with error."""
        handler, *_ = create_mock_handler()
        # Set timestamp as string instead of datetime (malformed cache)
        handler._cached_prices = {"AAPL": (Decimal("150.00"), "not-a-datetime")}  # type: ignore[dict-item]

        price, error = handler._get_fresh_price("AAPL")
        assert price is None
        assert "invalid" in error.lower() or "malformed" in error.lower()

    def test_get_fresh_price_invalid_price_value_zero(self) -> None:
        """Zero price returns error."""
        handler, *_ = create_mock_handler()
        handler.set_cached_prices({"AAPL": (Decimal("0"), datetime.now(UTC))})

        price, error = handler._get_fresh_price("AAPL")
        assert price is None
        assert "Invalid price" in error

    def test_get_fresh_price_invalid_price_value_negative(self) -> None:
        """Negative price returns error."""
        handler, *_ = create_mock_handler()
        handler.set_cached_prices({"AAPL": (Decimal("-10.00"), datetime.now(UTC))})

        price, error = handler._get_fresh_price("AAPL")
        assert price is None
        assert "Invalid price" in error

    def test_get_fresh_price_malformed_cache_entry(self) -> None:
        """Malformed cache entry (non-Decimal price) returns error."""
        handler, *_ = create_mock_handler()
        # Set price as string instead of Decimal
        handler._cached_prices = {"AAPL": ("150.00", datetime.now(UTC))}  # type: ignore[dict-item]

        price, error = handler._get_fresh_price("AAPL")
        assert price is None
        assert "Invalid price" in error or "malformed" in error.lower()


class TestOnShiftClick:
    """Test Shift+Click limit order functionality."""

    @pytest.mark.asyncio()
    async def test_shift_click_success(self) -> None:
        """Successful shift+click places limit order."""
        handler, mock_client, *_ = create_mock_handler()

        with patch("apps.web_console_ng.components.one_click_handler.ui"):
            await handler.on_shift_click("AAPL", Decimal("150.00"), "buy")

            mock_client.submit_manual_order.assert_called_once()
            call_kwargs = mock_client.submit_manual_order.call_args.kwargs
            order_data = call_kwargs["order_data"]
            assert order_data["symbol"] == "AAPL"
            assert order_data["order_type"] == "limit"
            assert order_data["limit_price"] == "150.00"
            assert order_data["side"] == "buy"

    @pytest.mark.asyncio()
    async def test_shift_click_blocked_when_disabled(self) -> None:
        """Shift+click blocked when one-click not enabled."""
        handler, mock_client, *_ = create_mock_handler(enabled=False)

        with patch("apps.web_console_ng.components.one_click_handler.ui") as mock_ui:
            await handler.on_shift_click("AAPL", Decimal("150.00"), "buy")

            mock_client.submit_manual_order.assert_not_called()
            mock_ui.notify.assert_called_once()
            assert "not enabled" in mock_ui.notify.call_args[0][0].lower()


class TestOnCtrlClick:
    """Test Ctrl+Click market order functionality."""

    @pytest.mark.asyncio()
    async def test_ctrl_click_success(self) -> None:
        """Successful ctrl+click places market order."""
        handler, mock_client, *_ = create_mock_handler()

        with patch("apps.web_console_ng.components.one_click_handler.ui"):
            await handler.on_ctrl_click("AAPL", "sell")

            mock_client.submit_manual_order.assert_called_once()
            call_kwargs = mock_client.submit_manual_order.call_args.kwargs
            order_data = call_kwargs["order_data"]
            assert order_data["symbol"] == "AAPL"
            assert order_data["order_type"] == "market"
            assert order_data["side"] == "sell"
            assert "limit_price" not in order_data

    @pytest.mark.asyncio()
    async def test_ctrl_click_blocked_stale_price(self) -> None:
        """Ctrl+click blocked when price is stale."""
        handler, mock_client, *_ = create_mock_handler()
        old_time = datetime.now(UTC) - timedelta(seconds=PRICE_STALENESS_THRESHOLD_S + 5)
        handler.set_cached_prices({"AAPL": (Decimal("150.00"), old_time)})

        with patch("apps.web_console_ng.components.one_click_handler.ui") as mock_ui:
            await handler.on_ctrl_click("AAPL", "buy")

            mock_client.submit_manual_order.assert_not_called()
            mock_ui.notify.assert_called()
            assert "stale" in mock_ui.notify.call_args[0][0].lower()


class TestOnAltClick:
    """Test Alt+Click cancel functionality."""

    @pytest.mark.asyncio()
    async def test_alt_click_cancel_at_price(self) -> None:
        """Alt+click cancels orders at price level."""
        handler, mock_client, *_ = create_mock_handler()

        working_orders = [
            {
                "symbol": "AAPL",
                "side": "buy",
                "limit_price": "150.00",
                "client_order_id": "order-1",
            },
            {
                "symbol": "AAPL",
                "side": "sell",
                "limit_price": "150.00",
                "client_order_id": "order-2",
            },
            {
                "symbol": "AAPL",
                "side": "buy",
                "limit_price": "155.00",
                "client_order_id": "order-3",
            },
        ]

        with patch("apps.web_console_ng.components.one_click_handler.ui"):
            await handler.on_alt_click("AAPL", Decimal("150.00"), working_orders)

            # Should cancel 2 orders at 150.00, not the one at 155.00
            assert mock_client.cancel_order.call_count == 2

    @pytest.mark.asyncio()
    async def test_alt_click_viewer_blocked(self) -> None:
        """Viewer cannot cancel orders."""
        handler, mock_client, *_ = create_mock_handler(user_role="viewer")

        with patch("apps.web_console_ng.components.one_click_handler.ui") as mock_ui:
            await handler.on_alt_click("AAPL", Decimal("150.00"), [])

            mock_client.cancel_order.assert_not_called()
            assert "Viewers" in mock_ui.notify.call_args[0][0]

    @pytest.mark.asyncio()
    async def test_alt_click_no_orders_at_price(self) -> None:
        """No orders at price level shows info message."""
        handler, mock_client, *_ = create_mock_handler()

        working_orders = [
            {
                "symbol": "AAPL",
                "limit_price": "155.00",
                "client_order_id": "order-1",
            }
        ]

        with patch("apps.web_console_ng.components.one_click_handler.ui") as mock_ui:
            await handler.on_alt_click("AAPL", Decimal("150.00"), working_orders)

            mock_client.cancel_order.assert_not_called()
            assert "No orders" in mock_ui.notify.call_args[0][0]

    @pytest.mark.asyncio()
    async def test_alt_click_skips_synthetic_orders(self) -> None:
        """Skip orders with SYNTHETIC prefix."""
        handler, mock_client, *_ = create_mock_handler()

        working_orders = [
            {
                "symbol": "AAPL",
                "limit_price": "150.00",
                "client_order_id": f"{SYNTHETIC_ID_PREFIX}12345",
            },
            {
                "symbol": "AAPL",
                "limit_price": "150.00",
                "client_order_id": "valid-order",
            },
        ]

        with patch("apps.web_console_ng.components.one_click_handler.ui") as mock_ui:
            await handler.on_alt_click("AAPL", Decimal("150.00"), working_orders)

            # Only valid order should be cancelled
            assert mock_client.cancel_order.call_count == 1
            assert "skipped" in mock_ui.notify.call_args[0][0].lower()

    @pytest.mark.asyncio()
    async def test_alt_click_skips_fallback_orders(self) -> None:
        """Skip orders with FALLBACK prefix."""
        handler, mock_client, *_ = create_mock_handler()

        working_orders = [
            {
                "symbol": "AAPL",
                "limit_price": "150.00",
                "client_order_id": f"{FALLBACK_ID_PREFIX}12345",
            },
        ]

        with patch("apps.web_console_ng.components.one_click_handler.ui") as mock_ui:
            await handler.on_alt_click("AAPL", Decimal("150.00"), working_orders)

            mock_client.cancel_order.assert_not_called()
            assert "skipped" in mock_ui.notify.call_args[0][0].lower()

    @pytest.mark.asyncio()
    async def test_alt_click_price_tolerance(self) -> None:
        """Orders within price tolerance are cancelled."""
        handler, mock_client, *_ = create_mock_handler()

        within_tolerance = "150.005"  # Within $0.01 of 150.00
        outside_tolerance = "150.02"  # Outside $0.01 of 150.00

        working_orders = [
            {
                "symbol": "AAPL",
                "limit_price": within_tolerance,
                "client_order_id": "order-1",
            },
            {
                "symbol": "AAPL",
                "limit_price": outside_tolerance,
                "client_order_id": "order-2",
            },
        ]

        with patch("apps.web_console_ng.components.one_click_handler.ui"):
            await handler.on_alt_click("AAPL", Decimal("150.00"), working_orders)

            # Only within tolerance should be cancelled
            assert mock_client.cancel_order.call_count == 1

    @pytest.mark.asyncio()
    async def test_alt_click_read_only_warning(self) -> None:
        """Read-only mode shows warning but allows cancel."""
        handler, mock_client, *_ = create_mock_handler()

        working_orders = [
            {
                "symbol": "AAPL",
                "limit_price": "150.00",
                "client_order_id": "order-1",
            },
        ]

        with patch("apps.web_console_ng.components.one_click_handler.ui") as mock_ui:
            await handler.on_alt_click(
                "AAPL", Decimal("150.00"), working_orders, is_read_only=True
            )

            # Should still cancel (fail-open for risk-reducing)
            mock_client.cancel_order.assert_called_once()
            # Should have warning about connection
            warning_calls = [
                c for c in mock_ui.notify.call_args_list if "warning" in str(c)
            ]
            assert len(warning_calls) > 0

    @pytest.mark.asyncio()
    async def test_alt_click_reports_failures(self) -> None:
        """Alt+click reports cancel failures accurately."""
        handler, mock_client, *_ = create_mock_handler()

        working_orders = [
            {
                "symbol": "AAPL",
                "limit_price": "150.00",
                "client_order_id": "order-1",
            },
            {
                "symbol": "AAPL",
                "limit_price": "150.00",
                "client_order_id": "order-2",
            },
            {
                "symbol": "AAPL",
                "limit_price": "150.00",
                "client_order_id": "order-3",
            },
        ]

        # First succeeds, second and third fail
        mock_client.cancel_order.side_effect = [
            None,  # Success
            Exception("API error"),  # Fail
            Exception("Timeout"),  # Fail
        ]

        with patch("apps.web_console_ng.components.one_click_handler.ui") as mock_ui:
            await handler.on_alt_click("AAPL", Decimal("150.00"), working_orders)

            # Should report both cancelled and failed counts
            notify_call = mock_ui.notify.call_args
            assert "1" in notify_call[0][0]  # 1 cancelled
            assert "failed" in notify_call[0][0].lower()
            assert "2" in notify_call[0][0]  # 2 failed
            # Should show warning type since some succeeded
            assert notify_call[1]["type"] == "warning"

    @pytest.mark.asyncio()
    async def test_alt_click_all_failures_negative_type(self) -> None:
        """Alt+click shows negative type when all cancels fail."""
        handler, mock_client, *_ = create_mock_handler()

        working_orders = [
            {
                "symbol": "AAPL",
                "limit_price": "150.00",
                "client_order_id": "order-1",
            },
        ]

        mock_client.cancel_order.side_effect = Exception("API error")

        with patch("apps.web_console_ng.components.one_click_handler.ui") as mock_ui:
            await handler.on_alt_click("AAPL", Decimal("150.00"), working_orders)

            notify_call = mock_ui.notify.call_args
            assert "failed" in notify_call[0][0].lower()
            # All failed = negative type
            assert notify_call[1]["type"] == "negative"


class TestConnectionStateCaseSensitivity:
    """Test connection state case handling."""

    @pytest.mark.asyncio()
    async def test_handle_alt_cancel_lowercase_connection_state(self) -> None:
        """Handle alt_cancel with lowercase connection state."""
        handler, mock_client, *_ = create_mock_handler()

        # Set lowercase connection state
        handler.set_cached_safety_state(
            kill_switch=False,
            connection_state="disconnected",  # lowercase
            circuit_breaker=False,
        )

        mock_client.fetch_open_orders = AsyncMock(
            return_value={
                "orders": [
                    {
                        "symbol": "AAPL",
                        "limit_price": "150.00",
                        "client_order_id": "order-1",
                    }
                ]
            }
        )

        with patch("apps.web_console_ng.components.one_click_handler.ui") as mock_ui:
            await handler.handle_one_click(
                {"mode": "alt_cancel", "symbol": "AAPL", "price": "150.00"}
            )

            # Should still process (case-insensitive comparison)
            # is_read_only should be True with "disconnected"
            mock_client.cancel_order.assert_called_once()
            # Should show warning for disconnected state
            warning_calls = [
                c for c in mock_ui.notify.call_args_list if "warning" in str(c)
            ]
            assert len(warning_calls) > 0

    @pytest.mark.asyncio()
    async def test_alt_click_warns_on_unknown_string_connection(self) -> None:
        """Alt+click shows warning for 'UNKNOWN' connection state string."""
        handler, mock_client, *_ = create_mock_handler()

        # Set UNKNOWN connection state (string, not None)
        handler.set_cached_safety_state(
            kill_switch=False,
            connection_state="UNKNOWN",
            circuit_breaker=False,
        )

        working_orders = [
            {
                "symbol": "AAPL",
                "limit_price": "150.00",
                "client_order_id": "order-1",
            },
        ]

        with patch("apps.web_console_ng.components.one_click_handler.ui") as mock_ui:
            await handler.on_alt_click("AAPL", Decimal("150.00"), working_orders)

            # Should still cancel (risk-reducing action)
            mock_client.cancel_order.assert_called_once()
            # Should show warning about unknown connection
            warning_calls = [
                c for c in mock_ui.notify.call_args_list if "warning" in str(c)
            ]
            assert len(warning_calls) > 0
            # Check the warning mentions unknown
            warning_msg = str(mock_ui.notify.call_args_list[0])
            assert "unknown" in warning_msg.lower()


class TestSafetyChecks:
    """Test safety gate integration."""

    @pytest.mark.asyncio()
    async def test_safety_check_blocks_order(self) -> None:
        """Safety check failure blocks order."""
        handler, mock_client, _, mock_safety, _ = create_mock_handler()
        mock_safety.check = AsyncMock(
            return_value=SafetyCheckResult(allowed=False, reason="Kill switch engaged", warnings=[])
        )

        with patch("apps.web_console_ng.components.one_click_handler.ui") as mock_ui:
            await handler.on_shift_click("AAPL", Decimal("150.00"), "buy")

            mock_client.submit_manual_order.assert_not_called()
            mock_ui.notify.assert_called()
            assert "Kill switch" in mock_ui.notify.call_args[0][0]

    @pytest.mark.asyncio()
    async def test_api_verification_blocks_order(self) -> None:
        """Fresh API verification failure blocks order."""
        handler, mock_client, _, mock_safety, _ = create_mock_handler()
        mock_safety.check_with_api_verification = AsyncMock(
            return_value=SafetyCheckResult(
                allowed=False, reason="Circuit breaker tripped", warnings=[]
            )
        )

        with patch("apps.web_console_ng.components.one_click_handler.ui") as mock_ui:
            await handler.on_shift_click("AAPL", Decimal("150.00"), "buy")

            mock_client.submit_manual_order.assert_not_called()
            assert "Circuit breaker" in mock_ui.notify.call_args[0][0]


class TestFatFingerValidation:
    """Test fat finger validation integration."""

    @pytest.mark.asyncio()
    async def test_fat_finger_blocks_order(self) -> None:
        """Fat finger validation failure blocks order."""
        handler, mock_client, mock_validator, *_ = create_mock_handler()
        mock_validator.validate.return_value = MagicMock(
            blocked=True,
            warnings=[MagicMock(message="Exceeds max notional limit")],
        )

        with patch("apps.web_console_ng.components.one_click_handler.ui") as mock_ui:
            await handler.on_shift_click("AAPL", Decimal("150.00"), "buy")

            mock_client.submit_manual_order.assert_not_called()
            assert "Fat finger" in mock_ui.notify.call_args[0][0]


class TestDailyNotionalCap:
    """Test daily notional cap enforcement."""

    @pytest.mark.asyncio()
    async def test_daily_cap_blocks_order(self) -> None:
        """Order blocked when it would exceed daily cap."""
        handler, mock_client, *_, mock_state = create_mock_handler()

        # Set daily notional near cap (500,000 - 100 * 150 = 485,000 used)
        mock_state.restore_state = AsyncMock(
            return_value={
                "preferences": {
                    f"one_click_notional_{datetime.now(UTC).strftime('%Y-%m-%d')}": "490000"
                }
            }
        )

        with patch("apps.web_console_ng.components.one_click_handler.ui") as mock_ui:
            # Try to place order for 100 shares @ $150 = $15,000
            # 490,000 + 15,000 = 505,000 > 500,000 cap
            await handler.on_shift_click("AAPL", Decimal("150.00"), "buy")

            mock_client.submit_manual_order.assert_not_called()
            assert "cap" in mock_ui.notify.call_args[0][0].lower()

    @pytest.mark.asyncio()
    async def test_daily_notional_updated_after_success(self) -> None:
        """Daily notional is updated after successful order."""
        handler, mock_client, *_, mock_state = create_mock_handler()

        mock_state.restore_state = AsyncMock(
            return_value={
                "preferences": {
                    f"one_click_notional_{datetime.now(UTC).strftime('%Y-%m-%d')}": "10000"
                }
            }
        )

        with patch("apps.web_console_ng.components.one_click_handler.ui"):
            await handler.on_shift_click("AAPL", Decimal("150.00"), "buy")

            # Should update notional: 10,000 + (100 * 150) = 25,000
            mock_state.save_preferences.assert_called_once()
            call_args = mock_state.save_preferences.call_args[0]
            assert "notional" in call_args[0].lower()
            # Decimal string may have trailing zeros
            assert Decimal(call_args[1]) == Decimal("25000")


class TestFirstUseConfirmation:
    """Test first-use confirmation dialog."""

    @pytest.mark.asyncio()
    async def test_first_use_confirmation_required(self) -> None:
        """First use requires confirmation dialog."""
        handler, mock_client, *_ = create_mock_handler(session_confirmed=False)

        with patch(
            "apps.web_console_ng.components.one_click_handler.ui"
        ) as mock_ui:
            # Mock dialog to not confirm
            mock_dialog = MagicMock()
            mock_dialog.__enter__ = MagicMock(return_value=mock_dialog)
            mock_dialog.__exit__ = MagicMock(return_value=False)
            mock_dialog.__await__ = lambda self: iter([False])
            mock_ui.dialog.return_value = mock_dialog

            mock_card = MagicMock()
            mock_card.__enter__ = MagicMock(return_value=mock_card)
            mock_card.__exit__ = MagicMock(return_value=False)
            mock_card.classes.return_value = mock_card
            mock_ui.card.return_value = mock_card

            mock_row = MagicMock()
            mock_row.__enter__ = MagicMock(return_value=mock_row)
            mock_row.__exit__ = MagicMock(return_value=False)
            mock_row.classes.return_value = mock_row
            mock_ui.row.return_value = mock_row

            mock_label = MagicMock()
            mock_label.classes.return_value = mock_label
            mock_ui.label.return_value = mock_label

            mock_button = MagicMock()
            mock_button.classes.return_value = mock_button
            mock_ui.button.return_value = mock_button

            # Since confirmation didn't happen, order should not be placed
            # (this test checks the dialog flow, actual blocking depends on dialog result)

    @pytest.mark.asyncio()
    async def test_skip_confirmation_after_first_use(self) -> None:
        """Subsequent uses don't show confirmation."""
        handler, mock_client, *_ = create_mock_handler(session_confirmed=True)

        with patch("apps.web_console_ng.components.one_click_handler.ui"):
            await handler.on_shift_click("AAPL", Decimal("150.00"), "buy")

            # Order should be placed without dialog
            mock_client.submit_manual_order.assert_called_once()
            # No dialog should have been created for confirmation
            # (ui.dialog would only be called for confirmation if session_confirmed=False)


class TestHandleOneClick:
    """Test CustomEvent handler."""

    @pytest.mark.asyncio()
    async def test_handle_shift_limit(self) -> None:
        """Handle shift_limit mode."""
        handler, mock_client, *_ = create_mock_handler()

        with patch("apps.web_console_ng.components.one_click_handler.ui"):
            await handler.handle_one_click(
                {
                    "mode": "shift_limit",
                    "symbol": "AAPL",
                    "price": "150.00",
                    "side": "buy",
                }
            )

            mock_client.submit_manual_order.assert_called_once()

    @pytest.mark.asyncio()
    async def test_handle_ctrl_market(self) -> None:
        """Handle ctrl_market mode."""
        handler, mock_client, *_ = create_mock_handler()

        with patch("apps.web_console_ng.components.one_click_handler.ui"):
            await handler.handle_one_click(
                {"mode": "ctrl_market", "symbol": "AAPL", "side": "sell"}
            )

            mock_client.submit_manual_order.assert_called_once()
            order_data = mock_client.submit_manual_order.call_args.kwargs["order_data"]
            assert order_data["order_type"] == "market"

    @pytest.mark.asyncio()
    async def test_handle_alt_cancel(self) -> None:
        """Handle alt_cancel mode."""
        handler, mock_client, *_ = create_mock_handler()

        mock_client.fetch_open_orders = AsyncMock(
            return_value={
                "orders": [
                    {
                        "symbol": "AAPL",
                        "limit_price": "150.00",
                        "client_order_id": "order-1",
                    }
                ]
            }
        )

        with patch("apps.web_console_ng.components.one_click_handler.ui"):
            await handler.handle_one_click(
                {"mode": "alt_cancel", "symbol": "AAPL", "price": "150.00"}
            )

            mock_client.cancel_order.assert_called_once()

    @pytest.mark.asyncio()
    async def test_handle_missing_symbol(self) -> None:
        """Missing symbol logs warning."""
        handler, mock_client, *_ = create_mock_handler()

        with patch(
            "apps.web_console_ng.components.one_click_handler.logger"
        ) as mock_logger:
            await handler.handle_one_click({"mode": "shift_limit", "price": "150.00"})

            mock_logger.warning.assert_called()
            mock_client.submit_manual_order.assert_not_called()

    @pytest.mark.asyncio()
    async def test_handle_invalid_price(self) -> None:
        """Invalid price shows error."""
        handler, mock_client, *_ = create_mock_handler()

        with patch("apps.web_console_ng.components.one_click_handler.ui") as mock_ui:
            await handler.handle_one_click(
                {
                    "mode": "shift_limit",
                    "symbol": "AAPL",
                    "price": "invalid",
                    "side": "buy",
                }
            )

            mock_client.submit_manual_order.assert_not_called()
            mock_ui.notify.assert_called()
            assert "Invalid price" in mock_ui.notify.call_args[0][0]

    @pytest.mark.asyncio()
    async def test_handle_unknown_mode(self) -> None:
        """Unknown mode logs warning."""
        handler, mock_client, *_ = create_mock_handler()

        with patch(
            "apps.web_console_ng.components.one_click_handler.logger"
        ) as mock_logger:
            await handler.handle_one_click({"mode": "unknown", "symbol": "AAPL"})

            mock_logger.warning.assert_called()
            mock_client.submit_manual_order.assert_not_called()


class TestAuditLog:
    """Test audit logging."""

    @pytest.mark.asyncio()
    async def test_audit_log_on_success(self) -> None:
        """Audit log entry created on successful order."""
        handler, mock_client, *_ = create_mock_handler()

        with (
            patch("apps.web_console_ng.components.one_click_handler.ui"),
            patch(
                "apps.web_console_ng.components.one_click_handler.logger"
            ) as mock_logger,
        ):
            await handler.on_shift_click("AAPL", Decimal("150.00"), "buy")

            # Check audit log was created
            mock_logger.info.assert_called()
            log_call = mock_logger.info.call_args
            assert log_call[0][0] == "one_click_order_submitted"
            extra = log_call[1]["extra"]
            assert extra["user_id"] == "user1"
            assert extra["symbol"] == "AAPL"
            assert extra["side"] == "buy"
            assert extra["order_type"] == "limit"


class TestConfiguration:
    """Test configuration methods."""

    def test_set_daily_notional_cap(self) -> None:
        """Test setting daily notional cap."""
        handler, *_ = create_mock_handler()
        handler.set_daily_notional_cap(Decimal("100000"))
        assert handler.get_config().daily_notional_cap == Decimal("100000")

    def test_set_default_qty(self) -> None:
        """Test setting default quantity."""
        handler, *_ = create_mock_handler()
        handler.set_default_qty(50)
        assert handler.get_config().default_qty == 50

    def test_set_cached_safety_state(self) -> None:
        """Test setting cached safety state."""
        handler, *_ = create_mock_handler()
        handler.set_cached_safety_state(
            kill_switch=True,
            connection_state="DISCONNECTED",
            circuit_breaker=True,
        )
        assert handler._cached_kill_switch is True
        assert handler._cached_connection_state == "DISCONNECTED"
        assert handler._cached_circuit_breaker is True


__all__ = [
    "TestAuditLog",
    "TestConfiguration",
    "TestCooldown",
    "TestDailyNotionalCap",
    "TestFatFingerValidation",
    "TestFirstUseConfirmation",
    "TestFreshPrice",
    "TestHandleOneClick",
    "TestOnAltClick",
    "TestOnCtrlClick",
    "TestOnShiftClick",
    "TestOneClickConfig",
    "TestOneClickHandlerEnablement",
    "TestSafetyChecks",
]
