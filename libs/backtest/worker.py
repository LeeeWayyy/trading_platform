from __future__ import annotations

import atexit
import json
import os
import shutil
import threading
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import psutil  # type: ignore[import-untyped]
import structlog
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from redis import Redis
from rq import get_current_job

from libs.alpha.alpha_library import create_alpha
from libs.alpha.exceptions import JobCancelled
from libs.alpha.metrics import AlphaMetricsAdapter
from libs.alpha.research_platform import BacktestResult, PITBacktester
from libs.backtest.job_queue import BacktestJobConfig, BacktestJobQueue
from libs.data_providers.compustat_local_provider import CompustatLocalProvider
from libs.data_providers.crsp_local_provider import CRSPLocalProvider
from libs.data_quality.manifest import ManifestManager
from libs.data_quality.versioning import DatasetVersionManager


class BacktestWorker:
    """Worker with cooperative cancellation and memory monitoring."""

    MAX_RSS_BYTES = int(
        os.getenv("BACKTEST_JOB_MEMORY_LIMIT", 4 * 1024 * 1024 * 1024)
    )  # 4GB default
    CANCEL_CHECK_INTERVAL = 10  # Check cancel flag every 10s even without progress

    def __init__(self, redis: Redis, db_pool: ConnectionPool):
        self.redis = redis
        self.db_pool = db_pool
        self.process = psutil.Process()
        self._last_cancel_check = 0.0
        self.logger = structlog.get_logger(__name__)

    def check_cancellation(self, job_id: str) -> None:
        """Check if cancellation requested; raise if so."""
        if self.redis.exists(f"backtest:cancel:{job_id}"):
            self.redis.delete(f"backtest:cancel:{job_id}")
            raise JobCancelled(f"Job {job_id} cancelled by user")

    def check_cancellation_periodic(self, job_id: str, job_timeout: int) -> None:
        """Check cancellation AND memory on interval, for long loops without progress updates."""
        now = time.monotonic()
        if now - self._last_cancel_check >= self.CANCEL_CHECK_INTERVAL:
            self.check_cancellation(job_id)
            ttl = max(int(job_timeout or 3600), 3600)
            pipe = self.redis.pipeline()
            pipe.set(f"backtest:heartbeat:{job_id}", datetime.now(UTC).isoformat())
            pipe.expire(f"backtest:heartbeat:{job_id}", ttl)
            pipe.expire(f"backtest:cancel:{job_id}", ttl)
            pipe.execute()
            self.check_memory()
            self._last_cancel_check = now

    def check_memory(self) -> None:
        """Kill job if memory exceeds limit."""
        rss = self.process.memory_info().rss
        if rss > self.MAX_RSS_BYTES:
            raise MemoryError(f"Job exceeded {self.MAX_RSS_BYTES // 1e9:.0f}GB limit")

    def update_progress(
        self,
        job_id: str,
        pct: int,
        stage: str,
        current_date: str | None = None,
        job_timeout: int = 3600,
        *,
        skip_cancel_check: bool = False,
        skip_memory_check: bool = False,
    ) -> None:
        """
        Update progress and check cancellation/memory.

        Progress is stored in Redis for fast UI polling.
        Sync to DB every 10% and at/above 90% so coarse progress survives Redis key expiry.
        """
        if not skip_cancel_check:
            self.check_cancellation(job_id)
        if not skip_memory_check:
            self.check_memory()
        self._last_cancel_check = time.monotonic()

        ttl = max(job_timeout, 3600)
        payload = json.dumps(
            {
                "pct": pct,
                "stage": stage,
                "current_date": current_date,
                "updated_at": datetime.now(UTC).isoformat(),
            }
        )

        pipe = self.redis.pipeline()
        pipe.set(f"backtest:progress:{job_id}", payload)
        pipe.expire(f"backtest:progress:{job_id}", ttl)
        pipe.set(f"backtest:heartbeat:{job_id}", datetime.now(UTC).isoformat())
        pipe.expire(f"backtest:heartbeat:{job_id}", ttl)
        pipe.expire(f"backtest:cancel:{job_id}", ttl)
        pipe.execute()

        if self.should_sync_db_progress(pct):
            self.update_db_progress(job_id, pct)

    def update_db_status(self, job_id: str, status: str, **kwargs: Any) -> None:
        """Update job status in Postgres (sync)."""
        TERMINAL_STATES = {"completed", "failed", "cancelled"}
        with self.db_pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT status FROM backtest_jobs WHERE job_id = %s", (job_id,))
            row = cur.fetchone()
            if not row:
                return
            if row["status"] in TERMINAL_STATES:
                return
            if status == "cancelled" and row["status"] not in ("running", "pending"):
                return
            sets = ["status = %s"]
            values = [status]
            for key, value in kwargs.items():
                sets.append(f"{key} = %s")
                values.append(value)
            values.append(job_id)
            cur.execute(f"UPDATE backtest_jobs SET {', '.join(sets)} WHERE job_id = %s", values)
            conn.commit()

    def update_db_progress(self, job_id: str, pct: int) -> None:
        """Persist progress to DB periodically for fallback when Redis expires."""
        with self.db_pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE backtest_jobs SET progress_pct = %s WHERE job_id = %s",
                (pct, job_id),
            )
            conn.commit()

    def should_sync_db_progress(self, pct: int) -> bool:
        """Syncs at 0, 10, 20, ..., 90, 100 (every 10%)."""
        return pct % 10 == 0


_RETRY_POOL: ConnectionPool | None = None
_WORKER_POOL: ConnectionPool | None = None
_POOL_LOCK = threading.Lock()


def _get_retry_pool() -> ConnectionPool:
    """
    Lazily create a shared psycopg pool for retry hook to avoid per-retry connections.
    """
    global _RETRY_POOL
    if _RETRY_POOL is None:
        db_url = os.getenv("DATABASE_URL")
        if not db_url:
            raise RuntimeError("DATABASE_URL not set; cannot create retry hook pool")
        assert db_url is not None
        _RETRY_POOL = ConnectionPool(conninfo=db_url)
        _RETRY_POOL.open()
    return _RETRY_POOL


def _get_worker_pool() -> ConnectionPool:
    """
    Lazily create a shared psycopg pool for worker jobs.

    Singleton pattern avoids creating a new pool per job, improving efficiency
    and preventing resource exhaustion under load.

    Thread-safe: Uses lock to prevent race conditions during initialization.
    Note: Under RQ's ForkingWorker, each child process gets its own pool instance.
    This is still beneficial as it prevents creating multiple pools within a single
    job execution and provides clean shutdown via atexit.
    """
    global _WORKER_POOL
    if _WORKER_POOL is None:
        with _POOL_LOCK:
            # Double-check after acquiring lock
            if _WORKER_POOL is None:
                db_url = os.getenv("DATABASE_URL")
                if not db_url:
                    raise RuntimeError("DATABASE_URL not set; cannot create worker pool")
                assert db_url is not None
                _WORKER_POOL = ConnectionPool(conninfo=db_url, min_size=1, max_size=4)
                _WORKER_POOL.open()
                atexit.register(_close_worker_pool)
    return _WORKER_POOL


def _close_worker_pool() -> None:
    """Close worker pool on process exit for clean shutdown."""
    global _WORKER_POOL
    if _WORKER_POOL is not None:
        try:
            _WORKER_POOL.close()
        except Exception:
            pass  # Best effort cleanup on exit
        _WORKER_POOL = None


def record_retry(job: Any, *exc_info: Any) -> bool:
    """RQ retry hook: increment retry_count for automated retries."""
    pool = _get_retry_pool()
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE backtest_jobs SET retry_count = COALESCE(retry_count,0) + 1 WHERE job_id = %s",
            (job.id,),
        )
        conn.commit()
    return False


def run_backtest(config: dict[str, Any], created_by: str) -> dict[str, Any]:
    """
    RQ job entrypoint for backtest execution.

    Uses singleton connection pool shared across jobs for efficiency.
    """
    redis = Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379"))
    db_pool = _get_worker_pool()

    with db_pool.connection() as conn:
        job_config = BacktestJobConfig.from_dict(config)
        job_id = job_config.compute_job_id(created_by)
        current_job = get_current_job()
        job_timeout = (
            int(current_job.timeout or BacktestJobQueue.DEFAULT_TIMEOUT)
            if current_job
            else BacktestJobQueue.DEFAULT_TIMEOUT
        )
        worker = BacktestWorker(redis, db_pool)

        try:
            worker.update_progress(job_id, 5, "init_dependencies", job_timeout=job_timeout)

            data_root = Path(os.getenv("DATA_ROOT", "data")).resolve()
            manifest_manager = ManifestManager(data_root=data_root)
            version_manager = DatasetVersionManager(manifest_manager)
            crsp_provider = CRSPLocalProvider(
                data_root / "crsp",
                manifest_manager,
                data_root=data_root,
            )
            compustat_provider = CompustatLocalProvider(
                data_root / "compustat",
                manifest_manager,
                data_root=data_root,
            )
            metrics_adapter = AlphaMetricsAdapter()

            backtester = PITBacktester(
                version_manager=version_manager,
                crsp_provider=crsp_provider,
                compustat_provider=compustat_provider,
                metrics_adapter=metrics_adapter,
            )

            worker.update_db_status(job_id, "running", started_at=datetime.now(UTC))
            worker.update_progress(job_id, 0, "started", job_timeout=job_timeout)
            worker.update_progress(job_id, 10, "loading_data", job_timeout=job_timeout)

            snapshot_id = job_config.extra_params.get("snapshot_id")

            alpha = create_alpha(job_config.alpha_name)

            result = backtester.run_backtest(
                alpha=alpha,
                start_date=job_config.start_date,
                end_date=job_config.end_date,
                snapshot_id=snapshot_id,
                weight_method=job_config.weight_method,
                progress_callback=lambda pct, d: worker.update_progress(
                    job_id,
                    20 + round(pct * 0.7),
                    "computing",
                    str(d) if d else None,
                    job_timeout=job_timeout,
                ),
                cancel_check=lambda: worker.check_cancellation_periodic(job_id, job_timeout),
            )

            worker.update_progress(job_id, 90, "saving_parquet", job_timeout=job_timeout)
            result_path = _save_parquet_artifacts(job_id, result)

            worker.update_progress(job_id, 95, "saving_db", job_timeout=job_timeout)
            _save_result_to_db(conn, job_id, result, result_path)

            worker.update_progress(job_id, 100, "completed", job_timeout=job_timeout)
            worker.update_db_status(job_id, "completed", completed_at=datetime.now(UTC))

            return {
                "job_id": job_id,
                "result_path": str(result_path),
                "summary_metrics": {
                    "mean_ic": result.mean_ic,
                    "icir": result.icir,
                    "hit_rate": result.hit_rate,
                },
            }

        except JobCancelled:
            last_progress_raw = cast(Any, redis.get(f"backtest:progress:{job_id}"))
            last_progress: str | None
            if isinstance(last_progress_raw, (bytes, bytearray)):  # noqa: UP038
                last_progress = last_progress_raw.decode()
            elif isinstance(last_progress_raw, str):
                last_progress = last_progress_raw
            else:
                last_progress = None

            last_pct = 0
            if last_progress:
                try:
                    last_pct_val = json.loads(last_progress)
                    if isinstance(last_pct_val, dict):
                        last_pct = int(last_pct_val.get("pct", 0))
                except (ValueError, TypeError, json.JSONDecodeError):
                    worker.logger.warning(
                        "cancel_progress_parse_failed", job_id=job_id, raw=last_progress
                    )
            shutil.rmtree(Path("data/backtest_results") / job_id, ignore_errors=True)
            worker.update_db_status(job_id, "cancelled", completed_at=datetime.now(UTC))
            worker.update_progress(
                job_id,
                last_pct,
                "cancelled",
                skip_cancel_check=True,
                skip_memory_check=True,
            )
            return {"job_id": job_id, "cancelled": True}

        except Exception as e:
            worker.update_db_status(
                job_id,
                "failed",
                error_message=str(e),
                completed_at=datetime.now(UTC),
            )
            raise


def _save_parquet_artifacts(job_id: str, result: BacktestResult) -> Path:
    """
    Save bulk time-series data to Parquet files.
    """
    import polars as pl

    def _validate_schema(df: pl.DataFrame, required: Mapping[str, object]) -> None:
        missing_cols = set(required.keys()) - set(df.columns)
        if missing_cols:
            raise ValueError(f"missing columns: {missing_cols}")
        for col, dtype in required.items():
            if df[col].dtype != dtype:
                raise ValueError(f"column {col} has type {df[col].dtype}, expected {dtype}")

    result_dir = Path("data/backtest_results") / job_id
    result_dir.mkdir(parents=True, exist_ok=True)

    required_signal_schema = {"date": pl.Date, "permno": pl.Int64, "signal": pl.Float64}
    required_weight_schema = {"date": pl.Date, "permno": pl.Int64, "weight": pl.Float64}
    required_ic_schema = {"date": pl.Date, "ic": pl.Float64, "rank_ic": pl.Float64}
    required_portfolio_schema = {"date": pl.Date, "return": pl.Float64}

    _validate_schema(result.daily_signals, required_signal_schema)
    _validate_schema(result.daily_weights, required_weight_schema)
    if result.daily_ic is None:
        raise ValueError("daily_ic DataFrame must be populated before parquet export")
    _validate_schema(result.daily_ic, required_ic_schema)
    _validate_schema(result.daily_portfolio_returns, required_portfolio_schema)

    result.daily_signals.select(["date", "permno", "signal"]).cast(required_signal_schema).write_parquet(  # type: ignore[arg-type]
        result_dir / "daily_signals.parquet",
        compression="snappy",
    )

    result.daily_weights.select(["date", "permno", "weight"]).cast(required_weight_schema).write_parquet(  # type: ignore[arg-type]
        result_dir / "daily_weights.parquet",
        compression="snappy",
    )

    result.daily_ic.select(["date", "ic", "rank_ic"]).cast(required_ic_schema).write_parquet(  # type: ignore[arg-type]
        result_dir / "daily_ic.parquet",
        compression="snappy",
    )

    result.daily_portfolio_returns.select(["date", "return"]).cast(required_portfolio_schema).write_parquet(  # type: ignore[arg-type]
        result_dir / "daily_portfolio_returns.parquet",
        compression="snappy",
    )

    _write_summary_json(result_dir, result)

    return result_dir


def _write_summary_json(result_dir: Path, result: BacktestResult) -> None:
    """Persist summary metrics and reproducibility metadata alongside Parquet artifacts."""
    summary = {
        "mean_ic": result.mean_ic,
        "icir": result.icir,
        "hit_rate": result.hit_rate,
        "snapshot_id": result.snapshot_id,
        "dataset_version_ids": result.dataset_version_ids,
    }
    (result_dir / "summary.json").write_text(json.dumps(summary, default=str, indent=2))


def _save_result_to_db(conn: Any, job_id: str, result: BacktestResult, result_path: Path) -> None:
    """Save summary metrics to Postgres (psycopg)."""
    if result.snapshot_id is None or result.dataset_version_ids is None:
        raise ValueError(
            "BacktestResult must include snapshot_id and dataset_version_ids for reproducibility"
        )

    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE backtest_jobs
            SET status='completed',
                result_path=%s,
                mean_ic=%s,
                icir=%s,
                hit_rate=%s,
                coverage=%s,
                long_short_spread=%s,
                average_turnover=%s,
                decay_half_life=%s,
                snapshot_id=%s,
                dataset_version_ids=%s,
                completed_at=%s
            WHERE job_id=%s
            """,
            (
                str(result_path),
                result.mean_ic,
                result.icir,
                result.hit_rate,
                result.coverage,
                result.long_short_spread,
                result.average_turnover,
                result.decay_half_life,
                result.snapshot_id,
                result.dataset_version_ids,  # psycopg3 handles dict → JSONB conversion automatically
                datetime.now(UTC),
                job_id,
            ),
        )
        if cur.rowcount == 0:
            raise RuntimeError(f"BacktestJob {job_id} missing when saving result")
        conn.commit()
