"""
Unit tests for libs.web_console_services.health_service.

Tests cover:
- ConnectivityStatus model initialization
- HealthMonitorService initialization
- get_all_services_status() delegation
- get_connectivity() with caching, redaction, async/sync handling
- Redis connectivity checks (success, failure, unavailable)
- Postgres connectivity checks (success, failure, unavailable)
- Timeout handling and stale cache fallback
- Redis INFO redaction (sensitive fields and prefixes)
- get_latency_metrics() delegation
- close() resource cleanup

Target: 85%+ branch coverage (baseline from 0%)
"""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import ANY, AsyncMock, Mock, patch

import pytest
import redis

from libs.web_console_services.health_service import ConnectivityStatus, HealthMonitorService


class TestConnectivityStatus:
    """Tests for ConnectivityStatus data model."""

    def test_connectivity_status_initialization_all_fields(self):
        """Test ConnectivityStatus initializes with all fields."""
        now = datetime.now(UTC)
        status = ConnectivityStatus(
            redis_connected=True,
            redis_info={"version": "7.0.0"},
            redis_error=None,
            postgres_connected=True,
            postgres_latency_ms=12.5,
            postgres_error=None,
            checked_at=now,
        )

        assert status.redis_connected is True
        assert status.redis_info == {"version": "7.0.0"}
        assert status.postgres_connected is True
        assert status.postgres_latency_ms == 12.5
        assert status.checked_at == now

    def test_connectivity_status_with_errors(self):
        """Test ConnectivityStatus with error messages."""
        now = datetime.now(UTC)
        status = ConnectivityStatus(
            redis_connected=False,
            redis_info=None,
            redis_error="Connection refused",
            postgres_connected=False,
            postgres_latency_ms=None,
            postgres_error="Database unavailable",
            checked_at=now,
        )

        assert status.redis_connected is False
        assert status.redis_error == "Connection refused"
        assert status.postgres_connected is False
        assert status.postgres_error == "Database unavailable"

    def test_connectivity_status_staleness_tracking(self):
        """Test ConnectivityStatus tracks staleness."""
        now = datetime.now(UTC)
        status = ConnectivityStatus(
            redis_connected=True,
            redis_info={},
            postgres_connected=True,
            postgres_latency_ms=10.0,
            checked_at=now,
            is_stale=True,
            stale_age_seconds=45.0,
        )

        assert status.is_stale is True
        assert status.stale_age_seconds == 45.0


class TestHealthMonitorServiceInitialization:
    """Tests for HealthMonitorService initialization."""

    def test_init_with_all_dependencies(self):
        """Test HealthMonitorService initializes with all dependencies."""
        mock_health = Mock()
        mock_prometheus = Mock()
        mock_redis = Mock()
        mock_db_pool = Mock()

        service = HealthMonitorService(
            health_client=mock_health,
            prometheus_client=mock_prometheus,
            redis_client=mock_redis,
            db_pool=mock_db_pool,
            connectivity_cache_ttl_seconds=60,
        )

        assert service.health is mock_health
        assert service.prometheus is mock_prometheus
        assert service.redis is mock_redis
        assert service.db_pool is mock_db_pool
        assert service._connectivity_cache_ttl == timedelta(seconds=60)
        assert service._connectivity_cache is None

    def test_init_without_optional_dependencies(self):
        """Test HealthMonitorService initializes with None redis/db_pool."""
        mock_health = Mock()
        mock_prometheus = Mock()

        service = HealthMonitorService(
            health_client=mock_health,
            prometheus_client=mock_prometheus,
            redis_client=None,
            db_pool=None,
        )

        assert service.redis is None
        assert service.db_pool is None


class TestGetAllServicesStatus:
    """Tests for get_all_services_status() method."""

    @pytest.mark.asyncio
    async def test_get_all_services_status_delegates_to_health_client(self):
        """Test get_all_services_status() delegates to health_client.check_all()."""
        mock_health = AsyncMock()
        mock_health.check_all = AsyncMock(return_value={"signal_service": {"status": "healthy"}})
        mock_prometheus = Mock()

        service = HealthMonitorService(
            health_client=mock_health,
            prometheus_client=mock_prometheus,
            redis_client=None,
            db_pool=None,
        )

        result = await service.get_all_services_status()

        assert result == {"signal_service": {"status": "healthy"}}
        mock_health.check_all.assert_called_once()


class TestGetConnectivityCaching:
    """Tests for get_connectivity() caching behavior."""

    @pytest.mark.asyncio
    async def test_get_connectivity_returns_fresh_cache(self):
        """Test get_connectivity() returns cached result within TTL."""
        mock_health = AsyncMock()
        mock_prometheus = Mock()
        mock_redis = Mock()
        mock_redis.health_check = Mock(return_value=True)
        mock_redis.get_info = Mock(return_value={"version": "7.0.0"})
        mock_db_pool = AsyncMock()

        service = HealthMonitorService(
            health_client=mock_health,
            prometheus_client=mock_prometheus,
            redis_client=mock_redis,
            db_pool=mock_db_pool,
            connectivity_cache_ttl_seconds=30,
        )

        # First call populates cache
        result1 = await service.get_connectivity()
        assert result1.redis_connected is True

        # Second call within TTL should return cached result
        mock_redis.health_check.reset_mock()
        result2 = await service.get_connectivity()

        assert result2.redis_connected is True
        # Should not call health_check again
        mock_redis.health_check.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_connectivity_refreshes_expired_cache(self):
        """Test get_connectivity() refreshes cache after TTL expires."""
        mock_health = AsyncMock()
        mock_prometheus = Mock()
        mock_redis = Mock()
        mock_redis.health_check = Mock(return_value=True)
        mock_redis.get_info = Mock(return_value={"version": "7.0.0"})
        mock_db_pool = AsyncMock()

        service = HealthMonitorService(
            health_client=mock_health,
            prometheus_client=mock_prometheus,
            redis_client=mock_redis,
            db_pool=mock_db_pool,
            connectivity_cache_ttl_seconds=0.1,  # 100ms TTL
        )

        # First call
        result1 = await service.get_connectivity()
        assert result1.redis_connected is True

        # Wait for cache to expire
        await asyncio.sleep(0.2)

        # Second call should refresh cache
        mock_redis.health_check.reset_mock()
        result2 = await service.get_connectivity()

        assert result2.redis_connected is True
        # Should call health_check again
        mock_redis.health_check.assert_called_once()


class TestGetConnectivityRedisChecks:
    """Tests for get_connectivity() Redis connectivity checks."""

    @pytest.mark.asyncio
    async def test_redis_connected_with_info(self):
        """Test get_connectivity() succeeds with Redis connected and INFO."""
        mock_health = AsyncMock()
        mock_prometheus = Mock()
        mock_redis = Mock()
        mock_redis.health_check = Mock(return_value=True)
        mock_redis.get_info = Mock(return_value={"version": "7.0.0", "uptime_in_seconds": 1000})
        mock_db_pool = None  # Focus on Redis only

        service = HealthMonitorService(
            health_client=mock_health,
            prometheus_client=mock_prometheus,
            redis_client=mock_redis,
            db_pool=mock_db_pool,
        )

        result = await service.get_connectivity()

        assert result.redis_connected is True
        assert result.redis_info == {"version": "7.0.0", "uptime_in_seconds": 1000}
        assert result.redis_error is None

    @pytest.mark.asyncio
    async def test_redis_health_check_false_no_info(self):
        """Test get_connectivity() handles Redis health_check returning False."""
        mock_health = AsyncMock()
        mock_prometheus = Mock()
        mock_redis = Mock()
        mock_redis.health_check = Mock(return_value=False)  # Unhealthy
        mock_db_pool = None

        service = HealthMonitorService(
            health_client=mock_health,
            prometheus_client=mock_prometheus,
            redis_client=mock_redis,
            db_pool=mock_db_pool,
        )

        result = await service.get_connectivity()

        assert result.redis_connected is False
        assert result.redis_info is None
        assert result.redis_error is None

    @pytest.mark.asyncio
    async def test_redis_client_unavailable(self):
        """Test get_connectivity() handles Redis client None."""
        mock_health = AsyncMock()
        mock_prometheus = Mock()
        mock_db_pool = None

        service = HealthMonitorService(
            health_client=mock_health,
            prometheus_client=mock_prometheus,
            redis_client=None,  # No Redis client
            db_pool=mock_db_pool,
        )

        result = await service.get_connectivity()

        assert result.redis_connected is False
        assert result.redis_info is None
        assert result.redis_error == "Redis client unavailable"

    @pytest.mark.asyncio
    async def test_redis_connection_error(self):
        """Test get_connectivity() handles Redis ConnectionError."""
        mock_health = AsyncMock()
        mock_prometheus = Mock()
        mock_redis = Mock()
        mock_redis.health_check = Mock(side_effect=ConnectionError("Connection refused"))
        mock_db_pool = None

        service = HealthMonitorService(
            health_client=mock_health,
            prometheus_client=mock_prometheus,
            redis_client=mock_redis,
            db_pool=mock_db_pool,
        )

        result = await service.get_connectivity()

        assert result.redis_connected is False
        assert result.redis_info is None
        assert "Connection refused" in result.redis_error

    @pytest.mark.asyncio
    async def test_redis_timeout_error(self):
        """Test get_connectivity() handles Redis TimeoutError."""
        mock_health = AsyncMock()
        mock_prometheus = Mock()
        mock_redis = Mock()
        mock_redis.health_check = Mock(side_effect=TimeoutError("Redis timeout"))
        mock_db_pool = None

        service = HealthMonitorService(
            health_client=mock_health,
            prometheus_client=mock_prometheus,
            redis_client=mock_redis,
            db_pool=mock_db_pool,
        )

        result = await service.get_connectivity()

        assert result.redis_connected is False
        assert "Redis timeout" in result.redis_error

    @pytest.mark.asyncio
    async def test_redis_error(self):
        """Test get_connectivity() handles RedisError."""
        mock_health = AsyncMock()
        mock_prometheus = Mock()
        mock_redis = Mock()
        mock_redis.health_check = Mock(side_effect=redis.RedisError("Redis error"))
        mock_db_pool = None

        service = HealthMonitorService(
            health_client=mock_health,
            prometheus_client=mock_prometheus,
            redis_client=mock_redis,
            db_pool=mock_db_pool,
        )

        result = await service.get_connectivity()

        assert result.redis_connected is False
        assert "Redis error" in result.redis_error


class TestGetConnectivityPostgresChecks:
    """Tests for get_connectivity() Postgres connectivity checks."""

    @pytest.mark.asyncio
    async def test_postgres_connected_with_latency(self):
        """Test get_connectivity() succeeds with Postgres connected."""
        mock_health = AsyncMock()
        mock_prometheus = Mock()
        mock_db_pool = AsyncMock()
        mock_conn = AsyncMock()
        mock_cur = AsyncMock()
        mock_cur.execute = AsyncMock()
        mock_cur.fetchone = AsyncMock(return_value=(1,))
        mock_cur.__aenter__ = AsyncMock(return_value=mock_cur)
        mock_cur.__aexit__ = AsyncMock()
        mock_conn.cursor = Mock(return_value=mock_cur)
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock()
        mock_db_pool.connection = Mock(return_value=mock_conn)

        service = HealthMonitorService(
            health_client=mock_health,
            prometheus_client=mock_prometheus,
            redis_client=None,
            db_pool=mock_db_pool,
        )

        result = await service.get_connectivity()

        assert result.postgres_connected is True
        assert result.postgres_latency_ms is not None
        assert result.postgres_latency_ms > 0
        assert result.postgres_error is None

    @pytest.mark.asyncio
    async def test_postgres_pool_unavailable(self):
        """Test get_connectivity() handles db_pool None."""
        mock_health = AsyncMock()
        mock_prometheus = Mock()

        service = HealthMonitorService(
            health_client=mock_health,
            prometheus_client=mock_prometheus,
            redis_client=None,
            db_pool=None,  # No database pool
        )

        result = await service.get_connectivity()

        assert result.postgres_connected is False
        assert result.postgres_latency_ms is None
        assert result.postgres_error == "No database pool configured"

    @pytest.mark.asyncio
    async def test_postgres_connection_error(self):
        """Test get_connectivity() handles Postgres ConnectionError."""
        mock_health = AsyncMock()
        mock_prometheus = Mock()
        mock_db_pool = AsyncMock()
        mock_conn = AsyncMock()
        mock_conn.__aenter__ = AsyncMock(side_effect=ConnectionError("DB connection failed"))
        mock_conn.__aexit__ = AsyncMock()
        mock_db_pool.connection = Mock(return_value=mock_conn)

        service = HealthMonitorService(
            health_client=mock_health,
            prometheus_client=mock_prometheus,
            redis_client=None,
            db_pool=mock_db_pool,
        )

        result = await service.get_connectivity()

        assert result.postgres_connected is False
        assert result.postgres_latency_ms is None
        assert "DB connection failed" in result.postgres_error

    @pytest.mark.asyncio
    async def test_postgres_timeout_error(self):
        """Test get_connectivity() handles Postgres TimeoutError."""
        mock_health = AsyncMock()
        mock_prometheus = Mock()
        mock_db_pool = AsyncMock()
        mock_conn = AsyncMock()
        mock_cur = AsyncMock()
        mock_cur.execute = AsyncMock(side_effect=TimeoutError("Query timeout"))
        mock_cur.__aenter__ = AsyncMock(return_value=mock_cur)
        mock_cur.__aexit__ = AsyncMock()
        mock_conn.cursor = Mock(return_value=mock_cur)
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock()
        mock_db_pool.connection = Mock(return_value=mock_conn)

        service = HealthMonitorService(
            health_client=mock_health,
            prometheus_client=mock_prometheus,
            redis_client=None,
            db_pool=mock_db_pool,
        )

        result = await service.get_connectivity()

        assert result.postgres_connected is False
        assert "Query timeout" in result.postgres_error

    @pytest.mark.asyncio
    async def test_postgres_oserror(self):
        """Test get_connectivity() handles Postgres OSError."""
        mock_health = AsyncMock()
        mock_prometheus = Mock()
        mock_db_pool = AsyncMock()
        mock_conn = AsyncMock()
        mock_conn.__aenter__ = AsyncMock(side_effect=OSError("Network error"))
        mock_conn.__aexit__ = AsyncMock()
        mock_db_pool.connection = Mock(return_value=mock_conn)

        service = HealthMonitorService(
            health_client=mock_health,
            prometheus_client=mock_prometheus,
            redis_client=None,
            db_pool=mock_db_pool,
        )

        result = await service.get_connectivity()

        assert result.postgres_connected is False
        assert "Network error" in result.postgres_error

    @pytest.mark.asyncio
    async def test_postgres_unexpected_exception(self):
        """Test get_connectivity() handles Postgres unexpected Exception."""
        mock_health = AsyncMock()
        mock_prometheus = Mock()
        mock_db_pool = AsyncMock()
        mock_conn = AsyncMock()
        mock_cur = AsyncMock()
        mock_cur.execute = AsyncMock(side_effect=ValueError("Unexpected DB error"))
        mock_cur.__aenter__ = AsyncMock(return_value=mock_cur)
        mock_cur.__aexit__ = AsyncMock()
        mock_conn.cursor = Mock(return_value=mock_cur)
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock()
        mock_db_pool.connection = Mock(return_value=mock_conn)

        service = HealthMonitorService(
            health_client=mock_health,
            prometheus_client=mock_prometheus,
            redis_client=None,
            db_pool=mock_db_pool,
        )

        result = await service.get_connectivity()

        assert result.postgres_connected is False
        assert "Unexpected DB error" in result.postgres_error


class TestRedisInfoRedaction:
    """Tests for Redis INFO redaction in get_connectivity()."""

    @pytest.mark.asyncio
    async def test_redis_info_redacts_sensitive_fields(self):
        """Test get_connectivity() redacts sensitive Redis INFO fields."""
        mock_health = AsyncMock()
        mock_prometheus = Mock()
        mock_redis = Mock()
        mock_redis.health_check = Mock(return_value=True)
        mock_redis.get_info = Mock(
            return_value={
                "version": "7.0.0",
                "requirepass": "secret_password",  # Should be redacted
                "masterauth": "secret_auth",  # Should be redacted
                "uptime_in_seconds": 1000,
                "used_memory": 1024000,
            }
        )

        service = HealthMonitorService(
            health_client=mock_health,
            prometheus_client=mock_prometheus,
            redis_client=mock_redis,
            db_pool=None,
        )

        result = await service.get_connectivity()

        assert result.redis_info is not None
        assert "requirepass" not in result.redis_info
        assert "masterauth" not in result.redis_info
        assert result.redis_info["version"] == "7.0.0"
        assert result.redis_info["uptime_in_seconds"] == 1000

    @pytest.mark.asyncio
    async def test_redis_info_redacts_replication_fields(self):
        """Test get_connectivity() redacts Redis replication/topology fields."""
        mock_health = AsyncMock()
        mock_prometheus = Mock()
        mock_redis = Mock()
        mock_redis.health_check = Mock(return_value=True)
        mock_redis.get_info = Mock(
            return_value={
                "version": "7.0.0",
                "role": "master",  # Should be redacted
                "connected_slaves": 2,  # Should be redacted
                "master_replid": "abc123",  # Should be redacted
                "uptime_in_seconds": 1000,
            }
        )

        service = HealthMonitorService(
            health_client=mock_health,
            prometheus_client=mock_prometheus,
            redis_client=mock_redis,
            db_pool=None,
        )

        result = await service.get_connectivity()

        assert result.redis_info is not None
        assert "role" not in result.redis_info
        assert "connected_slaves" not in result.redis_info
        assert "master_replid" not in result.redis_info
        assert result.redis_info["version"] == "7.0.0"

    @pytest.mark.asyncio
    async def test_redis_info_redacts_fields_by_prefix(self):
        """Test get_connectivity() redacts Redis fields by sensitive prefixes."""
        mock_health = AsyncMock()
        mock_prometheus = Mock()
        mock_redis = Mock()
        mock_redis.health_check = Mock(return_value=True)
        mock_redis.get_info = Mock(
            return_value={
                "version": "7.0.0",
                "slave0": "info",  # Should be redacted (prefix: slave)
                "master_link_status": "up",  # Should be redacted (prefix: master_)
                "cluster_enabled": 0,  # Should be redacted (prefix: cluster_)
                "used_memory": 1024000,
            }
        )

        service = HealthMonitorService(
            health_client=mock_health,
            prometheus_client=mock_prometheus,
            redis_client=mock_redis,
            db_pool=None,
        )

        result = await service.get_connectivity()

        assert result.redis_info is not None
        assert "slave0" not in result.redis_info
        assert "master_link_status" not in result.redis_info
        assert "cluster_enabled" not in result.redis_info
        assert result.redis_info["used_memory"] == 1024000


class TestStaleCache:
    """Tests for stale cache fallback in get_connectivity()."""

    @pytest.mark.asyncio
    async def test_both_checks_fail_uses_stale_cache(self):
        """Test get_connectivity() returns stale cache when both checks fail."""
        mock_health = AsyncMock()
        mock_prometheus = Mock()
        mock_redis = Mock()
        mock_redis.health_check = Mock(return_value=True)
        mock_redis.get_info = Mock(return_value={"version": "7.0.0"})
        mock_db_pool = None

        service = HealthMonitorService(
            health_client=mock_health,
            prometheus_client=mock_prometheus,
            redis_client=mock_redis,
            db_pool=mock_db_pool,
            connectivity_cache_ttl_seconds=10,
        )

        # First call populates cache
        result1 = await service.get_connectivity()
        assert result1.redis_connected is True

        # Simulate both checks failing
        mock_redis.health_check = Mock(side_effect=ConnectionError("Redis down"))

        # Second call should use stale cache
        result2 = await service.get_connectivity()

        assert result2.is_stale is True
        assert result2.stale_age_seconds is not None

    @pytest.mark.asyncio
    async def test_both_checks_fail_no_cache_returns_error(self):
        """Test get_connectivity() returns error status when no cache available."""
        mock_health = AsyncMock()
        mock_prometheus = Mock()
        mock_redis = Mock()
        mock_redis.health_check = Mock(side_effect=ConnectionError("Redis down"))
        mock_db_pool = AsyncMock()
        mock_conn = AsyncMock()
        mock_conn.__aenter__ = AsyncMock(side_effect=ConnectionError("DB down"))
        mock_conn.__aexit__ = AsyncMock()
        mock_db_pool.connection = Mock(return_value=mock_conn)

        service = HealthMonitorService(
            health_client=mock_health,
            prometheus_client=mock_prometheus,
            redis_client=mock_redis,
            db_pool=mock_db_pool,
        )

        # No cache, both checks fail
        result = await service.get_connectivity()

        assert result.redis_connected is False
        assert result.postgres_connected is False
        assert result.is_stale is False  # Not stale, just fresh failure

    @pytest.mark.asyncio
    async def test_stale_cache_expires_after_2x_ttl(self):
        """Test _get_stale_connectivity_or_none() returns None after 2x TTL."""
        mock_health = AsyncMock()
        mock_prometheus = Mock()
        mock_redis = Mock()
        mock_redis.health_check = Mock(return_value=True)
        mock_redis.get_info = Mock(return_value={"version": "7.0.0"})

        service = HealthMonitorService(
            health_client=mock_health,
            prometheus_client=mock_prometheus,
            redis_client=mock_redis,
            db_pool=None,
            connectivity_cache_ttl_seconds=0.1,  # 100ms TTL (stale expires at 200ms)
        )

        # Populate cache
        result1 = await service.get_connectivity()
        assert result1.redis_connected is True

        # Wait for 2x TTL to expire
        await asyncio.sleep(0.25)

        # Manually call _get_stale_connectivity_or_none
        stale = service._get_stale_connectivity_or_none(datetime.now(UTC))
        assert stale is None  # Expired


class TestGetConnectivityTimeout:
    """Tests for get_connectivity() timeout handling."""

    @pytest.mark.asyncio
    async def test_checks_timeout_uses_stale_cache(self, caplog):
        """Test get_connectivity() uses stale cache on timeout."""
        mock_health = AsyncMock()
        mock_prometheus = Mock()
        mock_redis = Mock()
        mock_redis.health_check = Mock(return_value=True)
        mock_redis.get_info = Mock(return_value={"version": "7.0.0"})

        service = HealthMonitorService(
            health_client=mock_health,
            prometheus_client=mock_prometheus,
            redis_client=mock_redis,
            db_pool=None,
        )

        # Populate cache first
        result1 = await service.get_connectivity()
        assert result1.redis_connected is True

        # Simulate timeout by mocking wait_for to raise TimeoutError
        with patch("asyncio.wait_for", side_effect=TimeoutError("Timeout")):
            result2 = await service.get_connectivity()

        # Should use stale cache
        assert result2.is_stale is True
        assert "Connectivity check timed out" in caplog.text

    @pytest.mark.asyncio
    async def test_checks_timeout_no_cache_returns_error(self, caplog):
        """Test get_connectivity() returns error status on timeout with no cache."""
        mock_health = AsyncMock()
        mock_prometheus = Mock()

        service = HealthMonitorService(
            health_client=mock_health,
            prometheus_client=mock_prometheus,
            redis_client=None,
            db_pool=None,
        )

        # Simulate timeout
        with patch("asyncio.wait_for", side_effect=TimeoutError("Timeout")):
            result = await service.get_connectivity()

        assert result.redis_connected is False
        assert result.postgres_connected is False
        assert "Timeout" in result.redis_error
        assert "Connectivity check timed out" in caplog.text

    @pytest.mark.asyncio
    async def test_unexpected_exception_uses_stale_cache(self, caplog):
        """Test get_connectivity() uses stale cache on unexpected exception."""
        mock_health = AsyncMock()
        mock_prometheus = Mock()
        mock_redis = Mock()
        mock_redis.health_check = Mock(return_value=True)
        mock_redis.get_info = Mock(return_value={"version": "7.0.0"})

        service = HealthMonitorService(
            health_client=mock_health,
            prometheus_client=mock_prometheus,
            redis_client=mock_redis,
            db_pool=None,
        )

        # Populate cache
        result1 = await service.get_connectivity()
        assert result1.redis_connected is True

        # Simulate unexpected exception
        with patch("asyncio.get_running_loop", side_effect=RuntimeError("Unexpected error")):
            result2 = await service.get_connectivity()

        assert result2.is_stale is True
        assert "Connectivity check failed unexpectedly" in caplog.text


class TestGetLatencyMetrics:
    """Tests for get_latency_metrics() method."""

    @pytest.mark.asyncio
    async def test_get_latency_metrics_delegates_to_prometheus(self):
        """Test get_latency_metrics() delegates to prometheus_client."""
        mock_health = AsyncMock()
        mock_prometheus = AsyncMock()
        mock_prometheus.get_service_latencies = AsyncMock(
            return_value=({"signal_service": Mock()}, False, None)
        )

        service = HealthMonitorService(
            health_client=mock_health,
            prometheus_client=mock_prometheus,
            redis_client=None,
            db_pool=None,
        )

        result = await service.get_latency_metrics()

        assert result[1] is False  # not stale
        mock_prometheus.get_service_latencies.assert_called_once()


class TestClose:
    """Tests for close() resource cleanup method."""

    @pytest.mark.asyncio
    async def test_close_calls_client_close_methods(self):
        """Test close() calls close() on health and prometheus clients."""
        mock_health = AsyncMock()
        mock_health.close = AsyncMock()
        mock_prometheus = AsyncMock()
        mock_prometheus.close = AsyncMock()

        service = HealthMonitorService(
            health_client=mock_health,
            prometheus_client=mock_prometheus,
            redis_client=None,
            db_pool=None,
        )

        await service.close()

        mock_health.close.assert_called_once()
        mock_prometheus.close.assert_called_once()
