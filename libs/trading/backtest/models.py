"""Backtest job models and exceptions.

This module provides:
- BacktestJob: Dataclass representing a backtest job record from Postgres
- JobNotFound: Exception for missing job_id in database
- ResultPathMissing: Exception for job with null/missing result_path
- row_to_backtest_job: Mapper from psycopg dict_row to BacktestJob

Schema Reference: db/migrations/0008_create_backtest_jobs.sql
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

# Status vocabulary matches CHECK constraint in migration
JobStatus = Literal["pending", "running", "completed", "failed", "cancelled"]

# Weight method matches job_queue.WeightMethod
WeightMethod = Literal["zscore", "quantile", "rank"]


class JobNotFound(Exception):
    """Raised when job_id not found in backtest_jobs table."""

    pass


class ResultPathMissing(Exception):
    """Raised when job exists but result_path is null or directory missing."""

    pass


@dataclass
class BacktestJob:
    """Backtest job record from Postgres.

    Fields align with db/migrations/0008_create_backtest_jobs.sql schema.
    Note: job_id is VARCHAR(32) per migration (idempotency key from SHA256[:32]).
    """

    # Primary identification
    id: UUID
    job_id: str  # VARCHAR(32) - idempotency key

    # Status
    status: JobStatus

    # Configuration
    alpha_name: str
    start_date: date
    end_date: date
    weight_method: WeightMethod
    config_json: dict[str, Any]

    # Execution metadata
    created_at: datetime  # UTC-aware
    created_by: str
    job_timeout: int  # seconds, 300-14400

    # Optional execution fields (may be None for pending jobs)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    worker_id: str | None = None
    progress_pct: int = 0

    # Result fields (populated on completion)
    result_path: str | None = None
    mean_ic: float | None = None
    icir: float | None = None
    hit_rate: float | None = None
    coverage: float | None = None
    long_short_spread: float | None = None
    average_turnover: float | None = None
    decay_half_life: float | None = None

    # Reproducibility fields (required for completed jobs)
    snapshot_id: str | None = None
    dataset_version_ids: dict[str, str] | None = None

    # Cost model fields (P6T9)
    cost_config: dict[str, Any] | None = None
    cost_summary: dict[str, Any] | None = None

    # Error handling
    error_message: str | None = None
    retry_count: int = 0


def row_to_backtest_job(row: dict[str, Any]) -> BacktestJob:
    """Convert psycopg dict_row to BacktestJob dataclass.

    Handles:
    - UUID conversion from string if needed
    - JSONB to dict conversion (psycopg3 does this automatically)
    - Nullable fields with safe defaults
    - Type coercion for numeric fields

    Args:
        row: Dictionary from psycopg cursor with row_factory=dict_row

    Returns:
        BacktestJob instance

    Raises:
        KeyError: If required fields are missing
        ValueError: If type conversion fails
    """
    # Handle UUID - psycopg3 returns UUID objects directly
    id_raw = row["id"]
    if isinstance(id_raw, UUID):
        id_uuid = id_raw
    else:
        id_uuid = UUID(str(id_raw))

    # Handle dates - psycopg3 returns date objects directly
    start_date = row["start_date"]
    end_date = row["end_date"]
    if not isinstance(start_date, date):
        start_date = date.fromisoformat(str(start_date))
    if not isinstance(end_date, date):
        end_date = date.fromisoformat(str(end_date))

    # Handle config_json - psycopg3 handles JSONB automatically
    config_json = row["config_json"]
    if config_json is None:
        config_json = {}

    # Handle dataset_version_ids - JSONB dict or None
    dataset_version_ids = row.get("dataset_version_ids")
    if dataset_version_ids is not None and not isinstance(dataset_version_ids, dict):
        dataset_version_ids = None

    # Safe numeric conversion for optional float fields
    def safe_float(val: Any) -> float | None:
        if val is None:
            return None
        try:
            return float(val)
        except (TypeError, ValueError):
            return None

    return BacktestJob(
        id=id_uuid,
        job_id=str(row["job_id"]),
        status=row["status"],
        alpha_name=str(row["alpha_name"]),
        start_date=start_date,
        end_date=end_date,
        weight_method=row["weight_method"],
        config_json=config_json,
        created_at=row["created_at"],
        created_by=str(row["created_by"]),
        job_timeout=int(row.get("job_timeout") or 3600),
        started_at=row.get("started_at"),
        completed_at=row.get("completed_at"),
        worker_id=row.get("worker_id"),
        progress_pct=int(row.get("progress_pct") or 0),
        result_path=row.get("result_path"),
        mean_ic=safe_float(row.get("mean_ic")),
        icir=safe_float(row.get("icir")),
        hit_rate=safe_float(row.get("hit_rate")),
        coverage=safe_float(row.get("coverage")),
        long_short_spread=safe_float(row.get("long_short_spread")),
        average_turnover=safe_float(row.get("average_turnover")),
        decay_half_life=safe_float(row.get("decay_half_life")),
        snapshot_id=row.get("snapshot_id"),
        dataset_version_ids=dataset_version_ids,
        cost_config=row.get("cost_config"),
        cost_summary=row.get("cost_summary"),
        error_message=row.get("error_message"),
        retry_count=int(row.get("retry_count") or 0),
    )


__all__ = [
    "BacktestJob",
    "JobNotFound",
    "JobStatus",
    "ResultPathMissing",
    "WeightMethod",
    "row_to_backtest_job",
]
