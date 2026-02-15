"""Service layer for shadow mode validation results (P6T14/T14.4)."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from typing import Any

from libs.platform.web_console_auth.permissions import Permission, has_permission

from .schemas.data_management import ShadowResultDTO, ShadowTrendDTO, ShadowTrendPointDTO

_MAX_RESULTS_LIMIT = 200
_MAX_TREND_DAYS = 365
_DEFAULT_STRATEGY = "alpha_baseline"


class ShadowResultsService:
    """Mock-backed service for shadow validation visibility."""

    def __init__(self, *, _rng_seed: int | None = None) -> None:
        self._rng_seed = _rng_seed

    def _rng(self, salt: int = 0) -> random.Random:
        if self._rng_seed is None:
            return random.Random()
        return random.Random(self._rng_seed + salt)

    @staticmethod
    def _check_permission(user: Any) -> None:
        if not has_permission(user, Permission.VIEW_SHADOW_RESULTS):
            raise PermissionError("Permission 'view_shadow_results' required")

    def _build_result(self, *, rng: random.Random, idx: int, strategy: str, now: datetime) -> ShadowResultDTO:
        correlation = round(rng.uniform(0.40, 0.95), 4)
        divergence = round(rng.uniform(0.10, 0.60), 4)
        sign_change = round(rng.uniform(0.05, 0.25), 4)
        passed = rng.random() < 0.8
        validation_time = now - timedelta(
            days=idx % 30,
            hours=(idx * 3) % 24,
            minutes=(idx * 11) % 60,
        )
        message = (
            "Validation passed: within correlation and divergence thresholds"
            if passed
            else "Validation failed: threshold breach detected"
        )
        old_range = round(rng.uniform(0.10, 1.75), 4)
        new_range = round(rng.uniform(0.10, 1.75), 4)
        return ShadowResultDTO(
            id=f"shadow-{idx + 1}",
            model_version=f"v{(idx % 7) + 1}.{(idx % 4) + 1}",
            strategy=strategy,
            validation_time=validation_time,
            passed=passed,
            correlation=correlation,
            mean_abs_diff_ratio=divergence,
            sign_change_rate=sign_change,
            sample_count=500 + ((idx * 37) % 4500),
            old_range=old_range,
            new_range=new_range,
            message=message,
            correlation_threshold=0.5,
            divergence_threshold=0.5,
        )

    async def get_recent_results(
        self,
        user: Any,
        strategy: str | None = None,
        limit: int = 50,
    ) -> list[ShadowResultDTO]:
        """Return recent shadow validation runs as mock data."""
        self._check_permission(user)
        clamped_limit = max(1, min(limit, _MAX_RESULTS_LIMIT))
        selected_strategy = strategy or _DEFAULT_STRATEGY
        now = datetime.now(UTC)
        rng = self._rng(101)
        return [
            self._build_result(rng=rng, idx=idx, strategy=selected_strategy, now=now)
            for idx in range(clamped_limit)
        ]

    def _generate_trend_points(
        self,
        *,
        rng: random.Random,
        strategy: str,
        days: int,
        now: datetime,
    ) -> list[ShadowTrendPointDTO]:
        points: list[ShadowTrendPointDTO] = []
        for day_offset in range(days):
            if rng.random() < 0.10:
                continue
            day = now - timedelta(days=(days - day_offset - 1))
            correlation = round(rng.uniform(0.40, 0.95), 4)
            divergence = round(rng.uniform(0.10, 0.60), 4)
            sign_change = round(rng.uniform(0.05, 0.25), 4)
            passed = correlation >= 0.5 and divergence <= 0.5
            points.append(
                ShadowTrendPointDTO(
                    date=day,
                    correlation=correlation,
                    mean_abs_diff_ratio=divergence,
                    sign_change_rate=sign_change,
                    passed=passed,
                )
            )
        return points

    async def get_trend(
        self,
        user: Any,
        strategy: str | None = None,
        days: int = 30,
    ) -> ShadowTrendDTO:
        """Return trend payload for charting."""
        self._check_permission(user)
        clamped_days = max(1, min(days, _MAX_TREND_DAYS))
        selected_strategy = strategy or _DEFAULT_STRATEGY
        now = datetime.now(UTC)
        rng = self._rng(303)

        points = self._generate_trend_points(
            rng=rng,
            strategy=selected_strategy,
            days=clamped_days,
            now=now,
        )

        if points:
            pass_rate = sum(1 for point in points if point.passed) / len(points) * 100.0
            avg_correlation = sum(point.correlation for point in points) / len(points)
            avg_divergence = sum(point.mean_abs_diff_ratio for point in points) / len(points)
        else:
            pass_rate = 0.0
            avg_correlation = None
            avg_divergence = None

        return ShadowTrendDTO(
            strategy=selected_strategy,
            period_days=clamped_days,
            data_points=points,
            total_validations=len(points),
            pass_rate=round(pass_rate, 2),
            avg_correlation=(round(avg_correlation, 4) if avg_correlation is not None else None),
            avg_divergence=(round(avg_divergence, 4) if avg_divergence is not None else None),
        )


__all__ = [
    "_MAX_RESULTS_LIMIT",
    "_MAX_TREND_DAYS",
    "ShadowResultsService",
]
