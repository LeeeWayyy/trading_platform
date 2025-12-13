from __future__ import annotations

import json
import sys
import types
from datetime import UTC, date, datetime
from unittest.mock import MagicMock

import pytest

# Provide lightweight stub if structlog not installed in test environment
if "structlog" not in sys.modules:
    sys.modules["structlog"] = MagicMock()

# Provide lightweight RQ stubs to satisfy imports without Redis
if "rq" not in sys.modules:
    rq_stub = types.SimpleNamespace(Queue=MagicMock, Retry=MagicMock, get_current_job=lambda: None)
    sys.modules["rq"] = rq_stub  # type: ignore[assignment]
if "rq.job" not in sys.modules:
    sys.modules["rq.job"] = types.SimpleNamespace(Job=MagicMock, NoSuchJobError=Exception)  # type: ignore[assignment]

import libs.backtest.job_queue as job_queue
from libs.backtest.job_queue import BacktestJobConfig, BacktestJobQueue, JobPriority


class DummyCursor:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.executed = []
        self.rowcount = len(self.rows)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return self.rows[0] if self.rows else None

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


@pytest.fixture()
def redis_mock():
    redis = MagicMock()
    redis.set.return_value = True
    redis.setex.return_value = True
    redis.get.return_value = None
    redis.delete.return_value = None
    return redis


@pytest.fixture(autouse=True)
def _patch_rq(monkeypatch):
    """Patch RQ Queue/Retry with light-weight stand-ins."""

    class DummyQueue:
        def __init__(self, name, connection):
            self.name = name
            self.connection = connection
            self.enqueue = MagicMock()

    monkeypatch.setattr(job_queue, "Queue", DummyQueue)
    monkeypatch.setattr(job_queue, "Retry", MagicMock())


def test_compute_job_id_and_roundtrip():
    config = BacktestJobConfig(
        alpha_name="alpha1",
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 31),
        weight_method="zscore",
        extra_params={"window": 5},
    )
    first = config.compute_job_id("tester")
    second = config.compute_job_id("tester")
    assert first == second

    data = config.to_dict()
    restored = BacktestJobConfig.from_dict(data)
    assert restored.alpha_name == config.alpha_name
    assert restored.start_date == config.start_date
    assert restored.end_date == config.end_date
    assert restored.weight_method == config.weight_method
    assert restored.extra_params == config.extra_params


def test_job_priority_values():
    assert JobPriority.HIGH.value == "high"
    assert JobPriority.NORMAL.value == "normal"
    assert JobPriority.LOW.value == "low"


def _make_queue(redis, db_pool):
    return BacktestJobQueue(redis, db_pool)


def test_enqueue_creates_job_and_db_record(redis_mock):
    db_pool = MagicMock()
    queue = _make_queue(redis_mock, db_pool)
    queue._create_db_job = MagicMock()
    queue._fetch_db_job = MagicMock(return_value=None)
    queue._safe_fetch_job = MagicMock(return_value=None)

    fake_job = MagicMock()
    queue.queues[JobPriority.NORMAL].enqueue.return_value = fake_job

    config = BacktestJobConfig("alpha1", date(2024, 1, 1), date(2024, 1, 31))
    job = queue.enqueue(config, created_by="alice")

    job_id = config.compute_job_id("alice")
    queue._create_db_job.assert_called_once_with(job_id, config, "alice", queue.DEFAULT_TIMEOUT, is_rerun=False)
    queue.queues[JobPriority.NORMAL].enqueue.assert_called_once()
    assert job is fake_job
    redis_mock.delete.assert_called_with(f"backtest:lock:{job_id}")


def test_enqueue_idempotent_returns_existing(redis_mock):
    db_pool = MagicMock()
    queue = _make_queue(redis_mock, db_pool)
    queue._create_db_job = MagicMock()
    queue._fetch_db_job = MagicMock(return_value=None)

    created_job = MagicMock()
    created_job.get_status.return_value = "queued"
    queue.queues[JobPriority.NORMAL].enqueue.return_value = created_job
    queue._safe_fetch_job = MagicMock(side_effect=[None, created_job])

    config = BacktestJobConfig("alpha1", date(2024, 1, 1), date(2024, 1, 31))
    first = queue.enqueue(config, created_by="bob")
    second = queue.enqueue(config, created_by="bob")

    assert first is created_job
    assert second is created_job
    queue._create_db_job.assert_called_once()
    assert queue.queues[JobPriority.NORMAL].enqueue.call_count == 1


@pytest.mark.parametrize("timeout", [299, 14_401])
def test_enqueue_validates_timeout_bounds(redis_mock, timeout):
    db_pool = MagicMock()
    queue = _make_queue(redis_mock, db_pool)
    config = BacktestJobConfig("alpha1", date(2024, 1, 1), date(2024, 1, 31))

    with pytest.raises(ValueError, match="timeout must be between"):
        queue.enqueue(config, timeout=timeout)


def test_get_job_status_prefers_db(redis_mock):
    db_pool = MagicMock()
    queue = _make_queue(redis_mock, db_pool)
    job_id = "job123"
    db_job = {
        "job_id": job_id,
        "status": "running",
        "progress_pct": 40,
        "created_at": datetime(2024, 1, 1, tzinfo=UTC),
        "started_at": datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
        "completed_at": None,
        "error_message": None,
        "result_path": None,
    }
    progress = {"pct": 25, "stage": "loading", "current_date": "2024-01-02", "updated_at": "ts"}
    redis_mock.get.return_value = json.dumps(progress).encode()
    queue._fetch_db_job = MagicMock(return_value=db_job)
    queue._safe_fetch_job = MagicMock(return_value=None)

    status = queue.get_job_status(job_id)

    assert status["status"] == "running"
    assert status["progress_pct"] == progress["pct"]
    assert status["progress_stage"] == progress["stage"]
    assert status["progress_date"] == progress["current_date"]
    assert status["created_at"] == db_job["created_at"].isoformat()  # type: ignore[attr-defined]


def test_cancel_job_updates_db_and_sets_flag(redis_mock):
    job = MagicMock()
    job.get_status.return_value = "queued"

    db_cursor = DummyCursor()
    db_conn = DummyConnection(db_cursor)
    db_pool = MagicMock()
    db_pool.connection.return_value = db_conn

    queue = _make_queue(redis_mock, db_pool)
    queue._safe_fetch_job = MagicMock(return_value=job)
    queue._fetch_db_job = MagicMock(return_value={"status": "pending"})

    result = queue.cancel_job("job123")

    assert result is True
    job.cancel.assert_called_once()
    redis_mock.setex.assert_called_with("backtest:cancel:job123", 3600, "1")
    assert db_cursor.executed  # Update executed
    assert db_conn.commits == 1


def test_watchdog_marks_lost_jobs_failed(redis_mock):
    running_jobs = [{"job_id": "lost_job", "job_timeout": 3600}]
    fetch_cursor = DummyCursor(rows=running_jobs)
    fetch_conn = DummyConnection(fetch_cursor)

    update_cursor = DummyCursor()
    update_conn = DummyConnection(update_cursor)

    db_pool = MagicMock()
    db_pool.connection.side_effect = [fetch_conn, update_conn]

    queue = _make_queue(redis_mock, db_pool)
    redis_mock.get.return_value = None  # Missing heartbeat

    failures = queue.watchdog_fail_lost_jobs()

    assert failures == 1
    assert update_cursor.executed
    assert update_conn.commits == 1


def test_watchdog_handles_missing_job_timeout(redis_mock):
    """Watchdog should not crash if job_timeout is NULL or invalid."""
    running_jobs = [{"job_id": "lost_job", "job_timeout": None}]
    fetch_cursor = DummyCursor(rows=running_jobs)
    fetch_conn = DummyConnection(fetch_cursor)

    update_cursor = DummyCursor()
    update_conn = DummyConnection(update_cursor)

    db_pool = MagicMock()
    db_pool.connection.side_effect = [fetch_conn, update_conn]

    queue = _make_queue(redis_mock, db_pool)
    redis_mock.get.return_value = None  # Missing heartbeat should trigger failure

    failures = queue.watchdog_fail_lost_jobs()

    assert failures == 1
    assert update_cursor.executed
    assert update_conn.commits == 1


def test_enqueue_heals_missing_rq_job(redis_mock):
    db_pool = MagicMock()
    queue = _make_queue(redis_mock, db_pool)
    queue._fetch_db_job = MagicMock(return_value={"status": "running"})
    queue._safe_fetch_job = MagicMock(return_value=None)
    queue._create_db_job = MagicMock()

    config = BacktestJobConfig("alpha1", date(2024, 1, 1), date(2024, 1, 31))
    healed_job = MagicMock()
    queue.queues[JobPriority.NORMAL].enqueue.return_value = healed_job

    result = queue.enqueue(config, created_by="heal_user")

    job_id = config.compute_job_id("heal_user")
    heal_key = f"backtest:heal_count:{job_id}"
    queue._create_db_job.assert_called_once_with(
        job_id, config, "heal_user", queue.DEFAULT_TIMEOUT, is_rerun=False
    )
    queue.queues[JobPriority.NORMAL].enqueue.assert_called_once()
    redis_mock.setex.assert_any_call(heal_key, queue.DEFAULT_TIMEOUT, "1")
    assert result is healed_job


def test_cancel_started_job_sets_redis_flag_only(redis_mock):
    """For running jobs, cancel_job should only set Redis flag (cooperative cancellation)."""
    job = MagicMock()
    job.get_status.return_value = "started"
    job.timeout = 1200

    db_cursor = DummyCursor()
    db_conn = DummyConnection(db_cursor)
    db_pool = MagicMock()
    db_pool.connection.return_value = db_conn

    queue = _make_queue(redis_mock, db_pool)
    queue._safe_fetch_job = MagicMock(return_value=job)
    queue._fetch_db_job = MagicMock(return_value={"status": "running", "job_timeout": 800})

    result = queue.cancel_job("job123")

    assert result is True
    # Redis cancel flag should be set
    redis_mock.setex.assert_called_once()
    # DB should NOT be updated - worker handles status via cooperative cancellation
    assert len(db_cursor.executed) == 0
    assert db_conn.commits == 0


def test_get_job_status_progress_json_error_falls_back(redis_mock):
    db_pool = MagicMock()
    queue = _make_queue(redis_mock, db_pool)
    queue.logger = MagicMock()
    job_id = "job123"
    db_job = {
        "job_id": job_id,
        "status": "running",
        "progress_pct": 55,
        "created_at": None,
        "started_at": None,
        "completed_at": None,
        "error_message": None,
        "result_path": None,
    }
    redis_mock.get.return_value = b"{not-json"
    queue._fetch_db_job = MagicMock(return_value=db_job)
    queue._safe_fetch_job = MagicMock(return_value=None)

    status = queue.get_job_status(job_id)

    assert status["progress_pct"] == 55
    assert status["progress_stage"] == "running"


def test_cancel_started_job_cooperative_cancellation(redis_mock):
    """Cancelling a running job should only set Redis flag for cooperative cancellation."""
    job = MagicMock()
    job.get_status.return_value = "started"
    job.timeout = 3600

    db_cursor = DummyCursor()
    db_conn = DummyConnection(db_cursor)
    db_pool = MagicMock()
    db_pool.connection.return_value = db_conn

    queue = _make_queue(redis_mock, db_pool)
    queue._safe_fetch_job = MagicMock(return_value=job)
    queue._fetch_db_job = MagicMock(return_value={"status": "running", "job_timeout": 3600})

    result = queue.cancel_job("job123")

    assert result is True
    # Verify Redis cancel flag set with appropriate TTL
    redis_mock.setex.assert_called()
    call_args = redis_mock.setex.call_args
    assert "backtest:cancel:job123" in call_args[0]
    # Verify DB is NOT updated - worker handles via cooperative cancellation
    assert len(db_cursor.executed) == 0
    assert db_conn.commits == 0


def test_get_job_status_handles_corrupt_json(redis_mock):
    """JSON parse errors in progress should fall back to DB progress_pct."""
    db_pool = MagicMock()
    queue = _make_queue(redis_mock, db_pool)
    job_id = "job123"
    db_job = {
        "job_id": job_id,
        "status": "running",
        "progress_pct": 50,
        "created_at": datetime(2024, 1, 1, tzinfo=UTC),
        "started_at": datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
        "completed_at": None,
        "error_message": None,
        "result_path": None,
    }
    # Return invalid JSON from Redis
    redis_mock.get.return_value = b"not-valid-json{{"
    queue._fetch_db_job = MagicMock(return_value=db_job)
    queue._safe_fetch_job = MagicMock(return_value=None)

    status = queue.get_job_status(job_id)

    # Should fall back to DB progress_pct
    assert status["status"] == "running"
    assert status["progress_pct"] == 50
    assert status["progress_stage"] == "running"


def test_enqueue_heals_missing_rq_job_preserves_retry_count(redis_mock):
    """When DB shows active but RQ job missing, heal creates new RQ job with is_rerun=False."""
    db_cursor = DummyCursor()
    db_conn = DummyConnection(db_cursor)
    db_pool = MagicMock()
    db_pool.connection.return_value = db_conn

    queue = _make_queue(redis_mock, db_pool)
    config = BacktestJobConfig("alpha1", date(2024, 1, 1), date(2024, 1, 31))
    job_id = config.compute_job_id("alice")

    # First call: DB shows pending but RQ job missing
    db_job_pending = {"job_id": job_id, "status": "pending"}
    queue._fetch_db_job = MagicMock(return_value=db_job_pending)
    queue._safe_fetch_job = MagicMock(return_value=None)
    queue._create_db_job = MagicMock()

    # Heal counter not set yet
    redis_mock.get.side_effect = [None, None]  # heal counter, lock

    healed_job = MagicMock()
    queue.queues[JobPriority.NORMAL].enqueue.return_value = healed_job

    queue.enqueue(config, created_by="alice")

    # Should have set heal counter
    assert redis_mock.setex.called
    # Should have created new RQ job
    assert queue.queues[JobPriority.NORMAL].enqueue.called
    # _create_db_job should be called with is_rerun=False to preserve retry_count
    queue._create_db_job.assert_called()
    call_kwargs = queue._create_db_job.call_args
    assert call_kwargs[1].get("is_rerun") is False or call_kwargs[0][4] is False
