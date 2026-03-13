"""Unit tests for AuthMiddleware role override (T16.2)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request

from apps.web_console_ng.auth.middleware import AuthMiddleware


@pytest.fixture()
def make_request() -> callable:
    def _make_request(
        *,
        path: str = "/",
        headers: list[tuple[bytes, bytes]] | None = None,
        client: tuple[str, int] = ("203.0.113.10", 1234),
        cookies: dict[str, str] | None = None,
    ) -> Request:
        scope = {
            "type": "http",
            "headers": headers or [],
            "client": client,
            "path": path,
            "scheme": "http",
            "query_string": b"",
        }
        req = Request(scope)
        if cookies:
            req._cookies = cookies
        return req

    return _make_request


@pytest.fixture()
def mock_storage_user() -> dict[str, Any]:
    """Fixture providing a mutable dict to simulate app.storage.user."""
    return {}


@pytest.fixture()
def mock_app(mock_storage_user: dict[str, Any]) -> SimpleNamespace:
    """Fixture providing a mock app with storage."""
    storage_user_obj = MagicMock()
    storage_user_obj.__getitem__ = lambda self, key: mock_storage_user[key]
    storage_user_obj.__setitem__ = lambda self, key, value: mock_storage_user.__setitem__(
        key, value
    )
    storage_user_obj.get = lambda key, default=None: mock_storage_user.get(key, default)
    storage_user_obj.clear = lambda: mock_storage_user.clear()
    return SimpleNamespace(storage=SimpleNamespace(user=storage_user_obj))


@pytest.fixture()
def middleware() -> AuthMiddleware:
    """Create an AuthMiddleware instance (app param unused for method tests)."""
    return AuthMiddleware(app=MagicMock())


# =============================================================================
# _override_role_from_db tests
# =============================================================================


@pytest.mark.asyncio()
async def test_role_override_from_db_success(
    middleware: AuthMiddleware,
    make_request: callable,
    mock_app: SimpleNamespace,
    mock_storage_user: dict[str, Any],
) -> None:
    """When DB has a different role, it overrides the session role."""
    request = make_request()
    user = {"user_id": "user-42", "role": "viewer", "username": "alice"}
    request.state.user = user

    # Mock Redis cache miss (returns None)
    mock_redis_client = AsyncMock()
    mock_redis_client.get = AsyncMock(return_value=None)
    mock_redis_client.setex = AsyncMock()

    mock_store = MagicMock()
    mock_store.get_master = AsyncMock(return_value=mock_redis_client)

    # Mock DB returning a different role
    mock_cursor = AsyncMock()
    mock_cursor.fetchone = AsyncMock(return_value=("admin",))

    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(return_value=mock_cursor)
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=False)

    mock_pool = MagicMock()
    mock_pool.connection = MagicMock(return_value=mock_conn)

    # Mock session store update
    mock_session_store = AsyncMock()
    mock_session_store.update_session_role = AsyncMock(return_value=True)

    with (
        patch(
            "apps.web_console_ng.auth.middleware.get_redis_store",
            return_value=mock_store,
        ),
        patch(
            "apps.web_console_ng.auth.middleware.get_db_pool",
            return_value=mock_pool,
        ),
        patch("apps.web_console_ng.auth.middleware.app", mock_app),
        patch(
            "apps.web_console_ng.auth.middleware.get_session_store",
            return_value=mock_session_store,
        ),
        patch(
            "apps.web_console_ng.auth.middleware.extract_session_id",
            return_value="session-abc",
        ),
        patch("apps.web_console_ng.auth.middleware.CookieConfig") as mock_cookie_cfg_cls,
    ):
        mock_cookie_cfg = MagicMock()
        mock_cookie_cfg.get_cookie_name.return_value = "ng_session"
        mock_cookie_cfg_cls.from_env.return_value = mock_cookie_cfg

        await middleware._override_role_from_db(request, user)

    # Role should be updated from viewer -> admin
    assert user["role"] == "admin"
    assert request.state.user["role"] == "admin"


@pytest.mark.asyncio()
async def test_role_override_cached_in_redis(
    middleware: AuthMiddleware,
    make_request: callable,
    mock_app: SimpleNamespace,
    mock_storage_user: dict[str, Any],
) -> None:
    """When Redis cache has a role, it skips the DB query and applies override."""
    request = make_request()
    user = {"user_id": "user-42", "role": "viewer", "username": "alice"}
    request.state.user = user

    # Mock Redis cache HIT (returns cached role as bytes)
    mock_redis_client = AsyncMock()
    mock_redis_client.get = AsyncMock(return_value=b"admin")

    mock_store = MagicMock()
    mock_store.get_master = AsyncMock(return_value=mock_redis_client)

    # DB should NOT be called at all
    mock_pool = MagicMock()
    mock_pool.connection = MagicMock(side_effect=AssertionError("DB should not be called"))

    with (
        patch(
            "apps.web_console_ng.auth.middleware.get_redis_store",
            return_value=mock_store,
        ),
        patch(
            "apps.web_console_ng.auth.middleware.get_db_pool",
            return_value=mock_pool,
        ),
        patch("apps.web_console_ng.auth.middleware.app", mock_app),
    ):
        await middleware._override_role_from_db(request, user)

    # Role should be updated from cached value
    assert user["role"] == "admin"
    assert request.state.user["role"] == "admin"


@pytest.mark.asyncio()
async def test_role_override_cached_same_role_skips_apply(
    middleware: AuthMiddleware,
    make_request: callable,
    mock_app: SimpleNamespace,
) -> None:
    """When Redis cache has the SAME role, no override is applied."""
    request = make_request()
    user = {"user_id": "user-42", "role": "viewer", "username": "alice"}
    request.state.user = user

    # Mock Redis cache returns same role
    mock_redis_client = AsyncMock()
    mock_redis_client.get = AsyncMock(return_value=b"viewer")

    mock_store = MagicMock()
    mock_store.get_master = AsyncMock(return_value=mock_redis_client)

    with (
        patch(
            "apps.web_console_ng.auth.middleware.get_redis_store",
            return_value=mock_store,
        ),
        patch("apps.web_console_ng.auth.middleware.app", mock_app),
    ):
        await middleware._override_role_from_db(request, user)

    # Role unchanged
    assert user["role"] == "viewer"


@pytest.mark.asyncio()
async def test_role_override_fails_open(
    middleware: AuthMiddleware,
    make_request: callable,
    mock_app: SimpleNamespace,
) -> None:
    """When DB query fails/times out, the original role is preserved (fail-open)."""
    request = make_request()
    user = {"user_id": "user-42", "role": "viewer", "username": "alice"}
    request.state.user = user

    # Mock Redis cache miss
    mock_redis_client = AsyncMock()
    mock_redis_client.get = AsyncMock(return_value=None)

    mock_store = MagicMock()
    mock_store.get_master = AsyncMock(return_value=mock_redis_client)

    # Mock DB pool that raises TimeoutError when queried
    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(side_effect=TimeoutError("DB query timed out"))
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=False)

    mock_pool = MagicMock()
    mock_pool.connection = MagicMock(return_value=mock_conn)

    with (
        patch(
            "apps.web_console_ng.auth.middleware.get_redis_store",
            return_value=mock_store,
        ),
        patch(
            "apps.web_console_ng.auth.middleware.get_db_pool",
            return_value=mock_pool,
        ),
        patch("apps.web_console_ng.auth.middleware.app", mock_app),
    ):
        await middleware._override_role_from_db(request, user)

    # Original role must be preserved
    assert user["role"] == "viewer"


@pytest.mark.asyncio()
async def test_role_override_fails_open_on_db_pool_none(
    middleware: AuthMiddleware,
    make_request: callable,
    mock_app: SimpleNamespace,
) -> None:
    """When get_db_pool() returns None, the original role is preserved."""
    request = make_request()
    user = {"user_id": "user-42", "role": "viewer", "username": "alice"}
    request.state.user = user

    # Mock Redis cache miss
    mock_redis_client = AsyncMock()
    mock_redis_client.get = AsyncMock(return_value=None)

    mock_store = MagicMock()
    mock_store.get_master = AsyncMock(return_value=mock_redis_client)

    with (
        patch(
            "apps.web_console_ng.auth.middleware.get_redis_store",
            return_value=mock_store,
        ),
        patch(
            "apps.web_console_ng.auth.middleware.get_db_pool",
            return_value=None,
        ),
        patch("apps.web_console_ng.auth.middleware.app", mock_app),
    ):
        await middleware._override_role_from_db(request, user)

    # Original role must be preserved
    assert user["role"] == "viewer"


@pytest.mark.asyncio()
async def test_role_override_skips_when_no_user_id(
    middleware: AuthMiddleware,
    make_request: callable,
) -> None:
    """When user has no user_id, _override_role_from_db returns early."""
    request = make_request()
    user = {"role": "viewer", "username": "anonymous"}
    request.state.user = user

    # No patches needed — method should bail out before touching Redis/DB
    await middleware._override_role_from_db(request, user)

    # Role unchanged
    assert user["role"] == "viewer"


# =============================================================================
# _apply_role_override tests
# =============================================================================


def test_apply_role_override(
    make_request: callable,
    mock_app: SimpleNamespace,
    mock_storage_user: dict[str, Any],
) -> None:
    """The static method correctly updates user dict, request.state, and storage."""
    request = make_request()
    user = {"user_id": "user-42", "role": "viewer", "username": "alice"}
    request.state.user = user

    # Pre-populate storage with old user data
    mock_storage_user["user"] = {"user_id": "user-42", "role": "viewer", "username": "alice"}

    with patch("apps.web_console_ng.auth.middleware.app", mock_app):
        AuthMiddleware._apply_role_override(request, user, "admin")

    # All three locations should be updated
    assert user["role"] == "admin"
    assert request.state.user["role"] == "admin"
    assert mock_storage_user["user"]["role"] == "admin"


def test_apply_role_override_storage_not_available(
    make_request: callable,
) -> None:
    """When app.storage is not available, user dict and request.state still update."""
    request = make_request()
    user = {"user_id": "user-42", "role": "viewer", "username": "alice"}
    request.state.user = user

    # Mock app.storage.user.get to raise RuntimeError
    mock_app_obj = MagicMock()
    mock_app_obj.storage.user.get.side_effect = RuntimeError("No storage context")

    with patch("apps.web_console_ng.auth.middleware.app", mock_app_obj):
        # Should not raise
        AuthMiddleware._apply_role_override(request, user, "admin")

    # In-memory objects should still be updated
    assert user["role"] == "admin"
    assert request.state.user["role"] == "admin"
