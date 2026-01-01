from __future__ import annotations

import ipaddress
import logging

from starlette.requests import HTTPConnection

from apps.web_console_ng import config

logger = logging.getLogger(__name__)

# Type alias for trusted proxies (IP or Network)
TrustedProxy = (
    ipaddress.IPv4Network | ipaddress.IPv6Network | ipaddress.IPv4Address | ipaddress.IPv6Address
)


def get_client_ip(
    request: HTTPConnection, trusted_proxies: list[TrustedProxy] | None = None
) -> str:
    """Extract client IP, trusting X-Forwarded-For only from trusted proxies.

    Wrapper around extract_trusted_client_ip for compatibility with middleware.

    Args:
        request: The Starlette request object.
        trusted_proxies: List of trusted IP/Network objects. If None, uses config.TRUSTED_PROXY_IPS.

    Returns:
        The client IP address as a string.
    """
    proxies = trusted_proxies if trusted_proxies is not None else config.TRUSTED_PROXY_IPS
    return extract_trusted_client_ip(request, proxies)


def extract_trusted_client_ip(request: HTTPConnection, trusted_proxies: list[TrustedProxy]) -> str:
    """Extract client IP, trusting X-Forwarded-For only from trusted proxies.

    Args:
        request: The Starlette request object.
        trusted_proxies: List of ipaddress.IPv4Network/IPv6Network/IPv4Address/IPv6Address objects.

    Returns:
        The client IP address as a string.
    """
    remote_addr = request.client.host if request.client else "0.0.0.0"

    # If direct connection isn't trusted, return it directly
    is_trusted = False
    try:
        ip = ipaddress.ip_address(remote_addr)
        for proxy in trusted_proxies:
            if isinstance(proxy, ipaddress.IPv4Network | ipaddress.IPv6Network):
                if ip in proxy:
                    is_trusted = True
                    break
            elif ip == proxy:
                is_trusted = True
                break
    except ValueError:
        pass

    if not is_trusted:
        return remote_addr

    # If trusted, check X-Forwarded-For
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        # X-Forwarded-For: client, proxy1, proxy2
        # We traverse from right to left (nearest proxy)
        ips = [ip.strip() for ip in xff.split(",")]

        # Start with the remote_addr (already verified as trusted)
        # We walk backwards through the chain.
        # If the current IP is trusted, we accept the *next* one to the left as potentially valid.
        # Once we hit an untrusted IP, or run out of trusted proxies, that's the client IP.

        for ip_str in reversed(ips):
            try:
                ip = ipaddress.ip_address(ip_str)
                ip_is_trusted = False
                for proxy in trusted_proxies:
                    if isinstance(proxy, ipaddress.IPv4Network | ipaddress.IPv6Network):
                        if ip in proxy:
                            ip_is_trusted = True
                            break
                    elif ip == proxy:
                        ip_is_trusted = True
                        break

                if not ip_is_trusted:
                    return ip_str
            except ValueError:
                continue

    return remote_addr
