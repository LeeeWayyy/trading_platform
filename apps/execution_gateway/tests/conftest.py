"""Shared pytest fixtures for execution_gateway app tests.

This conftest provides cleanup fixtures to prevent test pollution
from monkeypatching module-level variables.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _restore_main_globals():
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
    # The first assignment needs type: ignore because db_client is typed but we saved it as Any
    main.db_client = original_db_client  # type: ignore[assignment]
    main.redis_client = original_redis_client
    main.kill_switch = original_kill_switch
    main.circuit_breaker = original_circuit_breaker
    main.position_reservation = original_position_reservation
    main.reconciliation_service = original_reconciliation_service
    main.reconciliation_task = original_reconciliation_task
    main.FEATURE_PERFORMANCE_DASHBOARD = original_feature_flag

    # Clear dependency overrides
    main.app.dependency_overrides.clear()
