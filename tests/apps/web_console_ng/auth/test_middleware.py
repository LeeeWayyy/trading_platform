"""Unit tests for auth middleware helpers."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from starlette.requests import Request

from apps.web_console_ng.auth import middleware as middleware_module


@pytest.fixture()
def make_request() -> callable:
    def _make_request(
        *,
        path: str = "/",
        headers: list[tuple[bytes, bytes]] | None = None,
        client: tuple[str, int] = ("203.0.113.10", 1234),
    ) -> Request:
        scope = {
            "type": "http",
            "headers": headers or [],
            "client": client,
            "path": path,
            "scheme": "http",
            "query_string": b"",
        }
        return Request(scope)

    return _make_request


def test_get_request_from_storage_prefers_contextvar(
    make_request: callable, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = make_request(path="/health")

    def _return_request() -> Request:
        return request

    dummy_storage = SimpleNamespace(request_contextvar=SimpleNamespace(get=_return_request))
    dummy_ui = SimpleNamespace(context=SimpleNamespace(client=SimpleNamespace(request=None)))
    import nicegui

    monkeypatch.setattr(nicegui, "storage", dummy_storage, raising=False)
    monkeypatch.setattr(nicegui, "ui", dummy_ui, raising=False)

    assert middleware_module._get_request_from_storage() is request


def test_get_request_from_storage_falls_back_in_debug(
    make_request: callable, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _raise_lookup() -> Request:
        raise LookupError

    dummy_storage = SimpleNamespace(request_contextvar=SimpleNamespace(get=_raise_lookup))
    dummy_ui = SimpleNamespace(context=SimpleNamespace(client=SimpleNamespace()))
    import nicegui

    monkeypatch.setattr(nicegui, "storage", dummy_storage, raising=False)
    monkeypatch.setattr(nicegui, "ui", dummy_ui, raising=False)
    monkeypatch.setattr(middleware_module.config, "DEBUG", True)

    request = middleware_module._get_request_from_storage()

    assert request.client is not None
    assert request.client.host == "192.0.2.1"
    assert request.url.path == "/"


def test_validate_mtls_request_rejects_untrusted_ip(
    make_request: callable, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = make_request(headers=[(b"x-ssl-client-verify", b"SUCCESS")])
    monkeypatch.setattr(middleware_module, "is_trusted_ip", lambda _ip: False)

    assert middleware_module._validate_mtls_request(request, {"client_dn": "CN=user"}) is False


def test_validate_mtls_request_accepts_matching_dn(
    make_request: callable, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = [
        (b"x-ssl-client-verify", b"SUCCESS"),
        (b"x-ssl-client-dn", b"CN=user"),
    ]
    request = make_request(headers=headers)
    monkeypatch.setattr(middleware_module, "is_trusted_ip", lambda _ip: True)

    assert middleware_module._validate_mtls_request(request, {"client_dn": "CN=user"}) is True


def test_validate_mtls_request_rejects_mismatched_dn(
    make_request: callable, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = [
        (b"x-ssl-client-verify", b"SUCCESS"),
        (b"x-ssl-client-dn", b"CN=other"),
    ]
    request = make_request(headers=headers)
    monkeypatch.setattr(middleware_module, "is_trusted_ip", lambda _ip: True)

    assert middleware_module._validate_mtls_request(request, {"client_dn": "CN=user"}) is False


def test_redirect_to_login_sets_storage_and_navigates(
    make_request: callable, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage_user: dict[str, str] = {}
    dummy_app = SimpleNamespace(storage=SimpleNamespace(user=storage_user))
    dummy_ui = SimpleNamespace(navigate=SimpleNamespace(to=MagicMock()))
    monkeypatch.setattr(middleware_module, "app", dummy_app)
    monkeypatch.setattr(middleware_module, "ui", dummy_ui)

    request = make_request(path="/risk")

    middleware_module._redirect_to_login(request)

    assert storage_user["redirect_after_login"] == "/risk"
    assert storage_user["login_reason"] == "session_expired"
    dummy_ui.navigate.to.assert_called_once_with("/login")
