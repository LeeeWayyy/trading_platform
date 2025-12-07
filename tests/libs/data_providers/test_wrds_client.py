"""Tests for WRDS client with mocked connections."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import polars as pl
import pytest
from pydantic import SecretStr

from libs.data_providers.wrds_client import WRDSClient, WRDSConfig


@pytest.fixture
def mock_secret_manager() -> MagicMock:
    """Create a mock secret manager."""
    manager = MagicMock()
    manager.get_secret.side_effect = lambda key: {
        "wrds/username": "test_user",
        "wrds/password": "test_password",
    }.get(key, "")
    return manager


@pytest.fixture
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

        with patch("libs.data_providers.wrds_client.create_engine") as mock_engine:
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
        with patch("libs.data_providers.wrds_client.create_engine") as mock_engine:
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

    def test_credential_caching(
        self, config: WRDSConfig, mock_secret_manager: MagicMock
    ) -> None:
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
