"""Unit tests for ShadowResultsService (P6T14/T14.4)."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from libs.platform.web_console_auth.permissions import Role
from libs.web_console_services.shadow_results_service import (
    _MAX_RESULTS_LIMIT,
    _MAX_TREND_DAYS,
    ShadowResultsService,
)


@dataclass(frozen=True)
class DummyUser:
    user_id: str
    role: Role


@pytest.fixture()
def researcher_user() -> DummyUser:
    return DummyUser(user_id="researcher-1", role=Role.RESEARCHER)


@pytest.fixture()
def viewer_user() -> DummyUser:
    return DummyUser(user_id="viewer-1", role=Role.VIEWER)


@pytest.mark.asyncio()
async def test_get_recent_results_returns_valid_mock_data(researcher_user: DummyUser) -> None:
    service = ShadowResultsService(_rng_seed=42)

    results = await service.get_recent_results(researcher_user, limit=25)

    assert len(results) == 25
    assert all(result.id for result in results)
    assert all(result.model_version for result in results)
    assert all(result.strategy for result in results)


@pytest.mark.asyncio()
async def test_get_recent_results_respects_limit(researcher_user: DummyUser) -> None:
    service = ShadowResultsService(_rng_seed=42)

    results = await service.get_recent_results(researcher_user, limit=7)

    assert len(results) == 7


@pytest.mark.asyncio()
async def test_get_recent_results_clamps_limit(researcher_user: DummyUser) -> None:
    service = ShadowResultsService(_rng_seed=42)

    below = await service.get_recent_results(researcher_user, limit=0)
    above = await service.get_recent_results(researcher_user, limit=999)

    assert len(below) == 1
    assert len(above) == _MAX_RESULTS_LIMIT


@pytest.mark.asyncio()
async def test_get_trend_returns_data_points(researcher_user: DummyUser) -> None:
    service = ShadowResultsService(_rng_seed=42)

    trend = await service.get_trend(researcher_user, days=30)

    assert trend.period_days == 30
    assert trend.total_validations == len(trend.data_points)
    assert len(trend.data_points) > 0


@pytest.mark.asyncio()
async def test_get_trend_respects_days_parameter(researcher_user: DummyUser) -> None:
    service = ShadowResultsService(_rng_seed=42)

    trend = await service.get_trend(researcher_user, days=5)

    assert trend.period_days == 5


@pytest.mark.asyncio()
async def test_get_trend_clamps_days(researcher_user: DummyUser) -> None:
    service = ShadowResultsService(_rng_seed=42)

    low = await service.get_trend(researcher_user, days=0)
    high = await service.get_trend(researcher_user, days=999)

    assert low.period_days == 1
    assert high.period_days == _MAX_TREND_DAYS


@pytest.mark.asyncio()
async def test_permission_denied_without_view_shadow_results(viewer_user: DummyUser) -> None:
    service = ShadowResultsService(_rng_seed=42)

    with pytest.raises(PermissionError, match="view_shadow_results"):
        await service.get_recent_results(viewer_user)

    with pytest.raises(PermissionError, match="view_shadow_results"):
        await service.get_trend(viewer_user)


@pytest.mark.asyncio()
async def test_metric_ranges_in_bounds(researcher_user: DummyUser) -> None:
    service = ShadowResultsService(_rng_seed=42)

    results = await service.get_recent_results(researcher_user, limit=120)

    for result in results:
        assert 0.0 <= result.correlation <= 1.0
        assert result.mean_abs_diff_ratio >= 0.0
        assert 0.0 <= result.sign_change_rate <= 1.0


@pytest.mark.asyncio()
async def test_deterministic_pass_rate_seeded(researcher_user: DummyUser) -> None:
    service = ShadowResultsService(_rng_seed=42)

    results = await service.get_recent_results(researcher_user, limit=40)
    passed_count = sum(1 for item in results if item.passed)

    assert passed_count == 35


@pytest.mark.asyncio()
async def test_trend_pass_rate_and_averages_from_points(researcher_user: DummyUser) -> None:
    service = ShadowResultsService(_rng_seed=42)

    trend = await service.get_trend(researcher_user, days=21)

    if trend.data_points:
        expected_pass_rate = (
            sum(1 for point in trend.data_points if point.passed) / len(trend.data_points) * 100.0
        )
        expected_avg_corr = sum(point.correlation for point in trend.data_points) / len(
            trend.data_points
        )
        expected_avg_div = sum(point.mean_abs_diff_ratio for point in trend.data_points) / len(
            trend.data_points
        )
        assert trend.pass_rate == pytest.approx(round(expected_pass_rate, 2))
        assert trend.avg_correlation == pytest.approx(round(expected_avg_corr, 4))
        assert trend.avg_divergence == pytest.approx(round(expected_avg_div, 4))


@pytest.mark.asyncio()
async def test_trend_handles_zero_results(researcher_user: DummyUser, monkeypatch: pytest.MonkeyPatch) -> None:
    service = ShadowResultsService(_rng_seed=42)

    monkeypatch.setattr(service, "_generate_trend_points", lambda **_kwargs: [])

    trend = await service.get_trend(researcher_user, days=30)

    assert trend.data_points == []
    assert trend.total_validations == 0
    assert trend.pass_rate == 0.0
    assert trend.avg_correlation is None
    assert trend.avg_divergence is None


@pytest.mark.asyncio()
async def test_summary_metrics_strategy_passthrough(researcher_user: DummyUser) -> None:
    service = ShadowResultsService(_rng_seed=42)

    results = await service.get_recent_results(researcher_user, strategy="momentum_v2", limit=5)
    trend = await service.get_trend(researcher_user, strategy="momentum_v2", days=5)

    assert all(result.strategy == "momentum_v2" for result in results)
    assert trend.strategy == "momentum_v2"
