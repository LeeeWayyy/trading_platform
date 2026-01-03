from __future__ import annotations

import types
from unittest.mock import AsyncMock

import pytest

from apps.web_console_ng.components import orders_table as orders_module


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
    monkeypatch.setattr(orders_module, "ui", dummy)
    return notify_calls


def test_create_orders_table_columns(dummy_ui) -> None:
    grid = orders_module.create_orders_table()
    assert isinstance(grid, DummyGrid)

    column_defs = grid.options["columnDefs"]
    fields = [col["field"] for col in column_defs]
    assert fields == [
        "symbol",
        "side",
        "qty",
        "type",
        "limit_price",
        "status",
        "created_at",
        "actions",
    ]

    status_col = column_defs[5]
    assert status_col["cellRenderer"] == "statusBadgeRenderer"

    actions_col = column_defs[-1]
    assert actions_col["cellRenderer"] == "cancelButtonRenderer"

    limit_col = column_defs[4]
    assert "valueFormatter" in limit_col

    created_col = column_defs[6]
    assert "UTC" in created_col["valueFormatter"]

    assert grid.options["getRowId"] == "data => data.client_order_id"
    assert grid.options["onGridReady"] == "params => { window._ordersGridApi = params.api; }"


@pytest.mark.asyncio()
async def test_update_orders_table_add_update_remove(dummy_ui) -> None:
    grid = orders_module.create_orders_table()

    first_orders = [
        {"client_order_id": "id-1", "symbol": "AAPL", "status": "new"},
        {"client_order_id": "id-2", "symbol": "MSFT", "status": "new"},
    ]

    current_ids = await orders_module.update_orders_table(grid, first_orders)
    assert current_ids == {"id-1", "id-2"}
    assert grid.calls[-1][0] == "api.setRowData"

    next_orders = [
        {"client_order_id": "id-1", "symbol": "AAPL", "status": "filled"},
        {"client_order_id": "id-3", "symbol": "GOOG", "status": "new"},
    ]

    current_ids = await orders_module.update_orders_table(grid, next_orders, current_ids)
    assert current_ids == {"id-1", "id-3"}

    method, payload = grid.calls[-1]
    assert method == "api.applyTransaction"
    assert payload == {
        "add": [{"client_order_id": "id-3", "symbol": "GOOG", "status": "new"}],
        "update": [{"client_order_id": "id-1", "symbol": "AAPL", "status": "filled"}],
        "remove": [{"client_order_id": "id-2"}],
    }


@pytest.mark.asyncio()
async def test_update_orders_table_missing_client_order_id_fallback(dummy_ui) -> None:
    grid = orders_module.create_orders_table()

    orders = [
        {"id": "broker-1", "symbol": "AAPL", "status": "new"},
    ]

    current_ids = await orders_module.update_orders_table(grid, orders)
    assert current_ids == {"__ng_fallback_broker-1"}

    method, payload = grid.calls[-1]
    assert method == "api.setRowData"
    row = payload[0]
    assert row["client_order_id"] == "__ng_fallback_broker-1"
    assert row["_missing_client_order_id"] is True
    assert row["_broker_order_id"] == "broker-1"


@pytest.mark.asyncio()
async def test_update_orders_table_missing_all_ids_notifies_once(dummy_ui) -> None:
    grid = orders_module.create_orders_table()
    notified_missing_ids: set[str] = set()
    synthetic_id_map: dict[str, str] = {}

    orders = [
        {
            "symbol": "AAPL",
            "side": "buy",
            "created_at": "2025-01-01T00:00:00Z",
            "qty": 1,
            "type": "market",
            "status": "new",
        }
    ]

    current_ids = await orders_module.update_orders_table(
        grid,
        orders,
        notified_missing_ids=notified_missing_ids,
        synthetic_id_map=synthetic_id_map,
    )
    assert len(current_ids) == 1
    synthetic_id = next(iter(current_ids))
    assert synthetic_id.startswith("unknown_")

    notify_calls = dummy_ui
    assert len(notify_calls) == 1

    current_ids = await orders_module.update_orders_table(
        grid,
        orders,
        previous_order_ids=current_ids,
        notified_missing_ids=notified_missing_ids,
        synthetic_id_map=synthetic_id_map,
    )
    assert len(current_ids) == 1
    assert synthetic_id in current_ids
    assert len(notify_calls) == 1


@pytest.mark.asyncio()
async def test_on_cancel_order_blocks_missing_id(dummy_ui, monkeypatch: pytest.MonkeyPatch) -> None:
    mock_client = AsyncMock()
    monkeypatch.setattr(orders_module.AsyncTradingClient, "get", lambda: mock_client)

    await orders_module.on_cancel_order("", "AAPL", "user-1", "admin")

    assert mock_client.cancel_order.await_count == 0
    assert any("missing order ID" in message for message, _ in dummy_ui)


@pytest.mark.asyncio()
async def test_on_cancel_order_blocks_viewer(dummy_ui, monkeypatch: pytest.MonkeyPatch) -> None:
    mock_client = AsyncMock()
    monkeypatch.setattr(orders_module.AsyncTradingClient, "get", lambda: mock_client)

    await orders_module.on_cancel_order("order-1", "AAPL", "user-1", "viewer")

    assert mock_client.cancel_order.await_count == 0
    assert any("Viewers cannot cancel orders" in message for message, _ in dummy_ui)


@pytest.mark.asyncio()
async def test_on_cancel_order_uses_broker_id_for_fallback(
    dummy_ui, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_client = AsyncMock()
    monkeypatch.setattr(orders_module.AsyncTradingClient, "get", lambda: mock_client)

    await orders_module.on_cancel_order(
        "__ng_fallback_broker-1",
        "AAPL",
        "user-1",
        "admin",
        broker_order_id="broker-1",
    )

    mock_client.cancel_order.assert_awaited_once_with("broker-1", "user-1", role="admin")
