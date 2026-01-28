from __future__ import annotations

from decimal import Decimal

from apps.web_console_ng.components import hierarchical_orders as module


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
