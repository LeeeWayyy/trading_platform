"""Auth provider factory."""

from __future__ import annotations

from apps.web_console_ng.auth.providers import basic, dev, mtls, oauth2
from apps.web_console_ng.auth.providers.base import AuthProvider

_PROVIDERS: dict[str, type[AuthProvider]] = {
    "dev": dev.DevAuthHandler,
    "basic": basic.BasicAuthHandler,
    "mtls": mtls.MTLSAuthHandler,
    "oauth2": oauth2.OAuth2AuthHandler,
}


def get_auth_provider(auth_type: str) -> AuthProvider:
    if auth_type not in _PROVIDERS:
        raise KeyError(f"Unknown auth type: {auth_type}")
    return _PROVIDERS[auth_type]()


__all__ = ["get_auth_provider"]
