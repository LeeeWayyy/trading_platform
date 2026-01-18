"""Unit tests for OrchestrationDatabaseClient."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import psycopg
import pytest

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
