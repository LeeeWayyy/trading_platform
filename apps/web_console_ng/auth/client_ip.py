"""Client IP extraction with trusted proxy handling."""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable

from starlette.requests import Request

TrustedProxy = (
    ipaddress.IPv4Network
    | ipaddress.IPv6Network
    | ipaddress.IPv4Address
    | ipaddress.IPv6Address
)


def _is_trusted(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
    trusted_proxies: Iterable[TrustedProxy],
) -> bool:
    for proxy in trusted_proxies:
        if isinstance(proxy, ipaddress.IPv4Network | ipaddress.IPv6Network):
            if ip in proxy:
                return True
        elif isinstance(proxy, ipaddress.IPv4Address | ipaddress.IPv6Address):
            if ip == proxy:
                return True
    return False


def get_client_ip(request: Request, trusted_proxies: Iterable[TrustedProxy]) -> str:
    """Return the real client IP using trusted proxy rules.

    Parsing rules:
    - If the direct connection IP is not trusted, ignore X-Forwarded-For.
    - If trusted, parse X-Forwarded-For right-to-left and return the first
      untrusted IP.
    - If all entries are trusted or header missing, return connection IP.
    """

    connection_ip = request.client.host if request.client else ""
    if not connection_ip:
        return ""

    try:
        connection_addr = ipaddress.ip_address(connection_ip)
    except ValueError:
        return connection_ip

    trusted = list(trusted_proxies)
    if not trusted or not _is_trusted(connection_addr, trusted):
        return connection_ip

    forwarded = request.headers.get("x-forwarded-for")
    if not forwarded:
        return connection_ip

    parts = [part.strip() for part in forwarded.split(",") if part.strip()]
    for part in reversed(parts):
        try:
            addr = ipaddress.ip_address(part)
        except ValueError:
            continue
        if _is_trusted(addr, trusted):
            continue
        return part

    return connection_ip


__all__ = ["get_client_ip", "TrustedProxy"]
