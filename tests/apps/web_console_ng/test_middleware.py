"""Tests for SessionMiddleware and AuthMiddleware."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from fakeredis.aioredis import FakeRedis
from fastapi import FastAPI, Request
from httpx import AsyncClient

from apps.web_console_ng import config
from apps.web_console_ng.auth.middleware import AuthMiddleware, SessionMiddleware
from apps.web_console_ng.auth.session_store import ServerSessionStore


def _make_store(redis_client: FakeRedis) -> ServerSessionStore:
    signing_keys = {"01": b"a" * 32}
    return ServerSessionStore(
        redis_url="redis://localhost:6379/1",
        encryption_keys=[Fernet.generate_key()],
        signing_keys=signing_keys,
        current_signing_key_id="01",
        redis_client=redis_client,
    )


def _build_app(store: ServerSessionStore) -> FastAPI:
    app = FastAPI()

    @app.get("/protected")
    async def protected(request: Request) -> dict[str, str | None]:
        user = getattr(request.state, "user", None)
        return {"user_id": user.get("user_id") if isinstance(user, dict) else None}

    @app.get("/dev/login")
    async def dev_login() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    wrapped = AuthMiddleware(app)
    wrapped = SessionMiddleware(wrapped, session_store=store)
    return wrapped  # type: ignore[return-value]


@pytest.mark.asyncio()
async def test_session_middleware_populates_user(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "DEVICE_BINDING_ENABLED", False)
    redis_client = FakeRedis()
    store = _make_store(redis_client)
    app = _build_app(store)

    cookie_value, _ = await store.create_session(
        {"user_id": "user-1"},
        {"user_agent": "ua"},
        "testclient",
    )

    async with AsyncClient(app=app, base_url="http://test") as client:
        client.cookies.set(config.SESSION_COOKIE_NAME, cookie_value)
        resp = await client.get("/protected")

    assert resp.status_code == 200
    assert resp.json()["user_id"] == "user-1"


@pytest.mark.asyncio()
async def test_auth_middleware_blocks_unauthenticated() -> None:
    redis_client = FakeRedis()
    store = _make_store(redis_client)
    app = _build_app(store)

    async with AsyncClient(app=app, base_url="http://test") as client:
        resp = await client.get("/protected")

    assert resp.status_code == 401


@pytest.mark.asyncio()
async def test_auth_middleware_exempts_paths() -> None:
    redis_client = FakeRedis()
    store = _make_store(redis_client)
    app = _build_app(store)

    async with AsyncClient(app=app, base_url="http://test") as client:
        resp = await client.get("/dev/login")

    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
