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

    def classes(self, add: str | None = None, remove: str | None = None):
        if remove:
            for cls in remove.split():
                self._classes.discard(cls)
        if add:
            for cls in add.split():
                self._classes.add(cls)
        return self

    async def run_grid_method(self, method: str, payload: object) -> None:
        self.calls.append((method, payload))


@pytest.fixture()
def dummy_ui(monkeypatch: pytest.MonkeyPatch):
    notify_calls: list[tuple[str, dict]] = []

    def aggrid(options: dict) -> DummyGrid:
        return DummyGrid(options)

    def notify(message: str, **kwargs):
        notify_calls.append((message, kwargs))

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
        "unrealized_pl",
        "unrealized_plpc",
        "actions",
    ]

    symbol_col = column_defs[0]
    assert symbol_col["pinned"] == "left"

    actions_col = column_defs[-1]
    assert actions_col["pinned"] == "right"
    assert actions_col["cellRenderer"] == "closePositionRenderer"

    assert grid.options["getRowId"] == "data => data.symbol"
    assert grid.options["rowSelection"] == "multiple"
    assert grid.options["animateRows"] is True
    assert grid.options["onGridReady"] == "params => { window._positionsGridApi = params.api; }"


@pytest.mark.asyncio()
async def test_update_positions_grid_add_update_remove(dummy_ui: None) -> None:
    grid = grid_module.create_positions_grid()

    first_positions = [
        {"symbol": "AAPL", "qty": 10},
        {"symbol": "MSFT", "qty": 5},
    ]

    symbols = await grid_module.update_positions_grid(grid, first_positions)
    assert symbols == {"AAPL", "MSFT"}
    assert grid.calls[-1][0] == "api.setRowData"

    next_positions = [
        {"symbol": "AAPL", "qty": 12},
        {"symbol": "GOOG", "qty": 3},
    ]

    symbols = await grid_module.update_positions_grid(grid, next_positions, symbols)
    assert symbols == {"AAPL", "GOOG"}

    method, payload = grid.calls[-1]
    assert method == "api.applyTransaction"
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

    assert grid.calls[-1][0] == "api.setRowData"
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
    confirm_btn = next(
        (b for b in dummy_ui_with_dialog["buttons"] if b.label == "Confirm"), None
    )
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
    confirm_btn = next(
        (b for b in dummy_ui_with_dialog["buttons"] if b.label == "Confirm"), None
    )
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
    assert any("unreachable" in message.lower() for message, _ in dummy_ui_with_dialog["notify_calls"])

    # Confirm button should still work
    confirm_btn = next(
        (b for b in dummy_ui_with_dialog["buttons"] if b.label == "Confirm"), None
    )
    assert confirm_btn is not None
    await confirm_btn.on_click()

    # close_position should still be called (fail-open)
    mock_client.close_position.assert_awaited_once()


