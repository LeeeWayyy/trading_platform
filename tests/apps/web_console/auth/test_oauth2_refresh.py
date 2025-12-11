from datetime import UTC, datetime, timedelta

import pytest

from apps.web_console.auth.oauth2_flow import OAuth2Config, OAuth2FlowHandler
from apps.web_console.auth.session_store import RedisSessionStore, SessionData


class FakeRedis:
    def __init__(self):
        self.saved = None

    async def setex(self, key, ttl, value):
        self.saved = (key, ttl, value)


class FakeCursor:
    """Fake cursor mimicking psycopg3 AsyncCursor."""

    def __init__(self, row):
        self._row = row

    async def fetchone(self):
        return self._row


class FakeConn:
    """Fake connection mimicking psycopg3 AsyncConnection."""

    def __init__(self, session_version: int = 1):
        self.session_version = session_version

    async def execute(self, query, params=None):
        # Return cursor with tuple row (psycopg3 pattern)
        return FakeCursor(row=(self.session_version,))

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeAsyncContextManager:
    """Async context manager for connection()."""

    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *_args):
        return None


class FakePool:
    """Fake pool mimicking psycopg_pool.AsyncConnectionPool."""

    def __init__(self, session_version: int = 1):
        self.conn = FakeConn(session_version)

    def connection(self):
        """Return async context manager like psycopg_pool."""
        return FakeAsyncContextManager(self.conn)


class FakeStateStore:
    pass


class FakeJWKSValidator:
    async def validate_id_token(self, *args, **kwargs):
        return {}


class FakeAsyncClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, *args, **kwargs):
        class Response:
            def __init__(self):
                self.status_code = 200

            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "access_token": "new-access-token",
                    "refresh_token": "rotated-refresh-token",
                    "expires_in": 1200,
                }

        return Response()


class FakeSessionStore(RedisSessionStore):
    def __init__(self, session_data: SessionData, redis_client: FakeRedis):
        super().__init__(redis_client=redis_client, encryption_key=b"0" * 32)
        self._session = session_data
        self.deleted = False

    async def get_session(self, *args, **kwargs):
        return self._session

    async def delete_session(self, session_id: str):
        self.deleted = True


def _make_handler(session_data: SessionData, pool: FakePool, monkeypatch):
    redis_client = FakeRedis()
    session_store = FakeSessionStore(session_data=session_data, redis_client=redis_client)
    config = OAuth2Config(
        auth0_domain="example.auth0.com",
        client_id="cid",
        client_secret="secret",
        audience="aud",
        redirect_uri="https://app/callback",
        logout_redirect_uri="https://app/logout",
    )
    handler = OAuth2FlowHandler(
        config=config,
        session_store=session_store,
        state_store=FakeStateStore(),
        jwks_validator=FakeJWKSValidator(),
        db_pool=pool,
    )
    monkeypatch.setattr("apps.web_console.auth.oauth2_flow.httpx.AsyncClient", FakeAsyncClient)
    return handler, session_store, redis_client


def _session_data(session_version: int = 1) -> SessionData:
    now = datetime.now(UTC)
    return SessionData(
        access_token="old-access",
        refresh_token="old-refresh",
        id_token="id-token",
        user_id="user123",
        email="user@example.com",
        created_at=now,
        last_activity=now,
        ip_address="1.1.1.1",
        user_agent="ua",
        access_token_expires_at=now + timedelta(hours=1),
        role="viewer",
        strategies=["alpha"],
        session_version=session_version,
    )


@pytest.mark.asyncio
async def test_refresh_tokens_updates_session_and_persists(monkeypatch):
    pool = FakePool(session_version=1)
    handler, store, redis_client = _make_handler(_session_data(), pool, monkeypatch)

    updated = await handler.refresh_tokens(session_id="sid", db_pool=pool)

    assert updated.access_token == "new-access-token"
    assert updated.refresh_token == "rotated-refresh-token"
    assert redis_client.saved is not None
    key, ttl, _ = redis_client.saved
    assert key.startswith("session:sid")
    assert ttl > 0
    assert store.deleted is False


@pytest.mark.asyncio
async def test_refresh_tokens_rejects_on_session_version_mismatch(monkeypatch):
    pool = FakePool(session_version=5)
    handler, store, _ = _make_handler(_session_data(session_version=1), pool, monkeypatch)

    with pytest.raises(ValueError):
        await handler.refresh_tokens(session_id="sid", db_pool=pool)

    assert store.deleted is True
