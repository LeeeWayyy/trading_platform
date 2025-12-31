"""Tests for CSRF validation dependency."""

from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from httpx import AsyncClient

from apps.web_console_ng.auth import csrf
from apps.web_console_ng.auth.csrf import verify_csrf_token


def _build_app() -> FastAPI:
    app = FastAPI()

    @app.post("/auth/logout", dependencies=[Depends(verify_csrf_token)])
    async def logout() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/dev/login", dependencies=[Depends(verify_csrf_token)])
    async def dev_login() -> dict[str, str]:
        return {"status": "ok"}

    return app


@pytest.mark.asyncio()
async def test_csrf_allows_exempt_paths() -> None:
    app = _build_app()

    async with AsyncClient(app=app, base_url="http://test") as client:
        resp = await client.post("/dev/login")

    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.asyncio()
async def test_csrf_blocks_missing_token() -> None:
    app = _build_app()

    async with AsyncClient(app=app, base_url="http://test") as client:
        resp = await client.post("/auth/logout")

    assert resp.status_code == 403
    assert resp.json()["detail"] == "csrf_missing"


@pytest.mark.asyncio()
async def test_csrf_blocks_mismatched_token() -> None:
    app = _build_app()

    async with AsyncClient(app=app, base_url="http://test") as client:
        client.cookies.set("ng_csrf", "cookie-token")
        resp = await client.post("/auth/logout", headers={"X-CSRF-Token": "bad"})

    assert resp.status_code == 403
    assert resp.json()["detail"] == "csrf_invalid"


@pytest.mark.asyncio()
async def test_csrf_allows_matching_token() -> None:
    app = _build_app()

    async with AsyncClient(app=app, base_url="http://test") as client:
        client.cookies.set("ng_csrf", "token")
        resp = await client.post("/auth/logout", headers={"X-CSRF-Token": "token"})

    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.asyncio()
async def test_csrf_uses_constant_time_compare(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _build_app()
    called = {"value": False}

    def _compare(a: str, b: str) -> bool:
        called["value"] = True
        return True

    monkeypatch.setattr(csrf.hmac, "compare_digest", _compare)

    async with AsyncClient(app=app, base_url="http://test") as client:
        client.cookies.set("ng_csrf", "token")
        resp = await client.post("/auth/logout", headers={"X-CSRF-Token": "token"})

    assert resp.status_code == 200
    assert called["value"] is True
