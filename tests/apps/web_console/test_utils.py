"""Tests for trusted proxy validation."""

import pytest
from fastapi import HTTPException
from starlette.datastructures import Headers

from apps.web_console.utils import extract_client_ip_from_fastapi, validate_trusted_proxy


def test_validate_trusted_proxy_allows_trusted_ip(monkeypatch):
    """Test validate_trusted_proxy allows requests from trusted proxy."""
    monkeypatch.setenv("TRUSTED_PROXY_IPS", "172.28.0.10,172.28.0.11")

    # Mock request from trusted proxy
    class MockRequest:
        client = type("Client", (), {"host": "172.28.0.10"})()
        headers = Headers({})

    def get_remote_addr():
        return "172.28.0.10"

    request = MockRequest()

    # Should not raise exception
    validate_trusted_proxy(request, get_remote_addr)


def test_validate_trusted_proxy_blocks_untrusted_ip(monkeypatch):
    """Test validate_trusted_proxy blocks requests from untrusted IP."""
    monkeypatch.setenv("TRUSTED_PROXY_IPS", "172.28.0.10")

    # Mock request from untrusted IP
    class MockRequest:
        client = type("Client", (), {"host": "192.168.1.100"})()
        headers = Headers({"X-Forwarded-For": "10.0.0.1"})

    def get_remote_addr():
        return "192.168.1.100"

    request = MockRequest()

    # Should raise 403 Forbidden
    with pytest.raises(HTTPException) as exc_info:
        validate_trusted_proxy(request, get_remote_addr)

    assert exc_info.value.status_code == 403
    assert "not from trusted proxy" in exc_info.value.detail.lower()


def test_extract_client_ip_uses_x_forwarded_for_from_trusted_proxy(monkeypatch):
    """Test extract_client_ip uses X-Forwarded-For from trusted proxy."""
    monkeypatch.setenv("TRUSTED_PROXY_IPS", "172.28.0.10")

    # Mock request from trusted proxy with X-Forwarded-For
    class MockRequest:
        client = type("Client", (), {"host": "172.28.0.10"})()
        headers = Headers({"X-Forwarded-For": "203.0.113.45, 172.28.0.10"})

    def get_remote_addr():
        return "172.28.0.10"

    request = MockRequest()
    client_ip = extract_client_ip_from_fastapi(request, get_remote_addr)

    # Should extract first IP from X-Forwarded-For (original client)
    assert client_ip == "203.0.113.45"


def test_extract_client_ip_ignores_x_forwarded_for_from_untrusted_proxy(monkeypatch):
    """Test extract_client_ip ignores X-Forwarded-For from untrusted proxy."""
    monkeypatch.setenv("TRUSTED_PROXY_IPS", "172.28.0.10")

    # Mock request from UNTRUSTED proxy with X-Forwarded-For
    class MockRequest:
        client = type("Client", (), {"host": "192.168.1.100"})()
        headers = Headers({"X-Forwarded-For": "203.0.113.45"})

    def get_remote_addr():
        return "192.168.1.100"

    request = MockRequest()
    client_ip = extract_client_ip_from_fastapi(request, get_remote_addr)

    # Should ignore X-Forwarded-For and use remote_addr
    # This prevents IP spoofing attacks
    assert client_ip == "192.168.1.100"
