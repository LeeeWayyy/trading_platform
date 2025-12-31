"""mTLS auth provider stub (Phase 1)."""

from __future__ import annotations

from typing import Any

from apps.web_console_ng.auth.providers.base import AuthProvider


class MTLSAuthProvider(AuthProvider):
    async def authenticate(self, request: Any) -> dict[str, Any] | None:
        raise NotImplementedError("MTLSAuthProvider is not implemented in Phase 1")

    async def logout(self, session_id: str) -> None:
        return None


__all__ = ["MTLSAuthProvider"]
