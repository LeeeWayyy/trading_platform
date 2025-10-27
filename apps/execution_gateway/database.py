"""
Database operations for Execution Gateway.

Provides database access for orders and positions tables with:
- CRUD operations for orders
- Position updates from fills
- Transaction management
- Connection pooling

See ADR-0005 for architecture decisions.
"""

import logging
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal

import psycopg
from psycopg import DatabaseError, IntegrityError, OperationalError
from psycopg.rows import dict_row

from apps.execution_gateway.schemas import OrderDetail, OrderRequest, Position

logger = logging.getLogger(__name__)


class DatabaseClient:
    """
    Database client for orders and positions.

    Handles all database operations for the execution gateway, including:
    - Creating and updating orders
    - Querying order status
    - Updating positions from fills
    - Transaction management

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
    """

    def __init__(self, db_conn_string: str):
        """
        Initialize database client.

        Args:
            db_conn_string: PostgreSQL connection string

        Raises:
            ValueError: If connection string is empty
        """
        if not db_conn_string:
            raise ValueError("db_conn_string cannot be empty")

        self.db_conn_string = db_conn_string
        logger.info(
            "DatabaseClient initialized",
            extra={"db": db_conn_string.split("@")[1] if "@" in db_conn_string else "local"},
        )

    @contextmanager
    def transaction(self) -> Generator[psycopg.Connection, None, None]:
        """
        Context manager for executing multiple database operations in a single transaction.

        Provides a connection that will automatically commit on success or rollback
        on exception. Use this when multiple operations need atomic behavior.

        Yields:
            psycopg.Connection: Database connection with transaction support

        Raises:
            DatabaseError: If database operation fails (after rollback)

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
            - Transaction auto-rollbacks on any exception
            - Connection auto-closes after commit or rollback
        """
        conn = None
        try:
            conn = psycopg.connect(self.db_conn_string)
            yield conn
            conn.commit()
            logger.debug("Transaction committed successfully")
        except Exception as e:
            if conn:
                conn.rollback()
                logger.warning(
                    f"Transaction rolled back due to error: {e}",
                    extra={"error_type": type(e).__name__, "error_message": str(e)},
                )
            raise
        finally:
            if conn:
                conn.close()

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
            with psycopg.connect(self.db_conn_string) as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    submitted_at = datetime.now() if status != "dry_run" else None

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
                        f"Order created in database: {client_order_id}",
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
        try:
            # Two different paths: own connection vs. provided connection
            if conn is None:
                # Create and manage our own connection
                with psycopg.connect(self.db_conn_string) as conn:
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

                    # Commit manually (inside connection context, after cursor closes)
                    conn.commit()

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
            else:
                # Use provided connection (transactional mode - caller handles commit)
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

                # No commit - caller is responsible

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
        try:
            # Two different paths: own connection vs. provided connection
            if conn is None:
                # Create and manage our own connection
                with psycopg.connect(self.db_conn_string) as conn:
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

                    # Commit manually (inside connection context, after cursor closes)
                    conn.commit()

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
            else:
                # Use provided connection (transactional mode - caller handles commit)
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

                # No commit - caller is responsible

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
            with psycopg.connect(self.db_conn_string) as conn:
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
            with psycopg.connect(self.db_conn_string) as conn:
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
            with psycopg.connect(self.db_conn_string) as conn:
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
            with psycopg.connect(self.db_conn_string) as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    # Determine filled_at timestamp
                    filled_at = None
                    if status == "filled" and filled_qty is not None:
                        filled_at = datetime.now()

                    cur.execute(
                        """
                        UPDATE orders
                        SET
                            status = %s,
                            broker_order_id = COALESCE(%s, broker_order_id),
                            filled_qty = COALESCE(%s, filled_qty),
                            filled_avg_price = COALESCE(%s, filled_avg_price),
                            error_message = COALESCE(%s, error_message),
                            filled_at = COALESCE(%s, filled_at),
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
            with psycopg.connect(self.db_conn_string) as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    # Get current position
                    cur.execute("SELECT * FROM positions WHERE symbol = %s", (symbol,))
                    current = cur.fetchone()

                    # Calculate new position
                    if current:
                        old_qty = Decimal(str(current["qty"]))
                        old_avg_price = Decimal(str(current["avg_entry_price"]))
                        old_realized_pl = Decimal(str(current["realized_pl"]))
                    else:
                        old_qty = Decimal("0")
                        old_avg_price = Decimal("0")
                        old_realized_pl = Decimal("0")

                    # Apply fill (buy increases qty, sell decreases)
                    fill_qty = Decimal(str(qty))
                    if side == "sell":
                        fill_qty = -fill_qty

                    new_qty = old_qty + fill_qty
                    fill_price = Decimal(str(price))

                    # Calculate new avg_entry_price and realized P&L
                    if new_qty == 0:
                        # Position closed - realize P&L
                        pnl = (fill_price - old_avg_price) * abs(fill_qty)
                        if side == "sell" and old_qty > 0:
                            # Closing long position
                            pnl = (fill_price - old_avg_price) * abs(fill_qty)
                        elif side == "buy" and old_qty < 0:
                            # Closing short position
                            pnl = (old_avg_price - fill_price) * abs(fill_qty)
                        else:
                            pnl = Decimal("0")

                        new_avg_price = fill_price  # Use last fill price
                        new_realized_pl = old_realized_pl + pnl

                    elif (old_qty > 0 and new_qty > 0) or (old_qty < 0 and new_qty < 0):
                        # Adding to position - update weighted average
                        total_cost = (old_avg_price * abs(old_qty)) + (fill_price * abs(fill_qty))
                        new_avg_price = total_cost / abs(new_qty)
                        new_realized_pl = old_realized_pl

                    elif old_qty == 0:
                        # Opening new position
                        new_avg_price = fill_price
                        new_realized_pl = old_realized_pl

                    else:
                        # Reducing position (but not closing) - realize partial P&L
                        pnl = (fill_price - old_avg_price) * abs(fill_qty)
                        if side == "sell" and old_qty > 0:
                            pnl = (fill_price - old_avg_price) * abs(fill_qty)
                        elif side == "buy" and old_qty < 0:
                            pnl = (old_avg_price - fill_price) * abs(fill_qty)

                        new_avg_price = old_avg_price  # Keep same avg price
                        new_realized_pl = old_realized_pl + pnl

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
            with psycopg.connect(self.db_conn_string) as conn:
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
            with psycopg.connect(self.db_conn_string) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    return True

        except Exception as e:
            logger.error(f"Database connection check failed: {e}")
            return False
