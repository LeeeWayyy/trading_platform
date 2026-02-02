from __future__ import annotations

import types
from unittest.mock import AsyncMock

import httpx
import pytest

from apps.web_console_ng.components import positions_grid as grid_module


class DummyGrid:
    def __init__(self, options: dict) -> None:
        self.options = options
        self._classes: set[str] = set()
        self.calls: list[tuple[str, object]] = []
        self._event_handlers: dict[str, list] = {}

    def classes(self, add: str | None = None, remove: str | None = None):
        if remove:
            for cls in remove.split():
                self._classes.discard(cls)
        if add:
            for cls in add.split():
                self._classes.add(cls)
        return self

    def on(self, event: str, handler) -> None:
        """Register event handler (mock for NiceGUI aggrid.on())."""
        if event not in self._event_handlers:
            self._event_handlers[event] = []
        self._event_handlers[event].append(handler)

    def update(self) -> None:
        """Mock update method for NiceGUI aggrid."""
        pass

    def run_grid_method(self, method: str, payload: object, timeout: float = 5) -> None:
        """Mock run_grid_method - sync to capture fire-and-forget calls."""
        self.calls.append((method, payload))


@pytest.fixture()
def dummy_ui(monkeypatch: pytest.MonkeyPatch):
    notify_calls: list[tuple[str, dict]] = []

    def aggrid(options: dict) -> DummyGrid:
        grid = DummyGrid(options)
        return grid

    def notify(message: str, **kwargs):
        notify_calls.append((message, kwargs))

    # Mock asyncio.Event to be pre-set (grid is immediately ready in tests)
    class PreSetEvent:
        def is_set(self) -> bool:
            return True

        def set(self) -> None:
            pass

    monkeypatch.setattr(grid_module.asyncio, "Event", PreSetEvent)

    dummy = types.SimpleNamespace(aggrid=aggrid, notify=notify)
    monkeypatch.setattr(grid_module, "ui", dummy)
    return notify_calls


def test_create_positions_grid_columns(dummy_ui: None) -> None:
    grid = grid_module.create_positions_grid()
    assert isinstance(grid, DummyGrid)

    column_defs = grid.options["columnDefs"]
    fields = [col["field"] for col in column_defs]
    assert fields == [
        "symbol",
        "qty",
        "avg_entry_price",
        "current_price",
        "sparkline_svg",
        "unrealized_pl",
        "unrealized_plpc",
        "actions",
    ]

    symbol_col = column_defs[0]
    assert symbol_col["pinned"] == "left"

    actions_col = column_defs[-1]
    assert actions_col["pinned"] == "right"
    assert actions_col[":cellRenderer"] == "window.closePositionRenderer"

    assert grid.options[":getRowId"] == "params => params.data.symbol"
    assert grid.options["rowSelection"] == "multiple"
    assert grid.options["animateRows"] is True
    # P6T1: onGridReady now registers with GridThrottle for per-grid degradation tracking
    assert "window._positionsGridApi = params.api" in grid.options[":onGridReady"]
    assert "GridThrottle" in grid.options[":onGridReady"]


@pytest.mark.asyncio()
async def test_update_positions_grid_add_update_remove(dummy_ui: None) -> None:
    grid = grid_module.create_positions_grid()

    first_positions = [
        {"symbol": "AAPL", "qty": 10},
        {"symbol": "MSFT", "qty": 5},
    ]

    symbols = await grid_module.update_positions_grid(grid, first_positions)
    assert symbols == {"AAPL", "MSFT"}
    assert grid.calls[-1][0] == "setRowData"

    next_positions = [
        {"symbol": "AAPL", "qty": 12},
        {"symbol": "GOOG", "qty": 3},
    ]

    symbols = await grid_module.update_positions_grid(grid, next_positions, symbols)
    assert symbols == {"AAPL", "GOOG"}

    method, payload = grid.calls[-1]
    # P6T1: Changed to applyTransactionAsync for batched updates
    assert method == "applyTransactionAsync"
    assert payload == {
        "add": [{"symbol": "GOOG", "qty": 3}],
        "update": [{"symbol": "AAPL", "qty": 12}],
        "remove": [{"symbol": "MSFT"}],
    }


@pytest.mark.asyncio()
async def test_update_positions_grid_filters_malformed_entries(
    dummy_ui, caplog: pytest.LogCaptureFixture
) -> None:
    grid = grid_module.create_positions_grid()

    positions = [
        {"symbol": "AAPL", "qty": 10},
        {"qty": 5},
    ]

    symbols = await grid_module.update_positions_grid(grid, positions)
    assert symbols == {"AAPL"}

    assert grid.calls[-1][0] == "setRowData"
    assert grid.calls[-1][1] == [{"symbol": "AAPL", "qty": 10}]

    # Verify warning was logged
    assert any(
        record.message == "update_positions_grid_malformed_entries" for record in caplog.records
    )

    # Verify user was notified about data integrity issue (not silently hidden)
    assert any("Data Integrity" in message for message, _ in dummy_ui)


@pytest.mark.asyncio()
async def test_update_positions_grid_dedupes_malformed_notifications(
    dummy_ui, caplog: pytest.LogCaptureFixture
) -> None:
    """Malformed position notifications are deduped to avoid spamming users."""
    grid = grid_module.create_positions_grid()
    notified_malformed: set[int] = set()

    positions = [
        {"symbol": "AAPL", "qty": 10},
        {"qty": 5},  # Malformed - missing symbol
    ]

    # First update - should notify
    symbols = await grid_module.update_positions_grid(
        grid, positions, notified_malformed=notified_malformed
    )
    assert symbols == {"AAPL"}
    assert len(dummy_ui) == 1  # One notification

    # Second update with same malformed count - should NOT notify again
    symbols = await grid_module.update_positions_grid(
        grid, positions, previous_symbols=symbols, notified_malformed=notified_malformed
    )
    assert symbols == {"AAPL"}
    assert len(dummy_ui) == 1  # Still one notification (deduped)

    # Third update with different malformed count - should notify
    positions_more_malformed = [
        {"symbol": "AAPL", "qty": 10},
        {"qty": 5},  # Malformed
        {"qty": 3},  # Malformed - now 2 malformed
    ]
    symbols = await grid_module.update_positions_grid(
        grid,
        positions_more_malformed,
        previous_symbols=symbols,
        notified_malformed=notified_malformed,
    )
    assert symbols == {"AAPL"}
    assert len(dummy_ui) == 2  # New notification for different count


@pytest.mark.asyncio()
async def test_on_close_position_blocks_kill_switch_engaged(
    dummy_ui, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Kill switch ENGAGED prevents close position (returns early with notification)."""
    mock_client = AsyncMock()
    mock_client.fetch_kill_switch_status.return_value = {"state": "ENGAGED"}
    monkeypatch.setattr(grid_module.AsyncTradingClient, "get", lambda: mock_client)

    await grid_module.on_close_position("AAPL", 10, "user-1", "admin")

    # Kill switch check was called
    mock_client.fetch_kill_switch_status.assert_awaited_once()
    # close_position was NOT called (blocked by kill switch)
    mock_client.close_position.assert_not_awaited()
    # User was notified
    assert any("Kill Switch" in message for message, _ in dummy_ui)


@pytest.mark.asyncio()
async def test_on_close_position_blocks_viewer_role(
    dummy_ui, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Viewers cannot execute trades."""
    mock_client = AsyncMock()
    monkeypatch.setattr(grid_module.AsyncTradingClient, "get", lambda: mock_client)

    await grid_module.on_close_position("AAPL", 10, "user-1", "viewer")

    # Client methods were NOT called (blocked before API call)
    mock_client.fetch_kill_switch_status.assert_not_awaited()
    mock_client.close_position.assert_not_awaited()
    # User was notified
    assert any("Viewers cannot" in message for message, _ in dummy_ui)


@pytest.mark.asyncio()
async def test_on_close_position_blocks_zero_quantity(
    dummy_ui, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cannot close position with zero quantity."""
    mock_client = AsyncMock()
    mock_client.fetch_kill_switch_status.return_value = {"state": "DISENGAGED"}
    mock_client.fetch_circuit_breaker_status.return_value = {"state": "CLOSED"}
    monkeypatch.setattr(grid_module.AsyncTradingClient, "get", lambda: mock_client)

    await grid_module.on_close_position("AAPL", 0, "user-1", "admin")

    # close_position was NOT called
    mock_client.close_position.assert_not_awaited()
    # User was notified about zero quantity
    assert any("zero quantity" in message for message, _ in dummy_ui)


@pytest.mark.asyncio()
async def test_on_close_position_blocks_fractional_quantity(
    dummy_ui, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fractional shares not supported."""
    mock_client = AsyncMock()
    mock_client.fetch_kill_switch_status.return_value = {"state": "DISENGAGED"}
    monkeypatch.setattr(grid_module.AsyncTradingClient, "get", lambda: mock_client)

    await grid_module.on_close_position("AAPL", 10.5, "user-1", "admin")

    # close_position was NOT called
    mock_client.close_position.assert_not_awaited()
    # User was notified about fractional shares
    assert any("Fractional" in message for message, _ in dummy_ui)


@pytest.mark.asyncio()
async def test_on_close_position_blocks_nan_quantity(
    dummy_ui, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NaN quantity values are rejected."""
    mock_client = AsyncMock()
    monkeypatch.setattr(grid_module.AsyncTradingClient, "get", lambda: mock_client)

    await grid_module.on_close_position("AAPL", float("nan"), "user-1", "admin")

    # close_position was NOT called
    mock_client.close_position.assert_not_awaited()
    # User was notified about invalid quantity
    assert any("Invalid" in message for message, _ in dummy_ui)


class DummyButton:
    """Mock button that captures on_click callback."""

    def __init__(self, label: str, on_click=None):
        self.label = label
        self.on_click = on_click
        self._enabled = True

    def classes(self, *args, **kwargs):
        return self

    def disable(self):
        self._enabled = False

    def enable(self):
        self._enabled = True


class DummyDialog:
    """Mock dialog that captures inner elements."""

    def __init__(self):
        self.is_open = False
        self._closed = False

    def open(self):
        self.is_open = True

    def close(self):
        self._closed = True
        self.is_open = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class DummyCard:
    """Mock card context manager."""

    def classes(self, *args, **kwargs):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class DummyRow:
    """Mock row context manager."""

    def classes(self, *args, **kwargs):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


@pytest.fixture()
def dummy_ui_with_dialog(monkeypatch: pytest.MonkeyPatch):
    """UI fixture with dialog support for testing confirm flow."""
    notify_calls: list[tuple[str, dict]] = []
    captured_buttons: list[DummyButton] = []
    dialog_instance = DummyDialog()

    def aggrid(options: dict) -> DummyGrid:
        return DummyGrid(options)

    def notify(message: str, **kwargs):
        notify_calls.append((message, kwargs))

    def dialog():
        return dialog_instance

    def card():
        return DummyCard()

    def label(text: str):
        return types.SimpleNamespace(classes=lambda *a, **k: None)

    def button(label: str, on_click=None):
        btn = DummyButton(label, on_click)
        captured_buttons.append(btn)
        return btn

    def row():
        return DummyRow()

    dummy = types.SimpleNamespace(
        aggrid=aggrid,
        notify=notify,
        dialog=dialog,
        card=card,
        label=label,
        button=button,
        row=row,
    )
    monkeypatch.setattr(grid_module, "ui", dummy)
    return {
        "notify_calls": notify_calls,
        "buttons": captured_buttons,
        "dialog": dialog_instance,
    }


@pytest.mark.asyncio()
async def test_on_close_position_passes_qty_to_close_position(
    dummy_ui_with_dialog, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify close_position is called with qty parameter matching user confirmation."""
    mock_client = AsyncMock()
    mock_client.fetch_kill_switch_status.return_value = {"state": "DISENGAGED"}
    mock_client.fetch_circuit_breaker_status.return_value = {"state": "CLOSED"}
    mock_client.close_position.return_value = {"order_id": "test-order-123"}
    monkeypatch.setattr(grid_module.AsyncTradingClient, "get", lambda: mock_client)

    # Call on_close_position - this opens the dialog
    await grid_module.on_close_position("AAPL", 10, "user-1", "admin")

    # Dialog should be open
    assert dummy_ui_with_dialog["dialog"].is_open

    # Find and click the Confirm button
    confirm_btn = next((b for b in dummy_ui_with_dialog["buttons"] if b.label == "Confirm"), None)
    assert confirm_btn is not None
    assert confirm_btn.on_click is not None

    # Invoke the confirm callback
    await confirm_btn.on_click()

    # Verify close_position was called with qty
    mock_client.close_position.assert_awaited_once()
    call_kwargs = mock_client.close_position.call_args.kwargs
    assert call_kwargs["symbol"] == "AAPL"
    assert call_kwargs["qty"] == 10  # abs(10) = 10
    assert call_kwargs["user_id"] == "user-1"
    assert call_kwargs["role"] == "admin"


@pytest.mark.asyncio()
async def test_on_close_position_uses_abs_qty_for_negative(
    dummy_ui_with_dialog, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify abs(qty) is passed for negative positions (short positions)."""
    mock_client = AsyncMock()
    mock_client.fetch_kill_switch_status.return_value = {"state": "DISENGAGED"}
    mock_client.fetch_circuit_breaker_status.return_value = {"state": "CLOSED"}
    mock_client.close_position.return_value = {"order_id": "test-order-456"}
    monkeypatch.setattr(grid_module.AsyncTradingClient, "get", lambda: mock_client)

    # Call with negative qty (short position)
    await grid_module.on_close_position("AAPL", -15, "user-1", "admin")

    # Find and click Confirm
    confirm_btn = next((b for b in dummy_ui_with_dialog["buttons"] if b.label == "Confirm"), None)
    assert confirm_btn is not None
    await confirm_btn.on_click()

    # Verify abs(qty) was passed
    call_kwargs = mock_client.close_position.call_args.kwargs
    assert call_kwargs["qty"] == 15  # abs(-15) = 15


@pytest.mark.asyncio()
async def test_on_close_position_proceeds_when_safety_service_unreachable(
    dummy_ui_with_dialog, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail-open: safety service network error should warn but proceed (risk reduction)."""
    mock_client = AsyncMock()
    # Simulate network error when checking kill switch
    mock_client.fetch_kill_switch_status.side_effect = httpx.RequestError("Connection refused")
    mock_client.fetch_circuit_breaker_status.side_effect = httpx.RequestError("Connection refused")
    mock_client.close_position.return_value = {"order_id": "test-order-789"}
    monkeypatch.setattr(grid_module.AsyncTradingClient, "get", lambda: mock_client)

    # Call on_close_position - should proceed despite safety service failure
    await grid_module.on_close_position("AAPL", 10, "user-1", "admin")

    # Dialog should still open (fail-open behavior)
    assert dummy_ui_with_dialog["dialog"].is_open

    # User should be warned about safety service
    assert any(
        "unreachable" in message.lower() for message, _ in dummy_ui_with_dialog["notify_calls"]
    )

    # Confirm button should still work
    confirm_btn = next((b for b in dummy_ui_with_dialog["buttons"] if b.label == "Confirm"), None)
    assert confirm_btn is not None
    await confirm_btn.on_click()

    # close_position should still be called (fail-open)
    mock_client.close_position.assert_awaited_once()


# ============================================================================
# Tests for missing coverage - Grid update edge cases
# ============================================================================


@pytest.mark.asyncio()
async def test_update_positions_grid_before_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    """When grid is not ready, use options rowData instead of run_grid_method."""
    # Create a grid that is NOT ready (event not set)

    class NotSetEvent:
        def is_set(self) -> bool:
            return False

        def set(self) -> None:
            pass

    monkeypatch.setattr(grid_module.asyncio, "Event", NotSetEvent)

    notify_calls: list = []

    def aggrid(options: dict) -> DummyGrid:
        return DummyGrid(options)

    def notify(message: str, **kwargs):
        notify_calls.append((message, kwargs))

    dummy = types.SimpleNamespace(aggrid=aggrid, notify=notify)
    monkeypatch.setattr(grid_module, "ui", dummy)

    grid = grid_module.create_positions_grid()

    positions = [{"symbol": "AAPL", "qty": 10}]
    symbols = await grid_module.update_positions_grid(grid, positions)

    # Should update via options, not run_grid_method
    assert symbols == {"AAPL"}
    assert grid.options["rowData"] == positions
    # No run_grid_method calls for setRowData
    assert not any(call[0] == "setRowData" for call in grid.calls)


@pytest.mark.asyncio()
async def test_update_positions_grid_calculates_unrealized_plpc(dummy_ui) -> None:
    """Test unrealized_plpc calculation when not provided."""
    grid = grid_module.create_positions_grid()

    # Position without unrealized_plpc but with data to calculate it
    positions = [
        {
            "symbol": "AAPL",
            "qty": 10,
            "avg_entry_price": 100.0,
            "unrealized_pl": 50.0,
            # unrealized_plpc not provided - should be calculated
        }
    ]

    symbols = await grid_module.update_positions_grid(grid, positions)
    assert symbols == {"AAPL"}

    # Verify the calculation: unrealized_pl / (avg_entry * abs(qty))
    # 50.0 / (100.0 * 10) = 0.05 (5%)
    method, payload = grid.calls[-1]
    assert method == "setRowData"
    assert len(payload) == 1
    assert payload[0]["unrealized_plpc"] == pytest.approx(0.05)


@pytest.mark.asyncio()
async def test_update_positions_grid_preserves_existing_unrealized_plpc(dummy_ui) -> None:
    """Test that existing unrealized_plpc is preserved and not recalculated."""
    grid = grid_module.create_positions_grid()

    # Position WITH unrealized_plpc already provided - should not be recalculated
    positions = [
        {
            "symbol": "AAPL",
            "qty": 10,
            "avg_entry_price": 100.0,
            "unrealized_pl": 50.0,
            "unrealized_plpc": 0.10,  # Already provided (10%)
        }
    ]

    symbols = await grid_module.update_positions_grid(grid, positions)
    assert symbols == {"AAPL"}

    method, payload = grid.calls[-1]
    assert method == "setRowData"
    assert len(payload) == 1
    # Should preserve the original value, not recalculate (would be 0.05 if recalculated)
    assert payload[0]["unrealized_plpc"] == 0.10


@pytest.mark.asyncio()
async def test_update_positions_grid_skips_calculation_with_zero_values(dummy_ui) -> None:
    """Test that unrealized_plpc calculation skips when avg_entry or qty is zero."""
    grid = grid_module.create_positions_grid()

    positions = [
        {
            "symbol": "AAPL",
            "qty": 0,  # Zero qty - skip calculation
            "avg_entry_price": 100.0,
            "unrealized_pl": 0.0,
        },
        {
            "symbol": "MSFT",
            "qty": 10,
            "avg_entry_price": 0,  # Zero avg_entry - skip calculation
            "unrealized_pl": 50.0,
        },
    ]

    symbols = await grid_module.update_positions_grid(grid, positions)
    assert symbols == {"AAPL", "MSFT"}

    method, payload = grid.calls[-1]
    assert method == "setRowData"
    # unrealized_plpc should not be added (no calculation happened)
    assert "unrealized_plpc" not in payload[0]
    assert "unrealized_plpc" not in payload[1]


@pytest.mark.asyncio()
async def test_update_positions_grid_skips_calculation_with_invalid_values(dummy_ui) -> None:
    """Test that unrealized_plpc calculation skips when values are invalid."""
    grid = grid_module.create_positions_grid()

    positions = [
        {
            "symbol": "AAPL",
            "qty": "invalid",  # Invalid string
            "avg_entry_price": 100.0,
            "unrealized_pl": 50.0,
        },
        {
            "symbol": "MSFT",
            "qty": 10,
            "avg_entry_price": None,  # None value
            "unrealized_pl": 50.0,
        },
    ]

    symbols = await grid_module.update_positions_grid(grid, positions)
    assert symbols == {"AAPL", "MSFT"}

    method, payload = grid.calls[-1]
    assert method == "setRowData"
    # unrealized_plpc should not be added (calculation skipped due to errors)
    assert "unrealized_plpc" not in payload[0]
    assert "unrealized_plpc" not in payload[1]


@pytest.mark.asyncio()
async def test_update_positions_grid_clears_notified_on_resolve(dummy_ui) -> None:
    """Test that notified_malformed is cleared when issue resolves."""
    grid = grid_module.create_positions_grid()
    notified_malformed: set[int] = set()

    # First update with malformed entry
    positions_with_malformed = [
        {"symbol": "AAPL", "qty": 10},
        {"qty": 5},  # Malformed
    ]
    await grid_module.update_positions_grid(
        grid, positions_with_malformed, notified_malformed=notified_malformed
    )
    assert 1 in notified_malformed

    # Second update without malformed entries - should clear the set
    positions_all_valid = [{"symbol": "AAPL", "qty": 10}]
    await grid_module.update_positions_grid(
        grid, positions_all_valid, notified_malformed=notified_malformed
    )
    assert len(notified_malformed) == 0  # Set should be cleared


# ============================================================================
# Tests for missing coverage - on_close_position edge cases
# ============================================================================


@pytest.mark.asyncio()
async def test_on_close_position_blocks_invalid_qty_type(
    dummy_ui, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Invalid quantity type (cannot convert to float) is rejected."""
    mock_client = AsyncMock()
    monkeypatch.setattr(grid_module.AsyncTradingClient, "get", lambda: mock_client)

    await grid_module.on_close_position("AAPL", "invalid_string", "user-1", "admin")

    mock_client.close_position.assert_not_awaited()
    assert any("Invalid" in message for message, _ in dummy_ui)


@pytest.mark.asyncio()
async def test_on_close_position_blocks_infinity_quantity(
    dummy_ui, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Infinity quantity values are rejected."""
    mock_client = AsyncMock()
    monkeypatch.setattr(grid_module.AsyncTradingClient, "get", lambda: mock_client)

    await grid_module.on_close_position("AAPL", float("inf"), "user-1", "admin")

    mock_client.close_position.assert_not_awaited()
    assert any("Invalid" in message for message, _ in dummy_ui)


@pytest.mark.asyncio()
async def test_on_close_position_uses_cached_kill_switch_engaged(
    dummy_ui, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cached kill switch ENGAGED state blocks close instantly (no API call)."""
    mock_client = AsyncMock()
    monkeypatch.setattr(grid_module.AsyncTradingClient, "get", lambda: mock_client)

    # Pass kill_switch_engaged=True to use cached state
    await grid_module.on_close_position("AAPL", 10, "user-1", "admin", kill_switch_engaged=True)

    # No API call made (used cached state)
    mock_client.fetch_kill_switch_status.assert_not_awaited()
    mock_client.close_position.assert_not_awaited()
    assert any("Kill Switch" in message for message, _ in dummy_ui)


@pytest.mark.asyncio()
async def test_on_close_position_skips_precheck_when_cached_safe(
    dummy_ui_with_dialog, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cached kill switch False skips pre-check but still checks at confirm time."""
    mock_client = AsyncMock()
    mock_client.fetch_kill_switch_status.return_value = {"state": "DISENGAGED"}
    mock_client.fetch_circuit_breaker_status.return_value = {"state": "CLOSED"}
    mock_client.close_position.return_value = {"order_id": "test-order-123"}
    monkeypatch.setattr(grid_module.AsyncTradingClient, "get", lambda: mock_client)

    # Pass kill_switch_engaged=False to skip pre-check
    await grid_module.on_close_position("AAPL", 10, "user-1", "admin", kill_switch_engaged=False)

    # Pre-check was skipped, but dialog opened
    assert dummy_ui_with_dialog["dialog"].is_open
    # Only circuit breaker check was made (not kill switch pre-check)
    mock_client.fetch_circuit_breaker_status.assert_awaited_once()

    # Confirm and verify kill switch is checked at confirm time
    confirm_btn = next((b for b in dummy_ui_with_dialog["buttons"] if b.label == "Confirm"), None)
    await confirm_btn.on_click()

    # Kill switch was checked at confirm time
    mock_client.fetch_kill_switch_status.assert_awaited_once()


@pytest.mark.asyncio()
async def test_on_close_position_5xx_error_proceeds_with_warning(
    dummy_ui_with_dialog, monkeypatch: pytest.MonkeyPatch
) -> None:
    """5xx errors fail-open for risk reduction (warn but proceed)."""
    mock_client = AsyncMock()
    # Simulate 5xx server error
    response = httpx.Response(503)
    mock_client.fetch_kill_switch_status.side_effect = httpx.HTTPStatusError(
        "Service Unavailable", request=httpx.Request("GET", "http://test"), response=response
    )
    mock_client.fetch_circuit_breaker_status.return_value = {"state": "CLOSED"}
    mock_client.close_position.return_value = {"order_id": "test-order-123"}
    monkeypatch.setattr(grid_module.AsyncTradingClient, "get", lambda: mock_client)

    await grid_module.on_close_position("AAPL", 10, "user-1", "admin")

    # Dialog should open (fail-open)
    assert dummy_ui_with_dialog["dialog"].is_open
    # Warning notification about 5xx
    assert any("503" in message for message, _ in dummy_ui_with_dialog["notify_calls"])


@pytest.mark.asyncio()
async def test_on_close_position_4xx_error_blocks(
    dummy_ui_with_dialog, monkeypatch: pytest.MonkeyPatch
) -> None:
    """4xx errors fail-closed (block the action)."""
    mock_client = AsyncMock()
    # Simulate 4xx client error
    response = httpx.Response(403)
    mock_client.fetch_kill_switch_status.side_effect = httpx.HTTPStatusError(
        "Forbidden", request=httpx.Request("GET", "http://test"), response=response
    )
    monkeypatch.setattr(grid_module.AsyncTradingClient, "get", lambda: mock_client)

    await grid_module.on_close_position("AAPL", 10, "user-1", "admin")

    # Dialog should NOT open (fail-closed on 4xx)
    assert not dummy_ui_with_dialog["dialog"].is_open
    # Error notification
    assert any("403" in message for message, _ in dummy_ui_with_dialog["notify_calls"])


@pytest.mark.asyncio()
async def test_on_close_position_circuit_breaker_tripped_warns_but_allows(
    dummy_ui_with_dialog, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Circuit breaker tripped warns but allows close (risk reduction permitted)."""
    mock_client = AsyncMock()
    mock_client.fetch_kill_switch_status.return_value = {"state": "DISENGAGED"}
    mock_client.fetch_circuit_breaker_status.return_value = {"state": "TRIPPED"}
    mock_client.close_position.return_value = {"order_id": "test-order-123"}
    monkeypatch.setattr(grid_module.AsyncTradingClient, "get", lambda: mock_client)

    await grid_module.on_close_position("AAPL", 10, "user-1", "admin")

    # Dialog should open (close allowed for risk reduction)
    assert dummy_ui_with_dialog["dialog"].is_open
    # Warning about circuit breaker state
    assert any("TRIPPED" in message for message, _ in dummy_ui_with_dialog["notify_calls"])


@pytest.mark.asyncio()
async def test_on_close_position_circuit_breaker_quiet_period_warns(
    dummy_ui_with_dialog, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Circuit breaker in QUIET_PERIOD warns but allows close."""
    mock_client = AsyncMock()
    mock_client.fetch_kill_switch_status.return_value = {"state": "DISENGAGED"}
    mock_client.fetch_circuit_breaker_status.return_value = {"state": "QUIET_PERIOD"}
    mock_client.close_position.return_value = {"order_id": "test-order-123"}
    monkeypatch.setattr(grid_module.AsyncTradingClient, "get", lambda: mock_client)

    await grid_module.on_close_position("AAPL", 10, "user-1", "admin")

    assert dummy_ui_with_dialog["dialog"].is_open
    assert any("QUIET_PERIOD" in message for message, _ in dummy_ui_with_dialog["notify_calls"])


@pytest.mark.asyncio()
async def test_on_close_position_circuit_breaker_http_error_silent(
    dummy_ui_with_dialog, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Circuit breaker HTTP error is logged but doesn't block close."""
    mock_client = AsyncMock()
    mock_client.fetch_kill_switch_status.return_value = {"state": "DISENGAGED"}
    response = httpx.Response(500)
    mock_client.fetch_circuit_breaker_status.side_effect = httpx.HTTPStatusError(
        "Internal Error", request=httpx.Request("GET", "http://test"), response=response
    )
    mock_client.close_position.return_value = {"order_id": "test-order-123"}
    monkeypatch.setattr(grid_module.AsyncTradingClient, "get", lambda: mock_client)

    await grid_module.on_close_position("AAPL", 10, "user-1", "admin")

    # Dialog should still open (circuit breaker error is non-blocking)
    assert dummy_ui_with_dialog["dialog"].is_open
    # Should be logged
    assert any(
        "close_position_circuit_breaker_check_failed" in record.message for record in caplog.records
    )


# ============================================================================
# Tests for missing coverage - confirm() callback edge cases
# ============================================================================


@pytest.mark.asyncio()
async def test_confirm_blocks_when_kill_switch_engaged_at_confirm_time(
    dummy_ui_with_dialog, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Kill switch engaged at confirmation time blocks the order."""
    mock_client = AsyncMock()
    # Kill switch OK at dialog open, ENGAGED at confirm time
    mock_client.fetch_kill_switch_status.side_effect = [
        {"state": "DISENGAGED"},  # First call (pre-check)
        {"state": "ENGAGED"},  # Second call (confirm time)
    ]
    mock_client.fetch_circuit_breaker_status.return_value = {"state": "CLOSED"}
    monkeypatch.setattr(grid_module.AsyncTradingClient, "get", lambda: mock_client)

    await grid_module.on_close_position("AAPL", 10, "user-1", "admin")
    assert dummy_ui_with_dialog["dialog"].is_open

    confirm_btn = next((b for b in dummy_ui_with_dialog["buttons"] if b.label == "Confirm"), None)
    await confirm_btn.on_click()

    # close_position should NOT be called (blocked at confirm time)
    mock_client.close_position.assert_not_awaited()
    # Dialog should be closed
    assert dummy_ui_with_dialog["dialog"]._closed
    # Notification about kill switch
    assert any("Kill Switch" in message for message, _ in dummy_ui_with_dialog["notify_calls"])


@pytest.mark.asyncio()
async def test_confirm_5xx_error_proceeds(
    dummy_ui_with_dialog, monkeypatch: pytest.MonkeyPatch
) -> None:
    """5xx error at confirm time proceeds (fail-open for risk reduction)."""
    mock_client = AsyncMock()
    # OK at pre-check, 5xx at confirm time
    response = httpx.Response(502)
    mock_client.fetch_kill_switch_status.side_effect = [
        {"state": "DISENGAGED"},  # Pre-check
        httpx.HTTPStatusError(
            "Bad Gateway", request=httpx.Request("GET", "http://test"), response=response
        ),  # Confirm
    ]
    mock_client.fetch_circuit_breaker_status.return_value = {"state": "CLOSED"}
    mock_client.close_position.return_value = {"order_id": "test-order-123"}
    monkeypatch.setattr(grid_module.AsyncTradingClient, "get", lambda: mock_client)

    await grid_module.on_close_position("AAPL", 10, "user-1", "admin")

    confirm_btn = next((b for b in dummy_ui_with_dialog["buttons"] if b.label == "Confirm"), None)
    await confirm_btn.on_click()

    # Should proceed despite 5xx
    mock_client.close_position.assert_awaited_once()
    # Warning about 502
    assert any("502" in message for message, _ in dummy_ui_with_dialog["notify_calls"])


@pytest.mark.asyncio()
async def test_confirm_4xx_error_blocks(
    dummy_ui_with_dialog, monkeypatch: pytest.MonkeyPatch
) -> None:
    """4xx error at confirm time blocks the order."""
    mock_client = AsyncMock()
    # OK at pre-check, 4xx at confirm time
    response = httpx.Response(401)
    mock_client.fetch_kill_switch_status.side_effect = [
        {"state": "DISENGAGED"},  # Pre-check
        httpx.HTTPStatusError(
            "Unauthorized", request=httpx.Request("GET", "http://test"), response=response
        ),  # Confirm
    ]
    mock_client.fetch_circuit_breaker_status.return_value = {"state": "CLOSED"}
    monkeypatch.setattr(grid_module.AsyncTradingClient, "get", lambda: mock_client)

    await grid_module.on_close_position("AAPL", 10, "user-1", "admin")

    confirm_btn = next((b for b in dummy_ui_with_dialog["buttons"] if b.label == "Confirm"), None)
    await confirm_btn.on_click()

    # Should NOT proceed on 4xx
    mock_client.close_position.assert_not_awaited()
    # Dialog closed
    assert dummy_ui_with_dialog["dialog"]._closed
    # Error notification
    assert any("Cannot verify" in message for message, _ in dummy_ui_with_dialog["notify_calls"])


@pytest.mark.asyncio()
async def test_confirm_network_error_proceeds(
    dummy_ui_with_dialog, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Network error at confirm time proceeds (fail-open)."""
    mock_client = AsyncMock()
    # OK at pre-check, network error at confirm time
    mock_client.fetch_kill_switch_status.side_effect = [
        {"state": "DISENGAGED"},  # Pre-check
        httpx.RequestError("Connection reset"),  # Confirm
    ]
    mock_client.fetch_circuit_breaker_status.return_value = {"state": "CLOSED"}
    mock_client.close_position.return_value = {"order_id": "test-order-123"}
    monkeypatch.setattr(grid_module.AsyncTradingClient, "get", lambda: mock_client)

    await grid_module.on_close_position("AAPL", 10, "user-1", "admin")

    confirm_btn = next((b for b in dummy_ui_with_dialog["buttons"] if b.label == "Confirm"), None)
    await confirm_btn.on_click()

    # Should proceed despite network error
    mock_client.close_position.assert_awaited_once()
    # Warning about unreachable
    assert any(
        "unreachable" in message.lower() for message, _ in dummy_ui_with_dialog["notify_calls"]
    )


@pytest.mark.asyncio()
async def test_confirm_missing_order_id_in_response(
    dummy_ui_with_dialog, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Response without order_id logs warning but shows success."""
    mock_client = AsyncMock()
    mock_client.fetch_kill_switch_status.return_value = {"state": "DISENGAGED"}
    mock_client.fetch_circuit_breaker_status.return_value = {"state": "CLOSED"}
    mock_client.close_position.return_value = {}  # No order_id in response
    monkeypatch.setattr(grid_module.AsyncTradingClient, "get", lambda: mock_client)

    await grid_module.on_close_position("AAPL", 10, "user-1", "admin")

    confirm_btn = next((b for b in dummy_ui_with_dialog["buttons"] if b.label == "Confirm"), None)
    await confirm_btn.on_click()

    # Should still succeed
    assert any("Closing AAPL" in message for message, _ in dummy_ui_with_dialog["notify_calls"])
    # Warning should be logged
    assert any("close_position_missing_order_id" in record.message for record in caplog.records)


@pytest.mark.asyncio()
async def test_confirm_close_position_http_error(
    dummy_ui_with_dialog, monkeypatch: pytest.MonkeyPatch
) -> None:
    """HTTP error from close_position shows error notification."""
    mock_client = AsyncMock()
    mock_client.fetch_kill_switch_status.return_value = {"state": "DISENGAGED"}
    mock_client.fetch_circuit_breaker_status.return_value = {"state": "CLOSED"}
    response = httpx.Response(500)
    mock_client.close_position.side_effect = httpx.HTTPStatusError(
        "Internal Error", request=httpx.Request("POST", "http://test"), response=response
    )
    monkeypatch.setattr(grid_module.AsyncTradingClient, "get", lambda: mock_client)

    await grid_module.on_close_position("AAPL", 10, "user-1", "admin")

    confirm_btn = next((b for b in dummy_ui_with_dialog["buttons"] if b.label == "Confirm"), None)
    await confirm_btn.on_click()

    # Error notification
    assert any(
        "Close failed" in message and "500" in message
        for message, _ in dummy_ui_with_dialog["notify_calls"]
    )


@pytest.mark.asyncio()
async def test_confirm_close_position_network_error(
    dummy_ui_with_dialog, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Network error from close_position shows retry notification."""
    mock_client = AsyncMock()
    mock_client.fetch_kill_switch_status.return_value = {"state": "DISENGAGED"}
    mock_client.fetch_circuit_breaker_status.return_value = {"state": "CLOSED"}
    mock_client.close_position.side_effect = httpx.RequestError("Connection refused")
    monkeypatch.setattr(grid_module.AsyncTradingClient, "get", lambda: mock_client)

    await grid_module.on_close_position("AAPL", 10, "user-1", "admin")

    confirm_btn = next((b for b in dummy_ui_with_dialog["buttons"] if b.label == "Confirm"), None)
    await confirm_btn.on_click()

    # Error notification suggesting retry
    assert any(
        "network error" in message.lower() and "retry" in message.lower()
        for message, _ in dummy_ui_with_dialog["notify_calls"]
    )


@pytest.mark.asyncio()
async def test_confirm_double_click_prevention(
    dummy_ui_with_dialog, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Double-clicking confirm button only submits once."""
    mock_client = AsyncMock()
    mock_client.fetch_kill_switch_status.return_value = {"state": "DISENGAGED"}
    mock_client.fetch_circuit_breaker_status.return_value = {"state": "CLOSED"}

    # Make close_position slow to simulate in-flight request
    async def slow_close(**kwargs):
        await asyncio.sleep(0.1)
        return {"order_id": "test-order-123"}

    mock_client.close_position.side_effect = slow_close
    monkeypatch.setattr(grid_module.AsyncTradingClient, "get", lambda: mock_client)

    await grid_module.on_close_position("AAPL", 10, "user-1", "admin")

    confirm_btn = next((b for b in dummy_ui_with_dialog["buttons"] if b.label == "Confirm"), None)

    # Simulate double-click by calling twice quickly
    import asyncio

    task1 = asyncio.create_task(confirm_btn.on_click())
    task2 = asyncio.create_task(confirm_btn.on_click())  # Second click while first is in progress

    await asyncio.gather(task1, task2)

    # close_position should only be called once (double-click prevention)
    assert mock_client.close_position.await_count == 1
