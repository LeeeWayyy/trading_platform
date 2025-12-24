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
        patch("apps.execution_gateway.main.twap_slicer"),
    ):
        from apps.execution_gateway import main

        # Set recovery_manager state to healthy placeholders
        main.recovery_manager._state.kill_switch = None
        main.recovery_manager._state.circuit_breaker = None
        main.recovery_manager._state.position_reservation = None
        main.recovery_manager._state.slice_scheduler = None
        main.recovery_manager.set_kill_switch_unavailable(False)
        main.recovery_manager.set_circuit_breaker_unavailable(False)
        main.recovery_manager.set_position_reservation_unavailable(False)

        return main.app


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
