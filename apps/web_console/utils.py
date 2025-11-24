"""Shared utilities for web console authentication.

This module provides shared utilities for extracting client information
from requests in both Streamlit and FastAPI contexts.
"""

import logging
import os
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

# Trusted proxy IPs (same as config.TRUSTED_PROXY_IPS)
# Reloaded here to avoid circular imports
_TRUSTED_PROXY_IPS: set[str] = {
    ip.strip() for ip in os.getenv("TRUSTED_PROXY_IPS", "").split(",") if ip.strip()
}


def extract_client_ip_from_fastapi(request: Any, get_remote_addr: Callable[[], str]) -> str:
    """Extract client IP from FastAPI request with trusted proxy validation.

    Security Note:
        X-Real-IP and X-Forwarded-For headers can be trivially spoofed if not
        behind a trusted proxy. This function:
        1. Validates request comes from trusted proxy (remote_addr check)
        2. Only uses X-Real-IP/X-Forwarded-For if from trusted proxy
        3. Falls back to remote_addr if not trusted or headers missing

    Args:
        request: FastAPI Request object
        get_remote_addr: Function that returns remote_addr (immediate upstream)

    Returns:
        str: Client IP address (real client if from trusted proxy, else remote_addr)
    """
    # Get remote_addr (immediate upstream caller - nginx proxy IP in production)
    remote_addr = get_remote_addr()

    # If no trusted proxies configured, use remote_addr directly
    if not _TRUSTED_PROXY_IPS:
        logger.debug(f"No TRUSTED_PROXY_IPS configured, using remote_addr: {remote_addr}")
        return remote_addr

    # Validate request comes from trusted proxy
    if remote_addr not in _TRUSTED_PROXY_IPS:
        logger.warning(
            f"Request from untrusted source {remote_addr} (not in TRUSTED_PROXY_IPS). "
            f"Ignoring X-Real-IP/X-Forwarded-For to prevent IP spoofing. Using remote_addr."
        )
        return remote_addr

    # Request is from trusted proxy - honor X-Real-IP header
    x_real_ip = request.headers.get("X-Real-IP", "")
    if x_real_ip:
        logger.debug(
            f"Extracted client IP from X-Real-IP: {x_real_ip} "
            f"(verified from trusted proxy {remote_addr})"
        )
        return x_real_ip  # type: ignore[no-any-return]

    # Fallback: X-Forwarded-For (take leftmost IP)
    x_forwarded_for = request.headers.get("X-Forwarded-For", "")
    if x_forwarded_for:
        client_ip = x_forwarded_for.split(",")[0].strip()
        if client_ip:
            logger.debug(
                f"Extracted client IP from X-Forwarded-For: {client_ip} "
                f"(verified from trusted proxy {remote_addr})"
            )
            return client_ip  # type: ignore[no-any-return]

    # No headers found - use remote_addr
    logger.debug(f"No X-Real-IP or X-Forwarded-For headers, using remote_addr: {remote_addr}")
    return remote_addr


def extract_user_agent_from_fastapi(request: Any) -> str:
    """Extract User-Agent from FastAPI request.

    Args:
        request: FastAPI Request object

    Returns:
        str: User-Agent header value or "unknown"
    """
    return request.headers.get("User-Agent", "unknown")  # type: ignore[no-any-return]
