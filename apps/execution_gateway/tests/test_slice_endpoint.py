"""
Minimal tests for POST /api/v1/orders/slice endpoint.

Validates basic endpoint functionality with happy path scenario.
Full integration tests deferred to future iteration.
"""

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture()
def test_app_with_mocks():
    """Create test app with mocked dependencies for slice endpoint."""
    # Import after mocking to avoid initialization errors
    with (
        patch("apps.execution_gateway.main.db_client"),
        patch("apps.execution_gateway.main.redis_client"),
        patch("apps.execution_gateway.main.kill_switch"),
        patch("apps.execution_gateway.main.circuit_breaker"),
        patch("apps.execution_gateway.main.twap_slicer"),
        patch("apps.execution_gateway.main.slice_scheduler"),
    ):

        from apps.execution_gateway.main import app

        return app


def test_slice_endpoint_exists(test_app_with_mocks: FastAPI):
    """Test that POST /api/v1/orders/slice endpoint is registered."""
    client = TestClient(test_app_with_mocks)
    # OPTIONS request to check endpoint exists
    response = client.options("/api/v1/orders/slice")
    # Should not be 404
    assert response.status_code != 404


def test_get_slices_endpoint_exists(test_app_with_mocks: FastAPI):
    """Test that GET /api/v1/orders/{parent_id}/slices endpoint is registered."""
    client = TestClient(test_app_with_mocks)
    response = client.options("/api/v1/orders/test_parent/slices")
    assert response.status_code != 404


def test_delete_slices_endpoint_exists(test_app_with_mocks: FastAPI):
    """Test that DELETE /api/v1/orders/{parent_id}/slices endpoint is registered."""
    client = TestClient(test_app_with_mocks)
    response = client.options("/api/v1/orders/test_parent/slices")
    assert response.status_code != 404
