"""
Trading Orchestrator - Core orchestration logic.

Coordinates the complete trading flow:
1. Fetch signals from Signal Service (single or multiple strategies)
2. Allocate across strategies if multiple (via MultiAlphaAllocator)
3. Map signals to orders (position sizing)
4. Submit orders to Execution Gateway
5. Track execution and persist results
"""

import logging
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import httpx
import polars as pl

from apps.orchestrator.clients import ExecutionGatewayClient, SignalServiceClient
from apps.orchestrator.schemas import (
    OrchestrationResult,
    OrderRequest,
    Signal,
    SignalOrderMapping,
    SignalServiceResponse,
)
from libs.allocation import MultiAlphaAllocator
from libs.allocation.multi_alpha import AllocMethod

logger = logging.getLogger(__name__)


# ==============================================================================
# Signal Conversion Helpers
# ==============================================================================


def signals_to_dataframe(signals: list[Signal]) -> pl.DataFrame:
    """
    Convert Signal objects to Polars DataFrame for allocator.

    Args:
        signals: List of Signal objects from Signal Service

    Returns:
        pl.DataFrame with columns [symbol, score, weight]
            - score: predicted_return (used for ranking)
            - weight: target_weight (normalized across all signals)

    Example:
        >>> signals = [
        ...     Signal(symbol="AAPL", predicted_return=0.05, rank=1, target_weight=0.4),
        ...     Signal(symbol="MSFT", predicted_return=0.03, rank=2, target_weight=0.3)
        ... ]
        >>> df = signals_to_dataframe(signals)
        >>> df
        shape: (2, 3)
        ┌────────┬────────┬────────┐
        │ symbol ┆ score  ┆ weight │
        │ ---    ┆ ---    ┆ ---    │
        │ str    ┆ f64    ┆ f64    │
        ╞════════╪════════╪════════╡
        │ AAPL   ┆ 0.05   ┆ 0.4    │
        │ MSFT   ┆ 0.03   ┆ 0.3    │
        └────────┴────────┴────────┘
    """
    if not signals:
        return pl.DataFrame(
            {"symbol": [], "score": [], "weight": []},
            schema={"symbol": pl.Utf8, "score": pl.Float64, "weight": pl.Float64},
        )

    return pl.DataFrame(
        {
            "symbol": [s.symbol for s in signals],
            "score": [s.predicted_return for s in signals],
            "weight": [s.target_weight for s in signals],
        }
    )


def dataframe_to_signals(df: pl.DataFrame) -> list[Signal]:
    """
    Convert Polars DataFrame back to Signal objects.

    Args:
        df: pl.DataFrame with columns [symbol, final_weight]
            (or [symbol, final_weight, contributing_strategies])

    Returns:
        List of Signal objects with normalized weights

    Example:
        >>> df = pl.DataFrame({
        ...     "symbol": ["AAPL", "MSFT", "GOOGL"],
        ...     "final_weight": [0.4, 0.3, 0.3]
        ... })
        >>> signals = dataframe_to_signals(df)
        >>> signals[0].symbol
        'AAPL'
        >>> signals[0].target_weight
        0.4

    Notes:
        - predicted_return and rank are set to 0 since they're not preserved through allocation
        - Use final_weight as target_weight for execution
    """
    if df.height == 0:
        return []

    # Extract symbols and weights
    symbols = df["symbol"].to_list()
    weights = df["final_weight"].to_list()

    # Create Signal objects
    # Note: After allocation, we lose individual strategy predictions
    # So we set predicted_return=0, rank=0 (unused for execution)
    return [
        Signal(
            symbol=symbol,
            predicted_return=0.0,  # Not preserved through allocation
            rank=0,  # Not preserved through allocation
            target_weight=weight,
        )
        for symbol, weight in zip(symbols, weights, strict=True)
    ]


# ==============================================================================
# Trading Orchestrator
# ==============================================================================


class TradingOrchestrator:
    """
    Coordinates the complete trading flow.

    Responsibilities:
    1. Fetch signals from Signal Service (T3)
    2. Map signals to orders with position sizing
    3. Submit orders to Execution Gateway (T4)
    4. Track execution status
    5. Persist results to database

    Example:
        >>> orchestrator = TradingOrchestrator(
        ...     signal_service_url="http://localhost:8001",
        ...     execution_gateway_url="http://localhost:8002",
        ...     capital=Decimal("100000"),
        ...     max_position_size=Decimal("10000")
        ... )
        >>> result = await orchestrator.run(
        ...     symbols=["AAPL", "MSFT", "GOOGL"],
        ...     strategy_id="alpha_baseline"
        ... )
        >>> print(result.num_orders_submitted)
        2
    """

    def __init__(
        self,
        signal_service_url: str,
        execution_gateway_url: str,
        capital: Decimal,
        max_position_size: Decimal,
        price_cache: dict[str, Decimal] | None = None,
        allocation_method: AllocMethod = "rank_aggregation",
        per_strategy_max: float = 0.40,
    ):
        """
        Initialize Trading Orchestrator.

        Args:
            signal_service_url: URL of Signal Service (e.g., "http://localhost:8001")
            execution_gateway_url: URL of Execution Gateway (e.g., "http://localhost:8002")
            capital: Total capital to allocate (e.g., Decimal("100000"))
            max_position_size: Maximum position size per symbol (e.g., Decimal("10000"))
            price_cache: Optional dict of symbol -> price for testing
            allocation_method: Method for multi-alpha allocation ('rank_aggregation', 'inverse_vol', 'equal_weight')
            per_strategy_max: Maximum allocation to any single strategy (default 0.40 = 40%)
        """
        self.signal_client = SignalServiceClient(signal_service_url)
        self.execution_client = ExecutionGatewayClient(execution_gateway_url)
        self.capital = capital
        self.max_position_size = max_position_size
        self.price_cache = price_cache or {}
        self.allocation_method = allocation_method
        self.per_strategy_max = per_strategy_max

    async def close(self) -> None:
        """Close HTTP clients."""
        await self.signal_client.close()
        await self.execution_client.close()

    async def run(
        self,
        symbols: list[str],
        strategy_id: str | list[str],
        as_of_date: date | None = None,
        top_n: int | None = None,
        bottom_n: int | None = None,
    ) -> OrchestrationResult:
        """
        Execute complete orchestration workflow (single or multi-strategy).

        Single strategy mode (backward compatible):
            - strategy_id is a string
            - Signals used directly without allocation

        Multi-strategy mode:
            - strategy_id is a list of strategy IDs
            - Signals blended via MultiAlphaAllocator

        Args:
            symbols: List of symbols to trade
            strategy_id: Single strategy ID (str) or multiple (list[str])
            as_of_date: Date for signal generation (defaults to today)
            top_n: Override number of long positions
            bottom_n: Override number of short positions

        Returns:
            OrchestrationResult with complete run details

        Example (single strategy):
            >>> result = await orchestrator.run(
            ...     symbols=["AAPL", "MSFT", "GOOGL"],
            ...     strategy_id="alpha_baseline",
            ...     as_of_date=date(2024, 12, 31)
            ... )

        Example (multi-strategy):
            >>> result = await orchestrator.run(
            ...     symbols=["AAPL", "MSFT", "GOOGL"],
            ...     strategy_id=["alpha_baseline", "momentum", "mean_reversion"],
            ...     as_of_date=date(2024, 12, 31)
            ... )
        """
        run_id = uuid.uuid4()
        started_at = datetime.now()

        # Normalize strategy_id to list for consistent handling
        strategy_ids = [strategy_id] if isinstance(strategy_id, str) else strategy_id
        is_multi_strategy = len(strategy_ids) > 1

        logger.info(
            f"Starting orchestration run {run_id}",
            extra={
                "run_id": str(run_id),
                "strategy_ids": strategy_ids,
                "num_strategies": len(strategy_ids),
                "multi_strategy": is_multi_strategy,
                "num_symbols": len(symbols),
                "capital": float(self.capital),
            },
        )

        try:
            # Phase 1: Fetch signals (single or multi-strategy)
            if is_multi_strategy:
                # Multi-strategy: allocate across strategies
                final_signals = await self._run_multi_strategy(
                    symbols=symbols,
                    strategy_ids=strategy_ids,
                    as_of_date=as_of_date,
                    top_n=top_n,
                    bottom_n=bottom_n,
                )
            else:
                # Single strategy: use signals directly (backward compatible)
                signal_response = await self._fetch_signals(
                    symbols=symbols, as_of_date=as_of_date, top_n=top_n, bottom_n=bottom_n
                )
                final_signals = signal_response.signals

            # Phase 2: Map signals to orders
            mappings = await self._map_signals_to_orders(final_signals)

            # Phase 3: Submit orders
            await self._submit_orders(mappings)

            # Compute final stats
            num_orders_submitted = sum(1 for m in mappings if m.client_order_id is not None)
            num_orders_accepted = sum(
                1
                for m in mappings
                if m.order_status and m.order_status not in ("rejected", "cancelled")
            )
            num_orders_rejected = sum(
                1 for m in mappings if m.order_status in ("rejected", "cancelled")
            )

            completed_at = datetime.now()
            duration_seconds = (completed_at - started_at).total_seconds()

            # Determine final status
            if num_orders_rejected > 0 and num_orders_accepted > 0:
                status = "partial"
            elif num_orders_rejected > 0:
                status = "failed"
            else:
                status = "completed"

            logger.info(
                f"Orchestration run {run_id} {status}",
                extra={
                    "run_id": str(run_id),
                    "status": status,
                    "num_signals": len(final_signals),
                    "num_orders_submitted": num_orders_submitted,
                    "num_orders_accepted": num_orders_accepted,
                    "num_orders_rejected": num_orders_rejected,
                    "duration_seconds": duration_seconds,
                },
            )

            return OrchestrationResult(
                run_id=run_id,
                status=status,
                strategy_id=",".join(strategy_ids),  # Join multiple strategy IDs with comma
                as_of_date=as_of_date.isoformat() if as_of_date else date.today().isoformat(),
                symbols=symbols,
                capital=self.capital,
                num_signals=len(final_signals),
                signal_metadata={
                    "strategies": strategy_ids,
                    "multi_strategy": is_multi_strategy,
                    "allocation_method": self.allocation_method if is_multi_strategy else None,
                },
                num_orders_submitted=num_orders_submitted,
                num_orders_accepted=num_orders_accepted,
                num_orders_rejected=num_orders_rejected,
                mappings=mappings,
                started_at=started_at,
                completed_at=completed_at,
                duration_seconds=Decimal(str(duration_seconds)),
            )

        except Exception as e:
            logger.error(
                f"Orchestration run {run_id} failed: {e}",
                exc_info=True,
                extra={"run_id": str(run_id)},
            )

            completed_at = datetime.now()
            duration_seconds = (completed_at - started_at).total_seconds()

            return OrchestrationResult(
                run_id=run_id,
                status="failed",
                strategy_id=",".join(strategy_ids),  # Join multiple strategy IDs with comma
                as_of_date=as_of_date.isoformat() if as_of_date else date.today().isoformat(),
                symbols=symbols,
                capital=self.capital,
                num_signals=0,
                num_orders_submitted=0,
                num_orders_accepted=0,
                num_orders_rejected=0,
                mappings=[],
                started_at=started_at,
                completed_at=completed_at,
                duration_seconds=Decimal(str(duration_seconds)),
                error_message=str(e),
            )

    async def _fetch_signals(
        self,
        symbols: list[str],
        as_of_date: date | None = None,
        top_n: int | None = None,
        bottom_n: int | None = None,
        strategy_id: str | None = None,
    ) -> SignalServiceResponse:
        """
        Fetch signals from Signal Service.

        Args:
            symbols: List of symbols
            as_of_date: Date for signal generation
            top_n: Number of long positions
            bottom_n: Number of short positions
            strategy_id: Strategy identifier for multi-strategy mode (optional)

        Returns:
            SignalServiceResponse with signals and metadata

        Raises:
            httpx.HTTPError: If Signal Service request fails

        Notes:
            - In single-strategy mode, strategy_id is None and default model is used
            - In multi-strategy mode, strategy_id differentiates signal sources
            - TODO: Pass strategy_id to signal_client once it supports multiple strategies
        """
        logger.info(
            f"Fetching signals for {len(symbols)} symbols"
            + (f" (strategy: {strategy_id})" if strategy_id else "")
        )

        # TODO: Once SignalServiceClient supports strategy_id parameter, pass it here
        # For MVP, all strategies use the same signal service endpoint
        # In production, this would route to different strategy services or pass strategy_id
        signal_response = await self.signal_client.fetch_signals(
            symbols=symbols, as_of_date=as_of_date, top_n=top_n, bottom_n=bottom_n
        )

        logger.info(
            f"Received {len(signal_response.signals)} signals",
            extra={
                "num_signals": len(signal_response.signals),
                "model_version": signal_response.metadata.model_version,
                "num_longs": sum(1 for s in signal_response.signals if s.target_weight > 0),
                "num_shorts": sum(1 for s in signal_response.signals if s.target_weight < 0),
            },
        )

        return signal_response

    async def _run_multi_strategy(
        self,
        symbols: list[str],
        strategy_ids: list[str],
        as_of_date: date | None = None,
        top_n: int | None = None,
        bottom_n: int | None = None,
    ) -> list[Signal]:
        """
        Run multi-strategy allocation workflow.

        Workflow:
        1. Fetch signals from each strategy
        2. Convert signals to DataFrames
        3. Allocate across strategies via MultiAlphaAllocator
        4. Convert blended DataFrame back to Signal objects

        Args:
            symbols: List of symbols to trade
            strategy_ids: List of strategy IDs to blend
            as_of_date: Date for signal generation
            top_n: Number of long positions per strategy
            bottom_n: Number of short positions per strategy

        Returns:
            List of blended Signal objects with final target_weight from allocation

        Example:
            >>> # Fetch from alpha_baseline, momentum, mean_reversion
            >>> signals = await self._run_multi_strategy(
            ...     symbols=["AAPL", "MSFT", "GOOGL"],
            ...     strategy_ids=["alpha_baseline", "momentum", "mean_reversion"],
            ...     as_of_date=date(2024, 12, 31)
            ... )
            >>> # Returns blended signals with weights from MultiAlphaAllocator
        """
        logger.info(f"Running multi-strategy allocation for {len(strategy_ids)} strategies")

        # Step 1: Fetch signals from each strategy
        # TODO: In production, this would call multiple strategy services
        # For MVP, we assume all strategies share the same signal service
        # and differentiate via strategy_id parameter
        signal_responses = {}
        for strategy_id in strategy_ids:
            logger.info(f"Fetching signals for strategy: {strategy_id}")
            response = await self._fetch_signals(
                symbols=symbols,
                as_of_date=as_of_date,
                top_n=top_n,
                bottom_n=bottom_n,
                strategy_id=strategy_id,  # Pass strategy_id to differentiate signals
            )
            signal_responses[strategy_id] = response

        # Step 2: Convert signals to DataFrames
        signal_dfs: dict[str, pl.DataFrame] = {}
        for strategy_id, response in signal_responses.items():
            signal_dfs[strategy_id] = signals_to_dataframe(response.signals)

        logger.info(
            f"Converted signals to DataFrames: {len(signal_dfs)} strategies, "
            f"total signals = {sum(df.height for df in signal_dfs.values())}"
        )

        # Step 3: Allocate across strategies
        allocator = MultiAlphaAllocator(
            method=self.allocation_method, per_strategy_max=self.per_strategy_max
        )

        # No strategy_stats for now (inverse_vol would need this)
        # Pass empty dict for methods that don't require stats (rank_aggregation, equal_weight)
        strategy_stats: dict[str, dict[str, Any]] = {}

        blended_df = allocator.allocate(signal_dfs, strategy_stats)

        logger.info(
            f"Allocation complete: {blended_df.height} symbols, "
            f"method={self.allocation_method}, "
            f"per_strategy_max={self.per_strategy_max}"
        )

        # Step 4: Convert blended DataFrame back to Signal objects
        blended_signals = dataframe_to_signals(blended_df)

        logger.info(f"Blended {len(blended_signals)} signals across {len(strategy_ids)} strategies")

        return blended_signals

    async def _map_signals_to_orders(self, signals: list[Signal]) -> list[SignalOrderMapping]:
        """
        Map trading signals to executable orders with position sizing.

        Position Sizing Algorithm:
        1. Calculate dollar amount: capital * |target_weight|
        2. Apply max position size limit
        3. Fetch current price for symbol
        4. Convert to shares: qty = floor(dollar_amount / price)
        5. Skip if qty < 1 share

        Args:
            signals: List of trading signals from Signal Service

        Returns:
            List of SignalOrderMapping (signal + order info)

        Example:
            Capital = $100,000
            Signal: AAPL target_weight = 0.333 (33.3% long)
            Price = $150

            Dollar amount = $100,000 * 0.333 = $33,300
            Shares = floor($33,300 / $150) = 222 shares
            Order: BUY 222 AAPL @ market
        """
        logger.info(f"Mapping {len(signals)} signals to orders")

        mappings = []

        for signal in signals:
            # Create base mapping
            mapping = SignalOrderMapping(
                symbol=signal.symbol,
                predicted_return=signal.predicted_return,
                rank=signal.rank,
                target_weight=signal.target_weight,
            )

            # Skip zero-weight signals
            if signal.target_weight == 0:
                mapping.skip_reason = "zero_weight"
                mappings.append(mapping)
                logger.debug(f"Skipping {signal.symbol}: zero weight")
                continue

            # Calculate dollar amount
            dollar_amount = abs(Decimal(str(signal.target_weight))) * self.capital

            # Apply max position size
            if dollar_amount > self.max_position_size:
                logger.info(
                    f"Capping {signal.symbol} position: "
                    f"${dollar_amount} → ${self.max_position_size}"
                )
                dollar_amount = self.max_position_size

            # Get current price
            try:
                price = await self._get_current_price(signal.symbol)
            except Exception as e:
                logger.error(f"Failed to get price for {signal.symbol}: {e}")
                mapping.skip_reason = f"price_fetch_failed: {e}"
                mappings.append(mapping)
                continue

            # Convert to shares (round down)
            qty = int(dollar_amount / price)

            # Skip if qty < 1 share
            if qty < 1:
                logger.info(
                    f"Skipping {signal.symbol}: qty < 1 share "
                    f"(dollar_amount=${dollar_amount}, price=${price})"
                )
                mapping.skip_reason = "qty_less_than_one_share"
                mappings.append(mapping)
                continue

            # Determine side
            side = "buy" if signal.target_weight > 0 else "sell"

            # Store order info in mapping (but don't submit yet)
            mapping.order_qty = qty
            mapping.order_side = side

            mappings.append(mapping)

            logger.info(
                f"Mapped {signal.symbol}: {side} {qty} shares "
                f"(weight={signal.target_weight:.4f}, price=${price})"
            )

        logger.info(
            f"Created {sum(1 for m in mappings if m.order_qty is not None)} orders "
            f"(skipped {sum(1 for m in mappings if m.skip_reason is not None)})"
        )

        return mappings

    async def _submit_orders(self, mappings: list[SignalOrderMapping]) -> None:
        """
        Submit orders to Execution Gateway.

        Updates mappings in-place with submission results.

        Args:
            mappings: List of SignalOrderMapping with order_qty and order_side set
        """
        orders_to_submit = [m for m in mappings if m.order_qty is not None]

        logger.info(f"Submitting {len(orders_to_submit)} orders")

        for mapping in orders_to_submit:
            # Type narrowing: filter ensures order_qty not None, and order_side should also be set
            assert mapping.order_side is not None, "order_side must be set when order_qty is set"
            assert (
                mapping.order_qty is not None
            ), "order_qty must be set"  # Already filtered but helps mypy

            # Create order request
            order = OrderRequest(
                symbol=mapping.symbol,
                side=mapping.order_side,
                qty=mapping.order_qty,
                order_type="market",
                time_in_force="day",
            )

            try:
                # Submit order
                submission = await self.execution_client.submit_order(order)

                # Update mapping with submission result
                mapping.client_order_id = submission.client_order_id
                mapping.broker_order_id = submission.broker_order_id
                mapping.order_status = submission.status

                logger.info(
                    f"Order submitted: {mapping.symbol} {mapping.order_side} {mapping.order_qty} "
                    f"(client_order_id={submission.client_order_id}, status={submission.status})"
                )

            except httpx.HTTPStatusError as e:
                logger.error(
                    f"Order submission failed for {mapping.symbol}: {e}",
                    extra={
                        "symbol": mapping.symbol,
                        "side": mapping.order_side,
                        "qty": mapping.order_qty,
                        "status_code": e.response.status_code,
                        "response": e.response.text,
                    },
                )
                mapping.order_status = "rejected"
                mapping.skip_reason = f"submission_failed: {e.response.status_code}"

            except Exception as e:
                logger.error(
                    f"Unexpected error submitting order for {mapping.symbol}: {e}", exc_info=True
                )
                mapping.order_status = "rejected"
                mapping.skip_reason = f"unexpected_error: {str(e)}"

    async def _get_current_price(self, symbol: str) -> Decimal:
        """
        Get current market price for symbol.

        In MVP, uses a simple price cache or defaults to $100.
        In production, would fetch from market data API.

        Args:
            symbol: Stock symbol

        Returns:
            Current price as Decimal

        Example:
            >>> price = await orchestrator._get_current_price("AAPL")
            >>> print(price)
            Decimal('150.00')
        """
        # Check cache first
        if symbol in self.price_cache:
            return self.price_cache[symbol]

        # For MVP, use simple default
        # TODO: Fetch from Alpaca market data API or use last close price
        default_price = Decimal("100.00")

        logger.warning(f"No price available for {symbol}, using default ${default_price}")

        return default_price


# ==============================================================================
# Position Sizing Utilities
# ==============================================================================


def calculate_position_size(
    target_weight: float, capital: Decimal, price: Decimal, max_position_size: Decimal
) -> tuple[int, Decimal]:
    """
    Calculate position size (number of shares) from target weight.

    Args:
        target_weight: Target portfolio weight (-1.0 to 1.0)
        capital: Total capital available
        price: Current price per share
        max_position_size: Maximum dollar amount per position

    Returns:
        Tuple of (qty, dollar_amount)

    Example:
        >>> qty, dollar_amount = calculate_position_size(
        ...     target_weight=0.333,
        ...     capital=Decimal("100000"),
        ...     price=Decimal("150.00"),
        ...     max_position_size=Decimal("50000")
        ... )
        >>> print(qty, dollar_amount)
        222 Decimal('33300.00')
    """
    # Calculate dollar amount
    dollar_amount = abs(Decimal(str(target_weight))) * capital

    # Apply max position size
    dollar_amount = min(dollar_amount, max_position_size)

    # Convert to shares (round down)
    qty = int(dollar_amount / price)

    return qty, dollar_amount
