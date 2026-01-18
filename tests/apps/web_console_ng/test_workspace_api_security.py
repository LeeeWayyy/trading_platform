"""Tests for workspace persistence API security and validation."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI, HTTPException, status
from httpx import ASGITransport, AsyncClient

from apps.web_console_ng.api import workspace as workspace_api
from apps.web_console_ng.core.workspace_persistence import DatabaseUnavailableError


class _StubWorkspaceService:
    def __init__(
        self,
        *,
        save_result: bool = True,
        load_result: dict[str, Any] | None = None,
        save_exc: Exception | None = None,
        load_exc: Exception | None = None,
        reset_exc: Exception | None = None,
    ) -> None:
        self._save_result = save_result
        self._load_result = load_result
        self._save_exc = save_exc
        self._load_exc = load_exc
        self._reset_exc = reset_exc

    async def save_grid_state(self, user_id: str, grid_id: str, state: dict[str, Any]) -> bool:
        if self._save_exc:
            raise self._save_exc
        return self._save_result

    async def load_grid_state(self, user_id: str, grid_id: str) -> dict[str, Any] | None:
        if self._load_exc:
            raise self._load_exc
        return self._load_result

    async def reset_workspace(self, user_id: str, workspace_key: str | None = None) -> None:
        if self._reset_exc:
            raise self._reset_exc


def _build_app(monkeypatch: pytest.MonkeyPatch, service: _StubWorkspaceService) -> FastAPI:
    app = FastAPI()
    app.include_router(workspace_api.router)
    app.dependency_overrides[workspace_api.require_authenticated_user] = lambda: {
        "user_id": "user-1"
    }
    monkeypatch.setattr(workspace_api, "get_workspace_service", lambda: service)
    return app


@pytest.mark.asyncio()
async def test_invalid_grid_id_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _build_app(monkeypatch, _StubWorkspaceService())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/workspace/grid/invalid_grid")

    assert resp.status_code == 400
    assert "Invalid grid_id" in resp.json()["detail"]


@pytest.mark.asyncio()
async def test_csrf_missing_blocks_mutation(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _build_app(monkeypatch, _StubWorkspaceService())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/workspace/grid/positions_grid",
            json={"columns": []},
        )

    assert resp.status_code == 403
    assert resp.json()["detail"] == "csrf_missing"


@pytest.mark.asyncio()
async def test_csrf_invalid_blocks_mutation(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _build_app(monkeypatch, _StubWorkspaceService())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        client.cookies.set("ng_csrf", "cookie-token")
        resp = await client.post(
            "/api/workspace/grid/positions_grid",
            json={"columns": []},
            headers={"X-CSRF-Token": "bad-token"},
        )

    assert resp.status_code == 403
    assert resp.json()["detail"] == "csrf_invalid"


@pytest.mark.asyncio()
async def test_delete_missing_csrf_blocks_mutation(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _build_app(monkeypatch, _StubWorkspaceService())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.delete("/api/workspace/grid/positions_grid")

    assert resp.status_code == 403
    assert resp.json()["detail"] == "csrf_missing"


@pytest.mark.asyncio()
async def test_delete_invalid_csrf_blocks_mutation(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _build_app(monkeypatch, _StubWorkspaceService())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        client.cookies.set("ng_csrf", "cookie-token")
        resp = await client.delete(
            "/api/workspace/grid/positions_grid",
            headers={"X-CSRF-Token": "bad-token"},
        )

    assert resp.status_code == 403
    assert resp.json()["detail"] == "csrf_invalid"


@pytest.mark.asyncio()
async def test_delete_requires_authentication(monkeypatch: pytest.MonkeyPatch) -> None:
    app = FastAPI()
    app.include_router(workspace_api.router)

    def _unauthenticated_user() -> dict[str, Any]:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )

    app.dependency_overrides[workspace_api.require_authenticated_user] = _unauthenticated_user
    monkeypatch.setattr(workspace_api, "get_workspace_service", lambda: _StubWorkspaceService())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.delete("/api/workspace/grid/positions_grid")

    assert resp.status_code in {401, 403}


@pytest.mark.asyncio()
async def test_oversized_payload_returns_413(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _build_app(monkeypatch, _StubWorkspaceService(save_result=False))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        client.cookies.set("ng_csrf", "token")
        resp = await client.post(
            "/api/workspace/grid/positions_grid",
            json={"columns": []},
            headers={"X-CSRF-Token": "token"},
        )

    assert resp.status_code == 413
    assert resp.json()["detail"] == "State too large"


@pytest.mark.asyncio()
async def test_schema_mismatch_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _build_app(monkeypatch, _StubWorkspaceService(load_result=None))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/workspace/grid/positions_grid")

    assert resp.status_code == 200
    assert resp.json() is None


@pytest.mark.asyncio()
async def test_db_unavailable_returns_503(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _build_app(
        monkeypatch,
        _StubWorkspaceService(load_exc=DatabaseUnavailableError("db down")),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/workspace/grid/positions_grid")

    assert resp.status_code == 503
    assert resp.json()["detail"] == "Database not configured"
