from __future__ import annotations

import json
import math
import shutil
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import polars as pl
import structlog
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from libs.trading.alpha.portfolio import TurnoverCalculator, TurnoverResult
from libs.trading.alpha.research_platform import BacktestResult
from libs.trading.backtest.models import BacktestJob, JobNotFound, ResultPathMissing
from libs.trading.backtest.param_search import SearchResult
from libs.trading.backtest.walk_forward import (
    WalkForwardConfig,
    WalkForwardResult,
    WindowResult,
)

logger = structlog.get_logger(__name__)

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
            LIMIT %s OFFSET %s
        """
        params.extend([limit, offset])

        with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

        return [self._job_to_dict(row) for row in rows]

    def load_universe_signals_lazy(
        self,
        job_id: str,
        signal_name: str | None = None,
        date_range: tuple[date, date] | None = None,
        limit: int | None = None,
    ) -> pl.LazyFrame | None:
        """Load universe signals with lazy evaluation for predicate pushdown.

        Uses Polars lazy scan with predicate pushdown to avoid loading
        full file into memory.

        Args:
            job_id: The backtest job identifier.
            signal_name: Optional filter by signal name column.
            date_range: Optional (start, end) date filter (inclusive).
            limit: Maximum rows to return (None for unlimited).

        Returns:
            LazyFrame with filtered signals, or None if job/file not found.

        Raises:
            JobNotFound: If job_id doesn't exist in database.
            ResultPathMissing: If result_path is invalid or missing.
        """
        # Get the job to find result_path
        sql = "SELECT result_path FROM backtest_jobs WHERE job_id = %s"
        with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, (job_id,))
            row = cur.fetchone()

        if row is None:
            raise JobNotFound(f"job_id {job_id} not found")

        result_path = row.get("result_path")
        if not result_path:
            return None  # No result_path means no artifacts

        # Security: Validate result_path is within allowed directory
        try:
            safe_base = self.base_dir.resolve()
            target_path = Path(result_path).resolve(strict=False)
            if not target_path.is_relative_to(safe_base):
                raise ResultPathMissing(f"job_id {job_id} result_path outside allowed directory")
        except (OSError, ValueError) as e:
            raise ResultPathMissing(f"job_id {job_id} invalid result_path: {e}") from e

        # Check for signals file
        signals_path = target_path / "daily_signals.parquet"
        if not signals_path.exists():
            return None  # No signals file

        # Create lazy scan with predicate pushdown
        lf = pl.scan_parquet(signals_path)

        # Apply filters as predicates (pushed down to parquet reader)
        if signal_name is not None and "signal_name" in lf.collect_schema().names():
            lf = lf.filter(pl.col("signal_name") == signal_name)

        if date_range is not None:
            start_date, end_date = date_range
            lf = lf.filter((pl.col("date") >= start_date) & (pl.col("date") <= end_date))

        if limit is not None:
            lf = lf.limit(limit)

        return lf

    def load_walk_forward(self, job_id: str) -> WalkForwardResult | None:
        """Load walk-forward results for a backtest job.

        Args:
            job_id: The backtest job identifier.

        Returns:
            WalkForwardResult if artifact exists, None if not available (legacy job).

        Raises:
            JobNotFound: If job_id doesn't exist in database.
            ResultPathMissing: If result_path is invalid or missing.
        """
        target_path = self._get_job_artifact_path(job_id)
        if target_path is None:
            return None

        walk_forward_path = target_path / "walk_forward.json"
        if not walk_forward_path.exists():
            return None  # Legacy job or walk-forward not run

        try:
            data = json.loads(walk_forward_path.read_text())
            return self._deserialize_walk_forward(data)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            logger.warning(
                "walk_forward_artifact_load_failed",
                job_id=job_id,
                path=str(walk_forward_path),
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return None

    def load_param_search(self, job_id: str) -> SearchResult | None:
        """Load parameter search results for a backtest job.

        Args:
            job_id: The backtest job identifier.

        Returns:
            SearchResult if artifact exists, None if not available.

        Raises:
            JobNotFound: If job_id doesn't exist in database.
            ResultPathMissing: If result_path is invalid or missing.
        """
        target_path = self._get_job_artifact_path(job_id)
        if target_path is None:
            return None

        param_search_path = target_path / "param_search.json"
        if not param_search_path.exists():
            return None  # Legacy job or param search not run

        try:
            data = json.loads(param_search_path.read_text())
            return self._deserialize_param_search(data)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            logger.warning(
                "param_search_artifact_load_failed",
                job_id=job_id,
                path=str(param_search_path),
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return None

    def _get_job_artifact_path(self, job_id: str) -> Path | None:
        """Get validated artifact path for a job.

        Returns:
            Resolved Path if valid, None if no result_path.

        Raises:
            JobNotFound: If job_id doesn't exist.
            ResultPathMissing: If path is invalid or outside allowed directory.
        """
        sql = "SELECT result_path FROM backtest_jobs WHERE job_id = %s"
        with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, (job_id,))
            row = cur.fetchone()

        if row is None:
            raise JobNotFound(f"job_id {job_id} not found")

        result_path = row.get("result_path")
        if not result_path:
            return None  # No result_path means no artifacts

        # Security: Validate result_path is within allowed directory
        try:
            safe_base = self.base_dir.resolve()
            target_path = Path(result_path).resolve(strict=False)
            if not target_path.is_relative_to(safe_base):
                raise ResultPathMissing(f"job_id {job_id} result_path outside allowed directory")
        except (OSError, ValueError) as e:
            raise ResultPathMissing(f"job_id {job_id} invalid result_path: {e}") from e

        return target_path

    def _deserialize_walk_forward(self, data: dict[str, Any]) -> WalkForwardResult:
        """Deserialize walk-forward result from JSON data."""
        config_data = data.get("config", {})
        windows_data = data.get("windows", [])
        aggregated = data.get("aggregated", {})

        windows = [
            WindowResult(
                window_id=w["window_id"],
                train_start=date.fromisoformat(w["train_start"]),
                train_end=date.fromisoformat(w["train_end"]),
                test_start=date.fromisoformat(w["test_start"]),
                test_end=date.fromisoformat(w["test_end"]),
                best_params=w.get("best_params", {}),
                train_ic=_restore_float(w.get("train_ic")),
                test_ic=_restore_float(w.get("test_ic")),
                test_icir=_restore_float(w.get("test_icir")),
            )
            for w in windows_data
        ]

        return WalkForwardResult(
            windows=windows,
            aggregated_test_ic=_restore_float(aggregated.get("test_ic")),
            aggregated_test_icir=_restore_float(aggregated.get("test_icir")),
            overfitting_ratio=_restore_float(aggregated.get("overfitting_ratio")),
            overfitting_threshold=config_data.get("overfitting_threshold", 2.0),
        )

    def _deserialize_param_search(self, data: dict[str, Any]) -> SearchResult:
        """Deserialize parameter search result from JSON data.

        Restores sanitized None scores back to float("nan") so downstream
        numeric code (comparisons, aggregations) works correctly.
        """
        restored_results = [
            {"params": r["params"], "score": _restore_float(r.get("score"))}
            for r in data["all_results"]
        ]
        return SearchResult(
            best_params=data["best_params"],
            best_score=_restore_float(data["best_score"]),
            all_results=restored_results,
            param_names=data.get("param_names"),
            param_ranges=data.get("param_ranges"),
            metric_name=data.get("metric_name"),
        )

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

        with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(select_sql, (cutoff, list(terminal_statuses)))
            jobs = cur.fetchall()

            # Track job_ids where artifact cleanup succeeded or no artifacts existed
            # Only delete DB rows for these to avoid orphaning directories on disk
            successfully_cleaned_job_ids: list[str] = []
            safe_base = self.base_dir.resolve()

            for job in jobs:
                job_id = job["job_id"]
                result_path = job.get("result_path")

                if not result_path:
                    # No artifacts to clean, safe to delete DB row
                    successfully_cleaned_job_ids.append(job_id)
                    continue

                # Security: Only delete paths within base_dir to prevent arbitrary FS deletion
                # Use resolved path for deletion to prevent TOCTOU symlink attacks
                try:
                    target_path = Path(result_path).resolve(strict=False)
                    if not target_path.is_relative_to(safe_base):
                        # Path outside allowed directory - skip entirely (don't delete DB row)
                        # In production, this would be logged as a security concern
                        continue

                    # Delete using resolved path to prevent symlink swap attacks
                    if target_path.exists():
                        shutil.rmtree(target_path)  # Raises on failure - don't ignore errors
                    # Artifact deleted or didn't exist, safe to delete DB row
                    successfully_cleaned_job_ids.append(job_id)
                except (OSError, ValueError):
                    # Artifact deletion failed - keep DB row to allow retry
                    # In production, this would be logged for investigation
                    continue

            # Only delete DB rows for jobs where artifact cleanup succeeded
            if successfully_cleaned_job_ids:
                delete_by_ids_sql = """
                    DELETE FROM backtest_jobs
                    WHERE job_id = ANY(%s)
                """
                cur.execute(delete_by_ids_sql, (successfully_cleaned_job_ids,))
                deleted = cur.rowcount
            else:
                deleted = 0
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

        # Read Parquet files and summary.json with robust error handling
        try:
            signals = pl.read_parquet(path / "daily_signals.parquet")
            weights = pl.read_parquet(path / "daily_weights.parquet")
            ic = pl.read_parquet(path / "daily_ic.parquet")
            daily_portfolio_returns_path = path / "daily_portfolio_returns.parquet"
            if daily_portfolio_returns_path.exists():
                daily_portfolio_returns = pl.read_parquet(daily_portfolio_returns_path)
            else:
                daily_portfolio_returns = pl.DataFrame(
                    schema={"date": pl.Date, "return": pl.Float64}
                )
            daily_returns_path = path / "daily_returns.parquet"
            if daily_returns_path.exists():
                daily_returns = pl.read_parquet(daily_returns_path)
            else:
                daily_returns = pl.DataFrame(
                    schema={
                        "date": pl.Date,
                        "permno": pl.Int64,
                        "return": pl.Float64,
                        "symbol": pl.Utf8,
                    }
                )
            daily_prices_path = path / "daily_prices.parquet"
            if daily_prices_path.exists():
                daily_prices = pl.read_parquet(daily_prices_path)
            else:
                daily_prices = pl.DataFrame(
                    schema={
                        "date": pl.Date,
                        "permno": pl.Int64,
                        "price": pl.Float64,
                        "symbol": pl.Utf8,
                    }
                )

            # Load net portfolio returns if cost model was applied (T9.4)
            net_portfolio_returns_path = path / "net_portfolio_returns.parquet"
            if net_portfolio_returns_path.exists():
                net_portfolio_returns = pl.read_parquet(net_portfolio_returns_path)
            else:
                net_portfolio_returns = None

            summary_path = path / "summary.json"
            if not summary_path.exists():
                raise ValueError(
                    f"Missing summary.json in {path}; cannot reconstruct BacktestResult"
                )
            summary = json.loads(summary_path.read_text())
        except FileNotFoundError as e:
            raise ValueError(f"Missing backtest artifact in {path}: {e}") from e
        except json.JSONDecodeError as e:
            raise ValueError(f"Corrupt summary.json in {path}: {e}") from e
        except pl.exceptions.PolarsError as e:
            # Catch polars-specific errors during file reading
            raise ValueError(f"Failed to load Parquet artifact from {path}: {e}") from e

        snapshot_id = summary.get("snapshot_id")
        dataset_version_ids = summary.get("dataset_version_ids")
        if snapshot_id is None or dataset_version_ids is None:
            raise ValueError(
                f"Missing reproducibility metadata in {summary_path}: "
                f"snapshot_id={snapshot_id}, dataset_version_ids={dataset_version_ids}"
            )

        # Restore NaN-sanitized metrics (None in JSON → NaN in domain).
        # Only recompute when the key is missing entirely (legacy summaries),
        # not when the key is present with null (sanitized NaN).
        if "mean_ic" in summary:
            mean_ic = _restore_float(summary["mean_ic"])
        else:
            mean_ic_raw = ic["ic"].mean()
            mean_ic = float(cast(float, mean_ic_raw)) if mean_ic_raw is not None else 0.0

        if "icir" in summary:
            icir = _restore_float(summary["icir"])
        else:
            std_ic_raw = ic["ic"].std()
            std_ic = float(cast(float, std_ic_raw)) if std_ic_raw is not None else 0.0
            icir = float(mean_ic / std_ic) if std_ic != 0 else 0.0

        if "hit_rate" in summary:
            hit_rate = _restore_float(summary["hit_rate"])
        else:
            hit_rate = 0.0

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
                .with_columns((pl.col("valid_count") / pl.col("total_count")).alias("daily_cov"))
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
        n_symbols_avg = signals.group_by("date").len().select(pl.col("len").mean()).item() or 0.0

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

        # Load cost model data from summary.json (P6T9)
        cost_config = summary.get("cost_config")
        cost_summary = summary.get("cost_summary")
        capacity_analysis = summary.get("capacity_analysis")

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
            daily_portfolio_returns=daily_portfolio_returns,
            daily_returns=daily_returns,
            daily_prices=daily_prices,
            turnover_result=turnover_result,
            decay_curve=decay_curve,
            decay_half_life=decay_half_life,
            n_days=n_days,
            n_symbols_avg=n_symbols_avg,
            cost_config=cost_config,
            cost_summary=cost_summary,
            capacity_analysis=capacity_analysis,
            net_portfolio_returns=net_portfolio_returns,
        )

    def _job_to_dict(self, job: Any) -> dict[str, Any]:
        """
        Convert a DB row or BacktestJob dataclass to primitive dict for APIs.
        """
        # Support dataclass or raw dict_row
        if isinstance(job, BacktestJob):
            data = job.__dict__
        else:
            data = job

        created_at = data.get("created_at")
        created_at_iso = created_at.isoformat() if created_at is not None else None

        return {
            "job_id": data.get("job_id"),
            "status": data.get("status"),
            "alpha_name": data.get("alpha_name"),
            "start_date": str(data.get("start_date")) if data.get("start_date") else None,
            "end_date": str(data.get("end_date")) if data.get("end_date") else None,
            "created_by": data.get("created_by"),
            "created_at": created_at_iso,
            "mean_ic": data.get("mean_ic"),
            "icir": data.get("icir"),
            "hit_rate": data.get("hit_rate"),
            "coverage": data.get("coverage"),
            "long_short_spread": data.get("long_short_spread"),
            "average_turnover": data.get("average_turnover"),
            "decay_half_life": data.get("decay_half_life"),
        }


def _sanitize_float(value: float) -> float | None:
    """Convert NaN/inf to None for strict JSON compatibility.

    Standard json.dumps allows NaN by default, but strict JSON parsers
    (including many JS front-ends) reject NaN/inf as invalid JSON values.

    Handles native Python floats, numpy.float64, and Decimal types by
    normalizing to native float. Returns the normalized float (not the
    original type) to avoid stringify issues with json.dump(default=str).
    Non-numeric types pass through unchanged.
    """
    try:
        as_float = float(value)
        if math.isnan(as_float) or math.isinf(as_float):
            return None
        return as_float
    except (TypeError, ValueError, OverflowError):
        return value  # Non-numeric type (str, dict, etc.) — return as-is


def _restore_float(value: float | None) -> float:
    """Convert None back to NaN during deserialization.

    Inverse of _sanitize_float: restores None values (produced by NaN
    sanitization during serialization) back to float("nan") so that
    domain objects always hold float values in numeric fields.
    """
    if value is None:
        return float("nan")
    return float(value)


def serialize_walk_forward(result: WalkForwardResult, config: WalkForwardConfig) -> dict[str, Any]:
    """Serialize WalkForwardResult to JSON-compatible dict.

    Float values are sanitized: NaN/inf are converted to None for strict
    JSON parser compatibility.

    Args:
        result: The walk-forward result to serialize.
        config: The configuration used for the optimization.

    Returns:
        Dict ready for JSON serialization.
    """
    return {
        "version": "1.0",
        "config": {
            "train_months": config.train_months,
            "test_months": config.test_months,
            "step_months": config.step_months,
            "min_train_samples": config.min_train_samples,
            "overfitting_threshold": config.overfitting_threshold,
        },
        "windows": [
            {
                "window_id": w.window_id,
                "train_start": w.train_start.isoformat(),
                "train_end": w.train_end.isoformat(),
                "test_start": w.test_start.isoformat(),
                "test_end": w.test_end.isoformat(),
                "best_params": w.best_params,
                "train_ic": _sanitize_float(w.train_ic),
                "test_ic": _sanitize_float(w.test_ic),
                "test_icir": _sanitize_float(w.test_icir),
            }
            for w in result.windows
        ],
        "aggregated": {
            "test_ic": _sanitize_float(result.aggregated_test_ic),
            "test_icir": _sanitize_float(result.aggregated_test_icir),
            "overfitting_ratio": _sanitize_float(result.overfitting_ratio),
            "is_overfit": result.is_overfit,
        },
        "created_at": datetime.now(UTC).isoformat(),
    }


def serialize_param_search(result: SearchResult) -> dict[str, Any]:
    """Serialize SearchResult to JSON-compatible dict.

    Omits optional visualization fields when None so downstream consumers
    can distinguish "not available" from "intentionally empty".

    Args:
        result: The parameter search result to serialize.

    Returns:
        Dict ready for JSON serialization.
    """
    sanitized_results = [
        {"params": r["params"], "score": _sanitize_float(r["score"])}
        for r in result.all_results
    ]
    data: dict[str, Any] = {
        "version": "1.0",
        "best_params": result.best_params,
        "best_score": _sanitize_float(result.best_score),
        "all_results": sanitized_results,
        "created_at": datetime.now(UTC).isoformat(),
    }

    # Only include optional visualization fields when populated
    if result.param_names is not None:
        data["param_names"] = result.param_names
    if result.param_ranges is not None:
        data["param_ranges"] = result.param_ranges
    if result.metric_name is not None:
        data["metric_name"] = result.metric_name

    return data


__all__ = [
    "BacktestResultStorage",
    "PARQUET_BASE_DIR",
    "serialize_walk_forward",
    "serialize_param_search",
]
