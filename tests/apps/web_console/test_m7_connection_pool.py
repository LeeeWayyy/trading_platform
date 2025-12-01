"""
Tests for M7: Web Console Connection Pooling.

M7 Fix: Uses connection pooling for efficient database access in the
web console audit log viewer.

Contract:
- Connection pool is cached via @st.cache_resource across Streamlit reruns
- Pool is shared across all requests
- Falls back gracefully if pool initialization fails
"""

from typing import Any
from unittest.mock import MagicMock, patch

import pytest


class TestConnectionPoolInit:
    """Test connection pool initialization."""

    def test_get_db_pool_returns_none_on_import_error(self) -> None:
        """Without psycopg_pool module, should return None gracefully."""
        import apps.web_console.app as app_module
        import builtins

        # Clear cache to force re-initialization
        app_module._get_db_pool.clear()

        # Store original import
        original_import = builtins.__import__

        def mock_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "psycopg_pool":
                raise ImportError("No module named 'psycopg_pool'")
            return original_import(name, *args, **kwargs)

        # Patch import to fail for psycopg_pool
        with patch.object(builtins, "__import__", side_effect=mock_import):
            result = app_module._get_db_pool()
            # On import error, should return None
            assert result is None

    def test_get_db_pool_uses_cache_resource(self) -> None:
        """Pool should be cached via @st.cache_resource."""
        import apps.web_console.app as app_module

        # The function should have a clear() method from st.cache_resource
        assert hasattr(app_module._get_db_pool, "clear")

    def test_get_db_pool_returns_pool_on_success(self) -> None:
        """When psycopg_pool is available, should return a pool."""
        import apps.web_console.app as app_module

        # Clear cache to force re-initialization
        app_module._get_db_pool.clear()

        mock_pool = MagicMock()
        mock_psycopg_pool = MagicMock()
        mock_psycopg_pool.ConnectionPool.return_value = mock_pool

        # Patch builtins.__import__ to return our mock for psycopg_pool
        import builtins

        original_import = builtins.__import__

        def mock_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "psycopg_pool":
                return mock_psycopg_pool
            return original_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", side_effect=mock_import):
            result = app_module._get_db_pool()

            # Verify ConnectionPool was called with config values
            mock_psycopg_pool.ConnectionPool.assert_called_once()
            # Verify pool was returned (not None)
            assert result is mock_pool


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
    """Test that render_audit_log uses connection pool."""

    def test_audit_log_uses_pool_when_available(self) -> None:
        """render_audit_log should use pool.connection() when pool is available."""
        import apps.web_console.app as app_module

        # Create mock pool
        mock_pool = MagicMock()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []

        # Setup context managers
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_pool.connection.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_pool.connection.return_value.__exit__ = MagicMock(return_value=False)

        # Mock _get_db_pool to return our mock pool
        with patch.object(app_module, "_get_db_pool", return_value=mock_pool):
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
                                    mock_pool.connection.assert_called()

    def test_audit_log_falls_back_when_pool_unavailable(self) -> None:
        """render_audit_log should fall back to direct connect when pool is None."""
        import apps.web_console.app as app_module
        from apps.web_console import config

        # Mock _get_db_pool to return None (pool unavailable)
        with patch.object(app_module, "_get_db_pool", return_value=None):
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
                                    {"psycopg": mock_psycopg},
                                ):
                                    app_module.render_audit_log()

                                    # Verify direct connect was called as fallback
                                    mock_psycopg.connect.assert_called_once_with(
                                        config.DATABASE_URL,
                                        connect_timeout=config.DATABASE_CONNECT_TIMEOUT,
                                    )
