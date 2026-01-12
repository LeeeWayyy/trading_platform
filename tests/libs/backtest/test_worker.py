from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

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
        f"Skipping backtest worker tests because dependencies are missing: {', '.join(_missing)}",
        allow_module_level=True,
    )

import libs.backtest.worker as worker_module
from libs.alpha.exceptions import JobCancelled
from libs.backtest.worker import BacktestWorker, record_retry


@pytest.fixture()
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


@pytest.mark.unit()
def test_check_cancellation_periodic_updates_heartbeat(redis_with_pipeline, monkeypatch):
    redis, pipeline = redis_with_pipeline
    worker = BacktestWorker(redis, MagicMock())
    worker._last_cancel_check = -100  # force check
    worker.check_cancellation = MagicMock()
    worker.check_memory = MagicMock()
    monkeypatch.setattr(worker, "CANCEL_CHECK_INTERVAL", 0)

    worker.check_cancellation_periodic("job123", job_timeout=500)

    worker.check_cancellation.assert_called_once_with("job123")
    assert pipeline.set.call_args_list[0].args[0] == "backtest:heartbeat:job123"
    pipeline.expire.assert_any_call("backtest:cancel:job123", 3600)


@pytest.mark.unit()
def test_update_db_status_skips_terminal(redis_with_pipeline):
    redis, _ = redis_with_pipeline
    cursor = MagicMock()
    cursor.fetchone.return_value = {"status": "completed"}
    cursor.__enter__.return_value = cursor
    cursor.__exit__.return_value = False

    conn = MagicMock()
    conn.cursor.return_value = cursor
    conn.__enter__.return_value = conn
    conn.__exit__.return_value = False

    pool = MagicMock()
    pool.connection.return_value = conn

    worker = BacktestWorker(redis, pool)
    worker.update_db_status("jid", "running")
    # Only the initial SELECT should run; no update/commit for terminal state
    assert cursor.execute.call_count == 1
    assert conn.commit.call_count == 0


@pytest.mark.unit()
def test_update_db_status_updates_running(redis_with_pipeline):
    redis, _ = redis_with_pipeline
    cursor = MagicMock()
    cursor.fetchone.return_value = {"status": "running"}
    cursor.__enter__.return_value = cursor
    cursor.__exit__.return_value = False

    conn = MagicMock()
    conn.cursor.return_value = cursor
    conn.__enter__.return_value = conn
    conn.__exit__.return_value = False

    pool = MagicMock()
    pool.connection.return_value = conn

    worker = BacktestWorker(redis, pool)
    worker.update_db_status("jid", "completed", result_path="p")
    # Expect select + update
    assert cursor.execute.call_count == 2
    conn.commit.assert_called_once()


@pytest.mark.unit()
def test_update_db_progress_persists(redis_with_pipeline):
    redis, _ = redis_with_pipeline
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.__exit__.return_value = False

    conn = MagicMock()
    conn.cursor.return_value = cursor
    conn.__enter__.return_value = conn
    conn.__exit__.return_value = False

    pool = MagicMock()
    pool.connection.return_value = conn

    worker = BacktestWorker(redis, pool)
    worker.update_db_progress("jid", 50)
    cursor.execute.assert_called_once()
    conn.commit.assert_called_once()


@pytest.mark.unit()
def test_get_worker_pool_singleton_and_close(monkeypatch):
    created = []

    class DummyPool:
        def __init__(self, *args, **kwargs):
            created.append(self)

        def open(self):
            self.opened = True

        def close(self):
            self.closed = True

        def connection(self):
            raise RuntimeError("not used")

    monkeypatch.setenv("DATABASE_URL", "postgres://test")
    monkeypatch.setattr(worker_module, "ConnectionPool", DummyPool)
    worker_module._WORKER_POOL = None

    pool1 = worker_module._get_worker_pool()
    pool2 = worker_module._get_worker_pool()
    assert pool1 is pool2
    assert len(created) == 1
    worker_module._close_worker_pool()
    assert worker_module._WORKER_POOL is None


@pytest.mark.unit()
def test_close_worker_pool_logs_os_error(monkeypatch):
    class FailingPool:
        def close(self):
            raise OSError("Connection reset")

    worker_module._WORKER_POOL = FailingPool()
    mock_logger = MagicMock()
    monkeypatch.setattr(worker_module.structlog, "get_logger", lambda *_args: mock_logger)
    worker_module._close_worker_pool()
    assert worker_module._WORKER_POOL is None
    mock_logger.warning.assert_called_once()
    call_args = mock_logger.warning.call_args
    assert "OS error" in call_args[0][0]


@pytest.mark.unit()
def test_close_worker_pool_logs_runtime_error(monkeypatch):
    class FailingPool:
        def close(self):
            raise RuntimeError("Pool already closed")

    worker_module._WORKER_POOL = FailingPool()
    mock_logger = MagicMock()
    monkeypatch.setattr(worker_module.structlog, "get_logger", lambda *_args: mock_logger)
    worker_module._close_worker_pool()
    assert worker_module._WORKER_POOL is None
    mock_logger.warning.assert_called_once()
    call_args = mock_logger.warning.call_args
    assert "runtime error" in call_args[0][0]


@pytest.mark.unit()
def test_close_worker_pool_logs_value_error(monkeypatch):
    class FailingPool:
        def close(self):
            raise ValueError("Invalid pool state")

    worker_module._WORKER_POOL = FailingPool()
    mock_logger = MagicMock()
    monkeypatch.setattr(worker_module.structlog, "get_logger", lambda *_args: mock_logger)
    worker_module._close_worker_pool()
    assert worker_module._WORKER_POOL is None
    mock_logger.warning.assert_called_once()
    call_args = mock_logger.warning.call_args
    assert "invalid state" in call_args[0][0]


@pytest.mark.unit()
def test_run_backtest_success(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "postgres://test")
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    fake_conn.__exit__.return_value = False
    fake_conn.cursor.return_value = MagicMock()

    class DummyPool:
        def connection(self):
            return fake_conn

    redis_pipeline = MagicMock()
    redis_pipeline.set.return_value = redis_pipeline
    redis_pipeline.expire.return_value = redis_pipeline
    redis_pipeline.execute.return_value = None
    redis = MagicMock()
    redis.pipeline.return_value = redis_pipeline
    redis.exists.return_value = 0
    monkeypatch.setattr(worker_module.Redis, "from_url", lambda *_args, **_kwargs: redis)
    monkeypatch.setattr(worker_module, "_get_worker_pool", lambda: DummyPool())
    monkeypatch.setattr(worker_module, "ManifestManager", MagicMock())
    monkeypatch.setattr(worker_module, "DatasetVersionManager", MagicMock())
    monkeypatch.setattr(worker_module, "CRSPLocalProvider", MagicMock())
    monkeypatch.setattr(worker_module, "CompustatLocalProvider", MagicMock())
    monkeypatch.setattr(worker_module, "AlphaMetricsAdapter", MagicMock())
    monkeypatch.setattr(worker_module, "create_alpha", lambda name: f"alpha-{name}")

    class DummyBacktester:
        def __init__(self, *_, **__):
            pass

        def run_backtest(self, *_, **__):
            return types.SimpleNamespace(
                mean_ic=0.1,
                icir=0.2,
                hit_rate=0.3,
                coverage=0.4,
                long_short_spread=0.5,
                average_turnover=0.6,
                decay_half_life=0.7,
                snapshot_id="snap",
                dataset_version_ids={"ds": 1},
                daily_signals=MagicMock(),
                daily_weights=MagicMock(),
                daily_ic=MagicMock(),
            )

    monkeypatch.setattr(worker_module, "PITBacktester", DummyBacktester)
    monkeypatch.setattr(worker_module, "_save_parquet_artifacts", lambda *args, **kwargs: tmp_path)
    monkeypatch.setattr(worker_module, "_save_result_to_db", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        worker_module, "get_current_job", lambda: types.SimpleNamespace(timeout=500)
    )
    monkeypatch.setattr(worker_module.BacktestWorker, "check_memory", lambda self: None)

    result = worker_module.run_backtest(
        {"alpha_name": "a1", "start_date": "2024-01-01", "end_date": "2024-01-02"}, created_by="me"
    )

    assert result["job_id"]
    assert "summary_metrics" in result


@pytest.mark.unit()
def test_run_backtest_handles_cancel(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "postgres://test")

    class DummyPool:
        def connection(self):
            conn = MagicMock()
            conn.__enter__.return_value = conn
            conn.__exit__.return_value = False
            conn.cursor.return_value = MagicMock()
            return conn

    redis_pipeline = MagicMock()
    redis_pipeline.set.return_value = redis_pipeline
    redis_pipeline.expire.return_value = redis_pipeline
    redis_pipeline.execute.return_value = None

    redis = MagicMock()
    redis.get.return_value = json.dumps({"pct": 30}).encode()
    redis.pipeline.return_value = redis_pipeline
    redis.exists.return_value = 1

    monkeypatch.setattr(worker_module.Redis, "from_url", lambda *_a, **_k: redis)
    monkeypatch.setattr(worker_module, "_get_worker_pool", lambda: DummyPool())
    monkeypatch.setattr(worker_module, "ManifestManager", MagicMock())
    monkeypatch.setattr(worker_module, "DatasetVersionManager", MagicMock())
    monkeypatch.setattr(worker_module, "CRSPLocalProvider", MagicMock())
    monkeypatch.setattr(worker_module, "CompustatLocalProvider", MagicMock())
    monkeypatch.setattr(worker_module, "AlphaMetricsAdapter", MagicMock())
    monkeypatch.setattr(worker_module, "create_alpha", lambda name: f"alpha-{name}")

    class DummyBacktester:
        def __init__(self, *_, **__):
            pass

        def run_backtest(self, *_, **__):
            raise JobCancelled("stop")

    monkeypatch.setattr(worker_module, "PITBacktester", DummyBacktester)
    monkeypatch.setattr(worker_module, "_save_parquet_artifacts", lambda *_, **__: tmp_path)
    monkeypatch.setattr(worker_module, "_save_result_to_db", lambda *_, **__: None)
    monkeypatch.setattr(
        worker_module, "get_current_job", lambda: types.SimpleNamespace(timeout=400)
    )
    monkeypatch.setattr(worker_module.shutil, "rmtree", lambda *_, **__: None)

    result = worker_module.run_backtest(
        {"alpha_name": "a1", "start_date": "2024-01-01", "end_date": "2024-01-02"}, created_by="me"
    )

    assert result["cancelled"] is True


@pytest.mark.unit()
def test_save_parquet_artifacts_validates_daily_ic(monkeypatch, tmp_path):
    dtype_map = {
        "date": "Date",
        "permno": "Int64",
        "signal": "Float64",
        "weight": "Float64",
        "ic": "Float64",
        "rank_ic": "Float64",
    }

    class DummyDF:
        columns = ["date", "permno", "signal", "weight", "ic", "rank_ic"]

        def __getitem__(self, key):
            return MagicMock(dtype=dtype_map[key])

        def select(self, *_args):
            return self

        def cast(self, *_args, **_kwargs):
            return self

        def write_parquet(self, *_args, **_kwargs):
            return None

    dummy_df = DummyDF()

    fake_result = types.SimpleNamespace(
        daily_signals=dummy_df,
        daily_weights=dummy_df,
        daily_ic=None,
    )

    class DummyPolars(types.SimpleNamespace):
        Date = "Date"
        Int64 = "Int64"
        Float64 = "Float64"

    monkeypatch.setitem(sys.modules, "polars", DummyPolars())
    with pytest.raises(ValueError, match="parquet export"):
        worker_module._save_parquet_artifacts("jid", fake_result)  # type: ignore[arg-type]


@pytest.mark.unit()
def test_save_result_to_db_errors_when_missing_metadata():
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.__exit__.return_value = False
    conn = MagicMock()
    conn.cursor.return_value = cursor
    conn.__enter__.return_value = conn
    conn.__exit__.return_value = False

    incomplete = types.SimpleNamespace(
        snapshot_id=None,
        dataset_version_ids=None,
    )

    with pytest.raises(ValueError, match="reproducibility"):
        worker_module._save_result_to_db(conn, "jid", incomplete, Path("p"))


@pytest.mark.unit()
def test_save_result_to_db_commits_on_success():
    cursor = MagicMock()
    cursor.rowcount = 1
    cursor.__enter__.return_value = cursor
    cursor.__exit__.return_value = False
    conn = MagicMock()
    conn.cursor.return_value = cursor
    conn.__enter__.return_value = conn
    conn.__exit__.return_value = False

    result = types.SimpleNamespace(
        mean_ic=0.1,
        icir=0.2,
        hit_rate=0.3,
        coverage=0.4,
        long_short_spread=0.5,
        average_turnover=0.6,
        decay_half_life=0.7,
        snapshot_id="snap",
        dataset_version_ids={"ds": 1},
    )

    worker_module._save_result_to_db(conn, "jid", result, Path("p"))
    assert conn.commit.called


@pytest.mark.unit()
def test_get_retry_pool_requires_env(monkeypatch):
    worker_module._RETRY_POOL = None
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError):
        worker_module._get_retry_pool()


@pytest.mark.unit()
def test_check_memory_allows_within_limit(redis_with_pipeline):
    redis, _ = redis_with_pipeline
    worker = BacktestWorker(redis, MagicMock())
    worker.MAX_RSS_BYTES = 10_000
    worker.process = MagicMock()
    worker.process.memory_info.return_value = MagicMock(rss=5_000)
    worker.check_memory()  # should not raise


@pytest.mark.unit()
def test_run_backtest_failure_sets_status(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgres://test")

    class DummyPool:
        def connection(self):
            conn = MagicMock()
            conn.__enter__.return_value = conn
            conn.__exit__.return_value = False
            conn.cursor.return_value = MagicMock()
            return conn

    redis_pipeline = MagicMock()
    redis_pipeline.set.return_value = redis_pipeline
    redis_pipeline.expire.return_value = redis_pipeline
    redis_pipeline.execute.return_value = None
    redis = MagicMock()
    redis.pipeline.return_value = redis_pipeline
    redis.exists.return_value = 0

    worker_update = MagicMock()
    monkeypatch.setattr(worker_module.BacktestWorker, "update_db_status", worker_update)
    monkeypatch.setattr(worker_module.BacktestWorker, "check_memory", lambda self: None)
    monkeypatch.setattr(worker_module.BacktestWorker, "check_cancellation", lambda *a, **k: None)
    monkeypatch.setattr(worker_module.Redis, "from_url", lambda *_a, **_k: redis)
    monkeypatch.setattr(worker_module, "_get_worker_pool", lambda: DummyPool())
    monkeypatch.setattr(worker_module, "ManifestManager", MagicMock())
    monkeypatch.setattr(worker_module, "DatasetVersionManager", MagicMock())
    monkeypatch.setattr(worker_module, "CRSPLocalProvider", MagicMock())
    monkeypatch.setattr(worker_module, "CompustatLocalProvider", MagicMock())
    monkeypatch.setattr(worker_module, "AlphaMetricsAdapter", MagicMock())
    monkeypatch.setattr(worker_module, "create_alpha", lambda name: f"alpha-{name}")

    class FailingBacktester:
        def __init__(self, *_, **__):
            pass

        def run_backtest(self, *_, **__):
            raise RuntimeError("boom")

    monkeypatch.setattr(worker_module, "PITBacktester", FailingBacktester)
    monkeypatch.setattr(worker_module, "_save_parquet_artifacts", lambda *_, **__: Path("p"))
    monkeypatch.setattr(worker_module, "_save_result_to_db", lambda *_, **__: None)
    monkeypatch.setattr(
        worker_module, "get_current_job", lambda: types.SimpleNamespace(timeout=400)
    )

    with pytest.raises(RuntimeError):
        worker_module.run_backtest(
            {"alpha_name": "a1", "start_date": "2024-01-01", "end_date": "2024-01-02"},
            created_by="me",
        )
    assert worker_update.called


@pytest.mark.unit()
def test_save_parquet_artifacts_success(monkeypatch, tmp_path):
    class DummyDF:
        columns = ["date", "permno", "signal", "weight", "ic", "rank_ic"]

        def __init__(self):
            self.dtype_map = {
                "date": "Date",
                "permno": "Int64",
                "signal": "Float64",
                "weight": "Float64",
                "ic": "Float64",
                "rank_ic": "Float64",
            }

        def __getitem__(self, key):
            return MagicMock(dtype=self.dtype_map[key])

        def select(self, *_args):
            return self

        def cast(self, *_args, **_kwargs):
            return self

        def write_parquet(self, path, *_, **__):
            Path(path).touch()

    class DummyPortfolioDF(DummyDF):
        def __init__(self):
            self.columns = ["date", "return"]
            self.dtype_map = {"date": "Date", "return": "Float64"}

    class DummyPolars(types.SimpleNamespace):
        Date = "Date"
        Int64 = "Int64"
        Float64 = "Float64"

    monkeypatch.setitem(sys.modules, "polars", DummyPolars())

    result = types.SimpleNamespace(
        daily_signals=DummyDF(),
        daily_weights=DummyDF(),
        daily_ic=DummyDF(),
        daily_portfolio_returns=DummyPortfolioDF(),
        mean_ic=0.1,
        icir=0.2,
        hit_rate=0.3,
        snapshot_id="snap",
        dataset_version_ids={"ds": 1},
    )

    path = worker_module._save_parquet_artifacts("jid", result)  # type: ignore[arg-type]
    assert (path / "summary.json").exists()


@pytest.mark.unit()
def test_get_worker_pool_requires_env(monkeypatch):
    worker_module._WORKER_POOL = None
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError):
        worker_module._get_worker_pool()


@pytest.mark.unit()
def test_check_cancellation_no_flag(redis_with_pipeline):
    redis, _ = redis_with_pipeline
    redis.exists.return_value = 0
    worker = BacktestWorker(redis, MagicMock())
    worker.check_cancellation("job123")  # should not raise
    redis.delete.assert_not_called()


@pytest.mark.unit()
def test_get_retry_pool_creates_pool(monkeypatch):
    worker_module._RETRY_POOL = None
    created = []

    class DummyPool:
        def __init__(self, *args, **kwargs):
            created.append(self)

        def open(self):
            self.opened = True

        def connection(self):
            raise RuntimeError("unused")

    monkeypatch.setenv("DATABASE_URL", "postgres://test")
    monkeypatch.setattr(worker_module, "ConnectionPool", DummyPool)
    pool = worker_module._get_retry_pool()
    assert pool is created[0]
    assert getattr(pool, "opened", False)


@pytest.mark.unit()
def test_get_worker_pool_creates_pool(monkeypatch):
    worker_module._WORKER_POOL = None
    created = []

    class DummyPool:
        def __init__(self, *args, **kwargs):
            created.append(self)

        def open(self):
            self.opened = True

        def close(self):
            self.closed = True

        def connection(self):
            raise RuntimeError("unused")

    monkeypatch.setenv("DATABASE_URL", "postgres://test")
    monkeypatch.setattr(worker_module, "ConnectionPool", DummyPool)
    pool = worker_module._get_worker_pool()
    assert pool is created[0]
    assert getattr(pool, "opened", False)
    worker_module._close_worker_pool()
    assert worker_module._WORKER_POOL is None


@pytest.mark.unit()
def test_run_backtest_handles_cancel_bad_progress(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "postgres://test")

    class DummyPool:
        def connection(self):
            conn = MagicMock()
            conn.__enter__.return_value = conn
            conn.__exit__.return_value = False
            conn.cursor.return_value = MagicMock()
            return conn

    redis_pipeline = MagicMock()
    redis_pipeline.set.return_value = redis_pipeline
    redis_pipeline.expire.return_value = redis_pipeline
    redis_pipeline.execute.return_value = None
    redis = MagicMock()
    redis.get.return_value = b"{bad-json"
    redis.pipeline.return_value = redis_pipeline
    redis.exists.return_value = 1

    monkeypatch.setattr(worker_module.Redis, "from_url", lambda *_a, **_k: redis)
    monkeypatch.setattr(worker_module, "_get_worker_pool", lambda: DummyPool())
    monkeypatch.setattr(worker_module, "ManifestManager", MagicMock())
    monkeypatch.setattr(worker_module, "DatasetVersionManager", MagicMock())
    monkeypatch.setattr(worker_module, "CRSPLocalProvider", MagicMock())
    monkeypatch.setattr(worker_module, "CompustatLocalProvider", MagicMock())
    monkeypatch.setattr(worker_module, "AlphaMetricsAdapter", MagicMock())
    monkeypatch.setattr(worker_module, "create_alpha", lambda name: f"alpha-{name}")

    class CancellingBacktester:
        def __init__(self, *_, **__):
            pass

        def run_backtest(self, *_, **__):
            raise JobCancelled("stop")

    monkeypatch.setattr(worker_module, "PITBacktester", CancellingBacktester)
    monkeypatch.setattr(worker_module, "_save_parquet_artifacts", lambda *_, **__: tmp_path)
    monkeypatch.setattr(worker_module, "_save_result_to_db", lambda *_, **__: None)
    monkeypatch.setattr(
        worker_module, "get_current_job", lambda: types.SimpleNamespace(timeout=400)
    )
    monkeypatch.setattr(worker_module.shutil, "rmtree", lambda *_, **__: None)
    monkeypatch.setattr(worker_module.BacktestWorker, "check_memory", lambda self: None)
    result = worker_module.run_backtest(
        {"alpha_name": "a1", "start_date": "2024-01-01", "end_date": "2024-01-02"}, created_by="me"
    )
    assert result["cancelled"] is True


@pytest.mark.unit()
def test_save_result_to_db_raises_when_missing_row(monkeypatch):
    cursor = MagicMock()
    cursor.rowcount = 0
    cursor.__enter__.return_value = cursor
    cursor.__exit__.return_value = False
    conn = MagicMock()
    conn.cursor.return_value = cursor
    conn.__enter__.return_value = conn
    conn.__exit__.return_value = False

    result = types.SimpleNamespace(
        mean_ic=0.1,
        icir=0.2,
        hit_rate=0.3,
        coverage=0.4,
        long_short_spread=0.5,
        average_turnover=0.6,
        decay_half_life=0.7,
        snapshot_id="snap",
        dataset_version_ids={"ds": 1},
    )

    with pytest.raises(RuntimeError):
        worker_module._save_result_to_db(conn, "jid", result, Path("p"))


# =============================================================================
# Provider Routing Tests
# =============================================================================


class TestProviderRouting:
    """Tests for data provider routing in run_backtest."""

    @pytest.mark.unit()
    def test_crsp_provider_uses_pit_backtester(self, monkeypatch, tmp_path):
        """Test that CRSP provider routes to PITBacktester."""
        monkeypatch.setenv("DATABASE_URL", "postgres://test")
        monkeypatch.setenv("ENVIRONMENT", "development")

        class DummyPool:
            def connection(self):
                conn = MagicMock()
                conn.__enter__.return_value = conn
                conn.__exit__.return_value = False
                conn.cursor.return_value = MagicMock()
                return conn

        pit_backtester_called = []

        class MockPITBacktester:
            def __init__(self, *args, **kwargs):
                pit_backtester_called.append(True)

            def run_backtest(self, *args, **kwargs):
                return types.SimpleNamespace(
                    mean_ic=0.1, icir=0.2, hit_rate=0.3, coverage=0.4,
                    long_short_spread=0.5, average_turnover=0.6, decay_half_life=10,
                    snapshot_id="snap", dataset_version_ids={"ds": 1},
                    daily_signals=MagicMock(), daily_weights=MagicMock(),
                    daily_ic=MagicMock(), daily_portfolio_returns=MagicMock(),
                )

        redis_pipeline = MagicMock()
        redis_pipeline.set.return_value = redis_pipeline
        redis_pipeline.expire.return_value = redis_pipeline
        redis_pipeline.execute.return_value = None
        redis = MagicMock()
        redis.get.return_value = None
        redis.pipeline.return_value = redis_pipeline
        redis.exists.return_value = 0

        monkeypatch.setattr(worker_module.Redis, "from_url", lambda *_a, **_k: redis)
        monkeypatch.setattr(worker_module, "_get_worker_pool", lambda: DummyPool())
        monkeypatch.setattr(worker_module, "ManifestManager", MagicMock())
        monkeypatch.setattr(worker_module, "DatasetVersionManager", MagicMock())
        monkeypatch.setattr(worker_module, "CRSPLocalProvider", MagicMock())
        monkeypatch.setattr(worker_module, "CompustatLocalProvider", MagicMock())
        monkeypatch.setattr(worker_module, "AlphaMetricsAdapter", MagicMock())
        monkeypatch.setattr(worker_module, "create_alpha", lambda name: MagicMock(name=name))
        monkeypatch.setattr(worker_module, "PITBacktester", MockPITBacktester)
        monkeypatch.setattr(worker_module, "_save_parquet_artifacts", lambda *_, **__: tmp_path)
        monkeypatch.setattr(worker_module, "_save_result_to_db", lambda *_, **__: None)
        monkeypatch.setattr(
            worker_module, "get_current_job", lambda: types.SimpleNamespace(timeout=400)
        )
        monkeypatch.setattr(worker_module.BacktestWorker, "check_memory", lambda self: None)

        worker_module.run_backtest(
            {
                "alpha_name": "test",
                "start_date": "2024-01-01",
                "end_date": "2024-01-31",
                "provider": "crsp",  # Explicit CRSP provider
            },
            created_by="test_user",
        )

        assert len(pit_backtester_called) == 1, "PITBacktester should be used for CRSP"

    @pytest.mark.unit()
    def test_yfinance_provider_uses_simple_backtester(self, monkeypatch, tmp_path):
        """Test that Yahoo Finance provider routes to SimpleBacktester."""
        monkeypatch.setenv("DATABASE_URL", "postgres://test")
        monkeypatch.setenv("ENVIRONMENT", "development")

        class DummyPool:
            def connection(self):
                conn = MagicMock()
                conn.__enter__.return_value = conn
                conn.__exit__.return_value = False
                conn.cursor.return_value = MagicMock()
                return conn

        simple_backtester_called = []

        class MockSimpleBacktester:
            def __init__(self, *args, **kwargs):
                simple_backtester_called.append(True)

            def run_backtest(self, *args, **kwargs):
                return types.SimpleNamespace(
                    mean_ic=0.1, icir=0.2, hit_rate=0.3, coverage=0.4,
                    long_short_spread=0.5, average_turnover=0.6, decay_half_life=10,
                    snapshot_id="snap", dataset_version_ids={"ds": 1},
                    daily_signals=MagicMock(), daily_weights=MagicMock(),
                    daily_ic=MagicMock(), daily_portfolio_returns=MagicMock(),
                )

        redis_pipeline = MagicMock()
        redis_pipeline.set.return_value = redis_pipeline
        redis_pipeline.expire.return_value = redis_pipeline
        redis_pipeline.execute.return_value = None
        redis = MagicMock()
        redis.get.return_value = None
        redis.pipeline.return_value = redis_pipeline
        redis.exists.return_value = 0

        monkeypatch.setattr(worker_module.Redis, "from_url", lambda *_a, **_k: redis)
        monkeypatch.setattr(worker_module, "_get_worker_pool", lambda: DummyPool())
        monkeypatch.setattr(worker_module, "AlphaMetricsAdapter", MagicMock())
        monkeypatch.setattr(worker_module, "create_alpha", lambda name: MagicMock(name=name))
        monkeypatch.setattr(worker_module, "YFinanceProvider", MagicMock())
        monkeypatch.setattr(worker_module, "UnifiedDataFetcher", MagicMock())
        monkeypatch.setattr(worker_module, "FetcherConfig", MagicMock())
        monkeypatch.setattr(worker_module, "SimpleBacktester", MockSimpleBacktester)
        monkeypatch.setattr(worker_module, "_save_parquet_artifacts", lambda *_, **__: tmp_path)
        monkeypatch.setattr(worker_module, "_save_result_to_db", lambda *_, **__: None)
        monkeypatch.setattr(
            worker_module, "get_current_job", lambda: types.SimpleNamespace(timeout=400)
        )
        monkeypatch.setattr(worker_module.BacktestWorker, "check_memory", lambda self: None)

        worker_module.run_backtest(
            {
                "alpha_name": "test",
                "start_date": "2024-01-01",
                "end_date": "2024-01-31",
                "provider": "yfinance",  # Yahoo Finance provider
            },
            created_by="test_user",
        )

        assert len(simple_backtester_called) == 1, "SimpleBacktester should be used for yfinance"

    @pytest.mark.unit()
    def test_yfinance_blocked_in_production(self, monkeypatch):
        """Test that Yahoo Finance provider is blocked in production environment."""
        monkeypatch.setenv("DATABASE_URL", "postgres://test")
        monkeypatch.setenv("ENVIRONMENT", "production")  # Production mode

        class DummyPool:
            def connection(self):
                conn = MagicMock()
                conn.__enter__.return_value = conn
                conn.__exit__.return_value = False
                conn.cursor.return_value = MagicMock()
                return conn

        redis_pipeline = MagicMock()
        redis_pipeline.set.return_value = redis_pipeline
        redis_pipeline.expire.return_value = redis_pipeline
        redis_pipeline.execute.return_value = None
        redis = MagicMock()
        redis.get.return_value = None
        redis.pipeline.return_value = redis_pipeline
        redis.exists.return_value = 0

        monkeypatch.setattr(worker_module.Redis, "from_url", lambda *_a, **_k: redis)
        monkeypatch.setattr(worker_module, "_get_worker_pool", lambda: DummyPool())
        monkeypatch.setattr(worker_module, "AlphaMetricsAdapter", MagicMock())
        monkeypatch.setattr(worker_module, "create_alpha", lambda name: MagicMock(name=name))
        monkeypatch.setattr(
            worker_module, "get_current_job", lambda: types.SimpleNamespace(timeout=400)
        )
        monkeypatch.setattr(worker_module.BacktestWorker, "check_memory", lambda self: None)

        with pytest.raises(ValueError, match="not allowed in production"):
            worker_module.run_backtest(
                {
                    "alpha_name": "test",
                    "start_date": "2024-01-01",
                    "end_date": "2024-01-31",
                    "provider": "yfinance",
                },
                created_by="test_user",
            )

    @pytest.mark.unit()
    def test_invalid_provider_raises_error(self, monkeypatch):
        """Test that invalid provider strings raise ValueError."""
        monkeypatch.setenv("DATABASE_URL", "postgres://test")

        class DummyPool:
            def connection(self):
                conn = MagicMock()
                conn.__enter__.return_value = conn
                conn.__exit__.return_value = False
                return conn

        monkeypatch.setattr(worker_module.Redis, "from_url", MagicMock())
        monkeypatch.setattr(worker_module, "_get_worker_pool", lambda: DummyPool())

        # Invalid provider should fail during config parsing
        with pytest.raises(ValueError, match="Invalid data provider"):
            worker_module.run_backtest(
                {
                    "alpha_name": "test",
                    "start_date": "2024-01-01",
                    "end_date": "2024-01-31",
                    "provider": "invalid_provider",  # Invalid
                },
                created_by="test_user",
            )

    @pytest.mark.unit()
    @pytest.mark.parametrize("env_value", ["PRODUCTION", "Production", "PrOdUcTiOn"])
    def test_yfinance_blocked_in_production_case_insensitive(self, monkeypatch, env_value):
        """Test that Yahoo Finance is blocked regardless of ENVIRONMENT case."""
        monkeypatch.setenv("DATABASE_URL", "postgres://test")
        monkeypatch.setenv("ENVIRONMENT", env_value)  # Various casings

        class DummyPool:
            def connection(self):
                conn = MagicMock()
                conn.__enter__.return_value = conn
                conn.__exit__.return_value = False
                conn.cursor.return_value = MagicMock()
                return conn

        redis_pipeline = MagicMock()
        redis_pipeline.set.return_value = redis_pipeline
        redis_pipeline.expire.return_value = redis_pipeline
        redis_pipeline.execute.return_value = None
        redis = MagicMock()
        redis.get.return_value = None
        redis.pipeline.return_value = redis_pipeline
        redis.exists.return_value = 0

        monkeypatch.setattr(worker_module.Redis, "from_url", lambda *_a, **_k: redis)
        monkeypatch.setattr(worker_module, "_get_worker_pool", lambda: DummyPool())
        monkeypatch.setattr(worker_module, "AlphaMetricsAdapter", MagicMock())
        monkeypatch.setattr(worker_module, "create_alpha", lambda name: MagicMock(name=name))
        monkeypatch.setattr(
            worker_module, "get_current_job", lambda: types.SimpleNamespace(timeout=400)
        )
        monkeypatch.setattr(worker_module.BacktestWorker, "check_memory", lambda self: None)

        with pytest.raises(ValueError, match="not allowed in production"):
            worker_module.run_backtest(
                {
                    "alpha_name": "test",
                    "start_date": "2024-01-01",
                    "end_date": "2024-01-31",
                    "provider": "yfinance",
                },
                created_by="test_user",
            )

    @pytest.mark.unit()
    def test_universe_normalization(self, monkeypatch, tmp_path):
        """Test that universe input is normalized (strip, upper, filter empties)."""
        monkeypatch.setenv("DATABASE_URL", "postgres://test")
        monkeypatch.setenv("ENVIRONMENT", "development")

        class DummyPool:
            def connection(self):
                conn = MagicMock()
                conn.__enter__.return_value = conn
                conn.__exit__.return_value = False
                conn.cursor.return_value = MagicMock()
                return conn

        captured_universe = []

        class MockSimpleBacktester:
            def __init__(self, *args, **kwargs):
                pass

            def run_backtest(self, *args, universe=None, **kwargs):
                captured_universe.extend(universe or [])
                return types.SimpleNamespace(
                    mean_ic=0.1, icir=0.2, hit_rate=0.3, coverage=0.4,
                    long_short_spread=0.5, average_turnover=0.6, decay_half_life=10,
                    snapshot_id="snap", dataset_version_ids={"ds": 1},
                    daily_signals=MagicMock(), daily_weights=MagicMock(),
                    daily_ic=MagicMock(), daily_portfolio_returns=MagicMock(),
                )

        redis_pipeline = MagicMock()
        redis_pipeline.set.return_value = redis_pipeline
        redis_pipeline.expire.return_value = redis_pipeline
        redis_pipeline.execute.return_value = None
        redis = MagicMock()
        redis.get.return_value = None
        redis.pipeline.return_value = redis_pipeline
        redis.exists.return_value = 0

        monkeypatch.setattr(worker_module.Redis, "from_url", lambda *_a, **_k: redis)
        monkeypatch.setattr(worker_module, "_get_worker_pool", lambda: DummyPool())
        monkeypatch.setattr(worker_module, "AlphaMetricsAdapter", MagicMock())
        monkeypatch.setattr(worker_module, "create_alpha", lambda name: MagicMock(name=name))
        monkeypatch.setattr(worker_module, "YFinanceProvider", MagicMock())
        monkeypatch.setattr(worker_module, "UnifiedDataFetcher", MagicMock())
        monkeypatch.setattr(worker_module, "FetcherConfig", MagicMock())
        monkeypatch.setattr(worker_module, "SimpleBacktester", MockSimpleBacktester)
        monkeypatch.setattr(worker_module, "_save_parquet_artifacts", lambda *_, **__: tmp_path)
        monkeypatch.setattr(worker_module, "_save_result_to_db", lambda *_, **__: None)
        monkeypatch.setattr(
            worker_module, "get_current_job", lambda: types.SimpleNamespace(timeout=400)
        )
        monkeypatch.setattr(worker_module.BacktestWorker, "check_memory", lambda self: None)

        worker_module.run_backtest(
            {
                "alpha_name": "test",
                "start_date": "2024-01-01",
                "end_date": "2024-01-31",
                "provider": "yfinance",
                "extra_params": {
                    "universe": "  aapl , msft,  ,googl  ",  # Messy input
                },
            },
            created_by="test_user",
        )

        # Should be normalized to uppercase with whitespace stripped
        assert captured_universe == ["AAPL", "MSFT", "GOOGL"]

    @pytest.mark.unit()
    def test_empty_universe_after_normalization_raises(self, monkeypatch):
        """Test that empty universe after normalization raises error."""
        monkeypatch.setenv("DATABASE_URL", "postgres://test")
        monkeypatch.setenv("ENVIRONMENT", "development")

        class DummyPool:
            def connection(self):
                conn = MagicMock()
                conn.__enter__.return_value = conn
                conn.__exit__.return_value = False
                conn.cursor.return_value = MagicMock()
                return conn

        redis_pipeline = MagicMock()
        redis_pipeline.set.return_value = redis_pipeline
        redis_pipeline.expire.return_value = redis_pipeline
        redis_pipeline.execute.return_value = None
        redis = MagicMock()
        redis.get.return_value = None
        redis.pipeline.return_value = redis_pipeline
        redis.exists.return_value = 0

        monkeypatch.setattr(worker_module.Redis, "from_url", lambda *_a, **_k: redis)
        monkeypatch.setattr(worker_module, "_get_worker_pool", lambda: DummyPool())
        monkeypatch.setattr(worker_module, "AlphaMetricsAdapter", MagicMock())
        monkeypatch.setattr(worker_module, "create_alpha", lambda name: MagicMock(name=name))
        monkeypatch.setattr(worker_module, "YFinanceProvider", MagicMock())
        monkeypatch.setattr(worker_module, "UnifiedDataFetcher", MagicMock())
        monkeypatch.setattr(worker_module, "FetcherConfig", MagicMock())
        monkeypatch.setattr(
            worker_module, "get_current_job", lambda: types.SimpleNamespace(timeout=400)
        )
        monkeypatch.setattr(worker_module.BacktestWorker, "check_memory", lambda self: None)

        with pytest.raises(ValueError, match="Universe cannot be empty"):
            worker_module.run_backtest(
                {
                    "alpha_name": "test",
                    "start_date": "2024-01-01",
                    "end_date": "2024-01-31",
                    "provider": "yfinance",
                    "extra_params": {
                        "universe": "  ,  ,  ",  # Only whitespace/commas
                    },
                },
                created_by="test_user",
            )
