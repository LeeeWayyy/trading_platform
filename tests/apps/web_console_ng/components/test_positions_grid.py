from __future__ import annotations

import types

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
def dummy_ui(monkeypatch: pytest.MonkeyPatch) -> None:
    def aggrid(options: dict) -> DummyGrid:
        return DummyGrid(options)

    dummy = types.SimpleNamespace(aggrid=aggrid)
    monkeypatch.setattr(grid_module, "ui", dummy)


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
    dummy_ui: None, caplog: pytest.LogCaptureFixture
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

    assert any(
        record.message == "update_positions_grid_malformed_entries" for record in caplog.records
    )


def test_generate_close_order_id_deterministic_and_unique() -> None:
    order_id = grid_module.generate_close_order_id("aapl", 5, "nonce", "user-1")
    order_id_same = grid_module.generate_close_order_id("AAPL", 5, "nonce", "user-1")
    order_id_diff = grid_module.generate_close_order_id("AAPL", 6, "nonce", "user-1")

    assert order_id == order_id_same
    assert order_id != order_id_diff
    assert len(order_id) == 24
