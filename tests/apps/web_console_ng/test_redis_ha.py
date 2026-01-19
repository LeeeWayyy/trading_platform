# tests/apps/web_console_ng/test_redis_ha.py
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from redis.exceptions import RedisError

from apps.web_console_ng import config
from apps.web_console_ng.core.redis_ha import (
    HARedisStore,
    SimpleRedisStore,
    _build_ssl_context,
    get_redis_store,
)


@pytest.fixture()
def mock_redis_async():
    with patch("apps.web_console_ng.core.redis_ha.Redis") as mock:
        yield mock


@pytest.fixture()
def mock_sentinel():
    with patch("apps.web_console_ng.core.redis_ha.Sentinel") as mock:
        yield mock


@pytest.mark.asyncio()
async def test_simple_redis_store(mock_redis_async):
    """Test SimpleRedisStore initialization and ping."""
    with patch("apps.web_console_ng.config.REDIS_USE_SENTINEL", False):
        # Reset singleton before test
        from apps.web_console_ng.core.redis_ha import SimpleRedisStore

        SimpleRedisStore._instance = None

        store = get_redis_store()
        assert isinstance(store, SimpleRedisStore)

        # Test ping
        mock_instance = mock_redis_async.from_url.return_value
        mock_instance.ping = AsyncMock(return_value=True)
        assert await store.ping() is True


@pytest.mark.asyncio()
async def test_ha_redis_store_initialization(mock_sentinel):
    """Test HARedisStore initialization."""
    with patch("apps.web_console_ng.config.REDIS_USE_SENTINEL", True):
        # Reset singleton to ensure new init
        HARedisStore._instance = None

        store = get_redis_store()
        assert isinstance(store, HARedisStore)
        assert mock_sentinel.called

        # Check calling with config values
        args, kwargs = mock_sentinel.call_args
        assert args[0] == config.REDIS_SENTINEL_HOSTS


@pytest.mark.asyncio()
async def test_ha_redis_store_get_master(mock_sentinel):
    """Test get_master returns a master connection."""
    with patch("apps.web_console_ng.config.REDIS_USE_SENTINEL", True):
        HARedisStore._instance = None
        store = HARedisStore.get()

        mock_sentinel_instance = mock_sentinel.return_value
        mock_master = AsyncMock()
        mock_master.ping = AsyncMock(return_value=True)
        mock_sentinel_instance.master_for.return_value = mock_master

        # First call initializes master
        master = await store.get_master()
        assert master == mock_master
        mock_sentinel_instance.master_for.assert_called_with(
            config.REDIS_MASTER_NAME,
            socket_timeout=0.5,
            decode_responses=True,
            ssl=False,
            ssl_context=None,
            connection_pool_class_kwargs={"max_connections": config.REDIS_POOL_MAX_CONNECTIONS},
        )

        # Second call returns cached master
        master2 = await store.get_master()
        assert master2 == master


@pytest.mark.asyncio()
async def test_ha_redis_store_get_slave(mock_sentinel):
    """Test get_slave returns a slave connection."""
    with patch("apps.web_console_ng.config.REDIS_USE_SENTINEL", True):
        HARedisStore._instance = None
        store = HARedisStore.get()

        mock_sentinel_instance = mock_sentinel.return_value
        mock_slave = AsyncMock()
        mock_slave.ping = AsyncMock(return_value=True)
        mock_sentinel_instance.slave_for.return_value = mock_slave

        slave = await store.get_slave()
        assert slave == mock_slave
        mock_sentinel_instance.slave_for.assert_called_once()


@pytest.mark.asyncio()
async def test_ha_redis_store_slave_fallback(mock_sentinel):
    """Test get_slave falls back to master on error."""
    with patch("apps.web_console_ng.config.REDIS_USE_SENTINEL", True):
        HARedisStore._instance = None
        store = HARedisStore.get()

        mock_sentinel_instance = mock_sentinel.return_value
        mock_sentinel_instance.slave_for.side_effect = Exception("No slaves")

        mock_master = AsyncMock()
        mock_master.ping = AsyncMock(return_value=True)
        mock_sentinel_instance.master_for.return_value = mock_master

        # Should call master_for instead
        slave = await store.get_slave()
        assert slave == mock_master


def test_master_client_binary_decode_responses(mock_sentinel):
    """Ensure binary client uses decode_responses=False."""
    with patch("apps.web_console_ng.config.REDIS_USE_SENTINEL", True):
        HARedisStore._instance = None
        store = HARedisStore.get()
        mock_sentinel_instance = mock_sentinel.return_value

        store.get_master_client(decode_responses=False)
        mock_sentinel_instance.master_for.assert_called_with(
            config.REDIS_MASTER_NAME,
            socket_timeout=0.5,
            decode_responses=False,
            ssl=False,
            ssl_context=None,
            connection_pool_class_kwargs={"max_connections": config.REDIS_POOL_MAX_CONNECTIONS},
        )


# =============================================================================
# Tests for _build_ssl_context() function (lines 25-42)
# =============================================================================


def test_build_ssl_context_when_disabled():
    """Test _build_ssl_context returns None when SSL is disabled."""
    with (
        patch("apps.web_console_ng.core.redis_ha.config.REDIS_SSL_ENABLED", False),
        patch("apps.web_console_ng.core.redis_ha.config.REDIS_SENTINEL_SSL_ENABLED", False),
    ):
        result = _build_ssl_context()
        assert result is None


def test_build_ssl_context_when_enabled():
    """Test _build_ssl_context returns SSLContext when SSL is enabled."""
    with (
        patch("apps.web_console_ng.core.redis_ha.config.REDIS_SSL_ENABLED", True),
        patch("apps.web_console_ng.core.redis_ha.config.REDIS_SENTINEL_SSL_ENABLED", False),
        patch("apps.web_console_ng.core.redis_ha.config.REDIS_SSL_CA_CERTS", None),
        patch("apps.web_console_ng.core.redis_ha.config.REDIS_SSL_CERT_REQS", "required"),
        patch("apps.web_console_ng.core.redis_ha.config.REDIS_SSL_CERTFILE", None),
        patch("apps.web_console_ng.core.redis_ha.config.REDIS_SSL_KEYFILE", None),
    ):
        result = _build_ssl_context()
        assert result is not None
        import ssl

        assert isinstance(result, ssl.SSLContext)


def test_build_ssl_context_with_cert_reqs_optional():
    """Test _build_ssl_context with cert_reqs=optional."""
    import ssl

    with (
        patch("apps.web_console_ng.core.redis_ha.config.REDIS_SSL_ENABLED", True),
        patch("apps.web_console_ng.core.redis_ha.config.REDIS_SENTINEL_SSL_ENABLED", False),
        patch("apps.web_console_ng.core.redis_ha.config.REDIS_SSL_CA_CERTS", None),
        patch("apps.web_console_ng.core.redis_ha.config.REDIS_SSL_CERT_REQS", "optional"),
        patch("apps.web_console_ng.core.redis_ha.config.REDIS_SSL_CERTFILE", None),
        patch("apps.web_console_ng.core.redis_ha.config.REDIS_SSL_KEYFILE", None),
    ):
        result = _build_ssl_context()
        assert result is not None
        assert result.verify_mode == ssl.CERT_OPTIONAL
        assert result.check_hostname is False


def test_build_ssl_context_with_cert_reqs_none():
    """Test _build_ssl_context with cert_reqs=none raises ValueError.

    Note: ssl.create_default_context() creates a context with check_hostname=True.
    Setting verify_mode to CERT_NONE when check_hostname is True raises ValueError.
    This is a known limitation of the Python ssl module - check_hostname must be
    set to False before verify_mode can be set to CERT_NONE.
    """
    with (
        patch("apps.web_console_ng.core.redis_ha.config.REDIS_SSL_ENABLED", True),
        patch("apps.web_console_ng.core.redis_ha.config.REDIS_SENTINEL_SSL_ENABLED", False),
        patch("apps.web_console_ng.core.redis_ha.config.REDIS_SSL_CA_CERTS", None),
        patch("apps.web_console_ng.core.redis_ha.config.REDIS_SSL_CERT_REQS", "none"),
        patch("apps.web_console_ng.core.redis_ha.config.REDIS_SSL_CERTFILE", None),
        patch("apps.web_console_ng.core.redis_ha.config.REDIS_SSL_KEYFILE", None),
    ):
        # This raises ValueError because check_hostname is True by default
        # and cannot set verify_mode to CERT_NONE in that state
        with pytest.raises(ValueError, match="Cannot set verify_mode to CERT_NONE"):
            _build_ssl_context()


def test_build_ssl_context_with_client_cert():
    """Test _build_ssl_context with client certificate (mTLS)."""
    import tempfile

    # Create temporary cert and key files for testing
    with (
        tempfile.NamedTemporaryFile(suffix=".pem", delete=False) as cert_file,
        tempfile.NamedTemporaryFile(suffix=".key", delete=False) as key_file,
    ):
        # Write dummy PEM content (real cert not needed, just test the path)
        import os

        cert_path = cert_file.name
        key_path = key_file.name

    try:
        with (
            patch("apps.web_console_ng.core.redis_ha.config.REDIS_SSL_ENABLED", True),
            patch("apps.web_console_ng.core.redis_ha.config.REDIS_SENTINEL_SSL_ENABLED", False),
            patch("apps.web_console_ng.core.redis_ha.config.REDIS_SSL_CA_CERTS", None),
            patch("apps.web_console_ng.core.redis_ha.config.REDIS_SSL_CERT_REQS", "required"),
            patch("apps.web_console_ng.core.redis_ha.config.REDIS_SSL_CERTFILE", cert_path),
            patch("apps.web_console_ng.core.redis_ha.config.REDIS_SSL_KEYFILE", key_path),
            patch("ssl.SSLContext.load_cert_chain") as mock_load_cert_chain,
        ):
            result = _build_ssl_context()
            assert result is not None
            mock_load_cert_chain.assert_called_once_with(
                certfile=cert_path,
                keyfile=key_path,
            )
    finally:
        os.unlink(cert_path)
        os.unlink(key_path)


def test_build_ssl_context_sentinel_ssl_enabled():
    """Test _build_ssl_context returns SSLContext when Sentinel SSL is enabled."""
    with (
        patch("apps.web_console_ng.core.redis_ha.config.REDIS_SSL_ENABLED", False),
        patch("apps.web_console_ng.core.redis_ha.config.REDIS_SENTINEL_SSL_ENABLED", True),
        patch("apps.web_console_ng.core.redis_ha.config.REDIS_SSL_CA_CERTS", None),
        patch("apps.web_console_ng.core.redis_ha.config.REDIS_SSL_CERT_REQS", "required"),
        patch("apps.web_console_ng.core.redis_ha.config.REDIS_SSL_CERTFILE", None),
        patch("apps.web_console_ng.core.redis_ha.config.REDIS_SSL_KEYFILE", None),
    ):
        result = _build_ssl_context()
        assert result is not None


# =============================================================================
# Tests for HARedisStore Sentinel SSL kwargs (lines 64-65)
# =============================================================================


def test_ha_redis_store_sentinel_ssl_enabled():
    """Test HARedisStore initializes with Sentinel SSL kwargs when enabled."""
    mock_ssl_context = MagicMock()
    with (
        patch("apps.web_console_ng.core.redis_ha.config.REDIS_USE_SENTINEL", True),
        patch("apps.web_console_ng.core.redis_ha.config.REDIS_SENTINEL_SSL_ENABLED", True),
        patch("apps.web_console_ng.core.redis_ha.config.REDIS_SSL_ENABLED", True),
        patch("apps.web_console_ng.core.redis_ha.config.REDIS_SSL_CA_CERTS", None),
        patch("apps.web_console_ng.core.redis_ha.config.REDIS_SSL_CERT_REQS", "required"),
        patch("apps.web_console_ng.core.redis_ha.config.REDIS_SSL_CERTFILE", None),
        patch("apps.web_console_ng.core.redis_ha.config.REDIS_SSL_KEYFILE", None),
        patch("apps.web_console_ng.core.redis_ha._build_ssl_context", return_value=mock_ssl_context),
        patch("apps.web_console_ng.core.redis_ha.Sentinel") as mock_sentinel,
    ):
        HARedisStore._instance = None
        _ = HARedisStore()

        # Verify Sentinel was called with SSL kwargs
        args, kwargs = mock_sentinel.call_args
        assert kwargs["sentinel_kwargs"]["ssl"] is True
        assert kwargs["sentinel_kwargs"]["ssl_context"] is mock_ssl_context
        assert kwargs["ssl"] is True
        assert kwargs["ssl_context"] is mock_ssl_context


# =============================================================================
# Tests for get_slave_client binary path (lines 131-133)
# =============================================================================


def test_slave_client_binary_decode_responses(mock_sentinel):
    """Ensure binary slave client uses decode_responses=False."""
    with patch("apps.web_console_ng.config.REDIS_USE_SENTINEL", True):
        HARedisStore._instance = None
        store = HARedisStore.get()
        mock_sentinel_instance = mock_sentinel.return_value

        store.get_slave_client(decode_responses=False)
        mock_sentinel_instance.slave_for.assert_called_with(
            config.REDIS_MASTER_NAME,
            socket_timeout=0.5,
            decode_responses=False,
            ssl=False,
            ssl_context=None,
            connection_pool_class_kwargs={"max_connections": config.REDIS_POOL_MAX_CONNECTIONS},
        )

        # Second call should return cached client
        store.get_slave_client(decode_responses=False)
        # Should still only have been called once
        assert mock_sentinel_instance.slave_for.call_count == 1


# =============================================================================
# Tests for get_master reconnection (lines 147-148)
# =============================================================================


@pytest.mark.asyncio()
async def test_get_master_reconnects_on_failed_connection(mock_sentinel):
    """Test get_master rebuilds client when connection check fails."""
    with patch("apps.web_console_ng.config.REDIS_USE_SENTINEL", True):
        HARedisStore._instance = None
        store = HARedisStore.get()

        mock_sentinel_instance = mock_sentinel.return_value
        mock_master_disconnected = AsyncMock()
        mock_master_disconnected.ping = AsyncMock(side_effect=RedisError("Connection lost"))

        mock_master_new = AsyncMock()
        mock_master_new.ping = AsyncMock(return_value=True)

        # First call returns disconnected master, second returns new master
        mock_sentinel_instance.master_for.side_effect = [
            mock_master_disconnected,
            mock_master_new,
        ]

        master = await store.get_master()
        assert master == mock_master_new
        assert mock_sentinel_instance.master_for.call_count == 2


# =============================================================================
# Tests for get_slave fallback with RedisError (lines 160-166)
# =============================================================================


@pytest.mark.asyncio()
async def test_get_slave_fallback_on_redis_error(mock_sentinel):
    """Test get_slave falls back to master on RedisError from get_slave_client."""
    with patch("apps.web_console_ng.config.REDIS_USE_SENTINEL", True):
        HARedisStore._instance = None
        store = HARedisStore.get()

        mock_sentinel_instance = mock_sentinel.return_value

        # slave_for raises RedisError
        mock_sentinel_instance.slave_for.side_effect = RedisError("No replicas available")

        mock_master = AsyncMock()
        mock_master.ping = AsyncMock(return_value=True)
        mock_sentinel_instance.master_for.return_value = mock_master

        slave = await store.get_slave()
        assert slave == mock_master


@pytest.mark.asyncio()
async def test_get_slave_fallback_on_os_error(mock_sentinel):
    """Test get_slave falls back to master on OSError from get_slave_client."""
    with patch("apps.web_console_ng.config.REDIS_USE_SENTINEL", True):
        HARedisStore._instance = None
        store = HARedisStore.get()

        mock_sentinel_instance = mock_sentinel.return_value

        # slave_for raises OSError
        mock_sentinel_instance.slave_for.side_effect = OSError("Network unreachable")

        mock_master = AsyncMock()
        mock_master.ping = AsyncMock(return_value=True)
        mock_sentinel_instance.master_for.return_value = mock_master

        slave = await store.get_slave()
        assert slave == mock_master


@pytest.mark.asyncio()
async def test_get_slave_fallback_on_connection_error(mock_sentinel):
    """Test get_slave falls back to master on ConnectionError from get_slave_client."""
    with patch("apps.web_console_ng.config.REDIS_USE_SENTINEL", True):
        HARedisStore._instance = None
        store = HARedisStore.get()

        mock_sentinel_instance = mock_sentinel.return_value

        # slave_for raises ConnectionError
        mock_sentinel_instance.slave_for.side_effect = ConnectionError("Connection refused")

        mock_master = AsyncMock()
        mock_master.ping = AsyncMock(return_value=True)
        mock_sentinel_instance.master_for.return_value = mock_master

        slave = await store.get_slave()
        assert slave == mock_master


# =============================================================================
# Tests for slave reconnection and rebuild fallback (lines 179-188)
# =============================================================================


@pytest.mark.asyncio()
async def test_get_slave_reconnects_on_failed_connection(mock_sentinel):
    """Test get_slave rebuilds client when connection check fails."""
    with patch("apps.web_console_ng.config.REDIS_USE_SENTINEL", True):
        HARedisStore._instance = None
        store = HARedisStore.get()

        mock_sentinel_instance = mock_sentinel.return_value

        mock_slave_disconnected = AsyncMock()
        mock_slave_disconnected.ping = AsyncMock(side_effect=RedisError("Connection lost"))

        mock_slave_new = AsyncMock()
        mock_slave_new.ping = AsyncMock(return_value=True)

        # First call returns disconnected slave, second returns new slave
        mock_sentinel_instance.slave_for.side_effect = [
            mock_slave_disconnected,
            mock_slave_new,
        ]

        slave = await store.get_slave()
        assert slave == mock_slave_new
        assert mock_sentinel_instance.slave_for.call_count == 2


@pytest.mark.asyncio()
async def test_get_slave_rebuild_fallback_to_master(mock_sentinel):
    """Test get_slave falls back to master when slave rebuild fails."""
    with patch("apps.web_console_ng.config.REDIS_USE_SENTINEL", True):
        HARedisStore._instance = None
        store = HARedisStore.get()

        mock_sentinel_instance = mock_sentinel.return_value

        mock_slave_disconnected = AsyncMock()
        mock_slave_disconnected.ping = AsyncMock(side_effect=RedisError("Connection lost"))

        # First call returns disconnected slave, second raises error
        mock_sentinel_instance.slave_for.side_effect = [
            mock_slave_disconnected,
            RedisError("Cannot rebuild slave"),
        ]

        mock_master = AsyncMock()
        mock_master.ping = AsyncMock(return_value=True)
        mock_sentinel_instance.master_for.return_value = mock_master

        slave = await store.get_slave()
        assert slave == mock_master


# =============================================================================
# Tests for _is_connected returning False (lines 196-197)
# =============================================================================


@pytest.mark.asyncio()
async def test_is_connected_returns_false_on_timeout(mock_sentinel):
    """Test _is_connected returns False on TimeoutError."""

    with patch("apps.web_console_ng.config.REDIS_USE_SENTINEL", True):
        HARedisStore._instance = None
        store = HARedisStore.get()

        mock_conn = AsyncMock()
        mock_conn.ping = AsyncMock(side_effect=TimeoutError())

        result = await store._is_connected(mock_conn)
        assert result is False


@pytest.mark.asyncio()
async def test_is_connected_returns_false_on_os_error(mock_sentinel):
    """Test _is_connected returns False on OSError."""
    with patch("apps.web_console_ng.config.REDIS_USE_SENTINEL", True):
        HARedisStore._instance = None
        store = HARedisStore.get()

        mock_conn = AsyncMock()
        mock_conn.ping = AsyncMock(side_effect=OSError("Network error"))

        result = await store._is_connected(mock_conn)
        assert result is False


# =============================================================================
# Tests for ping() method (lines 201-202)
# =============================================================================


@pytest.mark.asyncio()
async def test_ha_redis_store_ping(mock_sentinel):
    """Test HARedisStore ping() method."""
    with patch("apps.web_console_ng.config.REDIS_USE_SENTINEL", True):
        HARedisStore._instance = None
        store = HARedisStore.get()

        mock_sentinel_instance = mock_sentinel.return_value
        mock_master = AsyncMock()
        mock_master.ping = AsyncMock(return_value=True)
        mock_sentinel_instance.master_for.return_value = mock_master

        result = await store.ping()
        assert result is True
        # ping is called twice: once in get_master -> _is_connected, once in ping()
        assert mock_master.ping.call_count == 2


# =============================================================================
# Tests for close() method with error handling (lines 211-226)
# =============================================================================


@pytest.mark.asyncio()
async def test_ha_redis_store_close_all_clients(mock_sentinel):
    """Test HARedisStore close() closes all clients."""
    with patch("apps.web_console_ng.config.REDIS_USE_SENTINEL", True):
        HARedisStore._instance = None
        store = HARedisStore.get()

        # Set up mock clients
        mock_master_text = AsyncMock()
        mock_master_binary = AsyncMock()
        mock_slave_text = AsyncMock()
        mock_slave_binary = AsyncMock()

        store._master_text = mock_master_text
        store._master_binary = mock_master_binary
        store._slave_text = mock_slave_text
        store._slave_binary = mock_slave_binary

        await store.close()

        # Verify all clients were closed
        mock_master_text.aclose.assert_called_once()
        mock_master_binary.aclose.assert_called_once()
        mock_slave_text.aclose.assert_called_once()
        mock_slave_binary.aclose.assert_called_once()

        # Verify all references are cleared
        assert store._master_text is None
        assert store._master_binary is None
        assert store._slave_text is None
        assert store._slave_binary is None


@pytest.mark.asyncio()
async def test_ha_redis_store_close_handles_errors(mock_sentinel):
    """Test HARedisStore close() handles errors gracefully."""
    with patch("apps.web_console_ng.config.REDIS_USE_SENTINEL", True):
        HARedisStore._instance = None
        store = HARedisStore.get()

        # Set up mock clients with some raising errors
        mock_master_text = AsyncMock()
        mock_master_text.aclose = AsyncMock(side_effect=RedisError("Close failed"))
        mock_master_binary = AsyncMock()
        mock_master_binary.aclose = AsyncMock(side_effect=OSError("OS error"))

        store._master_text = mock_master_text
        store._master_binary = mock_master_binary

        # Should not raise despite errors
        await store.close()

        # Verify references are still cleared
        assert store._master_text is None
        assert store._master_binary is None


@pytest.mark.asyncio()
async def test_ha_redis_store_close_with_none_clients(mock_sentinel):
    """Test HARedisStore close() handles None clients gracefully."""
    with patch("apps.web_console_ng.config.REDIS_USE_SENTINEL", True):
        HARedisStore._instance = None
        store = HARedisStore.get()

        # All clients are None by default
        assert store._master_text is None
        assert store._master_binary is None

        # Should not raise
        await store.close()


# =============================================================================
# Tests for SimpleRedisStore SSL configuration (lines 245-272)
# =============================================================================


@pytest.mark.asyncio()
async def test_simple_redis_store_with_ssl_enabled(mock_redis_async):
    """Test SimpleRedisStore initializes with SSL when enabled."""

    with (
        patch("apps.web_console_ng.config.REDIS_USE_SENTINEL", False),
        patch("apps.web_console_ng.core.redis_ha.config.REDIS_SSL_ENABLED", True),
        patch("apps.web_console_ng.core.redis_ha.config.REDIS_SSL_CERT_REQS", "required"),
        patch("apps.web_console_ng.core.redis_ha.config.REDIS_SSL_CA_CERTS", None),
        patch("apps.web_console_ng.core.redis_ha.config.REDIS_SSL_CERTFILE", None),
        patch("apps.web_console_ng.core.redis_ha.config.REDIS_SSL_KEYFILE", None),
    ):
        SimpleRedisStore._instance = None
        _ = SimpleRedisStore()

        # Verify from_url was called with SSL kwargs
        calls = mock_redis_async.from_url.call_args_list
        assert len(calls) == 2  # text and binary
        # Check that ssl=True is in kwargs
        for call in calls:
            _, kwargs = call
            assert kwargs["ssl"] is True
            assert "ssl_context" in kwargs


@pytest.mark.asyncio()
async def test_simple_redis_store_with_ssl_optional(mock_redis_async):
    """Test SimpleRedisStore with SSL cert_reqs=optional."""
    import ssl

    with (
        patch("apps.web_console_ng.config.REDIS_USE_SENTINEL", False),
        patch("apps.web_console_ng.core.redis_ha.config.REDIS_SSL_ENABLED", True),
        patch("apps.web_console_ng.core.redis_ha.config.REDIS_SSL_CERT_REQS", "optional"),
        patch("apps.web_console_ng.core.redis_ha.config.REDIS_SSL_CA_CERTS", None),
        patch("apps.web_console_ng.core.redis_ha.config.REDIS_SSL_CERTFILE", None),
        patch("apps.web_console_ng.core.redis_ha.config.REDIS_SSL_KEYFILE", None),
    ):
        SimpleRedisStore._instance = None
        _ = SimpleRedisStore()

        calls = mock_redis_async.from_url.call_args_list
        for call in calls:
            _, kwargs = call
            assert kwargs["ssl_context"].verify_mode == ssl.CERT_OPTIONAL


@pytest.mark.asyncio()
async def test_simple_redis_store_with_ssl_none_raises_error(mock_redis_async):
    """Test SimpleRedisStore with SSL cert_reqs=none raises ValueError.

    Note: ssl.create_default_context() creates a context with check_hostname=True.
    Setting verify_mode to CERT_NONE when check_hostname is True raises ValueError.
    """
    with (
        patch("apps.web_console_ng.config.REDIS_USE_SENTINEL", False),
        patch("apps.web_console_ng.core.redis_ha.config.REDIS_SSL_ENABLED", True),
        patch("apps.web_console_ng.core.redis_ha.config.REDIS_SSL_CERT_REQS", "none"),
        patch("apps.web_console_ng.core.redis_ha.config.REDIS_SSL_CA_CERTS", None),
        patch("apps.web_console_ng.core.redis_ha.config.REDIS_SSL_CERTFILE", None),
        patch("apps.web_console_ng.core.redis_ha.config.REDIS_SSL_KEYFILE", None),
    ):
        SimpleRedisStore._instance = None
        # This raises ValueError because check_hostname is True by default
        with pytest.raises(ValueError, match="Cannot set verify_mode to CERT_NONE"):
            SimpleRedisStore()


@pytest.mark.asyncio()
async def test_simple_redis_store_with_ca_certs(mock_redis_async):
    """Test SimpleRedisStore with CA certificates."""
    import os
    import tempfile

    # Create a temporary CA cert file
    with tempfile.NamedTemporaryFile(suffix=".pem", delete=False) as ca_file:
        ca_path = ca_file.name

    try:
        with (
            patch("apps.web_console_ng.config.REDIS_USE_SENTINEL", False),
            patch("apps.web_console_ng.core.redis_ha.config.REDIS_SSL_ENABLED", True),
            patch("apps.web_console_ng.core.redis_ha.config.REDIS_SSL_CERT_REQS", "required"),
            patch("apps.web_console_ng.core.redis_ha.config.REDIS_SSL_CA_CERTS", ca_path),
            patch("apps.web_console_ng.core.redis_ha.config.REDIS_SSL_CERTFILE", None),
            patch("apps.web_console_ng.core.redis_ha.config.REDIS_SSL_KEYFILE", None),
            patch("ssl.SSLContext.load_verify_locations") as mock_load_locations,
        ):
            SimpleRedisStore._instance = None
            _ = SimpleRedisStore()

            mock_load_locations.assert_called_with(ca_path)
    finally:
        os.unlink(ca_path)


@pytest.mark.asyncio()
async def test_simple_redis_store_with_client_cert(mock_redis_async):
    """Test SimpleRedisStore with client certificate (mTLS)."""
    import os
    import tempfile

    # Create temporary cert and key files
    with (
        tempfile.NamedTemporaryFile(suffix=".pem", delete=False) as cert_file,
        tempfile.NamedTemporaryFile(suffix=".key", delete=False) as key_file,
    ):
        cert_path = cert_file.name
        key_path = key_file.name

    try:
        with (
            patch("apps.web_console_ng.config.REDIS_USE_SENTINEL", False),
            patch("apps.web_console_ng.core.redis_ha.config.REDIS_SSL_ENABLED", True),
            patch("apps.web_console_ng.core.redis_ha.config.REDIS_SSL_CERT_REQS", "required"),
            patch("apps.web_console_ng.core.redis_ha.config.REDIS_SSL_CA_CERTS", None),
            patch("apps.web_console_ng.core.redis_ha.config.REDIS_SSL_CERTFILE", cert_path),
            patch("apps.web_console_ng.core.redis_ha.config.REDIS_SSL_KEYFILE", key_path),
            patch("ssl.SSLContext.load_cert_chain") as mock_load_cert_chain,
        ):
            SimpleRedisStore._instance = None
            _ = SimpleRedisStore()

            mock_load_cert_chain.assert_called_with(
                certfile=cert_path,
                keyfile=key_path,
            )
    finally:
        os.unlink(cert_path)
        os.unlink(key_path)


# =============================================================================
# Tests for SimpleRedisStore get methods (lines 284, 287, 290, 293)
# =============================================================================


@pytest.mark.asyncio()
async def test_simple_redis_store_get_master(mock_redis_async):
    """Test SimpleRedisStore get_master() returns redis client."""
    with patch("apps.web_console_ng.config.REDIS_USE_SENTINEL", False):
        SimpleRedisStore._instance = None
        store = SimpleRedisStore()

        master = await store.get_master()
        assert master == store.redis


@pytest.mark.asyncio()
async def test_simple_redis_store_get_slave(mock_redis_async):
    """Test SimpleRedisStore get_slave() returns redis client."""
    with patch("apps.web_console_ng.config.REDIS_USE_SENTINEL", False):
        SimpleRedisStore._instance = None
        store = SimpleRedisStore()

        slave = await store.get_slave()
        assert slave == store.redis


def test_simple_redis_store_get_master_client_text(mock_redis_async):
    """Test SimpleRedisStore get_master_client with decode_responses=True."""
    with patch("apps.web_console_ng.config.REDIS_USE_SENTINEL", False):
        SimpleRedisStore._instance = None
        store = SimpleRedisStore()

        client = store.get_master_client(decode_responses=True)
        assert client == store.redis


def test_simple_redis_store_get_master_client_binary(mock_redis_async):
    """Test SimpleRedisStore get_master_client with decode_responses=False."""
    with patch("apps.web_console_ng.config.REDIS_USE_SENTINEL", False):
        SimpleRedisStore._instance = None
        store = SimpleRedisStore()

        client = store.get_master_client(decode_responses=False)
        assert client == store.redis_binary


def test_simple_redis_store_get_slave_client_text(mock_redis_async):
    """Test SimpleRedisStore get_slave_client with decode_responses=True."""
    with patch("apps.web_console_ng.config.REDIS_USE_SENTINEL", False):
        SimpleRedisStore._instance = None
        store = SimpleRedisStore()

        client = store.get_slave_client(decode_responses=True)
        assert client == store.redis


def test_simple_redis_store_get_slave_client_binary(mock_redis_async):
    """Test SimpleRedisStore get_slave_client with decode_responses=False."""
    with patch("apps.web_console_ng.config.REDIS_USE_SENTINEL", False):
        SimpleRedisStore._instance = None
        store = SimpleRedisStore()

        client = store.get_slave_client(decode_responses=False)
        assert client == store.redis_binary


# =============================================================================
# Tests for SimpleRedisStore close() method (lines 300-307)
# =============================================================================


@pytest.mark.asyncio()
async def test_simple_redis_store_close(mock_redis_async):
    """Test SimpleRedisStore close() closes both clients."""
    with patch("apps.web_console_ng.config.REDIS_USE_SENTINEL", False):
        SimpleRedisStore._instance = None

        mock_instance = mock_redis_async.from_url.return_value
        mock_instance.aclose = AsyncMock()

        store = SimpleRedisStore()
        await store.close()

        # aclose should have been called twice (redis and redis_binary)
        assert mock_instance.aclose.call_count == 2


@pytest.mark.asyncio()
async def test_simple_redis_store_close_handles_errors(mock_redis_async):
    """Test SimpleRedisStore close() handles errors gracefully."""
    with patch("apps.web_console_ng.config.REDIS_USE_SENTINEL", False):
        SimpleRedisStore._instance = None

        mock_redis = AsyncMock()
        mock_redis.aclose = AsyncMock(side_effect=RedisError("Close failed"))
        mock_redis_binary = AsyncMock()
        mock_redis_binary.aclose = AsyncMock(side_effect=OSError("OS error"))

        mock_redis_async.from_url.side_effect = [mock_redis, mock_redis_binary]

        store = SimpleRedisStore()

        # Should not raise despite errors
        await store.close()


# =============================================================================
# Tests for get_redis_store factory function
# =============================================================================


def test_get_redis_store_returns_ha_store_when_sentinel_enabled(mock_sentinel):
    """Test get_redis_store returns HARedisStore when Sentinel is enabled."""
    with patch("apps.web_console_ng.core.redis_ha.config.REDIS_USE_SENTINEL", True):
        HARedisStore._instance = None
        store = get_redis_store()
        assert isinstance(store, HARedisStore)


def test_get_redis_store_returns_simple_store_when_sentinel_disabled(mock_redis_async):
    """Test get_redis_store returns SimpleRedisStore when Sentinel is disabled."""
    with patch("apps.web_console_ng.core.redis_ha.config.REDIS_USE_SENTINEL", False):
        SimpleRedisStore._instance = None
        store = get_redis_store()
        assert isinstance(store, SimpleRedisStore)


# =============================================================================
# Tests for singleton caching (branch coverage)
# =============================================================================


def test_ha_redis_store_singleton_returns_cached_instance(mock_sentinel):
    """Test HARedisStore.get() returns cached instance on second call."""
    with patch("apps.web_console_ng.config.REDIS_USE_SENTINEL", True):
        HARedisStore._instance = None
        store1 = HARedisStore.get()
        store2 = HARedisStore.get()
        assert store1 is store2


def test_simple_redis_store_singleton_returns_cached_instance(mock_redis_async):
    """Test SimpleRedisStore.get() returns cached instance on second call."""
    with patch("apps.web_console_ng.config.REDIS_USE_SENTINEL", False):
        SimpleRedisStore._instance = None
        store1 = SimpleRedisStore.get()
        store2 = SimpleRedisStore.get()
        assert store1 is store2


def test_ha_redis_store_master_binary_cached(mock_sentinel):
    """Test get_master_client returns cached binary master on second call."""
    with patch("apps.web_console_ng.config.REDIS_USE_SENTINEL", True):
        HARedisStore._instance = None
        store = HARedisStore.get()
        mock_sentinel_instance = mock_sentinel.return_value

        # First call creates binary master
        client1 = store.get_master_client(decode_responses=False)
        # Second call returns cached binary master
        client2 = store.get_master_client(decode_responses=False)

        assert client1 is client2
        # master_for should only be called once for binary
        assert mock_sentinel_instance.master_for.call_count == 1


def test_ha_redis_store_slave_text_cached(mock_sentinel):
    """Test get_slave_client returns cached text slave on second call."""
    with patch("apps.web_console_ng.config.REDIS_USE_SENTINEL", True):
        HARedisStore._instance = None
        store = HARedisStore.get()
        mock_sentinel_instance = mock_sentinel.return_value

        # First call creates text slave
        client1 = store.get_slave_client(decode_responses=True)
        # Second call returns cached text slave
        client2 = store.get_slave_client(decode_responses=True)

        assert client1 is client2
        # slave_for should only be called once for text
        assert mock_sentinel_instance.slave_for.call_count == 1
