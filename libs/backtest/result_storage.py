from __future__ import annotations

import json
import shutil
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import polars as pl
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from libs.alpha.portfolio import TurnoverCalculator, TurnoverResult
from libs.alpha.research_platform import BacktestResult
from libs.backtest.models import JobNotFound, ResultPathMissing

PARQUET_BASE_DIR = Path("data/backtest_results")


class BacktestResultStorage:
    """Synchronous storage/retrieval for backtest results using psycopg3."""

    DEFAULT_RETENTION_DAYS = 90

    def __init__(self, pool: ConnectionPool, base_dir: Path | None = None):
        self.pool = pool
        # Configurable base directory for path safety checks (useful for testing)
        self.base_dir = base_dir or PARQUET_BASE_DIR

    # ------------------------------------------------------------------ public
    def get_result(self, job_id: str) -> BacktestResult:
        """
        Load a completed backtest result by job_id.

        Raises:
            JobNotFound: no row for job_id
            ResultPathMissing: row exists but result_path is null/absent on disk
            ValueError: corrupt/missing summary.json reproducibility metadata
        """
        sql = "SELECT * FROM backtest_jobs WHERE job_id = %s"
        with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, (job_id,))
            row = cur.fetchone()

        if row is None:
            raise JobNotFound(f"job_id {job_id} not found")

        result_path = row.get("result_path")
        if not result_path:
            raise ResultPathMissing(f"job_id {job_id} missing result_path; rerun or reconcile")

        # Security: Validate result_path is within allowed directory
        # Use resolved path for subsequent operations to prevent TOCTOU attacks
        try:
            safe_base = self.base_dir.resolve()
            target_path = Path(result_path).resolve(strict=False)
            if not target_path.is_relative_to(safe_base):
                raise ResultPathMissing(
                    f"job_id {job_id} result_path outside allowed directory: {result_path}"
                )
        except (OSError, ValueError) as e:
            raise ResultPathMissing(f"job_id {job_id} invalid result_path: {e}") from e

        # Use the resolved target_path to prevent symlink TOCTOU attacks
        return self._load_result_from_path(target_path, job_row=row)

    def list_jobs(
        self,
        created_by: str | None = None,
        alpha_name: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Return filtered job list for APIs."""
        clauses = ["1=1"]
        params: list[Any] = []
        if created_by:
            clauses.append("created_by = %s")
            params.append(created_by)
        if alpha_name:
            clauses.append("alpha_name = %s")
            params.append(alpha_name)
        if status:
            clauses.append("status = %s")
            params.append(status)

        where_sql = " AND ".join(clauses)
        sql = f"""
            SELECT *
            FROM backtest_jobs
            WHERE {where_sql}
            ORDER BY created_at DESC
            OFFSET %s LIMIT %s
        """
        params.extend([offset, limit])

        with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

        return [self._job_to_dict(row) for row in rows]

    def cleanup_old_results(self, retention_days: int = DEFAULT_RETENTION_DAYS) -> int:
        """
        Delete Parquet artifacts and DB rows older than retention window.

        Only terminal jobs (completed, failed, cancelled) are removed to avoid
        orphaning active work. Parquet is removed first to guarantee disk
        cleanup even if the subsequent DELETE fails.
        """
        cutoff = datetime.now(UTC) - timedelta(days=retention_days)
        terminal_statuses = ("completed", "failed", "cancelled")

        select_sql = """
            SELECT job_id, result_path
            FROM backtest_jobs
            WHERE created_at < %s
              AND status = ANY(%s)
        """
        delete_sql = """
            DELETE FROM backtest_jobs
            WHERE created_at < %s
              AND status = ANY(%s)
        """

        with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(select_sql, (cutoff, list(terminal_statuses)))
            jobs = cur.fetchall()

            artifact_paths = [
                Path(row["result_path"])
                for row in jobs
                if row.get("result_path")
            ]

            # Security: Only delete paths within base_dir to prevent arbitrary FS deletion
            # Use resolved path for deletion to prevent TOCTOU symlink attacks
            safe_base = self.base_dir.resolve()
            for path in artifact_paths:
                try:
                    target_path = path.resolve(strict=False)
                    if not target_path.is_relative_to(safe_base):
                        # Skip paths outside allowed directory - log would be added in production
                        continue
                    # Delete using resolved path to prevent symlink swap attacks
                    if target_path.exists():
                        shutil.rmtree(target_path, ignore_errors=True)
                except (OSError, ValueError):
                    # Skip paths that can't be resolved safely
                    continue

            cur.execute(delete_sql, (cutoff, list(terminal_statuses)))
            deleted = cur.rowcount
            conn.commit()

        return int(deleted)

    # ----------------------------------------------------------------- helpers
    def _load_result_from_path(
        self,
        path: Path,
        job_row: dict[str, Any] | None = None,
    ) -> BacktestResult:
        """
        Reconstruct BacktestResult from Parquet artifacts + summary.json.

        Raises:
            ResultPathMissing: if path does not exist
            ValueError: if required reproducibility fields are missing
        """
        if not path.exists():
            raise ResultPathMissing(f"result_path {path} missing on disk")

        signals = pl.read_parquet(path / "daily_signals.parquet")
        weights = pl.read_parquet(path / "daily_weights.parquet")
        ic = pl.read_parquet(path / "daily_ic.parquet")

        summary_path = path / "summary.json"
        if not summary_path.exists():
            raise ValueError(f"Missing summary.json in {path}; cannot reconstruct BacktestResult")
        summary = json.loads(summary_path.read_text())

        snapshot_id = summary.get("snapshot_id")
        dataset_version_ids = summary.get("dataset_version_ids")
        if snapshot_id is None or dataset_version_ids is None:
            raise ValueError(
                f"Missing reproducibility metadata in {summary_path}: "
                f"snapshot_id={snapshot_id}, dataset_version_ids={dataset_version_ids}"
            )

        mean_ic = summary.get("mean_ic")
        if mean_ic is None:
            mean_ic_raw = ic["ic"].mean()
            mean_ic = float(cast(float, mean_ic_raw)) if mean_ic_raw is not None else 0.0

        icir = summary.get("icir")
        if icir is None:
            std_ic_raw = ic["ic"].std()
            std_ic = float(cast(float, std_ic_raw)) if std_ic_raw is not None else 0.0
            icir = float(mean_ic / std_ic) if std_ic != 0 else 0.0

        hit_rate = summary.get("hit_rate")

        # Metadata from DB row where available
        alpha_name = (job_row or {}).get("alpha_name", "unknown")
        backtest_id = (job_row or {}).get("job_id") or path.name
        start_date = (job_row or {}).get("start_date")
        end_date = (job_row or {}).get("end_date")
        weight_method = (job_row or {}).get("weight_method", "zscore")

        coverage = (job_row or {}).get("coverage")
        if coverage is None:
            coverage_df = (
                signals.group_by("date")
                .agg(
                    [
                        pl.col("signal").is_not_null().sum().alias("valid_count"),
                        pl.col("signal").count().alias("total_count"),
                    ]
                )
                .with_columns(
                    (pl.col("valid_count") / pl.col("total_count")).alias("daily_cov")
                )
            )
            coverage = coverage_df.select(pl.col("daily_cov").mean()).item() or 0.0

        long_short_spread = (job_row or {}).get("long_short_spread")
        if long_short_spread is None:
            # Use 0.0 instead of NaN to ensure JSON serialization compatibility
            long_short_spread = 0.0

        decay_half_life = (job_row or {}).get("decay_half_life")

        turnover_calc = TurnoverCalculator()
        turnover_result: TurnoverResult = turnover_calc.compute_turnover_result(weights)

        decay_curve = pl.DataFrame(
            schema={"horizon": pl.Int64, "ic": pl.Float64, "rank_ic": pl.Float64}
        )

        # Derived counts
        n_days = signals.select(pl.col("date").n_unique()).item() or 0
        n_symbols_avg = (
            signals.group_by("date").len().select(pl.col("len").mean()).item() or 0.0
        )

        # start/end fallback to data extents if DB metadata missing
        if start_date is None and signals.height > 0:
            start_date = signals.select(pl.col("date").min()).item()
        if end_date is None and signals.height > 0:
            end_date = signals.select(pl.col("date").max()).item()

        # start_date and end_date are required for BacktestResult
        if start_date is None or end_date is None:
            raise ValueError(
                f"Cannot determine start_date/end_date for {path}: "
                f"start_date={start_date}, end_date={end_date}"
            )

        return BacktestResult(
            alpha_name=alpha_name,
            backtest_id=str(backtest_id),
            start_date=cast(date, start_date),
            end_date=cast(date, end_date),
            snapshot_id=snapshot_id,
            dataset_version_ids=dataset_version_ids,
            daily_signals=signals,
            daily_ic=ic,
            mean_ic=mean_ic,
            icir=icir,
            hit_rate=hit_rate,
            coverage=coverage,
            long_short_spread=long_short_spread,
            autocorrelation={},  # Not persisted; empty dict avoids None
            weight_method=weight_method,
            daily_weights=weights,
            turnover_result=turnover_result,
            decay_curve=decay_curve,
            decay_half_life=decay_half_life,
            n_days=n_days,
            n_symbols_avg=n_symbols_avg,
        )

    def _job_to_dict(self, job: Any) -> dict[str, Any]:
        """
        Convert a DB row or BacktestJob dataclass to primitive dict for APIs.
        """
        # Support dataclass or raw dict_row
        if hasattr(job, "__dict__"):
            data = job.__dict__
        else:
            data = job

        return {
            "job_id": data.get("job_id"),
            "status": data.get("status"),
            "alpha_name": data.get("alpha_name"),
            "start_date": str(data.get("start_date")) if data.get("start_date") else None,
            "end_date": str(data.get("end_date")) if data.get("end_date") else None,
            "created_by": data.get("created_by"),
            "created_at": data.get("created_at").isoformat() if data.get("created_at") else None,
            "mean_ic": data.get("mean_ic"),
            "icir": data.get("icir"),
            "hit_rate": data.get("hit_rate"),
            "coverage": data.get("coverage"),
            "long_short_spread": data.get("long_short_spread"),
            "average_turnover": data.get("average_turnover"),
            "decay_half_life": data.get("decay_half_life"),
        }


__all__ = ["BacktestResultStorage", "PARQUET_BASE_DIR"]
