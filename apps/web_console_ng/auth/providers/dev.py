"""Development auth provider (functional)."""

from __future__ import annotations

from typing import Any

from apps.web_console_ng import config
from apps.web_console_ng.auth.providers.base import AuthProvider


class DevAuthProvider(AuthProvider):
    """Development auth - auto-login with configured dev user."""

    async def authenticate(self, request: Any) -> dict[str, Any] | None:
        return {
            "user_id": config.DEV_USER_ID,
            "role": config.DEV_ROLE,
            "strategies": config.DEV_STRATEGIES,
        }

    async def logout(self, session_id: str) -> None:
        return None


__all__ = ["DevAuthProvider"]
