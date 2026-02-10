"""Tests for data health monitoring (P6T12.4)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from libs.data.data_pipeline.health_monitor import (
    DataSourceHealth,
    HealthMonitor,
    HealthStatus,
    HealthThresholds,
    format_age,
)


# ---------------------------------------------------------------------------
# HealthStatus determination
# ---------------------------------------------------------------------------
class TestHealthStatusDetermination:
    @pytest.mark.asyncio()
    async def test_ok_when_fresh(self) -> None:
        """Source within threshold should be OK."""
        monitor = HealthMonitor(HealthThresholds(price_stale_seconds=900))
        now = datetime.now(UTC)

        async def check() -> datetime | None:
            return now - timedelta(seconds=300)

        monitor.register_source("Price", "price", check)
        results = await monitor.check_all()
        assert len(results) == 1
        assert results[0].status == HealthStatus.OK
        assert results[0].age_seconds is not None
        assert results[0].age_seconds < 900

    @pytest.mark.asyncio()
    async def test_stale_when_old(self) -> None:
        """Source beyond threshold should be STALE."""
        monitor = HealthMonitor(HealthThresholds(price_stale_seconds=60))
        now = datetime.now(UTC)

        async def check() -> datetime | None:
            return now - timedelta(seconds=120)

        monitor.register_source("Price", "price", check)
        results = await monitor.check_all()
        assert results[0].status == HealthStatus.STALE

    @pytest.mark.asyncio()
    async def test_error_when_none(self) -> None:
        """No heartbeat recorded should be ERROR."""
        monitor = HealthMonitor()

        async def check() -> datetime | None:
            return None

        monitor.register_source("Fundamental", "fundamental", check)
        results = await monitor.check_all()
        assert results[0].status == HealthStatus.ERROR
        assert "No heartbeat" in results[0].message

    @pytest.mark.asyncio()
    async def test_error_when_check_raises(self) -> None:
        """Exception in check function should result in ERROR."""
        monitor = HealthMonitor()

        async def check() -> datetime | None:
            raise ConnectionError("Redis down")

        monitor.register_source("Price", "price", check)
        results = await monitor.check_all()
        assert results[0].status == HealthStatus.ERROR
        assert "Check failed" in results[0].message
        assert "Redis down" in results[0].message


# ---------------------------------------------------------------------------
# Threshold configuration
# ---------------------------------------------------------------------------
class TestThresholdConfiguration:
    def test_default_thresholds(self) -> None:
        t = HealthThresholds()
        assert t.price_stale_seconds == 900
        assert t.volume_stale_seconds == 900
        assert t.signal_stale_seconds == 600
        assert t.fundamental_stale_seconds == 86400

    def test_custom_thresholds(self) -> None:
        t = HealthThresholds(price_stale_seconds=300, signal_stale_seconds=120)
        assert t.price_stale_seconds == 300
        assert t.signal_stale_seconds == 120

    def test_get_threshold_by_category(self) -> None:
        t = HealthThresholds(price_stale_seconds=100, signal_stale_seconds=200)
        assert t.get_threshold("price") == 100
        assert t.get_threshold("signal") == 200
        assert t.get_threshold("unknown") == 100  # fallback to price


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------
class TestCaching:
    @pytest.mark.asyncio()
    async def test_cached_result_reused(self) -> None:
        """Second call within TTL should not invoke check_fn again."""
        monitor = HealthMonitor()
        call_count = 0

        async def check() -> datetime | None:
            nonlocal call_count
            call_count += 1
            return datetime.now(UTC)

        monitor.register_source("Price", "price", check)

        await monitor.check_all()
        assert call_count == 1

        await monitor.check_all()
        assert call_count == 1  # cached, not called again

    @pytest.mark.asyncio()
    async def test_independent_source_isolation(self) -> None:
        """One source failing should not affect others."""
        monitor = HealthMonitor()

        async def ok_check() -> datetime | None:
            return datetime.now(UTC)

        async def fail_check() -> datetime | None:
            raise RuntimeError("fail")

        monitor.register_source("Good", "price", ok_check)
        monitor.register_source("Bad", "volume", fail_check)

        results = await monitor.check_all()
        assert len(results) == 2
        assert results[0].status == HealthStatus.OK
        assert results[1].status == HealthStatus.ERROR


# ---------------------------------------------------------------------------
# format_age
# ---------------------------------------------------------------------------
class TestFormatAge:
    def test_seconds(self) -> None:
        assert format_age(5) == "5s ago"
        assert format_age(0) == "0s ago"

    def test_minutes(self) -> None:
        assert format_age(90) == "1m 30s ago"
        assert format_age(332) == "5m 32s ago"

    def test_hours(self) -> None:
        assert format_age(3600 + 900) == "1h 15m ago"

    def test_days(self) -> None:
        assert format_age(86400 * 3 + 3600 * 4) == "3d 4h ago"

    def test_none(self) -> None:
        assert format_age(None) == "unknown"

    def test_negative(self) -> None:
        assert format_age(-5) == "just now"


# ---------------------------------------------------------------------------
# DataSourceHealth
# ---------------------------------------------------------------------------
class TestDataSourceHealth:
    def test_fields(self) -> None:
        now = datetime.now(UTC)
        h = DataSourceHealth(
            name="Test",
            category="price",
            last_update=now,
            age_seconds=10.0,
            status=HealthStatus.OK,
            message="OK",
            last_checked=now,
        )
        assert h.name == "Test"
        assert h.status == HealthStatus.OK
        assert h.age_seconds == 10.0
