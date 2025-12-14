from __future__ import annotations

import importlib.util
import json
import math
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

# Skip if optional heavy deps missing (mirrors test_job_queue pattern)
_missing = [mod for mod in ("polars", "psycopg") if importlib.util.find_spec(mod) is None]
if _missing:
    pytest.skip(
        f"Skipping backtest result storage tests because dependencies are missing: {', '.join(_missing)}",
        allow_module_level=True,
    )

import polars as pl

from libs.alpha.research_platform import BacktestResult
from libs.backtest.models import BacktestJob, JobNotFound, ResultPathMissing
from libs.backtest.result_storage import BacktestResultStorage


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
            # keep current rowcount or length of rows as best-effort
            self.rowcount = len(self.rows)

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

    result = storage.list_jobs(created_by="alice", alpha_name="alphaA", status="completed", limit=5, offset=2)

    assert result[0]["job_id"] == "job1"
    # Ensure filters applied via params ordering
    assert cursor.executed[0][1] == ["alice", "alphaA", "completed", 2, 5]


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
    # Two statements executed: select then delete
    assert len(cursor.executed) == 2
    assert conn.commits == 1
    # terminal statuses should be passed to BOTH select and delete
    assert all(
        isinstance(call[1][1], list) and set(call[1][1]) == {"completed", "failed", "cancelled"}
        for call in cursor.executed
    )


@pytest.mark.unit()
def test_cleanup_old_results_non_terminal_not_selected(tmp_path):
    """Ensure status filter uses terminal list (pending job should not match)."""
    cursor = DummyCursor(rows=[])
    conn = DummyConnection(cursor)
    storage = BacktestResultStorage(DummyPool(conn))

    deleted = storage.cleanup_old_results(retention_days=0)

    assert deleted == 0
    assert len(cursor.executed) == 2  # select + delete still run
    # Status list should match terminal statuses even when no rows returned
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
    """Regression: cleanup should skip (not delete) paths outside base_dir."""
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

    # DB delete still runs for both rows (we can't control that in test)
    assert deleted == 2

    # But only inside_dir should be deleted from disk
    assert not inside_dir.exists(), "Path inside base_dir should be deleted"
    assert outside_dir.exists(), "Path outside base_dir should NOT be deleted"
