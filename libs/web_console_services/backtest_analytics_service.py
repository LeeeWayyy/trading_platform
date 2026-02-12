"""Backtest Analytics Service with ownership enforcement.

This service wraps BacktestResultStorage to provide secure access to
backtest artifacts. All Parquet file access from UI pages MUST go
through this service to ensure proper ownership verification.

P6T10: Quantile & Attribution Analytics
P6T12: Portfolio returns access for comparison and live overlay
"""

from __future__ import annotations

import logging
from datetime import date
from typing import TYPE_CHECKING, Literal

import polars as pl
from starlette.concurrency import run_in_threadpool

from libs.trading.backtest.quantile_analysis import (
    InsufficientDataError,
    QuantileAnalysisConfig,
)
from libs.trading.backtest.quantile_analysis import (
    run_quantile_analysis as quantile_helper,
)

if TYPE_CHECKING:
    import exchange_calendars as xcals  # type: ignore[import-not-found]

    from libs.data.data_providers.universe import ForwardReturnsProvider
    from libs.trading.alpha.research_platform import BacktestResult
    from libs.trading.backtest.quantile_analysis import QuantileAnalysisConfig, QuantileResult
    from libs.trading.backtest.result_storage import BacktestResultStorage
    from libs.web_console_data.strategy_scoped_queries import StrategyScopedDataAccess

logger = logging.getLogger(__name__)


class BacktestAnalyticsService:
    """Service wrapper for backtest analytics with ownership enforcement.

    SECURITY: ALL backtest artifact access MUST go through this service.
    Direct BacktestResultStorage access is NOT allowed from pages.

    ASYNC/SYNC BRIDGE: BacktestResultStorage is synchronous (file I/O).
    This service is async (web layer). Uses run_in_threadpool to avoid
    blocking the event loop.

    Example usage:
        service = BacktestAnalyticsService(data_access, storage)
        signals = await service.get_universe_signals(job_id, limit=1000)
    """

    # Limit constants (enforced at service layer, not storage)
    MAX_UNIVERSE_SIGNALS_LIMIT = 10000
    MIN_UNIVERSE_SIGNALS_LIMIT = 1

    def __init__(
        self,
        data_access: StrategyScopedDataAccess,
        storage: BacktestResultStorage,
    ) -> None:
        """Initialize with data access and storage dependencies.

        Args:
            data_access: For permission checks (verify_job_ownership).
            storage: For Parquet file access.
        """
        self._data = data_access
        self._storage = storage

    async def verify_job_ownership(self, job_id: str) -> None:
        """Verify current user owns the backtest job.

        Args:
            job_id: The backtest job identifier.

        Raises:
            PermissionError: If the user doesn't own the job.
        """
        await self._data.verify_job_ownership(job_id)

    async def get_universe_signals(
        self,
        job_id: str,
        signal_name: str | None = None,
        date_range: tuple[date, date] | None = None,
        limit: int = 10000,
    ) -> pl.DataFrame | None:
        """Load universe signals with ownership check and lazy filtering.

        CRITICAL: Uses Polars lazy scan with predicate pushdown.
        Never loads full file into memory.

        Return Type Contract:
        - Returns pl.DataFrame (NOT LazyFrame) - collected after filtering
        - Returns None if: (a) job not found, (b) no signals file exists
        - Returns empty DataFrame if: valid query but no matching rows

        UI Distinction (caller responsibility):
        - None → "Analytics unavailable" (config/permission issue)
        - Empty DataFrame → "No data for selected range" (valid but sparse)

        Limit Validation: Enforced here (not storage layer):
        - limit = min(max(limit, MIN_LIMIT), MAX_LIMIT)
        - Prevents callers from bypassing limit with huge values

        Args:
            job_id: The backtest job identifier.
            signal_name: Optional filter by signal name.
            date_range: Optional (start, end) date filter (inclusive).
            limit: Maximum rows to return (default 10000, max 10000).

        Returns:
            DataFrame with filtered signals, or None if unavailable.

        Raises:
            PermissionError: If user doesn't own the job.
        """
        # Verify ownership first (async)
        await self.verify_job_ownership(job_id)

        # Enforce limit bounds at service layer (defensive: handle None/invalid)
        if limit is None or not isinstance(limit, int):
            limit = self.MAX_UNIVERSE_SIGNALS_LIMIT
        validated_limit = min(
            max(limit, self.MIN_UNIVERSE_SIGNALS_LIMIT),
            self.MAX_UNIVERSE_SIGNALS_LIMIT,
        )

        # Async bridge: wrap sync I/O in threadpool
        # Catch storage errors to match documented contract (return None if unavailable)
        from libs.trading.backtest.models import JobNotFound, ResultPathMissing

        try:
            lazy_result = await run_in_threadpool(
                self._storage.load_universe_signals_lazy,
                job_id,
                signal_name,
                date_range,
                validated_limit,
            )
        except (JobNotFound, ResultPathMissing) as e:
            logger.warning(
                "get_universe_signals_unavailable",
                extra={"job_id": job_id, "error": str(e)},
            )
            return None

        if lazy_result is None:
            return None

        # Collect LazyFrame to DataFrame before returning
        return await run_in_threadpool(lazy_result.collect)

    async def get_backtest_result(self, job_id: str) -> BacktestResult:
        """Load full backtest result with ownership check.

        Args:
            job_id: The backtest job identifier.

        Returns:
            BacktestResult with all artifacts loaded.

        Raises:
            PermissionError: If user doesn't own the job.
            JobNotFound: If job doesn't exist.
            ResultPathMissing: If result path is invalid.
        """

        await self.verify_job_ownership(job_id)

        # Wrap sync storage call in threadpool
        result: BacktestResult = await run_in_threadpool(self._storage.get_result, job_id)
        return result

    async def run_quantile_analysis(
        self,
        job_id: str,
        forward_returns_provider: ForwardReturnsProvider,
        calendar: xcals.ExchangeCalendar,
        config: QuantileAnalysisConfig | None = None,
        signal_name: str | None = None,
        universe_name: str = "",
    ) -> QuantileResult:
        """Run quantile analysis on universe signals with ownership check.

        Args:
            job_id: The backtest job identifier.
            forward_returns_provider: Provider for forward returns.
            calendar: Trading calendar for date arithmetic.
            config: Analysis configuration (optional).
            signal_name: Filter to specific signal name.
            universe_name: Universe name for metadata.

        Returns:
            QuantileResult with Rank IC and quantile metrics.

        Raises:
            PermissionError: If user doesn't own the job.
            InsufficientDataError: If not enough data for analysis.
            CRSPUnavailableError: If CRSP data is not available.
        """
        # Verify ownership first
        await self.verify_job_ownership(job_id)

        # Load universe signals - use unlimited for analytics to avoid biased metrics
        # (get_universe_signals limit is for UI pagination, not analytics)
        # Catch storage errors to convert to InsufficientDataError for controlled handling
        from libs.trading.backtest.models import JobNotFound, ResultPathMissing

        try:
            lazy_result = await run_in_threadpool(
                self._storage.load_universe_signals_lazy,
                job_id,
                signal_name,
                None,  # No date range filter
                None,  # No limit for analytics - need full dataset
            )
            signals = await run_in_threadpool(lazy_result.collect) if lazy_result else None
        except (JobNotFound, ResultPathMissing) as e:
            logger.warning(
                "run_quantile_analysis_storage_error",
                extra={"job_id": job_id, "error": str(e)},
            )
            raise InsufficientDataError(
                f"Backtest artifacts unavailable for job {job_id}: {e}"
            ) from e

        if signals is None or signals.height == 0:
            raise InsufficientDataError("No universe signals found for this backtest")

        # Handle column name variations: storage uses 'signal', analytics expects 'signal_value'
        if "signal" in signals.columns and "signal_value" not in signals.columns:
            signals = signals.rename({"signal": "signal_value"})

        required_cols = {"signal_value", "permno"}
        # Check for date column (can be 'date' or 'signal_date')
        has_date = "date" in signals.columns or "signal_date" in signals.columns
        if not has_date:
            raise InsufficientDataError("Signal data missing date column ('date' or 'signal_date')")

        missing_cols = required_cols - set(signals.columns)
        if missing_cols:
            raise InsufficientDataError(f"Signal data missing required columns: {missing_cols}")

        # Coerce date column to pl.Date to prevent join/calendar mismatches
        date_col = "signal_date" if "signal_date" in signals.columns else "date"
        if signals[date_col].dtype != pl.Date:
            try:
                signals = signals.with_columns(pl.col(date_col).cast(pl.Date))
            except Exception as e:
                raise InsufficientDataError(
                    f"Failed to convert {date_col} to Date type: {e}"
                ) from e

        # Use the run_quantile_analysis helper which handles:
        # 1. Date column renaming (date -> signal_date)
        # 2. Signal date normalization (once, avoids redundancy)
        # 3. Forward returns computation with deduplication
        # 4. Analysis with skip_normalization=True
        cfg = config or QuantileAnalysisConfig()
        result: QuantileResult = await run_in_threadpool(
            quantile_helper,
            signals,
            forward_returns_provider,
            calendar,
            cfg,
            signal_name or "",
            universe_name,
        )

        return result

    async def get_portfolio_returns(
        self,
        job_id: str,
        basis: Literal["net", "gross"] = "net",
    ) -> tuple[pl.DataFrame | None, Literal["net", "gross"]]:
        """Load portfolio returns with ownership check and basis fallback.

        Verifies user ownership, then loads the return series for the
        requested basis.  If ``basis="net"`` and net returns are unavailable,
        falls back to gross returns so the caller knows a fallback occurred.

        Args:
            job_id: The backtest job identifier.
            basis: Preferred return basis (``"net"`` or ``"gross"``).

        Returns:
            Tuple of ``(DataFrame_with_{date,return}_columns | None,
            actual_basis_used)``.  ``None`` means no return data is
            available for this job.

        Raises:
            PermissionError: If user doesn't own the job (propagated).
        """
        await self.verify_job_ownership(job_id)

        from libs.trading.backtest.models import JobNotFound, ResultPathMissing

        try:
            if basis == "net":
                df = await run_in_threadpool(
                    self._storage.load_portfolio_returns, job_id, "net"
                )
                if df is not None:
                    return (df, "net")
                # Fallback to gross
                df = await run_in_threadpool(
                    self._storage.load_portfolio_returns, job_id, "gross"
                )
                return (df, "gross")
            else:
                df = await run_in_threadpool(
                    self._storage.load_portfolio_returns, job_id, "gross"
                )
                return (df, "gross")
        except (JobNotFound, ResultPathMissing) as e:
            logger.warning(
                "get_portfolio_returns_unavailable",
                extra={"job_id": job_id, "error": str(e)},
            )
            return (None, basis)


    async def get_portfolio_returns_both(
        self,
        job_id: str,
    ) -> tuple[pl.DataFrame | None, pl.DataFrame | None]:
        """Load both net and gross portfolio returns in one ownership-verified call.

        Single ownership check, then loads both bases.  Avoids the two-pass
        pattern where the caller first requests net, discovers some jobs
        lack cost data, and re-fetches as gross.

        Returns:
            ``(net_df, gross_df)`` — either may be ``None`` if the
            corresponding Parquet artifact is missing.

        Raises:
            PermissionError: If user doesn't own the job.
        """
        await self.verify_job_ownership(job_id)

        from libs.trading.backtest.models import JobNotFound, ResultPathMissing

        net_df: pl.DataFrame | None = None
        gross_df: pl.DataFrame | None = None

        try:
            net_df = await run_in_threadpool(
                self._storage.load_portfolio_returns, job_id, "net"
            )
        except (JobNotFound, ResultPathMissing):
            pass

        try:
            gross_df = await run_in_threadpool(
                self._storage.load_portfolio_returns, job_id, "gross"
            )
        except (JobNotFound, ResultPathMissing):
            pass

        return (net_df, gross_df)


__all__ = ["BacktestAnalyticsService"]
