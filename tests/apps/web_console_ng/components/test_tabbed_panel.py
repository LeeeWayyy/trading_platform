from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from apps.web_console_ng.components import tabbed_panel as panel_module


class DummyTab:
    def __init__(self) -> None:
        self.label: str | None = None

    def set_label(self, label: str) -> None:
        self.label = label


class DummyContainer:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None


def test_format_tab_label() -> None:
    assert panel_module.format_tab_label("Positions", None) == "Positions"
    assert panel_module.format_tab_label("Positions", 0) == "Positions"
    assert panel_module.format_tab_label("Positions", 3) == "Positions (3)"
    assert panel_module.format_tab_label("Working", 120) == "Working (99+)"


def test_filter_items_by_symbol() -> None:
    items = [
        {"symbol": "AAPL", "qty": 1},
        {"symbol": "MSFT", "qty": 2},
    ]
    assert panel_module.filter_items_by_symbol(items, None) == items
    assert panel_module.filter_items_by_symbol(items, "msft") == [items[1]]


def test_filter_working_orders() -> None:
    orders = [
        {"symbol": "AAPL", "status": "new"},
        {"symbol": "AAPL", "status": "filled"},
        {"symbol": "MSFT", "status": "pending_new"},
    ]
    filtered = panel_module.filter_working_orders(orders)
    assert filtered == [orders[0], orders[2]]
    filtered = panel_module.filter_working_orders(orders, symbol="AAPL")
    assert filtered == [orders[0]]


@pytest.mark.asyncio()
async def test_tabbed_panel_state_load_save() -> None:
    state = panel_module.TabbedPanelState(user_id="user-1")
    service = AsyncMock()
    service.load_panel_state.return_value = {"active_tab": panel_module.TAB_WORKING}

    await state.load(service=service)
    assert state.active_tab == panel_module.TAB_WORKING

    await state.save(service=service)
    service.save_panel_state.assert_awaited_once()


@pytest.mark.asyncio()
async def test_tabbed_panel_state_load_invalid_tab() -> None:
    state = panel_module.TabbedPanelState(user_id="user-1")
    service = AsyncMock()
    service.load_panel_state.return_value = {"active_tab": "unknown"}

    await state.load(service=service)
    assert state.active_tab == panel_module.TAB_POSITIONS


def test_tabbed_panel_lazy_load(monkeypatch: pytest.MonkeyPatch) -> None:
    state = panel_module.TabbedPanelState(user_id=None)
    container = DummyContainer()
    tab = DummyTab()
    created = []

    def factory():
        obj = object()
        created.append(obj)
        return obj

    panel = panel_module.TabbedPanel(
        state=state,
        tabs=object(),
        tab_map={panel_module.TAB_POSITIONS: tab},
        tab_containers={panel_module.TAB_POSITIONS: container},
        grid_factories={panel_module.TAB_POSITIONS: factory},
        symbol_filter=panel_module.SymbolFilterState(value=None, select=None),
    )

    first = panel.ensure_tab(panel_module.TAB_POSITIONS)
    second = panel.ensure_tab(panel_module.TAB_POSITIONS)
    assert first is second
    assert len(created) == 1

    panel.set_badge_count(panel_module.TAB_POSITIONS, 5)
    assert tab.label == "Positions (5)"


def test_tabbed_panel_handle_tab_change(monkeypatch: pytest.MonkeyPatch) -> None:
    state = panel_module.TabbedPanelState(user_id=None)
    container = DummyContainer()
    tab = DummyTab()
    created = []
    called = []

    def factory():
        obj = object()
        created.append(obj)
        return obj

    def on_tab_change(name: str) -> None:
        called.append(name)

    panel = panel_module.TabbedPanel(
        state=state,
        tabs=object(),
        tab_map={panel_module.TAB_WORKING: tab},
        tab_containers={panel_module.TAB_WORKING: container},
        grid_factories={panel_module.TAB_WORKING: factory},
        symbol_filter=panel_module.SymbolFilterState(value=None, select=None),
        on_tab_change=on_tab_change,
    )

    tasks = []

    def fake_create_task(coro):
        tasks.append(coro)
        return None

    monkeypatch.setattr(panel_module.asyncio, "create_task", fake_create_task)

    panel._handle_tab_change(panel_module.TAB_WORKING)

    assert state.active_tab == panel_module.TAB_WORKING
    assert called == [panel_module.TAB_WORKING]
    assert len(created) == 1
    assert len(tasks) == 1
