from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import Enum
from typing import Any, cast

import structlog  # type: ignore[import-not-found]
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from redis import Redis
from rq import Queue, Retry
from rq.job import Job, NoSuchJobError  # type: ignore[attr-defined]


class JobPriority(Enum):
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


def _resolve_rq_finished_status(job: Job) -> tuple[str, str | None]:
    """
    Resolve DB status from an RQ job in finished state.

    Returns a tuple of (status, error_message).
    """
    if job.get_status() != "finished":
        return ("unknown", None)

    result = job.result
    if result is None:
        return ("failed", "RQ job finished but payload missing")

    if isinstance(result, dict) and result.get("cancelled") is True:
        return ("cancelled", None)

    return ("completed", None)


@dataclass
class BacktestJobConfig:
    """Configuration for a backtest job."""

    alpha_name: str
    start_date: date
    end_date: date
    weight_method: str = "zscore"
    extra_params: dict[str, Any] = field(default_factory=dict)

    def compute_job_id(self, created_by: str) -> str:
        content = json.dumps(
            {
                "alpha": self.alpha_name,
                "start": str(self.start_date),
                "end": str(self.end_date),
                "weight": self.weight_method,
                "params": self.extra_params,
                "created_by": created_by,
            },
            sort_keys=True,
        )
        return hashlib.sha256(content.encode()).hexdigest()[:32]

    def to_dict(self) -> dict[str, Any]:
        return {
            "alpha_name": self.alpha_name,
            "start_date": str(self.start_date),
            "end_date": str(self.end_date),
            "weight_method": self.weight_method,
            "extra_params": self.extra_params,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BacktestJobConfig:
        return cls(
            alpha_name=data["alpha_name"],
            start_date=date.fromisoformat(data["start_date"]),
            end_date=date.fromisoformat(data["end_date"]),
            weight_method=data.get("weight_method", "zscore"),
            extra_params=data.get("extra_params", {}),
        )


class BacktestJobQueue:
    """Redis-based job queue for backtests."""

    DEFAULT_TIMEOUT = 3600  # 1 hour
    MAX_RETRIES = 3

    def __init__(
        self,
        redis_client: Redis,
        db_pool: ConnectionPool,
        default_queue: str = "backtest_normal",
    ):
        self.redis = redis_client
        self.db_pool = db_pool
        self.queues = {
            JobPriority.HIGH: Queue("backtest_high", connection=redis_client),
            JobPriority.NORMAL: Queue("backtest_normal", connection=redis_client),
            JobPriority.LOW: Queue("backtest_low", connection=redis_client),
        }
        self.default_queue = self.queues[JobPriority.NORMAL]
        self.logger = structlog.get_logger(__name__)

    def _safe_fetch_job(self, job_id: str) -> Job | None:
        try:
            return Job.fetch(job_id, connection=self.redis)
        except NoSuchJobError:
            return None

    def _fetch_db_job(self, job_id: str) -> dict[str, Any] | None:
        sql = "SELECT * FROM backtest_jobs WHERE job_id = %s"
        with self.db_pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, (job_id,))
            return cur.fetchone()

    def _create_db_job(
        self,
        job_id: str,
        config: BacktestJobConfig,
        created_by: str,
        job_timeout: int,
        *,
        is_rerun: bool = False,
    ) -> None:
        # Schema reference: See P4T4_TASK.md "Result Storage Schema" for authoritative DDL.
        # Column list below must stay synchronized with the parent schema definition.
        upsert_sql = """
        INSERT INTO backtest_jobs (
            job_id, status, alpha_name, start_date, end_date, weight_method,
            config_json, created_by, retry_count, progress_pct, job_timeout
        ) VALUES (%(job_id)s, 'pending', %(alpha)s, %(start)s, %(end)s, %(weight)s,
                  %(config)s, %(created_by)s, 0, 0, %(timeout)s)
        ON CONFLICT (job_id) DO UPDATE SET
            status = 'pending',
            retry_count = CASE WHEN %(is_rerun)s THEN 0 ELSE COALESCE(backtest_jobs.retry_count,0) END,
            started_at = NULL,
            completed_at = NULL,
            worker_id = NULL,
            progress_pct = 0,
            error_message = NULL,
            job_timeout = %(timeout)s,
            result_path = NULL,
            mean_ic = NULL,
            icir = NULL,
            hit_rate = NULL,
            coverage = NULL,
            long_short_spread = NULL,
            average_turnover = NULL,
            decay_half_life = NULL,
            snapshot_id = NULL,
            dataset_version_ids = NULL;
        """

        params = {
            "job_id": job_id,
            "alpha": config.alpha_name,
            "start": config.start_date,
            "end": config.end_date,
            "weight": config.weight_method,
            "config": json.dumps(config.to_dict()),
            "created_by": created_by,
            "timeout": job_timeout,
            "is_rerun": is_rerun,
        }

        with self.db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(upsert_sql, params)
                conn.commit()

    def enqueue(
        self,
        config: BacktestJobConfig,
        priority: JobPriority = JobPriority.NORMAL,
        created_by: str = "system",
        timeout: int | None = None,
        *,
        is_rerun: bool = False,  # user-triggered rerun resets retry_count
    ) -> Job:
        """
        Enqueue a backtest job.

        CRITICAL: Creates BacktestJob DB row BEFORE enqueueing to Redis.
        Returns existing job if same config+user already queued/running (idempotent).
        TOCTOU mitigation: guard enqueue with a short-lived Redis `SETNX backtest:lock:{job_id}` (or rely on the DB unique index on job_id); if the lock fails, return the existing job to avoid double-enqueue.
        Timeout guardrails: validate `job_timeout` within [300, 14400] seconds to avoid starving workers or running forever.

        Re-enqueue Policy:
        - If job is queued/started: return existing (no-op)
        - If job is finished/failed: delete from RQ, reset DB, create new RQ job
        - If DB row is pending/running but RQ job is missing: heal by recreating the RQ job (never return None)
        - User-triggered reruns must call enqueue(..., is_rerun=True) to reset retry_count to 0 (automatic retries handled by RQ retry hook)
        - Healing must NOT bump retry_count; only the RQ retry hook increments it to avoid double-counting.
        """
        job_id = config.compute_job_id(created_by)  # Include user in hash

        job_timeout = timeout or self.DEFAULT_TIMEOUT
        if not 300 <= job_timeout <= 14_400:
            raise ValueError("timeout must be between 300 and 14,400 seconds")

        lock_key = f"backtest:lock:{job_id}"
        if not self.redis.set(lock_key, "1", nx=True, ex=10):
            # Another enqueue in-flight; return existing job if present
            existing = self._safe_fetch_job(job_id)
            if existing:
                return existing
            import time

            time.sleep(0.1)  # 100ms backoff
            if not self.redis.set(lock_key, "1", nx=True, ex=10):
                raise RuntimeError(
                    "enqueue lock contention after retry; another enqueue in progress"
                )

        try:
            # DB status is source of truth for idempotency (pending/running = active)
            db_job = self._fetch_db_job(job_id)
            if db_job and db_job["status"] in ("pending", "running"):
                existing = self._safe_fetch_job(job_id)
                if existing:
                    return existing
                # DB says active but RQ job missing → recreate RQ job deterministically
                # Track heal count in Redis to prevent infinite re-enqueue loops (max 3 per hour)
                heal_key = f"backtest:heal_count:{job_id}"
                heal_raw = self.redis.get(heal_key)
                heal_value = cast(str | bytes | bytearray | int | None, heal_raw)
                if isinstance(heal_value, (bytes, bytearray)):  # noqa: UP038 - tuple form avoids reviewer-reported isinstance issues
                    heal_count = int(heal_value.decode() or 0)
                else:
                    heal_count = int(heal_value or 0)
                if heal_count >= 3:
                    # Too many heals → fail the job instead of looping forever
                    with self.db_pool.connection() as conn, conn.cursor() as cur:
                        cur.execute(
                            """
                            UPDATE backtest_jobs
                            SET status = 'failed',
                                error_message = %s,
                                completed_at = %s
                            WHERE job_id = %s
                            """,
                            (
                                f"Job healed {heal_count} times in 1h; marked failed to prevent infinite loop",
                                datetime.now(UTC),
                                job_id,
                            ),
                        )
                        conn.commit()
                    self.logger.error("heal_loop_breaker", job_id=job_id, heal_count=heal_count)
                    raise RuntimeError(f"Job {job_id} exceeded max heal attempts")
                heal_ttl = max(job_timeout, 3600)
                self.redis.setex(heal_key, heal_ttl, str(heal_count + 1))
                # Healing must preserve existing retry_count; do NOT reset on heal.
                self._create_db_job(job_id, config, created_by, job_timeout, is_rerun=False)
                queue = self.queues[priority]
                healed_job = queue.enqueue(
                    "libs.backtest.worker.run_backtest",
                    kwargs={
                        "config": config.to_dict(),
                        "created_by": created_by,
                    },
                    job_id=job_id,
                    job_timeout=job_timeout,
                    retry=Retry(max=self.MAX_RETRIES, interval=[60, 300, 900]),
                    result_ttl=86400 * 7,
                    failure_ttl=86400 * 30,
                )
                self.logger.info("healed_missing_rq_job", job_id=job_id, heal_count=heal_count + 1)
                return healed_job

            # Check for existing job (idempotency) - safe lookup in RQ
            existing = self._safe_fetch_job(job_id)
            if existing:
                status = existing.get_status()
                if status in ("queued", "started"):
                    return existing
                else:
                    existing.delete()

            # Create/reset DB row (worker will update status)
            self._create_db_job(job_id, config, created_by, job_timeout, is_rerun=is_rerun)

            queue = self.queues[priority]
            job = queue.enqueue(
                "libs.backtest.worker.run_backtest",
                kwargs={
                    "config": config.to_dict(),
                    "created_by": created_by,
                },
                job_id=job_id,
                job_timeout=job_timeout,
                retry=Retry(max=self.MAX_RETRIES, interval=[60, 300, 900]),
                result_ttl=86400 * 7,  # Keep results 7 days
                failure_ttl=86400 * 30,  # Keep failed job info 30 days
            )
            return job
        finally:
            self.redis.delete(lock_key)

    def get_job_status(self, job_id: str) -> dict[str, Any]:
        """
        Get job status with progress information.

        CRITICAL: Uses DB status as source of truth (not RQ status).
        Falls back to RQ status only if DB row not found.
        """
        db_job = self._fetch_db_job(job_id)
        rq_job = self._safe_fetch_job(job_id)
        if not db_job:
            if not rq_job:
                return {"status": "not_found"}
            payload = rq_job.result if isinstance(rq_job.result, dict) else {}
            cancelled = payload.get("cancelled") is True if isinstance(payload, dict) else False
            effective_status = "cancelled" if cancelled else "unknown"
            return {
                "job_id": job_id,
                "status": effective_status,
                "rq_status": rq_job.get_status(),
                "warning": "DB row missing; derived from RQ payload",
            }

        rq_payload = rq_job.result if rq_job and isinstance(rq_job.result, dict) else {}
        rq_cancelled = (
            rq_payload.get("cancelled") is True if isinstance(rq_payload, dict) else False
        )

        progress_raw = self.redis.get(f"backtest:progress:{job_id}")
        if isinstance(progress_raw, (bytes, bytearray)):  # noqa: UP038 - align with reviewer request for tuple isinstance
            progress_raw_decoded: str | None = progress_raw.decode()
        else:
            progress_raw_decoded = progress_raw if isinstance(progress_raw, str) else None

        progress = None
        if progress_raw_decoded:
            try:
                progress = json.loads(progress_raw_decoded)
            except (ValueError, TypeError, json.JSONDecodeError):
                self.logger.warning(
                    "progress_json_decode_failed", job_id=job_id, raw=progress_raw_decoded
                )

        if not isinstance(progress, dict):
            fallback_pct = db_job.get("progress_pct") or 0
            progress = {"pct": fallback_pct, "stage": db_job["status"]}

        return {
            "job_id": job_id,
            "status": "cancelled" if rq_cancelled else db_job["status"],
            "progress_pct": progress.get("pct", db_job.get("progress_pct") or 0),
            "progress_stage": progress.get("stage", db_job["status"]),
            "progress_date": progress.get("current_date"),
            "progress_updated_at": progress.get("updated_at"),
            "created_at": db_job["created_at"].isoformat() if db_job.get("created_at") else None,
            "started_at": db_job["started_at"].isoformat() if db_job.get("started_at") else None,
            "completed_at": (
                db_job["completed_at"].isoformat() if db_job.get("completed_at") else None
            ),
            "error_message": db_job.get("error_message"),
            "result_path": db_job.get("result_path"),
        }

    def cancel_job(self, job_id: str, job_timeout: int | None = None) -> bool:
        """
        Cancel a queued or running job.

        CRITICAL: Updates DB status immediately for queued jobs.
        For running jobs, sets cancel flag and lets worker update DB.
        """
        job = self._safe_fetch_job(job_id)
        db_job = self._fetch_db_job(job_id)

        if not job:
            if not db_job:
                return False
            if db_job["status"] == "pending":
                with self.db_pool.connection() as conn, conn.cursor() as cur:
                    cur.execute(
                        "UPDATE backtest_jobs SET status='cancelled', completed_at=%s WHERE job_id=%s",
                        (datetime.now(UTC), job_id),
                    )
                    conn.commit()
                self.logger.info("cancel_orphan_db_only", job_id=job_id, status="cancelled")
                return True
            if db_job["status"] == "running":
                ttl = max(
                    int(
                        job_timeout
                        or db_job.get("job_timeout")
                        or BacktestJobQueue.DEFAULT_TIMEOUT
                        or 3600
                    ),
                    3600,
                )
                self.redis.setex(f"backtest:cancel:{job_id}", ttl, "1")
                self.logger.info("cancel_flag_set_orphan", job_id=job_id, ttl=ttl, status="running")
                return True
            return False

        status = job.get_status()
        if status == "queued":
            job.cancel()
            if db_job and db_job["status"] == "pending":
                with self.db_pool.connection() as conn, conn.cursor() as cur:
                    cur.execute(
                        "UPDATE backtest_jobs SET status='cancelled', completed_at=%s WHERE job_id=%s",
                        (datetime.now(UTC), job_id),
                    )
                    conn.commit()
            self.redis.setex(f"backtest:cancel:{job_id}", 3600, "1")
            self.logger.info("cancelled_queued_job", job_id=job_id)
            return True
        elif status == "started":
            # For running jobs, only set the Redis cancel flag.
            # The worker will detect this flag and update DB status cooperatively.
            effective_timeout = int(
                (job.timeout if job else None)
                or (db_job.get("job_timeout") if db_job else None)
                or (job_timeout if job_timeout is not None else None)
                or BacktestJobQueue.DEFAULT_TIMEOUT
                or 3600
            )
            ttl = max(effective_timeout, 3600)
            self.redis.setex(f"backtest:cancel:{job_id}", ttl, "1")
            self.logger.info("cancel_flag_set", job_id=job_id, ttl=ttl, status=status)
            return True
        return False

    def watchdog_fail_lost_jobs(self) -> int:
        """
        Mark running jobs as failed if their heartbeat expired (lost worker).
        """
        now_ts = datetime.now(UTC).timestamp()
        with self.db_pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM backtest_jobs WHERE status = 'running'")
            running_jobs = cur.fetchall()
        failures = 0
        for job in running_jobs:
            threshold = now_ts - max(int(job["job_timeout"]), 3600)
            heartbeat_raw = self.redis.get(f"backtest:heartbeat:{job['job_id']}")
            if isinstance(heartbeat_raw, (bytes, bytearray)):  # noqa: UP038 - align with reviewer request for tuple isinstance
                heartbeat_str = heartbeat_raw.decode()
            elif isinstance(heartbeat_raw, str):
                heartbeat_str = heartbeat_raw
            else:
                heartbeat_str = None
            try:
                heartbeat_ts = (
                    datetime.fromisoformat(heartbeat_str).timestamp() if heartbeat_str else None
                )
            except (ValueError, UnicodeDecodeError):
                self.logger.warning(
                    "heartbeat_parse_failed", job_id=job["job_id"], raw=heartbeat_raw
                )
                heartbeat_ts = None
            if heartbeat_ts is None or heartbeat_ts < threshold:
                with self.db_pool.connection() as conn, conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE backtest_jobs
                        SET status='failed',
                            error_message='Worker heartbeat lost; marked failed by watchdog',
                            completed_at=%s
                        WHERE job_id=%s
                        """,
                        (datetime.now(UTC), job["job_id"]),
                    )
                    conn.commit()
                failures += 1
        return failures
