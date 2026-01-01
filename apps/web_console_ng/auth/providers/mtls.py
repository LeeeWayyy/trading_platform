from __future__ import annotations

import ipaddress
import logging
from typing import Any

from nicegui import app

from apps.web_console_ng import config
from apps.web_console_ng.auth.auth_result import AuthResult
from apps.web_console_ng.auth.providers.base import AuthProvider
from apps.web_console_ng.auth.session_store import get_session_store

logger = logging.getLogger(__name__)


class MTLSAuthHandler(AuthProvider):
    """Mutual TLS authentication handler.

    Rely on nginx/proxy to validate certificate and pass details in headers.
    Headers:
        X-SSL-Client-Verify: SUCCESS
        X-SSL-Client-DN: /CN=user/OU=admin...
    """

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
        remote_addr = request.client.host if request.client else "0.0.0.0"
        try:
            ip = ipaddress.ip_address(remote_addr)
        except ValueError:
            return False

        for proxy in config.TRUSTED_PROXY_IPS:
            if isinstance(proxy, ipaddress.IPv4Network | ipaddress.IPv6Network):
                if ip in proxy:
                    return True
            elif ip == proxy:
                return True

        return False

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
        except Exception:
            logger.warning("Failed to parse DN: %s", dn)
            return None
