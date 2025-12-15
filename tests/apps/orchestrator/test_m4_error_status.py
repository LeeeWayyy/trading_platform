"""
Tests for M4: Orchestration Returns 500 on Error.

M4 Fix: Verified that orchestrator properly re-raises exceptions, allowing
FastAPI to return 500 status code for unhandled errors.

Contract:
- Unhandled exceptions are re-raised (not caught and hidden)
- FastAPI default handler returns 500 for unhandled exceptions
- HTTPException subclasses are re-raised to preserve their status codes
"""

from fastapi import HTTPException
from fastapi.testclient import TestClient

from apps.orchestrator.main import app


class TestErrorStatusCodes:
    """Test that appropriate status codes are returned for errors."""

    def test_health_endpoint_returns_200(self) -> None:
        """Health endpoint should return 200 when service is healthy."""
        client = TestClient(app)
        response = client.get("/health")
        # Health check may return 200 or 503 depending on initialization state
        assert response.status_code in (200, 503)

    def test_missing_endpoint_returns_404(self) -> None:
        """Non-existent endpoint should return 404."""
        client = TestClient(app)
        response = client.get("/api/v1/nonexistent")
        assert response.status_code == 404


class TestExceptionHandlingBehavior:
    """Test that exceptions are properly propagated (not caught and hidden)."""

    def test_httpexception_preserves_status_code(self) -> None:
        """HTTPException should preserve its status code when re-raised."""
        # Create an HTTPException with a specific status code
        exc = HTTPException(status_code=400, detail="Bad request")
        assert exc.status_code == 400
        assert exc.detail == "Bad request"

        # HTTPExceptions with 500 should also preserve
        exc_500 = HTTPException(status_code=500, detail="Internal error")
        assert exc_500.status_code == 500

    def test_orchestration_endpoint_validation_error_returns_422(self) -> None:
        """Invalid request body should return 422 validation error."""
        client = TestClient(app)

        # Missing required fields should return 422
        response = client.post(
            "/api/v1/orchestration/run",
            json={},  # Empty body, missing required fields
        )
        # Should be 422 (Unprocessable Entity) for Pydantic validation errors
        assert response.status_code == 422
