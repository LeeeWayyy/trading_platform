from __future__ import annotations

import logging
import os
from typing import Any

from apps.web_console_ng import config
from apps.web_console_ng.auth.auth_result import AuthResult
from apps.web_console_ng.auth.providers.base import AuthProvider
from apps.web_console_ng.auth.session_store import get_session_store

logger = logging.getLogger(__name__)


def _get_dev_user() -> dict[str, Any]:
    """Build dev user from environment variables.

    Uses WEB_CONSOLE_USER/PASSWORD from .env for credentials,
    and WEB_CONSOLE_DEV_ROLE/USER_ID/STRATEGIES for RBAC context.
    """
    username = os.getenv("WEB_CONSOLE_USER", "admin")
    password = os.getenv("WEB_CONSOLE_PASSWORD", "changeme")
    role = config.DEV_ROLE or "admin"
    user_id = config.DEV_USER_ID or username
    strategies = config.DEV_STRATEGIES or ["alpha_baseline"]

    return {
        "username": username,
        "password": password,
        "user_id": user_id,
        "role": role,
        "strategies": strategies,
    }


class DevAuthHandler(AuthProvider):
    """Development mode authentication handler.

    Authenticates using credentials from environment variables:
    - WEB_CONSOLE_USER: Username (default: admin)
    - WEB_CONSOLE_PASSWORD: Password (default: changeme)
    - WEB_CONSOLE_DEV_ROLE: Role for RBAC (default: admin)
    - WEB_CONSOLE_DEV_USER_ID: User ID (default: same as username)
    - WEB_CONSOLE_DEV_STRATEGIES: Comma-separated strategy IDs
    """

    async def authenticate(self, **kwargs: Any) -> AuthResult:
        """Authenticate using environment-configured dev user.

        Args:
            username (str): Username to check.
            password (str): Password to check.
            client_ip (str): Client IP address (optional).
            user_agent (str): User agent string (optional).
        """
        if config.AUTH_TYPE != "dev":
            return AuthResult(success=False, error_message="Dev auth not enabled")

        input_username = kwargs.get("username", "")
        input_password = kwargs.get("password", "")

        dev_user = _get_dev_user()

        logger.debug(
            "dev_auth_attempt",
            extra={
                "input_username": input_username,
                "expected_username": dev_user["username"],
                "client_ip": kwargs.get("client_ip", "unknown"),
            },
        )

        # Check credentials against env vars
        if input_username != dev_user["username"] or input_password != dev_user["password"]:
            logger.warning(
                "dev_auth_failed",
                extra={
                    "input_username": input_username,
                    "expected_username": dev_user["username"],
                    "client_ip": kwargs.get("client_ip", "unknown"),
                },
            )
            return AuthResult(success=False, error_message="Invalid credentials")

        # Create session
        try:
            session_store = get_session_store()
            user_data = {
                "user_id": dev_user["user_id"],
                "username": dev_user["username"],
                "role": dev_user["role"],
                "strategies": dev_user["strategies"],
                "auth_method": "dev",
            }

            cookie_value, csrf_token = await session_store.create_session(
                user_data=user_data,
                device_info={"user_agent": kwargs.get("user_agent", "dev-browser")},
                client_ip=kwargs.get("client_ip", "127.0.0.1"),
            )

            logger.info(
                "dev_auth_success",
                extra={
                    "user_id": dev_user["user_id"],
                    "role": dev_user["role"],
                    "client_ip": kwargs.get("client_ip", "unknown"),
                },
            )

            return AuthResult(
                success=True,
                cookie_value=cookie_value,
                csrf_token=csrf_token,
                user_data=user_data,
                requires_mfa=False,
            )
        except Exception as e:
            logger.exception("dev_auth_session_error")
            return AuthResult(success=False, error_message=f"Session creation failed: {e}")
