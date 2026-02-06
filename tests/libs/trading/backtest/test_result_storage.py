from __future__ import annotations

import importlib.util
import json
import math
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Skip if optional heavy deps missing (mirrors test_job_queue pattern)
_missing = [mod for mod in ("polars", "psycopg") if importlib.util.find_spec(mod) is None]
if _missing:
    pytest.skip(
        f"Skipping backtest result storage tests because dependencies are missing: {', '.join(_missing)}",
        allow_module_level=True,
    )

import polars as pl

from libs.trading.alpha.research_platform import BacktestResult
from libs.trading.backtest.models import BacktestJob, JobNotFound, ResultPathMissing
from libs.trading.backtest.result_storage import BacktestResultStorage


class DummyCursor:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.executed: list[tuple[str, object]] = []
        self.rowcount = len(self.rows)
        self._fetch_idx = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        # Preserve rowcount from delete statements when caller relies on it
        if sql.strip().lower().startswith("delete"):
            # For job_id = ANY(%s) pattern, rowcount = len of the list passed
            if params and isinstance(params, tuple) and len(params) > 0:
                first_param = params[0]
                if isinstance(first_param, list):
                    self.rowcount = len(first_param)
                else:
                    # Assume a single row operation if params are present but not a list
                    self.rowcount = 1
            else:
                # Default to 0 if no params are provided for the delete
                self.rowcount = 0

    def fetchone(self):
        if not self.rows:
            return None
        if self._fetch_idx >= len(self.rows):
            return None
        row = self.rows[self._fetch_idx]
        self._fetch_idx += 1
        return row

    def fetchall(self):
        return self.rows


class DummyConnection:
    def __init__(self, cursor: DummyCursor):
        self.cursor_obj = cursor
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def cursor(self, *args, **kwargs):
        return self.cursor_obj

    def commit(self):
        self.commits += 1


class DummyPool:
    def __init__(self, connection: DummyConnection):
        self._connection = connection

    def connection(self):
        return self._connection


def _write_parquet_result(base: Path) -> None:
    """Create minimal valid backtest parquet bundle in base path."""
    base.mkdir(parents=True, exist_ok=True)
    signals = pl.DataFrame(
        {
            "permno": [1, 2, 1, 2],
            "date": [date(2024, 1, 1), date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 2)],
            "signal": [0.1, -0.2, 0.3, -0.1],
        }
    )
    weights = pl.DataFrame(
        {
            "permno": [1, 2, 1, 2],
            "date": [date(2024, 1, 1), date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 2)],
            "weight": [0.5, -0.5, 0.6, -0.6],
        }
    )
    ic = pl.DataFrame(
        {
            "date": [date(2024, 1, 1), date(2024, 1, 2)],
            "ic": [0.1, 0.2],
            "rank_ic": [0.05, 0.25],
        }
    )

    signals.write_parquet(base / "daily_signals.parquet")
    weights.write_parquet(base / "daily_weights.parquet")
    ic.write_parquet(base / "daily_ic.parquet")

    summary = {
        "snapshot_id": "snap-123",
        "dataset_version_ids": {"crsp": "v1"},
        "mean_ic": 0.15,
        "icir": 1.5,
        "hit_rate": 0.6,
    }
    (base / "summary.json").write_text(json.dumps(summary))


def _write_parquet_result_custom_summary(base: Path, summary: dict) -> None:
    """Create parquet bundle with caller-provided summary payload."""
    base.mkdir(parents=True, exist_ok=True)
    signals = pl.DataFrame(
        {
            "permno": [1, 2, 1, 2],
            "date": [date(2024, 1, 1), date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 2)],
            "signal": [0.1, -0.2, 0.3, -0.1],
        }
    )
    weights = pl.DataFrame(
        {
            "permno": [1, 2, 1, 2],
            "date": [date(2024, 1, 1), date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 2)],
            "weight": [0.5, -0.5, 0.6, -0.6],
        }
    )
    ic = pl.DataFrame(
        {
            "date": [date(2024, 1, 1), date(2024, 1, 2)],
            "ic": [0.1, 0.2],
            "rank_ic": [0.05, 0.25],
        }
    )

    signals.write_parquet(base / "daily_signals.parquet")
    weights.write_parquet(base / "daily_weights.parquet")
    ic.write_parquet(base / "daily_ic.parquet")
    (base / "summary.json").write_text(json.dumps(summary))


@pytest.mark.unit()
def test_get_result_happy_path(tmp_path):
    result_dir = tmp_path / "job123"
    _write_parquet_result(result_dir)

    row = {
        "job_id": "job123",
        "result_path": str(result_dir),
        "alpha_name": "alpha1",
        "start_date": date(2024, 1, 1),
        "end_date": date(2024, 1, 2),
        "weight_method": "zscore",
        "coverage": 1.0,
    }

    cursor = DummyCursor(rows=[row])
    conn = DummyConnection(cursor)
    storage = BacktestResultStorage(DummyPool(conn), base_dir=tmp_path)

    result = storage.get_result("job123")

    assert isinstance(result, BacktestResult)
    assert result.backtest_id == "job123"
    assert result.alpha_name == "alpha1"
    assert math.isclose(result.mean_ic, 0.15)
    assert result.snapshot_id == "snap-123"
    assert result.dataset_version_ids == {"crsp": "v1"}
    assert result.coverage == 1.0


@pytest.mark.unit()
def test_get_result_job_not_found_raises(tmp_path):
    cursor = DummyCursor(rows=[])
    conn = DummyConnection(cursor)
    storage = BacktestResultStorage(DummyPool(conn))

    with pytest.raises(JobNotFound):
        storage.get_result("missing")


@pytest.mark.unit()
def test_get_result_missing_result_path_raises(tmp_path):
    row = {"job_id": "job123", "result_path": None}
    cursor = DummyCursor(rows=[row])
    conn = DummyConnection(cursor)
    storage = BacktestResultStorage(DummyPool(conn))

    with pytest.raises(ResultPathMissing):
        storage.get_result("job123")


@pytest.mark.unit()
def test_load_result_missing_path(tmp_path):
    cursor = DummyCursor(rows=[])
    storage = BacktestResultStorage(DummyPool(DummyConnection(cursor)))

    with pytest.raises(ResultPathMissing):
        storage._load_result_from_path(tmp_path / "nope")


@pytest.mark.unit()
def test_load_result_missing_summary_json(tmp_path):
    result_dir = tmp_path / "job1"
    result_dir.mkdir()
    _write_parquet_result(result_dir)
    (result_dir / "summary.json").unlink()

    storage = BacktestResultStorage(DummyPool(DummyConnection(DummyCursor())))

    with pytest.raises(ValueError, match="summary.json"):
        storage._load_result_from_path(result_dir)


@pytest.mark.unit()
def test_load_result_missing_repro_metadata(tmp_path):
    result_dir = tmp_path / "job1"
    result_dir.mkdir()
    _write_parquet_result(result_dir)
    # Overwrite summary without snapshot_id / dataset_version_ids
    (result_dir / "summary.json").write_text(json.dumps({"mean_ic": 0.1}))

    storage = BacktestResultStorage(DummyPool(DummyConnection(DummyCursor())))

    with pytest.raises(ValueError, match="reproducibility"):
        storage._load_result_from_path(result_dir)


@pytest.mark.unit()
def test_list_jobs_with_filters():
    rows = [
        {
            "job_id": "job1",
            "status": "completed",
            "alpha_name": "alphaA",
            "start_date": date(2024, 1, 1),
            "end_date": date(2024, 1, 2),
            "created_by": "alice",
            "created_at": datetime(2024, 1, 3, tzinfo=UTC),
            "mean_ic": 0.1,
            "icir": 1.0,
        }
    ]
    cursor = DummyCursor(rows=rows)
    conn = DummyConnection(cursor)
    storage = BacktestResultStorage(DummyPool(conn))

    result = storage.list_jobs(
        created_by="alice", alpha_name="alphaA", status="completed", limit=5, offset=2
    )

    assert result[0]["job_id"] == "job1"
    # Ensure filters applied via params ordering (LIMIT before OFFSET per PostgreSQL)
    assert cursor.executed[0][1] == ["alice", "alphaA", "completed", 5, 2]


@pytest.mark.unit()
def test_cleanup_old_results_deletes_terminal(tmp_path):
    old_path = tmp_path / "old_job"
    _write_parquet_result(old_path)

    rows = [{"job_id": "old_job", "result_path": str(old_path)}]
    cursor = DummyCursor(rows=rows)
    conn = DummyConnection(cursor)
    storage = BacktestResultStorage(DummyPool(conn), base_dir=tmp_path)

    deleted = storage.cleanup_old_results(retention_days=0)

    assert deleted == 1
    assert not old_path.exists()
    # Two statements executed: select then delete by job_ids
    assert len(cursor.executed) == 2
    assert conn.commits == 1
    # First query (select) should have terminal statuses
    assert isinstance(cursor.executed[0][1][1], list)
    assert set(cursor.executed[0][1][1]) == {"completed", "failed", "cancelled"}
    # Second query (delete) should use job_id = ANY(...)
    assert "job_id = ANY" in cursor.executed[1][0]
    assert cursor.executed[1][1] == (["old_job"],)


@pytest.mark.unit()
def test_cleanup_old_results_non_terminal_not_selected(tmp_path):
    """Ensure status filter uses terminal list (pending job should not match)."""
    cursor = DummyCursor(rows=[])
    conn = DummyConnection(cursor)
    storage = BacktestResultStorage(DummyPool(conn))

    deleted = storage.cleanup_old_results(retention_days=0)

    assert deleted == 0
    # Only select runs when no jobs match (no delete needed)
    assert len(cursor.executed) == 1
    # Status list should match terminal statuses
    assert set(cursor.executed[0][1][1]) == {"completed", "failed", "cancelled"}


@pytest.mark.unit()
def test_job_to_dict_handles_dataclass_and_dict():
    created_at = datetime(2024, 1, 1, tzinfo=UTC)
    job = BacktestJob(
        id=uuid.uuid4(),
        job_id="job1",
        status="completed",
        alpha_name="alpha1",
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 2),
        weight_method="zscore",
        config_json={},
        created_at=created_at,
        created_by="alice",
        job_timeout=3600,
    )
    storage = BacktestResultStorage(DummyPool(DummyConnection(DummyCursor())))

    as_dict = storage._job_to_dict(job)
    assert as_dict["start_date"] == "2024-01-01"
    assert as_dict["created_at"] == created_at.isoformat()

    raw_dict = {
        "job_id": "job2",
        "status": "failed",
        "created_at": created_at,
        "start_date": date(2024, 2, 1),
        "end_date": date(2024, 2, 5),
    }
    as_dict2 = storage._job_to_dict(raw_dict)
    assert as_dict2["job_id"] == "job2"
    assert as_dict2["end_date"] == "2024-02-05"


@pytest.mark.unit()
def test_load_result_computes_mean_ic_and_icir_when_missing(tmp_path):
    """Summary lacking mean_ic/icir should compute from IC parquet."""
    result_dir = tmp_path / "job_mean_ic"
    summary = {
        "snapshot_id": "snap-abc",
        "dataset_version_ids": {"crsp": "v1"},
        # intentionally omit mean_ic and icir
    }
    _write_parquet_result_custom_summary(result_dir, summary)

    storage = BacktestResultStorage(DummyPool(DummyConnection(DummyCursor())))
    result = storage._load_result_from_path(result_dir)

    # mean of [0.1, 0.2] = 0.15; std sample ≈ 0.07071 → icir ≈ 2.12
    assert math.isclose(result.mean_ic, 0.15, rel_tol=1e-6)
    assert math.isclose(result.icir, 2.1213203436, rel_tol=1e-6)


@pytest.mark.unit()
def test_load_result_falls_back_coverage_and_dates_from_signals(tmp_path):
    result_dir = tmp_path / "job_cov"
    summary = {
        "snapshot_id": "snap-cov",
        "dataset_version_ids": {"crsp": "v1"},
        "mean_ic": 0.15,
        "icir": 1.5,
    }
    _write_parquet_result_custom_summary(result_dir, summary)

    job_row = {
        "job_id": "job_cov",
        "alpha_name": "alpha_cov",
        "start_date": None,
        "end_date": None,
        "weight_method": "zscore",
        "coverage": None,
    }

    storage = BacktestResultStorage(DummyPool(DummyConnection(DummyCursor())))
    result = storage._load_result_from_path(result_dir, job_row=job_row)

    assert result.coverage == 1.0  # all signals present
    assert result.start_date == date(2024, 1, 1)
    assert result.end_date == date(2024, 1, 2)


# ------------------------------------------------------------------ Path Safety Regression Tests


@pytest.mark.unit()
def test_get_result_rejects_path_outside_base_dir(tmp_path):
    """Regression: result_path pointing outside base_dir should raise ResultPathMissing."""
    # Create a valid parquet result in a sibling directory (outside base_dir)
    outside_dir = tmp_path / "outside"
    _write_parquet_result(outside_dir)

    # base_dir is a subdirectory, so outside_dir is NOT relative to it
    base_dir = tmp_path / "allowed"
    base_dir.mkdir()

    row = {
        "job_id": "job_outside",
        "result_path": str(outside_dir),  # Points outside base_dir
        "alpha_name": "alpha1",
        "start_date": date(2024, 1, 1),
        "end_date": date(2024, 1, 2),
    }

    cursor = DummyCursor(rows=[row])
    conn = DummyConnection(cursor)
    storage = BacktestResultStorage(DummyPool(conn), base_dir=base_dir)

    with pytest.raises(ResultPathMissing, match="outside allowed directory"):
        storage.get_result("job_outside")


@pytest.mark.unit()
def test_get_result_rejects_path_traversal_attack(tmp_path):
    """Regression: result_path with .. traversal should raise ResultPathMissing."""
    base_dir = tmp_path / "allowed"
    base_dir.mkdir()

    # Attempt path traversal via ..
    malicious_path = str(base_dir / ".." / "escaped")

    row = {
        "job_id": "job_traversal",
        "result_path": malicious_path,
        "alpha_name": "alpha1",
        "start_date": date(2024, 1, 1),
        "end_date": date(2024, 1, 2),
    }

    cursor = DummyCursor(rows=[row])
    conn = DummyConnection(cursor)
    storage = BacktestResultStorage(DummyPool(conn), base_dir=base_dir)

    with pytest.raises(ResultPathMissing, match="outside allowed directory"):
        storage.get_result("job_traversal")


@pytest.mark.unit()
def test_cleanup_skips_paths_outside_base_dir(tmp_path):
    """Regression: cleanup should skip paths outside base_dir entirely (no artifact or DB deletion)."""
    # Create directories: one inside base_dir, one outside
    base_dir = tmp_path / "allowed"
    base_dir.mkdir()
    inside_dir = base_dir / "inside_job"
    _write_parquet_result(inside_dir)

    outside_dir = tmp_path / "outside_job"
    _write_parquet_result(outside_dir)

    rows = [
        {"job_id": "inside_job", "result_path": str(inside_dir)},
        {"job_id": "outside_job", "result_path": str(outside_dir)},
    ]
    cursor = DummyCursor(rows=rows)
    conn = DummyConnection(cursor)
    storage = BacktestResultStorage(DummyPool(conn), base_dir=base_dir)

    deleted = storage.cleanup_old_results(retention_days=0)

    # Only inside_job DB row should be deleted (outside_job skipped to avoid orphaning)
    assert deleted == 1

    # Only inside_dir should be deleted from disk
    assert not inside_dir.exists(), "Path inside base_dir should be deleted"
    assert outside_dir.exists(), "Path outside base_dir should NOT be deleted"

    # Verify delete query only includes inside_job
    delete_query = cursor.executed[1]
    assert "job_id = ANY" in delete_query[0]
    assert delete_query[1] == (["inside_job"],)


@pytest.mark.unit()
def test_load_result_corrupt_parquet_raises_value_error(tmp_path):
    """Corrupt parquet file should raise ValueError with descriptive message."""
    result_dir = tmp_path / "corrupt_job"
    result_dir.mkdir()

    # Write valid summary.json
    summary = {
        "snapshot_id": "snap-corrupt",
        "dataset_version_ids": {"crsp": "v1"},
        "mean_ic": 0.15,
    }
    (result_dir / "summary.json").write_text(json.dumps(summary))

    # Write corrupt parquet file (just garbage bytes)
    (result_dir / "daily_signals.parquet").write_bytes(b"not a valid parquet file")
    (result_dir / "daily_weights.parquet").write_bytes(b"also garbage")
    (result_dir / "daily_ic.parquet").write_bytes(b"more garbage")

    storage = BacktestResultStorage(DummyPool(DummyConnection(DummyCursor())))

    with pytest.raises(ValueError, match="Failed to load Parquet artifact"):
        storage._load_result_from_path(result_dir)


# ------------------------------------------------------------------ load_universe_signals_lazy Tests


def _write_signals_parquet(base: Path, include_signal_name: bool = False) -> None:
    """Create minimal signals parquet file for testing."""
    base.mkdir(parents=True, exist_ok=True)
    data = {
        "permno": [1, 2, 1, 2, 3],
        "date": [
            date(2024, 1, 2),
            date(2024, 1, 2),
            date(2024, 1, 3),
            date(2024, 1, 3),
            date(2024, 1, 4),
        ],
        "signal": [0.1, -0.2, 0.3, -0.1, 0.5],
    }
    if include_signal_name:
        data["signal_name"] = ["alpha1", "alpha1", "alpha1", "alpha2", "alpha1"]
    signals = pl.DataFrame(data)
    signals.write_parquet(base / "daily_signals.parquet")


@pytest.mark.unit()
def test_load_universe_signals_lazy_happy_path(tmp_path):
    """Should return LazyFrame with signals data."""
    result_dir = tmp_path / "job_signals"
    _write_signals_parquet(result_dir)

    row = {"result_path": str(result_dir)}
    cursor = DummyCursor(rows=[row])
    conn = DummyConnection(cursor)
    storage = BacktestResultStorage(DummyPool(conn), base_dir=tmp_path)

    lf = storage.load_universe_signals_lazy("job123")

    assert lf is not None
    # Collect to verify data
    df = lf.collect()
    assert df.height == 5
    assert "signal" in df.columns


@pytest.mark.unit()
def test_load_universe_signals_lazy_job_not_found_raises(tmp_path):
    """Missing job should raise JobNotFound."""
    cursor = DummyCursor(rows=[])  # No rows
    conn = DummyConnection(cursor)
    storage = BacktestResultStorage(DummyPool(conn))

    with pytest.raises(JobNotFound):
        storage.load_universe_signals_lazy("missing_job")


@pytest.mark.unit()
def test_load_universe_signals_lazy_no_result_path_returns_none(tmp_path):
    """Job with no result_path should return None."""
    row = {"result_path": None}
    cursor = DummyCursor(rows=[row])
    conn = DummyConnection(cursor)
    storage = BacktestResultStorage(DummyPool(conn))

    result = storage.load_universe_signals_lazy("job123")

    assert result is None


@pytest.mark.unit()
def test_load_universe_signals_lazy_no_signals_file_returns_none(tmp_path):
    """Missing signals file should return None."""
    result_dir = tmp_path / "job_no_signals"
    result_dir.mkdir()
    # No daily_signals.parquet created

    row = {"result_path": str(result_dir)}
    cursor = DummyCursor(rows=[row])
    conn = DummyConnection(cursor)
    storage = BacktestResultStorage(DummyPool(conn), base_dir=tmp_path)

    result = storage.load_universe_signals_lazy("job123")

    assert result is None


@pytest.mark.unit()
def test_load_universe_signals_lazy_filters_by_signal_name(tmp_path):
    """Should filter by signal_name when provided."""
    result_dir = tmp_path / "job_signal_filter"
    _write_signals_parquet(result_dir, include_signal_name=True)

    row = {"result_path": str(result_dir)}
    cursor = DummyCursor(rows=[row])
    conn = DummyConnection(cursor)
    storage = BacktestResultStorage(DummyPool(conn), base_dir=tmp_path)

    lf = storage.load_universe_signals_lazy("job123", signal_name="alpha2")

    assert lf is not None
    df = lf.collect()
    assert df.height == 1  # Only one row with alpha2
    assert df["signal_name"][0] == "alpha2"


@pytest.mark.unit()
def test_load_universe_signals_lazy_filters_by_date_range(tmp_path):
    """Should filter by date_range when provided."""
    result_dir = tmp_path / "job_date_filter"
    _write_signals_parquet(result_dir)

    row = {"result_path": str(result_dir)}
    cursor = DummyCursor(rows=[row])
    conn = DummyConnection(cursor)
    storage = BacktestResultStorage(DummyPool(conn), base_dir=tmp_path)

    lf = storage.load_universe_signals_lazy(
        "job123",
        date_range=(date(2024, 1, 2), date(2024, 1, 3)),
    )

    assert lf is not None
    df = lf.collect()
    # Should have 4 rows (2 on Jan 2, 2 on Jan 3)
    assert df.height == 4


@pytest.mark.unit()
def test_load_universe_signals_lazy_applies_limit(tmp_path):
    """Should apply limit when provided."""
    result_dir = tmp_path / "job_limit"
    _write_signals_parquet(result_dir)

    row = {"result_path": str(result_dir)}
    cursor = DummyCursor(rows=[row])
    conn = DummyConnection(cursor)
    storage = BacktestResultStorage(DummyPool(conn), base_dir=tmp_path)

    lf = storage.load_universe_signals_lazy("job123", limit=2)

    assert lf is not None
    df = lf.collect()
    assert df.height == 2


@pytest.mark.unit()
def test_load_universe_signals_lazy_no_limit_returns_all(tmp_path):
    """Should return all rows when limit is None."""
    result_dir = tmp_path / "job_no_limit"
    _write_signals_parquet(result_dir)

    row = {"result_path": str(result_dir)}
    cursor = DummyCursor(rows=[row])
    conn = DummyConnection(cursor)
    storage = BacktestResultStorage(DummyPool(conn), base_dir=tmp_path)

    lf = storage.load_universe_signals_lazy("job123", limit=None)

    assert lf is not None
    df = lf.collect()
    assert df.height == 5  # All rows


@pytest.mark.unit()
def test_load_universe_signals_lazy_rejects_path_outside_base(tmp_path):
    """Path outside base_dir should raise ResultPathMissing."""
    # Create directories: one inside base_dir, one outside
    base_dir = tmp_path / "allowed"
    base_dir.mkdir()

    outside_dir = tmp_path / "outside_job"
    _write_signals_parquet(outside_dir)

    row = {"result_path": str(outside_dir)}  # Points outside base_dir
    cursor = DummyCursor(rows=[row])
    conn = DummyConnection(cursor)
    storage = BacktestResultStorage(DummyPool(conn), base_dir=base_dir)

    with pytest.raises(ResultPathMissing, match="outside allowed directory"):
        storage.load_universe_signals_lazy("job123")


@pytest.mark.unit()
def test_load_universe_signals_lazy_rejects_path_traversal(tmp_path):
    """Path traversal attack should raise ResultPathMissing."""
    base_dir = tmp_path / "allowed"
    base_dir.mkdir()

    # Attempt path traversal via ..
    malicious_path = str(base_dir / ".." / "escaped")

    row = {"result_path": malicious_path}
    cursor = DummyCursor(rows=[row])
    conn = DummyConnection(cursor)
    storage = BacktestResultStorage(DummyPool(conn), base_dir=base_dir)

    with pytest.raises(ResultPathMissing, match="outside allowed directory"):
        storage.load_universe_signals_lazy("job123")


# =============================================================================
# P6T11: Walk-Forward and Parameter Search Artifact Tests
# =============================================================================


def _write_walk_forward_artifact(base: Path) -> dict:
    """Create a valid walk_forward.json artifact and return the data."""
    base.mkdir(parents=True, exist_ok=True)

    walk_forward_data = {
        "version": "1.0",
        "config": {
            "train_months": 12,
            "test_months": 3,
            "step_months": 3,
            "min_train_samples": 252,
            "overfitting_threshold": 2.0,
        },
        "windows": [
            {
                "window_id": 0,
                "train_start": "2020-01-01",
                "train_end": "2020-12-31",
                "test_start": "2021-01-01",
                "test_end": "2021-03-31",
                "best_params": {"window": 20, "zscore": 2.0},
                "train_ic": 0.045,
                "test_ic": 0.032,
                "test_icir": 1.2,
            },
            {
                "window_id": 1,
                "train_start": "2020-04-01",
                "train_end": "2021-03-31",
                "test_start": "2021-04-01",
                "test_end": "2021-06-30",
                "best_params": {"window": 25, "zscore": 1.5},
                "train_ic": 0.038,
                "test_ic": 0.028,
                "test_icir": 1.1,
            },
        ],
        "aggregated": {
            "test_ic": 0.030,
            "test_icir": 1.15,
            "overfitting_ratio": 1.4,
            "is_overfit": False,
        },
        "created_at": "2026-02-02T10:30:00Z",
    }

    (base / "walk_forward.json").write_text(json.dumps(walk_forward_data))
    return walk_forward_data


def _write_param_search_artifact(base: Path) -> dict:
    """Create a valid param_search.json artifact and return the data."""
    base.mkdir(parents=True, exist_ok=True)

    param_search_data = {
        "version": "1.0",
        "param_names": ["window", "zscore"],
        "param_ranges": {"window": [10, 15, 20, 25, 30], "zscore": [1.0, 1.5, 2.0, 2.5]},
        "metric_name": "mean_ic",
        "best_params": {"window": 20, "zscore": 2.0},
        "best_score": 0.045,
        "all_results": [
            {"params": {"window": 10, "zscore": 1.0}, "score": 0.012},
            {"params": {"window": 20, "zscore": 2.0}, "score": 0.045},
            {"params": {"window": 30, "zscore": 1.5}, "score": 0.028},
        ],
        "created_at": "2026-02-02T10:30:00Z",
    }

    (base / "param_search.json").write_text(json.dumps(param_search_data))
    return param_search_data


@pytest.mark.unit()
def test_load_walk_forward_success(tmp_path):
    """Successfully load walk-forward results from artifact."""
    result_dir = tmp_path / "job123"
    _write_walk_forward_artifact(result_dir)

    row = {"result_path": str(result_dir)}
    cursor = DummyCursor(rows=[row])
    conn = DummyConnection(cursor)
    storage = BacktestResultStorage(DummyPool(conn), base_dir=tmp_path)

    result = storage.load_walk_forward("job123")

    assert result is not None
    assert len(result.windows) == 2
    assert result.windows[0].window_id == 0
    assert result.windows[0].train_start == date(2020, 1, 1)
    assert result.windows[0].test_end == date(2021, 3, 31)
    assert result.windows[0].best_params == {"window": 20, "zscore": 2.0}
    assert math.isclose(result.windows[0].train_ic, 0.045)
    assert math.isclose(result.aggregated_test_ic, 0.030)
    assert math.isclose(result.overfitting_ratio, 1.4)
    assert result.overfitting_threshold == 2.0
    assert not result.is_overfit


@pytest.mark.unit()
def test_load_walk_forward_not_found(tmp_path):
    """Job not found should raise JobNotFound."""
    cursor = DummyCursor(rows=[])
    conn = DummyConnection(cursor)
    storage = BacktestResultStorage(DummyPool(conn), base_dir=tmp_path)

    with pytest.raises(JobNotFound):
        storage.load_walk_forward("nonexistent")


@pytest.mark.unit()
def test_load_walk_forward_legacy_job_returns_none(tmp_path):
    """Legacy job without walk_forward.json should return None."""
    result_dir = tmp_path / "job123"
    result_dir.mkdir(parents=True)
    # Don't create walk_forward.json - simulate legacy job

    row = {"result_path": str(result_dir)}
    cursor = DummyCursor(rows=[row])
    conn = DummyConnection(cursor)
    storage = BacktestResultStorage(DummyPool(conn), base_dir=tmp_path)

    result = storage.load_walk_forward("job123")
    assert result is None


@pytest.mark.unit()
def test_load_walk_forward_no_result_path_returns_none(tmp_path):
    """Job with no result_path should return None."""
    row = {"result_path": None}
    cursor = DummyCursor(rows=[row])
    conn = DummyConnection(cursor)
    storage = BacktestResultStorage(DummyPool(conn), base_dir=tmp_path)

    result = storage.load_walk_forward("job123")
    assert result is None


@pytest.mark.unit()
def test_load_walk_forward_invalid_json_returns_none(tmp_path):
    """Corrupted walk_forward.json should return None (graceful degradation)."""
    result_dir = tmp_path / "job123"
    result_dir.mkdir(parents=True)
    (result_dir / "walk_forward.json").write_text("not valid json {{{")

    row = {"result_path": str(result_dir)}
    cursor = DummyCursor(rows=[row])
    conn = DummyConnection(cursor)
    storage = BacktestResultStorage(DummyPool(conn), base_dir=tmp_path)

    result = storage.load_walk_forward("job123")
    assert result is None


@pytest.mark.unit()
def test_load_param_search_success(tmp_path):
    """Successfully load parameter search results from artifact."""
    result_dir = tmp_path / "job123"
    _write_param_search_artifact(result_dir)

    row = {"result_path": str(result_dir)}
    cursor = DummyCursor(rows=[row])
    conn = DummyConnection(cursor)
    storage = BacktestResultStorage(DummyPool(conn), base_dir=tmp_path)

    result = storage.load_param_search("job123")

    assert result is not None
    assert result.best_params == {"window": 20, "zscore": 2.0}
    assert math.isclose(result.best_score, 0.045)
    assert len(result.all_results) == 3
    assert result.param_names == ["window", "zscore"]
    assert result.param_ranges == {"window": [10, 15, 20, 25, 30], "zscore": [1.0, 1.5, 2.0, 2.5]}
    assert result.metric_name == "mean_ic"


@pytest.mark.unit()
def test_load_param_search_not_found(tmp_path):
    """Job not found should raise JobNotFound."""
    cursor = DummyCursor(rows=[])
    conn = DummyConnection(cursor)
    storage = BacktestResultStorage(DummyPool(conn), base_dir=tmp_path)

    with pytest.raises(JobNotFound):
        storage.load_param_search("nonexistent")


@pytest.mark.unit()
def test_load_param_search_legacy_job_returns_none(tmp_path):
    """Legacy job without param_search.json should return None."""
    result_dir = tmp_path / "job123"
    result_dir.mkdir(parents=True)
    # Don't create param_search.json - simulate legacy job

    row = {"result_path": str(result_dir)}
    cursor = DummyCursor(rows=[row])
    conn = DummyConnection(cursor)
    storage = BacktestResultStorage(DummyPool(conn), base_dir=tmp_path)

    result = storage.load_param_search("job123")
    assert result is None


@pytest.mark.unit()
def test_load_param_search_invalid_json_returns_none(tmp_path):
    """Corrupted param_search.json should return None (graceful degradation)."""
    result_dir = tmp_path / "job123"
    result_dir.mkdir(parents=True)
    (result_dir / "param_search.json").write_text("{{invalid json")

    row = {"result_path": str(result_dir)}
    cursor = DummyCursor(rows=[row])
    conn = DummyConnection(cursor)
    storage = BacktestResultStorage(DummyPool(conn), base_dir=tmp_path)

    result = storage.load_param_search("job123")
    assert result is None


@pytest.mark.unit()
def test_load_param_search_legacy_schema_missing_optional_fields(tmp_path):
    """Legacy param_search.json without optional fields should still load."""
    result_dir = tmp_path / "job123"
    result_dir.mkdir(parents=True)

    # Minimal schema without optional fields
    legacy_data = {
        "best_params": {"window": 20},
        "best_score": 0.05,
        "all_results": [{"params": {"window": 20}, "score": 0.05}],
    }
    (result_dir / "param_search.json").write_text(json.dumps(legacy_data))

    row = {"result_path": str(result_dir)}
    cursor = DummyCursor(rows=[row])
    conn = DummyConnection(cursor)
    storage = BacktestResultStorage(DummyPool(conn), base_dir=tmp_path)

    result = storage.load_param_search("job123")

    assert result is not None
    assert result.best_params == {"window": 20}
    assert math.isclose(result.best_score, 0.05)
    assert result.param_names is None  # Optional field missing
    assert result.param_ranges is None
    assert result.metric_name is None


@pytest.mark.unit()
def test_serialize_walk_forward_roundtrip(tmp_path):
    """Serialize and deserialize walk-forward result preserves data."""
    from libs.trading.backtest.result_storage import serialize_walk_forward
    from libs.trading.backtest.walk_forward import (
        WalkForwardConfig,
        WalkForwardResult,
        WindowResult,
    )

    config = WalkForwardConfig(
        train_months=12,
        test_months=3,
        step_months=3,
        min_train_samples=252,
        overfitting_threshold=2.0,
    )

    original = WalkForwardResult(
        windows=[
            WindowResult(
                window_id=0,
                train_start=date(2020, 1, 1),
                train_end=date(2020, 12, 31),
                test_start=date(2021, 1, 1),
                test_end=date(2021, 3, 31),
                best_params={"window": 20},
                train_ic=0.045,
                test_ic=0.032,
                test_icir=1.2,
            )
        ],
        aggregated_test_ic=0.032,
        aggregated_test_icir=1.2,
        overfitting_ratio=1.4,
        overfitting_threshold=2.0,
    )

    # Serialize
    serialized = serialize_walk_forward(original, config)

    # Write to file and read back
    result_dir = tmp_path / "job123"
    result_dir.mkdir()
    (result_dir / "walk_forward.json").write_text(json.dumps(serialized))

    # Deserialize
    row = {"result_path": str(result_dir)}
    cursor = DummyCursor(rows=[row])
    conn = DummyConnection(cursor)
    storage = BacktestResultStorage(DummyPool(conn), base_dir=tmp_path)

    loaded = storage.load_walk_forward("job123")

    assert loaded is not None
    assert len(loaded.windows) == 1
    assert loaded.windows[0].train_start == original.windows[0].train_start
    assert loaded.windows[0].best_params == original.windows[0].best_params
    assert math.isclose(loaded.aggregated_test_ic, original.aggregated_test_ic)
    assert math.isclose(loaded.overfitting_ratio, original.overfitting_ratio)


@pytest.mark.unit()
def test_serialize_param_search_roundtrip(tmp_path):
    """Serialize and deserialize param search result preserves data."""
    from libs.trading.backtest.param_search import SearchResult
    from libs.trading.backtest.result_storage import serialize_param_search

    original = SearchResult(
        best_params={"window": 20, "zscore": 2.0},
        best_score=0.045,
        all_results=[
            {"params": {"window": 10, "zscore": 1.0}, "score": 0.012},
            {"params": {"window": 20, "zscore": 2.0}, "score": 0.045},
        ],
        param_names=["window", "zscore"],
        param_ranges={"window": [10, 20], "zscore": [1.0, 2.0]},
        metric_name="mean_ic",
    )

    # Serialize
    serialized = serialize_param_search(original)

    # Write to file and read back
    result_dir = tmp_path / "job123"
    result_dir.mkdir()
    (result_dir / "param_search.json").write_text(json.dumps(serialized))

    # Deserialize
    row = {"result_path": str(result_dir)}
    cursor = DummyCursor(rows=[row])
    conn = DummyConnection(cursor)
    storage = BacktestResultStorage(DummyPool(conn), base_dir=tmp_path)

    loaded = storage.load_param_search("job123")

    assert loaded is not None
    assert loaded.best_params == original.best_params
    assert math.isclose(loaded.best_score, original.best_score)
    assert len(loaded.all_results) == len(original.all_results)
    assert loaded.param_names == original.param_names
    assert loaded.param_ranges == original.param_ranges
    assert loaded.metric_name == original.metric_name


@pytest.mark.unit()
def test_serialize_param_search_omits_none_fields():
    """Optional visualization fields should be omitted when None."""
    from libs.trading.backtest.param_search import SearchResult
    from libs.trading.backtest.result_storage import serialize_param_search

    result = SearchResult(
        best_params={"window": 20},
        best_score=0.045,
        all_results=[{"params": {"window": 20}, "score": 0.045}],
        param_names=None,
        param_ranges=None,
        metric_name=None,
    )

    serialized = serialize_param_search(result)

    assert "param_names" not in serialized
    assert "param_ranges" not in serialized
    assert "metric_name" not in serialized
    assert serialized["best_params"] == {"window": 20}
    assert serialized["best_score"] == 0.045


@pytest.mark.unit()
def test_load_walk_forward_invalid_json_logs_warning(tmp_path):
    """Corrupted walk_forward.json should log a warning with job_id and path."""
    import libs.trading.backtest.result_storage as rs_module

    result_dir = tmp_path / "job123"
    result_dir.mkdir(parents=True)
    (result_dir / "walk_forward.json").write_text("not valid json {{{")

    row = {"result_path": str(result_dir)}
    cursor = DummyCursor(rows=[row])
    conn = DummyConnection(cursor)
    storage = BacktestResultStorage(DummyPool(conn), base_dir=tmp_path)

    mock_logger = MagicMock()
    original_logger = rs_module.logger
    rs_module.logger = mock_logger
    try:
        result = storage.load_walk_forward("job123")
    finally:
        rs_module.logger = original_logger

    assert result is None
    mock_logger.warning.assert_called_once()
    call_kwargs = mock_logger.warning.call_args
    assert call_kwargs.args[0] == "walk_forward_artifact_load_failed"
    assert call_kwargs.kwargs["job_id"] == "job123"


@pytest.mark.unit()
def test_load_param_search_invalid_json_logs_warning(tmp_path):
    """Corrupted param_search.json should log a warning with job_id and path."""
    import libs.trading.backtest.result_storage as rs_module

    result_dir = tmp_path / "job123"
    result_dir.mkdir(parents=True)
    (result_dir / "param_search.json").write_text("{{invalid json")

    row = {"result_path": str(result_dir)}
    cursor = DummyCursor(rows=[row])
    conn = DummyConnection(cursor)
    storage = BacktestResultStorage(DummyPool(conn), base_dir=tmp_path)

    mock_logger = MagicMock()
    original_logger = rs_module.logger
    rs_module.logger = mock_logger
    try:
        result = storage.load_param_search("job123")
    finally:
        rs_module.logger = original_logger

    assert result is None
    mock_logger.warning.assert_called_once()
    call_kwargs = mock_logger.warning.call_args
    assert call_kwargs.args[0] == "param_search_artifact_load_failed"
    assert call_kwargs.kwargs["job_id"] == "job123"


@pytest.mark.unit()
def test_serialize_walk_forward_sanitizes_nan():
    """NaN/inf float values should be serialized as None for strict JSON compatibility."""
    from datetime import date

    from libs.trading.backtest.result_storage import serialize_walk_forward
    from libs.trading.backtest.walk_forward import (
        WalkForwardConfig,
        WalkForwardResult,
        WindowResult,
    )

    config = WalkForwardConfig(overfitting_threshold=2.0)

    windows = [
        WindowResult(
            window_id=0,
            train_start=date(2020, 1, 1),
            train_end=date(2020, 12, 31),
            test_start=date(2021, 1, 1),
            test_end=date(2021, 3, 31),
            best_params={"window": 20},
            train_ic=float("nan"),
            test_ic=0.032,
            test_icir=float("inf"),
        ),
    ]

    result = WalkForwardResult(
        windows=windows,
        aggregated_test_ic=float("nan"),
        aggregated_test_icir=float("nan"),
        overfitting_ratio=float("nan"),
        overfitting_threshold=2.0,
    )

    serialized = serialize_walk_forward(result, config)

    # Window-level NaN/inf should become None
    assert serialized["windows"][0]["train_ic"] is None
    assert serialized["windows"][0]["test_ic"] == 0.032
    assert serialized["windows"][0]["test_icir"] is None

    # Aggregated NaN should become None
    assert serialized["aggregated"]["test_ic"] is None
    assert serialized["aggregated"]["test_icir"] is None
    assert serialized["aggregated"]["overfitting_ratio"] is None

    # Verify the output is valid strict JSON (no NaN tokens)
    import json

    json_str = json.dumps(serialized)
    assert "NaN" not in json_str
    assert "Infinity" not in json_str


@pytest.mark.unit()
def test_serialize_param_search_sanitizes_nan_scores():
    """NaN scores in param search results should be serialized as None."""
    from libs.trading.backtest.param_search import SearchResult
    from libs.trading.backtest.result_storage import serialize_param_search

    result = SearchResult(
        best_params={"window": 20},
        best_score=float("nan"),
        all_results=[
            {"params": {"window": 10}, "score": float("nan")},
            {"params": {"window": 20}, "score": 0.045},
        ],
        param_names=["window"],
        param_ranges={"window": [10, 20]},
        metric_name="mean_ic",
    )

    serialized = serialize_param_search(result)

    assert serialized["best_score"] is None
    assert serialized["all_results"][0]["score"] is None
    assert serialized["all_results"][1]["score"] == 0.045

    import json

    json_str = json.dumps(serialized)
    assert "NaN" not in json_str


@pytest.mark.unit()
def test_deserialize_walk_forward_restores_nan_from_none(tmp_path):
    """Deserialization should restore None values back to NaN for numeric fields."""
    import json
    import math
    from datetime import date

    from libs.trading.backtest.result_storage import serialize_walk_forward
    from libs.trading.backtest.walk_forward import (
        WalkForwardConfig,
        WalkForwardResult,
        WindowResult,
    )

    config = WalkForwardConfig(overfitting_threshold=2.0)
    windows = [
        WindowResult(
            window_id=0,
            train_start=date(2020, 1, 1),
            train_end=date(2020, 12, 31),
            test_start=date(2021, 1, 1),
            test_end=date(2021, 3, 31),
            best_params={"window": 20},
            train_ic=float("nan"),
            test_ic=0.032,
            test_icir=float("inf"),
        ),
    ]
    result = WalkForwardResult(
        windows=windows,
        aggregated_test_ic=float("nan"),
        aggregated_test_icir=float("nan"),
        overfitting_ratio=float("nan"),
        overfitting_threshold=2.0,
    )

    # Serialize (NaN/inf → None) then write to disk and reload
    serialized = serialize_walk_forward(result, config)
    artifact_path = tmp_path / "walk_forward.json"
    artifact_path.write_text(json.dumps(serialized))

    # Create storage and load
    row = {"result_path": str(tmp_path)}
    cursor = DummyCursor(rows=[row])
    conn = DummyConnection(cursor)
    storage = BacktestResultStorage(DummyPool(conn), base_dir=tmp_path.parent)

    loaded = storage.load_walk_forward("test_job")

    assert loaded is not None
    # Window-level: None should be restored to NaN
    assert math.isnan(loaded.windows[0].train_ic)
    assert loaded.windows[0].test_ic == 0.032
    assert math.isnan(loaded.windows[0].test_icir)
    # Aggregated: None should be restored to NaN
    assert math.isnan(loaded.aggregated_test_ic)
    assert math.isnan(loaded.overfitting_ratio)
    # is_overfit should work without TypeError (NaN → not overfit)
    assert loaded.is_overfit is False


@pytest.mark.unit()
def test_deserialize_param_search_restores_nan_from_none(tmp_path):
    """Deserialization should restore None scores back to NaN."""
    import json
    import math

    from libs.trading.backtest.param_search import SearchResult
    from libs.trading.backtest.result_storage import serialize_param_search

    original = SearchResult(
        best_params={"window": 20},
        best_score=float("nan"),
        all_results=[
            {"params": {"window": 10}, "score": float("nan")},
            {"params": {"window": 20}, "score": 0.045},
        ],
        param_names=["window"],
        param_ranges={"window": [10, 20]},
        metric_name="mean_ic",
    )

    # Serialize (NaN → None) then write to disk and reload
    serialized = serialize_param_search(original)
    artifact_path = tmp_path / "param_search.json"
    artifact_path.write_text(json.dumps(serialized))

    row = {"result_path": str(tmp_path)}
    cursor = DummyCursor(rows=[row])
    conn = DummyConnection(cursor)
    storage = BacktestResultStorage(DummyPool(conn), base_dir=tmp_path.parent)

    loaded = storage.load_param_search("test_job")

    assert loaded is not None
    assert math.isnan(loaded.best_score)
    assert math.isnan(loaded.all_results[0]["score"])
    assert loaded.all_results[1]["score"] == 0.045
    assert loaded.param_names == ["window"]
    assert loaded.metric_name == "mean_ic"
