"""Execution quality analysis with benchmark comparison and cost decomposition.

Implements:
- Fill and FillBatch Pydantic models for execution data
- VWAP and TWAP benchmark computation
- Implementation shortfall calculation (side-adjusted with opportunity cost)
- Market impact estimation using T3.1 spread data
- Cost decomposition (price_shortfall + fee_cost + opportunity_cost)

All outputs include dataset_version_id for reproducibility and PIT support.
Design per approved T3.2 plan v7.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Literal, cast

import polars as pl
from pydantic import BaseModel, Field, field_validator, model_validator

from libs.analytics.microstructure import CompositeVersionInfo, SpreadDepthResult

if TYPE_CHECKING:
    from libs.analytics.microstructure import MicrostructureAnalyzer
    from libs.data_providers.taq_query_provider import TAQLocalProvider

logger = logging.getLogger(__name__)


def _series_mean_to_float(val: Any) -> float:
    """Convert polars Series.mean() result to float safely.

    polars mean() can return various types. This helper handles the
    conversion with proper None handling for mypy compatibility.
    """
    if val is None:
        return float("nan")
    return float(cast("float", val))


# =============================================================================
# Pydantic Models - Fill Schemas
# =============================================================================


class Fill(BaseModel):
    """Single fill record.

    Each fill is self-contained with symbol/side for standalone use,
    dedupe via fill_id, and explicit fee handling.

    Attributes:
        fill_id: Broker-assigned unique fill ID for deduplication.
        order_id: Parent order ID.
        client_order_id: Idempotent client order ID.
        timestamp: Fill timestamp (UTC required).
        symbol: Symbol at fill level.
        side: Side at fill level ("buy" or "sell").
        price: Fill price per share (must be > 0).
        quantity: Shares filled (must be > 0).
        exchange: Exchange where fill occurred (optional).
        liquidity_flag: "add" for maker, "remove" for taker (optional).
        fee_amount: Total fee (positive) or rebate (negative).
        fee_currency: Currency of fee (default "USD").
    """

    # Identity (REQUIRED for idempotency/dedupe)
    fill_id: str = Field(..., description="Broker-assigned unique fill ID")
    order_id: str = Field(..., description="Parent order ID")
    client_order_id: str = Field(..., description="Idempotent client order ID")

    # Core fields
    timestamp: datetime = Field(..., description="Fill timestamp (UTC required)")
    symbol: str = Field(..., description="Symbol at fill level")
    side: Literal["buy", "sell"] = Field(..., description="Side at fill level")
    price: float = Field(..., gt=0, description="Fill price per share")
    quantity: int = Field(..., gt=0, description="Shares filled")

    # Venue details
    exchange: str | None = Field(default=None, description="Exchange where fill occurred")
    liquidity_flag: Literal["add", "remove"] | None = Field(
        default=None, description="Liquidity flag: 'add' (maker) or 'remove' (taker)"
    )

    # Fee handling (REQUIRED for accurate IS)
    fee_amount: float = Field(
        default=0.0, description="Total fee (positive) or rebate (negative)"
    )
    fee_currency: str = Field(default="USD", description="Currency of fee")

    @field_validator("timestamp")
    @classmethod
    def validate_utc(cls, v: datetime) -> datetime:
        """Validate timestamp is timezone-aware UTC."""
        if v.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware UTC")
        offset = v.utcoffset()
        if offset is not None and offset.total_seconds() != 0:
            raise ValueError("timestamp must be UTC (offset must be 0)")
        return v

    @field_validator("symbol")
    @classmethod
    def validate_symbol(cls, v: str) -> str:
        """Normalize symbol to uppercase and strip whitespace."""
        return v.upper().strip()

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "fill_id": "fill_001",
                    "order_id": "order_123",
                    "client_order_id": "client_abc",
                    "timestamp": "2024-12-08T14:30:00Z",
                    "symbol": "AAPL",
                    "side": "buy",
                    "price": 150.25,
                    "quantity": 100,
                    "exchange": "XNYS",
                    "liquidity_flag": "remove",
                    "fee_amount": 0.50,
                    "fee_currency": "USD",
                }
            ]
        }
    }


class FillBatch(BaseModel):
    """Batch of fills for execution quality analysis.

    Includes chronological validation and aggregate properties.
    All timestamps UTC-validated, side-mismatch consistently handled.

    Attributes:
        symbol: Primary symbol (fills may have different symbols for multi-leg).
        side: Primary side ("buy" or "sell").
        fills: List of Fill objects.
        decision_time: When signal was generated (arrival price source).
        submission_time: When order was submitted to broker.
        total_target_qty: Total quantity intended to fill.
    """

    symbol: str = Field(..., description="Primary symbol")
    side: Literal["buy", "sell"] = Field(..., description="Primary side")
    fills: list[Fill] = Field(..., description="List of fills")
    decision_time: datetime = Field(
        ..., description="When signal was generated (arrival price source)"
    )
    submission_time: datetime = Field(
        ..., description="When order was submitted to broker"
    )
    total_target_qty: int = Field(
        ..., gt=0, description="Total quantity intended to fill"
    )

    @field_validator("decision_time", "submission_time")
    @classmethod
    def validate_utc_timestamps(cls, v: datetime) -> datetime:
        """Validate batch timestamps are UTC."""
        if v.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware UTC")
        offset = v.utcoffset()
        if offset is not None and offset.total_seconds() != 0:
            raise ValueError("timestamp must be UTC (offset must be 0)")
        return v

    @field_validator("symbol")
    @classmethod
    def validate_symbol(cls, v: str) -> str:
        """Normalize symbol to uppercase and strip whitespace."""
        return v.upper().strip()

    @model_validator(mode="after")
    def validate_chronology_and_sides(self) -> FillBatch:
        """Validate chronology: decision_time <= submission_time (STRICT).

        Raises:
            ValueError: If decision_time > submission_time.
        """
        if self.decision_time > self.submission_time:
            raise ValueError(
                f"decision_time ({self.decision_time}) must be <= "
                f"submission_time ({self.submission_time})"
            )
        return self

    @property
    def matching_fills(self) -> list[Fill]:
        """Fills with side == batch.side (used for filtering)."""
        return [f for f in self.fills if f.side == self.side]

    @property
    def valid_fills(self) -> list[Fill]:
        """Fills valid for IS calculation: matching symbol, side AND after decision_time.

        Excludes:
        - Fills with symbol != batch.symbol (cross-symbol contamination)
        - Fills with side != batch.side (potential errors)
        - Fills with timestamp < decision_time (pre-signal, invalid)
        """
        return [
            f
            for f in self.fills
            if f.symbol == self.symbol
            and f.side == self.side
            and f.timestamp >= self.decision_time
        ]

    @property
    def fills_before_decision(self) -> list[Fill]:
        """Fills timestamped before decision_time (excluded from IS)."""
        return [f for f in self.fills if f.timestamp < self.decision_time]

    @property
    def has_fills_before_decision(self) -> bool:
        """True if any fill timestamp < decision_time."""
        return len(self.fills_before_decision) > 0

    @property
    def mismatched_side_fills(self) -> list[Fill]:
        """Fills with side != batch.side (potential crossed/error fills)."""
        return [f for f in self.fills if f.side != self.side]

    @property
    def has_side_mismatch(self) -> bool:
        """True if any fill has side != batch.side."""
        return len(self.mismatched_side_fills) > 0

    @property
    def clock_drift_detected(self) -> bool:
        """Check if submission_time > first_fill by >100ms."""
        drift = self.clock_drift_ms
        if drift is None:
            return False
        return drift > 100  # >100ms threshold

    @property
    def clock_drift_ms(self) -> float | None:
        """Clock drift in milliseconds (submission to first fill).

        Uses valid_fills if available, falls back to all fills for drift detection.
        Positive = submission after fill (potential clock sync issue).
        """
        fills_for_drift = self.valid_fills if self.valid_fills else self.fills
        if not fills_for_drift:
            return None
        first_fill = min(f.timestamp for f in fills_for_drift)
        delta = (self.submission_time - first_fill).total_seconds() * 1000
        return delta

    @property
    def total_filled_qty(self) -> int:
        """Total quantity from valid fills only."""
        return sum(f.quantity for f in self.valid_fills)

    @property
    def unfilled_qty(self) -> int:
        """Unfilled quantity for opportunity cost calculation."""
        return max(0, self.total_target_qty - self.total_filled_qty)

    @property
    def avg_fill_price(self) -> float:
        """Volume-weighted average fill price from valid fills only."""
        fills = self.valid_fills
        if not fills:
            return 0.0
        total_value = sum(f.price * f.quantity for f in fills)
        total_qty = sum(f.quantity for f in fills)
        return total_value / total_qty if total_qty > 0 else 0.0

    @property
    def total_fees(self) -> float:
        """Total fees from valid fills only."""
        return sum(f.fee_amount for f in self.valid_fills)

    @property
    def has_mixed_currencies(self) -> bool:
        """True if fills have different fee_currency values."""
        currencies = {f.fee_currency for f in self.valid_fills}
        return len(currencies) > 1

    @property
    def has_non_usd_fees(self) -> bool:
        """True if any fill has fee_currency != USD."""
        return any(f.fee_currency != "USD" for f in self.valid_fills)

    @property
    def fee_currency(self) -> str:
        """Fee currency (all fills should match)."""
        currencies = {f.fee_currency for f in self.valid_fills}
        if len(currencies) == 1:
            return currencies.pop()
        return "MIXED"  # Warning: mixed currencies detected

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "symbol": "AAPL",
                    "side": "buy",
                    "fills": [],
                    "decision_time": "2024-12-08T14:30:00Z",
                    "submission_time": "2024-12-08T14:30:01Z",
                    "total_target_qty": 1000,
                }
            ]
        }
    }


class FillStatus(str, Enum):
    """Fill lifecycle status."""

    FILLED = "filled"
    PARTIAL = "partial"
    CANCELLED = "cancelled"
    AMENDED = "amended"


class ExtendedFill(Fill):
    """Extended fill with lifecycle tracking for complex order handling.

    Adds status tracking and timing fields for latency analysis.
    All optional timestamps are UTC-validated if provided.
    """

    status: FillStatus = Field(default=FillStatus.FILLED, description="Fill status")
    amends_fill_id: str | None = Field(
        default=None, description="ID of fill being amended"
    )
    cancel_reason: str | None = Field(default=None, description="Reason for cancel")

    # Timing for latency analysis (all UTC-validated)
    broker_received_at: datetime | None = Field(
        default=None, description="When broker received order"
    )
    exchange_ack_at: datetime | None = Field(
        default=None, description="When exchange acknowledged"
    )
    fill_reported_at: datetime | None = Field(
        default=None, description="When fill was reported"
    )

    @field_validator(
        "broker_received_at", "exchange_ack_at", "fill_reported_at", mode="before"
    )
    @classmethod
    def validate_optional_utc(cls, v: datetime | None) -> datetime | None:
        """Validate optional timestamps are UTC if provided."""
        if v is None:
            return None
        if v.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware UTC")
        offset = v.utcoffset()
        if offset is not None and offset.total_seconds() != 0:
            raise ValueError("timestamp must be UTC (offset must be 0)")
        return v


# =============================================================================
# Result Dataclass
# =============================================================================


@dataclass
class ExecutionAnalysisResult:
    """Result of execution quality analysis.

    Inherits versioning pattern from MicrostructureResult for PIT support.
    All cost metrics are in basis points (bps) and sign-adjusted so
    positive = worse execution (cost), negative = price improvement.

    Cost decomposition (v7):
    - price_shortfall_bps: Price-only component on filled qty
    - fee_cost_bps: Fee component (positive=cost, negative=rebate)
    - opportunity_cost_bps: Cost of unfilled qty (weighted by unfilled fraction)
    - total_cost_bps: price_shortfall + fees + opportunity (true IS)
    - market_impact_bps: Estimated permanent price impact
    - timing_cost_bps: price_shortfall - market_impact (delay cost)
    """

    # === MicrostructureResult base fields (REQUIRED for PIT parity) ===
    dataset_version_id: str
    dataset_versions: dict[str, str] | None
    computation_timestamp: datetime
    as_of_date: date | None

    # === Execution identification ===
    symbol: str
    side: Literal["buy", "sell"]
    execution_date: date

    # === Core prices ===
    arrival_price: float  # Price at decision_time
    execution_price: float  # Volume-weighted avg fill price
    vwap_benchmark: float  # Market VWAP over execution window
    twap_benchmark: float  # Market TWAP over execution window
    mid_price_at_arrival: float | None  # Mid from T3.1 spread stats

    # === Cost decomposition (bps, sign-adjusted) ===
    price_shortfall_bps: float  # Price-only component on filled qty
    vwap_slippage_bps: float  # (exec - vwap) / vwap * 10000 * side_sign
    fee_cost_bps: float  # SIGNED: positive=fee, negative=rebate
    opportunity_cost_bps: float  # Unfilled qty cost (weighted by unfilled fraction)
    total_cost_bps: float  # price_shortfall + fee_cost + opportunity_cost

    # === Impact decomposition ===
    market_impact_bps: float  # Estimated permanent impact
    timing_cost_bps: float  # price_shortfall - market_impact

    # === Fill statistics ===
    fill_rate: float  # filled_qty / target_qty (0-1)
    total_filled_qty: int
    unfilled_qty: int  # target - filled
    total_target_qty: int
    total_notional: float  # execution_price * total_filled_qty
    total_fees: float
    close_price: float | None  # Close price for opportunity cost
    execution_duration_seconds: float
    num_fills: int

    # === Data quality ===
    warnings: list[str] = field(default_factory=list)
    arrival_source: Literal["decision_time", "submission_time"] = "decision_time"
    clock_drift_warning: bool = False  # True if submission > first_fill by >100ms
    fills_before_decision_warning: bool = False  # True if any fill < decision_time
    side_mismatch_warning: bool = False  # True if any fill.side != batch.side
    mixed_currency_warning: bool = False  # True if fills have different currencies
    non_usd_fee_warning: bool = False  # True if any fill has non-USD fee
    vwap_coverage_pct: float = 0.0  # % of execution window with TAQ data


@dataclass
class ExecutionWindowRecommendation:
    """Recommendation for optimal execution timing based on liquidity patterns."""

    symbol: str
    target_date: date
    order_size_shares: int
    recommended_start_time: datetime
    recommended_end_time: datetime
    expected_participation_rate: float  # % of average volume
    avg_spread_bps: float
    liquidity_score: float  # 0-1, higher = better
    warnings: list[str] = field(default_factory=list)


# =============================================================================
# Core Analyzer Class
# =============================================================================


class ExecutionQualityAnalyzer:
    """Analyze execution quality against market benchmarks.

    Uses TAQLocalProvider for benchmark data and MicrostructureAnalyzer
    for spread statistics integration.

    Metrics computed:
    - VWAP/TWAP benchmarks from TAQ minute bars
    - Implementation shortfall with opportunity cost
    - Market impact estimation using spread data
    - Cost decomposition (price, fees, opportunity)

    Example:
        >>> analyzer = ExecutionQualityAnalyzer(taq_provider, micro_analyzer)
        >>> result = analyzer.analyze_execution(fill_batch)
        >>> print(f"Total cost: {result.total_cost_bps:.1f} bps")
    """

    DATASET_1MIN = "taq_1min_bars"
    DATASET_SPREAD_STATS = "taq_spread_stats"

    def __init__(
        self,
        taq_provider: TAQLocalProvider,
        microstructure_analyzer: MicrostructureAnalyzer | None = None,
    ) -> None:
        """Initialize with TAQ data provider.

        Args:
            taq_provider: TAQLocalProvider instance for data access.
            microstructure_analyzer: Optional MicrostructureAnalyzer for spread data.
        """
        self.taq = taq_provider
        self.micro = microstructure_analyzer

    def _get_multi_version_id(
        self,
        datasets: list[str],
        as_of: date | None = None,
    ) -> CompositeVersionInfo:
        """Get version IDs for multiple datasets from SINGLE SNAPSHOT.

        Uses the same pattern as MicrostructureAnalyzer for PIT consistency.
        """
        if as_of:
            if self.taq.version_manager is None:
                raise ValueError("version_manager required for PIT queries")

            _path, snapshot = self.taq.version_manager.query_as_of(datasets[0], as_of)

            versions = {}
            for ds in datasets:
                if ds not in snapshot.datasets:
                    # Dataset not in snapshot - use "unknown"
                    versions[ds] = "unknown"
                else:
                    versions[ds] = str(snapshot.datasets[ds].sync_manifest_version)

            return CompositeVersionInfo(
                versions=versions,
                snapshot_id=snapshot.aggregate_checksum,
                is_pit=True,
            )
        else:
            versions = {}
            for ds in datasets:
                manifest = self.taq.manifest_manager.load_manifest(ds)
                versions[ds] = manifest.checksum if manifest else "unknown"

            return CompositeVersionInfo(
                versions=versions,
                snapshot_id=None,
                is_pit=False,
            )

    def analyze_execution(
        self,
        fill_batch: FillBatch,
        as_of: date | None = None,
        close_price: float | None = None,
    ) -> ExecutionAnalysisResult:
        """Full execution quality analysis.

        Args:
            fill_batch: Batch of fills to analyze.
            as_of: Optional PIT date for benchmark data.
            close_price: Optional close price for opportunity cost calculation.
                         If not provided, will attempt to fetch from TAQ data.

        Returns:
            ExecutionAnalysisResult with complete cost decomposition.

        Raises:
            ValueError: If no valid fills to analyze.
        """
        warnings: list[str] = []

        # Check for valid fills
        valid_fills = fill_batch.valid_fills
        if not valid_fills:
            raise ValueError(
                "No valid fills to analyze. Check that fills have matching side "
                "and timestamps >= decision_time."
            )

        # Data quality warnings
        clock_drift_warning = fill_batch.clock_drift_detected
        fills_before_decision_warning = fill_batch.has_fills_before_decision
        side_mismatch_warning = fill_batch.has_side_mismatch
        mixed_currency_warning = fill_batch.has_mixed_currencies
        non_usd_fee_warning = fill_batch.has_non_usd_fees

        if clock_drift_warning:
            drift_ms = fill_batch.clock_drift_ms
            warnings.append(f"Clock drift detected: {drift_ms:.0f}ms")

        if fills_before_decision_warning:
            count = len(fill_batch.fills_before_decision)
            warnings.append(
                f"{count} fill(s) before decision_time excluded from analysis"
            )

        if side_mismatch_warning:
            count = len(fill_batch.mismatched_side_fills)
            warnings.append(
                f"{count} fill(s) with mismatched side excluded from analysis"
            )

        if mixed_currency_warning:
            warnings.append("Mixed fee currencies detected - fee aggregation may be incorrect")

        if non_usd_fee_warning:
            warnings.append("Non-USD fee currency detected - fee_cost_bps assumes USD")

        # Execution metrics
        symbol = fill_batch.symbol
        side = fill_batch.side
        execution_price = fill_batch.avg_fill_price
        total_filled_qty = fill_batch.total_filled_qty
        unfilled_qty = fill_batch.unfilled_qty
        total_target_qty = fill_batch.total_target_qty
        total_fees = fill_batch.total_fees

        # Execution window
        first_fill_time = min(f.timestamp for f in valid_fills)
        last_fill_time = max(f.timestamp for f in valid_fills)
        execution_date = first_fill_time.date()
        execution_duration_seconds = (last_fill_time - first_fill_time).total_seconds()

        # Get version info for datasets
        version_info = self._get_multi_version_id(
            [self.DATASET_1MIN, self.DATASET_SPREAD_STATS], as_of
        )

        # Get arrival price from TAQ data at decision_time
        arrival_price, arrival_source = self._get_arrival_price(
            symbol=symbol,
            decision_time=fill_batch.decision_time,
            submission_time=fill_batch.submission_time,
            as_of=as_of,
            warnings=warnings,
        )

        # Compute benchmarks
        vwap_benchmark, vwap_coverage = self._compute_vwap_with_coverage(
            symbol=symbol,
            start_time=fill_batch.decision_time,
            end_time=last_fill_time,
            as_of=as_of,
        )

        twap_benchmark = self._compute_twap(
            symbol=symbol,
            start_time=fill_batch.decision_time,
            end_time=last_fill_time,
            as_of=as_of,
        )

        if vwap_coverage < 0.8:
            warnings.append(f"Low VWAP coverage: {vwap_coverage:.1%}")

        # Get close price for opportunity cost
        actual_close_price = close_price
        if actual_close_price is None and unfilled_qty > 0:
            actual_close_price = self._get_close_price(symbol, execution_date, as_of)
            if actual_close_price is None:
                warnings.append("Close price unavailable for opportunity cost calculation")

        # Get spread stats for market impact
        spread_stats = None
        mid_price_at_arrival: float | None = None
        if self.micro is not None:
            try:
                spread_stats = self.micro.compute_spread_depth_stats(
                    symbol=symbol,
                    target_date=execution_date,
                    as_of=as_of,
                )
            except Exception as e:
                logger.warning(
                    "Failed to get spread stats for market impact",
                    extra={"symbol": symbol, "date": str(execution_date), "error": str(e)},
                )
                warnings.append("Spread data unavailable - using arrival_price as mid proxy")

        # Compute cost decomposition
        side_sign = 1 if side == "buy" else -1

        # Price shortfall (filled qty only)
        if arrival_price > 0:
            price_shortfall_bps = (
                side_sign * (execution_price - arrival_price) / arrival_price * 10000
            )
        else:
            price_shortfall_bps = float("nan")

        # VWAP slippage
        if not math.isnan(vwap_benchmark) and vwap_benchmark > 0:
            vwap_slippage_bps = (
                side_sign * (execution_price - vwap_benchmark) / vwap_benchmark * 10000
            )
        else:
            vwap_slippage_bps = float("nan")

        # Fee cost (SIGNED - rebates reduce cost)
        fee_per_share = total_fees / total_filled_qty if total_filled_qty > 0 else 0.0
        if arrival_price > 0:
            fee_cost_bps = fee_per_share / arrival_price * 10000
        else:
            fee_cost_bps = 0.0

        # Opportunity cost (unfilled qty weighted by unfilled fraction)
        if unfilled_qty > 0 and actual_close_price is not None and arrival_price > 0:
            unfilled_fraction = unfilled_qty / total_target_qty
            opportunity_cost_bps = (
                side_sign
                * (actual_close_price - arrival_price)
                / arrival_price
                * 10000
                * unfilled_fraction
            )
        else:
            opportunity_cost_bps = 0.0

        # Fill rate for weighting
        fill_rate = total_filled_qty / total_target_qty if total_target_qty > 0 else 0.0

        # Total cost (true IS) - weight filled components by fill_rate
        # price_shortfall and fee_cost apply only to filled portion
        # opportunity_cost is already weighted by unfilled_fraction
        total_cost_bps = (
            (price_shortfall_bps + fee_cost_bps) * fill_rate + opportunity_cost_bps
        )

        # Market impact estimation with timing/permanent decomposition
        market_impact_bps, timing_cost_bps, mid_price_at_arrival = (
            self._estimate_market_impact(
                fill_batch=fill_batch,
                arrival_price=arrival_price,
                execution_price=execution_price,
                spread_stats=spread_stats,
                warnings=warnings,
            )
        )
        total_notional = execution_price * total_filled_qty

        return ExecutionAnalysisResult(
            # Versioning
            dataset_version_id=version_info.composite_version_id,
            dataset_versions=version_info.versions,
            computation_timestamp=datetime.now(UTC),
            as_of_date=as_of,
            # Identification
            symbol=symbol,
            side=side,
            execution_date=execution_date,
            # Core prices
            arrival_price=arrival_price,
            execution_price=execution_price,
            vwap_benchmark=vwap_benchmark,
            twap_benchmark=twap_benchmark,
            mid_price_at_arrival=mid_price_at_arrival,
            # Cost decomposition
            price_shortfall_bps=price_shortfall_bps,
            vwap_slippage_bps=vwap_slippage_bps,
            fee_cost_bps=fee_cost_bps,
            opportunity_cost_bps=opportunity_cost_bps,
            total_cost_bps=total_cost_bps,
            # Impact decomposition
            market_impact_bps=market_impact_bps,
            timing_cost_bps=timing_cost_bps,
            # Fill statistics
            fill_rate=fill_rate,
            total_filled_qty=total_filled_qty,
            unfilled_qty=unfilled_qty,
            total_target_qty=total_target_qty,
            total_notional=total_notional,
            total_fees=total_fees,
            close_price=actual_close_price,
            execution_duration_seconds=execution_duration_seconds,
            num_fills=len(valid_fills),
            # Data quality
            warnings=warnings,
            arrival_source=arrival_source,
            clock_drift_warning=clock_drift_warning,
            fills_before_decision_warning=fills_before_decision_warning,
            side_mismatch_warning=side_mismatch_warning,
            mixed_currency_warning=mixed_currency_warning,
            non_usd_fee_warning=non_usd_fee_warning,
            vwap_coverage_pct=vwap_coverage,
        )

    def compute_vwap(
        self,
        symbol: str,
        start_time: datetime,
        end_time: datetime,
        as_of: date | None = None,
    ) -> float:
        """Compute VWAP over time window from TAQ data.

        Args:
            symbol: Stock symbol.
            start_time: Start of window (inclusive).
            end_time: End of window (inclusive).
            as_of: Optional PIT date.

        Returns:
            VWAP value, or NaN if no data available.
        """
        vwap, _ = self._compute_vwap_with_coverage(symbol, start_time, end_time, as_of)
        return vwap

    def _compute_vwap_with_coverage(
        self,
        symbol: str,
        start_time: datetime,
        end_time: datetime,
        as_of: date | None = None,
    ) -> tuple[float, float]:
        """Compute VWAP with coverage percentage.

        Returns (vwap, coverage_pct) where coverage_pct is % of window with data.
        """
        bars_df = self.taq.fetch_minute_bars(
            symbols=[symbol.upper()],
            start_date=start_time.date(),
            end_date=end_time.date(),
            as_of=as_of,
        )

        if bars_df.is_empty():
            return float("nan"), 0.0

        # Filter to time window
        bars_df = bars_df.filter(
            (pl.col("ts") >= start_time) & (pl.col("ts") <= end_time)
        )

        if bars_df.is_empty():
            return float("nan"), 0.0

        # Filter out zero-volume bars
        valid_bars = bars_df.filter(pl.col("volume") > 0)

        if valid_bars.is_empty():
            return float("nan"), 0.0

        # Prefer bar's vwap field if available (more accurate)
        if "vwap" in valid_bars.columns:
            total_value = (valid_bars["vwap"] * valid_bars["volume"]).sum()
        else:
            # Fallback: use typical price = (high + low + close) / 3
            typical_price = (
                valid_bars["high"] + valid_bars["low"] + valid_bars["close"]
            ) / 3
            total_value = (typical_price * valid_bars["volume"]).sum()

        total_volume = valid_bars["volume"].sum()
        vwap = total_value / total_volume

        # Coverage: valid bars / expected bars in window
        # Calculate expected bars from time window (not returned bars)
        # This handles missing minutes correctly
        window_minutes = (end_time - start_time).total_seconds() / 60
        expected_bars = int(window_minutes) + 1  # +1 for inclusive end
        expected_bars = max(1, expected_bars)  # At least 1 expected
        coverage_pct = min(1.0, valid_bars.height / expected_bars)

        return float(vwap), coverage_pct

    def compute_twap(
        self,
        symbol: str,
        start_time: datetime,
        end_time: datetime,
        as_of: date | None = None,
    ) -> float:
        """Compute TWAP over time window from TAQ data.

        Args:
            symbol: Stock symbol.
            start_time: Start of window (inclusive).
            end_time: End of window (inclusive).
            as_of: Optional PIT date.

        Returns:
            TWAP value (simple average of close prices), or NaN if no data.
        """
        return self._compute_twap(symbol, start_time, end_time, as_of)

    def _compute_twap(
        self,
        symbol: str,
        start_time: datetime,
        end_time: datetime,
        as_of: date | None = None,
    ) -> float:
        """Compute TWAP (simple average of close prices)."""
        bars_df = self.taq.fetch_minute_bars(
            symbols=[symbol.upper()],
            start_date=start_time.date(),
            end_date=end_time.date(),
            as_of=as_of,
        )

        if bars_df.is_empty():
            return float("nan")

        # Filter to time window
        bars_df = bars_df.filter(
            (pl.col("ts") >= start_time) & (pl.col("ts") <= end_time)
        )

        if bars_df.is_empty():
            return float("nan")

        return _series_mean_to_float(bars_df["close"].mean())

    def _get_arrival_price(
        self,
        symbol: str,
        decision_time: datetime,
        submission_time: datetime,
        as_of: date | None,
        warnings: list[str],
    ) -> tuple[float, Literal["decision_time", "submission_time"]]:
        """Get arrival price at decision_time or submission_time.

        Primary: decision_time → use TAQ bar close at that minute
        Fallback: submission_time → with warning documented
        """
        # Try decision_time first
        bars_df = self.taq.fetch_minute_bars(
            symbols=[symbol.upper()],
            start_date=decision_time.date(),
            end_date=decision_time.date(),
            as_of=as_of,
        )

        if not bars_df.is_empty():
            # Get bar closest to decision_time
            bars_df = bars_df.filter(pl.col("ts") <= decision_time).sort("ts", descending=True)
            if not bars_df.is_empty():
                return float(bars_df["close"][0]), "decision_time"

        # Fallback to submission_time
        warnings.append("Using submission_time for arrival price (decision_time bar unavailable)")
        bars_df = self.taq.fetch_minute_bars(
            symbols=[symbol.upper()],
            start_date=submission_time.date(),
            end_date=submission_time.date(),
            as_of=as_of,
        )

        if not bars_df.is_empty():
            bars_df = bars_df.filter(pl.col("ts") <= submission_time).sort("ts", descending=True)
            if not bars_df.is_empty():
                return float(bars_df["close"][0]), "submission_time"

        # Last resort: return NaN
        warnings.append("No TAQ data for arrival price")
        return float("nan"), "decision_time"

    def _get_close_price(
        self,
        symbol: str,
        execution_date: date,
        as_of: date | None,
    ) -> float | None:
        """Get close price for opportunity cost calculation."""
        bars_df = self.taq.fetch_minute_bars(
            symbols=[symbol.upper()],
            start_date=execution_date,
            end_date=execution_date,
            as_of=as_of,
        )

        if bars_df.is_empty():
            return None

        # Get last bar of the day
        bars_df = bars_df.sort("ts", descending=True)
        return float(bars_df["close"][0])

    def _estimate_market_impact(
        self,
        fill_batch: FillBatch,
        arrival_price: float,
        execution_price: float,
        spread_stats: SpreadDepthResult | None,
        warnings: list[str],
    ) -> tuple[float, float, float | None]:
        """Estimate market impact with timing/permanent decomposition.

        Returns: (permanent_impact_bps, timing_cost_bps, mid_price_at_arrival)

        Decomposition:
        - total_impact = price_shortfall_bps (exec vs arrival)
        - timing_cost = expected cost from crossing spread (half-spread)
        - permanent_impact = total_impact - timing_cost

        When spread data is available:
        - half_spread_bps = (spread_width / 2) / arrival * 10000
        - This represents the expected cost of crossing the bid-ask spread
        - Permanent impact captures market movement beyond spread crossing

        Without spread data:
        - Cannot decompose: permanent_impact = total_impact, timing_cost = 0
        """
        side_sign = 1 if fill_batch.side == "buy" else -1

        # Total impact = price shortfall
        total_impact_bps: float
        if arrival_price > 0:
            total_impact_bps = (
                side_sign * (execution_price - arrival_price) / arrival_price * 10000
            )
        else:
            return float("nan"), float("nan"), None

        mid_price: float = arrival_price  # Arrival price is our best mid proxy

        # Derive timing cost from spread data
        timing_cost_bps: float = 0.0
        if spread_stats is not None and not spread_stats.depth_is_estimated:
            # qwap_spread is spread WIDTH in price units (ask - bid)
            spread_width = spread_stats.qwap_spread
            spread_pct = spread_width / arrival_price if arrival_price > 0 else 0

            if spread_pct > 0.05:  # >5% spread is suspicious
                warnings.append(f"Unusually wide spread ({spread_pct:.1%})")

            # Half-spread = expected cost of crossing the spread
            # This is timing cost (cost of immediacy)
            half_spread = spread_width / 2
            timing_cost_bps = half_spread / arrival_price * 10000
        else:
            if spread_stats is None:
                warnings.append(
                    "No spread data - cannot decompose timing/permanent impact"
                )

        # Permanent impact = total impact minus timing cost
        # If we paid more than half-spread, it's market movement
        permanent_impact_bps = total_impact_bps - timing_cost_bps

        return permanent_impact_bps, timing_cost_bps, mid_price

    def estimate_market_impact(
        self,
        fill_batch: FillBatch,
        arrival_price: float | None = None,
        spread_stats: SpreadDepthResult | None = None,
        as_of: date | None = None,
    ) -> float:
        """Estimate permanent market impact using spread data.

        Args:
            fill_batch: Batch of fills to analyze.
            arrival_price: Price at decision time. If None, derived from TAQ.
            spread_stats: Optional spread statistics from T3.1.
            as_of: Point-in-time date for TAQ lookup.

        Returns:
            Market impact in basis points.

        Note:
            For full cost decomposition, use analyze_execution() instead.
            This method provides a simplified impact estimate.
        """
        valid_fills = fill_batch.valid_fills
        if not valid_fills:
            return float("nan")

        execution_price = fill_batch.avg_fill_price

        # Derive arrival price from TAQ if not provided
        if arrival_price is None:
            warnings_list: list[str] = []
            arrival_price, _ = self._get_arrival_price(
                symbol=fill_batch.symbol,
                decision_time=fill_batch.decision_time,
                submission_time=fill_batch.submission_time,
                as_of=as_of,
                warnings=warnings_list,
            )
            if math.isnan(arrival_price):
                return float("nan")

        warnings: list[str] = []
        impact_bps, _, _ = self._estimate_market_impact(
            fill_batch=fill_batch,
            arrival_price=arrival_price,
            execution_price=execution_price,
            spread_stats=spread_stats,
            warnings=warnings,
        )
        return impact_bps

    def recommend_execution_window(
        self,
        symbol: str,
        target_date: date,
        order_size_shares: int,
        as_of: date | None = None,
    ) -> ExecutionWindowRecommendation:
        """Recommend optimal execution timing based on liquidity patterns.

        Uses historical volume patterns to recommend execution window
        that minimizes market impact.

        Args:
            symbol: Stock symbol.
            target_date: Date for execution.
            order_size_shares: Size of order in shares.
            as_of: Optional PIT date.

        Returns:
            ExecutionWindowRecommendation with optimal timing.
        """
        warnings: list[str] = []
        symbol = symbol.upper()

        # Fetch minute bars for historical volume analysis
        bars_df = self.taq.fetch_minute_bars(
            symbols=[symbol],
            start_date=target_date,
            end_date=target_date,
            as_of=as_of,
        )

        if bars_df.is_empty():
            warnings.append("No historical data - using default window")
            # Default: first 2 hours of trading
            recommended_start = datetime(
                target_date.year, target_date.month, target_date.day, 9, 30, tzinfo=UTC
            )
            recommended_end = datetime(
                target_date.year, target_date.month, target_date.day, 11, 30, tzinfo=UTC
            )
            return ExecutionWindowRecommendation(
                symbol=symbol,
                target_date=target_date,
                order_size_shares=order_size_shares,
                recommended_start_time=recommended_start,
                recommended_end_time=recommended_end,
                expected_participation_rate=0.0,
                avg_spread_bps=float("nan"),
                liquidity_score=0.5,
                warnings=warnings,
            )

        # Calculate daily volume
        total_volume = bars_df["volume"].sum()
        if total_volume == 0:
            warnings.append("Zero daily volume")
            total_volume = 1  # Avoid division by zero

        participation_rate = order_size_shares / total_volume

        if participation_rate > 0.10:
            warnings.append(
                f"High participation rate ({participation_rate:.1%}) - consider TWAP execution"
            )

        # Find period with highest liquidity (highest volume concentration)
        bars_df = bars_df.with_columns([
            pl.col("ts").dt.hour().alias("hour"),
        ])

        hourly_volume = bars_df.group_by("hour").agg([
            pl.col("volume").sum().alias("total_volume"),
        ]).sort("total_volume", descending=True)

        # Recommend window around highest volume hour
        if not hourly_volume.is_empty():
            best_hour = int(hourly_volume["hour"][0])
            recommended_start = datetime(
                target_date.year, target_date.month, target_date.day, best_hour, 0, tzinfo=UTC
            )
            recommended_end = datetime(
                target_date.year, target_date.month, target_date.day, best_hour + 1, 0, tzinfo=UTC
            )
        else:
            recommended_start = datetime(
                target_date.year, target_date.month, target_date.day, 9, 30, tzinfo=UTC
            )
            recommended_end = datetime(
                target_date.year, target_date.month, target_date.day, 10, 30, tzinfo=UTC
            )

        # Get spread data if available
        avg_spread_bps = float("nan")
        if self.micro is not None:
            try:
                spread_stats = self.micro.compute_spread_depth_stats(
                    symbol=symbol,
                    target_date=target_date,
                    as_of=as_of,
                )
                # Convert qwap_spread to bps using average price
                avg_price_f = _series_mean_to_float(bars_df["close"].mean())
                if not math.isnan(avg_price_f) and avg_price_f > 0:
                    avg_spread_bps = spread_stats.qwap_spread / avg_price_f * 10000
            except Exception:
                pass

        # Compute liquidity score (simple heuristic)
        liquidity_score = min(1.0, max(0.0, 1.0 - participation_rate * 5))

        return ExecutionWindowRecommendation(
            symbol=symbol,
            target_date=target_date,
            order_size_shares=order_size_shares,
            recommended_start_time=recommended_start,
            recommended_end_time=recommended_end,
            expected_participation_rate=participation_rate,
            avg_spread_bps=avg_spread_bps,
            liquidity_score=liquidity_score,
            warnings=warnings,
        )
