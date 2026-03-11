"""Strategy exposure aggregation service (P6T15/T15.3).

Computes per-strategy Long/Short/Gross/Net exposure from live positions and
falls back to mock data when no positions are available.
"""

from __future__ import annotations

import logging
import math
from decimal import Decimal, InvalidOperation
from typing import Any

from libs.platform.web_console_auth.permissions import (
    Permission,
    get_authorized_strategies,
    has_permission,
)
from libs.web_console_data.exposure_queries import (
    ExposureQueryResult,
    get_strategy_positions,
)
from libs.web_console_services.schemas.exposure import (
    StrategyExposureDTO,
    TotalExposureDTO,
)

logger = logging.getLogger(__name__)

_BIAS_AMBER_THRESHOLD = 10.0
_BIAS_RED_THRESHOLD = 25.0

# ---------------------------------------------------------------------------
# Mock data for placeholder display
# ---------------------------------------------------------------------------

_MOCK_STRATEGIES: list[dict[str, Any]] = [
    {
        "strategy": "Momentum Alpha",
        "positions": [
            {"qty": 100, "price": 2875.0},
            {"qty": 50, "price": 1500.0},
            {"qty": -80, "price": 2031.25},
        ],
    },
    {
        "strategy": "Mean Reversion",
        "positions": [
            {"qty": 30, "price": 2500.0},
            {"qty": -60, "price": 2083.33},
        ],
    },
    {
        "strategy": "Stat Arb",
        "positions": [
            {"qty": 50, "price": 1850.0},
            {"qty": -45, "price": 1944.44},
        ],
    },
]


def _generate_mock_exposures() -> list[StrategyExposureDTO]:
    """Generate mock strategy exposures for placeholder display."""
    exposures: list[StrategyExposureDTO] = []
    for mock in _MOCK_STRATEGIES:
        long_notional = 0.0
        short_notional = 0.0
        count = 0
        for pos in mock["positions"]:
            qty = pos["qty"]
            price = pos["price"]
            if qty > 0:
                long_notional += qty * price
            else:
                short_notional += abs(qty) * price
            count += 1

        gross = long_notional + short_notional
        net = long_notional - short_notional
        net_pct = (net / gross * 100) if gross > 0 else 0.0

        exposures.append(
            StrategyExposureDTO(
                strategy=mock["strategy"],
                long_notional=round(long_notional, 2),
                short_notional=round(short_notional, 2),
                gross_notional=round(gross, 2),
                net_notional=round(net, 2),
                net_pct=round(net_pct, 2),
                position_count=count,
            )
        )
    return exposures


def _compute_bias_warning(net_pct: float) -> tuple[str | None, str | None]:
    """Return ``(warning_text, severity)`` based on net exposure percentage.

    Severity is ``"red"`` (> 25%), ``"amber"`` (> 10%), or ``None``.
    """
    abs_pct = abs(net_pct)
    direction = "Long" if net_pct > 0 else "Short"
    if abs_pct > _BIAS_RED_THRESHOLD:
        return (
            f"Severe {direction} bias across strategies ({net_pct:+.1f}% of gross)",
            "red",
        )
    if abs_pct > _BIAS_AMBER_THRESHOLD:
        return (
            f"Net {direction} bias across strategies ({net_pct:+.1f}% of gross)",
            "amber",
        )
    return None, None


def _aggregate_exposures(
    result: ExposureQueryResult,
    *,
    scoped_unmapped: int = 0,
) -> tuple[list[StrategyExposureDTO], bool]:
    """Aggregate position dicts into per-strategy exposure DTOs.

    Args:
        result: Query result with positions and quality counts.
        scoped_unmapped: Permission-scoped unmapped position count.
            Used to gate mock fallback without leaking global counts.

    Returns:
        Tuple of (exposures, is_placeholder). is_placeholder is True when
        mock fallback is used.
    """
    if not result.positions:
        # Only show mock (example) data when there is genuinely no portfolio
        # activity.  If positions were excluded or unmapped, returning mock
        # totals/bias would mask real risk exposure.  Use scoped_unmapped
        # (not result.unmapped_position_count) to avoid cross-scope inference.
        if result.excluded_symbol_count > 0 or scoped_unmapped > 0:
            return [], False
        return _generate_mock_exposures(), True

    # Group positions by strategy
    by_strategy: dict[str, list[dict[str, Any]]] = {}
    for pos in result.positions:
        strategy = str(pos.get("strategy", "unknown"))
        by_strategy.setdefault(strategy, []).append(pos)

    exposures: list[StrategyExposureDTO] = []
    for strategy, positions in sorted(by_strategy.items()):
        long_notional = 0.0
        short_notional = 0.0
        valued_count = 0
        missing_price = 0

        fallback_price = 0
        fallback_symbols: list[str] = []
        for pos in positions:
            try:
                qty = float(pos.get("qty", 0))
            except (ValueError, TypeError, OverflowError):
                missing_price += 1  # Unvalued: malformed qty
                continue

            if not math.isfinite(qty):
                missing_price += 1  # Unvalued: non-finite qty
                continue

            price = pos.get("current_price")
            used_fallback = False
            if price is None or price == 0:
                price = pos.get("avg_entry_price")
                used_fallback = True

            if price is None:
                missing_price += 1
                continue

            try:
                price_float = float(Decimal(str(price)))
            except (InvalidOperation, ValueError, OverflowError):
                missing_price += 1
                continue

            if not math.isfinite(price_float) or price_float <= 0:
                # Invalid price — try fallback if not already using it
                if not used_fallback:
                    fb = pos.get("avg_entry_price")
                    if fb is not None:
                        try:
                            fb_float = float(Decimal(str(fb)))
                        except (InvalidOperation, ValueError, OverflowError):
                            fb_float = 0.0
                        if math.isfinite(fb_float) and fb_float > 0:
                            price_float = fb_float
                            used_fallback = True
                if not math.isfinite(price_float) or price_float <= 0:
                    missing_price += 1
                    continue

            if used_fallback:
                fallback_price += 1
                if len(fallback_symbols) < 5:
                    fallback_symbols.append(str(pos.get("symbol", "?")))

            if qty > 0:
                long_notional += qty * price_float
            elif qty < 0:
                short_notional += abs(qty) * price_float
            valued_count += 1

        if fallback_price > 0:
            logger.warning(
                "exposure_price_fallback",
                extra={
                    "strategy_id": strategy,
                    "fallback_count": fallback_price,
                    "fallback": "avg_entry_price",
                    "sample_symbols": fallback_symbols[:5],
                },
            )

        gross = long_notional + short_notional
        net = long_notional - short_notional
        net_pct = (net / gross * 100) if gross > 0 else 0.0

        exposures.append(
            StrategyExposureDTO(
                strategy=strategy,
                long_notional=round(long_notional, 2),
                short_notional=round(short_notional, 2),
                gross_notional=round(gross, 2),
                net_notional=round(net, 2),
                net_pct=round(net_pct, 2),
                position_count=valued_count + missing_price,
                missing_price_count=missing_price,
                fallback_price_count=fallback_price,
            )
        )

    return exposures, False


def _build_total(
    exposures: list[StrategyExposureDTO],
    *,
    is_placeholder: bool,
    excluded_symbol_count: int,
    unmapped_position_count: int = 0,
) -> TotalExposureDTO:
    """Build total exposure DTO from per-strategy exposures."""
    long_total = sum(e.long_notional for e in exposures)
    short_total = sum(e.short_notional for e in exposures)
    gross_total = sum(e.gross_notional for e in exposures)
    net_total = sum(e.net_notional for e in exposures)
    net_pct = (net_total / gross_total * 100) if gross_total > 0 else 0.0

    bias_warning, bias_severity = _compute_bias_warning(net_pct)

    total_missing = sum(e.missing_price_count for e in exposures)
    total_fallback = sum(e.fallback_price_count for e in exposures)
    is_partial = (
        excluded_symbol_count > 0
        or unmapped_position_count > 0
        or (total_missing > 0 and not is_placeholder)
    )

    warnings: list[str] = []
    if excluded_symbol_count > 0:
        warnings.append(
            f"{excluded_symbol_count} symbols excluded (traded by multiple strategies)"
        )
    if unmapped_position_count > 0:
        warnings.append(
            f"{unmapped_position_count} position"
            f"{'s' if unmapped_position_count != 1 else ''}"
            " without strategy mapping"
        )
    if total_missing > 0 and not is_placeholder:
        warnings.append(
            f"{total_missing} position{'s' if total_missing != 1 else ''}"
            " could not be valued (missing or invalid price/qty)"
        )
    if total_fallback > 0 and not is_placeholder:
        warnings.append(
            f"{total_fallback} position{'s' if total_fallback != 1 else ''}"
            " using entry price (live price unavailable)"
        )
    data_quality_warning = "; ".join(warnings) if warnings else None

    return TotalExposureDTO(
        long_total=round(long_total, 2),
        short_total=round(short_total, 2),
        gross_total=round(gross_total, 2),
        net_total=round(net_total, 2),
        net_pct=round(net_pct, 2),
        strategy_count=len(exposures),
        bias_warning=bias_warning,
        bias_severity=bias_severity,
        is_placeholder=is_placeholder,
        is_partial=is_partial,
        data_quality_warning=data_quality_warning,
        missing_price_count=total_missing,
        fallback_price_count=total_fallback,
        unmapped_position_count=unmapped_position_count,
    )


class ExposureService:
    """Strategy exposure aggregation service."""

    async def get_strategy_exposure(
        self,
        user: Any,
        db_pool: Any,
    ) -> tuple[list[StrategyExposureDTO], TotalExposureDTO]:
        """Compute per-strategy exposure and total.

        Args:
            user: Authenticated user dict with ``role`` and ``strategies``.
            db_pool: Database connection pool.

        Returns:
            Tuple of (per-strategy exposures, total exposure).

        Raises:
            PermissionError: If user lacks ``VIEW_STRATEGY_EXPOSURE``.
        """
        if not has_permission(user, Permission.VIEW_STRATEGY_EXPOSURE):
            raise PermissionError("Permission 'view_strategy_exposure' required")

        has_view_all = has_permission(user, Permission.VIEW_ALL_STRATEGIES)

        # VIEW_ALL_STRATEGIES unconditionally queries all mapped positions.
        # Using the provisioned strategy list for these users would risk
        # under-reporting exposure if provisioning is stale or incomplete.
        strategies: list[str] | None
        if has_view_all:
            strategies = None
        else:
            strategies = get_authorized_strategies(user)

        result = await get_strategy_positions(strategies, db_pool)

        # Unmapped counts are global — only surface to users who can see
        # all strategies to avoid leaking cross-scope portfolio information.
        # Scoping must happen BEFORE aggregation so the mock-fallback gate
        # doesn't reveal global state to restricted users.
        scoped_unmapped = (
            result.unmapped_position_count if has_view_all else 0
        )

        exposures, is_placeholder = _aggregate_exposures(
            result, scoped_unmapped=scoped_unmapped,
        )

        total = _build_total(
            exposures,
            is_placeholder=is_placeholder,
            excluded_symbol_count=result.excluded_symbol_count,
            unmapped_position_count=scoped_unmapped,
        )

        # Distinct warning when user has no strategies configured and
        # no VIEW_ALL_STRATEGIES permission (restricted user).
        if strategies is not None and not strategies and is_placeholder:
            total = total.model_copy(
                update={
                    "data_quality_warning": "No authorized strategies assigned",
                    "strategy_count": 0,
                },
            )

        return exposures, total


__all__ = [
    "ExposureService",
]
