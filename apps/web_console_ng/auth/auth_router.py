from __future__ import annotations

from typing import Literal, overload

from apps.web_console_ng import config
from apps.web_console_ng.auth.providers.base import AuthProvider
from apps.web_console_ng.auth.providers.basic import BasicAuthHandler
from apps.web_console_ng.auth.providers.dev import DevAuthHandler
from apps.web_console_ng.auth.providers.mtls import MTLSAuthHandler
from apps.web_console_ng.auth.providers.oauth2 import OAuth2AuthHandler


@overload
def get_auth_handler(auth_type: Literal["mtls"]) -> MTLSAuthHandler: ...


@overload
def get_auth_handler(auth_type: Literal["dev"]) -> DevAuthHandler: ...


@overload
def get_auth_handler(auth_type: Literal["basic"]) -> BasicAuthHandler: ...


@overload
def get_auth_handler(auth_type: Literal["oauth2"]) -> OAuth2AuthHandler: ...


@overload
def get_auth_handler(auth_type: None = None) -> AuthProvider: ...


def get_auth_handler(
    auth_type: Literal["dev", "basic", "mtls", "oauth2"] | None = None,
) -> AuthProvider:
    """Return appropriate auth handler based on AUTH_TYPE config or argument.

    Args:
        auth_type: Explicit auth type to request. Defaults to config.AUTH_TYPE.

    Returns:
        AuthProvider: Instance of the requested auth provider.

    Raises:
        ValueError: If auth_type is unknown.
    """
    selected_type = auth_type or config.AUTH_TYPE

    handlers: dict[str, type[AuthProvider]] = {
        "dev": DevAuthHandler,
        "basic": BasicAuthHandler,
        "mtls": MTLSAuthHandler,
        "oauth2": OAuth2AuthHandler,
    }

    handler_class = handlers.get(selected_type)
    if not handler_class:
        # Fallback to dev if unknown type in dev/debug mode?
        # Better to fail fail-safe.
        raise ValueError(f"Unknown auth type: {selected_type}")

    return handler_class()
