"""
Pydantic DTO definitions for Web Console data management features.

Covers data sync, dataset exploration, and data quality reporting.
"""

from __future__ import annotations

from typing import Any

from pydantic import AwareDatetime, BaseModel


class SyncStatusDTO(BaseModel):
    """Summary status for a dataset's most recent sync."""

    dataset: str
    last_sync: AwareDatetime | None = None
    row_count: int | None = None
    validation_status: str | None = None
    schema_version: str | None = None


class DataSourceStatusDTO(BaseModel):
    """Status and metadata for a registered data source/provider."""

    name: str
    display_name: str
    provider_type: str
    dataset_key: str | None = None
    status: str
    last_update: AwareDatetime | None = None
    age_seconds: float | None = None
    row_count: int | None = None
    error_rate_pct: float | None = None
    error_message: str | None = None
    is_production_ready: bool
    tables: list[str]


class ShadowResultDTO(BaseModel):
    """Shadow mode validation result."""

    id: str
    model_version: str
    strategy: str
    validation_time: AwareDatetime
    passed: bool
    correlation: float
    mean_abs_diff_ratio: float
    sign_change_rate: float
    sample_count: int
    old_range: float
    new_range: float
    message: str
    correlation_threshold: float
    divergence_threshold: float


class ShadowTrendPointDTO(BaseModel):
    """Single point in shadow validation trend chart."""

    date: AwareDatetime
    correlation: float
    mean_abs_diff_ratio: float
    sign_change_rate: float
    passed: bool


class ShadowTrendDTO(BaseModel):
    """Aggregated shadow validation trend payload."""

    strategy: str
    period_days: int
    data_points: list[ShadowTrendPointDTO]
    total_validations: int
    pass_rate: float
    avg_correlation: float | None
    avg_divergence: float | None


class SyncLogEntry(BaseModel):
    """Log entry emitted during a sync run."""

    id: str
    dataset: str
    level: str
    message: str
    extra: dict[str, Any] | None = None
    sync_run_id: str | None = None
    created_at: AwareDatetime


class SyncScheduleDTO(BaseModel):
    """Sync schedule configuration and current state."""

    id: str
    dataset: str
    enabled: bool
    cron_expression: str
    last_scheduled_run: AwareDatetime | None = None
    next_scheduled_run: AwareDatetime | None = None
    version: int


class SyncScheduleUpdateDTO(BaseModel):
    """Update payload for sync schedule configuration."""

    enabled: bool | None = None
    cron_expression: str | None = None


class SyncJobDTO(BaseModel):
    """Details for an individual sync job execution."""

    id: str
    dataset: str
    status: str
    started_at: AwareDatetime | None = None
    completed_at: AwareDatetime | None = None
    row_count: int | None = None
    error: str | None = None


class DatasetInfoDTO(BaseModel):
    """High-level metadata for a dataset in the explorer."""

    name: str
    description: str | None = None
    row_count: int | None = None
    date_range: dict[str, str] | None = None
    symbol_count: int | None = None
    last_sync: AwareDatetime | None = None


class DataPreviewDTO(BaseModel):
    """Lightweight preview of dataset rows and columns."""

    columns: list[str]
    rows: list[dict[str, Any]]
    total_count: int


class QueryResultDTO(BaseModel):
    """Paginated query results for dataset explorer queries."""

    columns: list[str]
    rows: list[dict[str, Any]]
    total_count: int
    has_more: bool
    cursor: str | None = None


class ExportJobDTO(BaseModel):
    """Metadata for a dataset export job."""

    id: str
    status: str
    format: str
    row_count: int | None = None
    file_path: str | None = None
    expires_at: AwareDatetime | None = None


class ValidationResultDTO(BaseModel):
    """Outcome for a single validation check in a sync run."""

    id: str
    dataset: str
    sync_run_id: str | None = None
    validation_type: str
    status: str
    expected_value: str | float | int | None = None
    actual_value: str | float | int | None = None
    error_message: str | None = None
    created_at: AwareDatetime


class AnomalyAlertDTO(BaseModel):
    """Anomaly alert emitted when data quality thresholds are breached."""

    id: str
    dataset: str
    metric: str
    severity: str
    current_value: float | int
    expected_value: float | int | None = None
    deviation_pct: float | None = None
    message: str
    acknowledged: bool
    acknowledged_by: str | None = None
    created_at: AwareDatetime


class AlertAcknowledgmentDTO(BaseModel):
    """Acknowledgment record for an anomaly alert."""

    id: str
    alert_id: str
    dataset: str
    metric: str
    severity: str
    acknowledged_by: str
    acknowledged_at: AwareDatetime
    reason: str | None = None


class QualityTrendPointDTO(BaseModel):
    """Single data point for quality trend series."""

    date: AwareDatetime
    metric: str
    value: float | int


class QualityTrendDTO(BaseModel):
    """Time-series trend of a quality metric for a dataset."""

    dataset: str
    period_days: int
    data_points: list[QualityTrendPointDTO]


class QuarantineEntryDTO(BaseModel):
    """Read-only summary of a dataset quarantine entry."""

    dataset: str
    quarantine_path: str
    reason: str
    created_at: AwareDatetime
