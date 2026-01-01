from __future__ import annotations

import logging
from typing import Any

from apps.web_console_ng import config
from apps.web_console_ng.auth.auth_result import AuthResult
from apps.web_console_ng.auth.providers.base import AuthProvider

# In a real implementation, this would call a backend service or database.
# For now, we reuse the DEV_USERS map but treat it as a backend validation in dev.
# In production, basic auth should be disabled or backed by an external auth service.
from apps.web_console_ng.auth.providers.dev import DEV_USERS
from apps.web_console_ng.auth.rate_limiter import AuthRateLimiter
from apps.web_console_ng.auth.session_store import get_session_store

logger = logging.getLogger(__name__)


class BasicAuthHandler(AuthProvider):
    """Basic username/password authentication handler."""

    def __init__(self) -> None:
        self._rate_limiter = AuthRateLimiter()

    async def authenticate(self, **kwargs: Any) -> AuthResult:
        """Authenticate using username/password against backend.

        Args:
            username (str): Username to check.
            password (str): Password to check.
            client_ip (str): Client IP address.
            user_agent (str): User agent string.
        """
        if config.AUTH_TYPE != "basic":
            return AuthResult(success=False, error_message="Basic auth not enabled")
        if not config.ALLOW_DEV_BASIC_AUTH:
            return AuthResult(
                success=False,
                error_message="Basic auth dev credentials are disabled",
            )

        username = kwargs.get("username", "")
        password = kwargs.get("password", "")
        client_ip = kwargs.get("client_ip", "127.0.0.1")

        # Check rate limits BEFORE attempting authentication
        is_blocked, retry_after, reason = await self._rate_limiter.check_only(client_ip, username)
        if is_blocked:
            if reason == "account_locked":
                return AuthResult(
                    success=False,
                    error_message="Account temporarily locked",
                    locked_out=True,
                    lockout_remaining=retry_after,
                )
            return AuthResult(
                success=False,
                error_message="Too many attempts",
                rate_limited=True,
                retry_after=retry_after,
            )

        # TODO: Replace with actual backend API call
        # user = await backend_client.validate_credentials(username, password)
        user = DEV_USERS.get(username)

        if not user or user["password"] != password:
            # Record failure and check if now locked
            is_allowed, retry_after, reason = await self._rate_limiter.record_failure(
                client_ip, username
            )
            if reason == "account_locked_now":
                return AuthResult(
                    success=False,
                    error_message="Account locked due to too many failed attempts",
                    locked_out=True,
                    lockout_remaining=retry_after,
                )
            return AuthResult(success=False, error_message="Invalid credentials")

        # Clear failure count on successful login
        await self._rate_limiter.clear_on_success(username)

        session_store = get_session_store()
        user_data = {
            "user_id": username,
            "username": username,
            "role": user["role"],
            "strategies": user["strategies"],
            "auth_method": "basic",
        }

        cookie_value, csrf_token = await session_store.create_session(
            user_data=user_data,
            device_info={"user_agent": kwargs.get("user_agent", "")},
            client_ip=client_ip,
        )

        return AuthResult(
            success=True,
            cookie_value=cookie_value,
            csrf_token=csrf_token,
            user_data=user_data,
            requires_mfa=False,  # TODO: Check user MFA preference
        )
