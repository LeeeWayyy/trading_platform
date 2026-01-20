"""Tests for session utilities."""

from __future__ import annotations

from types import SimpleNamespace

from apps.web_console_ng.utils import session


class _DummyLifecycle:
    def __init__(self, client_id: str) -> None:
        self._client_id = client_id

    def generate_client_id(self) -> str:
        return self._client_id


class _DummyLifecycleManager:
    def __init__(self, client_id: str) -> None:
        self._lifecycle = _DummyLifecycle(client_id)

    def get(self) -> _DummyLifecycle:
        return self._lifecycle


def test_get_or_create_client_id_returns_empty_on_context_error(monkeypatch) -> None:
    class _Boom:
        @property
        def context(self):  # noqa: D401 - test helper
            raise RuntimeError("boom")

    monkeypatch.setattr(session, "ui", _Boom())
    assert session.get_or_create_client_id() == ""


def test_get_or_create_client_id_returns_empty_when_client_missing(monkeypatch) -> None:
    dummy_ui = SimpleNamespace(context=SimpleNamespace(client=None))
    monkeypatch.setattr(session, "ui", dummy_ui)
    assert session.get_or_create_client_id() == ""


def test_get_or_create_client_id_uses_existing_storage(monkeypatch) -> None:
    storage = {"client_id": "stored-1"}
    client = SimpleNamespace(storage=storage, id="fallback")
    dummy_ui = SimpleNamespace(context=SimpleNamespace(client=client))
    monkeypatch.setattr(session, "ui", dummy_ui)

    assert session.get_or_create_client_id() == "stored-1"


def test_get_or_create_client_id_generates_and_stores(monkeypatch) -> None:
    storage: dict[str, str] = {}
    client = SimpleNamespace(storage=storage, id="fallback")
    dummy_ui = SimpleNamespace(context=SimpleNamespace(client=client))
    monkeypatch.setattr(session, "ui", dummy_ui)
    monkeypatch.setattr(
        session,
        "ClientLifecycleManager",
        _DummyLifecycleManager("generated-1"),
    )

    assert session.get_or_create_client_id() == "generated-1"
    assert storage["client_id"] == "generated-1"


def test_get_or_create_client_id_falls_back_when_storage_unwritable(monkeypatch) -> None:
    class _BadStorage:
        def get(self, key: str):
            return None

        def __setitem__(self, key: str, value: str) -> None:
            raise TypeError("nope")

    client = SimpleNamespace(storage=_BadStorage(), id="fallback-2")
    dummy_ui = SimpleNamespace(context=SimpleNamespace(client=client))
    monkeypatch.setattr(session, "ui", dummy_ui)
    monkeypatch.setattr(
        session,
        "ClientLifecycleManager",
        _DummyLifecycleManager("generated-2"),
    )

    assert session.get_or_create_client_id() == "fallback-2"
