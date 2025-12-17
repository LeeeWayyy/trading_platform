"""
TWAP (Time-Weighted Average Price) order slicing for large order execution.

Splits large parent orders into smaller child slices distributed evenly over time
to minimize market impact. This is a standard algorithmic execution strategy.

Algorithm:
    1. Determine slice count from duration and requested interval spacing
    2. H6 Fix: Distribute remainder uniformly using deterministic randomization
       (seed derived from order params ensures idempotency while hiding pattern)
    3. Generate deterministic client_order_id for parent and each slice
    4. Calculate scheduled execution times at the configured interval spacing

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
    [21, 20, 21, 20, 21]  # H6: Uniform remainder distribution (deterministic but not front-loaded)

See Also:
    - docs/TASKS/P2_PLANNING.md#p2t0-twap-order-slicer
    - docs/CONCEPTS/execution-algorithms.md#twap
"""

import hashlib
import logging
import math
import random
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
        - H6: Slice quantity distribution is uniform (deterministic random, not front-loaded)
        - All client_order_ids are deterministic (same inputs = same IDs)
        - Scheduled times honor configurable slice interval spacing
        - All slices initially have status="pending_new"
    """

    def plan(
        self,
        symbol: str,
        side: Literal["buy", "sell"],
        qty: int,
        duration_minutes: int,
        order_type: Literal["market", "limit", "stop", "stop_limit"],
        interval_seconds: int = 60,
        max_slice_qty: int | None = None,
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
            duration_minutes: Total slicing duration in minutes
            order_type: Order type ("market", "limit", "stop", "stop_limit")
            interval_seconds: Interval between slices in seconds
            max_slice_qty: Optional max quantity per slice (liquidity constraint)
            limit_price: Limit price for limit/stop_limit orders (required if order_type is limit/stop_limit)
            stop_price: Stop price for stop/stop_limit orders (required if order_type is stop/stop_limit)
            time_in_force: Time in force ("day", "gtc", "ioc", "fok")
            trade_date: Date for ID generation (defaults to today UTC); use explicit date for idempotency across midnight

        Returns:
            SlicingPlan with parent order ID and all child slice details

        Raises:
            ValueError: If qty < 1, duration_minutes < 1, interval_seconds < 1,
                       or qty insufficient for computed slice count,
                       or required prices are missing for limit/stop orders,
                       or max_slice_qty is provided but < 1

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
            - Remainder is distributed uniformly (deterministic random, not front-loaded)
            - Parent order ID uses total quantity + configuration parameters
            - Child order IDs use individual slice quantities
            - All IDs are deterministic (repeatable for same inputs)
        """
        # Validation
        if qty < 1:
            raise ValueError(f"qty must be at least 1, got {qty}")

        if duration_minutes < 1:
            raise ValueError(f"duration_minutes must be at least 1, got {duration_minutes}")

        if interval_seconds < 1:
            raise ValueError(f"interval_seconds must be at least 1, got {interval_seconds}")

        total_duration_seconds = duration_minutes * 60
        num_slices = max(1, math.ceil(total_duration_seconds / interval_seconds))
        base_slices = num_slices
        effective_interval_seconds = interval_seconds

        if max_slice_qty is not None:
            if max_slice_qty < 1:
                raise ValueError(f"max_slice_qty must be at least 1, got {max_slice_qty}")
            liquidity_slices = max(1, math.ceil(qty / max_slice_qty))
            if liquidity_slices > num_slices:
                num_slices = liquidity_slices

        # If liquidity constraints increase slice count, recompute interval to
        # keep total span within the requested duration.
        if max_slice_qty is not None and num_slices > base_slices:
            if num_slices == 1:
                effective_interval_seconds = interval_seconds
            else:
                max_interval = total_duration_seconds // (num_slices - 1)
                if max_interval < 1:
                    raise ValueError(
                        "Liquidity constraint requires more slices than can fit within duration "
                        "at 1s minimum interval"
                    )
                effective_interval_seconds = max_interval

        if qty < num_slices:
            raise ValueError(
                f"qty ({qty}) must be >= number of slices ({num_slices}) derived from duration "
                f"and interval to avoid zero-quantity slices"
            )

        # Validate order type requirements
        if order_type in ("limit", "stop_limit") and limit_price is None:
            raise ValueError(f"{order_type} orders require limit_price")

        if order_type in ("stop", "stop_limit") and stop_price is None:
            raise ValueError(f"{order_type} orders require stop_price")

        # H6 Fix: Calculate slice quantities with uniform remainder distribution
        # Use deterministic seed based on order params for idempotency
        # This hides the pattern from market makers while ensuring same inputs = same output
        interval_seconds = effective_interval_seconds

        base_qty = qty // num_slices
        remainder = qty % num_slices

        slice_qtys = [base_qty] * num_slices

        if remainder > 0:
            # Create deterministic seed from order parameters using stable hash
            # IMPORTANT: Use hashlib.sha256 instead of hash() because hash() is
            # randomized per process via PYTHONHASHSEED, breaking idempotency
            seed_string = f"{symbol}_{side}_{qty}_{duration_minutes}_{interval_seconds}"
            if trade_date:
                seed_string += f"_{trade_date.isoformat()}"
            # SHA256 provides stable, cross-process deterministic seed
            seed_bytes = hashlib.sha256(seed_string.encode()).digest()
            seed = int.from_bytes(seed_bytes[:4], "big")  # Use first 4 bytes as 32-bit seed

            # Use seeded random to select which slices get +1
            rng = random.Random(seed)
            indices_for_extra = rng.sample(range(num_slices), remainder)
            for idx in indices_for_extra:
                slice_qtys[idx] += 1

        # Generate parent order ID using deterministic trade date
        now = datetime.now(UTC)
        _trade_date = trade_date or now.date()

        # Generate parent strategy ID (deterministic, includes duration and interval)
        parent_strategy_id = f"twap_parent_{duration_minutes}m_{interval_seconds}s"

        parent_order_id = reconstruct_order_params_hash(
            symbol=symbol,
            side=side,
            qty=qty,
            limit_price=limit_price,
            stop_price=stop_price,
            order_type=order_type,
            time_in_force=time_in_force,
            strategy_id=parent_strategy_id,
            order_date=_trade_date,
        )

        # Generate child slices
        slices = []
        for i, slice_qty in enumerate(slice_qtys):
            # Calculate scheduled time (i minutes from now)
            scheduled_time = now + timedelta(seconds=i * interval_seconds)

            # Generate slice strategy ID (deterministic, includes parent and slice number)
            slice_strategy_id = f"twap_slice_{parent_order_id}_{i}"

            # Generate deterministic child order ID (same trade date as parent)
            child_order_id = reconstruct_order_params_hash(
                symbol=symbol,
                side=side,
                qty=slice_qty,
                limit_price=limit_price,
                stop_price=stop_price,
                order_type=order_type,
                time_in_force=time_in_force,
                strategy_id=slice_strategy_id,
                order_date=_trade_date,
            )

            slices.append(
                SliceDetail(
                    slice_num=i,
                    qty=slice_qty,
                    scheduled_time=scheduled_time,
                    client_order_id=child_order_id,
                    strategy_id=slice_strategy_id,  # Include strategy_id in slice details
                    status="pending_new",  # Initial status
                )
            )

        logger.info(
            "Generated TWAP slicing plan: %s %s %s over %smin → %s slices (interval=%ss), "
            "parent_id=%s...",
            symbol,
            side,
            qty,
            duration_minutes,
            num_slices,
            interval_seconds,
            parent_order_id[:8],
        )
        if max_slice_qty is not None and num_slices > base_slices:
            logger.info(
                "Liquidity constraint increased slices: base=%s max_slice_qty=%s adjusted=%s",
                base_slices,
                max_slice_qty,
                num_slices,
            )

        return SlicingPlan(
            parent_order_id=parent_order_id,
            parent_strategy_id=parent_strategy_id,
            symbol=symbol,
            side=side,
            total_qty=qty,
            total_slices=num_slices,
            duration_minutes=duration_minutes,
            interval_seconds=interval_seconds,
            slices=slices,
        )
