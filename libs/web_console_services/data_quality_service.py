"""Service layer for data quality reporting.

Enforces dataset-level access on all read paths for licensing compliance.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from libs.platform.web_console_auth.helpers import get_user_id
from libs.platform.web_console_auth.permissions import (
    Permission,
    has_dataset_permission,
    has_permission,
)

from .data_manifest_service import (
    ALPACA_SIP_DATASET_KEY,
    AlpacaSipManifestSummaryDTO,
    DataManifestService,
)
from .data_readiness_service import (
    ALPACA_SIP_COMPANION_MANIFEST_STALE,
    ALPACA_SIP_COMPANION_SYMBOL_SET_MISMATCH,
)
from .schemas.data_management import (
    AlertAcknowledgmentDTO,
    AnomalyAlertDTO,
    DataQualitySignalDTO,
    DataQualitySummaryDTO,
    QualityTrendDTO,
    QuarantineEntryDTO,
    ValidationResultDTO,
)

logger = logging.getLogger(__name__)

_DEFAULT_DATA_ROOT = Path(os.getenv("DATA_ROOT", "data")).resolve()
_SUPPORTED_DATASETS = ("crsp", "compustat", "taq", "fama_french", "alpaca_sip")
_MOCK_QUALITY_DATASETS = tuple(
    dataset for dataset in _SUPPORTED_DATASETS if dataset != ALPACA_SIP_DATASET_KEY
)
_ALPACA_SIP_INTEGRITY_CHECK = "alpaca_sip_integrity"
_ALPACA_FEED_DELTA_CHECK = "alpaca_feed_delta"
_ALPACA_SIP_INTEGRITY_LATEST_REPORT = "alpaca_sip_integrity_latest.json"
_ALPACA_FEED_DELTA_LATEST_REPORT = "alpaca_iex_sip_delta_latest.json"


@dataclass(frozen=True)
class QualityReportState:
    """Latest persisted quality report metadata for a data-quality signal."""

    report_type: str
    status: Literal["passed", "warning", "failed"]
    raw_status: str
    content_hash: str
    start: str
    end: str
    timeframe: str
    path: Path | None = None
    observed_at: datetime | None = None
    tolerance_version: str = ""


class AlpacaQualityReportStore:
    """Read persisted Alpaca SIP quality reports from the local data root."""

    def __init__(self, *, data_root: Path | None = None) -> None:
        self._data_root = data_root.resolve() if data_root is not None else _DEFAULT_DATA_ROOT

    def get_integrity_report(self) -> QualityReportState | None:
        """Return the latest deterministic SIP re-pull integrity report, if present."""
        report_path = self._resolve_report_path(
            env_var="ALPACA_SIP_INTEGRITY_REPORT",
            pattern="alpaca_sip_integrity*.json",
            latest_filename=_ALPACA_SIP_INTEGRITY_LATEST_REPORT,
        )
        if report_path is None:
            return None
        return self._load_report(report_path, expected_report_type="alpaca_sip_integrity")

    def get_feed_delta_report(self) -> QualityReportState | None:
        """Return the latest IEX-vs-SIP feed-delta report, if present."""
        report_path = self._resolve_report_path(
            env_var="ALPACA_FEED_DELTA_REPORT",
            pattern="alpaca_iex_sip_delta*.json",
            latest_filename=_ALPACA_FEED_DELTA_LATEST_REPORT,
        )
        if report_path is None:
            return None
        return self._load_report(report_path, expected_report_type="alpaca_feed_delta")

    def _resolve_report_path(
        self,
        *,
        env_var: str,
        pattern: str,
        latest_filename: str,
    ) -> Path | None:
        configured = os.getenv(env_var, "").strip()
        if configured:
            path = Path(configured).expanduser()
            if path.is_absolute():
                return self._resolve_quality_report_candidate(
                    path,
                    allow_outside_data_root=True,
                )
            return self._resolve_quality_report_candidate(self._data_root / path)

        quality_dir = self._data_root / "quality"
        if not quality_dir.exists():
            return None
        latest_pointer = self._resolve_quality_report_candidate(quality_dir / latest_filename)
        if latest_pointer is not None:
            return latest_pointer
        return self._latest_report_path(quality_dir, pattern)

    def _resolve_quality_report_candidate(
        self,
        candidate: Path,
        *,
        allow_outside_data_root: bool = False,
    ) -> Path | None:
        try:
            resolved = candidate.resolve(strict=True)
        except FileNotFoundError:
            return None
        except OSError as exc:
            logger.debug(
                "alpaca_quality_report_candidate_unavailable",
                extra={"report_path": str(candidate), "error": str(exc)},
            )
            return None
        if not allow_outside_data_root and not resolved.is_relative_to(self._data_root):
            logger.warning(
                "alpaca_quality_report_outside_data_root",
                extra={"path": str(resolved), "data_root": str(self._data_root)},
            )
            return None
        try:
            if not resolved.is_file():
                return None
        except OSError as exc:
            logger.debug(
                "alpaca_quality_report_candidate_unavailable",
                extra={"report_path": str(resolved), "error": str(exc)},
            )
            return None
        return resolved

    def _latest_report_path(self, quality_dir: Path, pattern: str) -> Path | None:
        latest_path: Path | None = None
        latest_key: tuple[float, str] | None = None
        try:
            for candidate in quality_dir.glob(pattern):
                safe_candidate = self._resolve_quality_report_candidate(candidate)
                if safe_candidate is None:
                    continue
                try:
                    candidate_key = (safe_candidate.stat().st_mtime, safe_candidate.name)
                except OSError as exc:
                    logger.debug(
                        "alpaca_quality_report_stat_unavailable",
                        extra={"report_path": str(safe_candidate), "error": str(exc)},
                    )
                    continue
                if latest_key is None or candidate_key > latest_key:
                    latest_key = candidate_key
                    latest_path = safe_candidate
        except OSError as exc:
            logger.debug(
                "alpaca_quality_report_scan_unavailable",
                extra={"quality_dir": str(quality_dir), "pattern": pattern, "error": str(exc)},
            )
        return latest_path

    def _load_report(
        self,
        report_path: Path,
        *,
        expected_report_type: str,
    ) -> QualityReportState | None:
        try:
            payload = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(
                "alpaca_quality_report_unreadable",
                extra={
                    "path": str(report_path),
                    "report_type": expected_report_type,
                    "error": str(exc),
                },
            )
            return None

        if not isinstance(payload, dict) or payload.get("report_type") != expected_report_type:
            return None

        tolerances = payload.get("tolerances")
        tolerance_version = (
            str(tolerances.get("version"))
            if isinstance(tolerances, dict) and tolerances.get("version")
            else ""
        )
        raw_status = str(payload.get("status", "unknown"))
        try:
            observed_at = datetime.fromtimestamp(report_path.stat().st_mtime, UTC)
        except OSError as exc:
            logger.debug(
                "alpaca_quality_report_stat_unavailable",
                extra={"report_path": str(report_path), "error": str(exc)},
            )
            observed_at = None
        return QualityReportState(
            report_type=expected_report_type,
            status=_normalize_report_status(raw_status),
            raw_status=raw_status,
            content_hash=str(payload.get("content_hash", "")),
            start=str(payload.get("start", "")),
            end=str(payload.get("end", "")),
            timeframe=str(payload.get("timeframe", "")),
            path=report_path,
            observed_at=observed_at,
            tolerance_version=tolerance_version,
        )


class DataQualityService:
    """Service layer for data quality reporting.

    Enforces dataset-level access on all read paths for licensing compliance.
    Alert acknowledgments are currently in-memory placeholders.

    IMPORTANT: ALL read paths filter by user's dataset permissions.
    Users only see quality data for datasets they have access to.

    NOTE: Current implementation uses mock data and in-memory storage.
    Production implementation requires:
    - DB queries against data_validation_results table
    - DB queries against data_anomaly_alerts table
    - DB persistence for alert acknowledgments
    """

    def __init__(
        self,
        *,
        manifest_service: DataManifestService | None = None,
        report_store: AlpacaQualityReportStore | None = None,
        data_root: Path | None = None,
        integrity_reports_available: bool | None = None,
        feed_delta_reports_available: bool | None = None,
    ) -> None:
        # TODO: Replace with PostgreSQL persistence using data_quality_alert_acknowledgments table
        # Current in-memory implementation is for interface validation only.
        # Production requires: INSERT ... ON CONFLICT DO NOTHING RETURNING for idempotency
        self._ack_store: dict[str, AlertAcknowledgmentDTO] = {}
        self._manifest_service = manifest_service or DataManifestService()
        self._report_store = report_store or AlpacaQualityReportStore(data_root=data_root)
        self._integrity_reports_available_override = integrity_reports_available
        self._feed_delta_reports_available_override = feed_delta_reports_available
        # Hard-pinned false until durable DB-backed acknowledgment storage lands.
        self._acknowledgments_persistent = False

    @property
    def acknowledgments_persistent(self) -> bool:
        """Whether alert acknowledgments are backed by durable server-side storage."""
        return self._acknowledgments_persistent

    async def get_validation_results(
        self,
        user: Any,
        dataset: str | None,
        limit: int = 50,
        *,
        alpaca_sip_summary: AlpacaSipManifestSummaryDTO | None = None,
    ) -> list[ValidationResultDTO]:
        """Get recent validation run results.

        Permission: VIEW_DATA_QUALITY + dataset-level access (filtered)
        Filtering: If dataset specified, validate access; otherwise filter to accessible datasets
        """
        self._require_permission(user, Permission.VIEW_DATA_QUALITY)
        if dataset:
            self._require_dataset_access(user, dataset)

        # TODO: Query data_validation_results table instead of mock data for non-SIP datasets.
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
            for idx, name in enumerate(_MOCK_QUALITY_DATASETS, start=1)
        ]
        filtered = (
            [item for item in mock if item.dataset == dataset]
            if dataset
            else [item for item in mock if has_dataset_permission(user, item.dataset)]
        )
        alpaca_results: list[ValidationResultDTO] = []
        if (dataset is None and has_dataset_permission(user, ALPACA_SIP_DATASET_KEY)) or (
            dataset == ALPACA_SIP_DATASET_KEY
        ):
            summary = alpaca_sip_summary
            if summary is None:
                summary = await asyncio.to_thread(self._manifest_service.get_alpaca_sip_summary)
            alpaca_results.extend(self._alpaca_sip_validation_results(now, summary))

        return [*alpaca_results, *filtered][:limit]

    async def get_alpaca_sip_quality_summary(
        self,
        user: Any,
        *,
        alpaca_sip_summary: AlpacaSipManifestSummaryDTO | None = None,
    ) -> DataQualitySummaryDTO:
        """Return manifest-backed Alpaca SIP quality state for the data page."""
        self._require_permission(user, Permission.VIEW_DATA_QUALITY)
        self._require_dataset_access(user, ALPACA_SIP_DATASET_KEY)
        summary = alpaca_sip_summary
        if summary is None:
            summary = await asyncio.to_thread(self._manifest_service.get_alpaca_sip_summary)
        integrity_report, feed_delta_report = await asyncio.gather(
            asyncio.to_thread(self._get_integrity_report_state),
            asyncio.to_thread(self._get_feed_delta_report_state),
        )
        return _build_alpaca_sip_quality_summary(
            summary,
            integrity_report=integrity_report,
            feed_delta_report=feed_delta_report,
            acknowledgments_persistent=self.acknowledgments_persistent,
        )

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
            for idx, name in enumerate(_MOCK_QUALITY_DATASETS, start=1)
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
        Storage: in-memory placeholder until PostgreSQL persistence is implemented
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
            acknowledged_by=get_user_id(user),
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
            for name in _MOCK_QUALITY_DATASETS
        ]
        return [item for item in mock if has_dataset_permission(user, item.dataset)]

    def _alpaca_sip_validation_results(
        self,
        observed_at: datetime,
        summary: AlpacaSipManifestSummaryDTO,
    ) -> list[ValidationResultDTO]:
        if summary.latest_sync is not None:
            observed_at = summary.latest_sync
        validation_signal = _manifest_validation_signal(summary)
        pairing_signal = _manifest_pairing_signal(summary)
        return [
            ValidationResultDTO(
                id="alpaca-sip-manifest-summary",
                dataset=ALPACA_SIP_DATASET_KEY,
                sync_run_id=None,
                validation_type="manifest_summary",
                status=_validation_status_from_signal(validation_signal.status),
                expected_value="trusted daily and corporate-actions manifests",
                actual_value=_alpaca_sip_validation_actual_value(
                    summary,
                    validation_signal.status,
                ),
                error_message=(
                    validation_signal.message if validation_signal.status != "passed" else None
                ),
                created_at=observed_at,
            ),
            ValidationResultDTO(
                id="alpaca-sip-manifest-pairing",
                dataset=ALPACA_SIP_DATASET_KEY,
                sync_run_id=None,
                validation_type="manifest_pairing",
                status=_validation_status_from_signal(pairing_signal.status),
                expected_value="paired daily bars and corporate-actions manifests",
                actual_value=pairing_signal.status,
                error_message=pairing_signal.message if pairing_signal.status != "passed" else None,
                created_at=pairing_signal.observed_at or observed_at,
            ),
        ]

    def _require_permission(self, user: Any, permission: Permission) -> None:
        if not has_permission(user, permission):
            raise PermissionError(f"Permission {permission.value} required")

    def _require_dataset_access(self, user: Any, dataset: str) -> None:
        if not has_dataset_permission(user, dataset):
            raise PermissionError(f"Dataset access required for {dataset}")

    def _get_integrity_report_state(self) -> QualityReportState | None:
        report = self._report_store.get_integrity_report()
        if report is not None:
            return report
        return _quality_report_state_from_override(
            _ALPACA_SIP_INTEGRITY_CHECK,
            self._integrity_reports_available_override,
        )

    def _get_feed_delta_report_state(self) -> QualityReportState | None:
        report = self._report_store.get_feed_delta_report()
        if report is not None:
            return report
        return _quality_report_state_from_override(
            _ALPACA_FEED_DELTA_CHECK,
            self._feed_delta_reports_available_override,
        )

    @staticmethod
    def _resolve_alert_dataset(alert_id: str) -> str:
        # TODO: Query data_anomaly_alerts table to get actual dataset for alert_id
        # Production must: SELECT dataset FROM data_anomaly_alerts WHERE id = alert_id
        # Current mock derives dataset from alert ID format "alert-{idx}" where idx maps to dataset
        try:
            idx = int(alert_id.split("-")[1]) - 1
            if 0 <= idx < len(_SUPPORTED_DATASETS):
                return _SUPPORTED_DATASETS[idx]
        except (ValueError, IndexError) as e:
            logger.debug("Failed to parse alert_id '%s': %s", alert_id, e)
        raise ValueError(f"Could not resolve dataset for alert_id: {alert_id}")


__all__ = ["AlpacaQualityReportStore", "DataQualityService", "QualityReportState"]


def _build_alpaca_sip_quality_summary(
    summary: AlpacaSipManifestSummaryDTO,
    *,
    integrity_report: QualityReportState | None = None,
    feed_delta_report: QualityReportState | None = None,
    integrity_reports_available: bool | None = None,
    feed_delta_reports_available: bool | None = None,
    acknowledgments_persistent: bool,
) -> DataQualitySummaryDTO:
    generated_at = datetime.now(UTC)
    integrity_report = integrity_report or _quality_report_state_from_override(
        _ALPACA_SIP_INTEGRITY_CHECK,
        integrity_reports_available,
    )
    feed_delta_report = feed_delta_report or _quality_report_state_from_override(
        _ALPACA_FEED_DELTA_CHECK,
        feed_delta_reports_available,
    )
    signals = [
        _manifest_validation_signal(summary),
        _manifest_pairing_signal(summary),
        _quality_report_signal(
            _ALPACA_SIP_INTEGRITY_CHECK,
            "No persisted Alpaca SIP deterministic re-pull integrity report is available.",
            "Alpaca SIP deterministic re-pull integrity report",
            integrity_report,
            generated_at,
        ),
        _quality_report_signal(
            _ALPACA_FEED_DELTA_CHECK,
            "No persisted Alpaca IEX-vs-SIP feed-delta report is available.",
            "Alpaca IEX-vs-SIP feed-delta report",
            feed_delta_report,
            generated_at,
        ),
        _acknowledgment_persistence_signal(acknowledgments_persistent, generated_at),
    ]
    status: Literal["passed", "warning", "failed", "unavailable"]
    if any(signal.status == "failed" for signal in signals):
        status = "failed"
    elif any(signal.status == "warning" for signal in signals):
        status = "warning"
    elif any(signal.status == "unavailable" for signal in signals):
        status = "unavailable"
    else:
        status = "passed"
    return DataQualitySummaryDTO(
        dataset=ALPACA_SIP_DATASET_KEY,
        status=status,
        generated_at=generated_at,
        signals=signals,
        acknowledgments_persistent=acknowledgments_persistent,
        acknowledgment_status_source=(
            "persistent_store" if acknowledgments_persistent else "in_memory_store_unavailable"
        ),
    )


def _alpaca_sip_validation_actual_value(
    summary: AlpacaSipManifestSummaryDTO,
    signal_status: str,
) -> str:
    if signal_status == "passed":
        return summary.sync_validation_status
    if summary.missing_datasets:
        return summary.sync_validation_status
    return signal_status


def _validation_status_from_signal(
    signal_status: Literal["passed", "warning", "failed", "unavailable"],
) -> Literal["ok", "warning", "error", "unavailable"]:
    if signal_status == "passed":
        return "ok"
    if signal_status == "warning":
        return "warning"
    if signal_status == "unavailable":
        return "unavailable"
    return "error"


def _manifest_validation_signal(summary: AlpacaSipManifestSummaryDTO) -> DataQualitySignalDTO:
    reason_codes: list[str] = []
    if summary.missing_datasets:
        reason_codes.extend(
            f"alpaca_sip_missing_manifest:{item}" for item in summary.missing_datasets
        )
    failed = [manifest for manifest in summary.manifests if manifest.validation_status != "passed"]
    reason_codes.extend(
        f"alpaca_sip_manifest_validation_{manifest.validation_status}:{manifest.dataset}"
        for manifest in failed
    )
    status: Literal["passed", "failed"]
    if not summary.manifests or summary.missing_datasets or failed:
        status = "failed"
    else:
        status = "passed"
    if status == "failed":
        if summary.source_error_message:
            message = summary.source_error_message
        elif summary.missing_datasets:
            message = "Missing Alpaca SIP manifests: " + ", ".join(sorted(summary.missing_datasets))
        elif failed:
            sorted_failed = sorted(failed, key=lambda manifest: manifest.dataset)
            message = "Alpaca SIP manifest validation failed: " + ", ".join(
                f"{manifest.dataset}={manifest.validation_status}" for manifest in sorted_failed
            )
        else:
            message = "No Alpaca SIP manifests found."
    else:
        message = "Alpaca SIP manifests are present and passed validation."
    return DataQualitySignalDTO(
        dataset=ALPACA_SIP_DATASET_KEY,
        check="manifest_validation",
        status=status,
        source="manifest",
        observed_at=summary.latest_sync,
        message=message,
        reason_codes=sorted(reason_codes),
    )


def _manifest_pairing_signal(summary: AlpacaSipManifestSummaryDTO) -> DataQualitySignalDTO:
    # Only pairing warnings are surfaced here; other manifest warnings belong to
    # producer-specific quality checks as they are added.
    pairing_warnings = sorted(
        warning
        for warning in summary.warnings
        if warning
        in {
            ALPACA_SIP_COMPANION_MANIFEST_STALE,
            ALPACA_SIP_COMPANION_SYMBOL_SET_MISMATCH,
        }
    )
    missing_pairing = sorted(
        f"alpaca_sip_missing_manifest:{dataset}" for dataset in summary.missing_datasets
    )
    invalid_pairing = sorted(
        f"alpaca_sip_manifest_validation_{manifest.validation_status}:{manifest.dataset}"
        for manifest in summary.manifests
        if manifest.validation_status != "passed"
    )
    reason_codes = [*missing_pairing, *invalid_pairing, *pairing_warnings]
    status: Literal["passed", "warning", "failed"]
    if missing_pairing:
        status = "failed"
        message = "Companion manifest is missing."
    elif invalid_pairing:
        status = "failed"
        message = "Companion manifest validation failed."
    elif pairing_warnings:
        status = "warning"
        message = "Companion manifests need operator review."
    else:
        status = "passed"
        message = "Daily bars and corporate-actions manifests are paired."
    return DataQualitySignalDTO(
        dataset=ALPACA_SIP_DATASET_KEY,
        check="manifest_pairing",
        status=status,
        source="manifest",
        observed_at=summary.latest_sync,
        message=message,
        reason_codes=sorted(reason_codes),
    )


def _normalize_report_status(raw_status: str) -> Literal["passed", "warning", "failed"]:
    normalized = raw_status.lower()
    if normalized == "passed":
        return "passed"
    if normalized == "failed":
        return "failed"
    return "warning"


def _quality_report_state_from_override(
    check: str,
    available: bool | None,
) -> QualityReportState | None:
    if available is not True:
        return None
    return QualityReportState(
        report_type=check,
        status="passed",
        raw_status="passed",
        content_hash="",
        start="",
        end="",
        timeframe="",
    )


def _quality_report_signal(
    check: str,
    unavailable_message: str,
    display_name: str,
    report: QualityReportState | None,
    observed_at: datetime,
) -> DataQualitySignalDTO:
    if report is not None:
        reason_codes = [f"{check}_report_{report.raw_status}"]
        if report.content_hash:
            reason_codes.append(f"{check}_hash:{report.content_hash}")
        if report.timeframe:
            reason_codes.append(f"{check}_timeframe:{report.timeframe}")
        if report.tolerance_version:
            reason_codes.append(f"{check}_tolerance:{report.tolerance_version}")
        return DataQualitySignalDTO(
            dataset=ALPACA_SIP_DATASET_KEY,
            check=check,
            status=report.status,
            source="report_store",
            observed_at=report.observed_at or observed_at,
            message=_quality_report_message(display_name, report),
            reason_codes=reason_codes,
        )
    return DataQualitySignalDTO(
        dataset=ALPACA_SIP_DATASET_KEY,
        check=check,
        status="unavailable",
        source="report_store",
        observed_at=None,
        message=unavailable_message,
        reason_codes=[f"{check}_report_unavailable"],
    )


def _quality_report_message(display_name: str, report: QualityReportState) -> str:
    status_text = f"status is {report.raw_status}"
    details: list[str] = []
    if report.timeframe:
        details.append(f"timeframe={report.timeframe}")
    if report.start or report.end:
        details.append(f"window={report.start or '-'}..{report.end or '-'}")
    if report.content_hash:
        details.append(f"hash={report.content_hash[:16]}")
    suffix = f" ({', '.join(details)})" if details else ""
    return f"{display_name} {status_text}{suffix}."


def _acknowledgment_persistence_signal(
    acknowledgments_persistent: bool,
    observed_at: datetime,
) -> DataQualitySignalDTO:
    if acknowledgments_persistent:
        return DataQualitySignalDTO(
            dataset=ALPACA_SIP_DATASET_KEY,
            check="quality_acknowledgment_persistence",
            status="passed",
            source="persistent_store",
            observed_at=observed_at,
            message="Quality acknowledgments are persisted server-side.",
            reason_codes=[],
        )
    return DataQualitySignalDTO(
        dataset=ALPACA_SIP_DATASET_KEY,
        check="quality_acknowledgment_persistence",
        status="unavailable",
        source="in_memory_store",
        observed_at=None,
        message=(
            "Quality acknowledgment controls are unavailable until server-side "
            "persistence records actor, time, source, and issue scope."
        ),
        reason_codes=["quality_acknowledgment_persistence_unavailable"],
    )
