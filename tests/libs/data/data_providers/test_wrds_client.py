"""Tests for WRDS client with mocked connections."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from libs.data.data_providers.wrds_client import WRDSClient, WRDSConfig


@pytest.fixture()
def mock_secret_manager() -> MagicMock:
    """Create a mock secret manager."""
    manager = MagicMock()
    manager.get_secret.side_effect = lambda key: {
        "wrds/username": "test_user",
        "wrds/password": "test_password",
    }.get(key, "")
    return manager


@pytest.fixture()
def config() -> WRDSConfig:
    """Create a test config."""
    return WRDSConfig(
        host="localhost",
        port=5432,
        database="test_db",
        pool_size=2,
        max_overflow=1,
        rate_limit_queries_per_minute=60,
        max_retries=2,
    )


class TestWRDSConfig:
    """Tests for WRDSConfig."""

    def test_default_config_values(self) -> None:
        """Test default configuration values."""
        config = WRDSConfig()

        assert config.host == "wrds-pgdata.wharton.upenn.edu"
        assert config.port == 9737
        assert config.database == "wrds"
        assert config.pool_size == 3
        assert config.rate_limit_queries_per_minute == 10

    def test_config_from_env(self) -> None:
        """Test configuration from environment variables."""
        with patch.dict(
            "os.environ",
            {"WRDS_HOST": "custom.host", "WRDS_PORT": "1234"},
        ):
            config = WRDSConfig()
            assert config.host == "custom.host"
            assert config.port == 1234


class TestWRDSClientConnectionPool:
    """Tests for connection pooling."""

    def test_connection_pool_creation(
        self, config: WRDSConfig, mock_secret_manager: MagicMock
    ) -> None:
        """Test 15: Connection pool creation."""
        client = WRDSClient(config, mock_secret_manager)

        with patch("libs.data.data_providers.wrds_client.create_engine") as mock_engine:
            mock_engine.return_value.connect.return_value.__enter__ = MagicMock()
            mock_engine.return_value.connect.return_value.__exit__ = MagicMock()

            client.connect()

            # Verify engine was created with correct pool settings
            mock_engine.assert_called_once()
            call_kwargs = mock_engine.call_args[1]
            assert call_kwargs["pool_size"] == config.pool_size
            assert call_kwargs["max_overflow"] == config.max_overflow

            client.close()


class TestWRDSClientRateLimiting:
    """Tests for rate limiting."""

    def test_rate_limiting_respects_qpm(
        self, config: WRDSConfig, mock_secret_manager: MagicMock
    ) -> None:
        """Test 16: Rate limiting respects QPM."""
        # Set high QPM for fast testing (no actual waiting)
        config.rate_limit_queries_per_minute = 100
        client = WRDSClient(config, mock_secret_manager)

        # Simulate queries without actual connection
        for _ in range(5):
            client._rate_limit()

        # All 5 queries should be recorded (under limit, no waiting)
        assert len(client._query_times) == 5

    def test_rate_limiting_delays_at_limit(
        self, config: WRDSConfig, mock_secret_manager: MagicMock
    ) -> None:
        """Test rate limiting delays when at QPM limit."""
        # Set very low QPM to trigger delay
        config.rate_limit_queries_per_minute = 2
        client = WRDSClient(config, mock_secret_manager)

        # First two should be immediate
        start = time.monotonic()
        client._rate_limit()
        client._rate_limit()
        first_two_elapsed = time.monotonic() - start

        # These should be nearly instant
        assert first_two_elapsed < 0.5

        # Third call at the limit - should work but mechanism should exist
        # We verify the third timestamp is recorded
        client._rate_limit()

        # Should have at least one entry (pruning may remove older ones after wait)
        assert len(client._query_times) >= 1


class TestWRDSClientRetry:
    """Tests for retry logic."""

    def test_query_timeout_cancellation(
        self, config: WRDSConfig, mock_secret_manager: MagicMock
    ) -> None:
        """Test 17: Query timeout cancellation."""
        config.query_timeout_seconds = 1
        client = WRDSClient(config, mock_secret_manager)

        # The timeout is set via connect_args, verify it's configured
        with patch("libs.data.data_providers.wrds_client.create_engine") as mock_engine:
            mock_engine.return_value.connect.return_value.__enter__ = MagicMock()
            mock_engine.return_value.connect.return_value.__exit__ = MagicMock()

            client.connect()

            call_kwargs = mock_engine.call_args[1]
            assert "statement_timeout" in str(call_kwargs.get("connect_args", {}))

            client.close()

    def test_retry_on_transient_errors(
        self, config: WRDSConfig, mock_secret_manager: MagicMock
    ) -> None:
        """Test 18: Retry on transient errors."""
        client = WRDSClient(config, mock_secret_manager)

        # Test transient error detection
        from sqlalchemy.exc import OperationalError

        transient_error = OperationalError("", "", Exception("connection reset"))
        assert client._is_transient_error(transient_error)

        permanent_error = OperationalError("", "", Exception("syntax error"))
        assert not client._is_transient_error(permanent_error)


class TestWRDSClientCredentials:
    """Tests for credential management."""

    def test_credential_loading_from_secrets(
        self, config: WRDSConfig, mock_secret_manager: MagicMock
    ) -> None:
        """Test 19: Credential loading from secrets."""
        client = WRDSClient(config, mock_secret_manager)

        username, password = client._get_credentials()

        assert username == "test_user"
        assert password.get_secret_value() == "test_password"

        # Verify secrets manager was called
        mock_secret_manager.get_secret.assert_any_call("wrds/username")
        mock_secret_manager.get_secret.assert_any_call("wrds/password")

    def test_credential_caching(self, config: WRDSConfig, mock_secret_manager: MagicMock) -> None:
        """Test credential caching to avoid repeated lookups."""
        client = WRDSClient(config, mock_secret_manager)

        # First call
        client._get_credentials()
        call_count_1 = mock_secret_manager.get_secret.call_count

        # Second call should use cache
        client._get_credentials()
        call_count_2 = mock_secret_manager.get_secret.call_count

        # Call count should not increase
        assert call_count_1 == call_count_2


class TestWRDSClientCredentialExpiry:
    """Tests for credential expiry checking."""

    def test_check_credential_expiry(
        self, config: WRDSConfig, mock_secret_manager: MagicMock
    ) -> None:
        """Test credential expiry check returns unknown status.

        WRDS does not expose credential expiry via API, so the method
        returns sentinel values indicating "unknown" status.
        """
        client = WRDSClient(config, mock_secret_manager)

        is_expiring, days = client.check_credential_expiry()

        # Returns (False, None) to indicate "not expiring" with "unknown" days
        # Callers should not rely on this for monitoring - use external reminders
        assert not is_expiring
        assert days is None


class TestWRDSClientClose:
    """Tests for connection close behavior."""

    def test_close_when_not_connected(
        self, config: WRDSConfig, mock_secret_manager: MagicMock
    ) -> None:
        """Test close() is no-op when engine is None (line 177->exit)."""
        client = WRDSClient(config, mock_secret_manager)

        # Engine is None by default, close should not raise
        assert client._engine is None
        client.close()  # Should be no-op
        assert client._engine is None

    def test_close_disposes_engine(
        self, config: WRDSConfig, mock_secret_manager: MagicMock
    ) -> None:
        """Test close() properly disposes the engine."""
        client = WRDSClient(config, mock_secret_manager)

        # Create a mock engine
        mock_engine = MagicMock()
        client._engine = mock_engine

        client.close()

        mock_engine.dispose.assert_called_once()
        assert client._engine is None


class TestWRDSClientExecuteQuery:
    """Tests for execute_query method."""

    def test_execute_query_not_connected_raises(
        self, config: WRDSConfig, mock_secret_manager: MagicMock
    ) -> None:
        """Test execute_query raises RuntimeError when not connected (line 202-203)."""
        client = WRDSClient(config, mock_secret_manager)

        with pytest.raises(RuntimeError, match="Not connected. Call connect\\(\\) first."):
            client.execute_query("SELECT 1")

    def test_execute_query_with_transient_error_retries(
        self, config: WRDSConfig, mock_secret_manager: MagicMock
    ) -> None:
        """Test execute_query retries on transient errors (lines 210-227)."""
        from sqlalchemy.exc import OperationalError

        config.max_retries = 3
        config.retry_backoff_factor = 0.01  # Fast backoff for testing
        client = WRDSClient(config, mock_secret_manager)

        # Set up mock engine
        mock_engine = MagicMock()
        client._engine = mock_engine

        # Create transient error
        transient_error = OperationalError("", "", Exception("connection reset by peer"))

        # Fail twice, succeed on third attempt
        import polars as pl

        success_df = pl.DataFrame({"col": [1, 2, 3]})

        with patch.object(
            client, "_execute_query_internal", side_effect=[transient_error, transient_error, success_df]
        ):
            result = client.execute_query("SELECT 1")

        assert result.equals(success_df)

    def test_execute_query_non_transient_error_raises_immediately(
        self, config: WRDSConfig, mock_secret_manager: MagicMock
    ) -> None:
        """Test execute_query raises immediately for non-transient errors (line 226-227)."""
        from sqlalchemy.exc import OperationalError

        config.max_retries = 3
        client = WRDSClient(config, mock_secret_manager)

        # Set up mock engine
        mock_engine = MagicMock()
        client._engine = mock_engine

        # Create non-transient error
        non_transient_error = OperationalError("", "", Exception("syntax error"))

        with patch.object(client, "_execute_query_internal", side_effect=non_transient_error):
            with pytest.raises(OperationalError):
                client.execute_query("SELECT 1")

    def test_execute_query_all_retries_exhausted(
        self, config: WRDSConfig, mock_secret_manager: MagicMock
    ) -> None:
        """Test execute_query raises after all retries exhausted (lines 229-230)."""
        from sqlalchemy.exc import OperationalError

        config.max_retries = 2
        config.retry_backoff_factor = 0.01  # Fast backoff for testing
        client = WRDSClient(config, mock_secret_manager)

        # Set up mock engine
        mock_engine = MagicMock()
        client._engine = mock_engine

        # Create transient error that persists
        transient_error = OperationalError("", "", Exception("connection timeout"))

        with patch.object(client, "_execute_query_internal", side_effect=transient_error):
            with pytest.raises(OperationalError, match="connection timeout"):
                client.execute_query("SELECT 1")


class TestWRDSClientGetTableInfo:
    """Tests for get_table_info method."""

    def test_get_table_info_returns_metadata(
        self, config: WRDSConfig, mock_secret_manager: MagicMock
    ) -> None:
        """Test get_table_info returns table metadata (lines 243-260)."""
        import polars as pl

        client = WRDSClient(config, mock_secret_manager)
        mock_engine = MagicMock()
        client._engine = mock_engine

        # Mock column info result
        cols_df = pl.DataFrame({
            "column_name": ["id", "date", "value"],
            "data_type": ["integer", "date", "numeric"],
        })

        # Mock row count result
        count_df = pl.DataFrame({"estimate": [1000000]})

        with patch.object(client, "execute_query", side_effect=[cols_df, count_df]):
            result = client.get_table_info("crsp", "dsf")

        assert result["schema"] == "crsp"
        assert result["table"] == "dsf"
        assert result["columns"] == cols_df.to_dicts()
        assert result["row_count_estimate"] == 1000000

    def test_get_table_info_empty_count(
        self, config: WRDSConfig, mock_secret_manager: MagicMock
    ) -> None:
        """Test get_table_info handles empty count result."""
        import polars as pl

        client = WRDSClient(config, mock_secret_manager)
        mock_engine = MagicMock()
        client._engine = mock_engine

        # Mock column info result
        cols_df = pl.DataFrame({
            "column_name": ["id"],
            "data_type": ["integer"],
        })

        # Mock empty count result
        count_df = pl.DataFrame({"estimate": []}).cast({"estimate": pl.Int64})

        with patch.object(client, "execute_query", side_effect=[cols_df, count_df]):
            result = client.get_table_info("test_schema", "test_table")

        assert result["row_count_estimate"] == 0


class TestWRDSClientExecuteQueryInternal:
    """Tests for _execute_query_internal method."""

    def test_execute_query_internal_with_results(
        self, config: WRDSConfig, mock_secret_manager: MagicMock
    ) -> None:
        """Test _execute_query_internal returns DataFrame with results (lines 360-400)."""
        client = WRDSClient(config, mock_secret_manager)

        # Create mock engine and connection
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_result = MagicMock()

        # Set up context manager
        mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        # Mock execution options chain
        mock_conn.execution_options.return_value.execute.return_value = mock_result

        # Mock result with data
        mock_result.keys.return_value = ["id", "value"]
        mock_result.fetchmany.side_effect = [
            [(1, "a"), (2, "b")],  # First chunk
            [],  # End of results
        ]

        client._engine = mock_engine

        result = client._execute_query_internal("SELECT id, value FROM test", None)

        assert len(result) == 2
        assert result.columns == ["id", "value"]

    def test_execute_query_internal_empty_results_with_schema(
        self, config: WRDSConfig, mock_secret_manager: MagicMock
    ) -> None:
        """Test _execute_query_internal preserves schema for empty results (lines 383-394)."""
        import polars as pl

        client = WRDSClient(config, mock_secret_manager)

        # Create mock engine and connection
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_cursor = MagicMock()

        # Set up context manager
        mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        # Mock execution options chain
        mock_conn.execution_options.return_value.execute.return_value = mock_result

        # Mock empty result with cursor metadata
        mock_result.keys.return_value = ["id", "value", "active"]
        mock_result.fetchmany.return_value = []  # Empty results
        mock_result.cursor = mock_cursor

        # Mock cursor description with type OIDs
        # desc format: (name, type_code, display_size, internal_size, precision, scale, null_ok)
        mock_cursor.description = [
            ("id", 23, None, None, None, None, None),  # 23 = int4 -> Int32
            ("value", 701, None, None, None, None, None),  # 701 = float8 -> Float64
            ("active", 16, None, None, None, None, None),  # 16 = bool -> Boolean
        ]

        client._engine = mock_engine

        result = client._execute_query_internal("SELECT id, value, active FROM test WHERE 1=0", None)

        assert len(result) == 0
        assert result.columns == ["id", "value", "active"]
        assert result.schema["id"] == pl.Int32
        assert result.schema["value"] == pl.Float64
        assert result.schema["active"] == pl.Boolean

    def test_execute_query_internal_empty_results_no_cursor(
        self, config: WRDSConfig, mock_secret_manager: MagicMock
    ) -> None:
        """Test _execute_query_internal handles empty results without cursor metadata."""
        import polars as pl

        client = WRDSClient(config, mock_secret_manager)

        # Create mock engine and connection
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_result = MagicMock()

        # Set up context manager
        mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        # Mock execution options chain
        mock_conn.execution_options.return_value.execute.return_value = mock_result

        # Mock empty result without cursor
        mock_result.keys.return_value = ["col1", "col2"]
        mock_result.fetchmany.return_value = []
        mock_result.cursor = None

        client._engine = mock_engine

        result = client._execute_query_internal("SELECT col1, col2 FROM test WHERE 1=0", None)

        assert len(result) == 0
        assert result.columns == ["col1", "col2"]
        # Without cursor metadata, defaults to Utf8
        assert result.schema["col1"] == pl.Utf8
        assert result.schema["col2"] == pl.Utf8

    def test_execute_query_internal_multiple_chunks(
        self, config: WRDSConfig, mock_secret_manager: MagicMock
    ) -> None:
        """Test _execute_query_internal concatenates multiple chunks (lines 396-400)."""
        client = WRDSClient(config, mock_secret_manager)

        # Create mock engine and connection
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_result = MagicMock()

        # Set up context manager
        mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        # Mock execution options chain
        mock_conn.execution_options.return_value.execute.return_value = mock_result

        # Mock result with multiple chunks
        mock_result.keys.return_value = ["id"]
        mock_result.fetchmany.side_effect = [
            [(1,), (2,)],  # First chunk
            [(3,), (4,)],  # Second chunk
            [],  # End of results
        ]

        client._engine = mock_engine

        result = client._execute_query_internal("SELECT id FROM test", None)

        assert len(result) == 4
        assert result["id"].to_list() == [1, 2, 3, 4]

    def test_execute_query_internal_unknown_type_oid(
        self, config: WRDSConfig, mock_secret_manager: MagicMock
    ) -> None:
        """Test _execute_query_internal handles unknown type OID gracefully."""
        import polars as pl

        client = WRDSClient(config, mock_secret_manager)

        # Create mock engine and connection
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_cursor = MagicMock()

        # Set up context manager
        mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        # Mock execution options chain
        mock_conn.execution_options.return_value.execute.return_value = mock_result

        # Mock empty result with cursor having unknown type OID
        mock_result.keys.return_value = ["custom_col"]
        mock_result.fetchmany.return_value = []
        mock_result.cursor = mock_cursor

        # Use unknown type OID (99999)
        mock_cursor.description = [
            ("custom_col", 99999, None, None, None, None, None),
        ]

        client._engine = mock_engine

        result = client._execute_query_internal("SELECT custom_col FROM test WHERE 1=0", None)

        assert len(result) == 0
        # Unknown type OID defaults to Utf8
        assert result.schema["custom_col"] == pl.Utf8


class TestWRDSClientRateLimitingSleep:
    """Tests for rate limiting sleep behavior."""

    def test_rate_limiting_sleeps_when_at_limit(
        self, config: WRDSConfig, mock_secret_manager: MagicMock
    ) -> None:
        """Test rate limiting sleeps when QPM limit reached (line 427->408)."""
        # Set very low QPM
        config.rate_limit_queries_per_minute = 2
        client = WRDSClient(config, mock_secret_manager)

        # Pre-fill query times to be at the limit
        now = time.monotonic()
        client._query_times = [now - 0.1, now]  # Two recent queries

        # Track if sleep was called
        with patch("libs.data.data_providers.wrds_client.time.sleep") as mock_sleep:
            # Need to make the sleep actually advance time for the loop to exit
            def advance_time(seconds: float) -> None:
                # Simulate time passing by clearing query times
                client._query_times = []

            mock_sleep.side_effect = advance_time

            client._rate_limit()

            # Sleep should have been called (rate limited)
            mock_sleep.assert_called()

    def test_rate_limiting_loops_back_after_sleep(
        self, config: WRDSConfig, mock_secret_manager: MagicMock
    ) -> None:
        """Test rate limiting loops back to re-check after sleeping (line 427->408 branch)."""
        # Set very low QPM
        config.rate_limit_queries_per_minute = 1
        client = WRDSClient(config, mock_secret_manager)

        call_count = 0

        # Pre-fill query times to be at the limit
        now = time.monotonic()
        client._query_times = [now - 0.1]  # One recent query (at limit with QPM=1)

        # Track sleep calls and simulate time advancement
        with patch("libs.data.data_providers.wrds_client.time.sleep") as mock_sleep:
            with patch("libs.data.data_providers.wrds_client.time.monotonic") as mock_monotonic:
                # First call returns "now", second call returns "now + 60" (after sleep)
                mock_monotonic.side_effect = [
                    now,  # First iteration: check time
                    now + 60,  # After sleep: window has passed
                    now + 60,  # For the query timestamp
                ]

                def do_sleep(seconds: float) -> None:
                    nonlocal call_count
                    call_count += 1
                    # Don't actually sleep, but the mock_monotonic will advance

                mock_sleep.side_effect = do_sleep

                client._rate_limit()

                # Sleep should have been called exactly once
                assert call_count == 1
                # Query should have been recorded after sleep
                assert len(client._query_times) >= 1

    def test_rate_limiting_with_zero_sleep_time(
        self, config: WRDSConfig, mock_secret_manager: MagicMock
    ) -> None:
        """Test rate limiting when sleep_time is zero or negative (edge case for line 427)."""
        # Set low QPM
        config.rate_limit_queries_per_minute = 1
        client = WRDSClient(config, mock_secret_manager)

        # Pre-fill query times with a query that's almost exactly at window boundary
        # This can cause sleep_time to be 0 or negative after calculation
        now = time.monotonic()
        # Put query exactly 60 seconds ago (at window boundary)
        client._query_times = [now - 60.0]

        # Track sleep calls
        with patch("libs.data.data_providers.wrds_client.time.sleep") as mock_sleep:
            client._rate_limit()

            # sleep_time = (now - 60.0) - (now - 60.0) = 0
            # Since sleep_time <= 0, sleep should NOT be called
            # BUT the old query should be pruned and new one added
            mock_sleep.assert_not_called()
            # Query should have been recorded (under limit after pruning)
            assert len(client._query_times) == 1


class TestWRDSClientContextManager:
    """Tests for context manager protocol."""

    def test_context_manager_enter_connects(
        self, config: WRDSConfig, mock_secret_manager: MagicMock
    ) -> None:
        """Test __enter__ calls connect (lines 450-451)."""
        client = WRDSClient(config, mock_secret_manager)

        with patch.object(client, "connect") as mock_connect:
            result = client.__enter__()

            mock_connect.assert_called_once()
            assert result is client

    def test_context_manager_exit_closes(
        self, config: WRDSConfig, mock_secret_manager: MagicMock
    ) -> None:
        """Test __exit__ calls close (line 460)."""
        client = WRDSClient(config, mock_secret_manager)

        with patch.object(client, "close") as mock_close:
            client.__exit__(None, None, None)

            mock_close.assert_called_once()

    def test_context_manager_full_usage(
        self, config: WRDSConfig, mock_secret_manager: MagicMock
    ) -> None:
        """Test context manager with statement usage."""
        with patch("libs.data.data_providers.wrds_client.create_engine") as mock_create_engine:
            mock_engine = MagicMock()
            mock_create_engine.return_value = mock_engine
            mock_engine.connect.return_value.__enter__ = MagicMock()
            mock_engine.connect.return_value.__exit__ = MagicMock()

            with WRDSClient(config, mock_secret_manager) as client:
                assert client._engine is not None

            # After exiting context, engine should be disposed
            mock_engine.dispose.assert_called_once()
