"""Unit tests for synthetic ID helpers."""

from __future__ import annotations

from apps.web_console_ng.core import synthetic_id


def test_normalize_num_int_float_none() -> None:
    assert synthetic_id.normalize_num(5) == "5.0"
    assert synthetic_id.normalize_num(1.234) == repr(1.234)
    assert synthetic_id.normalize_num(None) == ""
    assert synthetic_id.normalize_num("abc") == "abc"


def test_compute_order_fingerprint_stable_fields() -> None:
    order = {
        "symbol": "AAPL",
        "side": "buy",
        "created_at": "2025-01-01T00:00:00Z",
        "account_id": "acct-1",
        "qty": 5,
        "type": "limit",
        "limit_price": 123.45,
        "time_in_force": "day",
    }
    fingerprint, base_hash = synthetic_id.compute_order_fingerprint(order)
    assert fingerprint == "AAPL|buy|2025-01-01T00:00:00Z|acct-1|5.0|limit|123.45|day"
    assert len(base_hash) == 12


def test_resolve_synthetic_id_reuses_base_when_present() -> None:
    fingerprint = "fp"
    base_id = "unknown_hash"
    ctx = synthetic_id.SyntheticIdContext(
        synthetic_id_map={fingerprint: base_id},
        previous_order_ids={base_id},
        batch_generated_ids=set(),
    )

    resolved = synthetic_id.resolve_synthetic_id(fingerprint, "hash", ctx)

    assert resolved == base_id
    assert base_id in ctx.batch_generated_ids


def test_resolve_synthetic_id_prefers_suffix_from_previous_snapshot() -> None:
    fingerprint = "fp"
    base_id = "unknown_hash"
    suffix_id = "unknown_hash_1"
    ctx = synthetic_id.SyntheticIdContext(
        synthetic_id_map={
            fingerprint: base_id,
            f"{fingerprint}|_suffix_1": suffix_id,
        },
        previous_order_ids={suffix_id},
        batch_generated_ids=set(),
    )

    resolved = synthetic_id.resolve_synthetic_id(fingerprint, "hash", ctx)

    assert resolved == suffix_id
    assert suffix_id in ctx.batch_generated_ids


def test_resolve_synthetic_id_handles_batch_collision_with_suffix() -> None:
    fingerprint = "fp"
    base_id = "unknown_hash"
    suffix_id = "unknown_hash_1"
    ctx = synthetic_id.SyntheticIdContext(
        synthetic_id_map={
            fingerprint: base_id,
            f"{fingerprint}|_suffix_1": suffix_id,
        },
        previous_order_ids={base_id},
        batch_generated_ids={base_id},
    )

    resolved = synthetic_id.resolve_synthetic_id(fingerprint, "hash", ctx)

    assert resolved == suffix_id
    assert suffix_id in ctx.batch_generated_ids


def test_resolve_synthetic_id_uses_orphan_suffix_when_base_missing() -> None:
    fingerprint = "fp"
    orphan_id = "unknown_hash_2"
    ctx = synthetic_id.SyntheticIdContext(
        synthetic_id_map={f"{fingerprint}|_suffix_1": orphan_id},
        previous_order_ids=None,
        batch_generated_ids=set(),
    )

    resolved = synthetic_id.resolve_synthetic_id(fingerprint, "hash", ctx)

    assert resolved == orphan_id
    assert orphan_id in ctx.batch_generated_ids


def test_resolve_synthetic_id_generates_unique_id_when_collision_in_batch() -> None:
    base_hash = "abc123"
    expected_base = f"{synthetic_id.SYNTHETIC_ID_PREFIX}{base_hash}"
    ctx = synthetic_id.SyntheticIdContext(
        synthetic_id_map=None,
        previous_order_ids=None,
        batch_generated_ids={expected_base},
    )

    resolved = synthetic_id.resolve_synthetic_id("fp", base_hash, ctx)

    assert resolved == f"{expected_base}_1"
    assert resolved in ctx.batch_generated_ids
