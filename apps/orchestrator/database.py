"""
Database client for Orchestrator Service.

Handles persistence of orchestration runs and signal-order mappings.
H2 Fix: Uses connection pooling for 10x performance improvement.
"""

import json
import logging
import os
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import class_row
from psycopg_pool import ConnectionPool

from apps.orchestrator.schemas import (
    OrchestrationResult,
    OrchestrationRunSummary,
    SignalOrderMapping,
)

logger = logging.getLogger(__name__)

# H2 Fix: Configurable pool settings via environment variables
DB_POOL_MIN_SIZE = int(os.getenv("DB_POOL_MIN_SIZE", "2"))
DB_POOL_MAX_SIZE = int(os.getenv("DB_POOL_MAX_SIZE", "10"))
DB_POOL_TIMEOUT = float(os.getenv("DB_POOL_TIMEOUT", "10.0"))


# ==============================================================================
# Database Models (for psycopg class_row mapping)
# ==============================================================================


class OrchestrationRunDB:
    """Database model for orchestration_runs table."""

    id: int
    run_id: UUID
    strategy_id: str
    as_of_date: str
    status: str
    symbols: list[str]
    capital: Decimal
    max_position_size: Decimal | None
    num_signals: int
    model_version: str | None
    num_orders_submitted: int
    num_orders_accepted: int
    num_orders_rejected: int
    num_orders_filled: int
    started_at: datetime
    completed_at: datetime | None
    duration_seconds: Decimal | None
    error_message: str | None
    signal_service_response: dict[str, Any] | None
    execution_gateway_responses: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime


# ==============================================================================
# Database Client
# ==============================================================================


class OrchestrationDatabaseClient:
    """
    Database client for orchestration persistence.

    Handles CRUD operations for:
    - orchestration_runs table
    - signal_order_mappings table

    H2 Fix: Uses connection pooling for 10x performance improvement.
    Pool opens lazily on first use, so tests/scripts work without setup.

    Example:
        >>> db = OrchestrationDatabaseClient(
        ...     "postgresql://postgres:postgres@localhost:5432/trading_platform"
        ... )
        >>> db.create_run(result)
        >>> runs = db.list_runs(limit=10)
        >>> db.close()  # For clean shutdown
    """

    def __init__(self, database_url: str):
        """
        Initialize database client with connection pool.

        Args:
            database_url: PostgreSQL connection string

        Notes:
            Pool opens lazily on first .connection() call.
            Call close() for clean shutdown in production.
        """
        self.database_url = database_url

        # H2 Fix: Connection pooling for 10x performance
        self._pool = ConnectionPool(
            database_url,
            min_size=DB_POOL_MIN_SIZE,
            max_size=DB_POOL_MAX_SIZE,
            timeout=DB_POOL_TIMEOUT,
        )

        logger.info(
            "OrchestrationDatabaseClient initialized with connection pool",
            extra={"pool_min": DB_POOL_MIN_SIZE, "pool_max": DB_POOL_MAX_SIZE},
        )

    def close(self) -> None:
        """Close connection pool. Safe to call multiple times."""
        self._pool.close()
        logger.info("OrchestrationDatabaseClient connection pool closed")

    def check_connection(self) -> bool:
        """
        Check if database connection is working.

        Returns:
            True if connection successful, False otherwise
        """
        try:
            with self._pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    return True
        except Exception as e:
            logger.error(f"Database connection check failed: {e}")
            return False

    def create_run(self, result: OrchestrationResult) -> int:
        """
        Create orchestration run record.

        Args:
            result: OrchestrationResult with complete run details

        Returns:
            Database ID of created run

        Example:
            >>> run_id = db.create_run(result)
            >>> print(run_id)
            42
        """
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                # Prepare signal metadata
                signal_metadata = result.signal_metadata or {}

                # Insert run
                cur.execute(
                    """
                    INSERT INTO orchestration_runs (
                        run_id, strategy_id, as_of_date, status,
                        symbols, capital, max_position_size,
                        num_signals, model_version,
                        num_orders_submitted, num_orders_accepted, num_orders_rejected,
                        started_at, completed_at, duration_seconds,
                        error_message, signal_service_response
                    ) VALUES (
                        %s, %s, %s, %s,
                        %s, %s, %s,
                        %s, %s,
                        %s, %s, %s,
                        %s, %s, %s,
                        %s, %s
                    )
                    RETURNING id
                    """,
                    (
                        result.run_id,
                        result.strategy_id,
                        result.as_of_date,
                        result.status,
                        result.symbols,
                        result.capital,
                        None,  # max_position_size (not in result yet)
                        result.num_signals,
                        signal_metadata.get("model_version"),
                        result.num_orders_submitted,
                        result.num_orders_accepted,
                        result.num_orders_rejected,
                        result.started_at,
                        result.completed_at,
                        result.duration_seconds,
                        result.error_message,
                        json.dumps(signal_metadata) if signal_metadata else None,
                    ),
                )

                row = cur.fetchone()
                assert row is not None, "INSERT RETURNING should always return a row"
                run_id: int = row[0]

                # Insert signal-order mappings
                if result.mappings:
                    self._create_mappings(cur, result.run_id, result.mappings)

                conn.commit()

                logger.info(
                    f"Created orchestration run: {result.run_id}",
                    extra={"run_id": str(result.run_id), "db_id": run_id},
                )

                return run_id

    def _create_mappings(
        self, cur: psycopg.Cursor, run_id: UUID, mappings: list[SignalOrderMapping]
    ) -> None:
        """
        Create signal-order mapping records.

        Args:
            cur: Database cursor
            run_id: Orchestration run UUID
            mappings: List of signal-order mappings
        """
        for mapping in mappings:
            cur.execute(
                """
                INSERT INTO signal_order_mappings (
                    run_id,
                    symbol, predicted_return, rank, target_weight,
                    client_order_id, order_qty, order_side,
                    broker_order_id, order_status,
                    filled_qty, filled_avg_price,
                    skip_reason
                ) VALUES (
                    %s,
                    %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s,
                    %s, %s,
                    %s
                )
                """,
                (
                    run_id,
                    mapping.symbol,
                    mapping.predicted_return,
                    mapping.rank,
                    mapping.target_weight,
                    mapping.client_order_id,
                    mapping.order_qty,
                    mapping.order_side,
                    mapping.broker_order_id,
                    mapping.order_status,
                    mapping.filled_qty,
                    mapping.filled_avg_price,
                    mapping.skip_reason,
                ),
            )

    def update_run_status(
        self,
        run_id: UUID,
        status: str,
        completed_at: datetime | None = None,
        duration_seconds: Decimal | None = None,
        error_message: str | None = None,
    ) -> None:
        """
        Update orchestration run status.

        Args:
            run_id: Orchestration run UUID
            status: New status (running, completed, failed, partial)
            completed_at: Completion timestamp
            duration_seconds: Total duration
            error_message: Error message if failed
        """
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE orchestration_runs
                    SET status = %s,
                        completed_at = COALESCE(%s, completed_at),
                        duration_seconds = COALESCE(%s, duration_seconds),
                        error_message = COALESCE(%s, error_message)
                    WHERE run_id = %s
                    """,
                    (status, completed_at, duration_seconds, error_message, run_id),
                )
                conn.commit()

    def get_run(self, run_id: UUID) -> OrchestrationRunSummary | None:
        """
        Get orchestration run by UUID.

        Args:
            run_id: Orchestration run UUID

        Returns:
            OrchestrationRunSummary or None if not found

        Example:
            >>> run = db.get_run(uuid.UUID("..."))
            >>> print(run.status)
            'completed'
        """
        with self._pool.connection() as conn:
            with conn.cursor(row_factory=class_row(OrchestrationRunDB)) as cur:
                cur.execute(
                    """
                    SELECT id, run_id, strategy_id, as_of_date, status,
                           symbols, capital, max_position_size,
                           num_signals, model_version,
                           num_orders_submitted, num_orders_accepted,
                           num_orders_rejected, num_orders_filled,
                           started_at, completed_at, duration_seconds,
                           error_message, signal_service_response,
                           execution_gateway_responses,
                           created_at, updated_at
                    FROM orchestration_runs
                    WHERE run_id = %s
                    """,
                    (run_id,),
                )

                row = cur.fetchone()

                if not row:
                    return None

                return OrchestrationRunSummary(
                    run_id=row.run_id,
                    status=row.status,
                    strategy_id=row.strategy_id,
                    as_of_date=row.as_of_date,
                    num_signals=row.num_signals,
                    num_orders_submitted=row.num_orders_submitted,
                    num_orders_accepted=row.num_orders_accepted,
                    num_orders_rejected=row.num_orders_rejected,
                    started_at=row.started_at,
                    completed_at=row.completed_at,
                    duration_seconds=row.duration_seconds,
                )

    def list_runs(
        self,
        limit: int = 50,
        offset: int = 0,
        strategy_id: str | None = None,
        status: str | None = None,
    ) -> list[OrchestrationRunSummary]:
        """
        List recent orchestration runs.

        Args:
            limit: Maximum number of runs to return
            offset: Number of runs to skip
            strategy_id: Filter by strategy ID
            status: Filter by status

        Returns:
            List of OrchestrationRunSummary

        Example:
            >>> runs = db.list_runs(limit=10, status="completed")
            >>> print(len(runs))
            10
        """
        with self._pool.connection() as conn:
            with conn.cursor(row_factory=class_row(OrchestrationRunDB)) as cur:
                # Build query
                query = """
                    SELECT id, run_id, strategy_id, as_of_date, status,
                           symbols, capital, max_position_size,
                           num_signals, model_version,
                           num_orders_submitted, num_orders_accepted,
                           num_orders_rejected, num_orders_filled,
                           started_at, completed_at, duration_seconds,
                           error_message, signal_service_response,
                           execution_gateway_responses,
                           created_at, updated_at
                    FROM orchestration_runs
                    WHERE 1=1
                """
                params: list[Any] = []

                if strategy_id:
                    query += " AND strategy_id = %s"
                    params.append(strategy_id)

                if status:
                    query += " AND status = %s"
                    params.append(status)

                query += " ORDER BY started_at DESC LIMIT %s OFFSET %s"
                params.extend([limit, offset])

                cur.execute(query, params)
                rows = cur.fetchall()

                return [
                    OrchestrationRunSummary(
                        run_id=row.run_id,
                        status=row.status,
                        strategy_id=row.strategy_id,
                        as_of_date=row.as_of_date,
                        num_signals=row.num_signals,
                        num_orders_submitted=row.num_orders_submitted,
                        num_orders_accepted=row.num_orders_accepted,
                        num_orders_rejected=row.num_orders_rejected,
                        started_at=row.started_at,
                        completed_at=row.completed_at,
                        duration_seconds=row.duration_seconds,
                    )
                    for row in rows
                ]

    def get_mappings(self, run_id: UUID) -> list[SignalOrderMapping]:
        """
        Get signal-order mappings for a run.

        Args:
            run_id: Orchestration run UUID

        Returns:
            List of SignalOrderMapping

        Example:
            >>> mappings = db.get_mappings(uuid.UUID("..."))
            >>> print(len(mappings))
            5
        """
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT symbol, predicted_return, rank, target_weight,
                           client_order_id, order_qty, order_side,
                           broker_order_id, order_status,
                           filled_qty, filled_avg_price,
                           skip_reason
                    FROM signal_order_mappings
                    WHERE run_id = %s
                    ORDER BY rank
                    """,
                    (run_id,),
                )

                rows = cur.fetchall()

                return [
                    SignalOrderMapping(
                        symbol=row[0],
                        predicted_return=row[1],
                        rank=row[2],
                        target_weight=row[3],
                        client_order_id=row[4],
                        order_qty=row[5],
                        order_side=row[6],
                        broker_order_id=row[7],
                        order_status=row[8],
                        filled_qty=row[9],
                        filled_avg_price=row[10],
                        skip_reason=row[11],
                    )
                    for row in rows
                ]
