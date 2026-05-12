"""Shared helpers for data-management UI components."""

from __future__ import annotations

from typing import Any

from libs.web_console_services.data_manifest_service import (
    ALPACA_SIP_CORP_ACTIONS_DATASET,
    ALPACA_SIP_DAILY_DATASET,
    AlpacaSipManifestSummaryDTO,
)

TREND_DATASETS: tuple[str, ...] = (
    "crsp",
    "compustat",
    "taq",
    "fama_french",
    "alpaca_sip",
)
_SPLIT_ADJUSTMENT_BLOCKING_WARNINGS = frozenset(
    {
        "alpaca_sip_companion_manifest_stale",
        "alpaca_sip_companion_symbol_set_mismatch",
    }
)


def format_datetime(dt: Any) -> str:
    """Format a datetime for display, handling None and non-datetime values."""
    if dt is not None and hasattr(dt, "isoformat"):
        return str(dt.isoformat())
    return "-"


def get_user_id_safe(user: Any) -> str | None:
    """Extract user_id from user dict or object safely."""
    if isinstance(user, dict):
        val = user.get("id")
        return str(val) if val is not None else None
    val = getattr(user, "id", None)
    return str(val) if val is not None else None


def summary_supports_split_adjustment(summary: AlpacaSipManifestSummaryDTO) -> bool:
    """Return whether Alpaca SIP daily bars can use read-time split adjustment."""
    manifests = {manifest.dataset: manifest for manifest in summary.manifests}
    daily = manifests.get(ALPACA_SIP_DAILY_DATASET)
    corp_actions = manifests.get(ALPACA_SIP_CORP_ACTIONS_DATASET)
    if daily is None or corp_actions is None:
        return False
    if daily.validation_status != "passed" or corp_actions.validation_status != "passed":
        return False
    return not any(warning in _SPLIT_ADJUSTMENT_BLOCKING_WARNINGS for warning in summary.warnings)


__all__ = [
    "TREND_DATASETS",
    "format_datetime",
    "get_user_id_safe",
    "summary_supports_split_adjustment",
]
