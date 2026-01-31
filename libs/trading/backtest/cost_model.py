"""Transaction cost model for realistic backtest P&L.

This module implements the Almgren-Chriss square root market impact model
with configurable commission/spread and participation limits.

The cost model assumes constant-notional backtesting:
- Portfolio value is fixed throughout the backtest
- Weights represent fractions of the fixed AUM
- Trade sizes are computed against the same fixed AUM

Reference: ADR-0034-cost-model-architecture.md
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

import polars as pl
import structlog

if TYPE_CHECKING:
    from libs.data.data_providers.crsp_local_provider import CRSPLocalProvider

logger = structlog.get_logger(__name__)

# Rolling window constants
ADV_WINDOW_DAYS = 20  # 20 trading days for ADV
VOL_WINDOW_DAYS = 20  # 20 trading days for volatility
LOOKBACK_CALENDAR_DAYS = 40  # 2x calendar days for trading days buffer

# Fallback floor values (conservative to avoid understating costs)
ADV_FLOOR_USD = 100_000  # $100K minimum ADV (~10th percentile S&P 500)
VOL_FLOOR = 0.01  # 1% daily volatility minimum


class ADVSource(str, Enum):
    """Source for Average Daily Volume data."""

    YAHOO = "yahoo"
    ALPACA = "alpaca"
    CRSP = "crsp"  # PIT-compliant CRSP data (used when provider=CRSP)


# Minimum standard deviation for Sharpe ratio calculation (avoid division by near-zero)
MIN_STD_FOR_SHARPE = 1e-8

# Maximum grid size for date×symbol cross join (to prevent memory issues)
# 10 years × 5000 symbols = 12.6M rows; warn above 5M to allow headroom
MAX_GRID_SIZE_WARNING = 5_000_000


@dataclass
class CostModelConfig:
    """Configuration for transaction cost model.

    Attributes:
        enabled: Whether to apply costs (False for gross-only backtests)
        bps_per_trade: Fixed cost in basis points per trade (commission + half-spread)
        impact_coefficient: Almgren-Chriss impact coefficient (eta)
        participation_limit: Maximum fraction of ADV per trade (for capacity analysis)
        adv_source: Source for ADV/volatility data
        portfolio_value_usd: Fixed AUM for constant-notional backtesting
    """

    enabled: bool = True
    bps_per_trade: float = 5.0
    impact_coefficient: float = 0.1
    participation_limit: float = 0.05
    adv_source: ADVSource = ADVSource.YAHOO
    portfolio_value_usd: float = 1_000_000.0

    def __post_init__(self) -> None:
        """Validate configuration parameters."""
        # Check for NaN/Inf to prevent corruption of cost calculations and JSON output
        if not math.isfinite(self.bps_per_trade):
            raise ValueError(f"bps_per_trade must be finite, got {self.bps_per_trade}")
        if self.bps_per_trade < 0:
            raise ValueError(f"bps_per_trade must be >= 0, got {self.bps_per_trade}")

        if not math.isfinite(self.impact_coefficient):
            raise ValueError(f"impact_coefficient must be finite, got {self.impact_coefficient}")
        if self.impact_coefficient < 0:
            raise ValueError(f"impact_coefficient must be >= 0, got {self.impact_coefficient}")

        if not math.isfinite(self.participation_limit):
            raise ValueError(f"participation_limit must be finite, got {self.participation_limit}")
        if not 0 < self.participation_limit <= 1:
            raise ValueError(
                f"participation_limit must be in (0, 1], got {self.participation_limit}"
            )

        if not math.isfinite(self.portfolio_value_usd):
            raise ValueError(f"portfolio_value_usd must be finite, got {self.portfolio_value_usd}")
        if self.portfolio_value_usd <= 0:
            raise ValueError(
                f"portfolio_value_usd must be > 0, got {self.portfolio_value_usd}"
            )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary for JSON storage."""
        return {
            "enabled": self.enabled,
            "bps_per_trade": self.bps_per_trade,
            "impact_coefficient": self.impact_coefficient,
            "participation_limit": self.participation_limit,
            "adv_source": self.adv_source.value,
            "portfolio_value_usd": self.portfolio_value_usd,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CostModelConfig:
        """Deserialize from dictionary with type coercion.

        Raises:
            ValueError: If required fields cannot be coerced to expected types.
        """
        adv_source_str = data.get("adv_source", "yahoo")
        try:
            adv_source = ADVSource(adv_source_str)
        except ValueError:
            # Fallback to yahoo for unknown sources
            adv_source = ADVSource.YAHOO

        # Type coercion with explicit error messages
        def _coerce_float(field: str, value: Any, default: float) -> float:
            if value is None:
                return default
            try:
                return float(value)
            except (ValueError, TypeError) as e:
                raise ValueError(
                    f"cost_model.{field} must be a number, got {type(value).__name__}: {value!r}"
                ) from e

        def _coerce_bool(field: str, value: Any, default: bool) -> bool:
            if value is None:
                return default
            if isinstance(value, bool):
                return value
            # Reject string "true"/"false" - must be actual boolean
            raise ValueError(
                f"cost_model.{field} must be a boolean, got {type(value).__name__}: {value!r}"
            )

        return cls(
            enabled=_coerce_bool("enabled", data.get("enabled"), True),
            bps_per_trade=_coerce_float("bps_per_trade", data.get("bps_per_trade"), 5.0),
            impact_coefficient=_coerce_float(
                "impact_coefficient", data.get("impact_coefficient"), 0.1
            ),
            participation_limit=_coerce_float(
                "participation_limit", data.get("participation_limit"), 0.05
            ),
            adv_source=adv_source,
            portfolio_value_usd=_coerce_float(
                "portfolio_value_usd", data.get("portfolio_value_usd"), 1_000_000.0
            ),
        )


@dataclass
class TradeCost:
    """Cost breakdown for a single trade."""

    symbol: str
    trade_date: date
    trade_value_usd: float
    commission_spread_cost: float
    market_impact_cost: float
    total_cost_usd: float
    total_cost_bps: float
    adv_usd: float | None
    volatility: float | None
    participation_pct: float | None


@dataclass
class CostSummary:
    """Summary of transaction costs for a backtest.

    Attributes:
        total_gross_return: Compounded gross return (before costs)
        total_net_return: Compounded net return (after costs), None if invalid
        total_cost_drag: Cumulative cost as fraction of AUM
        total_cost_usd: Total costs in USD
        commission_spread_cost_usd: Portion from commission/spread
        market_impact_cost_usd: Portion from market impact
        gross_sharpe: Annualized Sharpe ratio of gross returns, None if invalid
        net_sharpe: Annualized Sharpe ratio of net returns, None if invalid
        gross_max_drawdown: Maximum drawdown of gross returns, None if invalid
        net_max_drawdown: Maximum drawdown of net returns, None if invalid
        num_trades: Total number of trades
        avg_trade_cost_bps: Average cost per trade in basis points
        adv_fallback_count: Number of trades using ADV fallback
        volatility_fallback_count: Number of trades using volatility fallback
        participation_violations: Number of trades exceeding participation limit
    """

    total_gross_return: float | None
    total_net_return: float | None
    total_cost_drag: float
    total_cost_usd: float
    commission_spread_cost_usd: float
    market_impact_cost_usd: float
    gross_sharpe: float | None
    net_sharpe: float | None
    gross_max_drawdown: float | None
    net_max_drawdown: float | None
    num_trades: int
    avg_trade_cost_bps: float
    adv_fallback_count: int = 0
    volatility_fallback_count: int = 0
    participation_violations: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary for JSON storage."""
        return {
            "total_gross_return": self.total_gross_return,
            "total_net_return": self.total_net_return,
            "total_cost_drag": self.total_cost_drag,
            "total_cost_usd": self.total_cost_usd,
            "commission_spread_cost_usd": self.commission_spread_cost_usd,
            "market_impact_cost_usd": self.market_impact_cost_usd,
            "gross_sharpe": self.gross_sharpe,
            "net_sharpe": self.net_sharpe,
            "gross_max_drawdown": self.gross_max_drawdown,
            "net_max_drawdown": self.net_max_drawdown,
            "num_trades": self.num_trades,
            "avg_trade_cost_bps": self.avg_trade_cost_bps,
            "adv_fallback_count": self.adv_fallback_count,
            "volatility_fallback_count": self.volatility_fallback_count,
            "participation_violations": self.participation_violations,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CostSummary:
        """Deserialize from dictionary with null handling."""
        return cls(
            total_gross_return=data.get("total_gross_return"),
            total_net_return=data.get("total_net_return"),
            total_cost_drag=data.get("total_cost_drag", 0.0),
            total_cost_usd=data.get("total_cost_usd", 0.0),
            commission_spread_cost_usd=data.get("commission_spread_cost_usd", 0.0),
            market_impact_cost_usd=data.get("market_impact_cost_usd", 0.0),
            gross_sharpe=data.get("gross_sharpe"),
            net_sharpe=data.get("net_sharpe"),
            gross_max_drawdown=data.get("gross_max_drawdown"),
            net_max_drawdown=data.get("net_max_drawdown"),
            num_trades=data.get("num_trades", 0),
            avg_trade_cost_bps=data.get("avg_trade_cost_bps", 0.0),
            adv_fallback_count=data.get("adv_fallback_count", 0),
            volatility_fallback_count=data.get("volatility_fallback_count", 0),
            participation_violations=data.get("participation_violations", 0),
        )


@dataclass
class CapacityAnalysis:
    """Strategy capacity analysis based on market impact.

    All fields are optional (None) when insufficient data is available.

    Attributes:
        avg_daily_turnover: Average daily gross turnover (weight change sum)
        avg_holding_period_days: Estimated average holding period
        portfolio_adv: Trade-weighted average ADV in USD
        portfolio_sigma: Trade-weighted average daily volatility
        gross_alpha_annualized: Annualized gross alpha (before costs)
        impact_aum_5bps: AUM where avg impact reaches 5 bps
        impact_aum_10bps: AUM where avg impact reaches 10 bps
        participation_aum: AUM at participation limit
        breakeven_aum: AUM where net alpha reaches zero
        implied_max_capacity: Minimum of all constraints
        limiting_factor: Which constraint is binding
    """

    avg_daily_turnover: float | None
    avg_holding_period_days: float | None
    portfolio_adv: float | None
    portfolio_sigma: float | None
    gross_alpha_annualized: float | None
    impact_aum_5bps: float | None
    impact_aum_10bps: float | None
    participation_aum: float | None
    breakeven_aum: float | None
    implied_max_capacity: float | None
    limiting_factor: str | None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary for JSON storage."""
        return {
            "avg_daily_turnover": self.avg_daily_turnover,
            "avg_holding_period_days": self.avg_holding_period_days,
            "portfolio_adv": self.portfolio_adv,
            "portfolio_sigma": self.portfolio_sigma,
            "gross_alpha_annualized": self.gross_alpha_annualized,
            "impact_aum_5bps": self.impact_aum_5bps,
            "impact_aum_10bps": self.impact_aum_10bps,
            "participation_aum": self.participation_aum,
            "breakeven_aum": self.breakeven_aum,
            "implied_max_capacity": self.implied_max_capacity,
            "limiting_factor": self.limiting_factor,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CapacityAnalysis:
        """Deserialize from dictionary with null/missing field handling."""
        return cls(
            avg_daily_turnover=data.get("avg_daily_turnover"),
            avg_holding_period_days=data.get("avg_holding_period_days"),
            portfolio_adv=data.get("portfolio_adv"),
            portfolio_sigma=data.get("portfolio_sigma"),
            gross_alpha_annualized=data.get("gross_alpha_annualized"),
            impact_aum_5bps=data.get("impact_aum_5bps"),
            impact_aum_10bps=data.get("impact_aum_10bps"),
            participation_aum=data.get("participation_aum"),
            breakeven_aum=data.get("breakeven_aum"),
            implied_max_capacity=data.get("implied_max_capacity"),
            limiting_factor=data.get("limiting_factor"),
        )


def compute_market_impact(
    trade_value_usd: float,
    adv_usd: float | None,
    volatility: float | None,
    impact_coefficient: float,
) -> float:
    """Compute market impact using Almgren-Chriss square root model.

    Formula: impact_bps = eta * sigma * sqrt(trade_value / ADV)

    Args:
        trade_value_usd: Absolute trade value in USD
        adv_usd: 20-day average daily volume in USD
        volatility: 20-day daily volatility (decimal)
        impact_coefficient: Almgren-Chriss eta parameter

    Returns:
        Market impact in basis points. Returns 0 if ADV/volatility unavailable.
    """
    if adv_usd is None or adv_usd <= 0 or not math.isfinite(adv_usd):
        return 0.0
    if volatility is None or volatility <= 0 or not math.isfinite(volatility):
        return 0.0
    if trade_value_usd <= 0:
        return 0.0

    participation = trade_value_usd / adv_usd
    impact_bps = impact_coefficient * volatility * 10000 * math.sqrt(participation)
    return impact_bps


def compute_trade_cost(
    symbol: str,
    trade_date: date,
    trade_value_usd: float,
    adv_usd: float | None,
    volatility: float | None,
    config: CostModelConfig,
) -> TradeCost:
    """Compute total cost for a single trade.

    Args:
        symbol: Security symbol
        trade_date: Date of the trade
        trade_value_usd: Absolute trade value in USD
        adv_usd: 20-day average daily volume in USD (PIT-compliant, D-1 lag)
        volatility: 20-day daily volatility (PIT-compliant, D-1 lag)
        config: Cost model configuration

    Returns:
        TradeCost with breakdown of costs
    """
    # Commission + spread (fixed bps)
    commission_spread_cost = config.bps_per_trade * trade_value_usd / 10000

    # Market impact (Almgren-Chriss)
    impact_bps = compute_market_impact(
        trade_value_usd, adv_usd, volatility, config.impact_coefficient
    )
    market_impact_cost = impact_bps * trade_value_usd / 10000

    total_cost_usd = commission_spread_cost + market_impact_cost
    total_cost_bps = (total_cost_usd / trade_value_usd * 10000) if trade_value_usd > 0 else 0.0

    # Participation percentage
    participation_pct = None
    if adv_usd is not None and adv_usd > 0 and math.isfinite(adv_usd):
        participation_pct = trade_value_usd / adv_usd

    return TradeCost(
        symbol=symbol,
        trade_date=trade_date,
        trade_value_usd=trade_value_usd,
        commission_spread_cost=commission_spread_cost,
        market_impact_cost=market_impact_cost,
        total_cost_usd=total_cost_usd,
        total_cost_bps=total_cost_bps,
        adv_usd=adv_usd,
        volatility=volatility,
        participation_pct=participation_pct,
    )


def _compute_daily_costs_generic(
    daily_weights: pl.DataFrame,
    adv_volatility: pl.DataFrame,
    config: CostModelConfig,
    identifier_col: str = "symbol",
    use_fallbacks: bool = False,
) -> tuple[pl.DataFrame, list[TradeCost], int, int, int]:
    """Generic function to compute daily transaction costs from weight changes.

    This function supports both symbol-keyed (Yahoo Finance) and permno-keyed (CRSP)
    data by using the identifier_col parameter.

    Args:
        daily_weights: DataFrame with columns [date, <identifier_col>, weight]
        adv_volatility: DataFrame with columns [date, <identifier_col>, adv_usd, volatility]
        config: Cost model configuration
        identifier_col: Column name for security identifier ("symbol" or "permno")
        use_fallbacks: Whether to apply ADV/volatility fallbacks and count violations

    Returns:
        Tuple of:
        - DataFrame with columns [date, cost_drag] (daily cost as fraction of AUM)
        - List of TradeCost objects for each trade
        - ADV fallback count (0 if use_fallbacks=False)
        - Volatility fallback count (0 if use_fallbacks=False)
        - Participation violation count (0 if use_fallbacks=False)
    """
    if not config.enabled:
        dates = daily_weights.select("date").unique().sort("date")
        return dates.with_columns(pl.lit(0.0).alias("cost_drag")), [], 0, 0, 0

    # Extract all unique dates and identifiers to create a full grid
    # This ensures exits (symbol disappearing) and re-entries are captured as trades
    all_dates = daily_weights.select("date").unique().sort("date")
    all_identifiers = daily_weights.select(identifier_col).unique()

    # Warn if the grid would be very large (potential memory/CPU impact)
    grid_size = all_dates.height * all_identifiers.height
    if grid_size > MAX_GRID_SIZE_WARNING:
        logger.warning(
            "large_date_identifier_grid",
            num_dates=all_dates.height,
            num_identifiers=all_identifiers.height,
            grid_size=grid_size,
            message="Large date×identifier grid may impact performance. "
            "Consider using a shorter date range or fewer symbols.",
        )

    # Create full date × identifier grid
    full_grid = all_dates.join(all_identifiers, how="cross")

    # Left join to get actual weights, filling absent entries with 0 weight
    # This captures exits: if a symbol had weight on day D-1 but is absent on day D,
    # the grid will have weight=0 on day D, generating an exit trade
    daily_weights_filled = full_grid.join(
        daily_weights.select(["date", identifier_col, "weight"]),
        on=["date", identifier_col],
        how="left",
    ).with_columns(pl.col("weight").fill_null(0.0))

    # Sort by identifier and date for consistent processing
    daily_weights_filled = daily_weights_filled.sort([identifier_col, "date"])

    # Compute weight changes (turnover), filling null from shift with 0.0 for the first day
    # This assumes starting from cash (zero weight), so first day's change equals the full weight
    weight_changes = daily_weights_filled.with_columns(
        (pl.col("weight") - pl.col("weight").shift(1).over(identifier_col).fill_null(0.0))
        .alias("weight_change")
    )

    # Trade value = |weight_change| * portfolio_value
    weight_changes = weight_changes.with_columns(
        (pl.col("weight_change").abs() * config.portfolio_value_usd).alias("trade_value_usd")
    )

    # Filter to only trades (non-zero weight changes)
    trades = weight_changes.filter(pl.col("trade_value_usd") > 0.01)  # Min $0.01

    # Join with ADV and volatility data
    trades = trades.join(adv_volatility, on=["date", identifier_col], how="left")

    # Early exit if no trades
    if trades.height == 0:
        dates = daily_weights.select("date").unique().sort("date")
        return dates.with_columns(pl.lit(0.0).alias("cost_drag")), [], 0, 0, 0

    # Vectorized fallback and cost computation
    # Step 1: Identify rows needing ADV/volatility fallbacks
    trades = trades.with_columns(
        used_adv_fallback=pl.when(
            pl.col("adv_usd").is_null() | ~pl.col("adv_usd").is_finite() | (pl.col("adv_usd") <= 0)
        ).then(True).otherwise(False),
        used_vol_fallback=pl.when(
            pl.col("volatility").is_null() | ~pl.col("volatility").is_finite() | (pl.col("volatility") <= 0)
        ).then(True).otherwise(False),
    )

    # Step 2: Apply fallbacks using vectorized expressions
    if use_fallbacks:
        trades = trades.with_columns(
            adv_usd=pl.when(pl.col("used_adv_fallback"))
            .then(ADV_FLOOR_USD)
            .otherwise(pl.col("adv_usd")),
            volatility=pl.when(pl.col("used_vol_fallback"))
            .then(VOL_FLOOR)
            .otherwise(pl.col("volatility")),
        )
        # Count fallbacks (vectorized sum)
        adv_fallback_count = trades.select(pl.col("used_adv_fallback").sum()).item()
        vol_fallback_count = trades.select(pl.col("used_vol_fallback").sum()).item()

        # Compute participation and count violations (vectorized)
        trades = trades.with_columns(
            participation_pct=pl.when(pl.col("adv_usd") > 0)
            .then(pl.col("trade_value_usd") / pl.col("adv_usd"))
            .otherwise(0.0)
        )
        participation_violations = trades.filter(
            pl.col("participation_pct") > config.participation_limit
        ).height
    else:
        adv_fallback_count = 0
        vol_fallback_count = 0
        participation_violations = 0
        # Compute participation for cost calculation
        # When not using fallbacks, participation is None if ADV is invalid
        # (null, <= 0, NaN, or infinite) - matches compute_trade_cost() behavior
        trades = trades.with_columns(
            participation_pct=pl.when(
                pl.col("adv_usd").is_not_null()
                & pl.col("adv_usd").is_finite()
                & (pl.col("adv_usd") > 0)
            )
            .then(pl.col("trade_value_usd") / pl.col("adv_usd"))
            .otherwise(None)
        )

    # Step 3: Vectorized cost computation (Almgren-Chriss model)
    # IMPORTANT: This logic mirrors compute_trade_cost() and compute_market_impact().
    # Any changes to those functions must be reflected here to maintain consistency.
    # Commission/spread cost: bps_per_trade * trade_value / 10000
    # Market impact: impact_coefficient * volatility * sqrt(participation) * trade_value
    # Note: impact_bps requires both volatility > 0 and valid participation (not None/0)
    # to match compute_market_impact behavior (returns 0 if ADV or volatility invalid)
    trades = trades.with_columns(
        commission_spread_usd=(config.bps_per_trade * pl.col("trade_value_usd") / 10000),
        impact_bps=pl.when(
            pl.col("volatility").is_not_null()
            & pl.col("volatility").is_finite()
            & (pl.col("volatility") > 0)
            & pl.col("participation_pct").is_not_null()
            & (pl.col("participation_pct") > 0)
        )
        .then(config.impact_coefficient * pl.col("volatility") * 10000 * pl.col("participation_pct").sqrt())
        .otherwise(0.0),
    )
    trades = trades.with_columns(
        market_impact_usd=(pl.col("impact_bps") * pl.col("trade_value_usd") / 10000),
    )
    trades = trades.with_columns(
        total_cost_bps=(config.bps_per_trade + pl.col("impact_bps")),
        total_cost_usd=(pl.col("commission_spread_usd") + pl.col("market_impact_usd")),
    )

    # Step 4: Build TradeCost objects from vectorized results
    # Using to_dicts() is faster than iter_rows() as it's implemented in Rust
    trade_costs: list[TradeCost] = [
        TradeCost(
            symbol=str(row[identifier_col]),
            trade_date=row["date"],
            trade_value_usd=row["trade_value_usd"],
            commission_spread_cost=row["commission_spread_usd"],
            market_impact_cost=row["market_impact_usd"],
            total_cost_bps=row["total_cost_bps"],
            total_cost_usd=row["total_cost_usd"],
            adv_usd=row.get("adv_usd"),
            volatility=row.get("volatility"),
            participation_pct=row.get("participation_pct"),
        )
        for row in trades.to_dicts()
    ]

    # Step 5: Aggregate daily costs using vectorized groupby
    daily_costs_df = trades.group_by("date").agg(
        pl.col("total_cost_usd").sum().alias("daily_cost_usd")
    )

    # Convert to cost drag (fraction of AUM) using vectorized operations
    cost_drag_df = daily_costs_df.with_columns(
        (pl.col("daily_cost_usd") / config.portfolio_value_usd).alias("cost_drag")
    ).select(["date", "cost_drag"]).sort("date")

    # Ensure all dates from daily_weights are included
    all_dates = daily_weights.select("date").unique().sort("date")
    cost_drag_df = all_dates.join(cost_drag_df, on="date", how="left").with_columns(
        pl.col("cost_drag").fill_null(0.0)
    )

    return cost_drag_df, trade_costs, adv_fallback_count, vol_fallback_count, participation_violations


def compute_daily_costs(
    daily_weights: pl.DataFrame,
    adv_data: pl.DataFrame,
    volatility_data: pl.DataFrame,
    config: CostModelConfig,
) -> tuple[pl.DataFrame, list[TradeCost]]:
    """Compute daily transaction costs from weight changes (symbol-keyed).

    This is a convenience wrapper around _compute_daily_costs_generic for
    symbol-keyed data (e.g., Yahoo Finance). For CRSP data, use
    compute_daily_costs_permno which includes fallback counting.

    Args:
        daily_weights: DataFrame with columns [date, symbol, weight]
        adv_data: DataFrame with columns [date, symbol, adv_usd]
        volatility_data: DataFrame with columns [date, symbol, volatility]
        config: Cost model configuration

    Returns:
        Tuple of:
        - DataFrame with columns [date, cost_drag] (daily cost as fraction of AUM)
        - List of TradeCost objects for each trade
    """
    # Combine ADV and volatility data for the generic function
    adv_volatility = adv_data.join(volatility_data, on=["date", "symbol"], how="full")

    cost_drag_df, trade_costs, _, _, _ = _compute_daily_costs_generic(
        daily_weights=daily_weights,
        adv_volatility=adv_volatility,
        config=config,
        identifier_col="symbol",
        use_fallbacks=False,
    )
    return cost_drag_df, trade_costs


def compute_net_returns(
    gross_returns: pl.DataFrame,
    cost_drag: pl.DataFrame,
) -> pl.DataFrame:
    """Compute net returns by subtracting cost drag from gross returns.

    Args:
        gross_returns: DataFrame with columns [date, return]
        cost_drag: DataFrame with columns [date, cost_drag]

    Returns:
        DataFrame with columns [date, gross_return, cost_drag, net_return]
    """
    # Use maintain_order="left" to preserve chronological order from gross_returns
    # This is critical for correct max drawdown calculation downstream
    result = gross_returns.join(cost_drag, on="date", how="left", maintain_order="left")
    result = result.with_columns(pl.col("cost_drag").fill_null(0.0))
    result = result.with_columns(
        (pl.col("return") - pl.col("cost_drag")).alias("net_return")
    )
    result = result.rename({"return": "gross_return"})
    # Sort by date for safety (ensures chronological order even if input wasn't sorted)
    return result.select(["date", "gross_return", "cost_drag", "net_return"]).sort("date")


def compute_compounded_return(returns: Sequence[float | None]) -> float | None:
    """Compute compounded return from daily returns.

    Args:
        returns: List of daily returns (decimals, not percentages), may contain None

    Returns:
        Compounded return, or None if no valid returns.
    """
    valid_returns = [r for r in returns if r is not None and math.isfinite(r)]
    if len(valid_returns) == 0:
        return None

    compounded = 1.0
    for r in valid_returns:
        compounded *= 1 + r
    return compounded - 1


def compute_sharpe_ratio(
    returns: Sequence[float | None], annualization_factor: float = 252
) -> float | None:
    """Compute annualized Sharpe ratio.

    Args:
        returns: List of daily returns, may contain None
        annualization_factor: Trading days per year (default 252)

    Returns:
        Annualized Sharpe ratio, or None if insufficient data.
    """
    valid_returns = [r for r in returns if r is not None and math.isfinite(r)]
    if len(valid_returns) < 2:
        return None

    mean_return = sum(valid_returns) / len(valid_returns)
    variance = sum((r - mean_return) ** 2 for r in valid_returns) / (len(valid_returns) - 1)
    std_dev = math.sqrt(variance)

    if std_dev < MIN_STD_FOR_SHARPE:
        return None

    return mean_return / std_dev * math.sqrt(annualization_factor)


def compute_max_drawdown(returns: Sequence[float | None]) -> float | None:
    """Compute maximum drawdown from daily returns.

    Args:
        returns: List of daily returns, may contain None

    Returns:
        Maximum drawdown as positive fraction, or None if no valid returns.
    """
    valid_returns = [r for r in returns if r is not None and math.isfinite(r)]
    if len(valid_returns) == 0:
        return None

    cumulative = 1.0
    peak = 1.0
    max_dd = 0.0

    for r in valid_returns:
        cumulative *= 1 + r
        peak = max(peak, cumulative)
        drawdown = (peak - cumulative) / peak if peak > 0 else 0.0
        max_dd = max(max_dd, drawdown)

    return max_dd


def compute_cost_summary(
    gross_returns: list[float],
    net_returns: list[float],
    trade_costs: list[TradeCost],
    portfolio_value_usd: float,
    adv_fallback_count: int = 0,
    volatility_fallback_count: int = 0,
    participation_violations: int = 0,
) -> CostSummary:
    """Compute summary statistics for transaction costs.

    Args:
        gross_returns: List of daily gross returns
        net_returns: List of daily net returns
        trade_costs: List of TradeCost objects
        portfolio_value_usd: Portfolio value (for cost drag calculation)
        adv_fallback_count: Number of trades using ADV fallback
        volatility_fallback_count: Number of trades using volatility fallback
        participation_violations: Number of trades exceeding participation limit

    Returns:
        CostSummary with aggregated statistics
    """
    total_commission = sum(tc.commission_spread_cost for tc in trade_costs)
    total_impact = sum(tc.market_impact_cost for tc in trade_costs)
    total_cost = total_commission + total_impact

    total_trade_value = sum(tc.trade_value_usd for tc in trade_costs)
    avg_cost_bps = (total_cost / total_trade_value * 10000) if total_trade_value > 0 else 0.0

    return CostSummary(
        total_gross_return=compute_compounded_return(gross_returns),
        total_net_return=compute_compounded_return(net_returns),
        total_cost_drag=total_cost / portfolio_value_usd if portfolio_value_usd > 0 else 0.0,
        total_cost_usd=total_cost,
        commission_spread_cost_usd=total_commission,
        market_impact_cost_usd=total_impact,
        gross_sharpe=compute_sharpe_ratio(gross_returns),
        net_sharpe=compute_sharpe_ratio(net_returns),
        gross_max_drawdown=compute_max_drawdown(gross_returns),
        net_max_drawdown=compute_max_drawdown(net_returns),
        num_trades=len(trade_costs),
        avg_trade_cost_bps=avg_cost_bps,
        adv_fallback_count=adv_fallback_count,
        volatility_fallback_count=volatility_fallback_count,
        participation_violations=participation_violations,
    )


def compute_capacity_analysis(
    daily_weights: pl.DataFrame,
    trade_costs: list[TradeCost],
    cost_summary: CostSummary,
    config: CostModelConfig,
    identifier_col: str = "symbol",
) -> CapacityAnalysis:
    """Compute capacity analysis for strategy sizing.

    Uses three constraints:
    1. Impact constraint: At what AUM does average impact reach X bps?
    2. Participation constraint: At what AUM does average trade exceed Y% of ADV?
    3. Breakeven constraint: At what AUM does net alpha reach zero?

    Args:
        daily_weights: DataFrame with columns [date, <identifier_col>, weight]
        trade_costs: List of TradeCost objects
        cost_summary: Pre-computed cost summary
        config: Cost model configuration
        identifier_col: Column name for security identifier ("symbol" or "permno")

    Returns:
        CapacityAnalysis with capacity estimates
    """
    # Create full date × identifier grid to capture exits and re-entries
    # When a symbol disappears from daily_weights (e.g., null signal), we need to
    # record the exit trade (weight going to 0). Without the grid, sparse data would
    # miss these trades and understate turnover.
    all_dates = daily_weights.select("date").unique().sort("date")
    all_identifiers = daily_weights.select(identifier_col).unique()

    # Warn if the grid would be very large (potential memory/CPU impact)
    grid_size = all_dates.height * all_identifiers.height
    if grid_size > MAX_GRID_SIZE_WARNING:
        logger.warning(
            "large_date_identifier_grid_capacity",
            num_dates=all_dates.height,
            num_identifiers=all_identifiers.height,
            grid_size=grid_size,
            message="Large date×identifier grid in capacity analysis may impact performance.",
        )

    # Create full grid and fill missing weights with 0
    full_grid = all_dates.join(all_identifiers, how="cross")
    daily_weights_filled = full_grid.join(
        daily_weights.select(["date", identifier_col, "weight"]),
        on=["date", identifier_col],
        how="left",
    ).with_columns(pl.col("weight").fill_null(0.0))

    # Compute daily turnover, filling null from shift with 0.0 for the first day
    # First day's turnover equals the absolute weight (starting from cash)
    weight_changes = daily_weights_filled.sort([identifier_col, "date"]).with_columns(
        (pl.col("weight") - pl.col("weight").shift(1).over(identifier_col).fill_null(0.0))
        .abs()
        .alias("turnover")
    )

    daily_turnover = weight_changes.group_by("date").agg(pl.col("turnover").sum())
    num_days = daily_turnover.height
    if num_days == 0:
        return CapacityAnalysis(
            avg_daily_turnover=None,
            avg_holding_period_days=None,
            portfolio_adv=None,
            portfolio_sigma=None,
            gross_alpha_annualized=None,
            impact_aum_5bps=None,
            impact_aum_10bps=None,
            participation_aum=None,
            breakeven_aum=None,
            implied_max_capacity=None,
            limiting_factor=None,
        )

    avg_turnover = daily_turnover.select(pl.col("turnover").mean()).item()
    avg_holding_period = 1.0 / avg_turnover if avg_turnover and avg_turnover > 0 else None

    # Trade-weighted portfolio ADV and volatility
    total_trade_value = sum(tc.trade_value_usd for tc in trade_costs)
    if total_trade_value <= 0:
        portfolio_adv = None
        portfolio_sigma = None
    else:
        weighted_adv = sum(
            tc.trade_value_usd * (tc.adv_usd or 0)
            for tc in trade_costs
            if tc.adv_usd is not None and math.isfinite(tc.adv_usd)
        )
        weighted_sigma = sum(
            tc.trade_value_usd * (tc.volatility or 0)
            for tc in trade_costs
            if tc.volatility is not None and math.isfinite(tc.volatility)
        )
        portfolio_adv = weighted_adv / total_trade_value if weighted_adv > 0 else None
        portfolio_sigma = weighted_sigma / total_trade_value if weighted_sigma > 0 else None

    # Gross alpha annualized
    # Guard: gross_alpha must be > -1 to avoid complex/undefined exponentiation
    # A gross return of -100% or worse means total loss; capacity is irrelevant
    gross_alpha = cost_summary.total_gross_return
    trading_days = num_days
    gross_alpha_annual = None
    if (
        gross_alpha is not None
        and math.isfinite(gross_alpha)
        and gross_alpha > -1.0  # Prevent (1+gross_alpha) <= 0 causing crash
        and trading_days > 0
    ):
        gross_alpha_annual = (1 + gross_alpha) ** (252 / trading_days) - 1

    # Capacity constraints
    impact_aum_5bps = _compute_impact_aum(5.0, portfolio_adv, portfolio_sigma, avg_turnover, config)
    impact_aum_10bps = _compute_impact_aum(10.0, portfolio_adv, portfolio_sigma, avg_turnover, config)
    participation_aum = _compute_participation_aum(
        portfolio_adv, avg_turnover, config.participation_limit
    )
    breakeven_aum = _compute_breakeven_aum(
        gross_alpha_annual, portfolio_adv, portfolio_sigma, avg_turnover, config
    )

    # Find binding constraint
    constraints = [
        (impact_aum_5bps, "impact_5bps"),
        (participation_aum, "participation"),
        (breakeven_aum, "breakeven"),
    ]
    valid_constraints = [(aum, name) for aum, name in constraints if aum is not None]

    if valid_constraints:
        implied_max, limiting_factor = min(valid_constraints, key=lambda x: x[0])
    else:
        implied_max = None
        limiting_factor = None

    return CapacityAnalysis(
        avg_daily_turnover=avg_turnover,
        avg_holding_period_days=avg_holding_period,
        portfolio_adv=portfolio_adv,
        portfolio_sigma=portfolio_sigma,
        gross_alpha_annualized=gross_alpha_annual,
        impact_aum_5bps=impact_aum_5bps,
        impact_aum_10bps=impact_aum_10bps,
        participation_aum=participation_aum,
        breakeven_aum=breakeven_aum,
        implied_max_capacity=implied_max,
        limiting_factor=limiting_factor,
    )


def _compute_impact_aum(
    target_impact_bps: float,
    portfolio_adv: float | None,
    portfolio_sigma: float | None,
    avg_turnover: float | None,
    config: CostModelConfig,
) -> float | None:
    """Compute AUM at which average market impact reaches target.

    From Almgren-Chriss: impact = eta * sigma * sqrt(trade / ADV)
    Solving for trade at target impact: trade = (target / (eta * sigma))^2 * ADV
    AUM = trade / avg_turnover
    """
    if portfolio_adv is None or portfolio_adv <= 0 or not math.isfinite(portfolio_adv):
        return None
    if portfolio_sigma is None or portfolio_sigma <= 0 or not math.isfinite(portfolio_sigma):
        return None
    if avg_turnover is None or avg_turnover <= 0 or not math.isfinite(avg_turnover):
        return None
    if config.impact_coefficient <= 0:
        return None

    # impact_bps = eta * sigma * 10000 * sqrt(participation)
    # target_bps = eta * sigma * 10000 * sqrt(trade / ADV)
    # sqrt(trade / ADV) = target_bps / (eta * sigma * 10000)
    # trade / ADV = (target_bps / (eta * sigma * 10000))^2
    # trade = ADV * (target_bps / (eta * sigma * 10000))^2
    participation_at_target = (target_impact_bps / (config.impact_coefficient * portfolio_sigma * 10000)) ** 2
    trade_at_target = portfolio_adv * participation_at_target

    # Convert trade size to AUM: AUM = trade / avg_turnover
    return trade_at_target / avg_turnover


def _compute_participation_aum(
    portfolio_adv: float | None,
    avg_turnover: float | None,
    participation_limit: float,
) -> float | None:
    """Compute AUM at which average trade exceeds participation limit."""
    if portfolio_adv is None or portfolio_adv <= 0 or not math.isfinite(portfolio_adv):
        return None
    if avg_turnover is None or avg_turnover <= 0 or not math.isfinite(avg_turnover):
        return None

    # At limit: trade = participation_limit * ADV
    # trade = avg_turnover * AUM
    # AUM = participation_limit * ADV / avg_turnover
    return participation_limit * portfolio_adv / avg_turnover


def _compute_breakeven_aum(
    gross_alpha_annual: float | None,
    portfolio_adv: float | None,
    portfolio_sigma: float | None,
    avg_turnover: float | None,
    config: CostModelConfig,
) -> float | None:
    """Compute AUM at which net alpha reaches zero via binary search.

    Net alpha = gross_alpha - cost_drag
    Cost_drag = (bps_per_trade + impact_bps) * turnover * 252 / 10000
    Impact depends on sqrt(AUM * turnover / ADV)
    """
    if gross_alpha_annual is None or gross_alpha_annual <= 0 or not math.isfinite(gross_alpha_annual):
        return None
    if portfolio_adv is None or portfolio_adv <= 0 or not math.isfinite(portfolio_adv):
        return None
    if portfolio_sigma is None or portfolio_sigma <= 0 or not math.isfinite(portfolio_sigma):
        return None
    if avg_turnover is None or avg_turnover <= 0 or not math.isfinite(avg_turnover):
        return None

    def compute_net_alpha(aum: float) -> float | None:
        """Compute net alpha at given AUM."""
        daily_trade = aum * avg_turnover
        participation = daily_trade / portfolio_adv
        impact_bps = config.impact_coefficient * portfolio_sigma * 10000 * math.sqrt(participation)
        total_cost_bps = config.bps_per_trade + impact_bps
        annual_cost_drag = total_cost_bps * avg_turnover * 252 / 10000
        return gross_alpha_annual - annual_cost_drag

    # Binary search for breakeven
    low, high = 1000.0, 10_000_000_000.0  # $1K to $10B

    # Guard: Check if net alpha is already negative at lower bound
    # If so, the strategy is unprofitable even at minimal AUM (costs exceed alpha)
    net_alpha_at_low = compute_net_alpha(low)
    if net_alpha_at_low is not None and net_alpha_at_low <= 0:
        return None  # Strategy unprofitable at minimal AUM, no valid breakeven

    # Guard: Check if net alpha is still positive at upper bound
    # If so, breakeven is above our search range (capacity > $10B)
    net_alpha_at_high = compute_net_alpha(high)
    if net_alpha_at_high is not None and net_alpha_at_high > 0:
        return None  # Capacity exceeds $10B, no breakeven in search range

    for _ in range(50):  # Max iterations
        mid = (low + high) / 2
        net_alpha = compute_net_alpha(mid)
        if net_alpha is None:
            return None
        if abs(net_alpha) < 1e-6:  # Close enough to zero
            return mid
        if net_alpha > 0:
            low = mid
        else:
            high = mid

    return (low + high) / 2


def compute_rolling_adv_volatility(
    price_volume_data: pl.DataFrame,
    start_date: date,
    end_date: date,
) -> pl.DataFrame:
    """Compute rolling ADV and volatility from price/volume data.

    Computes 20-day rolling averages with 1-day lag for PIT compliance.

    Args:
        price_volume_data: DataFrame with columns [permno, date, prc, vol, ret]
            where prc is price, vol is volume, ret is return.
        start_date: Start of date range for output (inclusive).
        end_date: End of date range for output (inclusive).

    Returns:
        DataFrame with columns [permno, date, adv_usd, volatility]
        Both metrics are LAGGED by 1 day (use D-1 values for D trades).
    """
    if price_volume_data.height == 0:
        return pl.DataFrame(
            schema={
                "permno": pl.Int64,
                "date": pl.Date,
                "adv_usd": pl.Float64,
                "volatility": pl.Float64,
            }
        )

    # Sort by permno and date for correct rolling computation
    df = price_volume_data.sort(["permno", "date"])

    # Compute dollar volume for ADV (handle null prices/volumes)
    df = df.with_columns(
        pl.when(pl.col("prc").is_not_null() & pl.col("vol").is_not_null())
        .then(pl.col("prc").abs() * pl.col("vol"))
        .otherwise(None)
        .alias("dollar_vol")
    )

    # Compute rolling ADV and volatility per permno
    # Using Polars rolling_mean/rolling_std with min_samples (renamed from min_periods in 1.21.0)
    df = df.with_columns(
        [
            pl.col("dollar_vol")
            .rolling_mean(window_size=ADV_WINDOW_DAYS, min_samples=ADV_WINDOW_DAYS)
            .over("permno")
            .alias("adv_usd_raw"),
            pl.col("ret")
            .rolling_std(window_size=VOL_WINDOW_DAYS, min_samples=VOL_WINDOW_DAYS, ddof=1)
            .over("permno")
            .alias("volatility_raw"),
        ]
    )

    # Lag by 1 day (use D-1 values for D trades)
    df = df.with_columns(
        [
            pl.col("adv_usd_raw").shift(1).over("permno").alias("adv_usd"),
            pl.col("volatility_raw").shift(1).over("permno").alias("volatility"),
        ]
    )

    # Filter to requested date range
    result = df.filter(
        (pl.col("date") >= start_date) & (pl.col("date") <= end_date)
    ).select(["permno", "date", "adv_usd", "volatility"])

    return result


def load_pit_adv_volatility(
    crsp_provider: CRSPLocalProvider,
    permnos: list[int],
    start_date: date,
    end_date: date,
) -> pl.DataFrame:
    """Load PIT-compliant ADV and volatility from CRSP provider.

    Uses same PIT-compliant data path as returns for reproducibility.

    Args:
        crsp_provider: CRSP data provider.
        permnos: List of PERMNOs to load data for.
        start_date: Start of date range (inclusive).
        end_date: End of date range (inclusive).

    Returns:
        DataFrame with columns [permno, date, adv_usd, volatility]
        Both metrics are LAGGED by 1 day to avoid lookahead bias.
    """
    # Extra lookback for rolling window + lag
    lookback_start = start_date - timedelta(days=LOOKBACK_CALENDAR_DAYS)

    # Load price/volume/return data from CRSP
    price_data = crsp_provider.get_daily_prices(
        start_date=lookback_start,
        end_date=end_date,
        permnos=permnos,
        columns=["permno", "date", "prc", "vol", "ret"],
        adjust_prices=True,  # Use absolute price values
    )

    if price_data.height == 0:
        logger.warning(
            "no_price_data_for_adv",
            start_date=str(start_date),
            end_date=str(end_date),
            num_permnos=len(permnos),
        )
        return pl.DataFrame(
            schema={
                "permno": pl.Int64,
                "date": pl.Date,
                "adv_usd": pl.Float64,
                "volatility": pl.Float64,
            }
        )

    return compute_rolling_adv_volatility(price_data, start_date, end_date)


def apply_adv_fallback(adv_raw: float | None, permno: int) -> tuple[float, bool]:
    """Get ADV with deterministic fallback.

    Args:
        adv_raw: Raw ADV value (may be None, non-positive, NaN, or inf).
        permno: PERMNO for logging.

    Returns:
        Tuple of (adv_value, used_fallback).
    """
    if adv_raw is None or not math.isfinite(adv_raw) or adv_raw <= 0:
        logger.debug("adv_fallback_used", permno=permno, fallback=ADV_FLOOR_USD)
        return ADV_FLOOR_USD, True
    return adv_raw, False


def apply_volatility_fallback(vol_raw: float | None, permno: int) -> tuple[float, bool]:
    """Get volatility with deterministic fallback.

    Args:
        vol_raw: Raw volatility value (may be None, non-positive, NaN, or inf).
        permno: PERMNO for logging.

    Returns:
        Tuple of (volatility_value, used_fallback).
    """
    if vol_raw is None or not math.isfinite(vol_raw) or vol_raw <= 0:
        logger.debug("volatility_fallback_used", permno=permno, fallback=VOL_FLOOR)
        return VOL_FLOOR, True
    return vol_raw, False


def compute_daily_costs_permno(
    daily_weights: pl.DataFrame,
    adv_volatility: pl.DataFrame,
    config: CostModelConfig,
) -> tuple[pl.DataFrame, list[TradeCost], int, int, int]:
    """Compute daily transaction costs from weight changes (permno-keyed).

    This is a convenience wrapper around _compute_daily_costs_generic for
    permno-keyed data (CRSP). Includes ADV/volatility fallback counting
    and participation violation tracking.

    Args:
        daily_weights: DataFrame with columns [permno, date, weight].
        adv_volatility: DataFrame with columns [permno, date, adv_usd, volatility].
        config: Cost model configuration.

    Returns:
        Tuple of:
        - DataFrame with columns [date, cost_drag] (daily cost as fraction of AUM)
        - List of TradeCost objects for each trade
        - ADV fallback count
        - Volatility fallback count
        - Participation violation count
    """
    return _compute_daily_costs_generic(
        daily_weights=daily_weights,
        adv_volatility=adv_volatility,
        config=config,
        identifier_col="permno",
        use_fallbacks=True,
    )


@dataclass
class BacktestCostResult:
    """Complete result of cost model computation.

    Attributes:
        cost_summary: Summary statistics for transaction costs.
        capacity_analysis: Capacity analysis for strategy sizing.
        net_returns_df: DataFrame with daily net returns.
        cost_drag_df: DataFrame with daily cost drag.
        trade_costs: List of individual trade costs.
        adv_fallback_count: Number of trades using ADV fallback.
        volatility_fallback_count: Number of trades using volatility fallback.
        participation_violations: Number of trades exceeding participation limit.
    """

    cost_summary: CostSummary
    capacity_analysis: CapacityAnalysis
    net_returns_df: pl.DataFrame
    cost_drag_df: pl.DataFrame
    trade_costs: list[TradeCost]
    adv_fallback_count: int
    volatility_fallback_count: int
    participation_violations: int


def compute_backtest_costs(
    daily_weights: pl.DataFrame,
    gross_returns: pl.DataFrame,
    adv_volatility: pl.DataFrame,
    config: CostModelConfig,
) -> BacktestCostResult:
    """Compute full cost analysis for a backtest.

    Args:
        daily_weights: DataFrame with columns [permno, date, weight].
        gross_returns: DataFrame with columns [date, return].
        adv_volatility: DataFrame with columns [permno, date, adv_usd, volatility].
        config: Cost model configuration.

    Returns:
        BacktestCostResult with full cost analysis.
    """
    # Compute daily costs
    cost_drag_df, trade_costs, adv_fallback, vol_fallback, violations = compute_daily_costs_permno(
        daily_weights, adv_volatility, config
    )

    # Compute net returns
    net_returns_df = compute_net_returns(gross_returns, cost_drag_df)

    # Extract return lists for summary computation
    gross_return_list = net_returns_df.select("gross_return").to_series().to_list()
    net_return_list = net_returns_df.select("net_return").to_series().to_list()

    # Compute cost summary
    cost_summary = compute_cost_summary(
        gross_returns=gross_return_list,
        net_returns=net_return_list,
        trade_costs=trade_costs,
        portfolio_value_usd=config.portfolio_value_usd,
        adv_fallback_count=adv_fallback,
        volatility_fallback_count=vol_fallback,
        participation_violations=violations,
    )

    # Compute capacity analysis
    capacity_analysis = compute_capacity_analysis(
        daily_weights=daily_weights,
        trade_costs=trade_costs,
        cost_summary=cost_summary,
        config=config,
        identifier_col="permno",
    )

    return BacktestCostResult(
        cost_summary=cost_summary,
        capacity_analysis=capacity_analysis,
        net_returns_df=net_returns_df,
        cost_drag_df=cost_drag_df,
        trade_costs=trade_costs,
        adv_fallback_count=adv_fallback,
        volatility_fallback_count=vol_fallback,
        participation_violations=violations,
    )


__all__ = [
    "ADVSource",
    "ADV_FLOOR_USD",
    "ADV_WINDOW_DAYS",
    "BacktestCostResult",
    "CostModelConfig",
    "TradeCost",
    "CostSummary",
    "CapacityAnalysis",
    "VOL_FLOOR",
    "VOL_WINDOW_DAYS",
    "apply_adv_fallback",
    "apply_volatility_fallback",
    "compute_backtest_costs",
    "compute_daily_costs",
    "compute_daily_costs_permno",
    "compute_market_impact",
    "compute_net_returns",
    "compute_compounded_return",
    "compute_rolling_adv_volatility",
    "compute_sharpe_ratio",
    "compute_max_drawdown",
    "compute_cost_summary",
    "compute_capacity_analysis",
    "compute_trade_cost",
    "load_pit_adv_volatility",
]
