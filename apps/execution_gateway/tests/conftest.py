"""Shared pytest fixtures for execution_gateway app tests.

This conftest provides cleanup fixtures to prevent test pollution
from monkeypatching module-level variables.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

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
    original_context = getattr(main.app.state, "context", None)
    original_context_deps = getattr(main.app.state, "context_deps", None)
    original_metrics = getattr(main.app.state, "metrics", None)
    original_config = getattr(main.app.state, "config", None)
    original_env_dry_run = os.environ.get("DRY_RUN")
    original_main_dry_run = getattr(main, "DRY_RUN", None)

    # Set up default mocks for execution gateway globals to avoid cross-test pollution
    main.db_client = _create_mock_db_client()
    if main.redis_client is None:
        main.redis_client = MagicMock()
    main.recovery_manager = _create_mock_recovery_manager()

    # Force DRY_RUN=true for these tests to stabilize metrics expectations
    os.environ["DRY_RUN"] = "true"
    try:
        main.DRY_RUN = True
    except Exception:
        pass

    try:
        from apps.execution_gateway.app_factory import create_test_config

        main.app.state.config = create_test_config(dry_run=True)
    except Exception:
        main.app.state.config = None

    # Reset app.state context to avoid cross-test pollution
    main.app.state.context = None
    main.app.state.context_deps = None

    # Reset metrics to default baseline values
    try:
        from apps.execution_gateway.metrics import (
            alpaca_connection_status,
            database_connection_status,
            dry_run_mode,
            redis_connection_status,
        )

        dry_run_mode.set(1 if getattr(main, "DRY_RUN", True) else 0)
        database_connection_status.set(0)
        redis_connection_status.set(0)
        alpaca_connection_status.set(0)
    except Exception:
        pass

    yield

    # Restore original values (may be None in test context)
    main.db_client = original_db_client  # type: ignore[assignment]
    main.redis_client = original_redis_client
    main.recovery_manager = original_recovery_manager  # type: ignore[assignment]
    main.reconciliation_service = original_reconciliation_service
    main.reconciliation_task = original_reconciliation_task
    main.FEATURE_PERFORMANCE_DASHBOARD = original_feature_flag
    main.app.state.context = original_context
    main.app.state.context_deps = original_context_deps
    main.app.state.metrics = original_metrics
    main.app.state.config = original_config
    if original_env_dry_run is None:
        os.environ.pop("DRY_RUN", None)
    else:
        os.environ["DRY_RUN"] = original_env_dry_run
    if original_main_dry_run is not None:
        main.DRY_RUN = original_main_dry_run

    # Clear dependency overrides
    main.app.dependency_overrides.clear()
