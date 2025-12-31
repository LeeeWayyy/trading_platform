"""Tests for auth provider factory."""

from __future__ import annotations

import pytest

from apps.web_console_ng.auth.factory import get_auth_provider
from apps.web_console_ng.auth.providers.basic import BasicAuthProvider
from apps.web_console_ng.auth.providers.dev import DevAuthProvider
from apps.web_console_ng.auth.providers.mtls import MTLSAuthProvider
from apps.web_console_ng.auth.providers.oauth2 import OAuth2AuthProvider


def test_factory_returns_dev() -> None:
    assert isinstance(get_auth_provider("dev"), DevAuthProvider)


def test_factory_returns_basic() -> None:
    assert isinstance(get_auth_provider("basic"), BasicAuthProvider)


def test_factory_returns_mtls() -> None:
    assert isinstance(get_auth_provider("mtls"), MTLSAuthProvider)


def test_factory_returns_oauth2() -> None:
    assert isinstance(get_auth_provider("oauth2"), OAuth2AuthProvider)


def test_factory_unknown() -> None:
    with pytest.raises(KeyError):
        get_auth_provider("unknown")
