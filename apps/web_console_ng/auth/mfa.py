from __future__ import annotations

import logging

import pyotp  # type: ignore[import-not-found]

from apps.web_console_ng.auth.auth_result import AuthResult
from apps.web_console_ng.auth.rate_limiter import AuthRateLimiter
from apps.web_console_ng.auth.session_store import get_session_store

logger = logging.getLogger(__name__)


class MFAHandler:
    """Multi-Factor Authentication Handler."""

    def __init__(self) -> None:
        self.session_store = get_session_store()
        self.rate_limiter = AuthRateLimiter()

    async def verify(
        self,
        pending_cookie: str,
        code: str,
        client_ip: str = "127.0.0.1",
        user_agent: str = "",
    ) -> AuthResult:
        """Verify TOTP code for a pending session.

        Args:
            pending_cookie: The cookie value from the initial login (containing session_id).
            code: The TOTP code provided by the user.
            client_ip: Client IP address for session validation.
            user_agent: User agent string for session validation.

        Returns:
            AuthResult: Success if code is valid, Failure otherwise.
        """
        # Validate the pending session with proper device context
        session = await self.session_store.validate_session(
            pending_cookie,
            client_ip=client_ip,
            user_agent=user_agent,
        )

        if not session:
            return AuthResult(success=False, error_message="Session expired or invalid")

        session_id = self.session_store.verify_cookie(pending_cookie)
        if not session_id:
            return AuthResult(success=False, error_message="Session expired or invalid")

        user_data = session.get("user", {})
        # Check if this session is actually pending MFA
        # (This flag should have been set during login)
        if not user_data.get("mfa_pending"):
            return AuthResult(
                success=False,
                error_message="MFA not required for this session",
            )

        limiter_key = user_data.get("user_id") or session_id
        is_blocked, retry_after, reason = await self.rate_limiter.check_only(
            client_ip, limiter_key
        )
        if is_blocked:
            if reason == "account_locked":
                return AuthResult(
                    success=False,
                    error_message="MFA temporarily locked due to failed attempts",
                    locked_out=True,
                    lockout_remaining=retry_after,
                )
            return AuthResult(
                success=False,
                error_message="Too many MFA attempts",
                rate_limited=True,
                retry_after=retry_after,
            )

        # Get TOTP secret from user profile (mocked for now)
        # In production, fetch from DB using user_data['user_id']
        secret = self._get_totp_secret(user_data.get("user_id"))

        if not secret:
            # If no secret setup but MFA required, maybe allow setup?
            # For now, fail.
            return AuthResult(success=False, error_message="MFA not set up for user")

        totp = pyotp.TOTP(secret)
        if totp.verify(code):
            # Promote session to fully authenticated by rotating session ID
            # This prevents session fixation and generates a fresh CSRF token
            # Rotate session and clear mfa_pending in persisted session
            rotation_result = await self.session_store.rotate_session(
                session_id,
                user_updates={"mfa_pending": False},
            )
            if not rotation_result:
                return AuthResult(success=False, error_message="Session rotation failed")

            new_cookie_value, csrf_token = rotation_result

            # Also update local user_data for consistency
            user_data["mfa_pending"] = False
            await self.rate_limiter.clear_on_success(limiter_key)

            return AuthResult(
                success=True,
                cookie_value=new_cookie_value,
                csrf_token=csrf_token,
                user_data=user_data,
            )

        is_allowed, retry_after, reason = await self.rate_limiter.record_failure(
            client_ip, limiter_key
        )
        if reason == "account_locked_now":
            return AuthResult(
                success=False,
                error_message="MFA locked due to repeated failures",
                locked_out=True,
                lockout_remaining=retry_after,
            )
        if not is_allowed:
            return AuthResult(
                success=False,
                error_message="Too many MFA attempts",
                rate_limited=True,
                retry_after=retry_after,
            )

        return AuthResult(success=False, error_message="Invalid authentication code")

    def _get_totp_secret(self, user_id: str | None) -> str | None:
        """Retrieve TOTP secret for user.

        In production, this should fetch from a secure database.
        Test secrets are only available when AUTH_TYPE is 'dev'.
        """
        from apps.web_console_ng import config

        if not user_id:
            return None

        # Only use test secrets in dev mode
        if config.AUTH_TYPE == "dev":
            test_secrets = {
                "mfa": "JBSWY3DPEHPK3PXP",
                "admin": "JBSWY3DPEHPK3PXP",
            }
            return test_secrets.get(user_id)

        # TODO: In production, fetch from secure database
        # return await db.get_totp_secret(user_id)
        return None
