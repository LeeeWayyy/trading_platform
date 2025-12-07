"""Unit tests for :mod:`libs.data_providers.taq_storage`.

These tests focus on private helpers that guard disk usage, partition
construction, atomic writes, and cleanup behaviours. External systems (WRDS)
are mocked so tests remain fast and deterministic.
"""

from __future__ import annotations

import datetime
import shutil
from pathlib import Path
from unittest.mock import MagicMock

import polars as pl
import pytest

from libs.data_providers import taq_storage
from libs.data_providers.taq_storage import (
    TAQ_SCHEMAS,
    TAQStorageManager,
    register_taq_schemas,
)
from libs.data_quality.exceptions import DiskSpaceError
from libs.data_quality.manifest import ManifestManager
from libs.data_quality.schema import SchemaRegistry
from libs.data_quality.validation import DataValidator
from libs.data_quality.versioning import DatasetVersionManager


@pytest.fixture()
def storage_manager(tmp_path: Path) -> TAQStorageManager:
    """Create a TAQStorageManager wired to isolated temp directories."""

    data_root = tmp_path / "data"
    storage_path = data_root / "taq"
    lock_dir = data_root / "locks"

    manifest_manager = ManifestManager(
        storage_path=data_root / "manifests",
        lock_dir=lock_dir,
        data_root=data_root,
    )
    version_manager = DatasetVersionManager(
        manifest_manager=manifest_manager,
        validator=None,
        snapshots_dir=data_root / "snapshots",
        cas_dir=data_root / "cas",
        diffs_dir=data_root / "diffs",
        locks_dir=lock_dir,
        data_root=data_root,
    )
    schema_registry = SchemaRegistry(
        storage_path=data_root / "schemas",
        lock_dir=lock_dir / "schema",
    )

    manager = TAQStorageManager(
        wrds_client=MagicMock(),
        storage_path=storage_path,
        lock_dir=lock_dir,
        manifest_manager=manifest_manager,
        version_manager=version_manager,
        validator=DataValidator(),
        schema_registry=schema_registry,
    )

    return manager


def test_register_taq_schemas_creates_expected_entries(storage_manager: TAQStorageManager) -> None:
    """Schemas are registered once and persisted with correct columns."""

    registry = storage_manager.schema_registry
    register_taq_schemas(registry)

    for dataset, expected_schema in TAQ_SCHEMAS.items():
        schema = registry.get_expected_schema(dataset)
        assert schema is not None, f"Schema not registered for {dataset}"
        assert schema.columns == expected_schema


def test_check_disk_space_levels(
    monkeypatch: pytest.MonkeyPatch, storage_manager: TAQStorageManager
) -> None:
    """Disk space helper returns severity levels and raises when blocked."""

    usage_factory = shutil._ntuple_diskusage  # type: ignore[attr-defined]

    # OK state
    monkeypatch.setattr(
        taq_storage.shutil,
        "disk_usage",
        lambda path: usage_factory(1_000_000_000, 400_000_000, 600_000_000),
    )
    status = storage_manager._check_disk_space(estimated_rows=1_000)
    assert status.level == "ok"

    # Warning threshold
    monkeypatch.setattr(
        taq_storage.shutil,
        "disk_usage",
        lambda path: usage_factory(1_000_000_000, 880_000_000, 120_000_000),
    )
    status = storage_manager._check_disk_space(estimated_rows=100)
    assert status.level == "warning"

    # Blocked by usage percent
    monkeypatch.setattr(
        taq_storage.shutil,
        "disk_usage",
        lambda path: usage_factory(1_000_000_000, 960_000_000, 40_000_000),
    )
    with pytest.raises(DiskSpaceError):
        storage_manager._check_disk_space(estimated_rows=10)

    # Blocked by insufficient free bytes even when usage is low
    monkeypatch.setattr(
        taq_storage.shutil,
        "disk_usage",
        lambda path: usage_factory(1_000_000, 100_000, 100_000),
    )
    with pytest.raises(DiskSpaceError):
        storage_manager._check_disk_space(estimated_rows=10_000)


def test_build_month_partitions_cross_year(storage_manager: TAQStorageManager) -> None:
    """Month partition builder includes both endpoints across year boundaries."""

    start = datetime.date(2024, 11, 15)
    end = datetime.date(2025, 2, 2)

    partitions = storage_manager._build_month_partitions(start, end)

    assert partitions == ["202411", "202412", "202501", "202502"]


def test_atomic_write_parquet_creates_file_and_checksum(storage_manager: TAQStorageManager) -> None:
    """Atomic parquet write persists data, cleans temp file, and returns checksum."""

    df = pl.DataFrame(
        {
            "ts": [datetime.datetime(2024, 1, 2, 9, 30)],
            "symbol": ["AAPL"],
            "open": [100.0],
            "high": [101.0],
            "low": [99.5],
            "close": [100.5],
            "volume": [1_000],
            "vwap": [100.2],
            "date": [datetime.date(2024, 1, 2)],
        }
    )

    target_path = storage_manager.storage_path / "aggregates" / "1min_bars" / "202401.parquet"

    checksum = storage_manager._atomic_write_parquet(df, target_path)

    assert target_path.exists()
    assert not (
        storage_manager.storage_path / storage_manager.TMP_DIR / "202401.parquet.tmp"
    ).exists()

    expected_checksum = storage_manager._compute_checksum_and_fsync(target_path)
    assert checksum == expected_checksum


def test_cleanup_removes_old_samples_and_quarantine(
    storage_manager: TAQStorageManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cleanup removes items older than retention threshold across tiers."""

    # Freeze "today" for deterministic cutoff calculations
    fixed_today = datetime.date(2025, 1, 31)

    class _FixedDate(datetime.date):
        @classmethod
        def today(cls) -> datetime.date:  # pragma: no cover - exercised indirectly
            return fixed_today

    monkeypatch.setattr(taq_storage.datetime, "date", _FixedDate)

    samples_dir = storage_manager.storage_path / storage_manager.SAMPLES_DIR
    old_sample = samples_dir / "2025-01-10"
    recent_sample = samples_dir / "2025-01-25"
    old_sample.mkdir(parents=True, exist_ok=True)
    recent_sample.mkdir(parents=True, exist_ok=True)

    quarantine_dir = storage_manager.storage_path / storage_manager.QUARANTINE_DIR
    old_quarantine = quarantine_dir / "20250101_010101_old"
    recent_quarantine = quarantine_dir / "20250130_010101_new"
    old_quarantine.mkdir(parents=True, exist_ok=True)
    recent_quarantine.mkdir(parents=True, exist_ok=True)

    deleted = storage_manager.cleanup(retention_days=10)

    assert deleted == 2
    assert not old_sample.exists()
    assert not old_quarantine.exists()
    assert recent_sample.exists()
    assert recent_quarantine.exists()
