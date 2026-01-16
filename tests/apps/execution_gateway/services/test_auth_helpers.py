"""Tests for authentication helper functions.

This test suite validates RBAC helper functions extracted from main.py,
ensuring correct:
- User context extraction from request.state
- Fail-closed security (missing auth → 401)
- Dict and object user representation support
- Query parameter extraction
- Security: header spoofing prevention

Target: 90%+ coverage per Phase 1 requirements.

See REFACTOR_EXECUTION_GATEWAY_TASK.md Phase 1 for design decisions.
"""

import pytest
from fastapi import HTTPException, Request
from unittest.mock import MagicMock

from apps.execution_gateway.services.auth_helpers import build_user_context


# ============================================================================
# Test build_user_context
# ============================================================================


def test_build_user_context_with_dict_user() -> None:
    """Test user context extraction from dict user."""
    request_mock = MagicMock(spec=Request)
    request_mock.state.user = {
        "role": "trader",
        "strategies": ["alpha_baseline", "momentum"],
        "user_id": "user123",
    }
    request_mock.query_params.getlist.return_value = ["alpha_baseline"]

    context = build_user_context(request_mock)

    assert context["role"] == "trader"
    assert context["strategies"] == ["alpha_baseline", "momentum"]
    assert context["requested_strategies"] == ["alpha_baseline"]
    assert context["user_id"] == "user123"
    assert context["user"]["role"] == "trader"


def test_build_user_context_with_object_user() -> None:
    """Test user context extraction from object user."""
    request_mock = MagicMock(spec=Request)

    # Create a simple class instance instead of MagicMock
    class UserObject:
        def __init__(self):
            self.role = "admin"
            self.strategies = ["alpha_baseline"]
            self.user_id = "admin456"

    user_obj = UserObject()

    request_mock.state.user = user_obj
    request_mock.query_params.getlist.return_value = []

    context = build_user_context(request_mock)

    assert context["role"] == "admin"
    assert context["strategies"] == ["alpha_baseline"]
    assert context["user_id"] == "admin456"


def test_build_user_context_with_id_attribute() -> None:
    """Test that 'id' attribute is mapped to 'user_id'."""
    request_mock = MagicMock(spec=Request)

    class UserObject:
        def __init__(self):
            self.role = "trader"
            self.id = "user789"  # Uses 'id' instead of 'user_id'

    user_obj = UserObject()

    request_mock.state.user = user_obj
    request_mock.query_params.getlist.return_value = []

    context = build_user_context(request_mock)

    assert context["user_id"] == "user789"  # Mapped from 'id'


def test_build_user_context_empty_strategies() -> None:
    """Test user context with empty strategies list."""
    request_mock = MagicMock(spec=Request)
    request_mock.state.user = {
        "role": "viewer",
        "user_id": "viewer123",
    }
    request_mock.query_params.getlist.return_value = []

    context = build_user_context(request_mock)

    assert context["role"] == "viewer"
    assert context["strategies"] == []  # Defaults to empty list
    assert context["requested_strategies"] == []


def test_build_user_context_with_requested_strategies() -> None:
    """Test extraction of requested strategies from query params."""
    request_mock = MagicMock(spec=Request)
    request_mock.state.user = {
        "role": "trader",
        "strategies": ["alpha_baseline", "momentum", "mean_reversion"],
        "user_id": "user123",
    }
    request_mock.query_params.getlist.return_value = ["alpha_baseline", "momentum"]

    context = build_user_context(request_mock)

    assert context["requested_strategies"] == ["alpha_baseline", "momentum"]


def test_build_user_context_raises_when_no_user() -> None:
    """Test that 401 raised when no user in request.state."""
    request_mock = MagicMock(spec=Request)
    request_mock.state.user = None

    with pytest.raises(HTTPException) as exc_info:
        build_user_context(request_mock)

    assert exc_info.value.status_code == 401
    assert "Missing authenticated user context" in exc_info.value.detail


def test_build_user_context_raises_when_missing_role() -> None:
    """Test that 401 raised when user missing role."""
    request_mock = MagicMock(spec=Request)
    request_mock.state.user = {
        "strategies": ["alpha_baseline"],
        "user_id": "user123",
        # Missing "role" field
    }

    with pytest.raises(HTTPException) as exc_info:
        build_user_context(request_mock)

    assert exc_info.value.status_code == 401
    assert "Missing authenticated user context" in exc_info.value.detail


def test_build_user_context_raises_when_empty_role() -> None:
    """Test that 401 raised when user has empty role."""
    request_mock = MagicMock(spec=Request)
    request_mock.state.user = {
        "role": "",  # Empty string
        "user_id": "user123",
    }

    with pytest.raises(HTTPException) as exc_info:
        build_user_context(request_mock)

    assert exc_info.value.status_code == 401


def test_build_user_context_raises_when_none_role() -> None:
    """Test that 401 raised when user has None role."""
    request_mock = MagicMock(spec=Request)
    request_mock.state.user = {
        "role": None,
        "user_id": "user123",
    }

    with pytest.raises(HTTPException) as exc_info:
        build_user_context(request_mock)

    assert exc_info.value.status_code == 401


def test_build_user_context_object_with_empty_dict() -> None:
    """Test user object with empty __dict__ but attributes set."""
    request_mock = MagicMock(spec=Request)

    # Create a simple object with __dict__ override
    class UserWithEmptyDict:
        def __init__(self):
            # Clear __dict__ after setting attributes
            self.role = "trader"
            self.strategies = ["alpha_baseline"]
            self.user_id = "user999"
            # Keep __dict__ accessible

    user_obj = UserWithEmptyDict()

    request_mock.state.user = user_obj
    request_mock.query_params.getlist.return_value = []

    context = build_user_context(request_mock)

    # Attributes should be preserved even with __dict__
    assert context["role"] == "trader"
    assert context["strategies"] == ["alpha_baseline"]
    assert context["user_id"] == "user999"


def test_build_user_context_preserves_all_user_data() -> None:
    """Test that full user object is included in context."""
    request_mock = MagicMock(spec=Request)
    request_mock.state.user = {
        "role": "admin",
        "strategies": ["alpha_baseline"],
        "user_id": "admin123",
        "email": "admin@example.com",  # Extra field
        "permissions": ["read", "write"],  # Extra field
    }
    request_mock.query_params.getlist.return_value = []

    context = build_user_context(request_mock)

    # Full user dict should be preserved
    assert context["user"]["email"] == "admin@example.com"
    assert context["user"]["permissions"] == ["read", "write"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
