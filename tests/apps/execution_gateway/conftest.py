"""Shared pytest fixtures for execution_gateway tests.

This conftest provides cleanup fixtures to prevent test pollution
from dependency_overrides and module-level mocks.
"""

from __future__ import annotations

import os

import pytest

# Disable internal token validation for all execution_gateway tests
# This MUST override any CI environment setting to prevent 401s in tests
# Set before importing main.py to ensure settings are configured correctly
os.environ["INTERNAL_TOKEN_REQUIRED"] = "false"

# Clear settings cache to ensure our env var takes effect
# This is needed because settings may have been cached before this conftest runs
try:
    from config.settings import get_settings

    get_settings.cache_clear()
except (ImportError, AttributeError):
    pass


@pytest.fixture(autouse=True)
def restore_main_globals():
    """Restore main.py module-level globals after each test.

    Several tests monkeypatch main.db_client, main.redis_client, etc.
    which persists across tests since Python modules are singletons.
    This fixture saves and restores them to prevent pollution.
    """
    # Import the module
    try:
        from apps.execution_gateway import main
    except ImportError:
        yield
        return

    # Override auth dependencies for tests (C6)
    # Import lazily to avoid import order issues with test files that stub modules
    try:
        from typing import Any

        from libs.common.api_auth_dependency import AuthContext

        def _mock_auth_context() -> AuthContext:
            """Return a mock AuthContext that bypasses authentication for tests."""
            return AuthContext(
                user=None,
                internal_claims=None,
                auth_type="test",
                is_authenticated=True,
            )

        def _mock_user_context() -> dict[str, Any]:
            """Return a mock user context for RBAC tests."""
            return {
                "role": "admin",
                "strategies": ["alpha_baseline"],
                "requested_strategies": [],
                "user_id": "test-user",
                "user": {"role": "admin", "strategies": ["alpha_baseline"], "user_id": "test-user"},
            }

        main.app.dependency_overrides[main.order_submit_auth] = _mock_auth_context
        main.app.dependency_overrides[main.order_slice_auth] = _mock_auth_context
        main.app.dependency_overrides[main.order_cancel_auth] = _mock_auth_context
        main.app.dependency_overrides[main.order_read_auth] = _mock_auth_context
        main.app.dependency_overrides[main.kill_switch_auth] = _mock_auth_context
        main.app.dependency_overrides[main._build_user_context] = _mock_user_context
    except (ImportError, AttributeError):
        # Auth dependencies not available (module stubs in test files)
        pass

    # Save original values
    original_db_client = getattr(main, "db_client", None)
    original_redis_client = getattr(main, "redis_client", None)
    original_kill_switch = getattr(main, "kill_switch", None)
    original_circuit_breaker = getattr(main, "circuit_breaker", None)
    original_position_reservation = getattr(main, "position_reservation", None)
    original_reconciliation_service = getattr(main, "reconciliation_service", None)
    original_reconciliation_task = getattr(main, "reconciliation_task", None)
    original_feature_flag = getattr(main, "FEATURE_PERFORMANCE_DASHBOARD", True)

    yield

    # Restore original values
    main.db_client = original_db_client
    main.redis_client = original_redis_client
    main.kill_switch = original_kill_switch
    main.circuit_breaker = original_circuit_breaker
    main.position_reservation = original_position_reservation
    main.reconciliation_service = original_reconciliation_service
    main.reconciliation_task = original_reconciliation_task
    main.FEATURE_PERFORMANCE_DASHBOARD = original_feature_flag

    # Clear dependency overrides
    main.app.dependency_overrides.clear()
