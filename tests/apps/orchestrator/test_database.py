"""Unit tests for OrchestrationDatabaseClient."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import psycopg

from apps.orchestrator.database import OrchestrationDatabaseClient
from apps.orchestrator.schemas import OrchestrationResult, SignalOrderMapping


@contextmanager
def _mock_pool_connection():
    with patch("apps.orchestrator.database.ConnectionPool") as mock_pool:
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        pool_instance = mock_pool.return_value
        pool_instance.connection.return_value.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        yield mock_pool, mock_conn, mock_cursor


class TestDatabaseClientInitialization:
    def test_initialization_uses_connection_pool(self) -> None:
        with patch("apps.orchestrator.database.ConnectionPool") as mock_pool:
            db = OrchestrationDatabaseClient("postgresql://localhost/db")

        assert db.database_url == "postgresql://localhost/db"
        mock_pool.assert_called_once()

    def test_close_calls_pool_close(self) -> None:
        with patch("apps.orchestrator.database.ConnectionPool") as mock_pool:
            db = OrchestrationDatabaseClient("postgresql://localhost/db")

        db.close()
        mock_pool.return_value.close.assert_called_once()


class TestCheckConnection:
    def test_check_connection_success(self) -> None:
        with _mock_pool_connection() as (_, _, mock_cursor):
            db = OrchestrationDatabaseClient("postgresql://localhost/db")

            assert db.check_connection() is True
            mock_cursor.execute.assert_called_once_with("SELECT 1")

    def test_check_connection_operational_error(self) -> None:
        with patch("apps.orchestrator.database.ConnectionPool") as mock_pool:
            mock_pool.return_value.connection.side_effect = psycopg.OperationalError("boom")
            db = OrchestrationDatabaseClient("postgresql://localhost/db")

            assert db.check_connection() is False

    def test_check_connection_interface_error(self) -> None:
        with patch("apps.orchestrator.database.ConnectionPool") as mock_pool:
            mock_pool.return_value.connection.side_effect = psycopg.InterfaceError("boom")
            db = OrchestrationDatabaseClient("postgresql://localhost/db")

            assert db.check_connection() is False

    def test_check_connection_generic_exception(self) -> None:
        """Test check_connection handles generic exceptions."""
        with patch("apps.orchestrator.database.ConnectionPool") as mock_pool:
            mock_pool.return_value.connection.side_effect = RuntimeError("unexpected error")
            db = OrchestrationDatabaseClient("postgresql://localhost/db")

            assert db.check_connection() is False


class TestCreateRun:
    def test_create_run_inserts_and_commits(self) -> None:
        with _mock_pool_connection() as (_, mock_conn, mock_cursor):
            mock_cursor.fetchone.return_value = (42,)
            db = OrchestrationDatabaseClient("postgresql://localhost/db")

            run_id = uuid4()
            result = OrchestrationResult(
                run_id=run_id,
                status="completed",
                strategy_id="alpha",
                as_of_date="2024-12-31",
                symbols=["AAPL"],
                capital=Decimal("1000"),
                num_signals=1,
                signal_metadata={"model_version": "v1"},
                num_orders_submitted=1,
                num_orders_accepted=1,
                num_orders_rejected=0,
                num_orders_filled=1,
                mappings=[
                    SignalOrderMapping(
                        symbol="AAPL",
                        predicted_return=0.1,
                        rank=1,
                        target_weight=0.5,
                        client_order_id="cid",
                        order_qty=10,
                        order_side="buy",
                    )
                ],
                started_at=datetime(2024, 12, 31, 12, 0, 0, tzinfo=UTC),
                completed_at=datetime(2024, 12, 31, 12, 1, 0, tzinfo=UTC),
                duration_seconds=Decimal("60"),
            )

            with patch.object(db, "_create_mappings") as create_mappings:
                created_id = db.create_run(result)

            assert created_id == 42
            create_mappings.assert_called_once()
            mock_conn.commit.assert_called_once()

    def test_create_mappings_inserts_rows(self) -> None:
        with _mock_pool_connection() as (_, _, mock_cursor):
            db = OrchestrationDatabaseClient("postgresql://localhost/db")
            run_id = UUID("12345678-1234-5678-1234-567812345678")
            mappings = [
                SignalOrderMapping(
                    symbol="AAPL",
                    predicted_return=0.1,
                    rank=1,
                    target_weight=0.5,
                    client_order_id="cid",
                    order_qty=10,
                    order_side="buy",
                    broker_order_id="brk",
                    order_status="accepted",
                ),
                SignalOrderMapping(
                    symbol="MSFT",
                    predicted_return=-0.05,
                    rank=2,
                    target_weight=-0.25,
                    skip_reason="no_liquidity",
                ),
            ]

            db._create_mappings(mock_cursor, run_id, mappings)

            assert mock_cursor.execute.call_count == 2
            first_call = mock_cursor.execute.call_args_list[0]
            assert first_call.args[1][0] == run_id
            assert first_call.args[1][1] == "AAPL"


class TestUpdateRunStatus:
    def test_update_run_status_executes_update(self) -> None:
        with _mock_pool_connection() as (_, mock_conn, mock_cursor):
            db = OrchestrationDatabaseClient("postgresql://localhost/db")
            run_id = UUID("12345678-1234-5678-1234-567812345678")

            db.update_run_status(
                run_id,
                status="completed",
                completed_at=datetime(2024, 12, 31, 12, 1, 0, tzinfo=UTC),
                duration_seconds=Decimal("60"),
                error_message=None,
            )

            mock_cursor.execute.assert_called_once()
            mock_conn.commit.assert_called_once()


class TestRunQueries:
    def test_get_run_returns_summary(self) -> None:
        with _mock_pool_connection() as (_, _, mock_cursor):
            db = OrchestrationDatabaseClient("postgresql://localhost/db")
            run_id = UUID("12345678-1234-5678-1234-567812345678")

            mock_cursor.fetchone.return_value = SimpleNamespace(
                run_id=run_id,
                status="completed",
                strategy_id="alpha",
                as_of_date="2024-12-31",
                num_signals=1,
                num_orders_submitted=1,
                num_orders_accepted=1,
                num_orders_rejected=0,
                started_at=datetime(2024, 12, 31, 12, 0, 0, tzinfo=UTC),
                completed_at=None,
                duration_seconds=None,
            )

            result = db.get_run(run_id)

            assert result is not None
            assert result.run_id == run_id
            assert result.status == "completed"

    def test_get_run_returns_none_when_missing(self) -> None:
        with _mock_pool_connection() as (_, _, mock_cursor):
            db = OrchestrationDatabaseClient("postgresql://localhost/db")
            mock_cursor.fetchone.return_value = None

            assert db.get_run(uuid4()) is None

    def test_list_runs_with_filters(self) -> None:
        with _mock_pool_connection() as (_, _, mock_cursor):
            db = OrchestrationDatabaseClient("postgresql://localhost/db")
            run_id = UUID("12345678-1234-5678-1234-567812345678")
            mock_cursor.fetchall.return_value = [
                SimpleNamespace(
                    run_id=run_id,
                    status="completed",
                    strategy_id="alpha",
                    as_of_date="2024-12-31",
                    num_signals=1,
                    num_orders_submitted=1,
                    num_orders_accepted=1,
                    num_orders_rejected=0,
                    started_at=datetime(2024, 12, 31, 12, 0, 0, tzinfo=UTC),
                    completed_at=None,
                    duration_seconds=None,
                )
            ]

            results = db.list_runs(limit=10, offset=5, strategy_id="alpha", status="completed")

            assert len(results) == 1
            assert results[0].run_id == run_id
            assert mock_cursor.execute.call_args.args[1] == ["alpha", "completed", 10, 5]

    def test_get_mappings_returns_rows(self) -> None:
        with _mock_pool_connection() as (_, _, mock_cursor):
            db = OrchestrationDatabaseClient("postgresql://localhost/db")
            run_id = UUID("12345678-1234-5678-1234-567812345678")
            mock_cursor.fetchall.return_value = [
                (
                    "AAPL",
                    0.1,
                    1,
                    0.5,
                    "cid",
                    10,
                    "buy",
                    "brk",
                    "accepted",
                    Decimal("10"),
                    Decimal("150"),
                    None,
                )
            ]

            results = db.get_mappings(run_id)

            assert len(results) == 1
            assert results[0].symbol == "AAPL"


class TestConnectionPoolConfiguration:
    """Test connection pool configuration from environment variables."""

    def test_pool_uses_default_config_values(self) -> None:
        """Test pool uses default configuration when env vars not set."""
        with patch("apps.orchestrator.database.ConnectionPool") as mock_pool:
            with patch.dict("os.environ", {}, clear=False):
                _ = OrchestrationDatabaseClient("postgresql://localhost/db")

            call_kwargs = mock_pool.call_args.kwargs
            assert call_kwargs["min_size"] == 2
            assert call_kwargs["max_size"] == 10
            assert call_kwargs["timeout"] == 10.0

    def test_pool_uses_custom_config_from_env(self) -> None:
        """Test pool uses custom configuration from environment variables."""
        # Mock the env variables at the module level
        with patch("apps.orchestrator.database.DB_POOL_MIN_SIZE", 5):
            with patch("apps.orchestrator.database.DB_POOL_MAX_SIZE", 20):
                with patch("apps.orchestrator.database.DB_POOL_TIMEOUT", 30.0):
                    with patch("apps.orchestrator.database.ConnectionPool") as mock_pool:
                        _ = OrchestrationDatabaseClient("postgresql://localhost/db")

                    call_kwargs = mock_pool.call_args.kwargs
                    assert call_kwargs["min_size"] == 5
                    assert call_kwargs["max_size"] == 20
                    assert call_kwargs["timeout"] == 30.0


class TestCloseIdempotency:
    """Test that close() can be called multiple times safely."""

    def test_close_can_be_called_multiple_times(self) -> None:
        """Test close is safe to call multiple times as documented."""
        with patch("apps.orchestrator.database.ConnectionPool") as mock_pool:
            db = OrchestrationDatabaseClient("postgresql://localhost/db")

            db.close()
            db.close()
            db.close()

            assert mock_pool.return_value.close.call_count == 3


class TestCreateRunEdgeCases:
    """Test edge cases for create_run method."""

    def test_create_run_without_mappings(self) -> None:
        """Test creating run with empty mappings list."""
        with _mock_pool_connection() as (_, mock_conn, mock_cursor):
            mock_cursor.fetchone.return_value = (42,)
            db = OrchestrationDatabaseClient("postgresql://localhost/db")

            run_id = uuid4()
            result = OrchestrationResult(
                run_id=run_id,
                status="completed",
                strategy_id="alpha",
                as_of_date="2024-12-31",
                symbols=["AAPL"],
                capital=Decimal("1000"),
                num_signals=0,
                signal_metadata=None,
                num_orders_submitted=0,
                num_orders_accepted=0,
                num_orders_rejected=0,
                num_orders_filled=0,
                mappings=[],
                started_at=datetime(2024, 12, 31, 12, 0, 0, tzinfo=UTC),
                completed_at=datetime(2024, 12, 31, 12, 1, 0, tzinfo=UTC),
                duration_seconds=Decimal("60"),
            )

            created_id = db.create_run(result)

            assert created_id == 42
            mock_conn.commit.assert_called_once()

    def test_create_run_with_null_signal_metadata(self) -> None:
        """Test creating run with None signal_metadata."""
        with _mock_pool_connection() as (_, mock_conn, mock_cursor):
            mock_cursor.fetchone.return_value = (99,)
            db = OrchestrationDatabaseClient("postgresql://localhost/db")

            run_id = uuid4()
            result = OrchestrationResult(
                run_id=run_id,
                status="failed",
                strategy_id="beta",
                as_of_date="2024-12-31",
                symbols=["TSLA"],
                capital=Decimal("5000"),
                num_signals=0,
                signal_metadata=None,
                num_orders_submitted=0,
                num_orders_accepted=0,
                num_orders_rejected=0,
                mappings=[],
                started_at=datetime(2024, 12, 31, 10, 0, 0, tzinfo=UTC),
                error_message="Signal service unavailable",
            )

            created_id = db.create_run(result)

            assert created_id == 99
            # Verify None was passed for signal_service_response
            call_args = mock_cursor.execute.call_args
            assert call_args.args[1][16] is None  # signal_service_response parameter

    def test_create_run_with_empty_signal_metadata(self) -> None:
        """Test creating run with empty signal_metadata dict."""
        with _mock_pool_connection() as (_, mock_conn, mock_cursor):
            mock_cursor.fetchone.return_value = (100,)
            db = OrchestrationDatabaseClient("postgresql://localhost/db")

            run_id = uuid4()
            result = OrchestrationResult(
                run_id=run_id,
                status="completed",
                strategy_id="gamma",
                as_of_date="2024-12-31",
                symbols=["NVDA"],
                capital=Decimal("2000"),
                num_signals=1,
                signal_metadata={},  # Empty dict
                num_orders_submitted=1,
                num_orders_accepted=1,
                num_orders_rejected=0,
                mappings=[],
                started_at=datetime(2024, 12, 31, 11, 0, 0, tzinfo=UTC),
            )

            created_id = db.create_run(result)

            assert created_id == 100


class TestUpdateRunStatusEdgeCases:
    """Test edge cases for update_run_status method."""

    def test_update_run_status_with_minimal_fields(self) -> None:
        """Test updating status with only required field."""
        with _mock_pool_connection() as (_, mock_conn, mock_cursor):
            db = OrchestrationDatabaseClient("postgresql://localhost/db")
            run_id = UUID("12345678-1234-5678-1234-567812345678")

            db.update_run_status(run_id, status="running")

            mock_cursor.execute.assert_called_once()
            call_args = mock_cursor.execute.call_args
            assert call_args.args[1] == ("running", None, None, None, run_id)

    def test_update_run_status_with_error_only(self) -> None:
        """Test updating status with error message."""
        with _mock_pool_connection() as (_, mock_conn, mock_cursor):
            db = OrchestrationDatabaseClient("postgresql://localhost/db")
            run_id = UUID("12345678-1234-5678-1234-567812345678")

            db.update_run_status(run_id, status="failed", error_message="Database connection lost")

            mock_cursor.execute.assert_called_once()
            call_args = mock_cursor.execute.call_args
            assert call_args.args[1][0] == "failed"
            assert call_args.args[1][3] == "Database connection lost"


class TestListRunsVariations:
    """Test various parameter combinations for list_runs."""

    def test_list_runs_with_default_parameters(self) -> None:
        """Test list_runs with default limit and offset."""
        with _mock_pool_connection() as (_, _, mock_cursor):
            db = OrchestrationDatabaseClient("postgresql://localhost/db")
            mock_cursor.fetchall.return_value = []

            results = db.list_runs()

            assert results == []
            call_args = mock_cursor.execute.call_args
            # Default limit=50, offset=0
            assert call_args.args[1] == [50, 0]

    def test_list_runs_with_only_strategy_filter(self) -> None:
        """Test filtering by strategy_id only."""
        with _mock_pool_connection() as (_, _, mock_cursor):
            db = OrchestrationDatabaseClient("postgresql://localhost/db")
            mock_cursor.fetchall.return_value = []

            results = db.list_runs(strategy_id="alpha")

            assert results == []
            call_args = mock_cursor.execute.call_args
            assert call_args.args[1] == ["alpha", 50, 0]

    def test_list_runs_with_only_status_filter(self) -> None:
        """Test filtering by status only."""
        with _mock_pool_connection() as (_, _, mock_cursor):
            db = OrchestrationDatabaseClient("postgresql://localhost/db")
            mock_cursor.fetchall.return_value = []

            results = db.list_runs(status="failed")

            assert results == []
            call_args = mock_cursor.execute.call_args
            assert call_args.args[1] == ["failed", 50, 0]

    def test_list_runs_returns_empty_list(self) -> None:
        """Test list_runs returns empty list when no results."""
        with _mock_pool_connection() as (_, _, mock_cursor):
            db = OrchestrationDatabaseClient("postgresql://localhost/db")
            mock_cursor.fetchall.return_value = []

            results = db.list_runs(limit=100)

            assert results == []
            assert isinstance(results, list)


class TestGetMappingsEdgeCases:
    """Test edge cases for get_mappings method."""

    def test_get_mappings_returns_empty_list(self) -> None:
        """Test get_mappings returns empty list when no mappings exist."""
        with _mock_pool_connection() as (_, _, mock_cursor):
            db = OrchestrationDatabaseClient("postgresql://localhost/db")
            run_id = UUID("12345678-1234-5678-1234-567812345678")
            mock_cursor.fetchall.return_value = []

            results = db.get_mappings(run_id)

            assert results == []
            assert isinstance(results, list)

    def test_get_mappings_with_multiple_rows(self) -> None:
        """Test get_mappings returns all rows ordered by rank."""
        with _mock_pool_connection() as (_, _, mock_cursor):
            db = OrchestrationDatabaseClient("postgresql://localhost/db")
            run_id = UUID("12345678-1234-5678-1234-567812345678")
            mock_cursor.fetchall.return_value = [
                ("AAPL", 0.1, 1, 0.5, "cid1", 10, "buy", "brk1", "accepted", Decimal("10"), Decimal("150"), None),
                ("MSFT", 0.08, 2, 0.3, "cid2", 5, "buy", "brk2", "accepted", Decimal("5"), Decimal("300"), None),
                ("TSLA", -0.05, 3, -0.2, None, None, None, None, None, None, None, "insufficient_liquidity"),
            ]

            results = db.get_mappings(run_id)

            assert len(results) == 3
            assert results[0].symbol == "AAPL"
            assert results[0].rank == 1
            assert results[1].symbol == "MSFT"
            assert results[1].rank == 2
            assert results[2].symbol == "TSLA"
            assert results[2].rank == 3
            assert results[2].skip_reason == "insufficient_liquidity"

    def test_get_mappings_with_partial_fill_data(self) -> None:
        """Test get_mappings handles partial fill data correctly."""
        with _mock_pool_connection() as (_, _, mock_cursor):
            db = OrchestrationDatabaseClient("postgresql://localhost/db")
            run_id = UUID("12345678-1234-5678-1234-567812345678")
            mock_cursor.fetchall.return_value = [
                ("AAPL", 0.1, 1, 0.5, "cid1", 10, "buy", "brk1", "partially_filled", Decimal("5"), Decimal("150.5"), None),
            ]

            results = db.get_mappings(run_id)

            assert len(results) == 1
            assert results[0].filled_qty == Decimal("5")
            assert results[0].filled_avg_price == Decimal("150.5")
            assert results[0].order_status == "partially_filled"


class TestCreateMappingsDirectly:
    """Test _create_mappings method with various scenarios."""

    def test_create_mappings_with_all_fields_populated(self) -> None:
        """Test _create_mappings with fully populated SignalOrderMapping."""
        with _mock_pool_connection() as (_, _, mock_cursor):
            db = OrchestrationDatabaseClient("postgresql://localhost/db")
            run_id = UUID("12345678-1234-5678-1234-567812345678")

            mapping = SignalOrderMapping(
                symbol="NVDA",
                predicted_return=0.15,
                rank=1,
                target_weight=0.6,
                client_order_id="nvda_cid",
                order_qty=20,
                order_side="buy",
                broker_order_id="nvda_broker",
                order_status="filled",
                filled_qty=Decimal("20"),
                filled_avg_price=Decimal("500.25"),
            )

            db._create_mappings(mock_cursor, run_id, [mapping])

            mock_cursor.execute.assert_called_once()
            call_args = mock_cursor.execute.call_args
            assert call_args.args[1][0] == run_id
            assert call_args.args[1][1] == "NVDA"
            assert call_args.args[1][11] == Decimal("500.25")

    def test_create_mappings_with_skip_reason_only(self) -> None:
        """Test _create_mappings with mapping that was skipped."""
        with _mock_pool_connection() as (_, _, mock_cursor):
            db = OrchestrationDatabaseClient("postgresql://localhost/db")
            run_id = UUID("12345678-1234-5678-1234-567812345678")

            mapping = SignalOrderMapping(
                symbol="GME",
                predicted_return=-0.1,
                rank=50,
                target_weight=-0.05,
                skip_reason="circuit_breaker_tripped",
            )

            db._create_mappings(mock_cursor, run_id, [mapping])

            call_args = mock_cursor.execute.call_args
            assert call_args.args[1][12] == "circuit_breaker_tripped"
            # Verify order fields are None
            assert call_args.args[1][5] is None  # client_order_id
            assert call_args.args[1][6] is None  # order_qty
