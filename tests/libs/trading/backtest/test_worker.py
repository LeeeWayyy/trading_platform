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

import libs.trading.backtest.worker as worker_module
from libs.trading.alpha.exceptions import JobCancelled
from libs.trading.backtest.cost_model import CostModelConfig
from libs.trading.backtest.worker import (
    MAX_COST_CONFIG_SIZE,
    BacktestWorker,
    _validate_config_size,
    _validate_cost_config,
    record_retry,
)


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
        daily_returns=None,
        daily_prices=None,
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
                    mean_ic=0.1,
                    icir=0.2,
                    hit_rate=0.3,
                    coverage=0.4,
                    long_short_spread=0.5,
                    average_turnover=0.6,
                    decay_half_life=10,
                    snapshot_id="snap",
                    dataset_version_ids={"ds": 1},
                    daily_signals=MagicMock(),
                    daily_weights=MagicMock(),
                    daily_ic=MagicMock(),
                    daily_portfolio_returns=MagicMock(),
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
                    mean_ic=0.1,
                    icir=0.2,
                    hit_rate=0.3,
                    coverage=0.4,
                    long_short_spread=0.5,
                    average_turnover=0.6,
                    decay_half_life=10,
                    snapshot_id="snap",
                    dataset_version_ids={"ds": 1},
                    daily_signals=MagicMock(),
                    daily_weights=MagicMock(),
                    daily_ic=MagicMock(),
                    daily_portfolio_returns=MagicMock(),
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

        with pytest.raises(ValueError, match="only allowed in development"):
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

        with pytest.raises(ValueError, match="only allowed in development"):
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
                    mean_ic=0.1,
                    icir=0.2,
                    hit_rate=0.3,
                    coverage=0.4,
                    long_short_spread=0.5,
                    average_turnover=0.6,
                    decay_half_life=10,
                    snapshot_id="snap",
                    dataset_version_ids={"ds": 1},
                    daily_signals=MagicMock(),
                    daily_weights=MagicMock(),
                    daily_ic=MagicMock(),
                    daily_portfolio_returns=MagicMock(),
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


# =============================================================================
# Cost Config Validation Tests (P6T9)
# =============================================================================


class TestValidateConfigSize:
    """Tests for _validate_config_size server-side size validation."""

    @pytest.mark.unit()
    def test_valid_size_passes(self):
        """Test that config within size limit passes."""
        raw_params = {"enabled": True, "bps_per_trade": 5.0}
        # Should not raise
        _validate_config_size(raw_params)

    @pytest.mark.unit()
    def test_oversized_config_raises_error(self):
        """Test that config exceeding size limit raises ValueError."""
        large_value = "x" * (MAX_COST_CONFIG_SIZE + 1)
        raw_params = {"enabled": True, "large_field": large_value}
        with pytest.raises(ValueError, match="exceeds size limit"):
            _validate_config_size(raw_params)

    @pytest.mark.unit()
    def test_config_at_size_limit_passes(self):
        """Test that config at exactly the size limit passes."""
        # Account for JSON overhead: {"enabled": true, "padding": "xxx..."}
        base_json = '{"enabled": true, "padding": ""}'
        base_size = len(base_json.encode("utf-8"))
        padding_size = MAX_COST_CONFIG_SIZE - base_size
        raw_params = {"enabled": True, "padding": "x" * padding_size}

        # Verify it's at exactly the limit
        config_json = json.dumps(raw_params, sort_keys=True)
        assert len(config_json.encode("utf-8")) == MAX_COST_CONFIG_SIZE

        # Should not raise
        _validate_config_size(raw_params)

    @pytest.mark.unit()
    def test_config_just_under_limit_passes(self):
        """Test that config just under size limit passes."""
        base_json = '{"enabled": true, "padding": ""}'
        base_size = len(base_json.encode("utf-8"))
        padding_size = MAX_COST_CONFIG_SIZE - base_size - 100
        raw_params = {"enabled": True, "padding": "y" * padding_size}

        # Verify it's under the limit
        config_json = json.dumps(raw_params, sort_keys=True)
        assert len(config_json.encode("utf-8")) < MAX_COST_CONFIG_SIZE

        # Should not raise
        _validate_config_size(raw_params)

    @pytest.mark.unit()
    def test_config_one_byte_over_limit_raises(self):
        """Test that config one byte over the limit raises."""
        base_json = '{"enabled": true, "padding": ""}'
        base_size = len(base_json.encode("utf-8"))
        padding_size = MAX_COST_CONFIG_SIZE - base_size + 1
        raw_params = {"enabled": True, "padding": "z" * padding_size}

        # Verify it's over the limit by exactly 1 byte
        config_json = json.dumps(raw_params, sort_keys=True)
        assert len(config_json.encode("utf-8")) == MAX_COST_CONFIG_SIZE + 1

        with pytest.raises(ValueError, match="exceeds size limit"):
            _validate_config_size(raw_params)

    @pytest.mark.unit()
    def test_unicode_characters_sized_correctly(self):
        """Test that Unicode characters are correctly sized (multi-byte UTF-8)."""
        # € is 3 bytes in UTF-8
        unicode_str = "€" * (MAX_COST_CONFIG_SIZE // 3)
        raw_params = {"enabled": True, "unicode_field": unicode_str}

        # Should raise because UTF-8 encoding exceeds limit
        with pytest.raises(ValueError, match="exceeds size limit"):
            _validate_config_size(raw_params)

    @pytest.mark.unit()
    def test_json_escaping_accounted(self):
        """Test that JSON escaping is accounted for in size calculation."""
        # Newlines double in size when JSON-encoded ("\n" → "\\n")
        raw_params = {"enabled": True, "field": "\n" * (MAX_COST_CONFIG_SIZE // 2)}

        # Should raise because JSON escaping exceeds limit
        with pytest.raises(ValueError, match="exceeds size limit"):
            _validate_config_size(raw_params)


class TestValidateCostConfig:
    """Tests for _validate_cost_config post-parse validation.

    Note: enabled=False is handled pre-parse in _validate_cost_params_preparse.
    _validate_cost_config only sees configs where enabled != False.
    """

    @pytest.mark.unit()
    def test_enabled_config_returns_true(self):
        """Test that enabled=True config returns True (apply costs)."""
        config = CostModelConfig(enabled=True, bps_per_trade=5.0)
        logger = MagicMock()
        result = _validate_cost_config(config, logger, "job123")
        assert result is True
        logger.warning.assert_not_called()

    @pytest.mark.unit()
    def test_post_parse_always_returns_true(self):
        """Test that _validate_cost_config always returns True (enabled=False handled pre-parse)."""
        # Note: enabled=False configs never reach _validate_cost_config;
        # they are short-circuited in _validate_cost_params_preparse.
        # This test verifies the function returns True for any config that reaches it.
        config = CostModelConfig(enabled=True, bps_per_trade=10.0, impact_coefficient=0.2)
        logger = MagicMock()
        result = _validate_cost_config(config, logger, "job456")
        assert result is True


class TestValidateCostParamsPreparse:
    """Tests for _validate_cost_params_preparse pre-parse validation."""

    @pytest.mark.unit()
    def test_enabled_false_returns_false_and_warns(self):
        """Test that enabled=False returns False and logs warning."""
        from libs.trading.backtest.worker import _validate_cost_params_preparse

        cost_params = {"enabled": False}
        logger = MagicMock()
        result = _validate_cost_params_preparse(cost_params, logger, "job123")
        assert result is False
        logger.warning.assert_called_once()
        call_args = logger.warning.call_args
        assert "cost_model_disabled_in_config" in call_args.args[0]
        assert call_args.kwargs["job_id"] == "job123"

    @pytest.mark.unit()
    def test_enabled_true_returns_true(self):
        """Test that enabled=True returns True (proceed with parsing)."""
        from libs.trading.backtest.worker import _validate_cost_params_preparse

        cost_params = {"enabled": True, "bps_per_trade": 5.0}
        logger = MagicMock()
        result = _validate_cost_params_preparse(cost_params, logger, "job123")
        assert result is True
        logger.warning.assert_not_called()

    @pytest.mark.unit()
    def test_enabled_none_returns_true(self):
        """Test that missing enabled field (None) returns True (default is enabled)."""
        from libs.trading.backtest.worker import _validate_cost_params_preparse

        cost_params = {"bps_per_trade": 5.0}  # enabled not specified
        logger = MagicMock()
        result = _validate_cost_params_preparse(cost_params, logger, "job123")
        assert result is True

    @pytest.mark.unit()
    def test_non_dict_raises_error(self):
        """Test that non-dict cost_params raises ValueError."""
        from libs.trading.backtest.worker import _validate_cost_params_preparse

        logger = MagicMock()
        with pytest.raises(ValueError, match="cost_model must be a dict"):
            _validate_cost_params_preparse("not a dict", logger, "job123")

    @pytest.mark.unit()
    def test_string_enabled_raises_error(self):
        """Test that string enabled value raises ValueError."""
        from libs.trading.backtest.worker import _validate_cost_params_preparse

        cost_params = {"enabled": "true"}  # String, not boolean
        logger = MagicMock()
        with pytest.raises(ValueError, match="must be a boolean"):
            _validate_cost_params_preparse(cost_params, logger, "job123")


# =============================================================================
# Cost Model Type Validation Tests (P6T9)
# =============================================================================


class TestCostModelTypeValidation:
    """Tests for cost_model type and enabled field validation in run_backtest."""

    @pytest.mark.unit()
    def test_non_dict_cost_model_raises_error(self, monkeypatch):
        """Test that non-dict cost_model raises ValueError."""
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

        monkeypatch.setattr(worker_module.Redis, "from_url", lambda *_a, **_k: redis)
        monkeypatch.setattr(worker_module, "_get_worker_pool", lambda: DummyPool())
        monkeypatch.setattr(worker_module, "ManifestManager", MagicMock())
        monkeypatch.setattr(worker_module, "DatasetVersionManager", MagicMock())
        monkeypatch.setattr(worker_module, "CRSPLocalProvider", MagicMock())
        monkeypatch.setattr(worker_module, "CompustatLocalProvider", MagicMock())
        monkeypatch.setattr(worker_module, "AlphaMetricsAdapter", MagicMock())
        monkeypatch.setattr(worker_module, "create_alpha", lambda name: MagicMock(name=name))
        monkeypatch.setattr(
            worker_module, "get_current_job", lambda: types.SimpleNamespace(timeout=400)
        )
        monkeypatch.setattr(worker_module.BacktestWorker, "check_memory", lambda self: None)

        # Mock PITBacktester to return a valid result
        class MockPITBacktester:
            def __init__(self, *args, **kwargs):
                pass

            def run_backtest(self, *args, **kwargs):
                return types.SimpleNamespace(
                    mean_ic=0.1,
                    icir=0.2,
                    hit_rate=0.3,
                    coverage=0.4,
                    long_short_spread=0.5,
                    average_turnover=0.6,
                    decay_half_life=10,
                    snapshot_id="snap",
                    dataset_version_ids={"ds": 1},
                    daily_signals=MagicMock(),
                    daily_weights=MagicMock(),
                    daily_ic=MagicMock(),
                    daily_portfolio_returns=MagicMock(),
                )

        monkeypatch.setattr(worker_module, "PITBacktester", MockPITBacktester)

        # Non-dict cost_model should raise ValueError
        with pytest.raises(ValueError, match="cost_model must be a dict"):
            worker_module.run_backtest(
                {
                    "alpha_name": "test",
                    "start_date": "2024-01-01",
                    "end_date": "2024-01-31",
                    "extra_params": {
                        "cost_model": "not_a_dict",  # Invalid: string instead of dict
                    },
                },
                created_by="test_user",
            )

    @pytest.mark.unit()
    def test_non_boolean_enabled_raises_error(self, monkeypatch):
        """Test that non-boolean enabled field raises ValueError."""
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

        monkeypatch.setattr(worker_module.Redis, "from_url", lambda *_a, **_k: redis)
        monkeypatch.setattr(worker_module, "_get_worker_pool", lambda: DummyPool())
        monkeypatch.setattr(worker_module, "ManifestManager", MagicMock())
        monkeypatch.setattr(worker_module, "DatasetVersionManager", MagicMock())
        monkeypatch.setattr(worker_module, "CRSPLocalProvider", MagicMock())
        monkeypatch.setattr(worker_module, "CompustatLocalProvider", MagicMock())
        monkeypatch.setattr(worker_module, "AlphaMetricsAdapter", MagicMock())
        monkeypatch.setattr(worker_module, "create_alpha", lambda name: MagicMock(name=name))
        monkeypatch.setattr(
            worker_module, "get_current_job", lambda: types.SimpleNamespace(timeout=400)
        )
        monkeypatch.setattr(worker_module.BacktestWorker, "check_memory", lambda self: None)

        class MockPITBacktester:
            def __init__(self, *args, **kwargs):
                pass

            def run_backtest(self, *args, **kwargs):
                return types.SimpleNamespace(
                    mean_ic=0.1,
                    icir=0.2,
                    hit_rate=0.3,
                    coverage=0.4,
                    long_short_spread=0.5,
                    average_turnover=0.6,
                    decay_half_life=10,
                    snapshot_id="snap",
                    dataset_version_ids={"ds": 1},
                    daily_signals=MagicMock(),
                    daily_weights=MagicMock(),
                    daily_ic=MagicMock(),
                    daily_portfolio_returns=MagicMock(),
                )

        monkeypatch.setattr(worker_module, "PITBacktester", MockPITBacktester)

        # String "false" for enabled should raise ValueError (would be truthy otherwise)
        with pytest.raises(ValueError, match="enabled must be a boolean"):
            worker_module.run_backtest(
                {
                    "alpha_name": "test",
                    "start_date": "2024-01-01",
                    "end_date": "2024-01-31",
                    "extra_params": {
                        "cost_model": {
                            "enabled": "false",  # Invalid: string instead of boolean
                        },
                    },
                },
                created_by="test_user",
            )

    @pytest.mark.unit()
    def test_list_cost_model_raises_error(self, monkeypatch):
        """Test that list cost_model raises ValueError."""
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

        monkeypatch.setattr(worker_module.Redis, "from_url", lambda *_a, **_k: redis)
        monkeypatch.setattr(worker_module, "_get_worker_pool", lambda: DummyPool())
        monkeypatch.setattr(worker_module, "ManifestManager", MagicMock())
        monkeypatch.setattr(worker_module, "DatasetVersionManager", MagicMock())
        monkeypatch.setattr(worker_module, "CRSPLocalProvider", MagicMock())
        monkeypatch.setattr(worker_module, "CompustatLocalProvider", MagicMock())
        monkeypatch.setattr(worker_module, "AlphaMetricsAdapter", MagicMock())
        monkeypatch.setattr(worker_module, "create_alpha", lambda name: MagicMock(name=name))
        monkeypatch.setattr(
            worker_module, "get_current_job", lambda: types.SimpleNamespace(timeout=400)
        )
        monkeypatch.setattr(worker_module.BacktestWorker, "check_memory", lambda self: None)

        class MockPITBacktester:
            def __init__(self, *args, **kwargs):
                pass

            def run_backtest(self, *args, **kwargs):
                return types.SimpleNamespace(
                    mean_ic=0.1,
                    icir=0.2,
                    hit_rate=0.3,
                    coverage=0.4,
                    long_short_spread=0.5,
                    average_turnover=0.6,
                    decay_half_life=10,
                    snapshot_id="snap",
                    dataset_version_ids={"ds": 1},
                    daily_signals=MagicMock(),
                    daily_weights=MagicMock(),
                    daily_ic=MagicMock(),
                    daily_portfolio_returns=MagicMock(),
                )

        monkeypatch.setattr(worker_module, "PITBacktester", MockPITBacktester)

        # List cost_model should raise ValueError
        with pytest.raises(ValueError, match="cost_model must be a dict"):
            worker_module.run_backtest(
                {
                    "alpha_name": "test",
                    "start_date": "2024-01-01",
                    "end_date": "2024-01-31",
                    "extra_params": {
                        "cost_model": [{"enabled": True}],  # Invalid: list instead of dict
                    },
                },
                created_by="test_user",
            )


class TestCostModelWorkflowIntegration:
    """Integration tests for cost model workflow in run_backtest."""

    @pytest.mark.unit()
    def test_cost_model_invoked_for_crsp_provider(self, monkeypatch, tmp_path):
        """Test that cost model functions are called when enabled for CRSP provider."""
        import polars as pl

        monkeypatch.setenv("DATABASE_URL", "postgres://test")
        monkeypatch.setenv("ENVIRONMENT", "development")

        class DummyPool:
            def connection(self):
                conn = MagicMock()
                conn.__enter__.return_value = conn
                conn.__exit__.return_value = False
                conn.cursor.return_value = MagicMock()
                return conn

        # Track function calls
        load_pit_adv_called = []
        compute_costs_called = []
        save_parquet_called = []

        def mock_load_pit_adv_volatility(*args, **kwargs):
            load_pit_adv_called.append(kwargs)
            return pl.DataFrame(
                {
                    "permno": [10001, 10002],
                    "date": [pl.date(2024, 1, 1), pl.date(2024, 1, 1)],
                    "adv_usd": [1_000_000.0, 2_000_000.0],
                    "volatility": [0.02, 0.03],
                }
            )

        def mock_compute_backtest_costs(*args, **kwargs):
            compute_costs_called.append(kwargs)
            # Return a mock cost result
            return types.SimpleNamespace(
                cost_summary=types.SimpleNamespace(
                    total_cost_usd=1000.0,
                    net_sharpe=0.5,
                    to_dict=lambda: {"total_cost_usd": 1000.0, "net_sharpe": 0.5},
                ),
                capacity_analysis=types.SimpleNamespace(
                    to_dict=lambda: {"capacity_at_breakeven": 10_000_000},
                ),
                net_returns_df=pl.DataFrame(
                    {"date": [pl.date(2024, 1, 1)], "net_return": [0.001]}
                ),
                adv_fallback_count=0,
                volatility_fallback_count=0,
                participation_violations=0,
            )

        def mock_save_parquet_artifacts(*args, **kwargs):
            save_parquet_called.append({"args": args, "kwargs": kwargs})
            return tmp_path

        class MockPITBacktester:
            def __init__(self, *args, **kwargs):
                pass

            def run_backtest(self, *args, **kwargs):
                # Return a result with non-empty daily_weights
                return types.SimpleNamespace(
                    mean_ic=0.1,
                    icir=0.2,
                    hit_rate=0.3,
                    coverage=0.4,
                    long_short_spread=0.5,
                    average_turnover=0.6,
                    decay_half_life=10,
                    snapshot_id="snap",
                    dataset_version_ids={"crsp_daily": "v1"},
                    daily_signals=pl.DataFrame(
                        {"date": [pl.date(2024, 1, 1)], "permno": [10001], "signal": [0.1]}
                    ),
                    daily_weights=pl.DataFrame(
                        {"date": [pl.date(2024, 1, 1)], "permno": [10001], "weight": [0.5]}
                    ),
                    daily_ic=pl.DataFrame({"date": [pl.date(2024, 1, 1)], "ic": [0.1]}),
                    daily_portfolio_returns=pl.DataFrame(
                        {"date": [pl.date(2024, 1, 1)], "return": [0.001]}
                    ),
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
        monkeypatch.setattr(
            worker_module, "load_pit_adv_volatility", mock_load_pit_adv_volatility
        )
        monkeypatch.setattr(worker_module, "compute_backtest_costs", mock_compute_backtest_costs)
        monkeypatch.setattr(worker_module, "_save_parquet_artifacts", mock_save_parquet_artifacts)
        monkeypatch.setattr(worker_module, "_save_result_to_db", lambda *_, **__: None)
        monkeypatch.setattr(
            worker_module, "get_current_job", lambda: types.SimpleNamespace(timeout=400)
        )
        monkeypatch.setattr(worker_module.BacktestWorker, "check_memory", lambda self: None)

        # Run backtest with cost model enabled
        worker_module.run_backtest(
            {
                "alpha_name": "test",
                "start_date": "2024-01-01",
                "end_date": "2024-01-31",
                "provider": "crsp",
                "extra_params": {
                    "cost_model": {
                        "enabled": True,
                        "bps_per_trade": 5.0,
                        "impact_coefficient": 0.1,
                        "participation_limit": 0.05,
                        "portfolio_value_usd": 1_000_000,
                    },
                },
            },
            created_by="test_user",
        )

        # Verify cost model functions were called
        assert len(load_pit_adv_called) == 1, "load_pit_adv_volatility should be called"
        assert len(compute_costs_called) == 1, "compute_backtest_costs should be called"
        assert len(save_parquet_called) == 1, "save_parquet_artifacts should be called"

        # Verify cost config was passed to save_parquet_artifacts
        save_args = save_parquet_called[0]["args"]
        # Args: job_id, result, cost_config, cost_summary, capacity_analysis, net_returns_df
        assert save_args[2] is not None, "cost_config should be passed to save_parquet"
        assert save_args[3] is not None, "cost_summary should be passed to save_parquet"
        assert save_args[5] is not None, "net_returns_df should be passed to save_parquet"

    @pytest.mark.unit()
    def test_cost_model_skipped_for_empty_weights(self, monkeypatch, tmp_path):
        """Test that cost model is skipped when daily_weights is empty."""
        import polars as pl

        monkeypatch.setenv("DATABASE_URL", "postgres://test")
        monkeypatch.setenv("ENVIRONMENT", "development")

        class DummyPool:
            def connection(self):
                conn = MagicMock()
                conn.__enter__.return_value = conn
                conn.__exit__.return_value = False
                conn.cursor.return_value = MagicMock()
                return conn

        load_pit_adv_called = []

        def mock_load_pit_adv_volatility(*args, **kwargs):
            load_pit_adv_called.append(True)
            return pl.DataFrame()

        class MockPITBacktester:
            def __init__(self, *args, **kwargs):
                pass

            def run_backtest(self, *args, **kwargs):
                # Return a result with EMPTY daily_weights
                return types.SimpleNamespace(
                    mean_ic=0.1,
                    icir=0.2,
                    hit_rate=0.3,
                    coverage=0.4,
                    long_short_spread=0.5,
                    average_turnover=0.6,
                    decay_half_life=10,
                    snapshot_id="snap",
                    dataset_version_ids={"crsp_daily": "v1"},
                    daily_signals=pl.DataFrame(schema={"date": pl.Date, "permno": pl.Int64}),
                    daily_weights=pl.DataFrame(schema={"date": pl.Date, "permno": pl.Int64}),
                    daily_ic=pl.DataFrame(schema={"date": pl.Date, "ic": pl.Float64}),
                    daily_portfolio_returns=pl.DataFrame(
                        schema={"date": pl.Date, "return": pl.Float64}
                    ),
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
        monkeypatch.setattr(
            worker_module, "load_pit_adv_volatility", mock_load_pit_adv_volatility
        )
        monkeypatch.setattr(worker_module, "_save_parquet_artifacts", lambda *_, **__: tmp_path)
        monkeypatch.setattr(worker_module, "_save_result_to_db", lambda *_, **__: None)
        monkeypatch.setattr(
            worker_module, "get_current_job", lambda: types.SimpleNamespace(timeout=400)
        )
        monkeypatch.setattr(worker_module.BacktestWorker, "check_memory", lambda self: None)

        # Run backtest with cost model enabled but empty weights
        worker_module.run_backtest(
            {
                "alpha_name": "test",
                "start_date": "2024-01-01",
                "end_date": "2024-01-31",
                "provider": "crsp",
                "extra_params": {
                    "cost_model": {"enabled": True},
                },
            },
            created_by="test_user",
        )

        # Verify load_pit_adv_volatility was NOT called (skipped due to empty weights)
        assert len(load_pit_adv_called) == 0, (
            "load_pit_adv_volatility should NOT be called for empty weights"
        )
