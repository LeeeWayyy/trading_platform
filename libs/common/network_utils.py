"""Network utility functions for client IP extraction and proxy validation.

This module provides shared utilities for extracting client information from
requests with trusted proxy validation. Used by both auth_service (FastAPI)
and web_console (Streamlit) to ensure consistent security enforcement.

Moved from apps/web_console/utils.py to resolve architectural layering violation:
Core backend services (auth_service) should not depend on frontend applications (web_console).
"""

import logging
import os
from collections.abc import Callable

from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)


def validate_trusted_proxy(request: Request, get_remote_addr: Callable[[], str]) -> None:
    """Validate request comes from trusted proxy.

    Prevents X-Forwarded-For header spoofing by checking the immediate
    peer IP address against TRUSTED_PROXY_IPS environment variable.

    NOTE: This is application-level validation. Nginx-level validation
    via real_ip_from directive is the PRIMARY defense (see nginx-oauth2.conf).
    This function provides defense-in-depth.

    Args:
        request: FastAPI request object
        get_remote_addr: Callable returning immediate peer IP (request.client.host)

    Raises:
        HTTPException: 403 Forbidden if request not from trusted proxy

    Example:
        def get_remote_addr():
            return request.client.host if request.client else "unknown"

        validate_trusted_proxy(request, get_remote_addr)
    """
    # Get trusted proxy IPs from environment (comma-separated)
    trusted_proxies_str = os.getenv("TRUSTED_PROXY_IPS", "")

    if not trusted_proxies_str:
        # No trusted proxies configured - allow all (development mode)
        logger.warning(
            "TRUSTED_PROXY_IPS not set - accepting all requests (INSECURE)",
            extra={"remote_addr": get_remote_addr()},
        )
        return

    trusted_proxies = [ip.strip() for ip in trusted_proxies_str.split(",")]
    remote_addr = get_remote_addr()

    if remote_addr not in trusted_proxies:
        logger.error(
            "Request from untrusted proxy blocked",
            extra={
                "remote_addr": remote_addr,
                "trusted_proxies": trusted_proxies,
                "x_forwarded_for": request.headers.get("X-Forwarded-For"),
            },
        )
        raise HTTPException(
            status_code=403,
            detail="Forbidden: Request not from trusted proxy",
        )

    logger.debug(
        "Trusted proxy validation passed",
        extra={"remote_addr": remote_addr},
    )


def extract_client_ip_from_fastapi(
    request: Request,
    get_remote_addr: Callable[[], str],
) -> str:
    """Extract client IP from FastAPI request with trusted proxy validation.

    UPDATED for Component 5: Now validates trusted proxy before using X-Forwarded-For.

    NOTE: Nginx real_ip_from directive provides Nginx-level validation.
    This function provides application-level defense-in-depth.

    Order of precedence:
    1. Validate request.client.host against TRUSTED_PROXY_IPS
    2. If trusted, use X-Forwarded-For (first IP = original client)
    3. If not trusted or no X-Forwarded-For, use request.client.host

    Args:
        request: FastAPI request object
        get_remote_addr: Callable returning request.client.host

    Returns:
        Client IP address (original client, not proxy)
    """
    # Get immediate peer IP (the proxy)
    remote_addr = get_remote_addr()

    # Check if request is from trusted proxy
    trusted_proxies_str = os.getenv("TRUSTED_PROXY_IPS", "")

    if not trusted_proxies_str:
        # No trusted proxies - use remote_addr directly (development mode)
        logger.debug(
            "No trusted proxies configured, using remote_addr",
            extra={"remote_addr": remote_addr},
        )
        return remote_addr

    trusted_proxies = [ip.strip() for ip in trusted_proxies_str.split(",")]

    if remote_addr not in trusted_proxies:
        # Request not from trusted proxy - use remote_addr
        # This prevents X-Forwarded-For spoofing
        logger.warning(
            "Ignoring X-Forwarded-For from untrusted proxy",
            extra={
                "remote_addr": remote_addr,
                "x_forwarded_for": request.headers.get("X-Forwarded-For"),
            },
        )
        return remote_addr

    # Request is from trusted proxy - use X-Forwarded-For
    x_forwarded_for = request.headers.get("X-Forwarded-For", "").strip()

    if x_forwarded_for:
        # X-Forwarded-For format: "client, proxy1, proxy2, ..."
        # First IP is the original client
        client_ip = x_forwarded_for.split(",")[0].strip()
        logger.debug(
            "Using X-Forwarded-For from trusted proxy",
            extra={
                "client_ip": client_ip,
                "proxy_ip": remote_addr,
                "x_forwarded_for": x_forwarded_for,
            },
        )
        return client_ip

    # No X-Forwarded-For header - use remote_addr
    logger.debug(
        "No X-Forwarded-For header, using remote_addr",
        extra={"remote_addr": remote_addr},
    )
    return remote_addr
