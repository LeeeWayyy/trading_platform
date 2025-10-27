"""
TWAP (Time-Weighted Average Price) order slicing for large order execution.

Splits large parent orders into smaller child slices distributed evenly over time
to minimize market impact. This is a standard algorithmic execution strategy.

Algorithm:
    1. Divide total quantity by duration (1 slice per minute)
    2. Distribute remainder using front-loaded approach (first slices get +1)
    3. Generate deterministic client_order_id for parent and each slice
    4. Calculate scheduled execution times at regular intervals

Example:
    >>> slicer = TWAPSlicer()
    >>> plan = slicer.plan(
    ...     symbol="AAPL",
    ...     side="buy",
    ...     qty=103,
    ...     duration_minutes=5,
    ...     order_type="market"
    ... )
    >>> len(plan.slices)
    5
    >>> [s.qty for s in plan.slices]
    [21, 21, 21, 20, 20]  # Front-loaded remainder distribution

See Also:
    - docs/TASKS/P2_PLANNING.md#p2t0-twap-order-slicer
    - docs/CONCEPTS/execution-algorithms.md#twap
"""

import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Literal

from apps.execution_gateway.order_id_generator import reconstruct_order_params_hash
from apps.execution_gateway.schemas import SliceDetail, SlicingPlan

logger = logging.getLogger(__name__)


class TWAPSlicer:
    """
    TWAP (Time-Weighted Average Price) order slicer.

    Splits large parent orders into smaller child slices distributed evenly
    over a time period to minimize market impact.

    Attributes:
        None (stateless slicer)

    Example:
        >>> slicer = TWAPSlicer()
        >>> plan = slicer.plan(
        ...     symbol="AAPL",
        ...     side="buy",
        ...     qty=100,
        ...     duration_minutes=5,
        ...     order_type="market"
        ... )
        >>> plan.total_slices
        5
        >>> plan.slices[0].qty
        20

    Notes:
        - Slice quantity distribution is front-loaded (first slices get remainder)
        - All client_order_ids are deterministic (same inputs = same IDs)
        - Scheduled times are at 1-minute intervals starting from now
        - All slices initially have status="pending_new"
    """

    def plan(
        self,
        symbol: str,
        side: Literal["buy", "sell"],
        qty: int,
        duration_minutes: int,
        order_type: Literal["market", "limit", "stop", "stop_limit"],
        limit_price: Decimal | None = None,
        stop_price: Decimal | None = None,
        time_in_force: Literal["day", "gtc", "ioc", "fok"] = "day",
        trade_date: date | None = None,
    ) -> SlicingPlan:
        """
        Generate TWAP slicing plan with deterministic client_order_ids.

        Args:
            symbol: Stock symbol (e.g., "AAPL")
            side: Order side ("buy" or "sell")
            qty: Total order quantity
            duration_minutes: Slicing duration in minutes (1 slice per minute)
            order_type: Order type ("market", "limit", "stop", "stop_limit")
            limit_price: Limit price for limit/stop_limit orders (required if order_type is limit/stop_limit)
            stop_price: Stop price for stop/stop_limit orders (required if order_type is stop/stop_limit)
            time_in_force: Time in force ("day", "gtc", "ioc", "fok")
            trade_date: Date for ID generation (defaults to today UTC); use explicit date for idempotency across midnight

        Returns:
            SlicingPlan with parent order ID and all child slice details

        Raises:
            ValueError: If qty < 1, duration_minutes < 1, qty < duration_minutes,
                       or required prices are missing for limit/stop orders

        Example:
            >>> slicer = TWAPSlicer()
            >>> plan = slicer.plan(
            ...     symbol="AAPL",
            ...     side="buy",
            ...     qty=103,
            ...     duration_minutes=5,
            ...     order_type="market"
            ... )
            >>> plan.total_qty
            103
            >>> [s.qty for s in plan.slices]
            [21, 21, 21, 20, 20]

        Notes:
            - Remainder is distributed front-loaded (first slices get +1)
            - Parent order ID uses total quantity
            - Child order IDs use individual slice quantities
            - All IDs are deterministic (repeatable for same inputs)
        """
        # Validation
        if qty < 1:
            raise ValueError(f"qty must be at least 1, got {qty}")

        if duration_minutes < 1:
            raise ValueError(f"duration_minutes must be at least 1, got {duration_minutes}")

        # Calculate number of slices (1 per minute)
        num_slices = duration_minutes

        if qty < num_slices:
            raise ValueError(
                f"qty ({qty}) must be >= duration_minutes ({num_slices}) "
                f"to avoid zero-quantity slices (1 slice per minute)"
            )

        # Validate order type requirements
        if order_type in ("limit", "stop_limit") and limit_price is None:
            raise ValueError(f"{order_type} orders require limit_price")

        if order_type in ("stop", "stop_limit") and stop_price is None:
            raise ValueError(f"{order_type} orders require stop_price")

        # Calculate slice quantities with front-loaded remainder distribution
        base_qty = qty // num_slices
        remainder = qty % num_slices

        slice_qtys = []
        for i in range(num_slices):
            if i < remainder:
                slice_qtys.append(base_qty + 1)  # Front-loaded
            else:
                slice_qtys.append(base_qty)

        # Generate parent order ID using deterministic trade date
        now = datetime.now(UTC)
        _trade_date = trade_date or now.date()

        parent_order_id = reconstruct_order_params_hash(
            symbol=symbol,
            side=side,
            qty=qty,
            limit_price=limit_price,
            stop_price=stop_price,
            strategy_id=f"twap_parent_{duration_minutes}m",
            order_date=_trade_date,
        )

        # Generate child slices
        slices = []
        for i, slice_qty in enumerate(slice_qtys):
            # Calculate scheduled time (i minutes from now)
            scheduled_time = now + timedelta(minutes=i)

            # Generate deterministic child order ID (same trade date as parent)
            child_order_id = reconstruct_order_params_hash(
                symbol=symbol,
                side=side,
                qty=slice_qty,
                limit_price=limit_price,
                stop_price=stop_price,
                strategy_id=f"twap_slice_{parent_order_id}_{i}",
                order_date=_trade_date,
            )

            slices.append(
                SliceDetail(
                    slice_num=i,
                    qty=slice_qty,
                    scheduled_time=scheduled_time,
                    client_order_id=child_order_id,
                    status="pending_new",  # Initial status
                )
            )

        logger.info(
            f"Generated TWAP slicing plan: {symbol} {side} {qty} over {duration_minutes}min "
            f"→ {num_slices} slices, parent_id={parent_order_id[:8]}..."
        )

        return SlicingPlan(
            parent_order_id=parent_order_id,
            symbol=symbol,
            side=side,
            total_qty=qty,
            total_slices=num_slices,
            duration_minutes=duration_minutes,
            slices=slices,
        )
