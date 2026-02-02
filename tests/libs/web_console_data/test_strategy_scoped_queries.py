"""Comprehensive unit tests for strategy-scoped data access with server-side filtering and caching.

Test coverage targets 85%+ branch coverage for libs/web_console_data/strategy_scoped_queries.py.

Coverage areas:
- Cache encryption/decryption with AES-256-GCM
- Strategy filtering and authorization
- Query building with filters
- Database operations (positions, orders, trades, PnL)
- Error handling (cache failures, DB errors, permission errors)
- Edge cases (None values, empty results, invalid inputs)
"""

from __future__ import annotations

import base64
import os
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest

from libs.web_console_data.strategy_scoped_queries import (
    STRATEGY_CACHE_KEY_ENV,
    StrategyScopedDataAccess,
    _build_cache_client,
    _date_to_utc_datetime,
    _get_cache_encryption_key,
    get_scoped_data_access,
)

# ============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture()
def mock_user() -> dict[str, Any]:
    """Standard user fixture with strategy access."""
    return {
        "user_id": "user-123",
        "sub": "auth0|user-123",
        "session_version": 1,
        "roles": ["trader"],
    }


@pytest.fixture()
def mock_db_pool() -> AsyncMock:
    """Mock database connection pool."""
    return AsyncMock()


@pytest.fixture()
def mock_redis_client() -> AsyncMock:
    """Mock Redis client for caching."""
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.setex = AsyncMock()
    return redis


@pytest.fixture()
def sample_strategies() -> list[str]:
    """Sample authorized strategies."""
    return ["strategy-alpha", "strategy-beta"]


# =============================================================================
# Helper Function Tests
# =============================================================================


class TestGetCacheEncryptionKey:
    """Tests for _get_cache_encryption_key function."""

    def test_get_cache_encryption_key_success(self) -> None:
        """Valid 32-byte base64 key should decode successfully."""
        valid_key = base64.b64encode(b"a" * 32).decode()
        with patch.dict(os.environ, {STRATEGY_CACHE_KEY_ENV: valid_key}):
            result = _get_cache_encryption_key()
            assert result is not None
            assert len(result) == 32

    def test_get_cache_encryption_key_not_configured(self) -> None:
        """Missing key should return None."""
        with patch.dict(os.environ, {}, clear=True):
            result = _get_cache_encryption_key()
            assert result is None

    def test_get_cache_encryption_key_invalid_length(self) -> None:
        """Key with wrong length should return None and log warning."""
        invalid_key = base64.b64encode(b"short").decode()
        with patch.dict(os.environ, {STRATEGY_CACHE_KEY_ENV: invalid_key}):
            result = _get_cache_encryption_key()
            assert result is None

    def test_get_cache_encryption_key_invalid_base64(self) -> None:
        """Invalid base64 should return None and log warning."""
        with patch.dict(os.environ, {STRATEGY_CACHE_KEY_ENV: "not-base64"}):
            result = _get_cache_encryption_key()
            assert result is None


class TestBuildCacheClient:
    """Tests for _build_cache_client function."""

    def test_build_cache_client_none_input(self) -> None:
        """None redis client should return None."""
        result = _build_cache_client(None)
        assert result is None

    def test_build_cache_client_no_connection_pool(self) -> None:
        """Client without connection_pool (e.g., test fake) should be returned as-is."""
        mock_client = Mock(spec=[])  # No connection_pool attribute
        result = _build_cache_client(mock_client)
        assert result == mock_client


class TestDateToUtcDatetime:
    """Tests for _date_to_utc_datetime helper."""

    def test_date_to_utc_datetime_conversion(self) -> None:
        """Date should convert to UTC-aware datetime at midnight."""
        test_date = date(2025, 1, 15)
        result = _date_to_utc_datetime(test_date)

        assert isinstance(result, datetime)
        assert result.tzinfo == UTC
        assert result.year == 2025
        assert result.month == 1
        assert result.day == 15
        assert result.hour == 0
        assert result.minute == 0
        assert result.second == 0


# =============================================================================
# StrategyScopedDataAccess Class Tests
# =============================================================================


class TestStrategyScopedDataAccessInit:
    """Tests for StrategyScopedDataAccess initialization."""

    @patch("libs.web_console_data.strategy_scoped_queries.get_authorized_strategies")
    @patch("libs.web_console_data.strategy_scoped_queries._get_cache_encryption_key")
    @patch("libs.web_console_data.strategy_scoped_queries._build_cache_client")
    def test_init_success(
        self,
        mock_build_cache: Mock,
        mock_get_key: Mock,
        mock_get_strategies: Mock,
        mock_db_pool: AsyncMock,
        mock_redis_client: AsyncMock,
        mock_user: dict[str, Any],
        sample_strategies: list[str],
    ) -> None:
        """Successful initialization with all components."""
        mock_get_strategies.return_value = sample_strategies
        mock_get_key.return_value = b"a" * 32
        mock_build_cache.return_value = mock_redis_client

        access = StrategyScopedDataAccess(mock_db_pool, mock_redis_client, mock_user)

        assert access.db_pool == mock_db_pool
        assert access.user == mock_user
        assert access.user_id == "user-123"
        assert access.authorized_strategies == sample_strategies
        assert access._cipher is not None

    @patch("libs.web_console_data.strategy_scoped_queries.get_authorized_strategies")
    @patch("libs.web_console_data.strategy_scoped_queries._get_cache_encryption_key")
    def test_init_no_encryption_key(
        self,
        mock_get_key: Mock,
        mock_get_strategies: Mock,
        mock_db_pool: AsyncMock,
        mock_redis_client: AsyncMock,
        mock_user: dict[str, Any],
        sample_strategies: list[str],
    ) -> None:
        """Initialization without encryption key should disable cipher."""
        mock_get_strategies.return_value = sample_strategies
        mock_get_key.return_value = None

        access = StrategyScopedDataAccess(mock_db_pool, mock_redis_client, mock_user)

        assert access._cipher is None


class TestEncryptionMethods:
    """Tests for cache encryption/decryption methods."""

    @patch("libs.web_console_data.strategy_scoped_queries.get_authorized_strategies")
    @patch("libs.web_console_data.strategy_scoped_queries._get_cache_encryption_key")
    def test_encrypt_decrypt_roundtrip(
        self,
        mock_get_key: Mock,
        mock_get_strategies: Mock,
        mock_db_pool: AsyncMock,
        mock_redis_client: AsyncMock,
        mock_user: dict[str, Any],
        sample_strategies: list[str],
    ) -> None:
        """Encryption and decryption should recover original data."""
        mock_get_strategies.return_value = sample_strategies
        mock_get_key.return_value = b"a" * 32

        access = StrategyScopedDataAccess(mock_db_pool, mock_redis_client, mock_user)

        original = "test data"
        encrypted = access._encrypt_cache_data(original)
        decrypted = access._decrypt_cache_data(encrypted)

        assert decrypted == original
        assert encrypted != original  # Verify it was actually encrypted

    @patch("libs.web_console_data.strategy_scoped_queries.get_authorized_strategies")
    @patch("libs.web_console_data.strategy_scoped_queries._get_cache_encryption_key")
    def test_encrypt_cache_data_no_cipher(
        self,
        mock_get_key: Mock,
        mock_get_strategies: Mock,
        mock_db_pool: AsyncMock,
        mock_redis_client: AsyncMock,
        mock_user: dict[str, Any],
        sample_strategies: list[str],
    ) -> None:
        """Without cipher, should return data unchanged."""
        mock_get_strategies.return_value = sample_strategies
        mock_get_key.return_value = None

        access = StrategyScopedDataAccess(mock_db_pool, mock_redis_client, mock_user)

        result = access._encrypt_cache_data("test data")
        assert result == "test data"

    @patch("libs.web_console_data.strategy_scoped_queries.get_authorized_strategies")
    @patch("libs.web_console_data.strategy_scoped_queries._get_cache_encryption_key")
    def test_decrypt_cache_data_invalid_base64(
        self,
        mock_get_key: Mock,
        mock_get_strategies: Mock,
        mock_db_pool: AsyncMock,
        mock_redis_client: AsyncMock,
        mock_user: dict[str, Any],
        sample_strategies: list[str],
    ) -> None:
        """Invalid base64 should raise ValueError."""
        mock_get_strategies.return_value = sample_strategies
        mock_get_key.return_value = b"a" * 32

        access = StrategyScopedDataAccess(mock_db_pool, mock_redis_client, mock_user)

        with pytest.raises(ValueError, match="(Invalid|Decryption|base64|padding|Incorrect)"):
            access._decrypt_cache_data("not-valid-base64")


class TestFilterMethods:
    """Tests for filter building and cache token generation."""

    def test_build_filter_clauses_empty(self) -> None:
        """Empty filters should return empty clauses."""
        clauses, params = StrategyScopedDataAccess._build_filter_clauses({}, {"symbol": "symbol"})

        assert clauses == []
        assert params == []

    def test_build_filter_clauses_single_value(self) -> None:
        """Single value filter should use equality."""
        clauses, params = StrategyScopedDataAccess._build_filter_clauses(
            {"symbol": "AAPL"}, {"symbol": "symbol"}
        )

        assert clauses == ["symbol = %s"]
        assert params == ["AAPL"]

    def test_build_filter_clauses_list_value(self) -> None:
        """List value should use ANY()."""
        clauses, params = StrategyScopedDataAccess._build_filter_clauses(
            {"symbol": ["AAPL", "GOOGL"]}, {"symbol": "symbol"}
        )

        assert clauses == ["symbol = ANY(%s)"]
        assert params == [["AAPL", "GOOGL"]]

    def test_build_filter_clauses_none_value(self) -> None:
        """None values should be ignored."""
        clauses, params = StrategyScopedDataAccess._build_filter_clauses(
            {"symbol": None}, {"symbol": "symbol"}
        )

        assert clauses == []
        assert params == []

    def test_filters_cache_token_deterministic(self) -> None:
        """Same filters should produce same token."""
        filters = {"symbol": "AAPL", "side": "buy"}
        allowed = {"symbol": "symbol", "side": "side"}

        token1 = StrategyScopedDataAccess._filters_cache_token(filters, allowed)
        token2 = StrategyScopedDataAccess._filters_cache_token(filters, allowed)

        assert token1 == token2
        assert token1.startswith(":")


class TestStrategyFilterAndPermissions:
    """Tests for strategy authorization and filtering."""

    @patch("libs.web_console_data.strategy_scoped_queries.get_authorized_strategies")
    @patch("libs.web_console_data.strategy_scoped_queries._get_cache_encryption_key")
    def test_get_strategy_filter_success(
        self,
        mock_get_key: Mock,
        mock_get_strategies: Mock,
        mock_db_pool: AsyncMock,
        mock_redis_client: AsyncMock,
        mock_user: dict[str, Any],
        sample_strategies: list[str],
    ) -> None:
        """Should return authorized strategies."""
        mock_get_strategies.return_value = sample_strategies
        mock_get_key.return_value = None

        access = StrategyScopedDataAccess(mock_db_pool, mock_redis_client, mock_user)
        result = access._get_strategy_filter()

        assert result == sample_strategies

    @patch("libs.web_console_data.strategy_scoped_queries.get_authorized_strategies")
    @patch("libs.web_console_data.strategy_scoped_queries._get_cache_encryption_key")
    def test_get_strategy_filter_no_access(
        self,
        mock_get_key: Mock,
        mock_get_strategies: Mock,
        mock_db_pool: AsyncMock,
        mock_redis_client: AsyncMock,
        mock_user: dict[str, Any],
    ) -> None:
        """Empty strategy list should raise PermissionError."""
        mock_get_strategies.return_value = []
        mock_get_key.return_value = None

        access = StrategyScopedDataAccess(mock_db_pool, mock_redis_client, mock_user)

        with pytest.raises(PermissionError, match="No strategy access"):
            access._get_strategy_filter()


class TestCacheMethods:
    """Tests for cache get/set operations."""

    @pytest.mark.asyncio()
    @patch("libs.web_console_data.strategy_scoped_queries.get_authorized_strategies")
    @patch("libs.web_console_data.strategy_scoped_queries._get_cache_encryption_key")
    async def test_get_cached_no_redis(
        self,
        mock_get_key: Mock,
        mock_get_strategies: Mock,
        mock_db_pool: AsyncMock,
        mock_user: dict[str, Any],
        sample_strategies: list[str],
    ) -> None:
        """Without Redis, should return None."""
        mock_get_strategies.return_value = sample_strategies
        mock_get_key.return_value = b"a" * 32

        access = StrategyScopedDataAccess(mock_db_pool, None, mock_user)

        result = await access._get_cached("test:key")
        assert result is None

    @pytest.mark.asyncio()
    @patch("libs.web_console_data.strategy_scoped_queries.get_authorized_strategies")
    @patch("libs.web_console_data.strategy_scoped_queries._get_cache_encryption_key")
    async def test_get_cached_no_cipher(
        self,
        mock_get_key: Mock,
        mock_get_strategies: Mock,
        mock_db_pool: AsyncMock,
        mock_redis_client: AsyncMock,
        mock_user: dict[str, Any],
        sample_strategies: list[str],
    ) -> None:
        """Without cipher, should return None (security requirement)."""
        mock_get_strategies.return_value = sample_strategies
        mock_get_key.return_value = None  # No encryption key

        access = StrategyScopedDataAccess(mock_db_pool, mock_redis_client, mock_user)

        result = await access._get_cached("test:key")
        assert result is None

    @pytest.mark.asyncio()
    @patch("libs.web_console_data.strategy_scoped_queries.get_authorized_strategies")
    @patch("libs.web_console_data.strategy_scoped_queries._get_cache_encryption_key")
    async def test_set_cached_no_redis(
        self,
        mock_get_key: Mock,
        mock_get_strategies: Mock,
        mock_db_pool: AsyncMock,
        mock_user: dict[str, Any],
        sample_strategies: list[str],
    ) -> None:
        """Without Redis, should return silently."""
        mock_get_strategies.return_value = sample_strategies
        mock_get_key.return_value = b"a" * 32

        access = StrategyScopedDataAccess(mock_db_pool, None, mock_user)

        # Should not raise
        await access._set_cached("test:key", [{"data": "test"}])

    @pytest.mark.asyncio()
    @patch("libs.web_console_data.strategy_scoped_queries.get_authorized_strategies")
    @patch("libs.web_console_data.strategy_scoped_queries._get_cache_encryption_key")
    async def test_set_cached_no_cipher(
        self,
        mock_get_key: Mock,
        mock_get_strategies: Mock,
        mock_db_pool: AsyncMock,
        mock_redis_client: AsyncMock,
        mock_user: dict[str, Any],
        sample_strategies: list[str],
    ) -> None:
        """Without cipher, should return silently (security requirement)."""
        mock_get_strategies.return_value = sample_strategies
        mock_get_key.return_value = None  # No encryption key

        access = StrategyScopedDataAccess(mock_db_pool, mock_redis_client, mock_user)

        # Should not raise
        await access._set_cached("test:key", [{"data": "test"}])


class TestLimitMethod:
    """Tests for _limit method."""

    @patch("libs.web_console_data.strategy_scoped_queries.get_authorized_strategies")
    @patch("libs.web_console_data.strategy_scoped_queries._get_cache_encryption_key")
    def test_limit_none(
        self,
        mock_get_key: Mock,
        mock_get_strategies: Mock,
        mock_db_pool: AsyncMock,
        mock_redis_client: AsyncMock,
        mock_user: dict[str, Any],
        sample_strategies: list[str],
    ) -> None:
        """None should return default limit."""
        mock_get_strategies.return_value = sample_strategies
        mock_get_key.return_value = None

        access = StrategyScopedDataAccess(mock_db_pool, mock_redis_client, mock_user)

        assert access._limit(None) == StrategyScopedDataAccess.DEFAULT_LIMIT

    @patch("libs.web_console_data.strategy_scoped_queries.get_authorized_strategies")
    @patch("libs.web_console_data.strategy_scoped_queries._get_cache_encryption_key")
    def test_limit_exceeds_max(
        self,
        mock_get_key: Mock,
        mock_get_strategies: Mock,
        mock_db_pool: AsyncMock,
        mock_redis_client: AsyncMock,
        mock_user: dict[str, Any],
        sample_strategies: list[str],
    ) -> None:
        """Limit exceeding MAX_LIMIT should be clamped."""
        mock_get_strategies.return_value = sample_strategies
        mock_get_key.return_value = None

        access = StrategyScopedDataAccess(mock_db_pool, mock_redis_client, mock_user)

        assert access._limit(10000) == StrategyScopedDataAccess.MAX_LIMIT

    @patch("libs.web_console_data.strategy_scoped_queries.get_authorized_strategies")
    @patch("libs.web_console_data.strategy_scoped_queries._get_cache_encryption_key")
    def test_limit_zero(
        self,
        mock_get_key: Mock,
        mock_get_strategies: Mock,
        mock_db_pool: AsyncMock,
        mock_redis_client: AsyncMock,
        mock_user: dict[str, Any],
        sample_strategies: list[str],
    ) -> None:
        """Zero should be clamped to 1."""
        mock_get_strategies.return_value = sample_strategies
        mock_get_key.return_value = None

        access = StrategyScopedDataAccess(mock_db_pool, mock_redis_client, mock_user)

        assert access._limit(0) == 1


class TestAddDateFilters:
    """Tests for _add_date_filters static method."""

    def test_add_date_filters_both(self) -> None:
        """Both date_from and date_to should add clauses."""
        clauses: list[str] = []
        params: list[Any] = []

        StrategyScopedDataAccess._add_date_filters(
            clauses,
            params,
            date(2025, 1, 1),
            date(2025, 1, 31),
        )

        assert len(clauses) == 2
        assert "executed_at >= %s" in clauses
        assert "executed_at < %s" in clauses
        assert len(params) == 2

    def test_add_date_filters_none(self) -> None:
        """Both None should not add any clauses."""
        clauses: list[str] = []
        params: list[Any] = []

        StrategyScopedDataAccess._add_date_filters(clauses, params, None, None)

        assert len(clauses) == 0
        assert len(params) == 0


class TestToDecimalOrNone:
    """Tests for _to_decimal_or_none helper."""

    @patch("libs.web_console_data.strategy_scoped_queries.get_authorized_strategies")
    @patch("libs.web_console_data.strategy_scoped_queries._get_cache_encryption_key")
    def test_to_decimal_or_none_with_value(
        self,
        mock_get_key: Mock,
        mock_get_strategies: Mock,
        mock_db_pool: AsyncMock,
        mock_redis_client: AsyncMock,
        mock_user: dict[str, Any],
        sample_strategies: list[str],
    ) -> None:
        """Valid value should convert to Decimal."""
        mock_get_strategies.return_value = sample_strategies
        mock_get_key.return_value = None

        access = StrategyScopedDataAccess(mock_db_pool, mock_redis_client, mock_user)

        result = access._to_decimal_or_none(123.45)
        assert isinstance(result, Decimal)
        assert result == Decimal("123.45")

    @patch("libs.web_console_data.strategy_scoped_queries.get_authorized_strategies")
    @patch("libs.web_console_data.strategy_scoped_queries._get_cache_encryption_key")
    def test_to_decimal_or_none_with_none(
        self,
        mock_get_key: Mock,
        mock_get_strategies: Mock,
        mock_db_pool: AsyncMock,
        mock_redis_client: AsyncMock,
        mock_user: dict[str, Any],
        sample_strategies: list[str],
    ) -> None:
        """None should return None."""
        mock_get_strategies.return_value = sample_strategies
        mock_get_key.return_value = None

        access = StrategyScopedDataAccess(mock_db_pool, mock_redis_client, mock_user)

        result = access._to_decimal_or_none(None)
        assert result is None


class TestGetScopedDataAccessFactory:
    """Tests for get_scoped_data_access factory function."""

    @patch("libs.web_console_data.strategy_scoped_queries.get_authorized_strategies")
    @patch("libs.web_console_data.strategy_scoped_queries._get_cache_encryption_key")
    def test_get_scoped_data_access_creates_instance(
        self,
        mock_get_key: Mock,
        mock_get_strategies: Mock,
        mock_db_pool: AsyncMock,
        mock_redis_client: AsyncMock,
        mock_user: dict[str, Any],
        sample_strategies: list[str],
    ) -> None:
        """Factory should create StrategyScopedDataAccess instance."""
        mock_get_strategies.return_value = sample_strategies
        mock_get_key.return_value = None

        result = get_scoped_data_access(mock_db_pool, mock_redis_client, mock_user)

        assert isinstance(result, StrategyScopedDataAccess)
        assert result.db_pool == mock_db_pool
        assert result.user == mock_user


# =============================================================================
# Query Method Tests
# =============================================================================


class TestGetPositions:
    """Tests for get_positions() query method."""

    @pytest.mark.asyncio()
    @patch("libs.web_console_data.strategy_scoped_queries.acquire_connection")
    @patch("libs.web_console_data.strategy_scoped_queries.get_authorized_strategies")
    @patch("libs.web_console_data.strategy_scoped_queries._get_cache_encryption_key")
    async def test_get_positions_cache_hit(
        self,
        mock_get_key: Mock,
        mock_get_strategies: Mock,
        mock_acquire: AsyncMock,
        mock_db_pool: AsyncMock,
        mock_redis_client: AsyncMock,
        mock_user: dict[str, Any],
        sample_strategies: list[str],
    ) -> None:
        """Should return cached data when available."""
        mock_get_strategies.return_value = sample_strategies
        mock_get_key.return_value = b"a" * 32

        cached_data = [{"symbol": "AAPL", "quantity": "100"}]

        access = StrategyScopedDataAccess(mock_db_pool, mock_redis_client, mock_user)
        # Mock _get_cached to return cached data
        access._get_cached = AsyncMock(return_value=cached_data)

        result = await access.get_positions()

        assert result == cached_data
        mock_acquire.assert_not_called()  # Should not hit DB
        access._get_cached.assert_called_once()

    @pytest.mark.asyncio()
    @patch("libs.web_console_data.strategy_scoped_queries.acquire_connection")
    @patch("libs.web_console_data.strategy_scoped_queries.get_authorized_strategies")
    @patch("libs.web_console_data.strategy_scoped_queries._get_cache_encryption_key")
    async def test_get_positions_cache_miss(
        self,
        mock_get_key: Mock,
        mock_get_strategies: Mock,
        mock_acquire: AsyncMock,
        mock_db_pool: AsyncMock,
        mock_redis_client: AsyncMock,
        mock_user: dict[str, Any],
        sample_strategies: list[str],
    ) -> None:
        """Should query DB on cache miss."""
        mock_get_strategies.return_value = sample_strategies
        mock_get_key.return_value = b"a" * 32

        mock_conn = AsyncMock()
        mock_acquire.return_value = AsyncMock()
        mock_acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_acquire.return_value.__aexit__ = AsyncMock(return_value=None)

        db_rows = [{"symbol": "AAPL", "quantity": "100"}]

        access = StrategyScopedDataAccess(mock_db_pool, mock_redis_client, mock_user)
        access._execute_fetchall = AsyncMock(return_value=db_rows)
        access._get_cached = AsyncMock(return_value=None)  # Cache miss
        access._set_cached = AsyncMock()  # Mock cache write

        result = await access.get_positions(limit=50)

        assert result == db_rows
        access._execute_fetchall.assert_called_once()
        # Should cache the result
        access._set_cached.assert_called_once()

    @pytest.mark.asyncio()
    @patch("libs.web_console_data.strategy_scoped_queries.acquire_connection")
    @patch("libs.web_console_data.strategy_scoped_queries.get_authorized_strategies")
    @patch("libs.web_console_data.strategy_scoped_queries._get_cache_encryption_key")
    async def test_get_positions_with_symbol_filter(
        self,
        mock_get_key: Mock,
        mock_get_strategies: Mock,
        mock_acquire: AsyncMock,
        mock_db_pool: AsyncMock,
        mock_redis_client: AsyncMock,
        mock_user: dict[str, Any],
        sample_strategies: list[str],
    ) -> None:
        """Should apply symbol filter."""
        mock_get_strategies.return_value = sample_strategies
        mock_get_key.return_value = b"a" * 32

        mock_conn = AsyncMock()
        mock_acquire.return_value = AsyncMock()
        mock_acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_acquire.return_value.__aexit__ = AsyncMock(return_value=None)

        db_rows = [{"symbol": "AAPL", "quantity": "100"}]

        access = StrategyScopedDataAccess(mock_db_pool, mock_redis_client, mock_user)
        access._execute_fetchall = AsyncMock(return_value=db_rows)
        access._get_cached = AsyncMock(return_value=None)
        access._set_cached = AsyncMock()

        result = await access.get_positions(symbol="AAPL", use_cache=False)

        assert result == db_rows
        # Verify query includes symbol filter
        call_args = access._execute_fetchall.call_args
        query = call_args[0][1]
        assert "symbol = %s" in query or "symbol = ANY(%s)" in query


class TestGetOrders:
    """Tests for get_orders() query method."""

    @pytest.mark.asyncio()
    @patch("libs.web_console_data.strategy_scoped_queries.acquire_connection")
    @patch("libs.web_console_data.strategy_scoped_queries.get_authorized_strategies")
    @patch("libs.web_console_data.strategy_scoped_queries._get_cache_encryption_key")
    async def test_get_orders_no_filters(
        self,
        mock_get_key: Mock,
        mock_get_strategies: Mock,
        mock_acquire: AsyncMock,
        mock_db_pool: AsyncMock,
        mock_redis_client: AsyncMock,
        mock_user: dict[str, Any],
        sample_strategies: list[str],
    ) -> None:
        """Should return all orders for authorized strategies."""
        mock_get_strategies.return_value = sample_strategies
        mock_get_key.return_value = None

        mock_conn = AsyncMock()
        mock_acquire.return_value = AsyncMock()
        mock_acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_acquire.return_value.__aexit__ = AsyncMock()

        db_rows = [
            {"symbol": "AAPL", "side": "buy", "status": "filled"},
            {"symbol": "MSFT", "side": "sell", "status": "open"},
        ]

        access = StrategyScopedDataAccess(mock_db_pool, mock_redis_client, mock_user)
        access._execute_fetchall = AsyncMock(return_value=db_rows)

        result = await access.get_orders()

        assert result == db_rows
        access._execute_fetchall.assert_called_once()

    @pytest.mark.asyncio()
    @patch("libs.web_console_data.strategy_scoped_queries.acquire_connection")
    @patch("libs.web_console_data.strategy_scoped_queries.get_authorized_strategies")
    @patch("libs.web_console_data.strategy_scoped_queries._get_cache_encryption_key")
    async def test_get_orders_with_filters(
        self,
        mock_get_key: Mock,
        mock_get_strategies: Mock,
        mock_acquire: AsyncMock,
        mock_db_pool: AsyncMock,
        mock_redis_client: AsyncMock,
        mock_user: dict[str, Any],
        sample_strategies: list[str],
    ) -> None:
        """Should apply symbol, side, and status filters."""
        mock_get_strategies.return_value = sample_strategies
        mock_get_key.return_value = None

        mock_conn = AsyncMock()
        mock_acquire.return_value = AsyncMock()
        mock_acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_acquire.return_value.__aexit__ = AsyncMock()

        db_rows = [{"symbol": "AAPL", "side": "buy", "status": "filled"}]

        access = StrategyScopedDataAccess(mock_db_pool, mock_redis_client, mock_user)
        access._execute_fetchall = AsyncMock(return_value=db_rows)

        result = await access.get_orders(symbol="AAPL", side="buy", status="filled")

        assert result == db_rows
        call_args = access._execute_fetchall.call_args
        query = call_args[0][1]
        # Verify filters applied
        assert "symbol" in query
        assert "side" in query
        assert "status" in query


class TestGetPnlSummary:
    """Tests for get_pnl_summary() query method."""

    @pytest.mark.asyncio()
    @patch("libs.web_console_data.strategy_scoped_queries.acquire_connection")
    @patch("libs.web_console_data.strategy_scoped_queries.get_authorized_strategies")
    @patch("libs.web_console_data.strategy_scoped_queries._get_cache_encryption_key")
    async def test_get_pnl_summary_with_date_range(
        self,
        mock_get_key: Mock,
        mock_get_strategies: Mock,
        mock_acquire: AsyncMock,
        mock_db_pool: AsyncMock,
        mock_redis_client: AsyncMock,
        mock_user: dict[str, Any],
        sample_strategies: list[str],
    ) -> None:
        """Should query PnL for date range."""
        mock_get_strategies.return_value = sample_strategies
        mock_get_key.return_value = None

        mock_conn = AsyncMock()
        mock_acquire.return_value = AsyncMock()
        mock_acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_acquire.return_value.__aexit__ = AsyncMock()

        db_rows = [
            {"trade_date": "2025-01-15", "realized_pnl": "1500.00"},
            {"trade_date": "2025-01-14", "realized_pnl": "800.00"},
        ]

        access = StrategyScopedDataAccess(mock_db_pool, mock_redis_client, mock_user)
        access._execute_fetchall = AsyncMock(return_value=db_rows)

        result = await access.get_pnl_summary(
            date_from=date(2025, 1, 14), date_to=date(2025, 1, 15), limit=100
        )

        assert result == db_rows
        access._execute_fetchall.assert_called_once()
        # Verify date parameters passed
        call_args = access._execute_fetchall.call_args
        params = call_args[0][2]
        assert params[1] == date(2025, 1, 14)
        assert params[2] == date(2025, 1, 15)


class TestGetTrades:
    """Tests for get_trades() query method."""

    @pytest.mark.asyncio()
    @patch("libs.web_console_data.strategy_scoped_queries.acquire_connection")
    @patch("libs.web_console_data.strategy_scoped_queries.get_authorized_strategies")
    @patch("libs.web_console_data.strategy_scoped_queries._get_cache_encryption_key")
    async def test_get_trades_no_filters(
        self,
        mock_get_key: Mock,
        mock_get_strategies: Mock,
        mock_acquire: AsyncMock,
        mock_db_pool: AsyncMock,
        mock_redis_client: AsyncMock,
        mock_user: dict[str, Any],
        sample_strategies: list[str],
    ) -> None:
        """Should return all non-superseded trades."""
        mock_get_strategies.return_value = sample_strategies
        mock_get_key.return_value = None

        mock_conn = AsyncMock()
        mock_acquire.return_value = AsyncMock()
        mock_acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_acquire.return_value.__aexit__ = AsyncMock()

        db_rows = [
            {"symbol": "AAPL", "side": "buy", "executed_at": "2025-01-15T10:00:00Z"},
            {"symbol": "MSFT", "side": "sell", "executed_at": "2025-01-15T11:00:00Z"},
        ]

        access = StrategyScopedDataAccess(mock_db_pool, mock_redis_client, mock_user)
        access._execute_fetchall = AsyncMock(return_value=db_rows)

        result = await access.get_trades()

        assert result == db_rows
        access._execute_fetchall.assert_called_once()
        # Verify superseded filter in query
        call_args = access._execute_fetchall.call_args
        query = call_args[0][1]
        assert "COALESCE(superseded, FALSE) = FALSE" in query

    @pytest.mark.asyncio()
    @patch("libs.web_console_data.strategy_scoped_queries.acquire_connection")
    @patch("libs.web_console_data.strategy_scoped_queries.get_authorized_strategies")
    @patch("libs.web_console_data.strategy_scoped_queries._get_cache_encryption_key")
    async def test_get_trades_with_date_filters(
        self,
        mock_get_key: Mock,
        mock_get_strategies: Mock,
        mock_acquire: AsyncMock,
        mock_db_pool: AsyncMock,
        mock_redis_client: AsyncMock,
        mock_user: dict[str, Any],
        sample_strategies: list[str],
    ) -> None:
        """Should apply date range filters."""
        mock_get_strategies.return_value = sample_strategies
        mock_get_key.return_value = None

        mock_conn = AsyncMock()
        mock_acquire.return_value = AsyncMock()
        mock_acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_acquire.return_value.__aexit__ = AsyncMock()

        db_rows = [{"symbol": "AAPL", "executed_at": "2025-01-15T10:00:00Z"}]

        access = StrategyScopedDataAccess(mock_db_pool, mock_redis_client, mock_user)
        access._execute_fetchall = AsyncMock(return_value=db_rows)

        result = await access.get_trades(date_from=date(2025, 1, 15), date_to=date(2025, 1, 16))

        assert result == db_rows
        call_args = access._execute_fetchall.call_args
        query = call_args[0][1]
        # Verify date filters in query
        assert "executed_at >=" in query
        assert "executed_at <" in query

    @pytest.mark.asyncio()
    @patch("libs.web_console_data.strategy_scoped_queries.acquire_connection")
    @patch("libs.web_console_data.strategy_scoped_queries.get_authorized_strategies")
    @patch("libs.web_console_data.strategy_scoped_queries._get_cache_encryption_key")
    async def test_get_trades_with_symbol_and_side_filters(
        self,
        mock_get_key: Mock,
        mock_get_strategies: Mock,
        mock_acquire: AsyncMock,
        mock_db_pool: AsyncMock,
        mock_redis_client: AsyncMock,
        mock_user: dict[str, Any],
        sample_strategies: list[str],
    ) -> None:
        """Should apply symbol and side filters."""
        mock_get_strategies.return_value = sample_strategies
        mock_get_key.return_value = None

        mock_conn = AsyncMock()
        mock_acquire.return_value = AsyncMock()
        mock_acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_acquire.return_value.__aexit__ = AsyncMock()

        db_rows = [{"symbol": "AAPL", "side": "buy", "executed_at": "2025-01-15T10:00:00Z"}]

        access = StrategyScopedDataAccess(mock_db_pool, mock_redis_client, mock_user)
        access._execute_fetchall = AsyncMock(return_value=db_rows)

        result = await access.get_trades(symbol="AAPL", side="buy")

        assert result == db_rows
        call_args = access._execute_fetchall.call_args
        query = call_args[0][1]
        # Verify filters applied
        assert "symbol" in query
        assert "side" in query


class TestGetTradeStats:
    """Tests for get_trade_stats() query method."""

    @pytest.mark.asyncio()
    @patch("libs.web_console_data.strategy_scoped_queries.acquire_connection")
    @patch("libs.web_console_data.strategy_scoped_queries.get_authorized_strategies")
    @patch("libs.web_console_data.strategy_scoped_queries._get_cache_encryption_key")
    async def test_get_trade_stats_success(
        self,
        mock_get_key: Mock,
        mock_get_strategies: Mock,
        mock_acquire: AsyncMock,
        mock_db_pool: AsyncMock,
        mock_redis_client: AsyncMock,
        mock_user: dict[str, Any],
        sample_strategies: list[str],
    ) -> None:
        """Should return trade statistics."""
        mock_get_strategies.return_value = sample_strategies
        mock_get_key.return_value = None

        mock_conn = AsyncMock()
        mock_acquire.return_value = AsyncMock()
        mock_acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_acquire.return_value.__aexit__ = AsyncMock(return_value=None)

        db_rows = [
            {
                "total_trades": 100,
                "winning_trades": 55,
                "losing_trades": 40,
                "break_even_trades": 5,
                "total_realized_pnl": Decimal("15000.50"),
                "gross_profit": Decimal("25000.00"),
                "gross_loss": Decimal("9999.50"),
                "avg_win": Decimal("454.54"),
                "avg_loss": Decimal("-249.99"),
                "largest_win": Decimal("1500.00"),
                "largest_loss": Decimal("-800.00"),
            }
        ]

        access = StrategyScopedDataAccess(mock_db_pool, mock_redis_client, mock_user)
        access._execute_fetchall = AsyncMock(return_value=db_rows)

        result = await access.get_trade_stats()

        assert result["total_trades"] == 100
        assert result["winning_trades"] == 55
        assert result["losing_trades"] == 40
        assert result["total_realized_pnl"] == Decimal("15000.50")
        access._execute_fetchall.assert_called_once()

    @pytest.mark.asyncio()
    @patch("libs.web_console_data.strategy_scoped_queries.acquire_connection")
    @patch("libs.web_console_data.strategy_scoped_queries.get_authorized_strategies")
    @patch("libs.web_console_data.strategy_scoped_queries._get_cache_encryption_key")
    async def test_get_trade_stats_with_filters(
        self,
        mock_get_key: Mock,
        mock_get_strategies: Mock,
        mock_acquire: AsyncMock,
        mock_db_pool: AsyncMock,
        mock_redis_client: AsyncMock,
        mock_user: dict[str, Any],
        sample_strategies: list[str],
    ) -> None:
        """Should apply symbol and date filters."""
        mock_get_strategies.return_value = sample_strategies
        mock_get_key.return_value = None

        mock_conn = AsyncMock()
        mock_acquire.return_value = AsyncMock()
        mock_acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_acquire.return_value.__aexit__ = AsyncMock(return_value=None)

        db_rows = [
            {
                "total_trades": 10,
                "winning_trades": 6,
                "losing_trades": 4,
                "break_even_trades": 0,
                "total_realized_pnl": Decimal("500.00"),
                "gross_profit": Decimal("800.00"),
                "gross_loss": Decimal("300.00"),
                "avg_win": Decimal("133.33"),
                "avg_loss": Decimal("-75.00"),
                "largest_win": Decimal("200.00"),
                "largest_loss": Decimal("-100.00"),
            }
        ]

        access = StrategyScopedDataAccess(mock_db_pool, mock_redis_client, mock_user)
        access._execute_fetchall = AsyncMock(return_value=db_rows)

        result = await access.get_trade_stats(
            symbol="AAPL", date_from=date(2025, 1, 1), date_to=date(2025, 1, 31)
        )

        assert result["total_trades"] == 10
        call_args = access._execute_fetchall.call_args
        query = call_args[0][1]
        # Verify filters in query
        assert "symbol" in query
        assert "executed_at >=" in query


class TestStreamTradesForExport:
    """Tests for stream_trades_for_export() async generator."""

    @pytest.mark.asyncio()
    @patch("libs.web_console_data.strategy_scoped_queries.acquire_connection")
    @patch("libs.web_console_data.strategy_scoped_queries.get_authorized_strategies")
    @patch("libs.web_console_data.strategy_scoped_queries._get_cache_encryption_key")
    async def test_stream_trades_for_export_success(
        self,
        mock_get_key: Mock,
        mock_get_strategies: Mock,
        mock_acquire: AsyncMock,
        mock_db_pool: AsyncMock,
        mock_redis_client: AsyncMock,
        mock_user: dict[str, Any],
        sample_strategies: list[str],
    ) -> None:
        """Should stream trades for export."""
        mock_get_strategies.return_value = sample_strategies
        mock_get_key.return_value = None

        # Mock cursor as async iterable
        async def mock_cursor_iter():
            yield {"symbol": "AAPL", "realized_pnl": "100.00"}
            yield {"symbol": "MSFT", "realized_pnl": "200.00"}

        mock_cursor = AsyncMock()
        mock_cursor.__aiter__ = lambda self: mock_cursor_iter()

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value=mock_cursor)
        mock_conn.transaction = Mock()
        mock_conn.transaction.return_value = AsyncMock()
        mock_conn.transaction.return_value.__aenter__ = AsyncMock()
        mock_conn.transaction.return_value.__aexit__ = AsyncMock(return_value=None)

        mock_acquire.return_value = AsyncMock()
        mock_acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_acquire.return_value.__aexit__ = AsyncMock(return_value=None)

        access = StrategyScopedDataAccess(mock_db_pool, mock_redis_client, mock_user)

        # Collect streamed trades
        trades = []
        async for trade in access.stream_trades_for_export(symbol="AAPL"):
            trades.append(trade)

        assert len(trades) == 2
        assert trades[0]["symbol"] == "AAPL"
        assert trades[1]["symbol"] == "MSFT"


class TestGetPortfolioReturns:
    """Tests for get_portfolio_returns() query method (P6T10)."""

    @pytest.mark.asyncio()
    @patch("libs.web_console_data.strategy_scoped_queries.acquire_connection")
    @patch("libs.web_console_data.strategy_scoped_queries.get_authorized_strategies")
    @patch("libs.web_console_data.strategy_scoped_queries._get_cache_encryption_key")
    async def test_get_portfolio_returns_success(
        self,
        mock_get_key: Mock,
        mock_get_strategies: Mock,
        mock_acquire: AsyncMock,
        mock_db_pool: AsyncMock,
        mock_redis_client: AsyncMock,
        mock_user: dict[str, Any],
        sample_strategies: list[str],
    ) -> None:
        """Should return daily returns for authorized strategy."""
        mock_get_strategies.return_value = sample_strategies
        mock_get_key.return_value = None

        mock_conn = AsyncMock()
        mock_acquire.return_value = AsyncMock()
        mock_acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_acquire.return_value.__aexit__ = AsyncMock()

        db_rows = [
            {"date": date(2025, 1, 15), "daily_return": 0.012},
            {"date": date(2025, 1, 16), "daily_return": -0.005},
        ]

        access = StrategyScopedDataAccess(mock_db_pool, mock_redis_client, mock_user)
        access._execute_fetchall = AsyncMock(return_value=db_rows)

        result = await access.get_portfolio_returns(
            strategy_id="strategy-alpha",
            start_date=date(2025, 1, 15),
            end_date=date(2025, 1, 16),
        )

        assert result == db_rows
        access._execute_fetchall.assert_called_once()
        call_args = access._execute_fetchall.call_args
        query = call_args[0][1]
        assert "pnl_daily" in query
        assert "daily_return" in query

    @pytest.mark.asyncio()
    @patch("libs.web_console_data.strategy_scoped_queries.get_authorized_strategies")
    @patch("libs.web_console_data.strategy_scoped_queries._get_cache_encryption_key")
    async def test_get_portfolio_returns_unauthorized_strategy(
        self,
        mock_get_key: Mock,
        mock_get_strategies: Mock,
        mock_db_pool: AsyncMock,
        mock_redis_client: AsyncMock,
        mock_user: dict[str, Any],
        sample_strategies: list[str],
    ) -> None:
        """Should raise PermissionError for unauthorized strategy."""
        mock_get_strategies.return_value = sample_strategies
        mock_get_key.return_value = None

        access = StrategyScopedDataAccess(mock_db_pool, mock_redis_client, mock_user)

        with pytest.raises(PermissionError, match="Not authorized for strategy"):
            await access.get_portfolio_returns(
                strategy_id="unauthorized-strategy",
                start_date=date(2025, 1, 15),
                end_date=date(2025, 1, 16),
            )


class TestVerifyJobOwnership:
    """Tests for verify_job_ownership() method (P6T10)."""

    @pytest.mark.asyncio()
    @patch("libs.web_console_data.strategy_scoped_queries.acquire_connection")
    @patch("libs.web_console_data.strategy_scoped_queries.get_authorized_strategies")
    @patch("libs.web_console_data.strategy_scoped_queries._get_cache_encryption_key")
    async def test_verify_job_ownership_success(
        self,
        mock_get_key: Mock,
        mock_get_strategies: Mock,
        mock_acquire: AsyncMock,
        mock_db_pool: AsyncMock,
        mock_redis_client: AsyncMock,
        mock_user: dict[str, Any],
        sample_strategies: list[str],
    ) -> None:
        """Should succeed for job belonging to authorized strategy."""
        mock_get_strategies.return_value = sample_strategies
        mock_get_key.return_value = None

        mock_conn = AsyncMock()
        mock_acquire.return_value = AsyncMock()
        mock_acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_acquire.return_value.__aexit__ = AsyncMock()

        db_rows = [{"strategy_id": "strategy-alpha"}]

        access = StrategyScopedDataAccess(mock_db_pool, mock_redis_client, mock_user)
        access._execute_fetchall = AsyncMock(return_value=db_rows)

        # Should not raise
        await access.verify_job_ownership("job-123")

        access._execute_fetchall.assert_called_once()
        call_args = access._execute_fetchall.call_args
        query = call_args[0][1]
        assert "backtest_jobs" in query
        assert "job_id" in query

    @pytest.mark.asyncio()
    @patch("libs.web_console_data.strategy_scoped_queries.acquire_connection")
    @patch("libs.web_console_data.strategy_scoped_queries.get_authorized_strategies")
    @patch("libs.web_console_data.strategy_scoped_queries._get_cache_encryption_key")
    async def test_verify_job_ownership_not_found(
        self,
        mock_get_key: Mock,
        mock_get_strategies: Mock,
        mock_acquire: AsyncMock,
        mock_db_pool: AsyncMock,
        mock_redis_client: AsyncMock,
        mock_user: dict[str, Any],
        sample_strategies: list[str],
    ) -> None:
        """Should raise PermissionError for non-existent job."""
        mock_get_strategies.return_value = sample_strategies
        mock_get_key.return_value = None

        mock_conn = AsyncMock()
        mock_acquire.return_value = AsyncMock()
        mock_acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_acquire.return_value.__aexit__ = AsyncMock()

        db_rows: list[dict[str, Any]] = []  # No rows

        access = StrategyScopedDataAccess(mock_db_pool, mock_redis_client, mock_user)
        access._execute_fetchall = AsyncMock(return_value=db_rows)

        with pytest.raises(PermissionError, match="Backtest job not found"):
            await access.verify_job_ownership("nonexistent-job")

    @pytest.mark.asyncio()
    @patch("libs.web_console_data.strategy_scoped_queries.acquire_connection")
    @patch("libs.web_console_data.strategy_scoped_queries.get_authorized_strategies")
    @patch("libs.web_console_data.strategy_scoped_queries._get_cache_encryption_key")
    async def test_verify_job_ownership_unauthorized_strategy(
        self,
        mock_get_key: Mock,
        mock_get_strategies: Mock,
        mock_acquire: AsyncMock,
        mock_db_pool: AsyncMock,
        mock_redis_client: AsyncMock,
        mock_user: dict[str, Any],
        sample_strategies: list[str],
    ) -> None:
        """Should raise PermissionError for job belonging to unauthorized strategy."""
        mock_get_strategies.return_value = sample_strategies
        mock_get_key.return_value = None

        mock_conn = AsyncMock()
        mock_acquire.return_value = AsyncMock()
        mock_acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_acquire.return_value.__aexit__ = AsyncMock()

        db_rows = [{"strategy_id": "unauthorized-strategy"}]

        access = StrategyScopedDataAccess(mock_db_pool, mock_redis_client, mock_user)
        access._execute_fetchall = AsyncMock(return_value=db_rows)

        with pytest.raises(PermissionError, match="Not authorized for backtest job"):
            await access.verify_job_ownership("job-456")
