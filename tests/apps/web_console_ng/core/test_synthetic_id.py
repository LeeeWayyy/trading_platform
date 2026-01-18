"""Comprehensive unit tests for synthetic ID generation module.

Tests cover:
- normalize_num function with various input types
- compute_order_fingerprint with different order structures
- resolve_synthetic_id with various collision scenarios
- Helper functions for suffix and orphan handling
- Edge cases and error conditions
"""

from __future__ import annotations

from apps.web_console_ng.core import synthetic_id


class TestNormalizeNum:
    """Test suite for normalize_num function."""

    def test_normalize_int(self) -> None:
        """Test normalization of integer values."""
        assert synthetic_id.normalize_num(5) == "5.0"
        assert synthetic_id.normalize_num(0) == "0.0"
        assert synthetic_id.normalize_num(-10) == "-10.0"
        assert synthetic_id.normalize_num(999999) == "999999.0"

    def test_normalize_float(self) -> None:
        """Test normalization of float values."""
        assert synthetic_id.normalize_num(1.234) == repr(1.234)
        assert synthetic_id.normalize_num(0.0) == repr(0.0)
        assert synthetic_id.normalize_num(-5.678) == repr(-5.678)
        # Test high precision floats (crypto use case)
        assert synthetic_id.normalize_num(0.123456789012345) == repr(0.123456789012345)

    def test_normalize_none(self) -> None:
        """Test normalization of None value."""
        assert synthetic_id.normalize_num(None) == ""

    def test_normalize_string(self) -> None:
        """Test normalization of string values."""
        assert synthetic_id.normalize_num("abc") == "abc"
        assert synthetic_id.normalize_num("") == ""
        assert synthetic_id.normalize_num("123") == "123"

    def test_normalize_other_types(self) -> None:
        """Test normalization of other object types."""
        assert synthetic_id.normalize_num([1, 2, 3]) == "[1, 2, 3]"
        assert synthetic_id.normalize_num({"key": "value"}) == "{'key': 'value'}"


class TestComputeOrderFingerprint:
    """Test suite for compute_order_fingerprint function."""

    def test_fingerprint_stable_with_all_fields(self) -> None:
        """Test fingerprint generation with complete order data."""
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
        assert isinstance(base_hash, str)

    def test_fingerprint_with_missing_fields(self) -> None:
        """Test fingerprint generation with missing optional fields."""
        order = {
            "symbol": "AAPL",
            "side": "buy",
        }
        fingerprint, base_hash = synthetic_id.compute_order_fingerprint(order)
        assert "AAPL|buy" in fingerprint
        assert len(base_hash) == 12

    def test_fingerprint_with_none_values(self) -> None:
        """Test fingerprint generation with explicit None values."""
        order = {
            "symbol": "AAPL",
            "side": "buy",
            "created_at": None,
            "account_id": None,
            "qty": None,
            "type": None,
            "limit_price": None,
            "time_in_force": None,
        }
        fingerprint, base_hash = synthetic_id.compute_order_fingerprint(order)
        assert fingerprint == "AAPL|buy||||||"
        assert len(base_hash) == 12

    def test_fingerprint_deterministic(self) -> None:
        """Test that same order produces same fingerprint."""
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
        fp1, hash1 = synthetic_id.compute_order_fingerprint(order)
        fp2, hash2 = synthetic_id.compute_order_fingerprint(order)
        assert fp1 == fp2
        assert hash1 == hash2

    def test_fingerprint_different_for_different_orders(self) -> None:
        """Test that different orders produce different fingerprints."""
        order1 = {"symbol": "AAPL", "side": "buy", "qty": 5}
        order2 = {"symbol": "AAPL", "side": "buy", "qty": 10}
        fp1, hash1 = synthetic_id.compute_order_fingerprint(order1)
        fp2, hash2 = synthetic_id.compute_order_fingerprint(order2)
        assert fp1 != fp2
        assert hash1 != hash2

    def test_fingerprint_with_high_precision_qty(self) -> None:
        """Test fingerprint with high precision quantity (crypto use case)."""
        order = {
            "symbol": "BTC",
            "side": "buy",
            "qty": 0.123456789012345,
        }
        fingerprint, base_hash = synthetic_id.compute_order_fingerprint(order)
        assert repr(0.123456789012345) in fingerprint
        assert len(base_hash) == 12


class TestSyntheticIdContext:
    """Test suite for SyntheticIdContext dataclass."""

    def test_context_initialization(self) -> None:
        """Test context can be initialized with all fields."""
        ctx = synthetic_id.SyntheticIdContext(
            synthetic_id_map={"fp1": "id1"},
            previous_order_ids={"id1", "id2"},
            batch_generated_ids={"id3"},
        )
        assert ctx.synthetic_id_map == {"fp1": "id1"}
        assert ctx.previous_order_ids == {"id1", "id2"}
        assert ctx.batch_generated_ids == {"id3"}

    def test_context_with_none_values(self) -> None:
        """Test context can be initialized with None values."""
        ctx = synthetic_id.SyntheticIdContext(
            synthetic_id_map=None,
            previous_order_ids=None,
            batch_generated_ids=set(),
        )
        assert ctx.synthetic_id_map is None
        assert ctx.previous_order_ids is None
        assert ctx.batch_generated_ids == set()


class TestResolveSyntheticId:
    """Test suite for resolve_synthetic_id function."""

    def test_reuses_base_id_when_present_in_previous(self) -> None:
        """Test that existing base ID is reused when in previous snapshot."""
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

    def test_prefers_suffix_from_previous_snapshot(self) -> None:
        """Test suffix ID preference when base not in previous snapshot."""
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

    def test_handles_batch_collision_with_suffix(self) -> None:
        """Test collision resolution when base ID already in batch."""
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

    def test_uses_orphan_suffix_when_base_missing(self) -> None:
        """Test orphan suffix reuse when base fingerprint not in map."""
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

    def test_generates_new_id_when_no_mapping(self) -> None:
        """Test new synthetic ID generation when no existing mapping."""
        base_hash = "abc123"
        expected_id = f"{synthetic_id.SYNTHETIC_ID_PREFIX}{base_hash}"
        ctx = synthetic_id.SyntheticIdContext(
            synthetic_id_map={},
            previous_order_ids=None,
            batch_generated_ids=set(),
        )

        resolved = synthetic_id.resolve_synthetic_id("fp", base_hash, ctx)

        assert resolved == expected_id
        assert resolved in ctx.batch_generated_ids
        assert "fp" in ctx.synthetic_id_map

    def test_generates_unique_id_on_collision_in_batch(self) -> None:
        """Test unique ID generation when collision in batch."""
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

    def test_multiple_suffix_collision_resolution(self) -> None:
        """Test resolution with multiple suffix collisions."""
        fingerprint = "fp"
        base_id = "unknown_hash"
        ctx = synthetic_id.SyntheticIdContext(
            synthetic_id_map={
                fingerprint: base_id,
                f"{fingerprint}|_suffix_1": f"{base_id}_1",
                f"{fingerprint}|_suffix_2": f"{base_id}_2",
            },
            previous_order_ids={base_id},
            batch_generated_ids={base_id, f"{base_id}_1"},
        )

        resolved = synthetic_id.resolve_synthetic_id(fingerprint, "hash", ctx)

        assert resolved == f"{base_id}_2"
        assert resolved in ctx.batch_generated_ids

    def test_creates_new_suffix_when_all_existing_in_batch(self) -> None:
        """Test new suffix creation when all existing suffixes in batch."""
        fingerprint = "fp"
        base_id = "unknown_hash"
        ctx = synthetic_id.SyntheticIdContext(
            synthetic_id_map={
                fingerprint: base_id,
                f"{fingerprint}|_suffix_1": f"{base_id}_1",
            },
            previous_order_ids={base_id},
            batch_generated_ids={base_id, f"{base_id}_1"},
        )

        resolved = synthetic_id.resolve_synthetic_id(fingerprint, "hash", ctx)

        # Should create suffix_2
        assert resolved == f"{base_id}_2"
        assert resolved in ctx.batch_generated_ids
        assert f"{fingerprint}|_suffix_2" in ctx.synthetic_id_map
        assert ctx.synthetic_id_map[f"{fingerprint}|_suffix_2"] == f"{base_id}_2"

    def test_batch_collision_without_map(self) -> None:
        """Test collision resolution when no synthetic_id_map available."""
        base_hash = "abc123"
        base_id = f"{synthetic_id.SYNTHETIC_ID_PREFIX}{base_hash}"
        ctx = synthetic_id.SyntheticIdContext(
            synthetic_id_map=None,
            previous_order_ids=None,
            batch_generated_ids={base_id},
        )

        resolved = synthetic_id.resolve_synthetic_id("fp", base_hash, ctx)

        assert resolved == f"{base_id}_1"
        assert resolved in ctx.batch_generated_ids

    def test_multiple_orphan_suffixes_first_available(self) -> None:
        """Test selection of first available orphan suffix."""
        fingerprint = "fp"
        ctx = synthetic_id.SyntheticIdContext(
            synthetic_id_map={
                f"{fingerprint}|_suffix_1": "orphan_1",
                f"{fingerprint}|_suffix_2": "orphan_2",
            },
            previous_order_ids=None,
            batch_generated_ids={"orphan_1"},
        )

        resolved = synthetic_id.resolve_synthetic_id(fingerprint, "hash", ctx)

        assert resolved == "orphan_2"
        assert resolved in ctx.batch_generated_ids

    def test_new_id_collision_with_existing_map_values(self) -> None:
        """Test new ID generation avoids collisions with existing map values."""
        base_hash = "abc123"
        base_id = f"{synthetic_id.SYNTHETIC_ID_PREFIX}{base_hash}"
        ctx = synthetic_id.SyntheticIdContext(
            synthetic_id_map={"other_fp": base_id},
            previous_order_ids=None,
            batch_generated_ids=set(),
        )

        resolved = synthetic_id.resolve_synthetic_id("new_fp", base_hash, ctx)

        # Should avoid collision with existing map value
        assert resolved == f"{base_id}_1"
        assert resolved in ctx.batch_generated_ids
        assert "new_fp" in ctx.synthetic_id_map

    def test_new_id_with_multiple_collisions(self) -> None:
        """Test new ID generation with multiple existing collisions."""
        base_hash = "abc123"
        base_id = f"{synthetic_id.SYNTHETIC_ID_PREFIX}{base_hash}"
        ctx = synthetic_id.SyntheticIdContext(
            synthetic_id_map={
                "other_fp1": base_id,
                "other_fp2": f"{base_id}_1",
                "other_fp3": f"{base_id}_2",
            },
            previous_order_ids=None,
            batch_generated_ids=set(),
        )

        resolved = synthetic_id.resolve_synthetic_id("new_fp", base_hash, ctx)

        assert resolved == f"{base_id}_3"
        assert resolved in ctx.batch_generated_ids


class TestFindSuffixInPrevious:
    """Test suite for _find_suffix_in_previous helper function."""

    def test_finds_first_suffix_in_previous(self) -> None:
        """Test finding first suffix that exists in previous snapshot."""
        fingerprint = "fp"
        suffix_id = "unknown_hash_1"
        result = synthetic_id._find_suffix_in_previous(
            fingerprint,
            {f"{fingerprint}|_suffix_1": suffix_id},
            {suffix_id},
            "default_id",
        )
        assert result == suffix_id

    def test_finds_later_suffix_when_first_not_in_previous(self) -> None:
        """Test finding later suffix when earlier ones not in previous."""
        fingerprint = "fp"
        suffix_id_2 = "unknown_hash_2"
        result = synthetic_id._find_suffix_in_previous(
            fingerprint,
            {
                f"{fingerprint}|_suffix_1": "unknown_hash_1",
                f"{fingerprint}|_suffix_2": suffix_id_2,
            },
            {suffix_id_2},
            "default_id",
        )
        assert result == suffix_id_2

    def test_returns_default_when_no_suffix_in_previous(self) -> None:
        """Test default return when no suffix in previous snapshot."""
        fingerprint = "fp"
        result = synthetic_id._find_suffix_in_previous(
            fingerprint,
            {f"{fingerprint}|_suffix_1": "unknown_hash_1"},
            {"other_id"},
            "default_id",
        )
        assert result == "default_id"

    def test_returns_default_when_no_suffixes_exist(self) -> None:
        """Test default return when no suffix entries exist."""
        result = synthetic_id._find_suffix_in_previous(
            "fp",
            {},
            {"some_id"},
            "default_id",
        )
        assert result == "default_id"


class TestResolveBatchCollision:
    """Test suite for _resolve_batch_collision helper function."""

    def test_finds_existing_unused_suffix(self) -> None:
        """Test finding existing suffix not yet used in batch."""
        fingerprint = "fp"
        suffix_id = "unknown_hash_1"
        result = synthetic_id._resolve_batch_collision(
            fingerprint,
            "unknown_hash",
            {f"{fingerprint}|_suffix_1": suffix_id},
            set(),
        )
        assert result == suffix_id

    def test_skips_suffix_already_in_batch(self) -> None:
        """Test skipping suffixes already used in batch."""
        fingerprint = "fp"
        suffix_id_2 = "unknown_hash_2"
        result = synthetic_id._resolve_batch_collision(
            fingerprint,
            "unknown_hash",
            {
                f"{fingerprint}|_suffix_1": "unknown_hash_1",
                f"{fingerprint}|_suffix_2": suffix_id_2,
            },
            {"unknown_hash_1"},
        )
        assert result == suffix_id_2

    def test_creates_new_suffix_when_none_available(self) -> None:
        """Test creating new suffix when all existing in batch."""
        fingerprint = "fp"
        current_id = "unknown_hash"
        synthetic_map = {f"{fingerprint}|_suffix_1": f"{current_id}_1"}
        result = synthetic_id._resolve_batch_collision(
            fingerprint,
            current_id,
            synthetic_map,
            {f"{current_id}_1"},
        )
        assert result == f"{current_id}_2"
        assert f"{fingerprint}|_suffix_2" in synthetic_map
        assert synthetic_map[f"{fingerprint}|_suffix_2"] == f"{current_id}_2"

    def test_creates_suffix_without_map(self) -> None:
        """Test suffix creation when no map available."""
        current_id = "unknown_hash"
        result = synthetic_id._resolve_batch_collision(
            "fp",
            current_id,
            None,
            set(),
        )
        assert result == f"{current_id}_1"

    def test_increments_suffix_on_collision_without_map(self) -> None:
        """Test suffix increment on collision without map."""
        current_id = "unknown_hash"
        result = synthetic_id._resolve_batch_collision(
            "fp",
            current_id,
            None,
            {f"{current_id}_1", f"{current_id}_2"},
        )
        assert result == f"{current_id}_3"


class TestFindOrphanSuffixOrCreate:
    """Test suite for _find_orphan_suffix_or_create helper function."""

    def test_finds_orphan_suffix_not_in_batch(self) -> None:
        """Test finding orphan suffix entry not yet in batch."""
        fingerprint = "fp"
        orphan_id = "orphan_1"
        result = synthetic_id._find_orphan_suffix_or_create(
            fingerprint,
            "hash123",
            {f"{fingerprint}|_suffix_1": orphan_id},
            set(),
        )
        assert result == orphan_id

    def test_skips_orphan_already_in_batch(self) -> None:
        """Test skipping orphan suffix already in batch."""
        fingerprint = "fp"
        orphan_id_2 = "orphan_2"
        result = synthetic_id._find_orphan_suffix_or_create(
            fingerprint,
            "hash123",
            {
                f"{fingerprint}|_suffix_1": "orphan_1",
                f"{fingerprint}|_suffix_2": orphan_id_2,
            },
            {"orphan_1"},
        )
        assert result == orphan_id_2

    def test_creates_new_id_when_no_orphans(self) -> None:
        """Test creating new synthetic ID when no orphans available."""
        base_hash = "hash123"
        expected_id = f"{synthetic_id.SYNTHETIC_ID_PREFIX}{base_hash}"
        synthetic_map: dict[str, str] = {}
        result = synthetic_id._find_orphan_suffix_or_create(
            "fp",
            base_hash,
            synthetic_map,
            set(),
        )
        assert result == expected_id
        assert "fp" in synthetic_map
        assert synthetic_map["fp"] == expected_id

    def test_creates_new_id_without_map(self) -> None:
        """Test creating new synthetic ID without map."""
        base_hash = "hash123"
        expected_id = f"{synthetic_id.SYNTHETIC_ID_PREFIX}{base_hash}"
        result = synthetic_id._find_orphan_suffix_or_create(
            "fp",
            base_hash,
            None,
            set(),
        )
        assert result == expected_id

    def test_avoids_collision_with_batch(self) -> None:
        """Test new ID avoids collision with batch."""
        base_hash = "hash123"
        base_id = f"{synthetic_id.SYNTHETIC_ID_PREFIX}{base_hash}"
        result = synthetic_id._find_orphan_suffix_or_create(
            "fp",
            base_hash,
            {},
            {base_id},
        )
        assert result == f"{base_id}_1"

    def test_avoids_collision_with_map_values(self) -> None:
        """Test new ID avoids collision with existing map values."""
        base_hash = "hash123"
        base_id = f"{synthetic_id.SYNTHETIC_ID_PREFIX}{base_hash}"
        synthetic_map: dict[str, str] = {"other_fp": base_id}
        result = synthetic_id._find_orphan_suffix_or_create(
            "fp",
            base_hash,
            synthetic_map,
            set(),
        )
        assert result == f"{base_id}_1"
        assert "fp" in synthetic_map

    def test_increments_suffix_on_multiple_collisions(self) -> None:
        """Test suffix increment with multiple collisions."""
        base_hash = "hash123"
        base_id = f"{synthetic_id.SYNTHETIC_ID_PREFIX}{base_hash}"
        result = synthetic_id._find_orphan_suffix_or_create(
            "fp",
            base_hash,
            {
                "fp1": base_id,
                "fp2": f"{base_id}_1",
                "fp3": f"{base_id}_2",
            },
            set(),
        )
        assert result == f"{base_id}_3"


class TestConstants:
    """Test suite for module constants."""

    def test_synthetic_id_prefix(self) -> None:
        """Test SYNTHETIC_ID_PREFIX constant."""
        assert synthetic_id.SYNTHETIC_ID_PREFIX == "unknown_"

    def test_fallback_id_prefix(self) -> None:
        """Test FALLBACK_ID_PREFIX constant."""
        assert synthetic_id.FALLBACK_ID_PREFIX == "__ng_fallback_"


class TestModuleExports:
    """Test suite for module __all__ exports."""

    def test_all_exports(self) -> None:
        """Test that __all__ contains expected exports."""
        expected_exports = [
            "SYNTHETIC_ID_PREFIX",
            "FALLBACK_ID_PREFIX",
            "normalize_num",
            "compute_order_fingerprint",
            "SyntheticIdContext",
            "resolve_synthetic_id",
        ]
        assert synthetic_id.__all__ == expected_exports

    def test_all_exports_accessible(self) -> None:
        """Test that all exported names are accessible."""
        for name in synthetic_id.__all__:
            assert hasattr(synthetic_id, name)
