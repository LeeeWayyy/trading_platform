"""Tests for quality_scorer.py (P6T13/T13.4).

Tests cover:
- normalize_validation_status: ok->passed, error->failed, unknown pass-through
- compute_quality_scores: various validation/alert/quarantine combinations
- compute_trend_summary: trend direction, degradation alerts, insufficient data
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from libs.data.data_quality.quality_scorer import (
    compute_quality_scores,
    compute_trend_summary,
    normalize_validation_status,
)

# ============================================================================
# Test Data Helpers
# ============================================================================


@dataclass
class FakeValidation:
    dataset: str
    status: str


@dataclass
class FakeAlert:
    dataset: str
    severity: str
    acknowledged: bool


@dataclass
class FakeQuarantine:
    dataset: str


@dataclass
class FakeTrendPoint:
    value: float
    date: datetime
    metric: str


@dataclass
class FakeTrend:
    dataset: str
    period_days: int
    data_points: list[FakeTrendPoint]


# ============================================================================
# normalize_validation_status
# ============================================================================


class TestNormalizeValidationStatus:
    def test_ok_to_passed(self) -> None:
        assert normalize_validation_status("ok") == "passed"

    def test_OK_uppercase(self) -> None:
        assert normalize_validation_status("OK") == "passed"

    def test_error_to_failed(self) -> None:
        assert normalize_validation_status("error") == "failed"

    def test_fail_to_failed(self) -> None:
        assert normalize_validation_status("fail") == "failed"

    def test_warn_to_warning(self) -> None:
        assert normalize_validation_status("warn") == "warning"

    def test_unknown_passes_through_lowercased(self) -> None:
        assert normalize_validation_status("CustomStatus") == "customstatus"

    def test_passed_passes_through(self) -> None:
        assert normalize_validation_status("passed") == "passed"


# ============================================================================
# compute_quality_scores
# ============================================================================


class TestComputeQualityScores:
    def test_all_passed(self) -> None:
        validations = [
            FakeValidation("ds1", "ok"),
            FakeValidation("ds1", "ok"),
        ]
        scores = compute_quality_scores(validations, [], [])
        assert len(scores) == 1
        s = scores[0]
        assert s.dataset == "ds1"
        assert s.validation_pass_rate == 100.0
        assert s.overall_score == 100.0
        assert s.anomaly_count == 0
        assert s.quarantine_count == 0

    def test_all_failed(self) -> None:
        validations = [
            FakeValidation("ds1", "error"),
            FakeValidation("ds1", "fail"),
        ]
        scores = compute_quality_scores(validations, [], [])
        assert scores[0].validation_pass_rate == 0.0
        assert scores[0].overall_score == 0.0

    def test_mixed_validations(self) -> None:
        validations = [
            FakeValidation("ds1", "ok"),
            FakeValidation("ds1", "error"),
        ]
        scores = compute_quality_scores(validations, [], [])
        assert scores[0].validation_pass_rate == 50.0
        assert scores[0].overall_score == 50.0

    def test_zero_validations_gives_none(self) -> None:
        alerts = [FakeAlert("ds1", "warning", False)]
        scores = compute_quality_scores([], alerts, [])
        assert scores[0].overall_score is None
        assert scores[0].validation_pass_rate is None

    def test_anomaly_penalty(self) -> None:
        validations = [FakeValidation("ds1", "ok")] * 10
        alerts = [
            FakeAlert("ds1", "warning", False),
            FakeAlert("ds1", "high", False),
        ]
        scores = compute_quality_scores(validations, alerts, [])
        # pass_rate=100, anomaly_penalty=2*5=10, quarantine=0
        assert scores[0].overall_score == 90.0
        assert scores[0].anomaly_count == 2

    def test_anomaly_penalty_cap(self) -> None:
        validations = [FakeValidation("ds1", "ok")] * 10
        alerts = [FakeAlert("ds1", "high", False)] * 10  # 10*5=50, capped at 30
        scores = compute_quality_scores(validations, alerts, [])
        assert scores[0].overall_score == 70.0  # 100 - 30

    def test_acknowledged_alerts_not_counted(self) -> None:
        validations = [FakeValidation("ds1", "ok")]
        alerts = [
            FakeAlert("ds1", "high", True),  # Acknowledged
            FakeAlert("ds1", "high", False),  # Unacknowledged
        ]
        scores = compute_quality_scores(validations, alerts, [])
        assert scores[0].anomaly_count == 1

    def test_quarantine_penalty(self) -> None:
        validations = [FakeValidation("ds1", "ok")] * 10
        quarantine = [FakeQuarantine("ds1"), FakeQuarantine("ds1")]
        scores = compute_quality_scores(validations, [], quarantine)
        # pass_rate=100, quarantine_penalty=2*10=20
        assert scores[0].overall_score == 80.0
        assert scores[0].quarantine_count == 2

    def test_quarantine_penalty_cap(self) -> None:
        validations = [FakeValidation("ds1", "ok")] * 10
        quarantine = [FakeQuarantine("ds1")] * 5  # 5*10=50, capped at 20
        scores = compute_quality_scores(validations, [], quarantine)
        assert scores[0].overall_score == 80.0  # 100 - 20

    def test_multiple_datasets(self) -> None:
        validations = [
            FakeValidation("ds1", "ok"),
            FakeValidation("ds2", "error"),
        ]
        scores = compute_quality_scores(validations, [], [])
        assert len(scores) == 2
        ds1 = next(s for s in scores if s.dataset == "ds1")
        ds2 = next(s for s in scores if s.dataset == "ds2")
        assert ds1.overall_score == 100.0
        assert ds2.overall_score == 0.0

    def test_score_breakdown(self) -> None:
        validations = [FakeValidation("ds1", "ok")] * 10
        alerts = [FakeAlert("ds1", "high", False)]
        quarantine = [FakeQuarantine("ds1")]
        scores = compute_quality_scores(validations, alerts, quarantine)
        bd = scores[0].score_breakdown
        assert bd["validation"] == 100.0
        assert bd["anomaly_penalty"] == 5.0
        assert bd["quarantine_penalty"] == 10.0

    def test_overall_score_never_negative(self) -> None:
        validations = [FakeValidation("ds1", "error")] * 10  # 0% pass rate
        alerts = [FakeAlert("ds1", "high", False)] * 10  # 30 penalty
        quarantine = [FakeQuarantine("ds1")] * 5  # 20 penalty
        scores = compute_quality_scores(validations, alerts, quarantine)
        assert scores[0].overall_score == 0.0

    def test_raw_ok_counted_as_passed(self) -> None:
        """Raw 'ok' from service must be counted as 'passed' (no external pre-normalization)."""
        validations = [FakeValidation("ds1", "ok")]
        scores = compute_quality_scores(validations, [], [])
        assert scores[0].validation_pass_rate == 100.0


# ============================================================================
# compute_trend_summary
# ============================================================================


def _make_trend(values: list[float], metric: str = "quality") -> FakeTrend:
    base = datetime(2024, 1, 1, tzinfo=UTC)
    points = [
        FakeTrendPoint(
            value=v,
            date=base + timedelta(days=i),
            metric=metric,
        )
        for i, v in enumerate(values)
    ]
    return FakeTrend(dataset="ds1", period_days=30, data_points=points)


class TestComputeTrendSummary:
    def test_empty_data(self) -> None:
        trend = FakeTrend(dataset="ds1", period_days=30, data_points=[])
        result = compute_trend_summary(trend, "quality")
        assert result.current_score is None
        assert result.avg_7d is None
        assert result.avg_30d is None
        assert result.trend_direction == "insufficient_data"
        assert result.degradation_alert is False

    def test_single_point(self) -> None:
        trend = _make_trend([95.0])
        result = compute_trend_summary(trend, "quality")
        assert result.current_score == 95.0
        assert result.avg_7d is None
        assert result.trend_direction == "insufficient_data"

    def test_two_to_six_points(self) -> None:
        trend = _make_trend([90.0, 91.0, 92.0, 93.0, 94.0, 95.0])
        result = compute_trend_summary(trend, "quality")
        assert result.current_score == 95.0
        assert result.avg_7d is None  # < 7 points
        assert result.avg_30d is not None
        assert result.trend_direction == "insufficient_data"

    def test_stable_trend(self) -> None:
        # All values same -> stable
        values = [90.0] * 10
        trend = _make_trend(values)
        result = compute_trend_summary(trend, "quality")
        assert result.trend_direction == "stable"
        assert result.degradation_alert is False

    def test_improving_trend(self) -> None:
        # Low values early, high values in last 7
        values = [70.0] * 23 + [95.0] * 7
        trend = _make_trend(values)
        result = compute_trend_summary(trend, "quality")
        assert result.trend_direction == "improving"

    def test_degrading_trend(self) -> None:
        # High values early, low values in last 7
        values = [95.0] * 23 + [70.0] * 7
        trend = _make_trend(values)
        result = compute_trend_summary(trend, "quality")
        assert result.trend_direction == "degrading"
        assert result.degradation_alert is True

    def test_degradation_alert_threshold(self) -> None:
        # avg_30d = (23*100 + 7*90)/30 = 2930/30 ≈ 97.67
        # avg_7d = 90.0
        # threshold = 97.67 * 0.95 ≈ 92.78
        # 90.0 < 92.78 -> True
        values = [100.0] * 23 + [90.0] * 7
        trend = _make_trend(values)
        result = compute_trend_summary(trend, "quality")
        assert result.degradation_alert is True

    def test_no_degradation_at_boundary(self) -> None:
        # avg_7d just above 95% of avg_30d
        values = [100.0] * 23 + [96.0] * 7
        trend = _make_trend(values)
        result = compute_trend_summary(trend, "quality")
        assert result.degradation_alert is False

    def test_filters_by_metric(self) -> None:
        base = datetime(2024, 1, 1, tzinfo=UTC)
        points = [
            FakeTrendPoint(value=90.0, date=base + timedelta(days=i), metric="quality")
            for i in range(10)
        ] + [
            FakeTrendPoint(value=50.0, date=base + timedelta(days=i), metric="other")
            for i in range(10)
        ]
        trend = FakeTrend(dataset="ds1", period_days=30, data_points=points)
        result = compute_trend_summary(trend, "quality")
        assert result.current_score == 90.0
        assert result.avg_7d == 90.0
