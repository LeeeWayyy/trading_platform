"""Tests for auth provider factory."""

from __future__ import annotations

import pytest

from apps.web_console_ng.auth.factory import get_auth_provider
from apps.web_console_ng.auth.providers.basic import BasicAuthHandler
from apps.web_console_ng.auth.providers.dev import DevAuthHandler
from apps.web_console_ng.auth.providers.mtls import MTLSAuthHandler
from apps.web_console_ng.auth.providers.oauth2 import OAuth2AuthHandler


def test_factory_returns_dev() -> None:
    assert isinstance(get_auth_provider("dev"), DevAuthHandler)


def test_factory_returns_basic() -> None:
    assert isinstance(get_auth_provider("basic"), BasicAuthHandler)


def test_factory_returns_mtls() -> None:
    assert isinstance(get_auth_provider("mtls"), MTLSAuthHandler)


def test_factory_returns_oauth2() -> None:
    assert isinstance(get_auth_provider("oauth2"), OAuth2AuthHandler)


def test_factory_unknown() -> None:
    with pytest.raises(KeyError):
        get_auth_provider("unknown")
