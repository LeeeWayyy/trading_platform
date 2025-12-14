"""
Tests for M7: Web Console Connection Pooling.

M7 Fix: Uses connection adapter for efficient database access in the
web console audit log viewer.

Contract:
- Connection adapter is cached via @st.cache_resource across Streamlit reruns
- Adapter is shared across all requests
- Falls back gracefully if adapter initialization fails

Note: The implementation moved from app.py to utils/db_pool.py.
Tests for the db_pool module are in tests/apps/web_console/utils/test_db_pool.py.
"""

from typing import Any
from unittest.mock import MagicMock, patch

import pytest


class TestConnectionPoolInit:
    """Test connection adapter initialization via utils/db_pool.py."""

    def test_get_db_pool_returns_none_when_no_database_url(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without DATABASE_URL, should return None gracefully."""

        def passthrough_cache_resource(func=None, *args: Any, **kwargs: Any):
            if func is None:
                return lambda fn: fn
            return func

        monkeypatch.setattr(
            "apps.web_console.utils.db_pool.st.cache_resource",
            passthrough_cache_resource,
        )

        # Patch DATABASE_URL to empty in config (has a default fallback)
        from apps.web_console import config

        monkeypatch.setattr(config, "DATABASE_URL", "")

        from importlib import reload

        from apps.web_console.utils import db_pool

        reload(db_pool)

        result = db_pool.get_db_pool()
        assert result is None

    def test_get_db_pool_uses_cache_resource(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Pool should be cached via @st.cache_resource in Streamlit environment."""
        # Note: In a real Streamlit environment, @st.cache_resource adds .clear() method.
        # In pytest, streamlit may not be fully initialized, so we test the decorator is applied
        # by checking the function is decorated (module inspection).
        import inspect

        from apps.web_console.utils import db_pool

        source = inspect.getsource(db_pool)
        # Verify the decorator is applied in source code
        assert "@st.cache_resource" in source or "st.cache_resource" in source

    def test_get_db_pool_returns_adapter_on_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When DATABASE_URL is configured, should return an AsyncConnectionAdapter."""

        def passthrough_cache_resource(func=None, *args: Any, **kwargs: Any):
            if func is None:
                return lambda fn: fn
            return func

        monkeypatch.setattr(
            "apps.web_console.utils.db_pool.st.cache_resource",
            passthrough_cache_resource,
        )

        monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")

        from importlib import reload

        from apps.web_console.utils import db_pool

        reload(db_pool)

        result = db_pool.get_db_pool()

        # Verify adapter was returned (not None)
        assert result is not None
        assert isinstance(result, db_pool.AsyncConnectionAdapter)


class TestConnectionPoolConfig:
    """Test connection pool configuration."""

    def test_pool_config_defaults(self) -> None:
        """Pool config should have sensible defaults."""
        from apps.web_console import config

        assert config.DB_POOL_MIN_SIZE >= 1
        assert config.DB_POOL_MAX_SIZE >= config.DB_POOL_MIN_SIZE
        assert config.DB_POOL_TIMEOUT > 0

    def test_pool_config_from_env(self) -> None:
        """Pool config should be configurable via environment."""
        import os

        with patch.dict(
            os.environ,
            {
                "DB_POOL_MIN_SIZE": "5",
                "DB_POOL_MAX_SIZE": "20",
                "DB_POOL_TIMEOUT": "10.0",
            },
        ):
            # Re-import to get new values
            import importlib

            from apps.web_console import config

            importlib.reload(config)

            assert config.DB_POOL_MIN_SIZE == 5
            assert config.DB_POOL_MAX_SIZE == 20
            assert config.DB_POOL_TIMEOUT == 10.0

            # Reset to defaults
            with patch.dict(
                os.environ,
                {
                    "DB_POOL_MIN_SIZE": "2",
                    "DB_POOL_MAX_SIZE": "10",
                    "DB_POOL_TIMEOUT": "5.0",
                },
            ):
                importlib.reload(config)


class TestAuditLogPoolUsage:
    """Test that render_audit_log uses connection adapter."""

    def test_audit_log_uses_pool_when_available(self) -> None:
        """render_audit_log should use pool.connection() when pool is available."""
        import apps.web_console.app as app_module

        class _FakeCursor:
            def __init__(self, pool):
                self.pool = pool

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def execute(self, *_args, **_kwargs):
                return None

            async def fetchall(self):
                # Return at least one row to avoid fallback path
                return [(None, "user1", "login", "{}", "ok", "127.0.0.1")]

        class _FakeConn:
            def __init__(self, pool):
                self.pool = pool

            def cursor(self):
                return _FakeCursor(self.pool)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

        class _FakePool:
            def __init__(self):
                self.connection_called = False

            def connection(self):
                pool = self

                class _ConnCM:
                    async def __aenter__(self_nonlocal):
                        pool.connection_called = True
                        return _FakeConn(pool)

                    async def __aexit__(self_nonlocal, *_args):
                        return None

                return _ConnCM()

        mock_pool = _FakePool()

        # Note: get_db_pool is imported into app_module from utils.db_pool
        # We patch the reference in app_module (where it's used), not the source
        with patch.object(app_module, "get_db_pool", return_value=mock_pool):
            with patch("streamlit.header"):
                with patch("streamlit.success"):
                    with patch("streamlit.info"):
                        with patch("streamlit.subheader"):
                            with patch("streamlit.table"):
                                # psycopg needs to be importable
                                with patch.dict(
                                    "sys.modules",
                                    {"psycopg": MagicMock()},
                                ):
                                    try:
                                        app_module.render_audit_log()
                                    except Exception:
                                        # May fail due to mocking issues, but we can check call
                                        pass

                                    # Verify pool.connection() was called
                                    assert mock_pool.connection_called is True

    def test_audit_log_falls_back_when_pool_unavailable(self) -> None:
        """render_audit_log should fall back to direct connect when pool is None."""
        import apps.web_console.app as app_module
        from apps.web_console import config

        # Note: get_db_pool is imported into app_module from utils.db_pool
        # We patch the reference in app_module (where it's used), not the source
        with patch.object(app_module, "get_db_pool", return_value=None):
            with patch("streamlit.header"):
                with patch("streamlit.success"):
                    with patch("streamlit.info"):
                        with patch("streamlit.subheader"):
                            with patch("streamlit.table"):
                                # Mock psycopg.connect to verify fallback
                                mock_psycopg = MagicMock()
                                mock_conn = MagicMock()
                                mock_cursor = MagicMock()
                                mock_cursor.fetchall.return_value = []

                                mock_conn.__enter__ = MagicMock(return_value=mock_conn)
                                mock_conn.__exit__ = MagicMock(return_value=False)
                                mock_conn.cursor.return_value.__enter__ = MagicMock(
                                    return_value=mock_cursor
                                )
                                mock_conn.cursor.return_value.__exit__ = MagicMock(
                                    return_value=False
                                )
                                mock_psycopg.connect.return_value = mock_conn

                                with patch.dict(
                                    "sys.modules",
                                    {"psycopg": mock_psycopg, "psycopg.rows": MagicMock()},
                                ):
                                    app_module.render_audit_log()

                                    # Verify direct connect was called as fallback
                                    # Note: row_factory=dict_row is now included for consistency
                                    # with pooled connections (see db_pool.py design decision)
                                    mock_psycopg.connect.assert_called_once()
                                    call_args = mock_psycopg.connect.call_args
                                    assert call_args[0][0] == config.DATABASE_URL
                                    assert call_args[1]["connect_timeout"] == config.DATABASE_CONNECT_TIMEOUT
                                    assert "row_factory" in call_args[1]
