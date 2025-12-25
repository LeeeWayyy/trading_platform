"""Shared pytest fixtures for execution_gateway app tests.

This conftest provides cleanup fixtures to prevent test pollution
from monkeypatching module-level variables.
"""

from __future__ import annotations

import pytest

# Import shared mock helpers from root conftest (DRY principle)
from tests.conftest import _create_mock_db_client, _create_mock_recovery_manager


@pytest.fixture(autouse=True)
def _restore_main_globals():
    """Restore main.py module-level globals after each test.

    Several tests monkeypatch main.db_client, main.redis_client, etc.
    which persists across tests since Python modules are singletons.
    This fixture saves and restores them to prevent pollution.

    Also sets up default mocks for module-level variables that are None
    by default (initialized in lifespan) to allow tests to run without
    the full lifespan context.
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
    original_recovery_manager = getattr(main, "recovery_manager", None)
    original_reconciliation_service = getattr(main, "reconciliation_service", None)
    original_reconciliation_task = getattr(main, "reconciliation_task", None)
    original_feature_flag = getattr(main, "FEATURE_PERFORMANCE_DASHBOARD", True)

    # Set up default mocks for variables that are None (initialized in lifespan)
    if main.db_client is None:
        main.db_client = _create_mock_db_client()
    if main.recovery_manager is None:
        main.recovery_manager = _create_mock_recovery_manager()

    yield

    # Restore original values (may be None in test context)
    main.db_client = original_db_client  # type: ignore[assignment]
    main.redis_client = original_redis_client
    main.recovery_manager = original_recovery_manager  # type: ignore[assignment]
    main.reconciliation_service = original_reconciliation_service
    main.reconciliation_task = original_reconciliation_task
    main.FEATURE_PERFORMANCE_DASHBOARD = original_feature_flag

    # Clear dependency overrides
    main.app.dependency_overrides.clear()
