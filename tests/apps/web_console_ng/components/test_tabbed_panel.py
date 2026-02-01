from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from apps.web_console_ng.components import tabbed_panel as panel_module
from apps.web_console_ng.core.workspace_persistence import DatabaseUnavailableError


class DummyTabs:
    def __init__(self, value=None) -> None:
        self.value = value
        self._handler = None

    def classes(self, *_args, **_kwargs):
        return self

    def on_value_change(self, handler) -> None:
        self._handler = handler

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None


class DummyTabWithName(DummyTabs):
    def __init__(self, name=None, label=None) -> None:
        super().__init__(value=None)
        self.name = name
        self.label = label

    def set_label(self, label: str) -> None:
        self.label = label


class DummyGrid:
    def __init__(self, options) -> None:
        self.options = options
        self._events: dict[str, object] = {}
        self._classes: list[str] = []

    def classes(self, cls: str):
        self._classes.append(cls)
        return self

    def on(self, event: str, handler) -> None:
        self._events[event] = handler


class DummyTab:
    def __init__(self) -> None:
        self.label: str | None = None

    def set_label(self, label: str) -> None:
        self.label = label


class DummyContainer:
    def classes(self, *_args, **_kwargs):
        return self

    def set_visibility(self, _visible: bool) -> None:
        """Mock visibility toggle for P6T8 export toolbar support."""
        pass

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


@pytest.mark.asyncio()
async def test_tabbed_panel_state_db_unavailable() -> None:
    state = panel_module.TabbedPanelState(user_id="user-1")
    service = AsyncMock()
    service.load_panel_state.side_effect = DatabaseUnavailableError("down")
    service.save_panel_state.side_effect = DatabaseUnavailableError("down")

    await state.load(service=service)
    await state.save(service=service)


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


def test_tabbed_panel_missing_factory() -> None:
    state = panel_module.TabbedPanelState(user_id=None)
    panel = panel_module.TabbedPanel(
        state=state,
        tabs=object(),
        tab_map={},
        tab_containers={},
        grid_factories={},
        symbol_filter=panel_module.SymbolFilterState(value=None, select=None),
    )
    assert panel.ensure_tab("missing") is None


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

    # Clean up the unawaited coroutines to avoid "coroutine never awaited" warnings
    for coro in tasks:
        coro.close()


def test_tabbed_panel_handle_tab_change_noop() -> None:
    state = panel_module.TabbedPanelState(user_id=None)
    panel = panel_module.TabbedPanel(
        state=state,
        tabs=object(),
        tab_map={},
        tab_containers={},
        grid_factories={},
        symbol_filter=panel_module.SymbolFilterState(value=None, select=None),
    )
    state.active_tab = panel_module.TAB_POSITIONS
    panel._handle_tab_change(panel_module.TAB_POSITIONS)
    assert state.active_tab == panel_module.TAB_POSITIONS


@pytest.mark.asyncio()
async def test_tabbed_panel_safe_save_state_handles_error() -> None:
    state = panel_module.TabbedPanelState(user_id="user-1")
    state.save = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[assignment]
    panel = panel_module.TabbedPanel(
        state=state,
        tabs=object(),
        tab_map={},
        tab_containers={},
        grid_factories={},
        symbol_filter=panel_module.SymbolFilterState(value=None, select=None),
    )
    await panel._safe_save_state()


def test_create_tabbed_panel_and_grids(monkeypatch: pytest.MonkeyPatch) -> None:
    def column():
        return DummyContainer()

    def row():
        return DummyContainer()

    def tabs(value=None):
        return DummyTabs(value=value)

    def tab(name=None, label=None):
        return DummyTabWithName(name=name, label=label)

    def tab_panels(*_args, **_kwargs):
        return DummyContainer()

    def tab_panel(*_args, **_kwargs):
        return DummyContainer()

    dummy_ui = type(
        "ui",
        (),
        {
            "column": column,
            "row": row,
            "tabs": tabs,
            "tab": tab,
            "tab_panels": tab_panels,
            "tab_panel": tab_panel,
            "element": lambda *_args, **_kwargs: DummyContainer(),
            "aggrid": lambda options: DummyGrid(options),
        },
    )
    monkeypatch.setattr(panel_module, "ui", dummy_ui)
    monkeypatch.setattr(
        panel_module,
        "create_symbol_filter",
        lambda *_args, **_kwargs: panel_module.SymbolFilterState(value=None, select=None),
    )

    panel = panel_module.create_tabbed_panel(
        positions_grid_factory=lambda: DummyGrid({}),
        orders_grid_factory=lambda: DummyGrid({}),
        fills_grid_factory=lambda: DummyGrid({}),
        history_grid_factory=lambda: DummyGrid({}),
        state=panel_module.TabbedPanelState(user_id=None),
        symbol_options=None,
    )
    assert panel.get_grid(panel_module.TAB_POSITIONS) is not None


def test_create_fills_and_history_grids(monkeypatch: pytest.MonkeyPatch) -> None:
    dummy_ui = type(
        "ui",
        (),
        {
            "aggrid": lambda options: DummyGrid(options),
        },
    )
    monkeypatch.setattr(panel_module, "ui", dummy_ui)
    monkeypatch.setattr(panel_module, "apply_compact_grid_classes", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(panel_module, "apply_compact_grid_options", lambda options: options)

    class DummyMonitor:
        def __init__(self, *_args, **_kwargs) -> None:
            self.attached = False

        def attach_to_grid(self, grid) -> None:
            self.attached = True

    monkeypatch.setattr(panel_module, "GridPerformanceMonitor", DummyMonitor)

    fills = panel_module.create_fills_grid()
    history = panel_module.create_history_grid()

    assert "columnDefs" in fills.options
    assert "columnDefs" in history.options
    assert fills._ready_event is not None
    assert history._ready_event is not None
