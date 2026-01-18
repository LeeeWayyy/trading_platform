"""Tests for web console data management schemas."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from libs.web_console_services.schemas.data_management import (
    AlertAcknowledgmentDTO,
    AnomalyAlertDTO,
    DataPreviewDTO,
    DatasetInfoDTO,
    ExportJobDTO,
    QualityTrendDTO,
    QualityTrendPointDTO,
    QueryResultDTO,
    QuarantineEntryDTO,
    SyncJobDTO,
    SyncLogEntry,
    SyncScheduleDTO,
    SyncScheduleUpdateDTO,
    SyncStatusDTO,
    ValidationResultDTO,
)


@pytest.fixture()
def aware_dt() -> datetime:
    return datetime(2025, 1, 15, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("model_cls", "payload"),
    [
        (
            SyncStatusDTO,
            {
                "dataset": "prices",
                "last_sync": datetime(2025, 1, 1, 0, 0, tzinfo=UTC),
                "row_count": 10,
                "validation_status": "ok",
                "schema_version": "v1",
            },
        ),
        (
            SyncLogEntry,
            {
                "id": "log-1",
                "dataset": "prices",
                "level": "INFO",
                "message": "sync started",
                "created_at": datetime(2025, 1, 1, 0, 0, tzinfo=UTC),
                "extra": {"rows": 10},
            },
        ),
        (
            SyncScheduleDTO,
            {
                "id": "sched-1",
                "dataset": "prices",
                "enabled": True,
                "cron_expression": "0 6 * * *",
                "version": 1,
                "last_scheduled_run": datetime(2025, 1, 1, 0, 0, tzinfo=UTC),
                "next_scheduled_run": datetime(2025, 1, 2, 0, 0, tzinfo=UTC),
            },
        ),
        (
            SyncJobDTO,
            {
                "id": "job-1",
                "dataset": "prices",
                "status": "completed",
                "started_at": datetime(2025, 1, 1, 0, 0, tzinfo=UTC),
                "completed_at": datetime(2025, 1, 1, 1, 0, tzinfo=UTC),
                "row_count": 100,
            },
        ),
        (
            DatasetInfoDTO,
            {
                "name": "prices",
                "description": "Daily prices",
                "row_count": 100,
                "date_range": {"start": "2024-01-01", "end": "2025-01-01"},
                "symbol_count": 5,
                "last_sync": datetime(2025, 1, 1, 0, 0, tzinfo=UTC),
            },
        ),
        (
            DataPreviewDTO,
            {
                "columns": ["symbol", "price"],
                "rows": [{"symbol": "AAPL", "price": 100.0}],
                "total_count": 1,
            },
        ),
        (
            QueryResultDTO,
            {
                "columns": ["symbol", "price"],
                "rows": [{"symbol": "AAPL", "price": 100.0}],
                "total_count": 1,
                "has_more": False,
                "cursor": None,
            },
        ),
        (
            ExportJobDTO,
            {
                "id": "export-1",
                "status": "ready",
                "format": "csv",
                "row_count": 10,
                "file_path": "/tmp/export.csv",
                "expires_at": datetime(2025, 1, 1, 0, 0, tzinfo=UTC),
            },
        ),
        (
            ValidationResultDTO,
            {
                "id": "val-1",
                "dataset": "prices",
                "validation_type": "row_count",
                "status": "pass",
                "expected_value": 10,
                "actual_value": 10,
                "created_at": datetime(2025, 1, 1, 0, 0, tzinfo=UTC),
            },
        ),
        (
            AnomalyAlertDTO,
            {
                "id": "alert-1",
                "dataset": "prices",
                "metric": "row_count",
                "severity": "high",
                "current_value": 120,
                "expected_value": 100,
                "deviation_pct": 20.0,
                "message": "Spike detected",
                "acknowledged": False,
                "created_at": datetime(2025, 1, 1, 0, 0, tzinfo=UTC),
            },
        ),
        (
            AlertAcknowledgmentDTO,
            {
                "id": "ack-1",
                "alert_id": "alert-1",
                "dataset": "prices",
                "metric": "row_count",
                "severity": "high",
                "acknowledged_by": "operator",
                "acknowledged_at": datetime(2025, 1, 1, 0, 0, tzinfo=UTC),
                "reason": "reviewed",
            },
        ),
        (
            QualityTrendPointDTO,
            {
                "date": datetime(2025, 1, 1, 0, 0, tzinfo=UTC),
                "metric": "row_count",
                "value": 100,
            },
        ),
        (
            QualityTrendDTO,
            {
                "dataset": "prices",
                "period_days": 30,
                "data_points": [
                    {
                        "date": datetime(2025, 1, 1, 0, 0, tzinfo=UTC),
                        "metric": "row_count",
                        "value": 100,
                    }
                ],
            },
        ),
        (
            QuarantineEntryDTO,
            {
                "dataset": "prices",
                "quarantine_path": "/tmp/quarantine",
                "reason": "schema mismatch",
                "created_at": datetime(2025, 1, 1, 0, 0, tzinfo=UTC),
            },
        ),
    ],
)
def test_data_management_dtos_accept_valid_payloads(model_cls: type, payload: dict) -> None:
    model = model_cls(**payload)

    assert model.model_dump() is not None


def test_sync_schedule_update_allows_empty_payload() -> None:
    model = SyncScheduleUpdateDTO()

    assert model.enabled is None
    assert model.cron_expression is None


def test_sync_log_entry_requires_aware_datetime(aware_dt: datetime) -> None:
    SyncLogEntry(
        id="log-1",
        dataset="prices",
        level="INFO",
        message="sync started",
        created_at=aware_dt,
    )

    with pytest.raises(ValidationError):
        SyncLogEntry(
            id="log-2",
            dataset="prices",
            level="INFO",
            message="sync started",
            created_at=datetime(2025, 1, 1, 0, 0),
        )
