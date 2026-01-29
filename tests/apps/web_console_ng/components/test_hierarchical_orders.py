from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock

import httpx
import pytest

from apps.web_console_ng.components import hierarchical_orders as module
from apps.web_console_ng.core.workspace_persistence import DatabaseUnavailableError


def test_transform_to_hierarchy_builds_tree() -> None:
    orders = [
        {
            "client_order_id": "parent-1",
            "symbol": "AAPL",
            "qty": 1000,
            "filled_qty": 0,
            "status": "new",
        },
        {
            "client_order_id": "child-1",
            "parent_order_id": "parent-1",
            "slice_num": 0,
            "symbol": "AAPL",
            "qty": 400,
            "filled_qty": 400,
            "status": "filled",
        },
        {
            "client_order_id": "child-2",
            "parent_order_id": "parent-1",
            "slice_num": 1,
            "symbol": "AAPL",
            "qty": 600,
            "filled_qty": 0,
            "status": "pending_new",
        },
    ]

    rows = module.transform_to_hierarchy(orders)
    assert [row["client_order_id"] for row in rows] == [
        "parent-1",
        "child-1",
        "child-2",
    ]

    parent = rows[0]
    assert parent["hierarchy_path"] == ["parent-1"]
    assert parent["is_parent"] is True
    assert parent["child_count"] == 2
    assert parent["progress"] == "400/1000 filled"

    child = rows[1]
    assert child["hierarchy_path"] == ["parent-1", "child-1"]
    assert child["is_child"] is True


def test_transform_to_hierarchy_orphan_child_flattened() -> None:
    orders = [
        {
            "client_order_id": "child-1",
            "parent_order_id": "missing-parent",
            "slice_num": 0,
            "symbol": "MSFT",
            "qty": 10,
            "filled_qty": 0,
            "status": "pending_new",
        }
    ]

    rows = module.transform_to_hierarchy(orders)
    assert len(rows) == 1
    orphan = rows[0]
    assert orphan["hierarchy_path"] == ["child-1"]
    assert orphan["is_orphan"] is True


def test_compute_parent_aggregates_uses_children_when_parent_qty_zero() -> None:
    parent = {
        "client_order_id": "parent-2",
        "symbol": "TSLA",
        "qty": 0,
        "filled_qty": 0,
    }
    children = [
        {"client_order_id": "child-a", "qty": "2", "filled_qty": "1"},
        {"client_order_id": "child-b", "qty": Decimal("3"), "filled_qty": 0},
    ]

    module.compute_parent_aggregates(parent, children)
    assert parent["total_qty_agg"] == Decimal("5")
    assert parent["filled_qty_agg"] == Decimal("1")
    assert parent["progress"] == "1/5 filled"


def test_transform_to_hierarchy_sorts_children_by_slice_num() -> None:
    orders = [
        {"client_order_id": "parent-3", "symbol": "NVDA", "qty": 10},
        {
            "client_order_id": "child-2",
            "parent_order_id": "parent-3",
            "slice_num": 2,
            "symbol": "NVDA",
            "qty": 1,
        },
        {
            "client_order_id": "child-1",
            "parent_order_id": "parent-3",
            "slice_num": 1,
            "symbol": "NVDA",
            "qty": 1,
        },
    ]

    rows = module.transform_to_hierarchy(orders)
    assert [row["client_order_id"] for row in rows] == [
        "parent-3",
        "child-1",
        "child-2",
    ]


def test_coerce_number_and_format_qty() -> None:
    assert module._coerce_number(Decimal("1.5")) == Decimal("1.5")
    assert module._coerce_number(3) == Decimal("3")
    assert module._coerce_number(2.2) == Decimal("2.2")
    assert module._coerce_number("bad", default=Decimal("9")) == Decimal("9")
    assert module._coerce_number(None) == Decimal("0")

    assert module._format_qty(Decimal("3.0")) == "3"
    assert module._format_qty(Decimal("3.50")) == "3.5"


def test_transform_to_hierarchy_includes_all_orders_parent() -> None:
    orders = [{"client_order_id": "child-1", "parent_order_id": "parent-1"}]
    all_orders = [{"client_order_id": "parent-1", "qty": 10}]
    rows = module.transform_to_hierarchy(orders, all_orders=all_orders)
    assert rows[0]["client_order_id"] == "parent-1"
    assert rows[1]["is_child"] is True


def test_filter_cancelable_children() -> None:
    children = [
        {"client_order_id": "ok-1", "status": "new"},
        {"client_order_id": "ok-2", "status": "pending_new"},
        {"client_order_id": "blocked", "status": "filled"},
        {"client_order_id": f"{module.SYNTHETIC_ID_PREFIX}abc", "status": "new"},
        {"client_order_id": f"{module.FALLBACK_ID_PREFIX}xyz", "status": "new"},
    ]
    cancelable = module._filter_cancelable_children(children)
    assert [child["client_order_id"] for child in cancelable] == ["ok-1", "ok-2"]


def test_render_children_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    labels: list[str] = []

    class DummyLabel:
        def __init__(self, text: str) -> None:
            labels.append(text)

        def classes(self, *_args, **_kwargs):
            return self

    monkeypatch.setattr(module, "ui", type("ui", (), {"label": DummyLabel}))

    module._render_children_summary(
        [
            {"slice_num": 1, "status": "new", "qty": 10},
            {"status": "filled", "qty": 5},
        ]
    )
    assert "Slice 1: 10 shares (new)" in labels[0]
    assert "Slice: 5 shares (filled)" in labels[1]


@pytest.mark.asyncio()
async def test_hierarchical_state_load_save_db_unavailable() -> None:
    state = module.HierarchicalOrdersState(user_id="user-1")
    service = AsyncMock()
    service.load_panel_state.side_effect = DatabaseUnavailableError("down")
    service.save_panel_state.side_effect = DatabaseUnavailableError("down")

    await state.load(service=service)
    await state.save(service=service)


class DummyDialog:
    def __init__(self) -> None:
        self.opened = False
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None

    def open(self) -> None:
        self.opened = True

    def close(self) -> None:
        self.closed = True


class DummyElement:
    def classes(self, *_args, **_kwargs):
        return self

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None


class DummyButton:
    def __init__(self, label: str, on_click) -> None:
        self.label = label
        self.on_click = on_click
        self.disabled = False

    def classes(self, *_args, **_kwargs):
        return self

    def disable(self) -> None:
        self.disabled = True


@pytest.mark.asyncio()
async def test_on_cancel_parent_order_viewer(monkeypatch: pytest.MonkeyPatch) -> None:
    notifications: list[tuple[str, str]] = []

    def notify(msg: str, type: str = "info") -> None:
        notifications.append((msg, type))

    dummy = type(
        "ui",
        (),
        {
            "notify": notify,
            "dialog": lambda: DummyDialog(),
            "card": lambda: DummyElement(),
            "label": lambda *_args, **_kwargs: DummyElement(),
            "row": lambda: DummyElement(),
            "button": lambda *_args, **_kwargs: DummyButton("noop", lambda: None),
        },
    )
    monkeypatch.setattr(module, "ui", dummy)

    await module.on_cancel_parent_order("parent-1", "AAPL", [], "user1", "viewer")
    assert notifications == [("Viewers cannot cancel orders", "warning")]


@pytest.mark.asyncio()
async def test_on_cancel_parent_order_no_cancelable(monkeypatch: pytest.MonkeyPatch) -> None:
    notifications: list[tuple[str, str]] = []

    def notify(msg: str, type: str = "info") -> None:
        notifications.append((msg, type))

    dummy = type(
        "ui",
        (),
        {
            "notify": notify,
            "dialog": lambda: DummyDialog(),
            "card": lambda: DummyElement(),
            "label": lambda *_args, **_kwargs: DummyElement(),
            "row": lambda: DummyElement(),
            "button": lambda *_args, **_kwargs: DummyButton("noop", lambda: None),
        },
    )
    monkeypatch.setattr(module, "ui", dummy)

    children = [{"client_order_id": "x", "status": "filled"}]
    await module.on_cancel_parent_order("parent-1", "AAPL", children, "user1", "trader")
    assert notifications == [("No pending child slices to cancel", "warning")]


@pytest.mark.asyncio()
async def test_on_cancel_parent_order_success(monkeypatch: pytest.MonkeyPatch) -> None:
    notifications: list[tuple[str, str]] = []
    buttons: list[DummyButton] = []

    def notify(msg: str, type: str = "info") -> None:
        notifications.append((msg, type))

    def button(label: str, on_click=None):
        btn = DummyButton(label, on_click)
        buttons.append(btn)
        return btn

    dummy = type(
        "ui",
        (),
        {
            "notify": notify,
            "dialog": lambda: DummyDialog(),
            "card": lambda: DummyElement(),
            "label": lambda *_args, **_kwargs: DummyElement(),
            "row": lambda: DummyElement(),
            "button": button,
        },
    )
    monkeypatch.setattr(module, "ui", dummy)

    client = AsyncMock()
    monkeypatch.setattr(module.AsyncTradingClient, "get", staticmethod(lambda: client))

    children = [
        {"client_order_id": "child-1", "status": "pending_new"},
        {"client_order_id": "child-2", "status": "new"},
    ]
    await module.on_cancel_parent_order("parent-1", "AAPL", children, "user1", "trader")

    confirm = next(btn for btn in buttons if btn.label == "Confirm")
    await confirm.on_click()
    assert confirm.disabled is True
    assert notifications[-1][1] == "positive"


@pytest.mark.asyncio()
async def test_on_cancel_parent_order_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    notifications: list[tuple[str, str]] = []
    buttons: list[DummyButton] = []

    def notify(msg: str, type: str = "info") -> None:
        notifications.append((msg, type))

    def button(label: str, on_click=None):
        btn = DummyButton(label, on_click)
        buttons.append(btn)
        return btn

    dummy = type(
        "ui",
        (),
        {
            "notify": notify,
            "dialog": lambda: DummyDialog(),
            "card": lambda: DummyElement(),
            "label": lambda *_args, **_kwargs: DummyElement(),
            "row": lambda: DummyElement(),
            "button": button,
        },
    )
    monkeypatch.setattr(module, "ui", dummy)

    client = AsyncMock()
    client.cancel_order.side_effect = [
        httpx.HTTPStatusError("bad", request=None, response=httpx.Response(400)),
        httpx.RequestError("boom", request=None),
    ]
    monkeypatch.setattr(module.AsyncTradingClient, "get", staticmethod(lambda: client))

    children = [
        {"client_order_id": "child-1", "status": "pending_new"},
        {"client_order_id": "child-2", "status": "new"},
    ]
    await module.on_cancel_parent_order("parent-1", "AAPL", children, "user1", "trader")

    confirm = next(btn for btn in buttons if btn.label == "Confirm")
    await confirm.on_click()
    assert notifications[-1][1] == "negative"
