"""TCA (Transaction Cost Analysis) routes for Execution Gateway (P6T8).

Provides endpoints for execution quality analysis:
- GET /api/v1/tca/analysis - TCA metrics summary for date range
- GET /api/v1/tca/analysis/{client_order_id} - TCA metrics for specific order
- GET /api/v1/tca/benchmarks - Benchmark comparison time series

Security:
- Requires VIEW_TCA permission
- Strategy-scoped access control

Design Pattern:
    - Router defined at module level
    - Dependencies injected via Depends()
    - Uses ExecutionQualityAnalyzer from libs/platform/analytics

Data Flow:
    1. Query trades from database (strategy-scoped)
    2. Group trades by client_order_id into FillBatch objects
    3. If TAQ data available: use ExecutionQualityAnalyzer for benchmarks
    4. Otherwise: compute simplified metrics from fill data only
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Query, status

from apps.execution_gateway.api.dependencies import build_gateway_authenticator
from apps.execution_gateway.app_context import AppContext
from apps.execution_gateway.dependencies import get_context
from apps.execution_gateway.schemas import (
    TCAAnalysisSummary,
    TCABenchmarkPoint,
    TCABenchmarkResponse,
    TCAOrderDetail,
    TCASummaryResponse,
)
from apps.execution_gateway.services.auth_helpers import build_user_context
from libs.core.common.api_auth_dependency import APIAuthConfig, AuthContext, api_auth
from libs.platform.analytics.execution_quality import (
    ExecutionAnalysisResult,
    ExecutionQualityAnalyzer,
    Fill,
    FillBatch,
)
from libs.platform.web_console_auth.permissions import Permission, get_authorized_strategies

logger = logging.getLogger(__name__)


# =============================================================================
# Configuration Constants
# =============================================================================

# TAQ data path - configurable via environment
TAQ_DATA_PATH = Path(os.getenv("TAQ_DATA_PATH", "data/taq"))

# Fallback opportunity cost estimate when market data is unavailable (bps per unfilled fraction)
# Real opportunity cost requires price movement data during unfilled period
FALLBACK_OPPORTUNITY_COST_BPS = 10.0

# Maximum lookback period for single-order queries (days)
# Orders older than this will return 404 - use date-range endpoint for historical analysis
ORDER_LOOKBACK_DAYS = 365


def _try_create_taq_provider() -> Any | None:
    """Try to create TAQLocalProvider if data is available.

    Returns None if TAQ data directory doesn't exist or required
    manifests are missing.
    """
    if not TAQ_DATA_PATH.exists():
        logger.debug("TAQ data path not found", extra={"path": str(TAQ_DATA_PATH)})
        return None

    try:
        from libs.data.data_providers.taq_query_provider import TAQLocalProvider
        from libs.data.data_quality.manifest import ManifestManager

        manifest_path = TAQ_DATA_PATH / "manifests"
        if not manifest_path.exists():
            logger.debug("TAQ manifests not found", extra={"path": str(manifest_path)})
            return None

        manifest_manager = ManifestManager(storage_path=manifest_path)
        provider = TAQLocalProvider(
            storage_path=TAQ_DATA_PATH,
            manifest_manager=manifest_manager,
            engine="polars",
        )
        logger.info("TAQ provider initialized for TCA analysis")
        return provider
    except Exception as e:
        logger.warning(
            "Failed to create TAQ provider",
            extra={"error": str(e), "error_type": type(e).__name__},
        )
        return None


# Lazy-loaded TAQ provider (singleton with retry on failure)
_taq_provider: Any | None = None
_taq_provider_initialized = False
_taq_provider_last_attempt: float = 0.0
_TAQ_RETRY_INTERVAL_SECONDS = 300.0  # Retry every 5 minutes after failure

# Lock for thread-safe TAQ provider initialization
_taq_provider_lock = threading.Lock()


def _get_taq_provider() -> Any | None:
    """Get or create the TAQ provider singleton.

    If initialization fails, retries after TAQ_RETRY_INTERVAL_SECONDS to handle
    transient failures (e.g., temporary mount unavailable at startup).

    Thread-safe: uses a lock to prevent concurrent initialization attempts.
    """
    global _taq_provider, _taq_provider_initialized, _taq_provider_last_attempt

    # Fast path: already initialized successfully
    if _taq_provider_initialized:
        return _taq_provider

    with _taq_provider_lock:
        # Double-check after acquiring lock
        if _taq_provider_initialized:
            return _taq_provider

        # Check if we should retry after a previous failure
        now = time.time()
        if _taq_provider_last_attempt > 0 and (now - _taq_provider_last_attempt) < _TAQ_RETRY_INTERVAL_SECONDS:
            return None  # Too soon to retry

        _taq_provider_last_attempt = now
        _taq_provider = _try_create_taq_provider()

        # Only mark as initialized on success (allows retry on failure)
        if _taq_provider is not None:
            _taq_provider_initialized = True

        return _taq_provider


router = APIRouter(prefix="/api/v1/tca", tags=["TCA"])

# TCA auth dependency - requires VIEW_TCA permission
tca_auth = api_auth(
    APIAuthConfig(
        action="view_tca",
        require_role=None,
        require_permission=Permission.VIEW_TCA,
    ),
    authenticator_getter=build_gateway_authenticator,
)


# =============================================================================
# Helper Functions
# =============================================================================


def _parse_datetime(value: Any, field_name: str) -> datetime | None:
    """Parse a datetime value from DB, handling string and datetime types.

    Always returns timezone-aware datetime (UTC if no timezone specified).
    Returns None if value is missing/invalid, allowing caller to skip the record.
    """
    if value is None:
        return None

    dt: datetime | None = None

    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        try:
            # Try ISO format first (most common)
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            logger.debug(
                "Invalid datetime string",
                extra={"field": field_name, "value": value[:50] if len(value) > 50 else value},
            )
            return None
    else:
        # Unexpected type
        logger.debug(
            "Unexpected datetime type",
            extra={"field": field_name, "type": type(value).__name__},
        )
        return None

    # Normalize to UTC: add UTC if naive, or convert if tz-aware
    # This ensures consistent execution_date across timezones
    if dt is not None:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        else:
            dt = dt.astimezone(UTC)

    return dt


def _normalize_side(value: Any) -> Literal["buy", "sell"] | None:
    """Normalize trade side to lowercase, handling bytes/enum/string types.

    Returns None if value is missing, empty, or invalid (not 'buy' or 'sell').
    This prevents inverted cost signs from defaulting to a side.
    """
    if not value:
        return None

    # Handle enum types first (extract .value)
    if hasattr(value, "value"):
        value = value.value

    # Handle bytes (including enum.value bytes)
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8")
        except (UnicodeDecodeError, AttributeError):
            return None  # Invalid encoding

    # Convert to lowercase string
    side_str = str(value).strip().lower()

    # Validate
    if side_str not in ("buy", "sell"):
        return None

    return cast(Literal["buy", "sell"], side_str)


# =============================================================================
# Real Data Processing Functions
# =============================================================================


def _group_trades_by_order(
    trades: list[dict[str, Any]],
) -> tuple[dict[str, list[dict[str, Any]]], int]:
    """Group trades by client_order_id for FillBatch construction.

    Returns:
        Tuple of (grouped trades dict, count of trades missing client_order_id)
    """
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    missing_order_id_count = 0
    for trade in trades:
        client_order_id = trade.get("client_order_id")
        if client_order_id:
            grouped[client_order_id].append(trade)
        else:
            missing_order_id_count += 1
    return grouped, missing_order_id_count


def _build_fill_batch(
    client_order_id: str,
    trades: list[dict[str, Any]],
) -> FillBatch | None:
    """Build a FillBatch from database trades for an order.

    Returns None if trades are invalid for TCA analysis.
    """
    if not trades:
        return None

    # Find first valid symbol across all trades (not just first)
    # This ensures we don't drop valid orders when first trade has missing/dirty data
    symbol: str | None = None
    for trade in trades:
        trade_symbol = trade.get("symbol", "")
        if trade_symbol and isinstance(trade_symbol, str) and trade_symbol.strip():
            symbol = trade_symbol.strip().upper()
            break
    if not symbol:
        return None  # No valid symbol found in any trade

    # Find first valid side across all trades (not just first)
    # IMPORTANT: Do NOT default to "buy" - missing/empty side is a data error that can
    # invert cost signs and distort metrics. Reject instead.
    side: Literal["buy", "sell"] | None = None
    for trade in trades:
        normalized_side = _normalize_side(trade.get("side"))
        if normalized_side is not None:
            side = normalized_side
            break
    if side is None:
        return None  # No valid side found in any trade

    # Parse order_submitted_at from first trade (timestamp metadata)
    first_trade = trades[0]
    order_submitted_at = _parse_datetime(first_trade.get("order_submitted_at"), "order_submitted_at")

    # Find first valid numeric order_qty across all trades (not just first)
    # This ensures we don't skip consistency checks when first trade is missing order_qty
    baseline_order_qty: int | None = None
    for trade in trades:
        raw_order_qty = trade.get("order_qty")
        if raw_order_qty is not None and raw_order_qty != 0:
            try:
                qty_val = int(float(raw_order_qty))
                if qty_val > 0:
                    baseline_order_qty = qty_val
                    break
            except (ValueError, TypeError):
                continue

    # Validate symbol/side/order_qty consistency across all trades in the order
    # Different symbols/sides/order_qty under one order indicates data corruption or bad join
    for trade in trades:
        trade_symbol = trade.get("symbol", "")
        if trade_symbol and trade_symbol.upper() != symbol.upper():
            logger.warning(
                "Order has inconsistent symbols across trades",
                extra={
                    "client_order_id": client_order_id,
                    "expected_symbol": symbol,
                    "found_symbol": trade_symbol,
                },
            )
            return None

        trade_side_normalized = _normalize_side(trade.get("side"))
        if trade_side_normalized is not None and trade_side_normalized != side:
            logger.warning(
                "Order has inconsistent sides across trades",
                extra={
                    "client_order_id": client_order_id,
                    "expected_side": side,
                    "found_side": trade_side_normalized,
                },
            )
            return None

        # Validate order_qty consistency (should be same for all trades in an order)
        # Compare against baseline_order_qty (first valid numeric value found above)
        trade_order_qty = trade.get("order_qty")
        if trade_order_qty is not None and baseline_order_qty is not None:
            try:
                trade_order_qty_int = int(float(trade_order_qty))
                if trade_order_qty_int > 0 and trade_order_qty_int != baseline_order_qty:
                    logger.warning(
                        "Order has inconsistent order_qty across trades",
                        extra={
                            "client_order_id": client_order_id,
                            "expected_order_qty": baseline_order_qty,
                            "found_order_qty": trade_order_qty_int,
                        },
                    )
                    return None
            except (ValueError, TypeError):
                pass  # Non-numeric, will be handled later

    # Convert trades to Fill objects
    fills: list[Fill] = []
    for trade in trades:
        # Parse executed_at with type handling for string timestamps
        executed_at = _parse_datetime(trade.get("executed_at"), "executed_at")
        if executed_at is None:
            continue

        # Skip trades with invalid qty (Fill model requires quantity > 0)
        # Handle float strings like "100.0" from DB drivers, but reject fractional shares
        qty = trade.get("qty", 0)
        try:
            qty_float = float(qty) if qty else 0.0
            qty_int = int(qty_float)
            # Reject fractional quantities - truncation would distort metrics
            if qty_float != qty_int:
                logger.debug(
                    "Skipping trade with fractional quantity",
                    extra={
                        "client_order_id": client_order_id,
                        "trade_id": trade.get("trade_id"),
                        "qty": qty,
                    },
                )
                continue
        except (ValueError, TypeError):
            continue  # Skip trades with non-numeric qty
        if qty_int <= 0:
            continue

        # Skip trades with invalid price (Fill model requires price > 0)
        price = trade.get("price", 0)
        try:
            price_float = float(price) if price else 0.0
        except (ValueError, TypeError):
            continue  # Skip trades with non-numeric price
        if price_float <= 0:
            continue

        # Extract fee from order metadata if available
        fee_amount = 0.0
        order_metadata = trade.get("order_metadata")
        if order_metadata and isinstance(order_metadata, dict):
            fills_meta = order_metadata.get("fills", [])
            for fm in fills_meta:
                if fm.get("fill_id") == trade.get("trade_id"):
                    try:
                        fee_amount = float(fm.get("fee", 0) or 0)
                    except (ValueError, TypeError):
                        fee_amount = 0.0  # Default to 0 for non-numeric fee
                    break

        fills.append(
            Fill(
                fill_id=trade.get("trade_id", ""),
                order_id=trade.get("broker_order_id") or client_order_id,
                client_order_id=client_order_id,
                timestamp=executed_at,  # Already timezone-aware from _parse_datetime
                symbol=symbol.upper(),
                side=side,
                price=price_float,
                quantity=qty_int,
                fee_amount=fee_amount,
            )
        )

    if not fills:
        return None

    # If order_submitted_at is missing, use earliest VALID fill timestamp as fallback
    # This ensures decision_time aligns with the fills actually being analyzed
    # (previously we computed fallback from all trades, including invalid ones)
    if order_submitted_at is None:
        earliest_fill_timestamp = min(f.timestamp for f in fills)
        logger.warning(
            "order_submitted_at missing, using earliest fill timestamp as fallback",
            extra={
                "client_order_id": client_order_id,
                "fallback_timestamp": earliest_fill_timestamp.isoformat(),
            },
        )
        order_submitted_at = earliest_fill_timestamp

    # Use order submission time as decision time (simplification)
    # In production, decision_time should come from signal generation timestamp
    # order_submitted_at is already timezone-aware from _parse_datetime
    decision_time = order_submitted_at
    submission_time = decision_time  # Same for now

    # Calculate target qty - use baseline_order_qty if found, else sum of fills
    # baseline_order_qty is already validated as the first valid numeric order_qty
    total_target_qty: int
    if baseline_order_qty is not None and baseline_order_qty > 0:
        total_target_qty = baseline_order_qty
    else:
        total_target_qty = sum(f.quantity for f in fills)

    # Validate total_target_qty > 0 (FillBatch requires gt=0)
    if total_target_qty <= 0:
        return None

    return FillBatch(
        symbol=symbol.upper(),
        side=side,
        fills=fills,
        decision_time=decision_time,
        submission_time=submission_time,
        total_target_qty=total_target_qty,
    )


def _analyze_order_with_taq(
    fill_batch: FillBatch,
    analyzer: ExecutionQualityAnalyzer,
) -> ExecutionAnalysisResult | None:
    """Analyze order using TAQ data for benchmarks.

    Args:
        fill_batch: The fill batch to analyze.
        analyzer: Pre-created analyzer instance (reused per request for efficiency).

    Returns None if analysis fails.
    """
    try:
        return analyzer.analyze_execution(fill_batch)
    except Exception as e:
        # Extract client_order_id from fills for debugging
        client_order_id = fill_batch.fills[0].client_order_id if fill_batch.fills else "unknown"
        logger.debug(
            "TAQ analysis failed",
            extra={
                "client_order_id": client_order_id,
                "symbol": fill_batch.symbol,
                "error": str(e),
                "error_type": type(e).__name__,
            },
        )
        return None


def _compute_simple_tca(fill_batch: FillBatch) -> TCAOrderDetail | None:
    """Compute simplified TCA metrics without TAQ data.

    Uses fill data only:
    - arrival_price = first fill price
    - VWAP/TWAP = volume-weighted average of fills
    - No market impact decomposition (set to 0)

    Returns None if no valid fills to analyze.
    """
    # Use valid_fills only - do NOT fall back to raw fills as they may contain invalid data
    # (e.g., zero/negative prices or quantities that can distort metrics)
    effective_fills = fill_batch.valid_fills
    if not effective_fills:
        return None

    # Compute metrics from the SAME effective fill list (not fill_batch properties)
    first_fill = min(effective_fills, key=lambda f: f.timestamp)
    last_fill = max(effective_fills, key=lambda f: f.timestamp)

    # Compute execution metrics from effective fills directly
    total_filled_qty = sum(f.quantity for f in effective_fills)
    total_notional = sum(f.price * f.quantity for f in effective_fills)
    total_fees = sum(f.fee_amount for f in effective_fills)

    if total_filled_qty == 0:
        return None

    execution_price = total_notional / total_filled_qty
    arrival_price = first_fill.price
    total_target_qty = fill_batch.total_target_qty
    execution_date = first_fill.timestamp.date()
    execution_duration = (last_fill.timestamp - first_fill.timestamp).total_seconds()

    # Compute metrics
    # Clamp fill_rate to [0, 1] - overfill (filled > target) treated as 100% filled
    raw_fill_rate = total_filled_qty / total_target_qty if total_target_qty > 0 else 1.0
    fill_rate = min(1.0, max(0.0, raw_fill_rate))
    side_sign = 1 if fill_batch.side == "buy" else -1

    # Price shortfall
    price_shortfall_bps = 0.0
    if arrival_price > 0:
        price_shortfall_bps = side_sign * (execution_price - arrival_price) / arrival_price * 10000

    # VWAP slippage (using arrival as benchmark since no market VWAP)
    # Note: vwap_benchmark is set to arrival_price below to match this calculation
    vwap_slippage_bps = price_shortfall_bps  # Same without market data

    # Fee cost
    fee_per_share = total_fees / total_filled_qty if total_filled_qty > 0 else 0.0
    fee_cost_bps = fee_per_share / arrival_price * 10000 if arrival_price > 0 else 0.0

    # Opportunity cost (simplified - proportional to unfilled)
    # Uses module-level FALLBACK_OPPORTUNITY_COST_BPS constant
    # Clamp to >= 0 to handle overfill case (fill_rate > 1 before clamping)
    unfilled_fraction = max(0.0, 1 - fill_rate)
    opportunity_cost_bps = unfilled_fraction * FALLBACK_OPPORTUNITY_COST_BPS

    # Total IS - weight filled components by fill_rate (matching ExecutionQualityAnalyzer)
    total_cost_bps = (price_shortfall_bps + fee_cost_bps) * fill_rate + opportunity_cost_bps

    # Build warnings list
    warnings = ["Simplified TCA - no market benchmark data available"]
    if raw_fill_rate > 1.0:
        warnings.append(f"Overfill detected: filled {total_filled_qty} vs target {total_target_qty}")

    return TCAOrderDetail(
        client_order_id=fill_batch.fills[0].client_order_id if fill_batch.fills else "",
        symbol=fill_batch.symbol,
        side=fill_batch.side,
        strategy_id="",  # Filled by caller
        execution_date=execution_date,
        arrival_price=round(arrival_price, 4),
        execution_price=round(execution_price, 4),
        vwap_benchmark=round(arrival_price, 4),  # No market VWAP - using arrival price as proxy
        twap_benchmark=round(arrival_price, 4),  # No market TWAP - using arrival price as proxy
        target_qty=total_target_qty,
        filled_qty=total_filled_qty,
        fill_rate=round(fill_rate, 4),
        total_notional=round(execution_price * total_filled_qty, 2),
        implementation_shortfall_bps=round(total_cost_bps, 2),
        price_shortfall_bps=round(price_shortfall_bps, 2),
        vwap_slippage_bps=round(vwap_slippage_bps, 2),
        fee_cost_bps=round(fee_cost_bps, 2),
        opportunity_cost_bps=round(opportunity_cost_bps, 2),
        market_impact_bps=0.0,  # Cannot compute without TAQ
        timing_cost_bps=0.0,  # Cannot compute without TAQ
        num_fills=len(effective_fills),
        execution_duration_seconds=execution_duration,
        total_fees=total_fees,
        warnings=warnings,
        vwap_coverage_pct=0.0,
    )


def _result_to_order_detail(
    result: ExecutionAnalysisResult,
    client_order_id: str,
    strategy_id: str,
) -> TCAOrderDetail:
    """Convert ExecutionAnalysisResult to TCAOrderDetail."""
    return TCAOrderDetail(
        client_order_id=client_order_id,
        symbol=result.symbol,
        side=result.side,
        strategy_id=strategy_id,
        execution_date=result.execution_date,
        arrival_price=round(result.arrival_price, 4),
        execution_price=round(result.execution_price, 4),
        vwap_benchmark=round(result.vwap_benchmark, 4),
        twap_benchmark=round(result.twap_benchmark, 4),
        target_qty=result.total_target_qty,
        filled_qty=result.total_filled_qty,
        fill_rate=round(result.fill_rate, 4),
        total_notional=round(result.total_notional, 2),
        implementation_shortfall_bps=round(result.total_cost_bps, 2),
        price_shortfall_bps=round(result.price_shortfall_bps, 2),
        vwap_slippage_bps=round(result.vwap_slippage_bps, 2),
        fee_cost_bps=round(result.fee_cost_bps, 2),
        opportunity_cost_bps=round(result.opportunity_cost_bps, 2),
        market_impact_bps=round(result.market_impact_bps, 2),
        timing_cost_bps=round(result.timing_cost_bps, 2),
        num_fills=result.num_fills,
        execution_duration_seconds=result.execution_duration_seconds,
        total_fees=result.total_fees,
        warnings=result.warnings,
        vwap_coverage_pct=result.vwap_coverage_pct * 100,  # Convert to percentage
    )


def _analyze_trades_for_tca(
    trades: list[dict[str, Any]],
    strategy_ids: list[str],
) -> tuple[list[TCAOrderDetail], list[str]]:
    """Analyze trades and return TCA order details.

    Returns (orders, warnings) tuple.
    """
    if not trades:
        return [], ["No trades found for the specified criteria"]

    # Group trades by order
    grouped, missing_order_id_count = _group_trades_by_order(trades)
    warnings: list[str] = []
    orders: list[TCAOrderDetail] = []

    # Warn if trades are missing client_order_id (data quality issue)
    if missing_order_id_count > 0:
        logger.warning(
            "Trades missing client_order_id dropped from TCA",
            extra={"count": missing_order_id_count},
        )
        warnings.append(f"{missing_order_id_count} trade(s) missing client_order_id were excluded")

    # Track skipped orders for visibility
    skipped_invalid_data = 0
    skipped_inconsistent_strategy = 0
    skipped_unauthorized = 0
    skipped_no_valid_fills = 0

    # Get TAQ provider for market benchmarks
    taq_provider = _get_taq_provider()
    analyzer: ExecutionQualityAnalyzer | None = None
    if taq_provider is None:
        warnings.append("Market benchmark data unavailable - using simplified TCA")
    else:
        # Create analyzer once per request for efficiency (reused across all orders)
        analyzer = ExecutionQualityAnalyzer(taq_provider=taq_provider)

    for client_order_id, order_trades in grouped.items():
        # Build FillBatch
        fill_batch = _build_fill_batch(client_order_id, order_trades)
        if fill_batch is None:
            skipped_invalid_data += 1
            logger.debug(
                "Skipping order with invalid data",
                extra={"client_order_id": client_order_id},
            )
            continue

        # Validate strategy ID consistency across all trades in the order
        strategy_ids_in_order = {t.get("strategy_id") for t in order_trades}
        if len(strategy_ids_in_order) > 1:
            # Log warning for inconsistent strategy IDs (data corruption or bad join)
            skipped_inconsistent_strategy += 1
            logger.warning(
                "Skipping order with inconsistent strategy IDs",
                extra={
                    "client_order_id": client_order_id,
                    "strategy_ids": list(strategy_ids_in_order),
                },
            )
            continue

        # Get strategy from first trade (validated as consistent above)
        strategy_id = order_trades[0].get("strategy_id", "unknown")
        if strategy_id not in strategy_ids:
            skipped_unauthorized += 1
            continue  # Skip unauthorized strategies

        # Try TAQ analysis first, fall back to simple TCA
        order_detail: TCAOrderDetail | None = None
        if analyzer is not None:
            result = _analyze_order_with_taq(fill_batch, analyzer)
            if result is not None:
                order_detail = _result_to_order_detail(result, client_order_id, strategy_id)

        if order_detail is None:
            order_detail = _compute_simple_tca(fill_batch)
            if order_detail is None:
                # Skip orders with invalid/empty fills
                skipped_no_valid_fills += 1
                logger.debug(
                    "Skipping order with no valid fills",
                    extra={"client_order_id": client_order_id},
                )
                continue
            order_detail.strategy_id = strategy_id

        orders.append(order_detail)

    # Add skip counters to warnings for visibility
    total_skipped = skipped_invalid_data + skipped_inconsistent_strategy + skipped_unauthorized + skipped_no_valid_fills
    if total_skipped > 0:
        skip_details: list[str] = []
        if skipped_invalid_data > 0:
            skip_details.append(f"invalid data: {skipped_invalid_data}")
        if skipped_inconsistent_strategy > 0:
            skip_details.append(f"inconsistent strategy: {skipped_inconsistent_strategy}")
        if skipped_unauthorized > 0:
            skip_details.append(f"unauthorized: {skipped_unauthorized}")
        if skipped_no_valid_fills > 0:
            skip_details.append(f"no valid fills: {skipped_no_valid_fills}")
        warnings.append(f"Skipped {total_skipped} order(s): {', '.join(skip_details)}")

    return orders, warnings


# =============================================================================
# Routes
# =============================================================================


@router.get(
    "/analysis",
    response_model=TCASummaryResponse,
    summary="Get TCA analysis summary",
    description="Returns aggregated TCA metrics for orders in the specified date range.",
)
def get_tca_analysis(
    start_date: date = Query(..., description="Start date (inclusive)"),
    end_date: date = Query(..., description="End date (inclusive)"),
    symbol: str | None = Query(default=None, description="Filter by symbol"),
    strategy_id: str | None = Query(default=None, description="Filter by strategy"),
    side: Literal["buy", "sell"] | None = Query(default=None, description="Filter by side"),
    ctx: AppContext = Depends(get_context),
    user: dict[str, Any] = Depends(build_user_context),
    _auth: AuthContext = Depends(tca_auth),
) -> TCASummaryResponse:
    """Get TCA analysis summary for date range.

    Returns aggregated metrics including:
    - Implementation shortfall
    - VWAP/TWAP slippage
    - Fee costs
    - Opportunity costs
    - Market impact decomposition
    """
    # Validate date range
    if end_date < start_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="end_date must be >= start_date",
        )

    max_days = 90
    if (end_date - start_date).days > max_days:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Date range cannot exceed {max_days} days",
        )

    # Validate strategy access
    user_obj = user.get("user")
    user_id = user.get("user_id", "unknown")
    authorized = get_authorized_strategies(user_obj)

    # Block users with no authorized strategies
    if not authorized:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No authorized strategies - contact administrator",
        )

    if strategy_id and strategy_id not in authorized:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Not authorized for strategy: {strategy_id}",
        )

    logger.info(
        "TCA analysis requested",
        extra={
            "user_id": user_id,
            "start_date": str(start_date),
            "end_date": str(end_date),
            "symbol": symbol,
            "strategy_id": strategy_id,
        },
    )

    # When no strategy_id specified, filter to user's authorized strategies
    effective_strategies = [strategy_id] if strategy_id else list(authorized)

    # Fetch trades from database with higher limit for analysis
    # Request limit+1 to detect if there are more trades (avoids false positives)
    # Use 999 so limit+1=1000 stays within DB layer's cap of 1000
    query_limit = 999
    trades = ctx.db.get_trades_for_tca(
        start_date=start_date,
        end_date=end_date,
        strategy_ids=effective_strategies,
        symbol=symbol,
        side=side,
        limit=query_limit + 1,  # Fetch one extra to detect truncation
    )

    # Handle truncation: if we got more than limit, there's more data
    # Only consider truncated if we actually got more than the limit
    # NOTE: Relies on DB ordering (ORDER BY executed_at ASC in get_trades_for_tca).
    # If multiple trades have same timestamp, their order is stable within a single
    # query but may vary across queries. The tail-discard heuristic accommodates this
    # by discarding enough orders (10%+) to cover interleaved edge cases.
    truncated = len(trades) > query_limit
    discarded_order_count = 0
    if truncated:
        # Trim to limit and discard potentially incomplete tail orders
        trades = trades[:query_limit]
        # Find all order IDs in the last 10% of trades (at least 10 trades)
        # These orders might be incomplete due to interleaving
        tail_size = max(10, len(trades) // 10)
        tail_trades = trades[-tail_size:]
        incomplete_order_ids = {t.get("client_order_id") for t in tail_trades if t.get("client_order_id")}
        discarded_order_count = len(incomplete_order_ids)

        # Remove all trades for potentially incomplete orders
        trades = [t for t in trades if t.get("client_order_id") not in incomplete_order_ids]

    # Analyze trades for TCA
    orders, warnings = _analyze_trades_for_tca(trades, effective_strategies)

    # Warn if results were truncated
    if truncated:
        warnings.append(
            f"Results truncated (limit: {query_limit} trades, {discarded_order_count} potentially incomplete "
            f"order(s) discarded). Consider narrowing date range or applying filters."
        )

    # Build summary from orders
    summary = TCAAnalysisSummary(
        start_date=start_date,
        end_date=end_date,
        computation_timestamp=datetime.now(UTC),
        total_orders=len(orders),
        total_fills=sum(o.num_fills for o in orders),
        total_notional=sum(o.total_notional for o in orders),
        total_shares=sum(o.filled_qty for o in orders),
        avg_fill_rate=sum(o.fill_rate for o in orders) / len(orders) if orders else 0,
        avg_implementation_shortfall_bps=sum(o.implementation_shortfall_bps for o in orders) / len(orders) if orders else 0,
        avg_price_shortfall_bps=sum(o.price_shortfall_bps for o in orders) / len(orders) if orders else 0,
        avg_vwap_slippage_bps=sum(o.vwap_slippage_bps for o in orders) / len(orders) if orders else 0,
        avg_fee_cost_bps=sum(o.fee_cost_bps for o in orders) / len(orders) if orders else 0,
        avg_opportunity_cost_bps=sum(o.opportunity_cost_bps for o in orders) / len(orders) if orders else 0,
        avg_market_impact_bps=sum(o.market_impact_bps for o in orders) / len(orders) if orders else 0,
        avg_timing_cost_bps=sum(o.timing_cost_bps for o in orders) / len(orders) if orders else 0,
        warnings=warnings,
    )

    return TCASummaryResponse(summary=summary, orders=orders)


@router.get(
    "/analysis/{client_order_id}",
    response_model=TCAOrderDetail,
    summary="Get TCA for specific order",
    description="Returns detailed TCA metrics for a specific order.",
)
def get_order_tca(
    client_order_id: str,
    ctx: AppContext = Depends(get_context),
    user: dict[str, Any] = Depends(build_user_context),
    _auth: AuthContext = Depends(tca_auth),
) -> TCAOrderDetail:
    """Get TCA metrics for a specific order.

    Returns detailed cost decomposition including:
    - Arrival vs execution price
    - VWAP/TWAP benchmarks
    - Fill statistics
    - Market impact breakdown
    """
    user_id = user.get("user_id", "unknown")
    user_obj = user.get("user")
    authorized_strategies = get_authorized_strategies(user_obj)

    if not authorized_strategies:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No authorized strategies - contact administrator",
        )

    logger.info(
        "Order TCA requested",
        extra={
            "user_id": user_id,
            "client_order_id": client_order_id,
        },
    )

    # Fetch trades for this specific order
    # Use a wide date range to find the order
    today = datetime.now(UTC).date()
    start_date = today - timedelta(days=ORDER_LOOKBACK_DAYS)

    trades = ctx.db.get_trades_for_tca(
        start_date=start_date,
        end_date=today,
        strategy_ids=list(authorized_strategies),
        client_order_id=client_order_id,
    )

    if not trades:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Order not found or no trades: {client_order_id}",
        )

    # Validate strategy ID consistency across all trades in the order
    strategy_ids_in_order = {t.get("strategy_id") for t in trades}
    if len(strategy_ids_in_order) > 1:
        logger.warning(
            "Order has inconsistent strategy IDs",
            extra={
                "client_order_id": client_order_id,
                "strategy_ids": list(strategy_ids_in_order),
            },
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Order has inconsistent strategy IDs: {list(strategy_ids_in_order)}",
        )

    # Verify user has access to this order's strategy
    strategy_id = trades[0].get("strategy_id", "unknown")
    if strategy_id not in authorized_strategies:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Not authorized for order's strategy: {strategy_id}",
        )

    # Build FillBatch and analyze
    fill_batch = _build_fill_batch(client_order_id, trades)
    if fill_batch is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Cannot analyze order - invalid trade data: {client_order_id}",
        )

    # Try TAQ analysis first, fall back to simple TCA
    taq_provider = _get_taq_provider()
    order_detail: TCAOrderDetail | None = None

    if taq_provider is not None:
        analyzer = ExecutionQualityAnalyzer(taq_provider=taq_provider)
        result = _analyze_order_with_taq(fill_batch, analyzer)
        if result is not None:
            order_detail = _result_to_order_detail(result, client_order_id, strategy_id)

    if order_detail is None:
        order_detail = _compute_simple_tca(fill_batch)
        if order_detail is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Cannot analyze order - no valid fills: {client_order_id}",
            )
        order_detail.strategy_id = strategy_id

    return order_detail


@router.get(
    "/benchmarks",
    response_model=TCABenchmarkResponse,
    summary="Get benchmark comparison",
    description="Returns time series of execution vs benchmark for charting.",
)
def get_benchmarks(
    client_order_id: str = Query(..., description="Order ID"),
    benchmark: Literal["vwap", "twap", "arrival"] = Query(
        default="vwap", description="Benchmark type"
    ),
    ctx: AppContext = Depends(get_context),
    user: dict[str, Any] = Depends(build_user_context),
    _auth: AuthContext = Depends(tca_auth),
) -> TCABenchmarkResponse:
    """Get benchmark comparison time series for charting.

    Returns execution price vs benchmark over the fill window,
    suitable for line chart visualization.
    """
    user_id = user.get("user_id", "unknown")
    user_obj = user.get("user")
    authorized_strategies = get_authorized_strategies(user_obj)

    if not authorized_strategies:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No authorized strategies - contact administrator",
        )

    logger.info(
        "TCA benchmarks requested",
        extra={
            "user_id": user_id,
            "client_order_id": client_order_id,
            "benchmark": benchmark,
        },
    )

    # Fetch trades for this specific order
    today = datetime.now(UTC).date()
    start_date = today - timedelta(days=ORDER_LOOKBACK_DAYS)

    trades = ctx.db.get_trades_for_tca(
        start_date=start_date,
        end_date=today,
        strategy_ids=list(authorized_strategies),
        client_order_id=client_order_id,
    )

    if not trades:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Order not found or no trades: {client_order_id}",
        )

    # Validate strategy ID consistency across all trades in the order
    strategy_ids_in_order = {t.get("strategy_id") for t in trades}
    if len(strategy_ids_in_order) > 1:
        logger.warning(
            "Order has inconsistent strategy IDs",
            extra={
                "client_order_id": client_order_id,
                "strategy_ids": list(strategy_ids_in_order),
            },
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Order has inconsistent strategy IDs: {list(strategy_ids_in_order)}",
        )

    # Verify user has access to this order's strategy
    strategy_id = trades[0].get("strategy_id", "unknown")
    if strategy_id not in authorized_strategies:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Not authorized for order's strategy: {strategy_id}",
        )

    # Build FillBatch and analyze
    fill_batch = _build_fill_batch(client_order_id, trades)
    if fill_batch is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Cannot analyze order - invalid trade data: {client_order_id}",
        )

    # Get order detail for summary
    taq_provider = _get_taq_provider()
    order_detail: TCAOrderDetail | None = None

    if taq_provider is not None:
        analyzer = ExecutionQualityAnalyzer(taq_provider=taq_provider)
        result = _analyze_order_with_taq(fill_batch, analyzer)
        if result is not None:
            order_detail = _result_to_order_detail(result, client_order_id, strategy_id)

    if order_detail is None:
        order_detail = _compute_simple_tca(fill_batch)
        if order_detail is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Cannot analyze order - no valid fills: {client_order_id}",
            )
        order_detail.strategy_id = strategy_id

    # Build benchmark time series from valid fills only
    # (we already validated fills exist via order_detail check above)
    valid_fills = fill_batch.valid_fills
    if not valid_fills:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Cannot generate benchmarks - no valid fills: {client_order_id}",
        )
    points: list[TCABenchmarkPoint] = []
    cumulative_qty = 0
    cumulative_notional = 0.0

    for fill in sorted(valid_fills, key=lambda f: f.timestamp):
        cumulative_qty += fill.quantity
        cumulative_notional += fill.price * fill.quantity
        exec_price = cumulative_notional / cumulative_qty if cumulative_qty > 0 else fill.price

        # Determine benchmark price
        benchmark_price = order_detail.arrival_price
        if benchmark == "vwap":
            benchmark_price = order_detail.vwap_benchmark
        elif benchmark == "twap":
            benchmark_price = order_detail.twap_benchmark

        # Compute slippage
        slippage = 0.0
        if benchmark_price > 0:
            slippage = (exec_price - benchmark_price) / benchmark_price * 10000
            if fill_batch.side == "sell":
                slippage = -slippage

        points.append(
            TCABenchmarkPoint(
                timestamp=fill.timestamp,
                execution_price=round(exec_price, 4),
                benchmark_price=round(benchmark_price, 4),
                benchmark_type=benchmark,
                slippage_bps=round(slippage, 2),
                cumulative_qty=cumulative_qty,
            )
        )

    return TCABenchmarkResponse(
        client_order_id=client_order_id,
        symbol=order_detail.symbol,
        side=order_detail.side,
        benchmark_type=benchmark,
        points=points,
        summary=order_detail,
    )


__all__ = ["router"]
