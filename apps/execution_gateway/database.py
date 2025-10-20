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
from datetime import datetime
from decimal import Decimal

import psycopg2
import psycopg2.extras
from psycopg2 import DatabaseError, IntegrityError, OperationalError

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
            extra={"db": db_conn_string.split("@")[1] if "@" in db_conn_string else "local"}
        )

    def create_order(
        self,
        client_order_id: str,
        strategy_id: str,
        order_request: OrderRequest,
        status: str,
        broker_order_id: str | None = None,
        error_message: str | None = None
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
            with psycopg2.connect(self.db_conn_string) as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
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
                            "status": status
                        }
                    )

                    return OrderDetail(**row)

        except IntegrityError:
            logger.warning(
                f"Order already exists: {client_order_id}",
                extra={"client_order_id": client_order_id}
            )
            raise

        except (OperationalError, DatabaseError) as e:
            logger.error(f"Database error creating order: {e}")
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
            with psycopg2.connect(self.db_conn_string) as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
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
        error_message: str | None = None
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
            with psycopg2.connect(self.db_conn_string) as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
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
                            "filled_qty": str(filled_qty) if filled_qty else None
                        }
                    )

                    return OrderDetail(**row)

        except (OperationalError, DatabaseError) as e:
            logger.error(f"Database error updating order: {e}")
            raise

    def update_position_on_fill(
        self,
        symbol: str,
        qty: int,
        price: Decimal,
        side: str
    ) -> Position:
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
            with psycopg2.connect(self.db_conn_string) as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    # Get current position
                    cur.execute(
                        "SELECT * FROM positions WHERE symbol = %s",
                        (symbol,)
                    )
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
                            "avg_price": str(new_avg_price)
                        }
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
            with psycopg2.connect(self.db_conn_string) as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
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
            with psycopg2.connect(self.db_conn_string) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    return True

        except Exception as e:
            logger.error(f"Database connection check failed: {e}")
            return False
