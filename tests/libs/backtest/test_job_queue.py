from __future__ import annotations

import importlib.util
import json
import time
from datetime import UTC, date, datetime
from unittest.mock import MagicMock

import pytest

# Skip the backtest queue tests if optional heavy deps are missing.
_missing = [
    mod
    for mod in (
        "structlog",
        "psutil",
        "polars",
        "duckdb",
        "pydantic_settings",
        "hvac",
        "boto3",
        "botocore",
        "dotenv",
        "sqlalchemy",
        "rq",
    )
    if importlib.util.find_spec(mod) is None
]
if _missing:
    pytest.skip(
        f"Skipping backtest queue tests because dependencies are missing: {', '.join(_missing)}",
        allow_module_level=True,
    )


import libs.backtest.job_queue as job_queue
from libs.backtest.job_queue import BacktestJobConfig, BacktestJobQueue, DataProvider, JobPriority


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
    queue._create_db_job.assert_called_once_with(
        job_id, config, "alice", queue.DEFAULT_TIMEOUT, is_rerun=False
    )
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


@pytest.mark.unit()
@pytest.mark.parametrize(
    ("status", "result", "expected"),
    [
        ("queued", None, ("unknown", None)),
        ("finished", None, ("failed", "RQ job finished but payload missing")),
        ("finished", {"cancelled": True}, ("cancelled", None)),
        ("finished", {"ok": True}, ("completed", None)),
    ],
)
def test_resolve_rq_finished_status(status, result, expected):
    job = MagicMock()
    job.get_status.return_value = status
    job.result = result

    assert job_queue._resolve_rq_finished_status(job) == expected


@pytest.mark.unit()
def test_safe_fetch_job_returns_none_on_missing(monkeypatch, redis_mock):
    def _raise_missing(job_id, connection):
        raise job_queue.NoSuchJobError("missing")

    DummyJob = type("DummyJob", (), {"fetch": staticmethod(_raise_missing)})
    monkeypatch.setattr(job_queue, "Job", DummyJob)
    queue = _make_queue(redis_mock, MagicMock())
    assert queue._safe_fetch_job("missing") is None


@pytest.mark.unit()
def test_fetch_db_job_returns_row(redis_mock):
    row = {"job_id": "abc"}
    cursor = DummyCursor(rows=[row])
    conn = DummyConnection(cursor)
    db_pool = MagicMock()
    db_pool.connection.return_value = conn

    queue = _make_queue(redis_mock, db_pool)
    assert queue._fetch_db_job("abc") == row
    assert cursor.executed[0][0].startswith("SELECT * FROM backtest_jobs")


@pytest.mark.unit()
def test_create_db_job_commits_and_uses_params(redis_mock):
    cursor = DummyCursor()
    conn = DummyConnection(cursor)
    db_pool = MagicMock()
    db_pool.connection.return_value = conn

    queue = _make_queue(redis_mock, db_pool)
    config = BacktestJobConfig("alpha", date(2024, 1, 1), date(2024, 1, 31))
    queue._create_db_job("jid", config, "me", 400, is_rerun=True)

    assert conn.commits == 1
    assert cursor.executed  # upsert executed
    assert cursor.executed[0][1]["is_rerun"] is True


@pytest.mark.unit()
def test_enqueue_lock_contention_raises(redis_mock, monkeypatch):
    """Lock contention polls for job 5 times, then raises if not found."""
    db_pool = MagicMock()
    queue = _make_queue(redis_mock, db_pool)
    queue._safe_fetch_job = MagicMock(return_value=None)  # Job never appears
    queue._fetch_db_job = MagicMock(return_value=None)
    redis_mock.set.return_value = False  # Lock not acquired
    monkeypatch.setattr(time, "sleep", lambda _: None)

    config = BacktestJobConfig("alpha", date(2024, 1, 1), date(2024, 1, 31))
    with pytest.raises(RuntimeError, match="lock contention"):
        queue.enqueue(config, created_by="lock")
    # Lock attempted once, then polling loop (5 iterations)
    assert redis_mock.set.call_count == 1
    assert queue._safe_fetch_job.call_count == 5


@pytest.mark.unit()
def test_enqueue_heal_loop_breaker_after_three(redis_mock):
    db_pool = MagicMock()
    cursor = DummyCursor()
    conn = DummyConnection(cursor)
    db_pool.connection.return_value = conn

    queue = _make_queue(redis_mock, db_pool)
    queue._fetch_db_job = MagicMock(return_value={"status": "running"})
    queue._safe_fetch_job = MagicMock(return_value=None)
    redis_mock.get.return_value = b"3"

    config = BacktestJobConfig("alpha", date(2024, 1, 1), date(2024, 1, 31))
    with pytest.raises(RuntimeError):
        queue.enqueue(config, created_by="heal")
    assert cursor.executed  # failure update executed
    assert conn.commits == 1


@pytest.mark.unit()
def test_get_job_status_db_missing_uses_rq_payload(redis_mock):
    db_pool = MagicMock()
    queue = _make_queue(redis_mock, db_pool)
    queue._fetch_db_job = MagicMock(return_value=None)
    rq_job = MagicMock()
    rq_job.get_status.return_value = "finished"
    rq_job.result = {"cancelled": True}
    queue._safe_fetch_job = MagicMock(return_value=rq_job)

    status = queue.get_job_status("job1")
    assert status["status"] == "cancelled"
    assert status["warning"] == "DB row missing; derived from RQ payload"


@pytest.mark.unit()
def test_cancel_job_orphan_pending_updates_db(redis_mock):
    cursor = DummyCursor()
    conn = DummyConnection(cursor)
    db_pool = MagicMock()
    db_pool.connection.return_value = conn
    queue = _make_queue(redis_mock, db_pool)
    queue._safe_fetch_job = MagicMock(return_value=None)
    queue._fetch_db_job = MagicMock(return_value={"status": "pending"})

    assert queue.cancel_job("jid")
    assert cursor.executed
    assert conn.commits == 1


@pytest.mark.unit()
def test_cancel_job_orphan_running_sets_flag(redis_mock):
    cursor = DummyCursor()
    conn = DummyConnection(cursor)
    db_pool = MagicMock()
    db_pool.connection.return_value = conn
    queue = _make_queue(redis_mock, db_pool)
    queue._safe_fetch_job = MagicMock(return_value=None)
    queue._fetch_db_job = MagicMock(return_value={"status": "running", "job_timeout": 500})

    assert queue.cancel_job("jid")
    redis_mock.setex.assert_called_once()
    assert not cursor.executed


@pytest.mark.unit()
def test_watchdog_skips_when_heartbeat_recent(redis_mock):
    now_iso = datetime.now(UTC).isoformat()
    running_jobs = [{"job_id": "ok_job", "job_timeout": 600}]
    fetch_cursor = DummyCursor(rows=running_jobs)
    fetch_conn = DummyConnection(fetch_cursor)
    db_pool = MagicMock()
    db_pool.connection.return_value = fetch_conn

    redis_mock.get.return_value = now_iso.encode()

    queue = _make_queue(redis_mock, db_pool)
    assert queue.watchdog_fail_lost_jobs() == 0


@pytest.mark.unit()
def test_watchdog_invalid_heartbeat_marks_failed(redis_mock):
    running_jobs = [{"job_id": "bad_job", "job_timeout": 600}]
    fetch_cursor = DummyCursor(rows=running_jobs)
    fetch_conn = DummyConnection(fetch_cursor)

    update_cursor = DummyCursor()
    update_conn = DummyConnection(update_cursor)

    db_pool = MagicMock()
    db_pool.connection.side_effect = [fetch_conn, update_conn]
    redis_mock.get.return_value = b"not-a-date"

    queue = _make_queue(redis_mock, db_pool)
    failures = queue.watchdog_fail_lost_jobs()

    assert failures == 1
    assert update_cursor.executed


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


class TestDataProviderEnum:
    """Tests for DataProvider enum and from_string validation."""

    def test_from_string_valid_crsp(self):
        """Test that 'crsp' string parses to CRSP provider."""
        assert DataProvider.from_string("crsp") == DataProvider.CRSP

    def test_from_string_valid_yfinance(self):
        """Test that 'yfinance' string parses to YFINANCE provider."""
        assert DataProvider.from_string("yfinance") == DataProvider.YFINANCE

    def test_from_string_case_insensitive(self):
        """Test that provider parsing is case-insensitive."""
        assert DataProvider.from_string("CRSP") == DataProvider.CRSP
        assert DataProvider.from_string("Crsp") == DataProvider.CRSP
        assert DataProvider.from_string("YFINANCE") == DataProvider.YFINANCE
        assert DataProvider.from_string("YFinance") == DataProvider.YFINANCE

    def test_from_string_strips_whitespace(self):
        """Test that provider parsing strips leading/trailing whitespace."""
        assert DataProvider.from_string("  crsp  ") == DataProvider.CRSP
        assert DataProvider.from_string("\tyfinance\n") == DataProvider.YFINANCE

    def test_from_string_invalid_raises(self):
        """Test that invalid provider string raises ValueError."""
        with pytest.raises(ValueError, match="Invalid data provider"):
            DataProvider.from_string("invalid_provider")

    def test_from_string_empty_raises(self):
        """Test that empty string raises ValueError."""
        with pytest.raises(ValueError, match="Invalid data provider"):
            DataProvider.from_string("")


class TestBacktestJobConfigFromDict:
    """Tests for BacktestJobConfig.from_dict edge cases."""

    def test_from_dict_provider_none_defaults_to_crsp(self):
        """Test that explicit provider=None defaults to CRSP (not crash)."""
        data = {
            "alpha_name": "test_alpha",
            "start_date": "2024-01-01",
            "end_date": "2024-01-31",
            "provider": None,  # Explicit None
        }
        config = BacktestJobConfig.from_dict(data)
        assert config.provider == DataProvider.CRSP

    def test_from_dict_provider_missing_defaults_to_crsp(self):
        """Test that missing provider key defaults to CRSP."""
        data = {
            "alpha_name": "test_alpha",
            "start_date": "2024-01-01",
            "end_date": "2024-01-31",
            # No provider key at all
        }
        config = BacktestJobConfig.from_dict(data)
        assert config.provider == DataProvider.CRSP

    def test_from_dict_provider_yfinance(self):
        """Test that yfinance provider is correctly parsed."""
        data = {
            "alpha_name": "test_alpha",
            "start_date": "2024-01-01",
            "end_date": "2024-01-31",
            "provider": "yfinance",
        }
        config = BacktestJobConfig.from_dict(data)
        assert config.provider == DataProvider.YFINANCE

    def test_from_dict_invalid_provider_raises(self):
        """Test that invalid provider in dict raises ValueError."""
        data = {
            "alpha_name": "test_alpha",
            "start_date": "2024-01-01",
            "end_date": "2024-01-31",
            "provider": "invalid",
        }
        with pytest.raises(ValueError, match="Invalid data provider"):
            BacktestJobConfig.from_dict(data)
