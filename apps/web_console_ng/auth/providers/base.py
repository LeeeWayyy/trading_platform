from abc import ABC, abstractmethod
from typing import Any

from apps.web_console_ng.auth.auth_result import AuthResult


class AuthProvider(ABC):
    """Abstract base class for authentication providers."""

    @abstractmethod
    async def authenticate(self, **kwargs: Any) -> AuthResult:
        """Authenticate user based on provided credentials.

        Args:
            **kwargs: Authentication parameters (username, password, request, etc.)

        Returns:
            AuthResult: Result of the authentication attempt.
        """
        pass  # pragma: no cover

    async def get_authorization_url(self) -> str:
        """Get authorization URL for external providers (OAuth2)."""
        raise NotImplementedError("This provider does not support authorization URL generation.")

    async def handle_callback(self, code: str, state: str, **kwargs: Any) -> AuthResult:
        """Handle authentication callback (OAuth2)."""
        raise NotImplementedError("This provider does not support callbacks.")
