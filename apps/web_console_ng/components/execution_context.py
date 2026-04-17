"""Execution context snapshot and gate evaluation helpers.

This module centralizes the typed snapshot passed from dashboard context
resolution into order-entry components.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict

from apps.web_console_ng.components.execution_gate import (
    is_model_execution_safe,
    is_strategy_execution_safe,
    normalize_execution_status,
)

EXECUTION_CONTEXT_READY = "READY"
EXECUTION_CONTEXT_BLOCKED = "BLOCKED"
EXECUTION_CONTEXT_STALE = "STALE"


class ExecutionContextSnapshot(BaseModel):
    """UI-level strategy/model context for the currently selected symbol."""

    model_config = ConfigDict(extra="forbid")

    symbol: str | None
    strategy_id: str | None
    strategy_status: str
    model_status: str
    model_version: str | None
    signal_id: str | None
    data_freshness_s: float | None
    risk_gate_state: str
    updated_at: datetime
    gate_reason: str | None = None


def compute_execution_context_gate_state(
    *,
    strategy_status: str | None,
    model_status: str | None,
    gate_reason: str | None,
    data_freshness_s: float | None,
    freshness_threshold_s: float,
) -> tuple[str, str | None]:
    """Return normalized gate state and optional reason."""
    normalized_strategy = normalize_execution_status(strategy_status)
    normalized_model = normalize_execution_status(model_status)

    if data_freshness_s is None:
        return (EXECUTION_CONTEXT_STALE, "market data freshness unavailable")
    if data_freshness_s > freshness_threshold_s:
        return (
            EXECUTION_CONTEXT_STALE,
            f"market data stale ({data_freshness_s:.0f}s old)",
        )

    if gate_reason:
        return (EXECUTION_CONTEXT_BLOCKED, gate_reason)

    if not is_strategy_execution_safe(normalized_strategy):
        return (
            EXECUTION_CONTEXT_BLOCKED,
            f"strategy is {normalized_strategy.upper()}",
        )
    if not is_model_execution_safe(normalized_model):
        return (
            EXECUTION_CONTEXT_BLOCKED,
            f"model is {normalized_model.upper()}",
        )
    return (EXECUTION_CONTEXT_READY, None)


def build_execution_context_snapshot(
    *,
    symbol: str | None,
    strategy_id: str | None,
    strategy_status: str | None,
    model_status: str | None,
    model_version: str | None,
    signal_id: str | None,
    data_freshness_s: float | None,
    gate_reason: str | None,
    freshness_threshold_s: float,
) -> ExecutionContextSnapshot:
    """Create snapshot with normalized statuses and derived gate state."""
    normalized_strategy = normalize_execution_status(strategy_status)
    normalized_model = normalize_execution_status(model_status)
    risk_gate_state, derived_reason = compute_execution_context_gate_state(
        strategy_status=normalized_strategy,
        model_status=normalized_model,
        gate_reason=gate_reason,
        data_freshness_s=data_freshness_s,
        freshness_threshold_s=freshness_threshold_s,
    )
    return ExecutionContextSnapshot(
        symbol=symbol,
        strategy_id=strategy_id,
        strategy_status=normalized_strategy,
        model_status=normalized_model,
        model_version=model_version,
        signal_id=signal_id,
        data_freshness_s=data_freshness_s,
        risk_gate_state=risk_gate_state,
        updated_at=datetime.now(UTC),
        gate_reason=derived_reason,
    )


def format_execution_context_ribbon(snapshot: ExecutionContextSnapshot | None) -> tuple[str, str]:
    """Return compact text and tone for order-ticket context ribbon."""
    if snapshot is None:
        return ("Context: --", "warning")

    strategy_id = snapshot.strategy_id or "unresolved"
    version = snapshot.model_version or "unassigned"
    freshness = (
        f"{snapshot.data_freshness_s:.0f}s"
        if snapshot.data_freshness_s is not None
        else "--"
    )
    tone = "normal" if snapshot.risk_gate_state == EXECUTION_CONTEXT_READY else "warning"
    text = (
        f"{snapshot.risk_gate_state} · {strategy_id} · "
        f"model {version} · fresh {freshness}"
    )
    if snapshot.gate_reason:
        text = f"{text} · {snapshot.gate_reason}"
    return (text, tone)
