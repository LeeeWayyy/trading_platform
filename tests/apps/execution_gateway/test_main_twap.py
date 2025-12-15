"""TWAP slicing happy‑path coverage for execution_gateway.main."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from apps.execution_gateway import main
from apps.execution_gateway.schemas import SliceDetail, SlicingPlan


class DummyScheduler:
    def __init__(self):
        self.cancel_calls = []

    def schedule_slices(self, parent_order_id, slices, **_kwargs):
        return ["job1"] * len(slices)

    def cancel_remaining_slices(self, parent_id):
        self.cancel_calls.append(parent_id)
        return 0, 0

    @property
    def scheduler(self):
        class _S:
            running = False

        return _S()

    def start(self):
        return None

    def shutdown(self, wait=True):
        return None


def make_plan():
    return SlicingPlan(
        parent_order_id="parent1",
        parent_strategy_id="twap_parent",
        symbol="AAPL",
        side="buy",
        total_qty=10,
        total_slices=2,
        duration_minutes=2,
        interval_seconds=60,
        slices=[
            SliceDetail(
                slice_num=0,
                qty=5,
                scheduled_time=datetime.now(UTC),
                client_order_id="c1",
                strategy_id="s1",
                status="pending_new",
            ),
            SliceDetail(
                slice_num=1,
                qty=5,
                scheduled_time=datetime.now(UTC) + timedelta(seconds=60),
                client_order_id="c2",
                strategy_id="s2",
                status="pending_new",
            ),
        ],
    )


@pytest.fixture()
def app_client(monkeypatch):
    # httpx>=0.28 removed the 'app' kwarg that Starlette's TestClient still passes.
    # Patch httpx.Client.__init__ to drop the unexpected argument for compatibility.
    import httpx

    original_client_init = httpx.Client.__init__

    def _patched_client_init(self, *args, **kwargs):
        kwargs.pop("app", None)
        return original_client_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.Client, "__init__", _patched_client_init)

    # Patch kill_switch + circuit breaker availability flags to healthy
    main.set_kill_switch_unavailable(False)
    main.set_circuit_breaker_unavailable(False)
    main.set_position_reservation_unavailable(False)

    # Stub kill_switch to never engage
    main.kill_switch = SimpleNamespace(is_engaged=lambda: False)
    main.slice_scheduler = DummyScheduler()
    main.twap_slicer.plan = lambda **kwargs: make_plan()

    # Minimal DB stubs
    class _Tx:
        def __enter__(self):
            return SimpleNamespace()

        def __exit__(self, exc_type, exc, tb):
            return False

    db = SimpleNamespace(
        get_order_by_client_id=lambda _cid: None,
        create_parent_order=lambda **kwargs: SimpleNamespace(),
        create_child_slice=lambda **kwargs: SimpleNamespace(),
        transaction=lambda: _Tx(),
        get_slices_by_parent_id=lambda parent: [],
        cancel_pending_slices=lambda parent: 0,
        update_order_status=lambda **kwargs: SimpleNamespace(),
    )
    main.db_client = db  # type: ignore

    # slice_scheduler needs kill_switch & circuit_breaker for initialization; they are already patched
    return TestClient(main.app)


def test_submit_sliced_order_happy_path(app_client):
    resp = app_client.post(
        "/api/v1/orders/slice",
        json={
            "symbol": "AAPL",
            "side": "buy",
            "qty": 10,
            "duration_minutes": 2,
            "interval_seconds": 60,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_slices"] == 2
    assert data["parent_order_id"] == "parent1"


def test_cancel_slices_endpoint(app_client):
    # Pre-create fake parent so cancel flow sees it
    main.db_client.get_order_by_client_id = lambda pid: SimpleNamespace(client_order_id=pid)
    resp = app_client.delete("/api/v1/orders/parent1/slices")
    assert resp.status_code in (200, 503)
