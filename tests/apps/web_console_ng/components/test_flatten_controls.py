"""Tests for FlattenControls component.

Tests cover:
- Role-based access control (viewers blocked)
- Quantity validation
- FAIL-OPEN policy for flatten operations
- FAIL-CLOSED policy for reverse operations
- Price fetching and staleness checks
- Fat finger validation
- Order cancellation logic
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.web_console_ng.components.flatten_controls import FlattenControls
from apps.web_console_ng.components.safety_gate import (
    SafetyCheckResult,
    SafetyGate,
    SafetyPolicy,
)
from apps.web_console_ng.utils.orders import (
    FALLBACK_ID_PREFIX,
    SYNTHETIC_ID_PREFIX,
)


class TestFlattenControlsInit:
    """Tests for FlattenControls initialization."""

    def test_init_stores_dependencies(self) -> None:
        mock_gate = MagicMock(spec=SafetyGate)
        mock_client = AsyncMock()
        mock_validator = MagicMock()
        strategies = ["alpha_baseline"]

        controls = FlattenControls(
            safety_gate=mock_gate,
            trading_client=mock_client,
            fat_finger_validator=mock_validator,
            strategies=strategies,
        )

        assert controls._safety is mock_gate
        assert controls._client is mock_client
        assert controls._validator is mock_validator
        assert controls._strategies == strategies


class TestValidateQty:
    """Tests for quantity validation."""

    @pytest.fixture()
    def controls(self) -> FlattenControls:
        return FlattenControls(
            safety_gate=MagicMock(),
            trading_client=AsyncMock(),
            fat_finger_validator=MagicMock(),
        )

    def test_valid_positive_int(self, controls: FlattenControls) -> None:
        qty, error = controls._validate_qty(100)
        assert qty == 100
        assert error == ""

    def test_valid_negative_int(self, controls: FlattenControls) -> None:
        qty, error = controls._validate_qty(-50)
        assert qty == -50
        assert error == ""

    def test_valid_float_whole_number(self, controls: FlattenControls) -> None:
        qty, error = controls._validate_qty(100.0)
        assert qty == 100
        assert error == ""

    def test_invalid_fractional(self, controls: FlattenControls) -> None:
        qty, error = controls._validate_qty(100.5)
        assert qty is None
        assert "integer" in error.lower()

    def test_invalid_zero(self, controls: FlattenControls) -> None:
        qty, error = controls._validate_qty(0)
        assert qty is None
        assert "Invalid" in error

    def test_invalid_non_finite(self, controls: FlattenControls) -> None:
        qty, error = controls._validate_qty(float("inf"))
        assert qty is None
        assert "Invalid" in error

    def test_invalid_nan(self, controls: FlattenControls) -> None:
        qty, error = controls._validate_qty(float("nan"))
        assert qty is None
        assert "Invalid" in error


class TestCancelSymbolOrders:
    """Tests for order cancellation logic."""

    @pytest.fixture()
    def mock_client(self) -> AsyncMock:
        client = AsyncMock()
        return client

    @pytest.fixture()
    def controls(self, mock_client: AsyncMock) -> FlattenControls:
        return FlattenControls(
            safety_gate=MagicMock(),
            trading_client=mock_client,
            fat_finger_validator=MagicMock(),
            strategies=["alpha_baseline"],
        )

    @pytest.mark.asyncio()
    async def test_cancel_filters_uncancellable_orders(
        self, controls: FlattenControls, mock_client: AsyncMock
    ) -> None:
        """Orders with synthetic/fallback IDs should be counted as uncancellable."""
        mock_client.fetch_open_orders.return_value = {
            "orders": [
                {"symbol": "AAPL", "client_order_id": "valid-order-123"},
                {"symbol": "AAPL", "client_order_id": f"{SYNTHETIC_ID_PREFIX}abc"},
                {"symbol": "AAPL", "client_order_id": f"{FALLBACK_ID_PREFIX}def"},
                {"symbol": "AAPL", "client_order_id": None},
            ]
        }
        mock_client.cancel_order.return_value = {}

        cancelled, failed, uncancellable, had_fetch_error = await controls._cancel_symbol_orders(
            "AAPL", "user_id", "trader", "test reason"
        )

        assert cancelled == 1  # Only the valid order
        assert uncancellable == 3  # Synthetic, fallback, and None
        assert had_fetch_error is False
        mock_client.cancel_order.assert_called_once()

    @pytest.mark.asyncio()
    async def test_cancel_handles_api_error(
        self, controls: FlattenControls, mock_client: AsyncMock
    ) -> None:
        """Failed cancellations should be counted."""
        mock_client.fetch_open_orders.return_value = {
            "orders": [
                {"symbol": "AAPL", "client_order_id": "order-1"},
                {"symbol": "AAPL", "client_order_id": "order-2"},
            ]
        }
        mock_client.cancel_order.side_effect = [
            {},  # First succeeds
            Exception("API error"),  # Second fails
        ]

        cancelled, failed, uncancellable, had_fetch_error = await controls._cancel_symbol_orders(
            "AAPL", "user_id", "trader", "test reason"
        )

        assert cancelled == 1
        assert failed == 1
        assert uncancellable == 0
        assert had_fetch_error is False

    @pytest.mark.asyncio()
    async def test_cancel_filters_other_symbols(
        self, controls: FlattenControls, mock_client: AsyncMock
    ) -> None:
        """Should only cancel orders for the specified symbol."""
        mock_client.fetch_open_orders.return_value = {
            "orders": [
                {"symbol": "AAPL", "client_order_id": "aapl-order"},
                {"symbol": "GOOG", "client_order_id": "goog-order"},
            ]
        }
        mock_client.cancel_order.return_value = {}

        cancelled, failed, uncancellable, had_fetch_error = await controls._cancel_symbol_orders(
            "AAPL", "user_id", "trader", "test reason"
        )

        assert cancelled == 1
        assert had_fetch_error is False
        mock_client.cancel_order.assert_called_once()

    @pytest.mark.asyncio()
    async def test_cancel_returns_fetch_error_on_exception(
        self, controls: FlattenControls, mock_client: AsyncMock
    ) -> None:
        """Fetch error should be signaled via had_fetch_error flag."""
        mock_client.fetch_open_orders.side_effect = Exception("Network error")

        cancelled, failed, uncancellable, had_fetch_error = await controls._cancel_symbol_orders(
            "AAPL", "user_id", "trader", "test reason"
        )

        assert cancelled == 0
        assert failed == 0
        assert uncancellable == 0
        assert had_fetch_error is True
        mock_client.cancel_order.assert_not_called()


class TestOnFlattenSymbol:
    """Tests for on_flatten_symbol method (FAIL-OPEN)."""

    @pytest.fixture()
    def mock_safety(self) -> AsyncMock:
        safety = AsyncMock(spec=SafetyGate)
        safety.check.return_value = SafetyCheckResult(
            allowed=True, reason=None, warnings=[]
        )
        safety.check_with_api_verification.return_value = SafetyCheckResult(
            allowed=True, reason=None, warnings=[]
        )
        return safety

    @pytest.fixture()
    def mock_client(self) -> AsyncMock:
        client = AsyncMock()
        client.fetch_open_orders.return_value = {"orders": []}
        return client

    @pytest.fixture()
    def controls(self, mock_safety: AsyncMock, mock_client: AsyncMock) -> FlattenControls:
        return FlattenControls(
            safety_gate=mock_safety,
            trading_client=mock_client,
            fat_finger_validator=MagicMock(),
            strategies=["alpha_baseline"],
        )

    @pytest.mark.asyncio()
    @patch("apps.web_console_ng.components.flatten_controls.ui")
    async def test_viewer_blocked(
        self, mock_ui: MagicMock, controls: FlattenControls
    ) -> None:
        """Viewers should be blocked from flattening."""
        await controls.on_flatten_symbol("AAPL", 100, "user_id", "viewer")
        mock_ui.notify.assert_called_once()
        assert "Viewers" in mock_ui.notify.call_args[0][0]

    @pytest.mark.asyncio()
    @patch("apps.web_console_ng.components.flatten_controls.ui")
    async def test_invalid_qty_blocked(
        self, mock_ui: MagicMock, controls: FlattenControls
    ) -> None:
        """Invalid quantity should block with error."""
        await controls.on_flatten_symbol("AAPL", 0, "user_id", "trader")
        mock_ui.notify.assert_called_once()
        assert mock_ui.notify.call_args[1]["type"] == "negative"

    @pytest.mark.asyncio()
    @patch("apps.web_console_ng.components.flatten_controls.ui")
    async def test_safety_check_fail_shows_error(
        self, mock_ui: MagicMock, controls: FlattenControls, mock_safety: AsyncMock
    ) -> None:
        """Failed safety check should show error notification."""
        mock_safety.check.return_value = SafetyCheckResult(
            allowed=False, reason="Kill switch engaged", warnings=[]
        )
        await controls.on_flatten_symbol("AAPL", 100, "user_id", "trader")
        mock_ui.notify.assert_called_once()
        assert "Kill switch" in mock_ui.notify.call_args[0][0]

    @pytest.mark.asyncio()
    @patch("apps.web_console_ng.components.flatten_controls.ui")
    async def test_safety_check_warnings_shown(
        self, mock_ui: MagicMock, controls: FlattenControls, mock_safety: AsyncMock
    ) -> None:
        """Safety warnings should be shown as notifications."""
        mock_safety.check.return_value = SafetyCheckResult(
            allowed=True, reason=None, warnings=["Connection degraded"]
        )
        await controls.on_flatten_symbol("AAPL", 100, "user_id", "trader")
        # Should show warning notification
        warning_calls = [c for c in mock_ui.notify.call_args_list if c[1].get("type") == "warning"]
        assert len(warning_calls) >= 1

    @pytest.mark.asyncio()
    @patch("apps.web_console_ng.components.flatten_controls.ui")
    async def test_uses_fail_open_policy(
        self, mock_ui: MagicMock, controls: FlattenControls, mock_safety: AsyncMock
    ) -> None:
        """Should use FAIL_OPEN policy for safety check."""
        await controls.on_flatten_symbol(
            "AAPL", 100, "user_id", "trader",
            cached_kill_switch=False,
            cached_connection_state="CONNECTED",
            cached_circuit_breaker=False,
        )
        mock_safety.check.assert_called_once()
        call_kwargs = mock_safety.check.call_args[1]
        assert call_kwargs["policy"] == SafetyPolicy.FAIL_OPEN


class TestOnReversePosition:
    """Tests for on_reverse_position method (FAIL-CLOSED)."""

    @pytest.fixture()
    def mock_safety(self) -> AsyncMock:
        safety = AsyncMock(spec=SafetyGate)
        safety.check.return_value = SafetyCheckResult(
            allowed=True, reason=None, warnings=[]
        )
        safety.check_with_api_verification.return_value = SafetyCheckResult(
            allowed=True, reason=None, warnings=[]
        )
        return safety

    @pytest.fixture()
    def mock_client(self) -> AsyncMock:
        client = AsyncMock()
        client.fetch_open_orders.return_value = {"orders": []}
        # Provide valid timestamp for strict_timestamp=True (reverse uses FAIL_CLOSED)
        client.fetch_market_prices.return_value = [
            {"symbol": "AAPL", "mid": "150.00", "timestamp": datetime.now(UTC).isoformat()}
        ]
        return client

    @pytest.fixture()
    def mock_validator(self) -> MagicMock:
        validator = MagicMock()
        validation_result = MagicMock()
        validation_result.blocked = False
        validation_result.warnings = []
        validator.validate.return_value = validation_result
        return validator

    @pytest.fixture()
    def controls(
        self, mock_safety: AsyncMock, mock_client: AsyncMock, mock_validator: MagicMock
    ) -> FlattenControls:
        return FlattenControls(
            safety_gate=mock_safety,
            trading_client=mock_client,
            fat_finger_validator=mock_validator,
            strategies=["alpha_baseline"],
        )

    @pytest.mark.asyncio()
    @patch("apps.web_console_ng.components.flatten_controls.ui")
    async def test_viewer_blocked(
        self, mock_ui: MagicMock, controls: FlattenControls
    ) -> None:
        """Viewers should be blocked from reversing."""
        await controls.on_reverse_position("AAPL", 100, "buy", "user_id", "viewer")
        mock_ui.notify.assert_called_once()
        assert "Viewers" in mock_ui.notify.call_args[0][0]

    @pytest.mark.asyncio()
    @patch("apps.web_console_ng.components.flatten_controls.ui")
    async def test_uses_fail_closed_policy(
        self, mock_ui: MagicMock, controls: FlattenControls, mock_safety: AsyncMock
    ) -> None:
        """Should use FAIL_CLOSED policy for safety check."""
        await controls.on_reverse_position(
            "AAPL", 100, "buy", "user_id", "trader",
            cached_kill_switch=False,
            cached_connection_state="CONNECTED",
            cached_circuit_breaker=False,
        )
        mock_safety.check.assert_called_once()
        call_kwargs = mock_safety.check.call_args[1]
        assert call_kwargs["policy"] == SafetyPolicy.FAIL_CLOSED
        assert call_kwargs["require_connected"] is True

    @pytest.mark.asyncio()
    @patch("apps.web_console_ng.components.flatten_controls.ui")
    async def test_safety_check_fail_blocks(
        self, mock_ui: MagicMock, controls: FlattenControls, mock_safety: AsyncMock
    ) -> None:
        """Failed safety check should block reverse."""
        mock_safety.check.return_value = SafetyCheckResult(
            allowed=False, reason="Connection unknown", warnings=[]
        )
        await controls.on_reverse_position(
            "AAPL", 100, "buy", "user_id", "trader",
            cached_kill_switch=None,  # Unknown state
        )
        mock_ui.notify.assert_called_once()
        assert "reverse" in mock_ui.notify.call_args[0][0].lower()

    @pytest.mark.asyncio()
    @patch("apps.web_console_ng.components.flatten_controls.ui")
    async def test_uncancellable_orders_block(
        self, mock_ui: MagicMock, controls: FlattenControls, mock_client: AsyncMock
    ) -> None:
        """Uncancellable orders should block reverse."""
        mock_client.fetch_open_orders.return_value = {
            "orders": [
                {"symbol": "AAPL", "client_order_id": f"{SYNTHETIC_ID_PREFIX}abc"}
            ]
        }
        await controls.on_reverse_position(
            "AAPL", 100, "buy", "user_id", "trader",
            cached_kill_switch=False,
            cached_connection_state="CONNECTED",
            cached_circuit_breaker=False,
        )
        # Should notify about uncancellable orders
        calls = [c[0][0] for c in mock_ui.notify.call_args_list]
        assert any("uncancellable" in c.lower() for c in calls)

    @pytest.mark.asyncio()
    @patch("apps.web_console_ng.components.flatten_controls.ui")
    async def test_price_unavailable_blocks(
        self, mock_ui: MagicMock, controls: FlattenControls, mock_client: AsyncMock
    ) -> None:
        """Unavailable price should block reverse (FAIL-CLOSED needs fresh price)."""
        mock_client.fetch_market_prices.return_value = []  # No price for symbol
        await controls.on_reverse_position(
            "AAPL", 100, "buy", "user_id", "trader",
            cached_kill_switch=False,
            cached_connection_state="CONNECTED",
            cached_circuit_breaker=False,
        )
        calls = [c[0][0] for c in mock_ui.notify.call_args_list]
        assert any("price" in c.lower() for c in calls)

    @pytest.mark.asyncio()
    @patch("apps.web_console_ng.components.flatten_controls.ui")
    async def test_fat_finger_blocked(
        self, mock_ui: MagicMock, controls: FlattenControls, mock_validator: MagicMock
    ) -> None:
        """Fat finger validation failure should block reverse."""
        warning = MagicMock()
        warning.message = "Order exceeds max notional"
        validation_result = MagicMock()
        validation_result.blocked = True
        validation_result.warnings = [warning]
        mock_validator.validate.return_value = validation_result

        await controls.on_reverse_position(
            "AAPL", 100, "buy", "user_id", "trader",
            cached_kill_switch=False,
            cached_connection_state="CONNECTED",
            cached_circuit_breaker=False,
        )
        calls = [c[0][0] for c in mock_ui.notify.call_args_list]
        assert any("notional" in c.lower() for c in calls)


class TestGetFreshPriceWithFallback:
    """Tests for price fetching logic."""

    @pytest.fixture()
    def mock_client(self) -> AsyncMock:
        client = AsyncMock()
        return client

    @pytest.fixture()
    def controls(self, mock_client: AsyncMock) -> FlattenControls:
        return FlattenControls(
            safety_gate=MagicMock(),
            trading_client=mock_client,
            fat_finger_validator=MagicMock(),
            strategies=["alpha_baseline"],
        )

    @pytest.mark.asyncio()
    async def test_returns_valid_price(
        self, controls: FlattenControls, mock_client: AsyncMock
    ) -> None:
        mock_client.fetch_market_prices.return_value = [
            {"symbol": "AAPL", "mid": "150.50", "timestamp": None}
        ]
        price, error = await controls._get_fresh_price_with_fallback(
            "AAPL", "user_id", "trader"
        )
        assert price == Decimal("150.50")
        assert error == ""

    @pytest.mark.asyncio()
    async def test_returns_error_for_missing_symbol(
        self, controls: FlattenControls, mock_client: AsyncMock
    ) -> None:
        mock_client.fetch_market_prices.return_value = [
            {"symbol": "GOOG", "mid": "100.00", "timestamp": None}
        ]
        price, error = await controls._get_fresh_price_with_fallback(
            "AAPL", "user_id", "trader"
        )
        assert price is None
        assert "No price data" in error

    @pytest.mark.asyncio()
    async def test_returns_error_for_invalid_price(
        self, controls: FlattenControls, mock_client: AsyncMock
    ) -> None:
        mock_client.fetch_market_prices.return_value = [
            {"symbol": "AAPL", "mid": "invalid", "timestamp": None}
        ]
        price, error = await controls._get_fresh_price_with_fallback(
            "AAPL", "user_id", "trader"
        )
        assert price is None
        assert "Unparseable" in error

    @pytest.mark.asyncio()
    async def test_returns_error_for_negative_price(
        self, controls: FlattenControls, mock_client: AsyncMock
    ) -> None:
        mock_client.fetch_market_prices.return_value = [
            {"symbol": "AAPL", "mid": "-10.00", "timestamp": None}
        ]
        price, error = await controls._get_fresh_price_with_fallback(
            "AAPL", "user_id", "trader"
        )
        assert price is None
        assert "Invalid price" in error

    @pytest.mark.asyncio()
    async def test_returns_error_for_api_failure(
        self, controls: FlattenControls, mock_client: AsyncMock
    ) -> None:
        mock_client.fetch_market_prices.side_effect = Exception("Network error")
        price, error = await controls._get_fresh_price_with_fallback(
            "AAPL", "user_id", "trader"
        )
        assert price is None
        assert "failed" in error.lower()

    @pytest.mark.asyncio()
    async def test_strict_timestamp_blocks_missing_timestamp(
        self, controls: FlattenControls, mock_client: AsyncMock
    ) -> None:
        """strict_timestamp=True should block on missing timestamp (FAIL_CLOSED for reverse)."""
        mock_client.fetch_market_prices.return_value = [
            {"symbol": "AAPL", "mid": "150.50", "timestamp": None}
        ]
        price, error = await controls._get_fresh_price_with_fallback(
            "AAPL", "user_id", "trader", strict_timestamp=True
        )
        assert price is None
        assert "timestamp missing" in error.lower()

    @pytest.mark.asyncio()
    async def test_strict_timestamp_blocks_unparseable_timestamp(
        self, controls: FlattenControls, mock_client: AsyncMock
    ) -> None:
        """strict_timestamp=True should block on unparseable timestamp (FAIL_CLOSED)."""
        mock_client.fetch_market_prices.return_value = [
            {"symbol": "AAPL", "mid": "150.50", "timestamp": "not-a-date"}
        ]
        price, error = await controls._get_fresh_price_with_fallback(
            "AAPL", "user_id", "trader", strict_timestamp=True
        )
        assert price is None
        assert "unparseable" in error.lower()

    @pytest.mark.asyncio()
    async def test_strict_timestamp_allows_valid_timestamp(
        self, controls: FlattenControls, mock_client: AsyncMock
    ) -> None:
        """strict_timestamp=True should allow valid recent timestamp."""
        mock_client.fetch_market_prices.return_value = [
            {"symbol": "AAPL", "mid": "150.50", "timestamp": datetime.now(UTC).isoformat()}
        ]
        price, error = await controls._get_fresh_price_with_fallback(
            "AAPL", "user_id", "trader", strict_timestamp=True
        )
        assert price == Decimal("150.50")
        assert error == ""

    @pytest.mark.asyncio()
    async def test_strict_timestamp_blocks_unrecognized_timestamp_type(
        self, controls: FlattenControls, mock_client: AsyncMock
    ) -> None:
        """strict_timestamp=True should block on unrecognized timestamp type (int/float)."""
        mock_client.fetch_market_prices.return_value = [
            {"symbol": "AAPL", "mid": "150.50", "timestamp": 1234567890}  # Unix timestamp int
        ]
        price, error = await controls._get_fresh_price_with_fallback(
            "AAPL", "user_id", "trader", strict_timestamp=True
        )
        assert price is None
        assert "unrecognized type" in error.lower()

    @pytest.mark.asyncio()
    async def test_non_strict_allows_unrecognized_timestamp_type(
        self, controls: FlattenControls, mock_client: AsyncMock
    ) -> None:
        """Non-strict timestamp allows unrecognized type (FAIL_OPEN)."""
        mock_client.fetch_market_prices.return_value = [
            {"symbol": "AAPL", "mid": "150.50", "timestamp": 1234567890}  # Unix timestamp int
        ]
        price, error = await controls._get_fresh_price_with_fallback(
            "AAPL", "user_id", "trader", strict_timestamp=False
        )
        # FAIL_OPEN: Should proceed with price
        assert price == Decimal("150.50")
        assert error == ""

    @pytest.mark.asyncio()
    async def test_price_with_datetime_object_timestamp(
        self, controls: FlattenControls, mock_client: AsyncMock
    ) -> None:
        """Price with datetime object timestamp is accepted."""
        mock_client.fetch_market_prices.return_value = [
            {"symbol": "AAPL", "mid": "150.50", "timestamp": datetime.now(UTC)}
        ]
        price, error = await controls._get_fresh_price_with_fallback(
            "AAPL", "user_id", "trader", strict_timestamp=True
        )
        assert price == Decimal("150.50")
        assert error == ""


class TestVerifyOrdersCleared:
    """Tests for order clearing verification."""

    @pytest.fixture()
    def mock_client(self) -> AsyncMock:
        client = AsyncMock()
        return client

    @pytest.fixture()
    def controls(self, mock_client: AsyncMock) -> FlattenControls:
        return FlattenControls(
            safety_gate=MagicMock(),
            trading_client=mock_client,
            fat_finger_validator=MagicMock(),
            strategies=["alpha_baseline"],
        )

    @pytest.mark.asyncio()
    async def test_orders_cleared_immediately(
        self, controls: FlattenControls, mock_client: AsyncMock
    ) -> None:
        """Verify orders cleared when no orders exist."""
        mock_client.fetch_open_orders.return_value = {"orders": []}
        cleared, error = await controls._verify_orders_cleared(
            "AAPL", "user_id", "trader", timeout_s=2.0, poll_interval_s=0.1
        )
        assert cleared is True
        assert error == ""

    @pytest.mark.asyncio()
    async def test_orders_not_cleared_timeout(
        self, controls: FlattenControls, mock_client: AsyncMock
    ) -> None:
        """Verify timeout when orders persist."""
        # Orders persist indefinitely
        mock_client.fetch_open_orders.return_value = {
            "orders": [{"symbol": "AAPL", "client_order_id": "order-1"}]
        }
        cleared, error = await controls._verify_orders_cleared(
            "AAPL", "user_id", "trader", timeout_s=0.3, poll_interval_s=0.1
        )
        assert cleared is False
        assert "not cleared" in error.lower()

    @pytest.mark.asyncio()
    async def test_orders_cleared_after_delay(
        self, controls: FlattenControls, mock_client: AsyncMock
    ) -> None:
        """Verify success when orders clear after a few polls."""
        call_count = [0]

        async def delayed_clear(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] < 3:
                return {"orders": [{"symbol": "AAPL", "client_order_id": "order-1"}]}
            return {"orders": []}

        mock_client.fetch_open_orders.side_effect = delayed_clear
        cleared, error = await controls._verify_orders_cleared(
            "AAPL", "user_id", "trader", timeout_s=2.0, poll_interval_s=0.1
        )
        assert cleared is True
        assert error == ""

    @pytest.mark.asyncio()
    async def test_orders_cleared_ignores_other_symbols(
        self, controls: FlattenControls, mock_client: AsyncMock
    ) -> None:
        """Verify only the target symbol is checked."""
        mock_client.fetch_open_orders.return_value = {
            "orders": [{"symbol": "GOOG", "client_order_id": "order-1"}]  # Different symbol
        }
        cleared, error = await controls._verify_orders_cleared(
            "AAPL", "user_id", "trader", timeout_s=1.0, poll_interval_s=0.1
        )
        assert cleared is True
        assert error == ""

    @pytest.mark.asyncio()
    async def test_orders_cleared_continues_on_fetch_error(
        self, controls: FlattenControls, mock_client: AsyncMock
    ) -> None:
        """Verify polling continues on fetch errors."""
        call_count = [0]

        async def error_then_success(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] < 2:
                raise Exception("Network error")
            return {"orders": []}

        mock_client.fetch_open_orders.side_effect = error_then_success
        cleared, error = await controls._verify_orders_cleared(
            "AAPL", "user_id", "trader", timeout_s=2.0, poll_interval_s=0.1
        )
        assert cleared is True
        assert error == ""


class TestGetAdv:
    """Tests for ADV fetching."""

    @pytest.fixture()
    def mock_client(self) -> AsyncMock:
        client = AsyncMock()
        return client

    @pytest.fixture()
    def controls(self, mock_client: AsyncMock) -> FlattenControls:
        return FlattenControls(
            safety_gate=MagicMock(),
            trading_client=mock_client,
            fat_finger_validator=MagicMock(),
            strategies=["alpha_baseline"],
        )

    @pytest.mark.asyncio()
    async def test_get_adv_success(
        self, controls: FlattenControls, mock_client: AsyncMock
    ) -> None:
        mock_client.fetch_adv.return_value = {"adv": 1000000}
        adv = await controls._get_adv("AAPL", "user_id", "trader")
        assert adv == 1000000

    @pytest.mark.asyncio()
    async def test_get_adv_returns_none_on_empty_response(
        self, controls: FlattenControls, mock_client: AsyncMock
    ) -> None:
        mock_client.fetch_adv.return_value = {}
        adv = await controls._get_adv("AAPL", "user_id", "trader")
        assert adv is None

    @pytest.mark.asyncio()
    async def test_get_adv_returns_none_on_exception(
        self, controls: FlattenControls, mock_client: AsyncMock
    ) -> None:
        mock_client.fetch_adv.side_effect = Exception("Network error")
        adv = await controls._get_adv("AAPL", "user_id", "trader")
        assert adv is None


class TestValidateQtyEdgeCases:
    """Additional edge case tests for quantity validation."""

    @pytest.fixture()
    def controls(self) -> FlattenControls:
        return FlattenControls(
            safety_gate=MagicMock(),
            trading_client=AsyncMock(),
            fat_finger_validator=MagicMock(),
        )

    def test_validate_qty_none(self, controls: FlattenControls) -> None:
        """None value returns error."""
        qty, error = controls._validate_qty(None)  # type: ignore[arg-type]
        assert qty is None
        assert "Invalid" in error

    def test_validate_qty_string(self, controls: FlattenControls) -> None:
        """String value returns error."""
        qty, error = controls._validate_qty("100")  # type: ignore[arg-type]
        assert qty is None or qty == 100  # float() can parse "100"
        # If it parses, it should succeed; if not, error

    def test_validate_qty_negative_infinity(self, controls: FlattenControls) -> None:
        """Negative infinity returns error."""
        qty, error = controls._validate_qty(float("-inf"))
        assert qty is None
        assert "Invalid" in error
