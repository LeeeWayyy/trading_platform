"""Tests for CSP violation reporting endpoint."""

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from apps.auth_service.main import app


@pytest.fixture()
def client():
    """Create test client."""
    return TestClient(app)


def test_csp_report_endpoint_accepts_valid_report(client):
    """Test /csp-report accepts valid CSP violation report."""
    violation_report = {
        "csp-report": {
            "document-uri": "https://localhost/dashboard",
            "violated-directive": "script-src 'self'",
            "effective-directive": "script-src",
            "original-policy": "default-src 'self'; script-src 'self' 'nonce-abc123'",
            "blocked-uri": "https://evil.com/malicious.js",
            "status-code": 200,
            "referrer": "",
            "source-file": "https://localhost/dashboard",
            "line-number": 42,
            "column-number": 15,
            "sample": "<script src='https://evil.com/malicious.js'></script>",
        }
    }

    response = client.post("/csp-report", json=violation_report)

    assert response.status_code == 200
    assert response.json()["status"] == "received"


def test_csp_report_endpoint_logs_xss_attempts(client, caplog):
    """Test /csp-report logs potential XSS attacks."""
    violation_report = {
        "csp-report": {
            "document-uri": "https://localhost/dashboard",
            "violated-directive": "script-src 'self'",
            "effective-directive": "script-src",
            "original-policy": "default-src 'self'; script-src 'self'",
            "blocked-uri": "javascript:alert(1)",  # XSS attempt
            "status-code": 200,
        }
    }

    response = client.post("/csp-report", json=violation_report)

    assert response.status_code == 200

    # Check logs for XSS warning
    assert any("POSSIBLE XSS ATTACK" in record.message for record in caplog.records)


def test_csp_report_endpoint_rejects_invalid_format(client):
    """Test /csp-report rejects invalid report format."""
    invalid_report = {
        "invalid-key": {
            "document-uri": "https://localhost/dashboard",
        }
    }

    response = client.post("/csp-report", json=invalid_report)

    # Should return 422 Unprocessable Entity (validation error)
    assert response.status_code == 422


def test_csp_report_endpoint_app_level_payload_size_check(client):
    """Test /csp-report app-level payload size guard.

    Addresses Codex Iteration 2 Issue #4: App-level defense-in-depth.
    Tests that endpoint validates actual body size (not just Content-Length header).
    """
    # Create payload exceeding 10KB
    large_payload = {
        "csp-report": {
            "document-uri": "https://localhost/dashboard",
            "violated-directive": "script-src 'self'",
            "effective-directive": "script-src",
            "original-policy": "default-src 'self'",
            "blocked-uri": "https://evil.com/script.js",
            "status-code": 200,
            "sample": "x" * 15000,  # 15KB sample (exceeds 10KB limit)
        }
    }

    # TestClient will serialize JSON and calculate actual body size
    # Endpoint should validate actual body size (defense against header spoofing)
    response = client.post(
        "/csp-report",
        json=large_payload,
    )

    # Should return 413 Payload Too Large (actual body size check)
    assert response.status_code == 413
    assert "too large" in response.json()["detail"].lower()


def test_csp_report_endpoint_handles_network_error_during_stream():
    """Test /csp-report handles OSError/IOError during body stream read.

    Tests specific exception handling for network errors.
    """
    from fastapi import Request

    from apps.auth_service.routes.csp_report import csp_report

    # Create mock request that raises OSError during stream()
    mock_request = AsyncMock(spec=Request)
    mock_request.headers.get.return_value = "100"  # Small Content-Length
    mock_request.client.host = "127.0.0.1"

    async def stream_with_error():
        raise OSError("Connection reset by peer")
        yield b"data"  # Never reached

    mock_request.stream.return_value = stream_with_error()

    # Should raise HTTPException with network error message
    import asyncio

    from fastapi import HTTPException

    with pytest.raises(HTTPException, match="Network error|request body"):
        asyncio.run(csp_report(mock_request))


def test_csp_report_endpoint_handles_unicode_decode_error():
    """Test /csp-report handles UnicodeDecodeError during body decode.

    Tests specific exception handling for invalid UTF-8 encoding.
    """
    # Create payload with invalid UTF-8 bytes
    # TestClient normalizes encoding, so we need to test at the route level
    from fastapi import Request

    from apps.auth_service.routes.csp_report import csp_report

    # Create mock request with invalid UTF-8 in body
    mock_request = AsyncMock(spec=Request)
    mock_request.headers.get.return_value = "50"  # Small Content-Length
    mock_request.client.host = "127.0.0.1"

    # Return invalid UTF-8 bytes
    invalid_utf8 = b"\xff\xfe"

    async def stream_invalid_utf8():
        yield invalid_utf8

    mock_request.stream.return_value = stream_invalid_utf8()

    # Should raise HTTPException with encoding error message
    import asyncio

    from fastapi import HTTPException

    with pytest.raises(HTTPException, match="Invalid request encoding|Invalid JSON"):
        asyncio.run(csp_report(mock_request))


def test_csp_report_endpoint_handles_json_decode_error(client):
    """Test /csp-report handles json.JSONDecodeError for malformed JSON.

    Tests specific exception handling for JSON parsing errors.
    """
    # Send malformed JSON (not valid JSON syntax)
    response = client.post(
        "/csp-report",
        data='{"csp-report": {invalid json}',  # Malformed JSON
        headers={"Content-Type": "application/json"},
    )

    # Should return 400 with JSON error message
    assert response.status_code == 400
    assert "Invalid JSON" in response.json()["detail"]


def test_csp_report_endpoint_handles_pydantic_validation_error(client):
    """Test /csp-report handles Pydantic ValidationError.

    Tests specific exception handling for Pydantic validation failures.
    """
    # Send valid JSON but invalid CSP report structure
    invalid_report = {
        "csp-report": {
            # Missing required fields
            "document-uri": "https://localhost/dashboard",
            # violated-directive is missing (required field)
        }
    }

    response = client.post("/csp-report", json=invalid_report)

    # Should return 422 with validation error message
    assert response.status_code == 422
    assert "Invalid CSP report format" in response.json()["detail"]
