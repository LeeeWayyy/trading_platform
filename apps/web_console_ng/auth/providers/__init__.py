"""Auth provider implementations."""

from apps.web_console_ng.auth.providers.base import AuthProvider
from apps.web_console_ng.auth.providers.basic import BasicAuthProvider
from apps.web_console_ng.auth.providers.dev import DevAuthProvider
from apps.web_console_ng.auth.providers.mtls import MTLSAuthProvider
from apps.web_console_ng.auth.providers.oauth2 import OAuth2AuthProvider

__all__ = [
    "AuthProvider",
    "BasicAuthProvider",
    "DevAuthProvider",
    "MTLSAuthProvider",
    "OAuth2AuthProvider",
]
