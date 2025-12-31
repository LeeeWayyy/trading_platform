"""Authentication provider base classes."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class AuthProvider(ABC):
    """Base class for authentication providers."""

    @abstractmethod
    async def authenticate(self, request: Any) -> dict[str, Any] | None:
        """Authenticate request, return user dict or None."""

    @abstractmethod
    async def logout(self, session_id: str) -> None:
        """Perform provider-specific logout cleanup."""


__all__ = ["AuthProvider"]
