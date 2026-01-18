"""
Unit tests for RedisKeys.

Tests cover:
- Key format generation for each RedisKeys helper
- Consistent prefixing and delimiter placement
"""

import pytest

from libs.core.redis_client.keys import RedisKeys


class TestRedisKeysPrice:
    """Tests for RedisKeys.price."""

    @pytest.mark.parametrize(
        ("symbol", "expected"),
        [
            ("AAPL", "price:AAPL"),
            ("msft", "price:msft"),
        ],
    )
    def test_price_key_format(self, symbol: str, expected: str) -> None:
        """Price key uses price:{symbol} format."""
        assert RedisKeys.price(symbol) == expected


class TestRedisKeysFeature:
    """Tests for RedisKeys.feature."""

    @pytest.mark.parametrize(
        ("symbol", "date", "expected"),
        [
            ("AAPL", "2025-01-17", "feature:AAPL:2025-01-17"),
            ("MSFT", "2025-12-31", "feature:MSFT:2025-12-31"),
        ],
    )
    def test_feature_key_format(self, symbol: str, date: str, expected: str) -> None:
        """Feature key uses feature:{symbol}:{date} format."""
        assert RedisKeys.feature(symbol, date) == expected


class TestRedisKeysCircuitBreaker:
    """Tests for RedisKeys.circuit_breaker."""

    @pytest.mark.parametrize(
        ("breaker_id", "expected"),
        [
            ("global", "cb:global"),
            ("AAPL", "cb:AAPL"),
        ],
    )
    def test_circuit_breaker_key_format(self, breaker_id: str, expected: str) -> None:
        """Circuit breaker key uses cb:{breaker_id} format."""
        assert RedisKeys.circuit_breaker(breaker_id) == expected


class TestRedisKeysModelVersion:
    """Tests for RedisKeys.model_version."""

    @pytest.mark.parametrize(
        ("strategy_name", "expected"),
        [
            ("alpha_baseline", "model:version:alpha_baseline"),
            ("intraday_v2", "model:version:intraday_v2"),
        ],
    )
    def test_model_version_key_format(self, strategy_name: str, expected: str) -> None:
        """Model version key uses model:version:{strategy_name} format."""
        assert RedisKeys.model_version(strategy_name) == expected


class TestRedisKeysQuarantine:
    """Tests for RedisKeys.quarantine."""

    @pytest.mark.parametrize(
        ("strategy_id", "symbol", "expected"),
        [
            ("strategy1", "AAPL", "quarantine:strategy1:AAPL"),
            ("alpha-1", "MSFT", "quarantine:alpha-1:MSFT"),
        ],
    )
    def test_quarantine_key_format(self, strategy_id: str, symbol: str, expected: str) -> None:
        """Quarantine key uses quarantine:{strategy_id}:{symbol} format."""
        assert RedisKeys.quarantine(strategy_id, symbol) == expected


class TestRedisKeysOrphanExposure:
    """Tests for RedisKeys.orphan_exposure."""

    @pytest.mark.parametrize(
        ("strategy_id", "symbol", "expected"),
        [
            ("strategy1", "AAPL", "orphan_exposure:strategy1:AAPL"),
            ("alpha-1", "MSFT", "orphan_exposure:alpha-1:MSFT"),
        ],
    )
    def test_orphan_exposure_key_format(self, strategy_id: str, symbol: str, expected: str) -> None:
        """Orphan exposure key uses orphan_exposure:{strategy_id}:{symbol} format."""
        assert RedisKeys.orphan_exposure(strategy_id, symbol) == expected


class TestRedisKeysReduceOnlyLock:
    """Tests for RedisKeys.reduce_only_lock."""

    @pytest.mark.parametrize(
        ("symbol", "expected"),
        [
            ("AAPL", "reduce_only_lock:AAPL"),
            ("MSFT", "reduce_only_lock:MSFT"),
        ],
    )
    def test_reduce_only_lock_key_format(self, symbol: str, expected: str) -> None:
        """Reduce-only lock key uses reduce_only_lock:{symbol} format."""
        assert RedisKeys.reduce_only_lock(symbol) == expected
