"""Shared strategy/model execution-gate status semantics."""

from __future__ import annotations

from typing import Final

STRATEGY_SAFE_STATUSES: Final[frozenset[str]] = frozenset({"active", "idle", "ready"})
MODEL_SAFE_STATUSES: Final[frozenset[str]] = frozenset({"active", "testing", "ready"})


def normalize_execution_status(status: str | None) -> str:
    """Normalize incoming status values to lowercase symbolic strings."""
    return str(status or "unknown").strip().lower()


def is_strategy_execution_safe(status: str | None) -> bool:
    """Return True when a strategy status allows risk-increasing execution."""
    return normalize_execution_status(status) in STRATEGY_SAFE_STATUSES


def is_model_execution_safe(status: str | None) -> bool:
    """Return True when a model status allows risk-increasing execution."""
    return normalize_execution_status(status) in MODEL_SAFE_STATUSES


__all__ = [
    "MODEL_SAFE_STATUSES",
    "STRATEGY_SAFE_STATUSES",
    "is_model_execution_safe",
    "is_strategy_execution_safe",
    "normalize_execution_status",
]
