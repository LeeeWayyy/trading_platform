from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import UUID

import pytest

from libs.backtest.models import (
    BacktestJob,
    JobNotFound,
    ResultPathMissing,
    row_to_backtest_job,
)

pytestmark = pytest.mark.unit


def _required_fields(**overrides):
    base = {
        "id": UUID("550e8400-e29b-41d4-a716-446655440000"),
        "job_id": "job123",
        "status": "pending",
        "alpha_name": "alpha",
        "start_date": date(2024, 1, 1),
        "end_date": date(2024, 1, 31),
        "weight_method": "zscore",
        "config_json": {"window": 5},
        "created_at": datetime(2024, 1, 1, tzinfo=UTC),
        "created_by": "tester",
        "job_timeout": 900,
    }
    base.update(overrides)
    return base


def test_backtest_job_instantiation_sets_defaults():
    job = BacktestJob(**_required_fields())

    assert job.progress_pct == 0
    assert job.retry_count == 0
    assert job.result_path is None
    assert job.started_at is None
    assert job.completed_at is None
    assert job.dataset_version_ids is None


def test_row_to_backtest_job_coerces_types_and_defaults():
    row = {
        "id": "550e8400-e29b-41d4-a716-446655440000",  # str -> UUID
        "job_id": "jid-1",
        "status": "completed",
        "alpha_name": "alpha42",
        "start_date": "2024-01-01",  # str -> date
        "end_date": date(2024, 1, 31),
        "weight_method": "quantile",
        "config_json": None,  # becomes {}
        "created_at": datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
        "created_by": "alice",
        "job_timeout": None,  # default 3600
        "progress_pct": None,  # default 0
        "mean_ic": "0.5",  # str -> float
        "icir": "not-a-number",  # invalid -> None
        "hit_rate": 0.8,  # int/float pass through
        "coverage": None,
        "long_short_spread": "1.23",  # str -> float
        "average_turnover": "bad",  # invalid -> None
        "decay_half_life": 5,  # int -> float
        "snapshot_id": "snap-1",
        "dataset_version_ids": "not-a-dict",  # coerced to None
        "error_message": None,
        "worker_id": "worker-1",
        "started_at": None,
        "completed_at": datetime(2024, 1, 2, tzinfo=UTC),
        "result_path": "/tmp/result.parquet",
        "retry_count": None,
    }

    job = row_to_backtest_job(row)

    assert isinstance(job.id, UUID)
    assert job.start_date == date(2024, 1, 1)
    assert job.end_date == date(2024, 1, 31)
    assert job.config_json == {}
    assert job.job_timeout == 3600
    assert job.progress_pct == 0
    assert job.mean_ic == 0.5
    assert job.icir is None
    assert job.hit_rate == 0.8
    assert job.average_turnover is None
    assert job.decay_half_life == 5.0
    assert job.dataset_version_ids is None
    assert job.retry_count == 0


def test_exceptions_are_raised():
    with pytest.raises(JobNotFound, match="missing job"):
        raise JobNotFound("missing job")

    with pytest.raises(ResultPathMissing, match="result path missing"):
        raise ResultPathMissing("result path missing")
