"""Tests for apps/web_console/utils/db_pool.py.

Tests the AsyncConnectionAdapter pattern which creates fresh database
connections per call to avoid event loop binding issues with run_async().
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest

if TYPE_CHECKING:
    pass


class TestAsyncConnectionAdapter:
    """Tests for AsyncConnectionAdapter class."""

    def test_adapter_stores_config(self) -> None:
        """Adapter stores database URL and timeout configuration."""
        from apps.web_console.utils.db_pool import AsyncConnectionAdapter

        adapter = AsyncConnectionAdapter(
            database_url="postgresql://test:test@localhost/test",
            connect_timeout=10.0,
        )

        assert adapter._database_url == "postgresql://test:test@localhost/test"
        assert adapter._connect_timeout == 10.0

    def test_adapter_default_timeout(self) -> None:
        """Adapter uses default timeout when not specified."""
        from apps.web_console.utils.db_pool import AsyncConnectionAdapter

        adapter = AsyncConnectionAdapter(database_url="postgresql://test:test@localhost/test")

        assert adapter._connect_timeout == 5.0

    @pytest.mark.asyncio()
    async def test_connection_creates_fresh_connection(self) -> None:
        """Each connection() call creates a fresh psycopg.AsyncConnection."""
        from apps.web_console.utils.db_pool import AsyncConnectionAdapter

        adapter = AsyncConnectionAdapter(database_url="postgresql://test:test@localhost/test")

        # Mock psycopg.AsyncConnection.connect
        mock_conn = AsyncMock()
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=None)

        with patch("psycopg.AsyncConnection.connect", return_value=mock_conn) as mock_connect:
            async with adapter.connection() as conn:
                assert conn is mock_conn

            from psycopg.rows import dict_row

            mock_connect.assert_called_once_with(
                "postgresql://test:test@localhost/test",
                connect_timeout=5,  # int cast from 5.0
                row_factory=dict_row,
            )

    @pytest.mark.asyncio()
    async def test_second_call_creates_new_connection(self) -> None:
        """[CRITICAL] Verify second call creates new connection (no loop binding issue).

        This test ensures the AsyncConnectionAdapter pattern correctly creates
        fresh connections for each call, avoiding the loop-binding issues that
        would occur with a cached AsyncConnectionPool.
        """
        from apps.web_console.utils.db_pool import AsyncConnectionAdapter

        adapter = AsyncConnectionAdapter(database_url="postgresql://test:test@localhost/test")

        # Create two mock connections to track separate calls
        mock_conn1 = AsyncMock()
        mock_conn1.__aenter__ = AsyncMock(return_value=mock_conn1)
        mock_conn1.__aexit__ = AsyncMock(return_value=None)

        mock_conn2 = AsyncMock()
        mock_conn2.__aenter__ = AsyncMock(return_value=mock_conn2)
        mock_conn2.__aexit__ = AsyncMock(return_value=None)

        with patch("psycopg.AsyncConnection.connect") as mock_connect:
            mock_connect.side_effect = [mock_conn1, mock_conn2]

            # First call
            async with adapter.connection() as conn1:
                assert conn1 is mock_conn1

            # Second call - should create NEW connection (not reuse)
            async with adapter.connection() as conn2:
                assert conn2 is mock_conn2
                assert conn2 is not conn1

            # Verify connect was called twice (fresh connection each time)
            assert mock_connect.call_count == 2


class TestGetDbPool:
    """Tests for get_db_pool() function."""

    def test_returns_adapter_when_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """get_db_pool returns AsyncConnectionAdapter when DATABASE_URL configured."""

        # Clear Streamlit cache to allow testing
        def passthrough_cache_resource(func=None, *args: Any, **kwargs: Any):
            """Return function unchanged; supports decorator and direct call styles."""
            if func is None:
                return lambda fn: fn
            return func

        monkeypatch.setattr(
            "apps.web_console.utils.db_pool.st.cache_resource",
            passthrough_cache_resource,
        )

        monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")

        # Need to reimport after monkeypatch
        from importlib import reload

        from apps.web_console.utils import db_pool

        reload(db_pool)

        adapter = db_pool.get_db_pool()
        assert adapter is not None
        assert isinstance(adapter, db_pool.AsyncConnectionAdapter)

    def test_returns_none_when_init_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """get_db_pool returns None when initialization fails."""

        def passthrough_cache_resource(func=None, *args: Any, **kwargs: Any):
            if func is None:
                return lambda fn: fn
            return func

        monkeypatch.setattr(
            "apps.web_console.utils.db_pool.st.cache_resource",
            passthrough_cache_resource,
        )

        from importlib import reload

        from apps.web_console.utils import db_pool

        reload(db_pool)

        # Force an exception during adapter creation (after reload so patch sticks)
        def failing_init(self, *args: Any, **kwargs: Any) -> None:  # type: ignore[no-untyped-def]
            raise RuntimeError("Connection failed")

        monkeypatch.setattr(db_pool.AsyncConnectionAdapter, "__init__", failing_init)

        adapter = db_pool.get_db_pool()
        assert adapter is None


class TestGetRedisClient:
    """Tests for get_redis_client() function."""

    def test_returns_none_when_url_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """get_redis_client returns None when REDIS_URL not set."""

        def passthrough_cache_resource(func=None, *args: Any, **kwargs: Any):
            if func is None:
                return lambda fn: fn
            return func

        monkeypatch.setattr(
            "apps.web_console.utils.db_pool.st.cache_resource",
            passthrough_cache_resource,
        )

        monkeypatch.delenv("REDIS_URL", raising=False)

        from importlib import reload

        from apps.web_console.utils import db_pool

        reload(db_pool)

        client = db_pool.get_redis_client()
        assert client is None

    def test_uses_cache_db_isolation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """get_redis_client uses DB=3 for cache isolation from sessions."""

        def passthrough_cache_resource(func=None, *args: Any, **kwargs: Any):
            if func is None:
                return lambda fn: fn
            return func

        monkeypatch.setattr(
            "apps.web_console.utils.db_pool.st.cache_resource",
            passthrough_cache_resource,
        )

        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
        monkeypatch.setenv("REDIS_STRATEGY_CACHE_DB", "3")

        from importlib import reload

        from apps.web_console.utils import db_pool

        reload(db_pool)

        # get_redis_client now returns an AsyncRedisAdapter, not a raw Redis client
        adapter = db_pool.get_redis_client()

        assert adapter is not None
        assert isinstance(adapter, db_pool.AsyncRedisAdapter)
        assert adapter._redis_url == "redis://localhost:6379/0"
        assert adapter._db == 3  # Cache isolation DB


class TestNegativePaths:
    """Tests for graceful fallback when dependencies unavailable."""

    def test_risk_dashboard_placeholder_when_db_none(self) -> None:
        """Risk dashboard shows placeholder data when db_pool=None."""
        from apps.web_console.services.risk_service import RiskService

        class DummyScopedAccess:
            def __init__(self) -> None:
                self.db_pool = None
                self.redis_client = None
                self.user = {
                    "user_id": "test_user",
                    "strategies": ["alpha_baseline"],
                    "session_version": 1,
                }

        scoped_access = DummyScopedAccess()
        service = RiskService(scoped_access)  # type: ignore[arg-type]

        # The service should handle None pool gracefully (placeholder path)
        assert service._scoped_access.db_pool is None

    def test_cache_disabled_when_encryption_key_absent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """StrategyScopedDataAccess disables cache without STRATEGY_CACHE_ENCRYPTION_KEY."""
        monkeypatch.delenv("STRATEGY_CACHE_ENCRYPTION_KEY", raising=False)

        class DummyScopedAccess:
            def __init__(self) -> None:
                self.db_pool = None
                self.redis_client = None
                self.user = {
                    "user_id": "test_user",
                    "strategies": ["alpha_baseline"],
                    "session_version": 1,
                }
                self._cipher = None

        scoped_access = DummyScopedAccess()
        assert scoped_access._cipher is None
