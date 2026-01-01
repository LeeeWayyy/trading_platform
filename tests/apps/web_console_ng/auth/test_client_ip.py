from __future__ import annotations

import ipaddress
from unittest.mock import MagicMock

from apps.web_console_ng.auth.client_ip import extract_trusted_client_ip


def test_extract_ip_no_headers() -> None:
    request = MagicMock()
    request.client.host = "1.2.3.4"
    request.headers = {}

    ip = extract_trusted_client_ip(request, [])
    assert ip == "1.2.3.4"


def test_extract_ip_trusted_proxy_single() -> None:
    request = MagicMock()
    request.client.host = "10.0.0.1"  # Trusted proxy
    request.headers = {"X-Forwarded-For": "5.6.7.8, 10.0.0.1"}

    trusted = [ipaddress.ip_network("10.0.0.0/8")]
    ip = extract_trusted_client_ip(request, trusted)
    assert ip == "5.6.7.8"


def test_extract_ip_trusted_proxy_chain() -> None:
    request = MagicMock()
    request.client.host = "10.0.0.1"  # LB
    request.headers = {"X-Forwarded-For": "1.2.3.4, 192.168.1.1, 10.0.0.1"}

    # 10.0.0.1 is trusted, 192.168.1.1 is trusted (internal), 1.2.3.4 is real client
    trusted = [
        ipaddress.ip_network("10.0.0.0/8"),
        ipaddress.ip_network("192.168.0.0/16")
    ]

    ip = extract_trusted_client_ip(request, trusted)
    assert ip == "1.2.3.4"


def test_extract_ip_untrusted_direct_connection() -> None:
    request = MagicMock()
    request.client.host = "1.2.3.4" # Untrusted source
    request.headers = {"X-Forwarded-For": "9.9.9.9"} # Spoofed header

    trusted = [ipaddress.ip_network("10.0.0.0/8")]
    ip = extract_trusted_client_ip(request, trusted)

    # Should ignore header because source is untrusted
    assert ip == "1.2.3.4"


def test_extract_ip_malformed_header() -> None:
    request = MagicMock()
    request.client.host = "10.0.0.1"
    request.headers = {"X-Forwarded-For": "invalid-ip"}

    trusted = [ipaddress.ip_network("10.0.0.0/8")]
    ip = extract_trusted_client_ip(request, trusted)

    # Should fall back to last trusted or remote addr if parsing fails
    # Our logic returns remote_addr if traversal fails to find valid untrusted IP
    # Actually logic might return "invalid-ip" if it doesn't parse?
    # The code `ipaddress.ip_address(ip_str)` raises ValueError.
    # The loop `except ValueError: continue`.
    # So it should fall through to remote_addr.
    assert ip == "10.0.0.1"
