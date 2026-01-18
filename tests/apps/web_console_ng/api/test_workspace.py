"""Tests for workspace API handlers and helpers."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI, HTTPException, status
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request

from apps.web_console_ng.api import workspace as workspace_api
from apps.web_console_ng.core.workspace_persistence import (
    MAX_STATE_SIZE,
    DatabaseUnavailableError,
)


def _make_request(
    body: bytes = b"",
    *,
    headers: dict[str, str] | None = None,
    chunk_size: int | None = None,
) -> Request:
    headers = headers or {}
    header_items = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    chunks: list[bytes] = []
    if chunk_size is None:
        chunks = [body]
    else:
        for idx in range(0, len(body), chunk_size):
            chunks.append(body[idx : idx + chunk_size])

    async def receive() -> dict[str, Any]:
        if chunks:
            chunk = chunks.pop(0)
            return {"type": "http.request", "body": chunk, "more_body": bool(chunks)}
        return {"type": "http.request", "body": b"", "more_body": False}

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/",
        "headers": header_items,
    }
    return Request(scope, receive)


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
        self.save_calls: list[tuple[str, str, dict[str, Any]]] = []
        self.reset_calls: list[tuple[str, str | None]] = []
        self.load_calls: list[tuple[str, str]] = []
        self._save_result = save_result
        self._load_result = load_result
        self._save_exc = save_exc
        self._load_exc = load_exc
        self._reset_exc = reset_exc

    async def save_grid_state(self, user_id: str, grid_id: str, state: dict[str, Any]) -> bool:
        if self._save_exc:
            raise self._save_exc
        self.save_calls.append((user_id, grid_id, state))
        return self._save_result

    async def load_grid_state(self, user_id: str, grid_id: str) -> dict[str, Any] | None:
        if self._load_exc:
            raise self._load_exc
        self.load_calls.append((user_id, grid_id))
        return self._load_result

    async def reset_workspace(self, user_id: str, workspace_key: str | None = None) -> None:
        if self._reset_exc:
            raise self._reset_exc
        self.reset_calls.append((user_id, workspace_key))


def _build_app(
    monkeypatch: pytest.MonkeyPatch, service: _StubWorkspaceService
) -> FastAPI:
    app = FastAPI()
    app.include_router(workspace_api.router)
    app.dependency_overrides[workspace_api.require_authenticated_user] = (
        lambda: {"user_id": "user-1"}
    )
    monkeypatch.setattr(workspace_api, "get_workspace_service", lambda: service)
    return app


@pytest.mark.asyncio()
async def test_validate_grid_id_length_limit() -> None:
    too_long = "x" * (workspace_api.MAX_GRID_ID_LENGTH + 1)
    with pytest.raises(HTTPException) as excinfo:
        workspace_api.validate_grid_id(too_long)

    assert excinfo.value.status_code == status.HTTP_400_BAD_REQUEST
    assert "grid_id exceeds max length" in str(excinfo.value.detail)


def test_validate_grid_id_accepts_allowlist() -> None:
    for grid_id in workspace_api.VALID_GRID_IDS:
        workspace_api.validate_grid_id(grid_id)


@pytest.mark.asyncio()
async def test_enforce_max_state_size_rejects_invalid_content_length() -> None:
    request = _make_request(headers={"content-length": "bad"})

    with pytest.raises(HTTPException) as excinfo:
        await workspace_api.enforce_max_state_size(request)

    assert excinfo.value.status_code == status.HTTP_400_BAD_REQUEST
    assert excinfo.value.detail == "Invalid Content-Length header"


@pytest.mark.asyncio()
async def test_enforce_max_state_size_rejects_large_content_length() -> None:
    request = _make_request(headers={"content-length": str(MAX_STATE_SIZE + 1)})

    with pytest.raises(HTTPException) as excinfo:
        await workspace_api.enforce_max_state_size(request)

    assert excinfo.value.status_code == status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
    assert excinfo.value.detail == "State too large"


@pytest.mark.asyncio()
async def test_enforce_max_state_size_rejects_cached_body() -> None:
    request = _make_request()
    request._body = b"x" * (MAX_STATE_SIZE + 1)

    with pytest.raises(HTTPException) as excinfo:
        await workspace_api.enforce_max_state_size(request)

    assert excinfo.value.status_code == status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
    assert excinfo.value.detail == "State too large"


@pytest.mark.asyncio()
async def test_enforce_max_state_size_streaming_sets_body() -> None:
    body = b"a" * 128
    request = _make_request(body, chunk_size=32)

    await workspace_api.enforce_max_state_size(request)

    assert request._body == body
    assert request._stream_consumed is True


@pytest.mark.asyncio()
async def test_enforce_max_state_size_streaming_rejects_oversize() -> None:
    body = b"a" * (MAX_STATE_SIZE + 1)
    request = _make_request(body, chunk_size=MAX_STATE_SIZE // 2 + 1)

    with pytest.raises(HTTPException) as excinfo:
        await workspace_api.enforce_max_state_size(request)

    assert excinfo.value.status_code == status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
    assert excinfo.value.detail == "State too large"


@pytest.mark.asyncio()
async def test_require_authenticated_user_prefers_request_state(monkeypatch: pytest.MonkeyPatch) -> None:
    request = _make_request()
    request.state.user = {"user_id": "user-123"}

    user = await workspace_api.require_authenticated_user(request)

    assert user["user_id"] == "user-123"


@pytest.mark.asyncio()
async def test_require_authenticated_user_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    request = _make_request()

    async def _fake_validate(_request: Request) -> tuple[dict[str, Any], Any]:
        return {"user_id": "user-456"}, None

    from apps.web_console_ng.auth import middleware as auth_middleware

    monkeypatch.setattr(auth_middleware, "_validate_session_and_get_user", _fake_validate)

    user = await workspace_api.require_authenticated_user(request)

    assert user["user_id"] == "user-456"


@pytest.mark.asyncio()
async def test_require_authenticated_user_unauthenticated(monkeypatch: pytest.MonkeyPatch) -> None:
    request = _make_request()

    async def _fake_validate(_request: Request) -> tuple[dict[str, Any] | None, Any]:
        return None, None

    from apps.web_console_ng.auth import middleware as auth_middleware

    monkeypatch.setattr(auth_middleware, "_validate_session_and_get_user", _fake_validate)

    with pytest.raises(HTTPException) as excinfo:
        await workspace_api.require_authenticated_user(request)

    assert excinfo.value.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio()
async def test_save_grid_state_success(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _StubWorkspaceService()
    app = _build_app(monkeypatch, service)

    async def _noop_csrf(_request: Request) -> None:
        return None

    monkeypatch.setattr(workspace_api, "verify_csrf_token", _noop_csrf)

    payload = {"columns": [{"id": "col"}], "filters": None, "sort": [{"id": "col"}]}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/workspace/grid/positions_grid",
            json=payload,
            headers={"X-CSRF-Token": "token"},
        )

    assert response.status_code == 200
    assert response.json() == {"success": True}
    assert service.save_calls
    user_id, grid_id, state = service.save_calls[0]
    assert user_id == "user-1"
    assert grid_id == "positions_grid"
    assert "filters" not in state


@pytest.mark.asyncio()
async def test_save_grid_state_db_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _StubWorkspaceService(save_exc=DatabaseUnavailableError("db down"))
    app = _build_app(monkeypatch, service)

    async def _noop_csrf(_request: Request) -> None:
        return None

    monkeypatch.setattr(workspace_api, "verify_csrf_token", _noop_csrf)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/workspace/grid/positions_grid",
            json={"columns": []},
            headers={"X-CSRF-Token": "token"},
        )

    assert response.status_code == 503
    assert response.json()["detail"] == "Database not configured"


@pytest.mark.asyncio()
async def test_load_grid_state_success(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _StubWorkspaceService(load_result={"columns": [{"id": "col"}]})
    app = _build_app(monkeypatch, service)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/workspace/grid/positions_grid")

    assert response.status_code == 200
    assert response.json() == {"columns": [{"id": "col"}]}
    assert service.load_calls == [("user-1", "positions_grid")]


@pytest.mark.asyncio()
async def test_reset_grid_state_success(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _StubWorkspaceService()
    app = _build_app(monkeypatch, service)

    async def _noop_csrf(_request: Request) -> None:
        return None

    monkeypatch.setattr(workspace_api, "verify_csrf_token", _noop_csrf)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.delete(
            "/api/workspace/grid/positions_grid",
            headers={"X-CSRF-Token": "token"},
        )

    assert response.status_code == 200
    assert response.json() == {"success": True}
    assert service.reset_calls == [("user-1", "grid.positions_grid")]
