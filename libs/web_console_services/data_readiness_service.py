"""Workflow readiness checks for data-management surfaces."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, Literal

from libs.platform.web_console_auth.permissions import (
    Permission,
    has_dataset_permission,
    has_permission,
)
from libs.web_console_services.data_manifest_service import (
    ALPACA_SIP_CORP_ACTIONS_DATASET,
    ALPACA_SIP_DAILY_DATASET,
    ALPACA_SIP_DATASET_KEY,
    AlpacaSipManifestSummaryDTO,
    DataManifestService,
    ManifestSummaryDTO,
)
from libs.web_console_services.schemas.data_management import (
    DataReadinessCheckDTO,
    DataReadinessDTO,
    ReadinessWorkflow,
)

HYBRID_CRSP_SIP_DATASET_KEY = "hybrid_crsp_universe_sip_prices"
CRSP_DATASET_KEY = "crsp"
CRSP_UNIVERSE_MANIFEST_DATASET = "crsp_daily"

RAW_SIP_RETURNS_UNAVAILABLE = "raw_sip_returns_unavailable"
ALPACA_SIP_UNTRUSTED_WITHOUT_MANIFEST = "alpaca_sip_untrusted_without_manifest"
ALPACA_SIP_MANIFEST_VALIDATION_FAILED = "alpaca_sip_manifest_validation_failed"
ALPACA_SIP_COMPANION_MANIFEST_STALE = "alpaca_sip_companion_manifest_stale"
ALPACA_SIP_COMPANION_SYMBOL_SET_MISMATCH = "alpaca_sip_companion_symbol_set_mismatch"
CRSP_UNIVERSE_UNAVAILABLE = "crsp_universe_unavailable"
HYBRID_PRICE_COMPONENT_READY = "hybrid_price_component_ready"
HYBRID_PRICE_COMPONENT_WARNING = "hybrid_price_component_warning"
HYBRID_PRICE_COMPONENT_BLOCKED = "hybrid_price_component_blocked"

_ALPACA_SIP_WORKFLOWS_REQUIRING_RETURNS = {
    "simple_backtest",
    "hybrid_research_backtest",
}


class DataReadinessService:
    """Evaluate workflow-specific data readiness from trusted manifest state."""

    def __init__(self, *, manifest_service: DataManifestService | None = None) -> None:
        self._manifest_service = manifest_service or DataManifestService()

    def get_readiness(
        self,
        user: Any,
        dataset: str,
        workflow: ReadinessWorkflow,
        *,
        alpaca_sip_summary: AlpacaSipManifestSummaryDTO | None = None,
    ) -> DataReadinessDTO:
        """Return readiness for a supported dataset/workflow pair.

        This method performs synchronous manifest I/O. Async callers must use
        get_readiness_async to avoid blocking the event loop.
        """
        _raise_if_running_event_loop()
        if dataset == ALPACA_SIP_DATASET_KEY:
            return self.get_alpaca_sip_readiness(
                user,
                workflow,
                alpaca_sip_summary=alpaca_sip_summary,
            )
        if dataset == HYBRID_CRSP_SIP_DATASET_KEY:
            return self.get_hybrid_crsp_sip_readiness(
                user,
                workflow,
                alpaca_sip_summary=alpaca_sip_summary,
            )
        raise ValueError(f"Unsupported readiness dataset: {dataset}")

    async def get_readiness_async(
        self,
        user: Any,
        dataset: str,
        workflow: ReadinessWorkflow,
        *,
        alpaca_sip_summary: AlpacaSipManifestSummaryDTO | None = None,
    ) -> DataReadinessDTO:
        """Return readiness without blocking the event loop on manifest I/O."""
        return await asyncio.to_thread(
            self.get_readiness,
            user,
            dataset,
            workflow,
            alpaca_sip_summary=alpaca_sip_summary,
        )

    def get_alpaca_sip_readiness(
        self,
        user: Any,
        workflow: ReadinessWorkflow = "simple_backtest",
        *,
        alpaca_sip_summary: AlpacaSipManifestSummaryDTO | None = None,
    ) -> DataReadinessDTO:
        """Return Alpaca SIP readiness with fail-closed raw-return blockers."""
        _raise_if_running_event_loop()
        self._require_dataset_readiness_access(user, ALPACA_SIP_DATASET_KEY)
        summary = alpaca_sip_summary or self._manifest_service.get_alpaca_sip_summary()
        return _build_alpaca_sip_readiness(summary, workflow)

    def get_hybrid_crsp_sip_readiness(
        self,
        user: Any,
        workflow: ReadinessWorkflow = "hybrid_research_backtest",
        *,
        alpaca_sip_summary: AlpacaSipManifestSummaryDTO | None = None,
    ) -> DataReadinessDTO:
        """Return hybrid CRSP-universe plus Alpaca SIP price readiness."""
        _raise_if_running_event_loop()
        self._require_dataset_readiness_access(user, HYBRID_CRSP_SIP_DATASET_KEY)
        has_direct_sip_access = has_dataset_permission(user, ALPACA_SIP_DATASET_KEY)
        summary = alpaca_sip_summary or self._manifest_service.get_alpaca_sip_summary()
        if has_direct_sip_access:
            checks = list(_alpaca_sip_checks(summary, workflow))
        else:
            checks = [_hybrid_price_component_check_from_summary(summary, workflow)]
        crsp_manifest = self._manifest_service.get_manifest_summary(
            CRSP_UNIVERSE_MANIFEST_DATASET
        )
        if crsp_manifest is None:
            checks.append(_crsp_unavailable_check("CRSP universe manifest is missing."))
        elif crsp_manifest.validation_status != "passed":
            checks.append(
                _crsp_unavailable_check(
                    f"CRSP universe manifest status is {crsp_manifest.validation_status}.",
                )
            )
        else:
            checks.append(
                DataReadinessCheckDTO(
                    code="crsp_universe_available",
                    status="passed",
                    message="CRSP universe manifest is trusted.",
                    source="manifest",
                )
            )
        return _readiness_from_checks(
            dataset=HYBRID_CRSP_SIP_DATASET_KEY,
            workflow=workflow,
            checks=checks,
        )

    @staticmethod
    def _require_dataset_readiness_access(user: Any, dataset: str) -> None:
        if not has_permission(user, Permission.VIEW_DATA_SYNC):
            raise PermissionError(f"Permission {Permission.VIEW_DATA_SYNC.value} required")
        if not has_dataset_permission(user, dataset):
            raise PermissionError(f"Dataset access required for {dataset}")


def _raise_if_running_event_loop() -> None:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    raise RuntimeError(
        "DataReadinessService synchronous methods block manifest I/O; "
        "use get_readiness_async from async code."
    )


def _build_alpaca_sip_readiness(
    summary: AlpacaSipManifestSummaryDTO,
    workflow: ReadinessWorkflow,
) -> DataReadinessDTO:
    return _readiness_from_checks(
        dataset=ALPACA_SIP_DATASET_KEY,
        workflow=workflow,
        checks=list(_alpaca_sip_checks(summary, workflow)),
    )


def _alpaca_sip_checks(
    summary: AlpacaSipManifestSummaryDTO,
    workflow: ReadinessWorkflow,
) -> list[DataReadinessCheckDTO]:
    manifests = {manifest.dataset: manifest for manifest in summary.manifests}
    checks: list[DataReadinessCheckDTO] = [
        _manifest_check(
            ALPACA_SIP_DAILY_DATASET,
            manifests.get(ALPACA_SIP_DAILY_DATASET),
            required=True,
        ),
        _manifest_check(
            ALPACA_SIP_CORP_ACTIONS_DATASET,
            manifests.get(ALPACA_SIP_CORP_ACTIONS_DATASET),
            required=workflow in _ALPACA_SIP_WORKFLOWS_REQUIRING_RETURNS,
        ),
    ]

    daily = manifests.get(ALPACA_SIP_DAILY_DATASET)
    requires_returns = workflow in _ALPACA_SIP_WORKFLOWS_REQUIRING_RETURNS
    if requires_returns and _raw_returns_unavailable(daily):
        checks.append(
            DataReadinessCheckDTO(
                code=RAW_SIP_RETURNS_UNAVAILABLE,
                status="blocked",
                message=(
                    "Simple backtests requiring ret or adj_close are blocked while "
                    "Alpaca SIP canonical OHLC is raw and read-time adjustment is unavailable."
                ),
                source="read_time_adjustment_policy",
                action_label="Use CRSP adjusted returns or wait for adjustment layer",
                target_section="backtest",
            )
        )

    for warning in summary.warnings:
        pairing_status: Literal["blocked", "warning"] = (
            "blocked" if requires_returns else "warning"
        )
        if warning == ALPACA_SIP_COMPANION_MANIFEST_STALE:
            checks.append(
                DataReadinessCheckDTO(
                    code=ALPACA_SIP_COMPANION_MANIFEST_STALE,
                    status=pairing_status,
                    message=(
                        "Daily bars and corporate actions manifests are materially out of date "
                        "relative to each other."
                    ),
                    source="manifest_pairing",
                    action_label="Refresh the stale companion dataset",
                    target_section="acquisition",
                )
            )
        elif warning == ALPACA_SIP_COMPANION_SYMBOL_SET_MISMATCH:
            checks.append(
                DataReadinessCheckDTO(
                    code=ALPACA_SIP_COMPANION_SYMBOL_SET_MISMATCH,
                    status=pairing_status,
                    message=(
                        "Daily bars and corporate actions manifests carry different symbol-set "
                        "hashes."
                    ),
                    source="manifest_pairing",
                    action_label="Review manifest cohesion",
                    target_section="quality",
                )
            )
    return checks


def _manifest_check(
    dataset: str,
    manifest: ManifestSummaryDTO | None,
    *,
    required: bool,
) -> DataReadinessCheckDTO:
    if manifest is None:
        status: Literal["warning", "blocked"] = "blocked" if required else "warning"
        return DataReadinessCheckDTO(
            code=ALPACA_SIP_UNTRUSTED_WITHOUT_MANIFEST,
            status=status,
            message=f"{dataset} has no trusted manifest.",
            source="manifest",
            action_label="Run acquisition preflight",
            target_section="acquisition",
        )
    if manifest.validation_status != "passed":
        return DataReadinessCheckDTO(
            code=ALPACA_SIP_MANIFEST_VALIDATION_FAILED,
            status="blocked" if required else "warning",
            message=f"{dataset} manifest validation status is {manifest.validation_status}.",
            source="manifest",
            action_label="Inspect manifest validation",
            target_section="quality",
        )
    return DataReadinessCheckDTO(
        code=f"{dataset}_manifest_trusted",
        status="passed",
        message=f"{dataset} manifest is trusted.",
        source="manifest",
    )


def _raw_returns_unavailable(manifest: ManifestSummaryDTO | None) -> bool:
    if manifest is None:
        return False
    return (
        manifest.canonical_storage_mode == "raw"
        and manifest.read_time_adjustment_mode != "available"
    )


def _crsp_unavailable_check(message: str) -> DataReadinessCheckDTO:
    return DataReadinessCheckDTO(
        code=CRSP_UNIVERSE_UNAVAILABLE,
        status="blocked",
        message=message,
        source="manifest",
        action_label="Acquire or grant CRSP universe data",
        target_section="acquisition",
    )


def _hybrid_price_component_check_from_summary(
    summary: AlpacaSipManifestSummaryDTO,
    workflow: ReadinessWorkflow,
) -> DataReadinessCheckDTO:
    manifests = {manifest.dataset: manifest for manifest in summary.manifests}
    component_states: list[Literal["blocked", "warning"]] = []
    for dataset, required in (
        (ALPACA_SIP_DAILY_DATASET, True),
        (
            ALPACA_SIP_CORP_ACTIONS_DATASET,
            workflow in _ALPACA_SIP_WORKFLOWS_REQUIRING_RETURNS,
        ),
    ):
        manifest = manifests.get(dataset)
        if manifest is None or manifest.validation_status != "passed":
            component_states.append("blocked" if required else "warning")

    daily = manifests.get(ALPACA_SIP_DAILY_DATASET)
    requires_returns = workflow in _ALPACA_SIP_WORKFLOWS_REQUIRING_RETURNS
    if requires_returns and _raw_returns_unavailable(daily):
        component_states.append("blocked")
    if any(
        warning
        in {
            ALPACA_SIP_COMPANION_MANIFEST_STALE,
            ALPACA_SIP_COMPANION_SYMBOL_SET_MISMATCH,
        }
        for warning in summary.warnings
    ):
        component_states.append("blocked" if requires_returns else "warning")

    return _hybrid_price_component_status_check(
        has_blockers="blocked" in component_states,
        has_warnings="warning" in component_states,
    )


def _hybrid_price_component_status_check(
    *,
    has_blockers: bool,
    has_warnings: bool,
) -> DataReadinessCheckDTO:
    if has_blockers:
        return DataReadinessCheckDTO(
            code=HYBRID_PRICE_COMPONENT_BLOCKED,
            status="blocked",
            message="Hybrid price component is not ready.",
            source="manifest",
            action_label="Review hybrid price component acquisition",
            target_section="acquisition",
        )
    if has_warnings:
        return DataReadinessCheckDTO(
            code=HYBRID_PRICE_COMPONENT_WARNING,
            status="warning",
            message="Hybrid price component needs operator review.",
            source="manifest",
            action_label="Review hybrid price component quality",
            target_section="quality",
        )
    return DataReadinessCheckDTO(
        code=HYBRID_PRICE_COMPONENT_READY,
        status="passed",
        message="Hybrid price component is trusted.",
        source="manifest",
    )


def _readiness_from_checks(
    *,
    dataset: str,
    workflow: ReadinessWorkflow,
    checks: list[DataReadinessCheckDTO],
) -> DataReadinessDTO:
    blockers = sorted({check.code for check in checks if check.status == "blocked"})
    warnings = sorted({check.code for check in checks if check.status == "warning"})
    status: Literal["ready", "warning", "blocked"]
    if blockers:
        status = "blocked"
    elif warnings:
        status = "warning"
    else:
        status = "ready"
    return DataReadinessDTO(
        dataset=dataset,
        workflow=workflow,
        status=status,
        generated_at=datetime.now(UTC),
        blockers=blockers,
        warnings=warnings,
        checks=checks,
    )


__all__ = [
    "ALPACA_SIP_COMPANION_MANIFEST_STALE",
    "ALPACA_SIP_COMPANION_SYMBOL_SET_MISMATCH",
    "ALPACA_SIP_MANIFEST_VALIDATION_FAILED",
    "ALPACA_SIP_UNTRUSTED_WITHOUT_MANIFEST",
    "CRSP_DATASET_KEY",
    "CRSP_UNIVERSE_MANIFEST_DATASET",
    "CRSP_UNIVERSE_UNAVAILABLE",
    "DataReadinessService",
    "HYBRID_CRSP_SIP_DATASET_KEY",
    "HYBRID_PRICE_COMPONENT_BLOCKED",
    "HYBRID_PRICE_COMPONENT_READY",
    "HYBRID_PRICE_COMPONENT_WARNING",
    "RAW_SIP_RETURNS_UNAVAILABLE",
]
