"""
Tests for T2: Signal Service Startup Hydration.

Validates:
- Hydration handles per-date failures without crashing.
- Health reports "degraded" until hydration completes.
- Readiness returns 503 when degraded.
"""

from datetime import UTC, date, datetime
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException


def test_hydrate_feature_cache_handles_date_failures() -> None:
    """Hydration should continue even if one date fails."""
    from apps.signal_service.signal_generator import SignalGenerator

    with pytest.MonkeyPatch().context() as monkeypatch:
        monkeypatch.setattr(SignalGenerator, "__init__", lambda self, **kw: None)
        generator = SignalGenerator()
        generator.feature_cache = MagicMock()
        generator.data_provider = MagicMock()
        generator.data_provider.get_date_range.return_value = (
            date(2024, 1, 1),
            date(2024, 1, 15),
        )

        def precompute_side_effect(*, symbols, as_of_date):  # type: ignore[no-untyped-def]
            if as_of_date.date() == date(2024, 1, 15):
                raise RuntimeError("boom")
            return {
                "cached_count": 1,
                "skipped_count": 0,
                "symbols_cached": ["AAPL"],
                "symbols_skipped": [],
            }

        generator.precompute_features = MagicMock(side_effect=precompute_side_effect)

        result = generator.hydrate_feature_cache(
            symbols=["AAPL"],
            history_days=2,
            end_date=datetime(2024, 1, 15, tzinfo=UTC),
        )

        assert generator.precompute_features.call_count == 2
        assert result["dates_attempted"] == 2
        assert result["dates_failed"] == 1
        assert result["cached_count"] == 1


@pytest.mark.asyncio
async def test_health_reports_degraded_when_hydration_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Health should return degraded while hydration is running."""
    import apps.signal_service.main as main

    metadata = MagicMock(strategy_name="alpha_baseline", version="v1.0.0", activated_at=None)
    monkeypatch.setattr(main, "model_registry", MagicMock(is_loaded=True, current_metadata=metadata))
    monkeypatch.setattr(main, "signal_generator", MagicMock())
    redis_client = MagicMock()
    redis_client.health_check.return_value = True
    monkeypatch.setattr(main, "redis_client", redis_client)
    monkeypatch.setattr(main, "feature_cache", MagicMock())
    monkeypatch.setattr(main.settings, "redis_enabled", True)
    monkeypatch.setattr(main.settings, "feature_hydration_enabled", True)
    monkeypatch.setattr(main, "hydration_complete", False)

    response = await main.health_check()
    assert response.status == "degraded"


@pytest.mark.asyncio
async def test_ready_returns_503_when_degraded(monkeypatch: pytest.MonkeyPatch) -> None:
    """Readiness should fail when hydration is incomplete."""
    import apps.signal_service.main as main

    metadata = MagicMock(strategy_name="alpha_baseline", version="v1.0.0", activated_at=None)
    monkeypatch.setattr(main, "model_registry", MagicMock(is_loaded=True, current_metadata=metadata))
    monkeypatch.setattr(main, "signal_generator", MagicMock())
    redis_client = MagicMock()
    redis_client.health_check.return_value = True
    monkeypatch.setattr(main, "redis_client", redis_client)
    monkeypatch.setattr(main, "feature_cache", MagicMock())
    monkeypatch.setattr(main.settings, "redis_enabled", True)
    monkeypatch.setattr(main.settings, "feature_hydration_enabled", True)
    monkeypatch.setattr(main, "hydration_complete", False)

    with pytest.raises(HTTPException) as exc:
        await main.readiness_check()

    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_ready_returns_200_when_healthy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Readiness should pass when hydration is complete."""
    import apps.signal_service.main as main

    metadata = MagicMock(strategy_name="alpha_baseline", version="v1.0.0", activated_at=None)
    monkeypatch.setattr(main, "model_registry", MagicMock(is_loaded=True, current_metadata=metadata))
    monkeypatch.setattr(main, "signal_generator", MagicMock())
    redis_client = MagicMock()
    redis_client.health_check.return_value = True
    monkeypatch.setattr(main, "redis_client", redis_client)
    monkeypatch.setattr(main, "feature_cache", MagicMock())
    monkeypatch.setattr(main.settings, "redis_enabled", True)
    monkeypatch.setattr(main.settings, "feature_hydration_enabled", True)
    monkeypatch.setattr(main, "hydration_complete", True)

    response = await main.readiness_check()
    assert response.status == "healthy"
