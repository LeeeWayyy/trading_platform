from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from nicegui import app

from apps.web_console_ng import config
from apps.web_console_ng.auth.auth_result import AuthResult
from apps.web_console_ng.auth.client_ip import extract_trusted_client_ip, is_trusted_ip
from apps.web_console_ng.auth.providers.base import AuthProvider
from apps.web_console_ng.auth.session_store import get_session_store

logger = logging.getLogger(__name__)


class MTLSAuthHandler(AuthProvider):
    """Mutual TLS authentication handler.

    Rely on nginx/proxy to validate certificate and pass details in headers.
    Headers:
        X-SSL-Client-Verify: SUCCESS
        X-SSL-Client-DN: /CN=user/OU=admin...
        X-SSL-Client-Not-After: Certificate expiry date (optional, for warnings)
    """

    async def try_auto_login(self, request: Any) -> AuthResult:
        """Attempt mTLS auto-login from request headers.

        This method encapsulates all mTLS header parsing and validation logic.
        Callers should simply pass the request object; the handler handles
        all proxy trust validation and certificate verification.

        Args:
            request: Starlette/NiceGUI Request object

        Returns:
            AuthResult with success=True if auto-login succeeded, or
            success=False with error_message explaining why it failed.
            On success, warning_message may contain certificate expiry warnings.
        """
        if config.AUTH_TYPE != "mtls":
            return AuthResult(success=False, error_message="mTLS auth not enabled")

        # Validate proxy trust (only accept headers from trusted IPs)
        if not self._is_trusted_proxy(request):
            return AuthResult(
                success=False,
                error_message="Client certificate required for mTLS authentication.",
            )

        # Check certificate verification status from proxy
        verify = request.headers.get("X-SSL-Client-Verify", "")
        cert_dn = request.headers.get("X-SSL-Client-DN", "")

        if not verify or verify != "SUCCESS":
            if verify and verify != "SUCCESS":
                return AuthResult(
                    success=False,
                    error_message=f"Certificate verification failed: {verify}",
                )
            return AuthResult(
                success=False,
                error_message="Client certificate required for mTLS authentication.",
            )

        if not cert_dn:
            return AuthResult(
                success=False,
                error_message="Client certificate required for mTLS authentication.",
            )

        # Check certificate expiry warning
        warning_message = self._check_certificate_expiry(request)

        # Extract client info for session creation
        client_ip = extract_trusted_client_ip(request, config.TRUSTED_PROXY_IPS)
        user_agent = request.headers.get("user-agent", "")

        # Delegate to authenticate() for actual session creation
        result = await self.authenticate(
            client_dn=cert_dn,
            client_ip=client_ip,
            user_agent=user_agent,
            request=request,
        )

        # Add warning message to result if present
        if result.success and warning_message:
            result.warning_message = warning_message

        return result

    def _check_certificate_expiry(self, request: Any) -> str | None:
        """Check certificate expiry and return warning message if expiring soon."""
        cert_not_after = request.headers.get("X-SSL-Client-Not-After", "")
        if not cert_not_after:
            return None

        try:
            # Parse nginx date format: "Dec 31 23:59:59 2025 GMT"
            expiry = datetime.strptime(cert_not_after, "%b %d %H:%M:%S %Y %Z")
            expiry = expiry.replace(tzinfo=UTC)
            days_until_expiry = (expiry - datetime.now(UTC)).days

            if days_until_expiry <= 0:
                return "Your certificate has expired. Please renew it immediately."
            elif days_until_expiry < 30:
                return (
                    f"Your certificate expires in {days_until_expiry} days. "
                    "Please renew it soon."
                )
        except ValueError:
            pass  # Ignore parse errors

        return None

    async def authenticate(self, client_dn: str | None = None, **kwargs: Any) -> AuthResult:
        """Authenticate using mTLS headers.

        Note: This is usually called automatically by middleware, not via login form.
        """
        if config.AUTH_TYPE != "mtls":
            return AuthResult(success=False, error_message="mTLS auth not enabled")

        # Get request from args or context
        request = kwargs.get("request")
        if not request and hasattr(app.storage, "request"):
            request = app.storage.request

        if not request:
            return AuthResult(success=False, error_message="No request context")

        # Validate proxy trust (only accept headers from trusted IPs)
        if not self._is_trusted_proxy(request):
            return AuthResult(success=False, error_message="Untrusted source")

        # Check headers (allow explicit override for tests/proxies)
        verify = request.headers.get("X-SSL-Client-Verify")
        dn = client_dn or kwargs.get("client_dn") or request.headers.get("X-SSL-Client-DN")
        serial = request.headers.get("X-SSL-Client-Serial")

        if verify != "SUCCESS":
            return AuthResult(success=False, error_message="Client certificate not verified")

        if not dn:
            return AuthResult(success=False, error_message="Missing client DN")

        # Extract user info from DN
        # Example DN: /CN=admin-user/OU=admin/O=TradingPlatform
        user_data = self._parse_dn(dn)
        if not user_data:
            return AuthResult(success=False, error_message="Invalid certificate DN format")

        session_store = get_session_store()
        user_data["auth_method"] = "mtls"
        user_data["client_dn"] = dn
        if serial:
            user_data["client_serial"] = serial

        cookie_value, csrf_token = await session_store.create_session(
            user_data=user_data,
            device_info={"user_agent": kwargs.get("user_agent", "")},
            client_ip=kwargs.get("client_ip", "127.0.0.1"),
        )

        return AuthResult(
            success=True,
            cookie_value=cookie_value,
            csrf_token=csrf_token,
            user_data=user_data,
        )

    def _is_trusted_proxy(self, request: Any) -> bool:
        """Check if request originates from a trusted proxy."""
        remote_addr = request.client.host if request.client else "0.0.0.0"
        return is_trusted_ip(remote_addr)

    def _parse_dn(self, dn: str) -> dict[str, Any] | None:
        """Parse Distinguished Name string to user data dict."""
        try:
            # Simple parsing - split by / or , depending on format
            # This logic needs to match your certificate generation strategy
            parts = [p for p in dn.split("/") if p.strip()]
            data = {}
            for part in parts:
                if "=" in part:
                    k, v = part.split("=", 1)
                    data[k] = v

            username = data.get("CN")
            role_ou = data.get("OU", "viewer")

            # Map OU to role
            role = "viewer"
            if role_ou == "admin":
                role = "admin"
            elif role_ou == "trader":
                role = "trader"

            if not username:
                return None

            return {
                "user_id": username,
                "username": username,
                "role": role,
                "strategies": [],  # Strategy assignment managed elsewhere or via extra attributes
            }
        except (ValueError, KeyError) as exc:
            # ValueError: Invalid DN format (e.g., malformed component)
            # KeyError: Missing expected field (e.g., no CN)
            logger.warning(
                "Failed to parse DN",
                extra={"dn": dn, "error_type": type(exc).__name__, "error": str(exc)},
            )
            return None
