"""
Root conftest for tests.

This ensures:
1. Redis module is properly initialized before test collection
2. Execution gateway globals are mocked for lifespan-initialized variables
"""

from unittest.mock import Mock

import pytest

# Import redis first to ensure it's in sys.modules before any module
# does 'import redis.asyncio as redis' which could shadow it
import redis  # noqa: F401

# Also import redis.exceptions to ensure it's available
import redis.exceptions  # noqa: F401


def _create_mock_db_client() -> Mock:
    """Create a mock DatabaseClient for tests."""
    mock = Mock()
    mock.check_connection.return_value = True
    mock.get_order_by_client_id.return_value = None
    mock.get_positions_for_strategies.return_value = []
    mock.get_daily_pnl_history.return_value = []
    return mock


def _create_mock_recovery_manager() -> Mock:
    """Create a mock RecoveryManager for tests."""
    from apps.execution_gateway.recovery_manager import RecoveryState

    mock_state = RecoveryState()

    # Create mock components for the state
    mock_kill_switch = Mock()
    mock_kill_switch.is_engaged.return_value = False
    mock_kill_switch.get_status.return_value = {"state": "ACTIVE"}

    mock_circuit_breaker = Mock()
    mock_circuit_breaker.is_tripped.return_value = False
    mock_circuit_breaker.get_trip_reason.return_value = None

    mock_position_reservation = Mock()
    mock_position_reservation.reserve.return_value = Mock(success=True, token="mock-token")
    mock_position_reservation.confirm.return_value = Mock(success=True)
    mock_position_reservation.release.return_value = Mock(success=True)

    mock_state.kill_switch = mock_kill_switch
    mock_state.circuit_breaker = mock_circuit_breaker
    mock_state.position_reservation = mock_position_reservation
    mock_state.kill_switch_unavailable = False
    mock_state.circuit_breaker_unavailable = False
    mock_state.position_reservation_unavailable = False

    mock = Mock()
    mock._state = mock_state
    mock.kill_switch = mock_kill_switch
    mock.circuit_breaker = mock_circuit_breaker
    mock.position_reservation = mock_position_reservation
    mock.needs_recovery.return_value = False
    mock.is_kill_switch_unavailable.return_value = False
    mock.is_circuit_breaker_unavailable.return_value = False
    mock.is_position_reservation_unavailable.return_value = False
    mock.set_kill_switch_unavailable = Mock()
    mock.set_circuit_breaker_unavailable = Mock()
    mock.set_position_reservation_unavailable = Mock()

    return mock


@pytest.fixture(autouse=True)
def execution_gateway_globals():
    """Initialize execution_gateway globals for tests.

    This fixture ensures that module-level variables (db_client, recovery_manager)
    are properly mocked for tests that import execution_gateway.main.
    These are normally initialized in lifespan, but tests run without lifespan.

    NOTE: This fixture provides MINIMAL setup for all tests in the project.
    The more comprehensive `restore_main_globals` fixture in
    `tests/apps/execution_gateway/conftest.py` extends this with:
    - Auth dependency overrides (C6)
    - Additional attribute save/restore (kill_switch, circuit_breaker, etc.)

    Both fixtures check `if main.X is None` before setting mocks, so they
    don't conflict - the first one sets the mock, subsequent ones skip.
    Pytest teardown order (inner-to-outer) ensures correct restoration.
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

    # Set up default mocks for variables that are None (initialized in lifespan)
    if main.db_client is None:
        main.db_client = _create_mock_db_client()
    if main.recovery_manager is None:
        main.recovery_manager = _create_mock_recovery_manager()

    yield

    # Restore original values
    main.db_client = original_db_client  # type: ignore[assignment]
    main.redis_client = original_redis_client
    main.recovery_manager = original_recovery_manager  # type: ignore[assignment]
