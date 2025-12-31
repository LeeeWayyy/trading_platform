"""Cookie configuration helpers for the NiceGUI web console."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from apps.web_console_ng import config


@dataclass(frozen=True)
class CookieConfig:
    """Encapsulates cookie settings derived from environment config."""

    secure: bool
    httponly: bool
    samesite: str
    path: str
    domain: str | None

    @classmethod
    def from_env(cls) -> CookieConfig:
        return cls(
            secure=config.SESSION_COOKIE_SECURE,
            httponly=config.SESSION_COOKIE_HTTPONLY,
            samesite=config.SESSION_COOKIE_SAMESITE,
            path=config.SESSION_COOKIE_PATH,
            domain=config.SESSION_COOKIE_DOMAIN,
        )

    def get_cookie_name(self) -> str:
        return "__Host-nicegui_session" if self.secure else "nicegui_session"

    def get_cookie_flags(self) -> dict[str, Any]:
        effective_path = "/" if self.secure else self.path
        flags: dict[str, Any] = {
            "httponly": self.httponly,
            "secure": self.secure,
            "samesite": self.samesite,
            "path": effective_path,
        }

        # __Host- cookies must not set Domain; enforce that even if configured.
        domain = None if self.secure else self.domain
        if domain:
            flags["domain"] = domain

        return flags

    def get_csrf_flags(self) -> dict[str, Any]:
        effective_path = "/" if self.secure else self.path
        flags: dict[str, Any] = {
            "httponly": False,
            "secure": self.secure,
            "samesite": self.samesite,
            "path": effective_path,
        }
        if self.domain:
            flags["domain"] = self.domain
        return flags


__all__ = ["CookieConfig"]
