# tests/apps/web_console_ng/test_redis_ha.py
from unittest.mock import AsyncMock, patch

import pytest

from apps.web_console_ng import config
from apps.web_console_ng.core.redis_ha import HARedisStore, SimpleRedisStore, get_redis_store


@pytest.fixture()
def mock_redis_async():
    with patch("redis.asyncio.Redis") as mock:
        yield mock


@pytest.fixture()
def mock_sentinel():
    with patch("apps.web_console_ng.core.redis_ha.Sentinel") as mock:
        yield mock


@pytest.mark.asyncio()
async def test_simple_redis_store(mock_redis_async):
    """Test SimpleRedisStore initialization and ping."""
    with patch("apps.web_console_ng.config.REDIS_USE_SENTINEL", False):
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
