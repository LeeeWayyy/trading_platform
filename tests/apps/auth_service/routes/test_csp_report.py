"""Tests for CSP violation reporting endpoint."""

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
