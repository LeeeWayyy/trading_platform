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
        # Simulate gridReady event being fired immediately (as happens in real browser)
        # This is needed because the component sets _ready_event and waits for it
        return grid

    def notify(message: str, **kwargs):
        notify_calls.append((message, kwargs))

    # Mock asyncio.Event to be pre-set (grid is immediately ready in tests)
    class PreSetEvent:
        def is_set(self) -> bool:
            return True

        def set(self) -> None:
            pass

    monkeypatch.setattr(orders_module.asyncio, "Event", PreSetEvent)

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
        "stop_price",
        "status",
        "created_at",
        "actions",
    ]

    status_col = column_defs[6]
    assert status_col[":cellRenderer"] == "window.statusBadgeRenderer"

    actions_col = column_defs[-1]
    assert actions_col[":cellRenderer"] == "window.orderActionsRenderer"

    limit_col = column_defs[4]
    assert ":valueFormatter" in limit_col

    created_col = column_defs[7]
    assert "UTC" in created_col[":valueFormatter"]

    assert grid.options[":getRowId"] == "params => params.data.client_order_id"
    # P6T1: onGridReady now registers with GridThrottle for per-grid degradation tracking
    assert "window._ordersGridApi = params.api" in grid.options[":onGridReady"]
    assert "GridThrottle" in grid.options[":onGridReady"]


@pytest.mark.asyncio()
async def test_update_orders_table_add_update_remove(dummy_ui) -> None:
    grid = orders_module.create_orders_table()

    first_orders = [
        {"client_order_id": "id-1", "symbol": "AAPL", "status": "new"},
        {"client_order_id": "id-2", "symbol": "MSFT", "status": "new"},
    ]

    current_ids = await orders_module.update_orders_table(grid, first_orders)
    assert current_ids == {"id-1", "id-2"}
    assert grid.calls[-1][0] == "setRowData"

    next_orders = [
        {"client_order_id": "id-1", "symbol": "AAPL", "status": "filled"},
        {"client_order_id": "id-3", "symbol": "GOOG", "status": "new"},
    ]

    current_ids = await orders_module.update_orders_table(grid, next_orders, current_ids)
    assert current_ids == {"id-1", "id-3"}

    method, payload = grid.calls[-1]
    # P6T1: Changed to applyTransactionAsync for batched updates
    assert method == "applyTransactionAsync"
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
    assert method == "setRowData"
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
async def test_update_orders_table_same_batch_duplicates_get_unique_ids(dummy_ui) -> None:
    """Same-batch duplicate orders with identical fingerprints get unique IDs."""
    grid = orders_module.create_orders_table()
    synthetic_id_map: dict[str, str] = {}

    # Two orders with identical fingerprints in same batch
    orders = [
        {
            "symbol": "AAPL",
            "side": "buy",
            "created_at": "2025-01-01T00:00:00Z",
            "qty": 1,
            "type": "market",
            "status": "new",
        },
        {
            "symbol": "AAPL",
            "side": "buy",
            "created_at": "2025-01-01T00:00:00Z",
            "qty": 1,
            "type": "market",
            "status": "new",
        },
    ]

    current_ids = await orders_module.update_orders_table(
        grid,
        orders,
        synthetic_id_map=synthetic_id_map,
    )

    # Both orders should have unique IDs (no collision)
    assert len(current_ids) == 2
    ids_list = list(current_ids)
    assert ids_list[0] != ids_list[1]
    assert all(id_.startswith("unknown_") for id_ in current_ids)

    # Verify grid received both orders
    method, payload = grid.calls[-1]
    assert method == "setRowData"
    assert len(payload) == 2
    row_ids = [row["client_order_id"] for row in payload]
    assert len(set(row_ids)) == 2  # All unique


@pytest.mark.asyncio()
async def test_update_orders_table_cleans_up_stale_synthetic_ids(dummy_ui) -> None:
    """Synthetic ID map is cleaned up after 3 consecutive misses (prevents churn)."""
    grid = orders_module.create_orders_table()
    synthetic_id_map: dict[str, str] = {}
    synthetic_id_miss_counts: dict[str, int] = {}

    # First batch: order without ID
    first_orders = [
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
        first_orders,
        synthetic_id_map=synthetic_id_map,
        synthetic_id_miss_counts=synthetic_id_miss_counts,
    )
    assert len(synthetic_id_map) == 1  # One fingerprint mapped
    first_synthetic_id = next(iter(current_ids))

    # Second batch: order is gone (miss 1)
    second_orders: list[dict] = []

    current_ids = await orders_module.update_orders_table(
        grid,
        second_orders,
        previous_order_ids=current_ids,
        synthetic_id_map=synthetic_id_map,
        synthetic_id_miss_counts=synthetic_id_miss_counts,
    )
    assert len(synthetic_id_map) == 1, "Should not be cleaned up after 1 miss"

    # Third batch: still gone (miss 2)
    current_ids = await orders_module.update_orders_table(
        grid,
        second_orders,
        previous_order_ids=current_ids,
        synthetic_id_map=synthetic_id_map,
        synthetic_id_miss_counts=synthetic_id_miss_counts,
    )
    assert len(synthetic_id_map) == 1, "Should not be cleaned up after 2 misses"

    # Fourth batch: still gone (miss 3 - now cleanup)
    current_ids = await orders_module.update_orders_table(
        grid,
        second_orders,
        previous_order_ids=current_ids,
        synthetic_id_map=synthetic_id_map,
        synthetic_id_miss_counts=synthetic_id_miss_counts,
    )

    # synthetic_id_map should be cleaned up after 3 misses
    assert len(synthetic_id_map) == 0, "Stale synthetic ID should be removed after 3 misses"
    assert first_synthetic_id not in current_ids


@pytest.mark.asyncio()
async def test_update_orders_table_resets_miss_count_on_reappear(dummy_ui) -> None:
    """Miss count is reset when order reappears (handles transient gaps)."""
    grid = orders_module.create_orders_table()
    synthetic_id_map: dict[str, str] = {}
    synthetic_id_miss_counts: dict[str, int] = {}

    # First batch: order without ID
    first_orders = [
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
        first_orders,
        synthetic_id_map=synthetic_id_map,
        synthetic_id_miss_counts=synthetic_id_miss_counts,
    )
    fingerprint = next(iter(synthetic_id_map.keys()))

    # Second batch: order disappears (miss 1)
    current_ids = await orders_module.update_orders_table(
        grid,
        [],
        previous_order_ids=current_ids,
        synthetic_id_map=synthetic_id_map,
        synthetic_id_miss_counts=synthetic_id_miss_counts,
    )
    assert synthetic_id_miss_counts.get(fingerprint) == 1

    # Third batch: order reappears - miss count should reset
    current_ids = await orders_module.update_orders_table(
        grid,
        first_orders,
        previous_order_ids=current_ids,
        synthetic_id_map=synthetic_id_map,
        synthetic_id_miss_counts=synthetic_id_miss_counts,
    )
    assert fingerprint not in synthetic_id_miss_counts, "Miss count should be reset on reappear"
    assert len(synthetic_id_map) == 1, "Synthetic ID should be preserved"


@pytest.mark.asyncio()
async def test_update_orders_table_reuses_existing_id_when_duplicate_fills(dummy_ui) -> None:
    """When one of multiple duplicate orders fills, remaining order reuses existing ID.

    Since orders with identical fingerprints are indistinguishable, we can't track
    which specific order got which ID. The important property is that the remaining
    order reuses an ID from the previous snapshot (not a new one) for row stability.
    """
    grid = orders_module.create_orders_table()
    synthetic_id_map: dict[str, str] = {}
    synthetic_id_miss_counts: dict[str, int] = {}

    # First batch: two identical orders (same fingerprint)
    duplicate_orders = [
        {
            "symbol": "AAPL",
            "side": "buy",
            "created_at": "2025-01-01T00:00:00Z",
            "qty": 1,
            "type": "market",
            "status": "new",
        },
        {
            "symbol": "AAPL",
            "side": "buy",
            "created_at": "2025-01-01T00:00:00Z",
            "qty": 1,
            "type": "market",
            "status": "new",
        },
    ]

    current_ids = await orders_module.update_orders_table(
        grid,
        duplicate_orders,
        synthetic_id_map=synthetic_id_map,
        synthetic_id_miss_counts=synthetic_id_miss_counts,
    )
    assert len(current_ids) == 2
    previous_ids = current_ids.copy()

    # Second batch: one order fills (removed from snapshot), one remains
    remaining_order = [duplicate_orders[0].copy()]

    current_ids = await orders_module.update_orders_table(
        grid,
        remaining_order,
        previous_order_ids=current_ids,
        synthetic_id_map=synthetic_id_map,
        synthetic_id_miss_counts=synthetic_id_miss_counts,
    )

    # Remaining order should reuse an ID from previous snapshot (row stability)
    assert len(current_ids) == 1
    remaining_id = next(iter(current_ids))
    assert remaining_id in previous_ids, "Should reuse existing ID, not create new one"


@pytest.mark.asyncio()
async def test_on_cancel_order_blocks_missing_id(dummy_ui, monkeypatch: pytest.MonkeyPatch) -> None:
    mock_client = AsyncMock()
    monkeypatch.setattr(orders_module.AsyncTradingClient, "get", lambda: mock_client)

    await orders_module.on_cancel_order("", "AAPL", "user-1", "admin")

    assert mock_client.cancel_order.await_count == 0
    assert any("missing client order ID" in message for message, _ in dummy_ui)


@pytest.mark.asyncio()
async def test_on_cancel_order_blocks_viewer(dummy_ui, monkeypatch: pytest.MonkeyPatch) -> None:
    mock_client = AsyncMock()
    monkeypatch.setattr(orders_module.AsyncTradingClient, "get", lambda: mock_client)

    await orders_module.on_cancel_order("order-1", "AAPL", "user-1", "viewer")

    assert mock_client.cancel_order.await_count == 0
    assert any("Viewers cannot cancel orders" in message for message, _ in dummy_ui)


@pytest.mark.asyncio()
async def test_on_cancel_order_blocks_fallback_id(
    dummy_ui, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fallback IDs are blocked - backend needs real client_order_id."""
    mock_client = AsyncMock()
    monkeypatch.setattr(orders_module.AsyncTradingClient, "get", lambda: mock_client)

    await orders_module.on_cancel_order(
        "__ng_fallback_broker-1",
        "AAPL",
        "user-1",
        "admin",
        broker_order_id="broker-1",
    )

    assert mock_client.cancel_order.await_count == 0
    assert any("no client ID" in message for message, _ in dummy_ui)


@pytest.mark.asyncio()
async def test_on_cancel_order_blocks_synthetic_unknown_id(
    dummy_ui, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Synthetic unknown_* IDs are blocked - backend needs real client_order_id."""
    mock_client = AsyncMock()
    monkeypatch.setattr(orders_module.AsyncTradingClient, "get", lambda: mock_client)

    await orders_module.on_cancel_order(
        "unknown_abc123def456",
        "AAPL",
        "user-1",
        "admin",
    )

    assert mock_client.cancel_order.await_count == 0
    assert any("no client ID" in message for message, _ in dummy_ui)


@pytest.mark.asyncio()
async def test_on_cancel_order_blocks_empty_order_id(
    dummy_ui, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Empty order_id is blocked even if broker_order_id present."""
    mock_client = AsyncMock()
    monkeypatch.setattr(orders_module.AsyncTradingClient, "get", lambda: mock_client)

    await orders_module.on_cancel_order(
        "",  # Empty order_id
        "AAPL",
        "user-1",
        "admin",
        broker_order_id="broker-123",
    )

    # Cancel is NOT called - backend needs client_order_id
    assert mock_client.cancel_order.await_count == 0
    assert any("missing client order ID" in message for message, _ in dummy_ui)


@pytest.mark.asyncio()
async def test_on_cancel_order_blocks_synthetic_id_with_broker_id(
    dummy_ui, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Synthetic IDs are blocked even if broker_order_id present - backend needs client_order_id."""
    mock_client = AsyncMock()
    monkeypatch.setattr(orders_module.AsyncTradingClient, "get", lambda: mock_client)

    await orders_module.on_cancel_order(
        "unknown_abc123def456",  # Synthetic ID
        "AAPL",
        "user-1",
        "admin",
        broker_order_id="broker-456",
    )

    # Cancel is NOT called - backend needs client_order_id
    assert mock_client.cancel_order.await_count == 0
    assert any("no client ID" in message for message, _ in dummy_ui)


@pytest.mark.asyncio()
async def test_on_cancel_order_succeeds_with_valid_client_order_id(
    dummy_ui, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Valid client_order_id is used for cancel."""
    mock_client = AsyncMock()
    monkeypatch.setattr(orders_module.AsyncTradingClient, "get", lambda: mock_client)

    await orders_module.on_cancel_order(
        "valid-client-order-id",
        "AAPL",
        "user-1",
        "admin",
    )

    mock_client.cancel_order.assert_awaited_once_with(
        "valid-client-order-id", "user-1", role="admin"
    )
    assert any("Cancel requested" in message for message, _ in dummy_ui)


@pytest.mark.asyncio()
async def test_update_orders_table_missing_all_ids_notifies_with_client_id_context(
    dummy_ui,
) -> None:
    """Notification includes client_id suffix when provided."""
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

    await orders_module.update_orders_table(
        grid,
        orders,
        notified_missing_ids=notified_missing_ids,
        synthetic_id_map=synthetic_id_map,
        client_id="test-client-123456789",
    )

    notify_calls = dummy_ui
    assert len(notify_calls) == 1
    message = notify_calls[0][0]
    assert "456789" in message  # Last 6 chars of client_id


@pytest.mark.asyncio()
async def test_update_orders_table_cleans_up_without_miss_tracking(dummy_ui) -> None:
    """Synthetic ID map cleanup works without miss_counts (legacy behavior)."""
    grid = orders_module.create_orders_table()
    synthetic_id_map: dict[str, str] = {}

    # First batch: order without ID
    first_orders = [
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
        first_orders,
        synthetic_id_map=synthetic_id_map,
        synthetic_id_miss_counts=None,  # No miss tracking - legacy mode
    )
    assert len(synthetic_id_map) == 1

    # Second batch: order is gone - should delete immediately (no miss tracking)
    current_ids = await orders_module.update_orders_table(
        grid,
        [],
        previous_order_ids=current_ids,
        synthetic_id_map=synthetic_id_map,
        synthetic_id_miss_counts=None,  # No miss tracking
    )

    # Should be cleaned up immediately (no 3-miss delay)
    assert len(synthetic_id_map) == 0
