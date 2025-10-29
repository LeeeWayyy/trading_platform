"""Tests for TWAP idempotency fallback behaviour."""

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

from apps.execution_gateway import main
from apps.execution_gateway.order_id_generator import reconstruct_order_params_hash
from apps.execution_gateway.order_slicer import TWAPSlicer
from apps.execution_gateway.schemas import SlicingRequest


class DummyDB:
    """Minimal database stub capturing lookup calls."""

    def __init__(
        self,
        parent_lookup: dict[str, object] | None = None,
        slices_lookup: dict[str, list[object]] | None = None,
    ) -> None:
        self.parent_lookup = parent_lookup or {}
        self.slices_lookup = slices_lookup or {}
        self.calls: list[str] = []

    def get_order_by_client_id(self, client_order_id: str):  # pragma: no cover - behaviour asserted in tests
        self.calls.append(client_order_id)
        return self.parent_lookup.get(client_order_id)

    def get_slices_by_parent_id(self, parent_order_id: str):  # pragma: no cover - simple data access
        return self.slices_lookup.get(parent_order_id, [])


def _build_legacy_parent(
    request: SlicingRequest, trade_date: date, total_slices: int
) -> tuple[str, object, list[object]]:
    """Create legacy parent/slice objects for fallback tests."""

    legacy_parent_id = reconstruct_order_params_hash(
        symbol=request.symbol,
        side=request.side,
        qty=request.qty,
        limit_price=request.limit_price,
        stop_price=request.stop_price,
        strategy_id=f"twap_parent_{request.duration_minutes}m",
        order_date=trade_date,
    )

    parent = SimpleNamespace(total_slices=total_slices, status="accepted")

    base_time = datetime(2025, 1, 1, tzinfo=UTC)
    slices = [
        SimpleNamespace(
            slice_num=idx,
            qty=request.qty // total_slices,
            scheduled_time=base_time + timedelta(minutes=idx),
            client_order_id=f"slice_{idx}",
            status="pending_new",
        )
        for idx in range(total_slices)
    ]

    return legacy_parent_id, parent, slices


def test_legacy_hash_used_for_default_interval(monkeypatch):
    """Legacy fallback should return stored plan when interval matches historical default."""

    trade_date = date(2025, 1, 2)
    request = SlicingRequest(
        symbol="AAPL",
        side="buy",
        qty=100,
        duration_minutes=5,
        interval_seconds=main.LEGACY_TWAP_INTERVAL_SECONDS,
        order_type="market",
        trade_date=trade_date,
    )

    slicer = TWAPSlicer()
    slicing_plan = slicer.plan(
        symbol=request.symbol,
        side=request.side,
        qty=request.qty,
        duration_minutes=request.duration_minutes,
        interval_seconds=request.interval_seconds,
        order_type=request.order_type,
        trade_date=trade_date,
    )

    new_parent_id = slicing_plan.parent_order_id
    total_slices = slicing_plan.total_slices
    legacy_parent_id, legacy_parent, legacy_slices = _build_legacy_parent(request, trade_date, total_slices)

    dummy_db = DummyDB(
        parent_lookup={legacy_parent_id: legacy_parent},
        slices_lookup={legacy_parent_id: legacy_slices},
    )
    monkeypatch.setattr(main, "db_client", dummy_db)

    existing_plan = main._find_existing_twap_plan(request, slicing_plan, trade_date)

    assert existing_plan is not None
    assert existing_plan.parent_order_id == legacy_parent_id
    assert existing_plan.interval_seconds == request.interval_seconds
    assert len(existing_plan.slices) == total_slices
    # Ensure lookup hit both the new and legacy hashes
    assert dummy_db.calls == [new_parent_id, legacy_parent_id]


def test_legacy_hash_skipped_for_custom_interval(monkeypatch):
    """Legacy fallback must not trigger when caller requests a non-default interval."""

    trade_date = date(2025, 1, 2)
    request = SlicingRequest(
        symbol="AAPL",
        side="buy",
        qty=100,
        duration_minutes=5,
        interval_seconds=70,
        order_type="market",
        trade_date=trade_date,
    )

    slicer = TWAPSlicer()
    slicing_plan = slicer.plan(
        symbol=request.symbol,
        side=request.side,
        qty=request.qty,
        duration_minutes=request.duration_minutes,
        interval_seconds=request.interval_seconds,
        order_type=request.order_type,
        trade_date=trade_date,
    )

    total_slices = slicing_plan.total_slices
    legacy_parent_id, legacy_parent, legacy_slices = _build_legacy_parent(request, trade_date, total_slices)

    dummy_db = DummyDB(
        parent_lookup={legacy_parent_id: legacy_parent},
        slices_lookup={legacy_parent_id: legacy_slices},
    )
    monkeypatch.setattr(main, "db_client", dummy_db)

    existing_plan = main._find_existing_twap_plan(request, slicing_plan, trade_date)

    assert existing_plan is None
    assert dummy_db.calls == [slicing_plan.parent_order_id]
