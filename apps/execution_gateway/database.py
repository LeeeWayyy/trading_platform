"""
Database operations for Execution Gateway.

Provides database access for orders and positions tables with:
- CRUD operations for orders
- Position updates from fills
- Transaction management
- Connection pooling (H2 fix: uses psycopg_pool for 10x performance)

See ADR-0014 for architecture decisions.
"""

import json
import logging
import os
from collections.abc import Callable, Generator, Mapping
from contextlib import contextmanager
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, TypeVar

import psycopg
from psycopg import DatabaseError, IntegrityError, OperationalError
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from pydantic import ValidationError as PydanticValidationError

from apps.execution_gateway.schemas import OrderDetail, OrderRequest, Position

logger = logging.getLogger(__name__)

# Terminal statuses used for conflict resolution and reconciliation locking.
TERMINAL_STATUSES: set[str] = {
    "filled",
    "canceled",
    "expired",
    "failed",
    "rejected",
    "replaced",
    "done_for_day",
    "blocked_kill_switch",
    "blocked_circuit_breaker",
}

# Status rank ordering for conflict resolution (kept in sync with reconciliation)
_STATUS_RANKS: dict[str, int] = {
    # Rank 1: initial
    "pending_new": 1,
    "dry_run": 1,
    # Rank 2: submitted
    "submitted": 2,
    "submitted_unconfirmed": 2,
    "new": 2,
    "accepted": 2,
    # Rank 3: active
    "pending_cancel": 3,
    "pending_replace": 3,
    "calculated": 3,
    "stopped": 3,
    "suspended": 3,
    "partially_filled": 3,
    # Rank 4: terminal (non-fill)
    "canceled": 4,
    "expired": 4,
    "failed": 4,
    "rejected": 4,
    "replaced": 4,
    "done_for_day": 4,
    "blocked_kill_switch": 4,
    "blocked_circuit_breaker": 4,
    # Rank 5: terminal (fill)
    "filled": 5,
}


def status_rank_for(status: str) -> int:
    """Return status rank for local status updates (unknown -> 0)."""
    return _STATUS_RANKS.get(status, 0)

# H2 Fix: Configurable pool settings via environment variables
# Defaults: min=2, max=10, timeout=10s (per Codex review feedback)
DB_POOL_MIN_SIZE = int(os.getenv("DB_POOL_MIN_SIZE", "2"))
DB_POOL_MAX_SIZE = int(os.getenv("DB_POOL_MAX_SIZE", "10"))
DB_POOL_TIMEOUT = float(os.getenv("DB_POOL_TIMEOUT", "10.0"))

T = TypeVar("T")


def calculate_position_update(
    old_qty: int,
    old_avg_price: Decimal,
    old_realized_pl: Decimal,
    fill_qty: int,
    fill_price: Decimal,
    side: str,
) -> tuple[int, Decimal, Decimal]:
    """
    Calculate new position state after a fill.

    Pure function for P&L calculation, extracted for testability.
    Handles all position update scenarios:
    - Opening new positions
    - Adding to existing positions (weighted average)
    - Partial closes (realize P&L, keep avg price)
    - Full closes (realize all P&L, reset avg price)
    - Position flips (realize P&L on closed portion, new avg at fill price)

    Args:
        old_qty: Current position quantity (positive=long, negative=short, 0=flat)
        old_avg_price: Current average entry price
        old_realized_pl: Current realized P&L
        fill_qty: Fill quantity (always positive, side determines direction)
        fill_price: Fill price
        side: Trade side ("buy" or "sell")

    Returns:
        Tuple of (new_qty, new_avg_price, new_realized_pl)

    Examples:
        >>> # Opening long position
        >>> calculate_position_update(0, Decimal("0"), Decimal("0"), 100, Decimal("150"), "buy")
        (100, Decimal('150'), Decimal('0'))

        >>> # Partial close with profit
        >>> calculate_position_update(100, Decimal("100"), Decimal("0"), 50, Decimal("120"), "sell")
        (50, Decimal('100'), Decimal('1000'))

        >>> # Position flip (long to short)
        >>> calculate_position_update(50, Decimal("100"), Decimal("0"), 100, Decimal("120"), "sell")
        (-50, Decimal('120'), Decimal('1000'))
    """
    # Convert side to signed qty
    signed_fill_qty = fill_qty if side == "buy" else -fill_qty
    new_qty = old_qty + signed_fill_qty

    # Determine if this is adding to position or reducing it
    is_adding_to_position = (old_qty > 0 and side == "buy") or (old_qty < 0 and side == "sell")

    # Gemini MEDIUM fix: Extract P&L calculation into helper to reduce duplication
    def _get_realized_pnl(qty_closed: int) -> Decimal:
        """Calculate P&L for the portion of the position being closed."""
        if side == "sell" and old_qty > 0:  # Closing a long
            return (fill_price - old_avg_price) * qty_closed
        if side == "buy" and old_qty < 0:  # Closing a short
            return (old_avg_price - fill_price) * qty_closed
        return Decimal("0")

    if new_qty == 0:
        # Position fully closed - realize all P&L
        pnl = _get_realized_pnl(abs(signed_fill_qty))

        # Reset avg_entry_price to 0 for closed positions (intentional design choice)
        # Exit price is captured in realized_pl; no position = no entry price
        # If UI needs exit price, use last_trade_at timestamp to query order fills
        new_avg_price = Decimal("0")
        new_realized_pl = old_realized_pl + pnl

    elif old_qty == 0:
        # Opening new position
        new_avg_price = fill_price
        new_realized_pl = old_realized_pl

    elif is_adding_to_position:
        # Adding to existing position - update weighted average, no P&L
        total_cost = (old_avg_price * abs(old_qty)) + (fill_price * abs(signed_fill_qty))
        new_avg_price = total_cost / abs(new_qty)
        new_realized_pl = old_realized_pl

    elif old_qty * new_qty < 0:
        # Position FLIP - crossed through flat (e.g., long 50, sell 100)
        # Realize P&L only on closed portion (old_qty shares)
        # New position starts at fill price
        pnl = _get_realized_pnl(abs(old_qty))
        new_avg_price = fill_price
        new_realized_pl = old_realized_pl + pnl

    else:
        # Partial close (same sign) - realize P&L on closed portion, keep avg price
        pnl = _get_realized_pnl(abs(signed_fill_qty))
        new_avg_price = old_avg_price
        new_realized_pl = old_realized_pl + pnl

    return (new_qty, new_avg_price, new_realized_pl)


class DatabaseClient:
    """
    Database client for orders and positions.

    Handles all database operations for the execution gateway, including:
    - Creating and updating orders
    - Querying order status
    - Updating positions from fills
    - Transaction management
    - Connection pooling (H2 fix: 10x performance improvement)

    Args:
        db_conn_string: PostgreSQL connection string
            Format: postgresql://user:pass@host:port/dbname

    Examples:
        >>> db = DatabaseClient("postgresql://localhost/trading_platform")
        >>> # Create order record
        >>> order = db.create_order(
        ...     client_order_id="abc123...",
        ...     strategy_id="alpha_baseline",
        ...     order_request=OrderRequest(symbol="AAPL", side="buy", qty=10),
        ...     status="pending_new",
        ...     broker_order_id="broker123"
        ... )
        >>> # Close pool when done (optional - for clean shutdown)
        >>> db.close()

    Notes:
        - Pool opens lazily on first connection request (tests/scripts work without setup)
        - Call close() for clean shutdown in production (FastAPI lifespan handles this)
        - Pool size configurable via DB_POOL_MIN_SIZE, DB_POOL_MAX_SIZE, DB_POOL_TIMEOUT
    """

    def __init__(self, db_conn_string: str):
        """
        Initialize database client with connection pool.

        Args:
            db_conn_string: PostgreSQL connection string

        Raises:
            ValueError: If connection string is empty

        Notes:
            Pool uses lazy open (open=True default) - connections created on first use.
            This ensures tests and scripts work without explicit pool setup.
        """
        if not db_conn_string:
            raise ValueError("db_conn_string cannot be empty")

        self.db_conn_string = db_conn_string

        # H2 Fix: Connection pooling for 10x performance
        # Pool opens lazily on first .connection() call (no explicit open needed)
        self._pool = ConnectionPool(
            db_conn_string,
            min_size=DB_POOL_MIN_SIZE,
            max_size=DB_POOL_MAX_SIZE,
            timeout=DB_POOL_TIMEOUT,
            # open=True is default - pool opens lazily on first connection request
        )

        logger.info(
            "DatabaseClient initialized with connection pool",
            extra={
                "db": db_conn_string.split("@")[1] if "@" in db_conn_string else "local",
                "pool_min": DB_POOL_MIN_SIZE,
                "pool_max": DB_POOL_MAX_SIZE,
                "pool_timeout": DB_POOL_TIMEOUT,
            },
        )

    def close(self) -> None:
        """
        Close connection pool. Safe to call multiple times.

        Should be called during application shutdown for clean resource cleanup.
        FastAPI apps should call this in lifespan shutdown handler.
        """
        self._pool.close()
        logger.info("DatabaseClient connection pool closed")

    def _execute_with_conn(
        self,
        conn: psycopg.Connection | None,
        operation: Callable[[psycopg.Connection], T],
    ) -> T:
        """
        Execute database operation with optional connection.

        Helper to handle two connection modes:
        1. conn=None: Create and manage own connection (auto-commit on success)
        2. conn provided: Use provided connection (transactional mode)

        Args:
            conn: Optional database connection for transactional use
            operation: Callable that takes a connection and returns a result

        Returns:
            Result of the operation

        Example:
            >>> def insert_order(conn):
            ...     with conn.cursor() as cur:
            ...         cur.execute("INSERT INTO orders ...")
            ...         return cur.fetchone()
            >>> result = db._execute_with_conn(None, insert_order)
        """
        # Use provided connection (transactional mode - caller handles commit)
        if conn is not None:
            return operation(conn)

        # H2 Fix: Use connection from pool instead of creating new connection
        # IMPORTANT: psycopg context manager does NOT auto-commit - it rolls back on exit
        # We must explicitly commit before the context manager exits
        with self._pool.connection() as new_conn:
            result = operation(new_conn)
            new_conn.commit()
            return result

    @contextmanager
    def transaction(self) -> Generator[psycopg.Connection, None, None]:
        """
        Context manager for executing multiple database operations in a single transaction.

        Provides a connection that will automatically commit on success or rollback
        on exception. Use this when multiple operations need atomic behavior.

        Yields:
            psycopg.Connection: Database connection with transaction support

        Raises:
            Exception: Re-raises any exception that occurs within the transaction context
                after performing a rollback. Common exception types include:
                - psycopg.IntegrityError: Constraint violations (e.g., duplicate keys)
                - psycopg.DatabaseError: Database-level errors (e.g., connection loss)
                - ValueError, TypeError: Application-level validation errors
                - Any other exception raised by operations within the context

        Examples:
            >>> db = DatabaseClient("postgresql://localhost/trading_platform")
            >>> # Atomic parent + child order creation
            >>> with db.transaction() as conn:
            ...     parent = db.create_parent_order(..., conn=conn)
            ...     for slice_detail in slices:
            ...         db.create_child_slice(..., conn=conn)
            >>> # On success: both committed. On error: both rolled back.

        Notes:
            - Pass the connection to methods that support it via `conn=` parameter
            - Transaction auto-commits on successful context exit
            - Transaction auto-rollbacks on any exception, then re-raises original exception
            - Connection auto-closes after commit or rollback
            - Nested transactions are NOT supported - opening multiple contexts will
              create separate connections and transactions
            - Rollback is logged at WARNING level with error type and message
        """
        # H2 Fix: Use pool.connection() context manager for transaction control
        # psycopg_pool.ConnectionPool uses .connection() not .getconn()/.putconn()
        # Use psycopg's built-in transaction context manager for automatic commit/rollback
        with self._pool.connection() as conn:
            with conn.transaction():
                try:
                    yield conn
                    logger.debug("Transaction committed successfully")
                except Exception as e:
                    logger.warning(
                        f"Transaction rolled back due to error: {e}",
                        extra={"error_type": type(e).__name__, "error_message": str(e)},
                    )
                    raise

    def create_order(
        self,
        client_order_id: str,
        strategy_id: str,
        order_request: OrderRequest,
        status: str,
        broker_order_id: str | None = None,
        error_message: str | None = None,
    ) -> OrderDetail:
        """
        Create new order record in database.

        Args:
            client_order_id: Deterministic client order ID
            strategy_id: Strategy identifier (e.g., "alpha_baseline")
            order_request: Order request details
            status: Initial order status (dry_run, pending_new, etc.)
            broker_order_id: Alpaca's order ID (None for dry_run)
            error_message: Error message if submission failed (optional)

        Returns:
            OrderDetail with created order information

        Raises:
            IntegrityError: If order with same client_order_id already exists
            DatabaseError: If database operation fails

        Examples:
            >>> db = DatabaseClient("postgresql://localhost/trading_platform")
            >>> order_request = OrderRequest(
            ...     symbol="AAPL",
            ...     side="buy",
            ...     qty=10,
            ...     order_type="market"
            ... )
            >>> order = db.create_order(
            ...     client_order_id="abc123...",
            ...     strategy_id="alpha_baseline",
            ...     order_request=order_request,
            ...     status="pending_new",
            ...     broker_order_id="broker123"
            ... )
            >>> order.status
            'pending_new'
        """
        try:
            with self._pool.connection() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    submitted_at = datetime.now(UTC) if status != "dry_run" else None

                    cur.execute(
                        """
                        INSERT INTO orders (
                            client_order_id,
                            strategy_id,
                            symbol,
                            side,
                            qty,
                            order_type,
                            limit_price,
                            stop_price,
                            time_in_force,
                            status,
                            broker_order_id,
                            error_message,
                            submitted_at,
                            created_at,
                            updated_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                        RETURNING *
                        """,
                        (
                            client_order_id,
                            strategy_id,
                            order_request.symbol,
                            order_request.side,
                            order_request.qty,
                            order_request.order_type,
                            order_request.limit_price,
                            order_request.stop_price,
                            order_request.time_in_force,
                            status,
                            broker_order_id,
                            error_message,
                            submitted_at,
                        ),
                    )

                    row = cur.fetchone()
                    conn.commit()

                    if row is None:
                        raise ValueError(f"Failed to create order: {client_order_id}")

                    logger.info(
                        "Order created in database",
                        extra={
                            "client_order_id": client_order_id,
                            "symbol": order_request.symbol,
                            "status": status,
                        },
                    )

                    return OrderDetail(**row)

        except IntegrityError:
            logger.warning(
                f"Order already exists: {client_order_id}",
                extra={"client_order_id": client_order_id},
            )
            raise

        except (OperationalError, DatabaseError) as e:
            logger.error(f"Database error creating order: {e}")
            raise

    def create_parent_order(
        self,
        client_order_id: str,
        strategy_id: str,
        order_request: OrderRequest,
        total_slices: int,
        status: str = "pending_new",
        conn: psycopg.Connection | None = None,
    ) -> OrderDetail:
        """
        Create parent order for TWAP slicing.

        Parent orders have parent_order_id=NULL and total_slices set to the
        number of child slices planned. They serve as logical containers for
        time-distributed child slice execution.

        Args:
            client_order_id: Unique parent order ID (deterministic)
            strategy_id: Strategy identifier (e.g., "twap_parent")
            order_request: Order parameters (symbol, side, qty, etc.)
            total_slices: Number of child slices planned
            status: Initial order status (default: "pending_new")
            conn: Optional database connection for transactional use
                  (if provided, caller is responsible for commit/rollback)

        Returns:
            OrderDetail with created parent order information

        Raises:
            IntegrityError: If order with same client_order_id already exists
            DatabaseError: If database operation fails

        Example:
            >>> db = DatabaseClient("postgresql://localhost/trading_platform")
            >>> order_request = OrderRequest(
            ...     symbol="AAPL",
            ...     side="buy",
            ...     qty=100,
            ...     order_type="market"
            ... )
            >>> parent = db.create_parent_order(
            ...     client_order_id="abc123...",
            ...     strategy_id="twap_parent",
            ...     order_request=order_request,
            ...     total_slices=5
            ... )
            >>> parent.parent_order_id is None
            True
            >>> parent.total_slices
            5

        Notes:
            - parent_order_id is explicitly set to NULL for parent orders
            - total_slices indicates how many child slices will be created
            - slice_num and scheduled_time are NULL for parent orders
            - Parent orders are typically not submitted to broker directly
            - When using with transaction(), pass conn= to avoid auto-commit
        """

        def _execute_insert(conn: psycopg.Connection) -> OrderDetail:
            """Helper to execute parent order insert and return OrderDetail."""
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    INSERT INTO orders (
                        client_order_id,
                        strategy_id,
                        symbol,
                        side,
                        qty,
                        order_type,
                        limit_price,
                        stop_price,
                        time_in_force,
                        status,
                        parent_order_id,
                        total_slices,
                        created_at,
                        updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NULL, %s, NOW(), NOW())
                    RETURNING *
                    """,
                    (
                        client_order_id,
                        strategy_id,
                        order_request.symbol,
                        order_request.side,
                        order_request.qty,
                        order_request.order_type,
                        order_request.limit_price,
                        order_request.stop_price,
                        order_request.time_in_force,
                        status,
                        total_slices,
                    ),
                )

                row = cur.fetchone()

            if row is None:
                raise ValueError(f"Failed to create parent order: {client_order_id}")

            logger.info(
                f"Parent order created in database: {client_order_id}",
                extra={
                    "client_order_id": client_order_id,
                    "symbol": order_request.symbol,
                    "total_slices": total_slices,
                    "status": status,
                },
            )

            return OrderDetail(**row)

        try:
            # Use helper to handle connection management
            return self._execute_with_conn(conn, _execute_insert)

        except IntegrityError:
            logger.warning(
                f"Parent order already exists: {client_order_id}",
                extra={"client_order_id": client_order_id},
            )
            raise

        except (OperationalError, DatabaseError) as e:
            logger.error(f"Database error creating parent order: {e}")
            raise

    def create_child_slice(
        self,
        client_order_id: str,
        parent_order_id: str,
        slice_num: int,
        strategy_id: str,
        order_request: OrderRequest,
        scheduled_time: datetime,
        status: str = "pending_new",
        conn: psycopg.Connection | None = None,
    ) -> OrderDetail:
        """
        Create child slice order for TWAP execution.

        Child slices reference their parent order via parent_order_id and include
        a slice_num for ordering and a scheduled_time for timed execution.

        Args:
            client_order_id: Unique child slice order ID (deterministic)
            parent_order_id: Parent order's client_order_id
            slice_num: Slice number (0-indexed)
            strategy_id: Strategy identifier (e.g., "twap_slice_<parent_id>_0")
            order_request: Order parameters (symbol, side, qty, etc.)
            scheduled_time: When to execute this slice (UTC)
            status: Initial order status (default: "pending_new")
            conn: Optional database connection for transactional use
                  (if provided, caller is responsible for commit/rollback)

        Returns:
            OrderDetail with created child slice information

        Raises:
            IntegrityError: If order with same client_order_id already exists,
                           or if (parent_order_id, slice_num) already exists
            DatabaseError: If database operation fails

        Example:
            >>> db = DatabaseClient("postgresql://localhost/trading_platform")
            >>> from datetime import datetime, timedelta, UTC
            >>> order_request = OrderRequest(
            ...     symbol="AAPL",
            ...     side="buy",
            ...     qty=20,
            ...     order_type="market"
            ... )
            >>> child = db.create_child_slice(
            ...     client_order_id="def456...",
            ...     parent_order_id="abc123...",
            ...     slice_num=0,
            ...     strategy_id="twap_slice_abc123_0",
            ...     order_request=order_request,
            ...     scheduled_time=datetime.now(UTC) + timedelta(minutes=1)
            ... )
            >>> child.parent_order_id
            'abc123...'
            >>> child.slice_num
            0

        Notes:
            - parent_order_id must reference an existing parent order
            - (parent_order_id, slice_num) must be unique (enforced by DB index)
            - slice_num should be 0-indexed and sequential
            - scheduled_time is used by scheduler to determine execution timing
            - When using with transaction(), pass conn= to avoid auto-commit
        """

        def _execute_insert(conn: psycopg.Connection) -> OrderDetail:
            """Helper to execute child slice insert and return OrderDetail."""
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    INSERT INTO orders (
                        client_order_id,
                        parent_order_id,
                        slice_num,
                        strategy_id,
                        symbol,
                        side,
                        qty,
                        order_type,
                        limit_price,
                        stop_price,
                        time_in_force,
                        scheduled_time,
                        status,
                        created_at,
                        updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                    RETURNING *
                    """,
                    (
                        client_order_id,
                        parent_order_id,
                        slice_num,
                        strategy_id,
                        order_request.symbol,
                        order_request.side,
                        order_request.qty,
                        order_request.order_type,
                        order_request.limit_price,
                        order_request.stop_price,
                        order_request.time_in_force,
                        scheduled_time,
                        status,
                    ),
                )

                row = cur.fetchone()

            if row is None:
                raise ValueError(f"Failed to create child slice: {client_order_id}")

            logger.info(
                f"Child slice created in database: {client_order_id}",
                extra={
                    "client_order_id": client_order_id,
                    "parent_order_id": parent_order_id,
                    "slice_num": slice_num,
                    "symbol": order_request.symbol,
                    "status": status,
                },
            )

            return OrderDetail(**row)

        try:
            # Use helper to handle connection management
            return self._execute_with_conn(conn, _execute_insert)

        except IntegrityError as e:
            logger.warning(
                f"Child slice already exists or duplicate slice_num: {client_order_id}",
                extra={
                    "client_order_id": client_order_id,
                    "parent_order_id": parent_order_id,
                    "slice_num": slice_num,
                    "error": str(e),
                },
            )
            raise

        except (OperationalError, DatabaseError) as e:
            logger.error(f"Database error creating child slice: {e}")
            raise

    def get_slices_by_parent_id(self, parent_order_id: str) -> list[OrderDetail]:
        """
        Get all child slices for a parent order, ordered by slice_num.

        Args:
            parent_order_id: Parent order's client_order_id

        Returns:
            List of OrderDetail for all child slices, ordered by slice_num
            (empty list if parent has no slices)

        Raises:
            DatabaseError: If database operation fails

        Example:
            >>> db = DatabaseClient("postgresql://localhost/trading_platform")
            >>> slices = db.get_slices_by_parent_id("abc123...")
            >>> len(slices)
            5
            >>> slices[0].slice_num
            0
            >>> slices[0].parent_order_id
            'abc123...'

        Notes:
            - Returns slices in slice_num order (0, 1, 2, ...)
            - Includes all slices regardless of status
            - Returns empty list if parent_order_id not found or has no slices
        """
        try:
            with self._pool.connection() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(
                        """
                        SELECT * FROM orders
                        WHERE parent_order_id = %s
                        ORDER BY slice_num
                        """,
                        (parent_order_id,),
                    )

                    rows = cur.fetchall()

                    logger.info(
                        f"Retrieved {len(rows)} slices for parent: {parent_order_id}",
                        extra={"parent_order_id": parent_order_id, "slice_count": len(rows)},
                    )

                    return [OrderDetail(**row) for row in rows]

        except (OperationalError, DatabaseError) as e:
            logger.error(f"Database error fetching slices: {e}")
            raise

    def cancel_pending_slices(self, parent_order_id: str) -> int:
        """
        Cancel all pending child slices for a parent order.

        Updates all child slices with status="pending_new" to status="canceled".
        Used when parent order is canceled or circuit breaker trips.

        Args:
            parent_order_id: Parent order's client_order_id

        Returns:
            Number of slices canceled

        Raises:
            DatabaseError: If database operation fails

        Example:
            >>> db = DatabaseClient("postgresql://localhost/trading_platform")
            >>> # Cancel all pending slices for parent
            >>> canceled_count = db.cancel_pending_slices("abc123...")
            >>> canceled_count
            3

        Notes:
            - Only cancels slices with status="pending_new"
            - Does not affect slices already accepted/filled/canceled
            - Returns 0 if parent has no pending slices
            - Sets updated_at timestamp to NOW()
        """
        try:
            with self._pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE orders
                        SET status = 'canceled', updated_at = NOW()
                        WHERE parent_order_id = %s AND status = 'pending_new'
                        """,
                        (parent_order_id,),
                    )

                    canceled_count = cur.rowcount
                    conn.commit()

                    logger.info(
                        f"Canceled {canceled_count} pending slices for parent: {parent_order_id}",
                        extra={
                            "parent_order_id": parent_order_id,
                            "canceled_count": canceled_count,
                        },
                    )

                    return canceled_count

        except (OperationalError, DatabaseError) as e:
            logger.error(f"Database error canceling pending slices: {e}")
            raise

    def get_order_by_client_id(self, client_order_id: str) -> OrderDetail | None:
        """
        Get order by client_order_id.

        Args:
            client_order_id: Client order ID to lookup

        Returns:
            OrderDetail if found, None otherwise

        Raises:
            DatabaseError: If database operation fails

        Examples:
            >>> db = DatabaseClient("postgresql://localhost/trading_platform")
            >>> order = db.get_order_by_client_id("abc123...")
            >>> if order:
            ...     print(f"Order status: {order.status}")
        """
        try:
            with self._pool.connection() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(
                        """
                        SELECT * FROM orders
                        WHERE client_order_id = %s
                        """,
                        (client_order_id,),
                    )

                    row = cur.fetchone()

                    if not row:
                        return None

                    return OrderDetail(**row)

        except (OperationalError, DatabaseError) as e:
            logger.error(f"Database error fetching order: {e}")
            raise

    def get_order_ids_by_client_ids(self, client_order_ids: list[str]) -> set[str]:
        """
        Return existing client_order_ids for the provided list.

        Used by reconciliation to avoid misclassifying terminal orders as orphans.
        """
        ids = [client_id for client_id in dict.fromkeys(client_order_ids) if client_id]
        if not ids:
            return set()

        try:
            with self._pool.connection() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(
                        """
                        SELECT client_order_id
                        FROM orders
                        WHERE client_order_id = ANY(%s)
                        """,
                        (ids,),
                    )
                    rows = cur.fetchall()
                    return {row["client_order_id"] for row in rows if row.get("client_order_id")}
        except (OperationalError, DatabaseError) as e:
            logger.error(f"Database error fetching order ids: {e}")
            raise

    def get_non_terminal_orders(self, created_before: datetime | None = None) -> list[OrderDetail]:
        """Return all non-terminal orders (optionally filtered by created_at)."""
        try:
            with self._pool.connection() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    if created_before is None:
                        cur.execute(
                            "SELECT * FROM orders WHERE is_terminal = FALSE ORDER BY created_at ASC",
                        )
                    else:
                        cur.execute(
                            """
                            SELECT * FROM orders
                            WHERE is_terminal = FALSE AND created_at <= %s
                            ORDER BY created_at ASC
                            """,
                            (created_before,),
                        )
                    rows = cur.fetchall()
                    return [OrderDetail(**row) for row in rows]
        except (OperationalError, DatabaseError) as e:
            logger.error(f"Database error fetching non-terminal orders: {e}")
            raise

    def get_reconciliation_high_water_mark(self, name: str = "orders") -> datetime | None:
        """Return reconciliation high-water mark timestamp for the given name."""
        try:
            with self._pool.connection() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(
                        """
                        SELECT last_check_time
                        FROM reconciliation_high_water_mark
                        WHERE name = %s
                        """,
                        (name,),
                    )
                    row = cur.fetchone()
                    if not row:
                        return None
                    last_check: datetime = row["last_check_time"]
                    return last_check
        except (OperationalError, DatabaseError) as e:
            logger.error(f"Database error fetching reconciliation high-water mark: {e}")
            raise

    def set_reconciliation_high_water_mark(
        self, last_check_time: datetime, name: str = "orders"
    ) -> None:
        """Upsert reconciliation high-water mark timestamp."""
        try:
            with self._pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO reconciliation_high_water_mark (name, last_check_time, updated_at)
                        VALUES (%s, %s, NOW())
                        ON CONFLICT (name)
                        DO UPDATE SET last_check_time = EXCLUDED.last_check_time, updated_at = NOW()
                        """,
                        (name, last_check_time),
                    )
                    conn.commit()
        except (OperationalError, DatabaseError) as e:
            logger.error(f"Database error updating reconciliation high-water mark: {e}")
            raise

    def create_orphan_order(
        self,
        broker_order_id: str,
        client_order_id: str | None,
        symbol: str,
        strategy_id: str,
        side: str,
        qty: int,
        estimated_notional: Decimal,
        status: str,
    ) -> None:
        """Insert a broker order that is missing from local DB into orphan_orders."""
        try:
            with self._pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO orphan_orders (
                            broker_order_id,
                            client_order_id,
                            symbol,
                            strategy_id,
                            side,
                            qty,
                            estimated_notional,
                            status,
                            detected_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
                        ON CONFLICT (broker_order_id) DO NOTHING
                        """,
                        (
                            broker_order_id,
                            client_order_id,
                            symbol,
                            strategy_id,
                            side,
                            qty,
                            estimated_notional,
                            status,
                        ),
                    )
                    conn.commit()
        except (OperationalError, DatabaseError) as e:
            logger.error(f"Database error creating orphan order: {e}")
            raise

    def update_orphan_order_status(
        self, broker_order_id: str, status: str, resolved_at: datetime | None = None
    ) -> None:
        """Update orphan order status and optionally mark as resolved."""
        try:
            with self._pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE orphan_orders
                        SET status = %s,
                            resolved_at = COALESCE(%s, resolved_at)
                        WHERE broker_order_id = %s
                        """,
                        (status, resolved_at, broker_order_id),
                    )
                    conn.commit()
        except (OperationalError, DatabaseError) as e:
            logger.error(f"Database error updating orphan order: {e}")
            raise

    def get_orphan_exposure(self, symbol: str, strategy_id: str) -> Decimal:
        """Return total unresolved orphan notional for symbol/strategy (includes external)."""
        try:
            with self._pool.connection() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(
                        """
                        SELECT COALESCE(SUM(estimated_notional), 0) AS total
                        FROM orphan_orders
                        WHERE resolved_at IS NULL
                          AND symbol = %s
                          AND strategy_id IN (%s, 'external')
                        """,
                        (symbol, strategy_id),
                    )
                    row = cur.fetchone()
                    total = row["total"] if row else 0
                    return Decimal(str(total))
        except (OperationalError, DatabaseError) as e:
            logger.error(f"Database error fetching orphan exposure: {e}")
            raise

    def update_order_status(
        self,
        client_order_id: str,
        status: str,
        broker_order_id: str | None = None,
        filled_qty: Decimal | None = None,
        filled_avg_price: Decimal | None = None,
        error_message: str | None = None,
    ) -> OrderDetail | None:
        """
        Update order status and fill details.

        Called when order status changes (via webhook or polling).

        Args:
            client_order_id: Client order ID
            status: New order status
            broker_order_id: Broker order ID (if newly available)
            filled_qty: Filled quantity (if order filled)
            filled_avg_price: Average fill price (if order filled)
            error_message: Error message (if order rejected)

        Returns:
            Updated OrderDetail if found, None if not found

        Raises:
            DatabaseError: If database operation fails

        Examples:
            >>> db = DatabaseClient("postgresql://localhost/trading_platform")
            >>> # Update to filled status
            >>> order = db.update_order_status(
            ...     client_order_id="abc123...",
            ...     status="filled",
            ...     filled_qty=Decimal("10"),
            ...     filled_avg_price=Decimal("150.25")
            ... )
        """
        try:
            with self._pool.connection() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    status_rank = status_rank_for(status)
                    is_terminal = status in TERMINAL_STATUSES

                    # Determine filled_at timestamp
                    filled_at = None
                    if status == "filled" and filled_qty is not None:
                        filled_at = datetime.now(UTC)

                    # C8 Fix: Don't use COALESCE for error_message
                    # COALESCE prevents clearing error_message by passing None
                    # For error_message: None clears, value sets (intended behavior for recovery)
                    # Other fields still use COALESCE to preserve existing values when None passed
                    cur.execute(
                        """
                        UPDATE orders
                        SET
                            status = %s,
                            broker_order_id = COALESCE(%s, broker_order_id),
                            filled_qty = COALESCE(%s, filled_qty),
                            filled_avg_price = COALESCE(%s, filled_avg_price),
                            error_message = %s,
                            filled_at = COALESCE(%s, filled_at),
                            last_updated_at = NOW(),
                            status_rank = %s,
                            is_terminal = %s,
                            updated_at = NOW()
                        WHERE client_order_id = %s
                        RETURNING *
                        """,
                        (
                            status,
                            broker_order_id,
                            filled_qty,
                            filled_avg_price,
                            error_message,
                            filled_at,
                            status_rank,
                            is_terminal,
                            client_order_id,
                        ),
                    )

                    row = cur.fetchone()
                    conn.commit()

                    if not row:
                        logger.warning(f"Order not found for update: {client_order_id}")
                        return None

                    logger.info(
                        f"Order updated: {client_order_id} -> {status}",
                        extra={
                            "client_order_id": client_order_id,
                            "status": status,
                            "filled_qty": str(filled_qty) if filled_qty else None,
                        },
                    )

                    return OrderDetail(**row)

        except (OperationalError, DatabaseError) as e:
            logger.error(f"Database error updating order: {e}")
            raise

    def update_order_status_cas(
        self,
        client_order_id: str,
        status: str,
        broker_updated_at: datetime,
        status_rank: int,
        source_priority: int,
        filled_qty: Decimal | None = None,
        filled_avg_price: Decimal | None = None,
        filled_at: datetime | None = None,
        broker_order_id: str | None = None,
        broker_event_id: str | None = None,
        conn: psycopg.Connection | None = None,
    ) -> OrderDetail | None:
        """
        Update order status with conflict resolution (CAS).

        Ensures we don't overwrite newer broker updates while still allowing
        broker corrections for terminal orders (e.g., late fills). This is
        used by reconciliation/webhook paths and accepts a broker timestamp
        for ordering.
        """

        new_filled_qty = filled_qty if filled_qty is not None else Decimal("0")

        def _execute(connection: psycopg.Connection) -> OrderDetail | None:
            with connection.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    UPDATE orders
                    SET status = %s,
                        last_updated_at = %s,
                        status_rank = %s,
                        source_priority = %s,
                        broker_event_id = COALESCE(%s, broker_event_id),
                        broker_order_id = COALESCE(%s, broker_order_id),
                        filled_qty = GREATEST(filled_qty, %s),
                        filled_avg_price = CASE
                            WHEN %s IS NOT NULL
                                 AND (filled_avg_price IS NULL OR %s > COALESCE(filled_qty, 0))
                                THEN %s
                            ELSE filled_avg_price
                        END,
                        filled_at = COALESCE(%s, filled_at),
                        is_terminal = %s,
                        updated_at = NOW()
                    WHERE client_order_id = %s
                      AND (
                            last_updated_at IS NULL
                            OR last_updated_at < %s
                            OR (last_updated_at = %s AND status_rank < %s)
                            OR (last_updated_at = %s AND status_rank = %s AND filled_qty < %s)
                            OR (
                                last_updated_at = %s
                                AND status_rank = %s
                                AND filled_qty = %s
                                AND source_priority < %s
                            )
                          )
                    RETURNING *
                    """,
                    (
                        status,
                        broker_updated_at,
                        status_rank,
                        source_priority,
                        broker_event_id,
                        broker_order_id,
                        new_filled_qty,
                        filled_avg_price,
                        new_filled_qty,
                        filled_avg_price,
                        filled_at,
                        status in TERMINAL_STATUSES,
                        client_order_id,
                        broker_updated_at,
                        broker_updated_at,
                        status_rank,
                        broker_updated_at,
                        status_rank,
                        new_filled_qty,
                        broker_updated_at,
                        status_rank,
                        new_filled_qty,
                        source_priority,
                    ),
                )

                row = cur.fetchone()
                if row is None:
                    return None
                return OrderDetail(**row)

        return self._execute_with_conn(conn, _execute)

    def get_order_for_update(
        self, client_order_id: str, conn: psycopg.Connection
    ) -> OrderDetail | None:
        """
        Fetch an order with a row-level lock.

        Must be called within an open transaction. Uses SELECT ... FOR UPDATE
        to prevent concurrent webhook processing from appending duplicate fills.

        Args:
            client_order_id: Order ID to lock
            conn: Active database connection in a transaction

        Returns:
            OrderDetail if found, otherwise None
        """

        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT * FROM orders WHERE client_order_id = %s FOR UPDATE",
                (client_order_id,),
            )
            row = cur.fetchone()

            if row is None:
                return None

            return self._row_to_order_detail(row)

    def update_order_status_with_conn(
        self,
        client_order_id: str,
        status: str,
        filled_qty: int,
        filled_avg_price: Decimal,
        filled_at: datetime | None,
        conn: psycopg.Connection,
        broker_order_id: str | None = None,
        broker_updated_at: datetime | None = None,
        status_rank: int | None = None,
        source_priority: int | None = None,
        broker_event_id: str | None = None,
    ) -> OrderDetail | None:
        """
        Update order status using an existing transaction connection.

        Designed for webhook transactional flow where order, position, and
        metadata updates must commit atomically.

        Args:
            client_order_id: Order ID to update
            status: New order status
            filled_qty: Cumulative filled quantity
            filled_avg_price: Cumulative weighted average fill price
            filled_at: Fill timestamp (set on final fill only)
            conn: Active database connection in a transaction
            broker_order_id: Broker-assigned order ID (optional, persisted if provided)

        Returns:
            Updated OrderDetail if found, otherwise None
        """
        if broker_updated_at is not None and status_rank is not None and source_priority is not None:
            return self.update_order_status_cas(
                client_order_id=client_order_id,
                status=status,
                broker_updated_at=broker_updated_at,
                status_rank=status_rank,
                source_priority=source_priority,
                filled_qty=Decimal(str(filled_qty)),
                filled_avg_price=filled_avg_price,
                filled_at=filled_at,
                broker_order_id=broker_order_id,
                broker_event_id=broker_event_id,
                conn=conn,
            )

        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                UPDATE orders
                SET status = %s,
                    filled_qty = %s,
                    filled_avg_price = %s,
                    filled_at = COALESCE(%s, filled_at),
                    broker_order_id = COALESCE(%s, broker_order_id),
                    updated_at = NOW()
                WHERE client_order_id = %s
                RETURNING *
                """,
                (status, filled_qty, filled_avg_price, filled_at, broker_order_id, client_order_id),
            )

            row = cur.fetchone()
            if row is None:
                logger.warning(
                    "Order not found during transactional update",
                    extra={"client_order_id": client_order_id},
                )
                return None

            return OrderDetail(**row)

    def get_position_for_update(self, symbol: str, conn: psycopg.Connection) -> Position | None:
        """
        Fetch a position row with symbol-scoped advisory lock.

        When the position row does not yet exist, row-level locks are skipped by
        PostgreSQL. We take a transactional advisory lock keyed by the symbol to
        serialize concurrent webhook fills for new symbols and avoid lost
        updates. The lock is held for the duration of the transaction.

        Args:
            symbol: Ticker symbol
            conn: Active transaction connection

        Returns:
            Position if found, otherwise None
        """

        with conn.cursor(row_factory=dict_row) as cur:
            # Serialize by symbol even when no row exists yet (handles first fill).
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (symbol,))

            cur.execute("SELECT * FROM positions WHERE symbol = %s FOR UPDATE", (symbol,))
            row = cur.fetchone()
            if row is None:
                return None
            return Position(**row)

    def append_fill_to_order_metadata(
        self, client_order_id: str, fill_data: dict[str, Any], conn: psycopg.Connection
    ) -> OrderDetail | None:
        """
        Append a single fill record to the order metadata inside a transaction.

        Uses jsonb_set to create the fills array if missing and to increment
        total_realized_pl. Caller must provide an open transaction connection
        to ensure atomicity with related updates.

        DESIGN DECISION: The nested jsonb_set SQL is intentionally verbose to handle
        all edge cases (null metadata, missing fills array, missing total_realized_pl)
        in a single atomic UPDATE. Alternative approaches like Python-side merge +
        full JSON replacement risk race conditions under concurrent webhooks.

        Args:
            client_order_id: Order ID to update
            fill_data: Dict containing fill_id, fill_qty, fill_price, realized_pl, timestamp
            conn: Active database connection

        Returns:
            Updated OrderDetail with metadata, or None if order not found
        """

        fill_json = json.dumps(fill_data)

        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                UPDATE orders
                SET
                    metadata = jsonb_set(
                        jsonb_set(
                            COALESCE(metadata, '{}'::jsonb),
                            '{fills}',
                            COALESCE((metadata->'fills'), '[]'::jsonb) || %s::jsonb
                        ),
                        '{total_realized_pl}',
                        to_jsonb(
                            COALESCE((metadata->>'total_realized_pl')::NUMERIC, 0)
                            + (%s::jsonb->>'realized_pl')::NUMERIC
                        )
                    ),
                    updated_at = NOW()
                WHERE client_order_id = %s
                RETURNING *
                """,
                (fill_json, fill_json, client_order_id),
            )

            row = cur.fetchone()

            if row is None:
                logger.warning(
                    "Order not found for fill metadata append",
                    extra={"client_order_id": client_order_id},
                )
                return None

            try:
                return self._row_to_order_detail(row)
            except PydanticValidationError as exc:
                logger.warning(
                    "Failed to build OrderDetail from metadata append row",
                    extra={"client_order_id": client_order_id, "error": str(exc)},
                )
                return None

    def update_position_on_fill_with_conn(
        self,
        symbol: str,
        fill_qty: int,
        fill_price: Decimal,
        side: str,
        conn: psycopg.Connection,
    ) -> Position:
        """
        Update a position inside an existing transaction.

        Caller must lock the position row with get_position_for_update before
        invoking this method to avoid race conditions under concurrent webhooks.

        Args:
            symbol: Stock symbol
            fill_qty: Incremental fill quantity (absolute value)
            fill_price: Per-fill execution price
            side: 'buy' or 'sell'
            conn: Active transaction connection

        Returns:
            Updated Position
        """

        with conn.cursor(row_factory=dict_row) as cur:
            # Load current position (row is already locked by caller)
            cur.execute("SELECT * FROM positions WHERE symbol = %s", (symbol,))
            current = cur.fetchone()

            if current:
                old_qty = int(current["qty"])
                old_avg_price = Decimal(str(current["avg_entry_price"]))
                old_realized_pl = Decimal(str(current["realized_pl"]))
            else:
                old_qty = 0
                old_avg_price = Decimal("0")
                old_realized_pl = Decimal("0")

            new_qty, new_avg_price, new_realized_pl = calculate_position_update(
                old_qty=old_qty,
                old_avg_price=old_avg_price,
                old_realized_pl=old_realized_pl,
                fill_qty=fill_qty,
                fill_price=Decimal(str(fill_price)),
                side=side,
            )

            cur.execute(
                """
                INSERT INTO positions (
                    symbol,
                    qty,
                    avg_entry_price,
                    realized_pl,
                    updated_at,
                    last_trade_at
                )
                VALUES (%s, %s, %s, %s, NOW(), NOW())
                ON CONFLICT (symbol)
                DO UPDATE SET
                    qty = EXCLUDED.qty,
                    avg_entry_price = EXCLUDED.avg_entry_price,
                    realized_pl = EXCLUDED.realized_pl,
                    updated_at = NOW(),
                    last_trade_at = NOW()
                RETURNING *
                """,
                (symbol, new_qty, new_avg_price, new_realized_pl),
            )

            row = cur.fetchone()

            if row is None:
                raise ValueError(f"Failed to update position for symbol: {symbol}")

            logger.info(
                "Position updated transactionally",
                extra={
                    "symbol": symbol,
                    "old_qty": str(old_qty),
                    "new_qty": str(new_qty),
                    "avg_price": str(new_avg_price),
                },
            )

            return Position(**row)

    # ------------------------------------------------------------------
    # Performance dashboard helpers (P4T6.2)
    # ------------------------------------------------------------------

    def get_data_availability_date(self) -> date | None:
        """Return earliest date with fill metadata (using fill timestamps)."""

        def _execute(conn: psycopg.Connection) -> date | None:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT MIN(DATE((fill->>'timestamp')::timestamptz AT TIME ZONE 'UTC')) AS first_date
                    FROM orders o,
                         jsonb_array_elements(o.metadata->'fills') AS fill
                    WHERE o.status IN ('filled', 'partially_filled')
                      AND o.metadata ? 'fills'
                      AND jsonb_array_length(o.metadata->'fills') > 0
                    """
                )
                row = cur.fetchone()
                return row["first_date"] if row and row["first_date"] else None

        return self._execute_with_conn(None, _execute)

    def get_daily_pnl_history(
        self, start_date: date, end_date: date, strategies: list[str]
    ) -> list[dict[str, Any]]:
        """
        Fetch daily realized P&L aggregated by fill timestamps for specific strategies.

        Returns list of dicts with keys: trade_date, daily_realized_pl, closing_trade_count.
        """

        if not strategies:
            return []

        def _execute(conn: psycopg.Connection) -> list[dict[str, Any]]:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT
                        DATE((fill->>'timestamp')::timestamptz AT TIME ZONE 'UTC') AS trade_date,
                        SUM((fill->>'realized_pl')::NUMERIC) AS daily_realized_pl,
                        COUNT(*) FILTER (
                            WHERE (fill->>'realized_pl')::NUMERIC != 0
                        ) AS closing_trade_count
                    FROM orders o,
                         jsonb_array_elements(o.metadata->'fills') AS fill
                    WHERE o.status IN ('filled', 'partially_filled')
                      AND o.metadata ? 'fills'
                      AND jsonb_array_length(o.metadata->'fills') > 0
                      AND (fill->>'timestamp')::timestamptz >= %s::timestamptz
                      AND (fill->>'timestamp')::timestamptz < (%s::timestamptz + interval '1 day')
                      AND o.strategy_id = ANY(%s)
                    GROUP BY DATE((fill->>'timestamp')::timestamptz AT TIME ZONE 'UTC')
                    ORDER BY trade_date
                    """,
                    (start_date, end_date, strategies),
                )

                rows = cur.fetchall()
                return [dict(row) for row in rows]

        return self._execute_with_conn(None, _execute)

    def update_position_on_fill(self, symbol: str, qty: int, price: Decimal, side: str) -> Position:
        """
        Update position when order is filled.

        This method:
        1. Fetches current position (if any)
        2. Calculates new position qty and avg_entry_price
        3. Upserts position record
        4. Returns updated position

        Args:
            symbol: Stock symbol
            qty: Fill quantity (always positive)
            price: Fill price
            side: Order side ("buy" or "sell")

        Returns:
            Updated Position

        Raises:
            DatabaseError: If database operation fails

        Examples:
            >>> db = DatabaseClient("postgresql://localhost/trading_platform")
            >>> # Buy 10 AAPL at $150
            >>> position = db.update_position_on_fill(
            ...     symbol="AAPL",
            ...     qty=10,
            ...     price=Decimal("150.00"),
            ...     side="buy"
            ... )
            >>> position.qty
            Decimal('10')
            >>> position.avg_entry_price
            Decimal('150.00')
            >>>
            >>> # Buy 5 more at $152 (increases position, updates avg price)
            >>> position = db.update_position_on_fill(
            ...     symbol="AAPL",
            ...     qty=5,
            ...     price=Decimal("152.00"),
            ...     side="buy"
            ... )
            >>> position.qty
            Decimal('15')
            >>> position.avg_entry_price  # Weighted average
            Decimal('150.67')

        Notes:
            - Position qty is signed: positive=long, negative=short
            - avg_entry_price is recalculated on each fill
            - Closing a position (qty=0) keeps the record with realized_pl
        """
        try:
            with self._pool.connection() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    # Get current position
                    cur.execute("SELECT * FROM positions WHERE symbol = %s", (symbol,))
                    current = cur.fetchone()

                    # Calculate new position
                    if current:
                        old_qty = int(current["qty"])
                        old_avg_price = Decimal(str(current["avg_entry_price"]))
                        old_realized_pl = Decimal(str(current["realized_pl"]))
                    else:
                        old_qty = 0
                        old_avg_price = Decimal("0")
                        old_realized_pl = Decimal("0")

                    # Use pure function for P&L calculation (extracted for testability)
                    new_qty, new_avg_price, new_realized_pl = calculate_position_update(
                        old_qty=old_qty,
                        old_avg_price=old_avg_price,
                        old_realized_pl=old_realized_pl,
                        fill_qty=qty,
                        fill_price=Decimal(str(price)),
                        side=side,
                    )

                    # Upsert position
                    cur.execute(
                        """
                        INSERT INTO positions (
                            symbol,
                            qty,
                            avg_entry_price,
                            realized_pl,
                            updated_at,
                            last_trade_at
                        )
                        VALUES (%s, %s, %s, %s, NOW(), NOW())
                        ON CONFLICT (symbol)
                        DO UPDATE SET
                            qty = EXCLUDED.qty,
                            avg_entry_price = EXCLUDED.avg_entry_price,
                            realized_pl = EXCLUDED.realized_pl,
                            updated_at = NOW(),
                            last_trade_at = NOW()
                        RETURNING *
                        """,
                        (symbol, new_qty, new_avg_price, new_realized_pl),
                    )

                    row = cur.fetchone()
                    conn.commit()

                    if row is None:
                        raise ValueError(f"Failed to update position for symbol: {symbol}")

                    logger.info(
                        f"Position updated: {symbol} qty={old_qty}->{new_qty}",
                        extra={
                            "symbol": symbol,
                            "old_qty": str(old_qty),
                            "new_qty": str(new_qty),
                            "avg_price": str(new_avg_price),
                        },
                    )

                    return Position(**row)

        except (OperationalError, DatabaseError) as e:
            logger.error(f"Database error updating position: {e}")
            raise

    def upsert_position_snapshot(
        self,
        symbol: str,
        qty: Decimal,
        avg_entry_price: Decimal,
        current_price: Decimal | None,
        updated_at: datetime,
    ) -> Position:
        """Upsert position from broker reconciliation snapshot."""
        try:
            with self._pool.connection() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(
                        """
                        INSERT INTO positions (
                            symbol,
                            qty,
                            avg_entry_price,
                            current_price,
                            updated_at,
                            last_trade_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (symbol)
                        DO UPDATE SET
                            qty = EXCLUDED.qty,
                            avg_entry_price = EXCLUDED.avg_entry_price,
                            current_price = EXCLUDED.current_price,
                            updated_at = EXCLUDED.updated_at,
                            last_trade_at = EXCLUDED.last_trade_at
                        RETURNING *
                        """,
                        (symbol, qty, avg_entry_price, current_price, updated_at, updated_at),
                    )
                    row = cur.fetchone()
                    conn.commit()
                    if row is None:
                        raise ValueError(f"Failed to upsert position snapshot: {symbol}")
                    return Position(**row)
        except (OperationalError, DatabaseError) as e:
            logger.error(f"Database error upserting position snapshot: {e}")
            raise

    def get_position_by_symbol(self, symbol: str) -> int:
        """
        Get current position quantity for a symbol.

        Used by position reservation to provide accurate fallback when Redis key
        is missing (e.g., after Redis restart). This prevents the system from
        incorrectly assuming position is 0 when it's not.

        Args:
            symbol: Stock symbol (e.g., "AAPL")

        Returns:
            Current position quantity (0 if no position exists)
            Positive = long, Negative = short

        Raises:
            DatabaseError: If database operation fails

        Examples:
            >>> db = DatabaseClient("postgresql://localhost/trading_platform")
            >>> qty = db.get_position_by_symbol("AAPL")
            >>> print(f"Current AAPL position: {qty} shares")

        Notes:
            - Returns 0 if symbol not found in positions table
            - Returns 0 if position qty is 0 (flat)
            - Used as fallback for position reservation after Redis restart
        """
        try:
            with self._pool.connection() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(
                        "SELECT qty FROM positions WHERE symbol = %s",
                        (symbol,),
                    )
                    row = cur.fetchone()

                    if row is None:
                        return 0

                    return int(row["qty"])

        except (OperationalError, DatabaseError) as e:
            logger.error(f"Database error fetching position for {symbol}: {e}")
            raise

    def get_all_positions(self) -> list[Position]:
        """
        Get all current positions.

        Returns:
            List of Position objects (empty list if no positions)

        Raises:
            DatabaseError: If database operation fails

        Examples:
            >>> db = DatabaseClient("postgresql://localhost/trading_platform")
            >>> positions = db.get_all_positions()
            >>> for pos in positions:
            ...     print(f"{pos.symbol}: {pos.qty} shares @ ${pos.avg_entry_price}")
        """
        try:
            with self._pool.connection() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(
                        """
                        SELECT * FROM positions
                        WHERE qty != 0
                        ORDER BY symbol
                        """
                    )

                    rows = cur.fetchall()

                    return [Position(**row) for row in rows]

        except (OperationalError, DatabaseError) as e:
            logger.error(f"Database error fetching positions: {e}")
            raise

    def get_positions_for_strategies(self, strategies: list[str]) -> list[Position]:
        """
        Return positions limited to the provided strategies.

        NOTE: The positions table is symbol-scoped and does not store strategy_id.
        Without a reliable symbol-to-strategy mapping, the safest fail-closed
        approach is to return an empty list when strategy scoping is requested.
        This prevents leaking portfolio-wide positions to users without
        VIEW_ALL_STRATEGIES permission. Upstream callers should provide a
        strategy-aware position source when available.

        DESIGN DECISION: Fail-closed returns empty list rather than raising exception.
        This ensures users without VIEW_ALL_STRATEGIES see "no positions" rather than
        an error, preserving UX while maintaining security. Symbols traded by multiple
        strategies are excluded to prevent cross-strategy data leakage. Long-term fix
        is adding strategy_id column to positions table.
        """
        if not strategies:
            return []

        # Attempt a best-effort, fail-closed mapping from position symbols to strategies by
        # inspecting historical orders. We only return a position when exactly one strategy
        # has traded the symbol to avoid leaking cross-strategy positions.
        # Filtering is done in SQL using HAVING and ANY for efficiency.
        def _execute(conn: psycopg.Connection) -> list[Position]:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    WITH symbol_strategy AS (
                        SELECT
                            symbol,
                            (ARRAY_AGG(DISTINCT strategy_id))[1] AS strategy
                        FROM orders
                        WHERE strategy_id IS NOT NULL
                          AND symbol IN (SELECT symbol FROM positions WHERE qty != 0)
                        GROUP BY symbol
                        HAVING COUNT(DISTINCT strategy_id) = 1
                    )
                    SELECT p.*
                    FROM positions p
                    JOIN symbol_strategy ss ON p.symbol = ss.symbol
                    WHERE p.qty != 0
                      AND ss.strategy = ANY(%s)
                    ORDER BY p.symbol
                    """,
                    (strategies,),
                )
                return [Position(**row) for row in cur.fetchall()]

        return self._execute_with_conn(None, _execute)

    def check_connection(self) -> bool:
        """
        Check if database connection is healthy.

        Returns:
            True if connected, False otherwise

        Examples:
            >>> db = DatabaseClient("postgresql://localhost/trading_platform")
            >>> if db.check_connection():
            ...     print("Database is connected")
        """
        try:
            with self._pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    return True

        except Exception as e:
            logger.error(f"Database connection check failed: {e}")
            return False

    def _row_to_order_detail(self, row: Mapping[str, Any]) -> OrderDetail:
        """
        Build OrderDetail from a database row, supplying safe defaults for missing fields.

        This helper ensures that a complete OrderDetail object can be constructed even
        from a partial database row. While useful for resilience, this can mask
        underlying data issues, so callers in production paths should be aware
        that missing fields will be populated with defaults.

        DESIGN DECISION: Default empty strings for client_order_id/symbol allow graceful
        degradation when processing webhook events for orders created before schema
        migrations. Raising ValueError would break fill processing for legacy orders.
        Alternative: Strict validation with ValueError, but risks webhook failures for
        historical data. Monitoring should alert on empty critical fields in production.

        Used by:
        - get_order_for_update()
        - append_fill_to_order_metadata()
        - Tests where mocks may provide partial rows
        """

        defaults: dict[str, Any] = {
            "client_order_id": "",
            "strategy_id": "unknown",
            "symbol": "",
            "side": "buy",
            "qty": 0,
            "order_type": "market",
            "time_in_force": "day",
            "status": "pending_new",
            "retry_count": 0,
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
            "filled_qty": Decimal("0"),
        }
        merged = {**defaults, **row}
        return OrderDetail(**merged)
