# tests/apps/web_console_ng/test_state_manager.py
import hashlib
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.web_console_ng.core.state_manager import (
    TradingJSONEncoder,
    UserStateManager,
    trading_json_object_hook,
)


@pytest.fixture()
def mock_redis_store():
    with patch("apps.web_console_ng.core.state_manager.get_redis_store") as mock:
        mock_instance = AsyncMock()
        mock.return_value = mock_instance
        yield mock_instance


@pytest.fixture()
def mock_trading_client():
    with patch("apps.web_console_ng.core.state_manager.AsyncTradingClient") as mock:
        # Ensure .get() is synchronous
        mock.get = MagicMock()
        mock_instance = AsyncMock()
        mock.get.return_value = mock_instance
        yield mock


def test_json_encoder_decoder():
    """Test custom JSON encoding/decoding."""
    now = datetime.now(UTC)
    today = date.today()
    dec = Decimal("10.50")

    data = {"ts": now, "d": today, "val": dec}

    encoded = json.dumps(data, cls=TradingJSONEncoder)
    decoded = json.loads(encoded, object_hook=trading_json_object_hook)

    assert decoded["ts"] == now
    assert decoded["d"] == today
    assert decoded["val"] == dec
    assert isinstance(decoded["val"], Decimal)


@pytest.mark.asyncio()
async def test_save_critical_state(mock_redis_store):
    """Test saving state to Redis."""
    manager = UserStateManager("user1")
    master_mock = AsyncMock()
    mock_redis_store.get_master.return_value = master_mock

    state = {"preferences": {"theme": "dark"}}
    await manager.save_critical_state(state)

    master_mock.setex.assert_called_once()
    args = master_mock.setex.call_args[0]
    assert args[0] == "user_state:user1"
    assert args[1] == 86400
    saved_data = json.loads(args[2])
    assert saved_data["data"] == state
    assert "saved_at" in saved_data


@pytest.mark.asyncio()
async def test_restore_state(mock_redis_store):
    """Test restoring state from Redis."""
    manager = UserStateManager("user1")
    master_mock = AsyncMock()
    mock_redis_store.get_master.return_value = master_mock

    state = {"preferences": {"theme": "dark"}}
    state_with_meta = {
        "data": state,
        "saved_at": datetime.now(UTC).isoformat(),
        "version": 1,
    }
    master_mock.get.return_value = json.dumps(state_with_meta)

    restored = await manager.restore_state()
    assert restored == state


@pytest.mark.asyncio()
async def test_save_preferences_atomic(mock_redis_store):
    """Test atomic preference update using WATCH/MULTI/EXEC."""
    manager = UserStateManager("user1")
    master_mock = AsyncMock()
    mock_redis_store.get_master.return_value = master_mock

    # Mock pipeline - it must be synchronous but return an async context manager
    pipeline_mock = AsyncMock()
    master_mock.pipeline = MagicMock(return_value=pipeline_mock)
    pipeline_mock.__aenter__.return_value = pipeline_mock

    # Mock initial state
    pipeline_mock.get.return_value = json.dumps({"data": {"preferences": {"theme": "light"}}})

    await manager.save_preferences("theme", "dark")

    pipeline_mock.watch.assert_called_with("user_state:user1")
    pipeline_mock.multi.assert_called_once()
    pipeline_mock.execute.assert_called_once()

    # Verify setex called on pipeline
    pipeline_mock.setex.assert_called_once()
    args = pipeline_mock.setex.call_args[0]
    saved_json = json.loads(args[2])
    assert saved_json["data"]["preferences"]["theme"] == "dark"


@pytest.mark.asyncio()
async def test_save_pending_form_hashes_data(mock_redis_store):
    """Test pending form hashing and client_order_id persistence."""
    manager = UserStateManager("user1")
    master_mock = AsyncMock()
    mock_redis_store.get_master.return_value = master_mock

    pipeline_mock = AsyncMock()
    master_mock.pipeline = MagicMock(return_value=pipeline_mock)
    pipeline_mock.__aenter__.return_value = pipeline_mock
    pipeline_mock.get.return_value = json.dumps({"data": {}})

    form_data = {"symbol": "AAPL", "qty": 10}
    await manager.save_pending_form("order_form", form_data, client_order_id="cid-1")

    args = pipeline_mock.setex.call_args[0]
    saved_json = json.loads(args[2])
    pending = saved_json["data"]["pending_forms"]["order_form"]
    assert pending["client_order_id"] == "cid-1"
    expected_hash = hashlib.sha256(
        json.dumps(form_data, sort_keys=True).encode("utf-8")
    ).hexdigest()
    assert pending["original_data_hash"] == expected_hash


@pytest.mark.asyncio()
async def test_on_reconnect(mock_redis_store, mock_trading_client):
    """Test full reconnection flow."""
    manager = UserStateManager("user1")
    master_mock = AsyncMock()
    mock_redis_store.get_master.return_value = master_mock

    # Mock stored state
    stored_state = {"data": {"preferences": {"theme": "dark"}, "filters": {"symbol": "AAPL"}}}
    master_mock.get.return_value = json.dumps(stored_state)

    # Mock API returns
    # mock_trading_client is the class/patch object.
    # mock_trading_client.get() returns the singleton instance (AsyncMock).
    client_instance = mock_trading_client.get.return_value

    # IMPORTANT: Configure return_value of the AsyncMock methods
    client_instance.fetch_positions.return_value = ["pos1"]
    client_instance.fetch_kill_switch_status.return_value = {"state": "DISENGAGED"}

    # Execute
    ui_context = MagicMock()
    result = await manager.on_reconnect(ui_context)

    assert result["preferences"] == {"theme": "dark"}
    assert result["filters"] == {"symbol": "AAPL"}
    assert result["api_data"]["positions"] == ["pos1"]
    assert result["api_data"]["kill_switch"] == {"state": "DISENGAGED"}
