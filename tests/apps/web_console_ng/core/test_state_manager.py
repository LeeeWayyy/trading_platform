from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from redis.exceptions import WatchError

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


def test_trading_json_encoder_handles_naive_datetime() -> None:
    naive = datetime(2024, 1, 2, 3, 4, 5)
    payload = {"ts": naive, "d": date(2024, 1, 2), "val": Decimal("1.23")}

    encoded = json.dumps(payload, cls=TradingJSONEncoder)
    raw = json.loads(encoded)
    assert raw["ts"]["value"].endswith("Z")

    decoded = json.loads(encoded, object_hook=trading_json_object_hook)
    assert decoded["ts"].tzinfo == UTC
    assert decoded["ts"].replace(tzinfo=None) == naive
    assert decoded["d"] == date(2024, 1, 2)
    assert decoded["val"] == Decimal("1.23")


@pytest.mark.asyncio()
async def test_restore_state_corrupted_json_records_metric(mock_redis_store) -> None:
    manager = UserStateManager("user-1")
    master = AsyncMock()
    mock_redis_store.get_master.return_value = master
    master.get.return_value = "{not-json"

    with patch("apps.web_console_ng.core.metrics.record_state_save_error") as record_error:
        restored = await manager.restore_state()

    assert restored == {}
    record_error.assert_called_once_with("json_decode_error")


@pytest.mark.asyncio()
async def test_save_preferences_retries_watcherror(mock_redis_store) -> None:
    manager = UserStateManager("user-1")
    master = AsyncMock()
    mock_redis_store.get_master.return_value = master

    pipe = AsyncMock()
    master.pipeline = MagicMock(return_value=pipe)
    pipe.__aenter__.return_value = pipe
    pipe.get.return_value = json.dumps({"data": {"preferences": {"theme": "light"}}})
    pipe.execute.side_effect = [WatchError(), None]
    pipe.multi = MagicMock()
    pipe.setex = MagicMock()

    await manager.save_preferences("theme", "dark")

    assert pipe.execute.call_count == 2
    assert pipe.setex.call_count == 2


@pytest.mark.asyncio()
async def test_clear_pending_form_removes_entry(mock_redis_store) -> None:
    manager = UserStateManager("user-1")
    master = AsyncMock()
    mock_redis_store.get_master.return_value = master

    pipe = AsyncMock()
    master.pipeline = MagicMock(return_value=pipe)
    pipe.__aenter__.return_value = pipe
    pipe.get.return_value = json.dumps(
        {"data": {"pending_forms": {"form-1": {"data": {"a": 1}}}}}
    )
    pipe.multi = MagicMock()
    pipe.setex = MagicMock()

    await manager.clear_pending_form("form-1")

    saved_json = json.loads(pipe.setex.call_args[0][2])
    assert saved_json["data"]["pending_forms"] == {}


@pytest.mark.asyncio()
async def test_on_reconnect_uses_auth_context(mock_redis_store) -> None:
    manager = UserStateManager("user-1", role="admin", strategies=["strat-a"])
    master = AsyncMock()
    mock_redis_store.get_master.return_value = master

    master.get.return_value = json.dumps({"data": {"preferences": {"theme": "dark"}}})

    with patch("apps.web_console_ng.core.state_manager.AsyncTradingClient") as client_patch:
        client_instance = AsyncMock()
        client_patch.get.return_value = client_instance
        client_instance.fetch_positions.return_value = {"positions": []}
        client_instance.fetch_kill_switch_status.return_value = {"state": "DISENGAGED"}

        result = await manager.on_reconnect(ui_context=object())

    assert result["preferences"] == {"theme": "dark"}
    client_instance.fetch_positions.assert_called_once_with(
        "user-1", role="admin", strategies=["strat-a"]
    )
    client_instance.fetch_kill_switch_status.assert_called_once_with(
        "user-1", role="admin", strategies=["strat-a"]
    )
