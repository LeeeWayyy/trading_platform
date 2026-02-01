"""Attribution Service for factor attribution with permission enforcement.

P6T10: Track 10 - Quantile & Attribution Analytics
Provides factor attribution for live/paper portfolios (not backtests).
"""

from __future__ import annotations

import logging
from datetime import date
from typing import TYPE_CHECKING, Literal

import polars as pl
from starlette.concurrency import run_in_threadpool

from libs.platform.analytics.attribution import (
    AttributionResult,
    FactorAttribution,
    FactorAttributionConfig,
)

if TYPE_CHECKING:
    from libs.data.data_providers.crsp_local_provider import CRSPLocalProvider
    from libs.data.data_providers.fama_french_local_provider import FamaFrenchLocalProvider
    from libs.web_console_data.strategy_scoped_queries import StrategyScopedDataAccess

logger = logging.getLogger(__name__)


class AttributionService:
    """Service for factor attribution with permission enforcement.

    DESIGN: Uses factory pattern - creates FactorAttribution per-request
    to support different model selections (ff3/ff5/ff6).

    SCOPE: This service is for Live/Paper Portfolio Attribution (pnl_daily table),
    NOT for backtest artifacts (use BacktestAnalyticsService for those).

    Example:
        service = AttributionService(data_access, ff_provider)
        result = await service.run_attribution(
            strategy_id="my_strategy",
            start_date=date(2023, 1, 1),
            end_date=date(2023, 12, 31),
            model="ff5",
        )
    """

    def __init__(
        self,
        data_access: StrategyScopedDataAccess,
        ff_provider: FamaFrenchLocalProvider,
        crsp_provider: CRSPLocalProvider | None = None,
    ) -> None:
        """Initialize with data access and providers.

        Args:
            data_access: For permission checks and portfolio returns.
            ff_provider: Fama-French factor data provider.
            crsp_provider: CRSP data provider (optional, for advanced filtering).
        """
        self._data = data_access
        self._ff_provider = ff_provider
        self._crsp_provider = crsp_provider
        # NOTE: Do NOT store FactorAttribution instance - create per-request

    def _create_attribution(
        self,
        model: Literal["ff3", "ff5", "ff6"],
    ) -> FactorAttribution:
        """Factory method: create FactorAttribution for specific model.

        Why factory pattern:
        - FactorAttribution may cache model-specific data
        - Different models (ff3/ff5/ff6) need different factor sets
        - Cleaner than resetting internal state
        """
        config = FactorAttributionConfig(model=model)
        return FactorAttribution(
            ff_provider=self._ff_provider,
            crsp_provider=self._crsp_provider,
            config=config,
        )

    async def run_attribution(
        self,
        strategy_id: str,
        start_date: date,
        end_date: date,
        model: Literal["ff3", "ff5", "ff6"] = "ff5",
    ) -> AttributionResult:
        """Run attribution with permission checks.

        Creates fresh FactorAttribution instance per-request.

        ASYNC/SYNC BRIDGE: FactorAttribution.fit() is CPU-heavy (stats).
        Uses run_in_threadpool to avoid blocking event loop.

        Args:
            strategy_id: The strategy/portfolio identifier.
            start_date: Start date for attribution window.
            end_date: End date for attribution window.
            model: Factor model (ff3, ff5, or ff6).

        Returns:
            AttributionResult with factor loadings and diagnostics.

        Raises:
            PermissionError: If user doesn't own the strategy.
            InsufficientObservationsError: If not enough data points.
            DataMismatchError: If dates don't overlap with factor data.
            ValueError: If start_date > end_date.
        """
        # Validate date range (defense-in-depth for non-UI callers)
        if start_date > end_date:
            raise ValueError(
                f"start_date ({start_date}) must be <= end_date ({end_date})"
            )

        # Get portfolio returns (ownership checked inside)
        returns_list = await self._data.get_portfolio_returns(
            strategy_id, start_date, end_date
        )

        if not returns_list:
            from libs.platform.analytics.attribution import InsufficientObservationsError

            raise InsufficientObservationsError(
                f"No return data for {strategy_id} between {start_date} and {end_date}"
            )

        # Convert to DataFrame format expected by FactorAttribution
        returns_df = pl.DataFrame(returns_list)

        # Schema validation: ensure required columns exist
        required_cols = {"date"}
        return_col = None
        if "daily_return" in returns_df.columns:
            return_col = "daily_return"
        elif "return" in returns_df.columns:
            return_col = "return"

        if return_col is None:
            from libs.platform.analytics.attribution import InsufficientObservationsError

            raise InsufficientObservationsError(
                f"Portfolio returns missing required return column. "
                f"Expected 'daily_return' or 'return', got: {list(returns_df.columns)}"
            )

        missing_cols = required_cols - set(returns_df.columns)
        if missing_cols:
            from libs.platform.analytics.attribution import InsufficientObservationsError

            raise InsufficientObservationsError(
                f"Portfolio returns missing required columns: {missing_cols}"
            )

        # Rename columns if needed (get_portfolio_returns returns date, daily_return)
        if "daily_return" in returns_df.columns:
            returns_df = returns_df.rename({"daily_return": "return"})

        # Normalize date column to pl.Date dtype (handles datetime, string)
        if returns_df["date"].dtype != pl.Date:
            try:
                returns_df = returns_df.with_columns(pl.col("date").cast(pl.Date))
            except Exception as e:
                from libs.platform.analytics.attribution import InsufficientObservationsError

                raise InsufficientObservationsError(
                    f"Failed to convert 'date' column to Date type: {e}"
                ) from e

        # Handle duplicate dates by averaging returns (prevents join errors)
        n_before = returns_df.height
        returns_df = returns_df.group_by("date").agg(pl.col("return").mean())
        n_after = returns_df.height
        if n_before > n_after:
            logger.info(
                "attribution_returns_deduplicated",
                extra={
                    "before": n_before,
                    "after": n_after,
                    "duplicates_merged": n_before - n_after,
                    "strategy_id": strategy_id,
                },
            )

        # Sort by date for consistent processing
        returns_df = returns_df.sort("date")

        # Wrap sync/CPU-heavy operations in threadpool
        attribution = await run_in_threadpool(self._create_attribution, model)
        result: AttributionResult = await run_in_threadpool(
            attribution.fit,
            returns_df,
            start_date,
            end_date,
            strategy_id,  # portfolio_id
        )
        return result


__all__ = ["AttributionService"]
