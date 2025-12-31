"""OAuth2 auth provider stub (Phase 1)."""

from __future__ import annotations

from typing import Any

from apps.web_console_ng.auth.providers.base import AuthProvider


class OAuth2AuthProvider(AuthProvider):
    async def authenticate(self, request: Any) -> dict[str, Any] | None:
        raise NotImplementedError("OAuth2AuthProvider is not implemented in Phase 1")

    async def logout(self, session_id: str) -> None:
        return None


__all__ = ["OAuth2AuthProvider"]
