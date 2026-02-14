"""Pure computation module for data quality scoring and trend analysis.

No I/O — accepts Protocol-based inputs and produces scoring results.
Service DTOs (ValidationResultDTO, AnomalyAlertDTO, QuarantineEntryDTO)
already satisfy these protocols without adapters.

Scoring formula per dataset:
    validation_pass_rate = passed_count / total_validations * 100
    anomaly_penalty = min(unacknowledged_count * 5.0, 30.0)
    quarantine_penalty = min(quarantine_count * 10.0, 20.0)
    overall_score = max(0.0, validation_pass_rate - anomaly_penalty - quarantine_penalty)

Trend direction:
    improving: avg_7d > avg_30d * 1.02
    degrading: avg_7d < avg_30d * 0.98
    stable: otherwise
    degradation_alert: avg_7d < avg_30d * 0.95
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

# Trend direction thresholds (7d avg vs 30d avg multipliers)
TREND_IMPROVING_THRESHOLD = 1.02
TREND_DEGRADING_THRESHOLD = 0.98
DEGRADATION_ALERT_THRESHOLD = 0.95

# ============================================================================
# Protocols (decoupled from service DTOs)
# ============================================================================


@runtime_checkable
class ValidationLike(Protocol):
    dataset: str
    status: str


@runtime_checkable
class AlertLike(Protocol):
    dataset: str
    severity: str
    acknowledged: bool


@runtime_checkable
class QuarantineLike(Protocol):
    dataset: str


@runtime_checkable
class TrendPointLike(Protocol):
    value: float
    date: datetime
    metric: str


@runtime_checkable
class TrendLike(Protocol):
    dataset: str
    period_days: int
    data_points: list[Any]


# ============================================================================
# Data Classes
# ============================================================================


@dataclass
class QualityScore:
    """Computed quality score for a single dataset."""

    dataset: str
    overall_score: float | None  # 0.0 - 100.0, None if no validations
    validation_pass_rate: float | None  # % passed, None if no validations
    anomaly_count: int  # Number of unacknowledged anomalies
    quarantine_count: int  # Number of quarantine entries
    score_breakdown: dict[str, float] = field(default_factory=dict)
    computed_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class TrendSummary:
    """Computed trend summary for a metric."""

    current_score: float | None
    avg_7d: float | None
    avg_30d: float | None
    trend_direction: str  # "improving", "stable", "degrading", "insufficient_data"
    degradation_alert: bool


# ============================================================================
# Status Normalization
# ============================================================================


_STATUS_MAP: dict[str, str] = {
    "ok": "passed",
    "error": "failed",
    "fail": "failed",
    "warn": "warning",
}


def normalize_validation_status(raw: str) -> str:
    """Normalize raw validation status to canonical form.

    Mapping: ok->passed, error/fail->failed, warn->warning.
    Unknown values pass through lowercased.
    """
    return _STATUS_MAP.get(raw.lower(), raw.lower())


# ============================================================================
# Scoring
# ============================================================================


def compute_quality_scores(
    validations: Sequence[ValidationLike],
    alerts: Sequence[AlertLike],
    quarantine: Sequence[QuarantineLike],
) -> list[QualityScore]:
    """Compute quality scores per dataset from raw service data.

    Groups inputs by dataset and computes:
    - validation_pass_rate: % of validations with normalized status "passed"
    - anomaly_penalty: min(unacked_count * 5.0, 30.0)
    - quarantine_penalty: min(quarantine_count * 10.0, 20.0)
    - overall_score: max(0.0, pass_rate - anomaly_penalty - quarantine_penalty)
    """
    # Collect all datasets
    datasets: set[str] = set()
    for v in validations:
        datasets.add(v.dataset)
    for a in alerts:
        datasets.add(a.dataset)
    for q in quarantine:
        datasets.add(q.dataset)

    scores: list[QualityScore] = []
    for ds in sorted(datasets):
        # Validations for this dataset
        ds_validations = [v for v in validations if v.dataset == ds]
        total = len(ds_validations)

        if total == 0:
            validation_pass_rate = None
            overall_score = None
        else:
            passed = sum(
                1
                for v in ds_validations
                if normalize_validation_status(v.status) == "passed"
            )
            validation_pass_rate = (passed / total) * 100.0

        # Unacknowledged anomaly count
        unacked = sum(
            1 for a in alerts if a.dataset == ds and not a.acknowledged
        )
        anomaly_penalty = min(unacked * 5.0, 30.0)

        # Quarantine count
        q_count = sum(1 for q in quarantine if q.dataset == ds)
        quarantine_penalty = min(q_count * 10.0, 20.0)

        # Overall score
        if validation_pass_rate is not None:
            overall_score = max(
                0.0, validation_pass_rate - anomaly_penalty - quarantine_penalty
            )

        scores.append(
            QualityScore(
                dataset=ds,
                overall_score=overall_score,
                validation_pass_rate=validation_pass_rate,
                anomaly_count=unacked,
                quarantine_count=q_count,
                score_breakdown={
                    "validation": validation_pass_rate if validation_pass_rate is not None else 0.0,
                    "anomaly_penalty": anomaly_penalty,
                    "quarantine_penalty": quarantine_penalty,
                },
            )
        )

    return scores


# ============================================================================
# Trend Summary
# ============================================================================


def compute_trend_summary(
    trend: TrendLike,
    metric_name: str,
) -> TrendSummary:
    """Compute trend summary for a specific metric from trend data.

    Filters data_points to metric_name, then computes:
    - current_score: latest data point value
    - avg_7d: mean of last 7 data points
    - avg_30d: mean of all data points (up to 30)
    - trend_direction: improving/stable/degrading based on 7d vs 30d
    - degradation_alert: True if avg_7d < avg_30d * 0.95
    """
    # Filter to metric and sort by date
    points = [p for p in trend.data_points if p.metric == metric_name]
    points.sort(key=lambda p: p.date)

    if len(points) < 2:
        return TrendSummary(
            current_score=points[0].value if points else None,
            avg_7d=None,
            avg_30d=None,
            trend_direction="insufficient_data",
            degradation_alert=False,
        )

    values = [p.value for p in points]
    current = values[-1]
    avg_30d = sum(values) / len(values)

    if len(values) < 7:
        return TrendSummary(
            current_score=current,
            avg_7d=None,
            avg_30d=avg_30d,
            trend_direction="insufficient_data",
            degradation_alert=False,
        )

    avg_7d = sum(values[-7:]) / 7

    # Trend direction
    if avg_7d > avg_30d * TREND_IMPROVING_THRESHOLD:
        direction = "improving"
    elif avg_7d < avg_30d * TREND_DEGRADING_THRESHOLD:
        direction = "degrading"
    else:
        direction = "stable"

    # Degradation alert: >5% decline
    degradation_alert = avg_7d < avg_30d * DEGRADATION_ALERT_THRESHOLD

    return TrendSummary(
        current_score=current,
        avg_7d=avg_7d,
        avg_30d=avg_30d,
        trend_direction=direction,
        degradation_alert=degradation_alert,
    )


__all__ = [
    "AlertLike",
    "DEGRADATION_ALERT_THRESHOLD",
    "QualityScore",
    "QuarantineLike",
    "TREND_DEGRADING_THRESHOLD",
    "TREND_IMPROVING_THRESHOLD",
    "TrendLike",
    "TrendPointLike",
    "TrendSummary",
    "ValidationLike",
    "compute_quality_scores",
    "compute_trend_summary",
    "normalize_validation_status",
]
