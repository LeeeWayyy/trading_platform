from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from apps.web_console_ng.core import workspace_persistence
from apps.web_console_ng.core.workspace_persistence import (
    DatabaseUnavailableError,
    WorkspacePersistenceService,
    get_workspace_service,
)


class AsyncContext:
    def __init__(self, obj: Any) -> None:
        self._obj = obj

    async def __aenter__(self) -> Any:
        return self._obj

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


def _make_pool(cursor: AsyncMock) -> Mock:
    """Create a mock pool that properly simulates psycopg3 AsyncConnectionPool.

    psycopg3's AsyncConnectionPool.connection() is a synchronous call that returns
    an async context manager, NOT a coroutine. So we use Mock (not AsyncMock) for
    the pool and connection attributes to avoid them auto-returning coroutines.
    """
    conn = Mock()
    conn.cursor.return_value = AsyncContext(cursor)
    conn.commit = AsyncMock()  # commit() is async
    pool = Mock()
    pool.connection.return_value = AsyncContext(conn)
    return pool


@pytest.fixture()
def service() -> WorkspacePersistenceService:
    return WorkspacePersistenceService()


@pytest.mark.asyncio()
async def test_save_grid_state_requires_db(
    service: WorkspacePersistenceService, monkeypatch
) -> None:
    monkeypatch.setattr(workspace_persistence, "get_db_pool", Mock(return_value=None))

    with pytest.raises(DatabaseUnavailableError):
        await service.save_grid_state("user", "grid", {"a": 1})


@pytest.mark.asyncio()
async def test_save_grid_state_too_large(service: WorkspacePersistenceService, monkeypatch) -> None:
    pool_getter = Mock()
    monkeypatch.setattr(workspace_persistence, "get_db_pool", pool_getter)
    payload = {"value": "x" * (workspace_persistence.MAX_STATE_SIZE + 1)}

    saved = await service.save_grid_state("user", "grid", payload)

    assert saved is False
    pool_getter.assert_not_called()


@pytest.mark.asyncio()
async def test_save_grid_state_success(service: WorkspacePersistenceService, monkeypatch) -> None:
    cursor = AsyncMock()
    pool = _make_pool(cursor)
    monkeypatch.setattr(workspace_persistence, "get_db_pool", Mock(return_value=pool))

    state = {"columns": ["a", "b"]}
    result = await service.save_grid_state("user-1", "grid-1", state)

    assert result is True
    cursor.execute.assert_called_once()
    args, params = cursor.execute.call_args[0]
    assert "INSERT INTO workspace_state" in args
    assert params[0] == "user-1"
    assert params[1] == "grid.grid-1"
    assert json.loads(params[2]) == state
    assert params[3] == workspace_persistence.SCHEMA_VERSIONS["grid"]


@pytest.mark.asyncio()
async def test_save_panel_state_success(service: WorkspacePersistenceService, monkeypatch) -> None:
    cursor = AsyncMock()
    pool = _make_pool(cursor)
    monkeypatch.setattr(workspace_persistence, "get_db_pool", Mock(return_value=pool))

    state = {"active_tab": "working"}
    result = await service.save_panel_state("user-1", "tabbed_panel", state)

    assert result is True
    cursor.execute.assert_called_once()
    args, params = cursor.execute.call_args[0]
    assert "INSERT INTO workspace_state" in args
    assert params[0] == "user-1"
    assert params[1] == "panel.tabbed_panel"
    assert json.loads(params[2]) == state
    assert params[3] == workspace_persistence.SCHEMA_VERSIONS["panel"]


@pytest.mark.asyncio()
async def test_load_grid_state_missing(service: WorkspacePersistenceService, monkeypatch) -> None:
    cursor = AsyncMock()
    cursor.fetchone.return_value = None
    pool = _make_pool(cursor)
    monkeypatch.setattr(workspace_persistence, "get_db_pool", Mock(return_value=pool))

    assert await service.load_grid_state("user", "grid") is None


@pytest.mark.asyncio()
async def test_load_grid_state_schema_mismatch(
    service: WorkspacePersistenceService, monkeypatch
) -> None:
    cursor = AsyncMock()
    cursor.fetchone.return_value = ("{}", 999)
    pool = _make_pool(cursor)
    monkeypatch.setattr(workspace_persistence, "get_db_pool", Mock(return_value=pool))

    assert await service.load_grid_state("user", "grid") is None


@pytest.mark.asyncio()
async def test_load_grid_state_from_dict(service: WorkspacePersistenceService, monkeypatch) -> None:
    cursor = AsyncMock()
    cursor.fetchone.return_value = ({"a": 1}, workspace_persistence.SCHEMA_VERSIONS["grid"])
    pool = _make_pool(cursor)
    monkeypatch.setattr(workspace_persistence, "get_db_pool", Mock(return_value=pool))

    result = await service.load_grid_state("user", "grid")

    assert result == {"a": 1}


@pytest.mark.asyncio()
async def test_load_panel_state_from_json(
    service: WorkspacePersistenceService, monkeypatch
) -> None:
    cursor = AsyncMock()
    cursor.fetchone.return_value = (
        json.dumps({"active_tab": "positions"}),
        workspace_persistence.SCHEMA_VERSIONS["panel"],
    )
    pool = _make_pool(cursor)
    monkeypatch.setattr(workspace_persistence, "get_db_pool", Mock(return_value=pool))

    result = await service.load_panel_state("user", "tabbed_panel")

    assert result == {"active_tab": "positions"}

@pytest.mark.asyncio()
async def test_load_grid_state_from_json(service: WorkspacePersistenceService, monkeypatch) -> None:
    cursor = AsyncMock()
    cursor.fetchone.return_value = (
        json.dumps({"a": 1}),
        workspace_persistence.SCHEMA_VERSIONS["grid"],
    )
    pool = _make_pool(cursor)
    monkeypatch.setattr(workspace_persistence, "get_db_pool", Mock(return_value=pool))

    result = await service.load_grid_state("user", "grid")

    assert result == {"a": 1}


@pytest.mark.asyncio()
async def test_load_grid_state_corrupt_json(
    service: WorkspacePersistenceService, monkeypatch
) -> None:
    cursor = AsyncMock()
    cursor.fetchone.return_value = (
        "{not-json}",
        workspace_persistence.SCHEMA_VERSIONS["grid"],
    )
    pool = _make_pool(cursor)
    monkeypatch.setattr(workspace_persistence, "get_db_pool", Mock(return_value=pool))

    assert await service.load_grid_state("user", "grid") is None


@pytest.mark.asyncio()
async def test_load_grid_state_unexpected_type(
    service: WorkspacePersistenceService, monkeypatch
) -> None:
    cursor = AsyncMock()
    cursor.fetchone.return_value = (42, workspace_persistence.SCHEMA_VERSIONS["grid"])
    pool = _make_pool(cursor)
    monkeypatch.setattr(workspace_persistence, "get_db_pool", Mock(return_value=pool))

    assert await service.load_grid_state("user", "grid") is None


@pytest.mark.asyncio()
async def test_load_grid_state_invalid_dict_type(
    service: WorkspacePersistenceService, monkeypatch
) -> None:
    cursor = AsyncMock()
    cursor.fetchone.return_value = (["a"], workspace_persistence.SCHEMA_VERSIONS["grid"])
    pool = _make_pool(cursor)
    monkeypatch.setattr(workspace_persistence, "get_db_pool", Mock(return_value=pool))

    assert await service.load_grid_state("user", "grid") is None


@pytest.mark.asyncio()
async def test_reset_workspace_specific(service: WorkspacePersistenceService, monkeypatch) -> None:
    cursor = AsyncMock()
    pool = _make_pool(cursor)
    monkeypatch.setattr(workspace_persistence, "get_db_pool", Mock(return_value=pool))

    await service.reset_workspace("user-1", "grid.positions")

    cursor.execute.assert_called_once_with(
        "DELETE FROM workspace_state WHERE user_id = %s AND workspace_key = %s",
        ("user-1", "grid.positions"),
    )


@pytest.mark.asyncio()
async def test_reset_workspace_all(service: WorkspacePersistenceService, monkeypatch) -> None:
    cursor = AsyncMock()
    pool = _make_pool(cursor)
    monkeypatch.setattr(workspace_persistence, "get_db_pool", Mock(return_value=pool))

    await service.reset_workspace("user-1")

    cursor.execute.assert_called_once_with(
        "DELETE FROM workspace_state WHERE user_id = %s",
        ("user-1",),
    )


def test_get_workspace_service_singleton() -> None:
    workspace_persistence._workspace_service = None
    first = get_workspace_service()
    second = get_workspace_service()
    assert first is second
