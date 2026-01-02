from __future__ import annotations

from typing import Any

from apps.web_console_ng import config
from apps.web_console_ng.auth.auth_result import AuthResult
from apps.web_console_ng.auth.providers.base import AuthProvider
from apps.web_console_ng.auth.session_store import get_session_store

# Test users for development
DEV_USERS = {
    "admin": {
        "password": "admin123",
        "role": "admin",
        "strategies": ["alpha_baseline", "momentum_v1"],
    },
    "trader": {
        "password": "trader123",
        "role": "trader",
        "strategies": ["alpha_baseline"],
    },
    "viewer": {
        "password": "viewer123",
        "role": "viewer",
        "strategies": [],
    },
    # User to test MFA flow simulation
    "mfa": {
        "password": "mfa123",
        "role": "admin",
        "strategies": ["*"],
    },
}


class DevAuthHandler(AuthProvider):
    """Development mode authentication handler."""

    async def authenticate(self, **kwargs: Any) -> AuthResult:
        """Authenticate using dev user fixtures.

        Args:
            username (str): Username to check.
            password (str): Password to check.
            client_ip (str): Client IP address (optional).
            user_agent (str): User agent string (optional).
        """
        if config.AUTH_TYPE != "dev":
            return AuthResult(success=False, error_message="Dev auth not enabled")

        username = kwargs.get("username", "")
        password = kwargs.get("password", "")

        user = DEV_USERS.get(username)
        if not user or user["password"] != password:
            return AuthResult(success=False, error_message="Invalid credentials")

        # Simulate MFA requirement for specific test user
        if username == "mfa":
            # For MFA flow, we create a temporary session with mfa_pending flag.
            # The login page stores cookie_value in pending_mfa_session for MFA verify.
            session_store = get_session_store()
            user_data = {
                "user_id": username,
                "username": username,
                "role": user["role"],
                "strategies": user["strategies"],
                "auth_method": "dev",
                "mfa_pending": True,
            }
            cookie_value, csrf_token = await session_store.create_session(
                user_data=user_data,
                device_info={"user_agent": kwargs.get("user_agent", "dev-browser")},
                client_ip=kwargs.get("client_ip", "127.0.0.1"),
            )
            return AuthResult(
                success=True,
                cookie_value=cookie_value,
                csrf_token=csrf_token,
                user_data=user_data,
                requires_mfa=True,
            )

        # Standard login
        session_store = get_session_store()
        user_data = {
            "user_id": username,
            "username": username,
            "role": user["role"],
            "strategies": user["strategies"],
            "auth_method": "dev",
        }

        cookie_value, csrf_token = await session_store.create_session(
            user_data=user_data,
            device_info={"user_agent": kwargs.get("user_agent", "dev-browser")},
            client_ip=kwargs.get("client_ip", "127.0.0.1"),
        )

        return AuthResult(
            success=True,
            cookie_value=cookie_value,
            csrf_token=csrf_token,
            user_data=user_data,
            requires_mfa=False,
        )
