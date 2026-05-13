"""Shared Alpaca SIP manifest capability helpers."""

from __future__ import annotations

from libs.web_console_services.data_manifest_service import (
    ALPACA_SIP_CORP_ACTIONS_DATASET,
    ALPACA_SIP_DAILY_DATASET,
    AlpacaSipManifestSummaryDTO,
    ManifestSummaryDTO,
)

SPLIT_ADJUSTMENT_BLOCKING_WARNINGS = frozenset(
    {
        "alpaca_sip_companion_manifest_stale",
        "alpaca_sip_companion_symbol_set_mismatch",
    }
)


def manifest_has_native_returns(manifest: ManifestSummaryDTO | None) -> bool:
    """Return whether a trusted daily manifest already carries adjusted returns."""
    if manifest is None or manifest.validation_status.lower() != "passed":
        return False
    return manifest.read_time_adjustment_mode == "available"


def summary_supports_split_adjustment(summary: AlpacaSipManifestSummaryDTO) -> bool:
    """Return whether Alpaca SIP daily bars can use read-time split adjustment."""
    manifests = {manifest.dataset: manifest for manifest in summary.manifests}
    daily = manifests.get(ALPACA_SIP_DAILY_DATASET)
    corp_actions = manifests.get(ALPACA_SIP_CORP_ACTIONS_DATASET)
    if daily is None or corp_actions is None:
        return False
    if daily.validation_status.lower() != "passed":
        return False
    if corp_actions.validation_status.lower() != "passed":
        return False
    return not any(
        warning in SPLIT_ADJUSTMENT_BLOCKING_WARNINGS for warning in summary.warnings
    )


__all__ = [
    "SPLIT_ADJUSTMENT_BLOCKING_WARNINGS",
    "manifest_has_native_returns",
    "summary_supports_split_adjustment",
]
