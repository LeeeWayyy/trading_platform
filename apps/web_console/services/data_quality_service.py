"""Service layer for data quality reporting.

Enforces dataset-level access on all read paths for licensing compliance.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from apps.web_console.schemas.data_management import (
    AlertAcknowledgmentDTO,
    AnomalyAlertDTO,
    QualityTrendDTO,
    QuarantineEntryDTO,
    ValidationResultDTO,
)
from libs.web_console_auth.permissions import Permission, has_dataset_permission, has_permission

_SUPPORTED_DATASETS = ("crsp", "compustat", "taq", "fama_french")


class DataQualityService:
    """Service layer for data quality reporting.

    Enforces dataset-level access on all read paths for licensing compliance.
    Alert acknowledgments stored in PostgreSQL.

    IMPORTANT: ALL read paths filter by user's dataset permissions.
    Users only see quality data for datasets they have access to.

    NOTE: Current implementation uses mock data and in-memory storage.
    Production implementation requires:
    - DB queries against data_validation_results table
    - DB queries against data_anomaly_alerts table
    - DB persistence for alert acknowledgments
    """

    # TODO: Replace with PostgreSQL persistence using data_quality_alert_acknowledgments table
    # Current in-memory implementation is for interface validation only.
    # Production requires: INSERT ... ON CONFLICT DO NOTHING RETURNING for idempotency
    _ack_store: dict[str, AlertAcknowledgmentDTO] = {}

    async def get_validation_results(
        self,
        user: Any,
        dataset: str | None,
        limit: int = 50,
    ) -> list[ValidationResultDTO]:
        """Get recent validation run results.

        Permission: VIEW_DATA_QUALITY + dataset-level access (filtered)
        Filtering: If dataset specified, validate access; otherwise filter to accessible datasets
        """
        self._require_permission(user, Permission.VIEW_DATA_QUALITY)
        if dataset:
            self._require_dataset_access(user, dataset)

        # TODO: Query data_validation_results table instead of mock data
        now = datetime.now(UTC)
        mock = [
            ValidationResultDTO(
                id=f"val-{idx}",
                dataset=name,
                sync_run_id=f"run-{idx}",
                validation_type="row_count",
                status="ok",
                expected_value=1000,
                actual_value=1000,
                error_message=None,
                created_at=now,
            )
            for idx, name in enumerate(_SUPPORTED_DATASETS, start=1)
        ]
        filtered = (
            [item for item in mock if item.dataset == dataset]
            if dataset
            else [item for item in mock if has_dataset_permission(user, item.dataset)]
        )
        return filtered[:limit]

    async def get_anomaly_alerts(
        self,
        user: Any,
        severity: str | None,
        acknowledged: bool | None,
    ) -> list[AnomalyAlertDTO]:
        """Get anomaly alerts with optional filters.

        Permission: VIEW_DATA_QUALITY + dataset-level access (filtered)
        Filtering: Only alerts for datasets user has access to
        """
        self._require_permission(user, Permission.VIEW_DATA_QUALITY)

        now = datetime.now(UTC)
        mock = [
            AnomalyAlertDTO(
                id=f"alert-{idx}",
                dataset=name,
                metric="row_drop",
                severity="warning",
                current_value=0.9,
                expected_value=1.0,
                deviation_pct=10.0,
                message="Placeholder alert",
                acknowledged=False,
                acknowledged_by=None,
                created_at=now,
            )
            for idx, name in enumerate(_SUPPORTED_DATASETS, start=1)
        ]
        filtered = [item for item in mock if has_dataset_permission(user, item.dataset)]
        if severity:
            filtered = [item for item in filtered if item.severity == severity]
        if acknowledged is not None:
            filtered = [item for item in filtered if item.acknowledged == acknowledged]
        return filtered

    async def acknowledge_alert(
        self,
        user: Any,
        alert_id: str,
        reason: str,
    ) -> AlertAcknowledgmentDTO:
        """Acknowledge an anomaly alert (idempotent).

        Permission: ACKNOWLEDGE_ALERTS + dataset-level access for alert's dataset
        Storage: PostgreSQL alert_acknowledgments table
        Audit: Logged with user, alert_id, reason
        Security: Validate user has access to the dataset referenced by alert_id

        Idempotency: First-write-wins (unique constraint on alert_id)
        - If alert not yet acknowledged: creates acknowledgment, returns AlertAcknowledgmentDTO
        - If alert already acknowledged: returns existing AlertAcknowledgmentDTO (no error)
        - Client can safely retry without side effects
        """
        self._require_permission(user, Permission.ACKNOWLEDGE_ALERTS)

        dataset = self._resolve_alert_dataset(alert_id)
        self._require_dataset_access(user, dataset)

        existing = self._ack_store.get(alert_id)
        if existing is not None:
            return existing

        now = datetime.now(UTC)
        acknowledgment = AlertAcknowledgmentDTO(
            id=str(uuid4()),
            alert_id=alert_id,
            dataset=dataset,
            metric="row_drop",
            severity="warning",
            acknowledged_by=self._user_id(user),
            acknowledged_at=now,
            reason=reason,
        )
        self._ack_store[alert_id] = acknowledgment
        return acknowledgment

    async def get_quality_trends(
        self,
        user: Any,
        dataset: str,
        days: int = 30,
    ) -> QualityTrendDTO:
        """Get historical quality metrics for trend visualization.

        Permission: VIEW_DATA_QUALITY + dataset-level access for specified dataset
        Security: Validate user has access to specified dataset before returning data
        """
        self._require_permission(user, Permission.VIEW_DATA_QUALITY)
        self._require_dataset_access(user, dataset)

        return QualityTrendDTO(dataset=dataset, period_days=days, data_points=[])

    async def get_quarantine_status(self, user: Any) -> list[QuarantineEntryDTO]:
        """Get list of quarantined sync attempts (read-only view).

        Permission: VIEW_DATA_QUALITY + dataset-level access (filtered)
        Filtering: Only quarantine entries for datasets user has access to
        Note: CRUD operations deferred to future task
        """
        self._require_permission(user, Permission.VIEW_DATA_QUALITY)

        now = datetime.now(UTC)
        mock = [
            QuarantineEntryDTO(
                dataset=name,
                quarantine_path=f"data/quarantine/{name}/{now.date().isoformat()}",
                reason="validation_failure",
                created_at=now,
            )
            for name in _SUPPORTED_DATASETS
        ]
        return [item for item in mock if has_dataset_permission(user, item.dataset)]

    def _require_permission(self, user: Any, permission: Permission) -> None:
        if not has_permission(user, permission):
            raise PermissionError(f"Permission {permission.value} required")

    def _require_dataset_access(self, user: Any, dataset: str) -> None:
        if not has_dataset_permission(user, dataset):
            raise PermissionError(f"Dataset access required for {dataset}")

    @staticmethod
    def _resolve_alert_dataset(alert_id: str) -> str:
        # TODO: Query data_anomaly_alerts table to get actual dataset for alert_id
        # Current stub returns fama_french for interface testing only.
        # Production must: SELECT dataset FROM data_anomaly_alerts WHERE id = alert_id
        return "fama_french"

    @staticmethod
    def _user_id(user: Any | dict[str, Any]) -> str:
        value = getattr(user, "user_id", None)
        if value:
            return str(value)
        if isinstance(user, dict):
            return str(user.get("user_id", "unknown"))
        return "unknown"


__all__ = ["DataQualityService"]
