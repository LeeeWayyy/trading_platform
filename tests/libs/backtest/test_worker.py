from __future__ import annotations

import json
import sys
import types
from unittest.mock import MagicMock

import pytest

# Provide lightweight stub if structlog not installed in test environment
if "structlog" not in sys.modules:
    sys.modules["structlog"] = MagicMock()

# Provide lightweight RQ stubs to satisfy imports without Redis
if "rq" not in sys.modules:
    rq_stub = types.SimpleNamespace(Queue=MagicMock, Retry=MagicMock, get_current_job=lambda: None)
    sys.modules["rq"] = rq_stub
if "rq.job" not in sys.modules:
    sys.modules["rq.job"] = types.SimpleNamespace(Job=MagicMock, NoSuchJobError=Exception)

import libs.backtest.worker as worker_module
from libs.alpha.exceptions import JobCancelled
from libs.backtest.worker import BacktestWorker, record_retry


@pytest.fixture
def redis_with_pipeline():
    pipeline = MagicMock()
    pipeline.set.return_value = pipeline
    pipeline.expire.return_value = pipeline
    pipeline.execute.return_value = None

    redis = MagicMock()
    redis.exists.return_value = 0
    redis.pipeline.return_value = pipeline
    redis.get.return_value = None
    return redis, pipeline


def test_check_cancellation_raises_when_flag_set(redis_with_pipeline):
    redis, _ = redis_with_pipeline
    redis.exists.return_value = 1
    worker = BacktestWorker(redis, MagicMock())

    with pytest.raises(JobCancelled):
        worker.check_cancellation("job123")

    redis.delete.assert_called_with("backtest:cancel:job123")


def test_check_memory_raises_when_limit_exceeded(redis_with_pipeline):
    redis, _ = redis_with_pipeline
    worker = BacktestWorker(redis, MagicMock())
    worker.MAX_RSS_BYTES = 1_000
    worker.process = MagicMock()
    worker.process.memory_info.return_value = MagicMock(rss=2_000)

    with pytest.raises(MemoryError):
        worker.check_memory()


def test_update_progress_writes_to_redis(redis_with_pipeline, monkeypatch):
    redis, pipeline = redis_with_pipeline
    worker = BacktestWorker(redis, MagicMock())
    worker.check_cancellation = MagicMock()
    worker.check_memory = MagicMock()
    worker.update_db_progress = MagicMock()

    worker.update_progress(
        "job123",
        pct=10,
        stage="loading",
        current_date="2024-01-02",
        job_timeout=600,
        skip_cancel_check=True,
        skip_memory_check=True,
    )

    progress_call = pipeline.set.call_args_list[0]
    assert progress_call.args[0] == "backtest:progress:job123"
    payload = json.loads(progress_call.args[1])
    assert payload["pct"] == 10
    assert payload["stage"] == "loading"
    assert payload["current_date"] == "2024-01-02"

    # TTL coerced to at least 3600 seconds
    pipeline.expire.assert_any_call("backtest:progress:job123", 3600)
    pipeline.expire.assert_any_call("backtest:heartbeat:job123", 3600)
    worker.update_db_progress.assert_called_once_with("job123", 10)


def test_should_sync_db_progress_every_tenth_percent(redis_with_pipeline):
    redis, _ = redis_with_pipeline
    worker = BacktestWorker(redis, MagicMock())

    true_points = list(range(0, 101, 10))
    for pct in true_points:
        assert worker.should_sync_db_progress(pct) is True

    assert worker.should_sync_db_progress(5) is False


def test_record_retry_increments_db(monkeypatch):
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.__exit__.return_value = False

    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.__exit__.return_value = False
    conn.cursor.return_value = cursor

    pool = MagicMock()
    pool.connection.return_value = conn

    monkeypatch.setattr(worker_module, "_get_retry_pool", lambda: pool)

    job = MagicMock(id="job456")
    record_retry(job, None)

    cursor.execute.assert_called_once()
    conn.commit.assert_called_once()
