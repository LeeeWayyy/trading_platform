"""
Simple backtester for non-PIT data sources (e.g., Yahoo Finance).

Provides SimpleBacktester that uses UnifiedDataFetcher for data access.
Suitable for development/testing where strict PIT compliance is not required.

WARNING: This backtester does NOT enforce point-in-time (PIT) compliance.
Results may exhibit look-ahead bias. Use CRSP/PITBacktester for production.

Example:
    >>> from libs.trading.alpha.simple_backtester import SimpleBacktester
    >>> from libs.trading.alpha.metrics import AlphaMetricsAdapter
    >>> from libs.data.data_providers.unified_fetcher import UnifiedDataFetcher, FetcherConfig
    >>> from libs.data.data_providers.yfinance_provider import YFinanceProvider
    >>> from datetime import date
    >>>
    >>> # Setup providers
    >>> yf_provider = YFinanceProvider(storage_path=Path("data/yfinance"))
    >>> config = FetcherConfig(provider=ProviderType.YFINANCE)
    >>> fetcher = UnifiedDataFetcher(config, yfinance_provider=yf_provider)
    >>>
    >>> # Run backtest
    >>> backtester = SimpleBacktester(fetcher, AlphaMetricsAdapter())
    >>> result = backtester.run_backtest(
    ...     alpha=my_alpha,
    ...     start_date=date(2024, 1, 1),
    ...     end_date=date(2024, 12, 31),
    ...     universe=["AAPL", "MSFT", "GOOGL"],
    ... )
    >>> print(f"Mean IC: {result.mean_ic:.4f}, ICIR: {result.icir:.4f}")
"""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from collections.abc import Callable
from datetime import date, timedelta
from typing import Literal

import polars as pl

from libs.data.data_providers.unified_fetcher import UnifiedDataFetcher
from libs.trading.alpha.alpha_definition import AlphaDefinition
from libs.trading.alpha.exceptions import MissingForwardReturnError
from libs.trading.alpha.metrics import AlphaMetricsAdapter
from libs.trading.alpha.portfolio import SignalToWeight, TurnoverCalculator
from libs.trading.alpha.research_platform import BacktestResult

logger = logging.getLogger(__name__)

# Buffer days for data fetching to ensure sufficient lookback/forward data
_LOOKBACK_BUFFER_DAYS = 90  # Buffer before start_date for returns calculation
_FORWARD_BUFFER_DAYS = 90   # Buffer after end_date for forward returns


class SimpleBacktester:
    """Backtester for non-PIT data sources (development/testing only).

    Adapts symbol-based data (yfinance) to permno-based alpha interface.
    Calculates returns on-the-fly from adjusted prices.

    WARNING: This backtester does NOT provide point-in-time guarantees.
    Use PITBacktester with CRSP data for production backtests.

    Features:
        - Uses UnifiedDataFetcher for data access
        - Maps symbols to pseudo-permnos for alpha compatibility
        - Computes returns from adjusted price history
        - Ignores filing lags/snapshots (no PIT compliance)

    Example:
        >>> backtester = SimpleBacktester(fetcher, metrics_adapter)
        >>> result = backtester.run_backtest(
        ...     alpha=momentum_alpha,
        ...     start_date=date(2024, 1, 1),
        ...     end_date=date(2024, 6, 30),
        ...     universe=["AAPL", "MSFT", "NVDA"],
        ... )
    """

    def __init__(
        self,
        data_fetcher: UnifiedDataFetcher,
        metrics_adapter: AlphaMetricsAdapter | None = None,
    ) -> None:
        """Initialize SimpleBacktester.

        Args:
            data_fetcher: Configured UnifiedDataFetcher for price data access.
            metrics_adapter: Alpha metrics adapter for IC/ICIR computation.
                Defaults to a new AlphaMetricsAdapter instance if not provided.
        """
        self._fetcher = data_fetcher
        self._metrics = metrics_adapter or AlphaMetricsAdapter()
        self._symbol_map: dict[str, int] = {}
        self._permno_map: dict[int, str] = {}
        self._next_permno = 1
        self._prices_cache: pl.DataFrame | None = None

    def _get_permno(self, symbol: str) -> int:
        """Get or create pseudo-permno for a symbol.

        Args:
            symbol: Ticker symbol (e.g., "AAPL").

        Returns:
            Integer pseudo-permno for the symbol.
        """
        if symbol not in self._symbol_map:
            permno = self._next_permno
            self._next_permno += 1
            self._symbol_map[symbol] = permno
            self._permno_map[permno] = symbol
        return self._symbol_map[symbol]

    def _prepare_data(
        self, start_date: date, end_date: date, symbols: list[str]
    ) -> pl.DataFrame:
        """Fetch and prepare data with pseudo-permnos and returns.

        Fetches price data with a buffer period for returns calculation,
        computes daily returns from adjusted close prices, and maps
        symbols to pseudo-permnos for alpha compatibility.

        Args:
            start_date: Backtest start date.
            end_date: Backtest end date.
            symbols: List of ticker symbols to fetch.

        Returns:
            DataFrame with columns: date, symbol, permno, prc, ret, vol, etc.
        """
        # Add buffer for returns calculation (lookback for historical returns, forward for forward returns)
        fetch_start = start_date - timedelta(days=_LOOKBACK_BUFFER_DAYS)
        fetch_end = end_date + timedelta(days=_FORWARD_BUFFER_DAYS)

        logger.info(
            "Fetching data from %s to %s for %d symbols",
            fetch_start, fetch_end, len(symbols)
        )

        raw_df = self._fetcher.get_daily_prices(symbols, fetch_start, fetch_end)

        if raw_df.is_empty():
            return raw_df

        # Calculate returns if missing (yfinance default)
        # Sort by symbol, date to ensure correct shift
        df = raw_df.sort(["symbol", "date"])

        # Calculate daily return: (adj_close / prev_adj_close) - 1
        # Use adj_close for returns to handle splits/dividends
        df = df.with_columns([
            pl.col("adj_close").pct_change().over("symbol").alias("calculated_ret")
        ])

        # Use calculated ret if 'ret' is null or missing
        if "ret" not in df.columns:
            df = df.with_columns(pl.col("calculated_ret").alias("ret"))
        else:
            df = df.with_columns(
                pl.coalesce(["ret", "calculated_ret"]).alias("ret")
            )

        # Map symbols to permnos efficiently using join
        unique_symbols = df["symbol"].unique().to_list()
        permno_rows = [
            {"symbol": s, "permno": self._get_permno(s)} for s in unique_symbols
        ]
        mapping_df = pl.DataFrame(
            permno_rows, schema={"symbol": pl.Utf8, "permno": pl.Int64}
        )

        df = df.join(mapping_df, on="symbol", how="left")

        # Rename close -> prc for alpha compatibility
        df = df.rename({"close": "prc"})

        # Ensure 'vol' exists if alpha needs it (Unified has 'volume')
        if "volume" in df.columns and "vol" not in df.columns:
            df = df.with_columns(pl.col("volume").alias("vol"))

        return df

    def _invoke_callbacks(
        self,
        progress_callback: Callable[[int, date | None], None] | None,
        cancel_check: Callable[[], None] | None,
        last_callback_time: float,
        pct: int,
        current_date: date | None,
        force: bool = False,
    ) -> float:
        """Invoke progress and cancellation callbacks with rate limiting.

        Args:
            progress_callback: Function to report progress percentage and date.
            cancel_check: Function to check if cancellation was requested.
            last_callback_time: Monotonic time of last callback invocation.
            pct: Current progress percentage (0-100).
            current_date: Current date being processed.
            force: If True, bypass rate limiting.

        Returns:
            Updated last_callback_time for next invocation.
        """
        now = time.monotonic()
        if cancel_check is not None:
            cancel_check()

        # Rate limit to 1Hz unless forced
        if not force and (now - last_callback_time) < 1.0:
            return last_callback_time

        pct_clamped = min(100, max(0, pct))

        if progress_callback is not None:
            progress_callback(pct_clamped, current_date)

        return now

    def _compute_forward_returns(
        self, prices: pl.DataFrame, as_of_date: date, horizon: int
    ) -> pl.DataFrame:
        """Compute forward returns from cached prices.

        Calculates geometrically compounded returns for a given horizon
        period starting from the day after as_of_date.

        Args:
            prices: Full price DataFrame with 'date', 'permno', 'ret' columns.
            as_of_date: Reference date (returns are computed after this date).
            horizon: Number of trading days for return calculation.

        Returns:
            DataFrame with columns: permno, return, date.

        Raises:
            MissingForwardReturnError: If insufficient future data available.
        """
        # Find target date
        future_dates = (
            prices.filter(pl.col("date") > as_of_date)
            .select("date")
            .unique()
            .sort("date")
            .head(horizon + 5)
            .to_series()
            .to_list()
        )

        if len(future_dates) < horizon:
            raise MissingForwardReturnError(
                f"Insufficient future data after {as_of_date}"
            )

        target_date = future_dates[horizon - 1]

        # Filter relevant rows
        forward_data = prices.filter(
            (pl.col("date") > as_of_date) & (pl.col("date") <= target_date)
        )

        # Geometric compounding: (1+r1)*(1+r2)*...*(1+rn) - 1
        forward_returns = (
            forward_data.group_by("permno")
            .agg(
                [
                    ((pl.col("ret") + 1).product() - 1).alias("return"),
                    pl.col("ret").count().alias("n_days"),
                ]
            )
            .filter(pl.col("n_days") == horizon)
            .select(["permno", "return"])
        )

        return forward_returns.with_columns(pl.lit(as_of_date).alias("date"))

    def _build_date_index(self, prices: pl.DataFrame) -> dict[date, int]:
        """Build a date-to-row-index mapping for efficient slicing.

        Creates a mapping from each unique date to its first row index,
        enabling O(1) date lookups instead of O(N) filtering per iteration.
        This optimization reduces the backtest loop from O(N²) to O(N) complexity.

        Args:
            prices: Sorted price DataFrame (must be sorted by date).

        Returns:
            Dictionary mapping dates to their first row index.

        Complexity:
            Time: O(N) where N is the number of rows in prices.
            Space: O(D) where D is the number of unique dates.
        """
        dates = prices["date"].to_list()
        date_index: dict[date, int] = {}
        for i, d in enumerate(dates):
            if d not in date_index:
                date_index[d] = i
        return date_index

    def run_backtest(
        self,
        alpha: AlphaDefinition,
        start_date: date,
        end_date: date,
        universe: list[str],
        weight_method: Literal["zscore", "quantile", "rank"] = "zscore",
        decay_horizons: list[int] | None = None,
        batch_size: int = 252,
        progress_callback: Callable[[int, date | None], None] | None = None,
        cancel_check: Callable[[], None] | None = None,
    ) -> BacktestResult:
        """Run backtest using non-PIT data.

        Executes a full backtest loop: fetches data, computes daily signals,
        calculates forward returns, and aggregates metrics.

        Args:
            alpha: Alpha definition with compute() method.
            start_date: First date of backtest period.
            end_date: Last date of backtest period.
            universe: List of ticker symbols to include.
            weight_method: Signal-to-weight conversion method.
            decay_horizons: List of horizons for decay curve computation.
            batch_size: Unused (kept for API compatibility).
            progress_callback: Function called with (pct, date) for progress.
            cancel_check: Function to check for cancellation request.

        Returns:
            BacktestResult with all computed metrics and time series.

        Raises:
            ValueError: If no data available or no signals computed.
            MissingForwardReturnError: Propagated from forward return computation.
        """
        if decay_horizons is None:
            decay_horizons = [1, 2, 5, 10, 20, 60]

        backtest_id = str(uuid.uuid4())
        # Deterministic snapshot_id for reproducibility (hash of config, not timestamp)
        snapshot_content = f"{alpha.name}|{start_date}|{end_date}|{sorted(universe)}|{weight_method}"
        snapshot_hash = hashlib.sha256(snapshot_content.encode()).hexdigest()[:16]
        snapshot_id = f"yfinance-simple-{snapshot_hash}"
        logger.info("Starting simple backtest %s for %s", backtest_id, alpha.name)

        last_callback_time = time.monotonic()

        # Fetch all data upfront (simplification for non-PIT)
        prices = self._prepare_data(start_date, end_date, universe)

        if prices.is_empty():
            raise ValueError("No data returned for universe")

        # Sort once and build index for efficient slicing (O(1) lookup vs O(N) filter)
        prices = prices.sort(["date", "symbol"])
        date_index = self._build_date_index(prices)
        sorted_dates = sorted(date_index.keys())

        # Precompute end_idx for each date to avoid O(N) scan per day (O(N²) → O(N))
        end_idx_by_date: dict[date, int | None] = {}
        for i, d in enumerate(sorted_dates):
            if i > 0:
                prev_date = sorted_dates[i - 1]
                end_idx_by_date[prev_date] = date_index[d]
        # Last date has no successor, so end_idx is None (use all rows)
        if sorted_dates:
            end_idx_by_date[sorted_dates[-1]] = None

        # Get trading days within range
        trading_days = [
            d for d in sorted_dates if start_date <= d <= end_date
        ]

        if not trading_days:
            raise ValueError(f"No trading days found between {start_date} and {end_date}")

        total_days = len(trading_days)
        processed_days = 0

        all_signals: list[pl.DataFrame] = []
        all_returns: list[pl.DataFrame] = []
        successfully_processed_dates: list[date] = []

        for as_of_date in trading_days:
            try:
                # Use precomputed end_idx for O(1) lookup (not O(N) scan per iteration)
                end_idx = end_idx_by_date.get(as_of_date)

                if end_idx is None:
                    # as_of_date is the last date or not in map - filter to prevent leakage
                    current_prices = prices.filter(pl.col("date") <= as_of_date)
                else:
                    current_prices = prices.head(end_idx)

                # CRITICAL: Data leakage assertion - ensure no future data in ANY row
                # Validate ALL rows, not just max date, to catch off-by-one errors in slicing
                future_rows = current_prices.filter(pl.col("date") > as_of_date)
                if not future_rows.is_empty():
                    future_dates = future_rows["date"].unique().sort().to_list()
                    raise RuntimeError(
                        f"Data leakage detected: {future_rows.height} rows with "
                        f"{len(future_dates)} future date(s) after {as_of_date}: "
                        f"{future_dates[:5]}{'...' if len(future_dates) > 5 else ''}"
                    )

                # Compute forward returns (1-day for IC)
                fwd_returns = self._compute_forward_returns(prices, as_of_date, horizon=1)

                # Compute signal (fundamentals are None for simple backtest)
                signal = alpha.compute(current_prices, None, as_of_date)

                all_signals.append(signal)
                all_returns.append(fwd_returns)
                successfully_processed_dates.append(as_of_date)

                processed_days += 1
                current_pct = int((processed_days / total_days) * 100)
                last_callback_time = self._invoke_callbacks(
                    progress_callback, cancel_check, last_callback_time,
                    current_pct, as_of_date
                )

            except MissingForwardReturnError:
                logger.warning(
                    "Stopping backtest at %s: forward returns unavailable", as_of_date
                )
                break

        if not all_signals:
            raise ValueError("No signals computed")

        daily_signals = pl.concat(all_signals)
        daily_returns = pl.concat(all_returns)

        # Attach symbol mapping for UI readability
        permno_rows = [
            {"permno": permno, "symbol": symbol} for permno, symbol in self._permno_map.items()
        ]
        mapping_df = pl.DataFrame(permno_rows, schema={"permno": pl.Int64, "symbol": pl.Utf8})
        daily_signals = daily_signals.join(mapping_df, on="permno", how="left")
        daily_returns = daily_returns.join(mapping_df, on="permno", how="left").with_columns(
            [
                pl.col("date").cast(pl.Date),
                pl.col("permno").cast(pl.Int64),
                pl.col("return").cast(pl.Float64),
            ]
        )

        # Persist price series for detail views (Yahoo only)
        prices_in_period = prices.filter(pl.col("date").is_between(start_date, end_date))
        price_col = "adj_close" if "adj_close" in prices_in_period.columns else "prc"
        daily_prices = prices_in_period.select(
            [
                pl.col("date").cast(pl.Date),
                pl.col("permno").cast(pl.Int64),
                pl.col("symbol").cast(pl.Utf8),
                pl.col(price_col).cast(pl.Float64).alias("price"),
            ]
        )

        # Compute Daily IC
        daily_ic_list = []
        unique_dates = daily_signals["date"].unique().sort()
        for d in unique_dates:
            ds = daily_signals.filter(pl.col("date") == d)
            dr = daily_returns.filter(pl.col("date") == d)
            if ds.height >= 2:  # Min obs for correlation
                ic_res = self._metrics.compute_ic(ds, dr)
                daily_ic_list.append({
                    "date": d,
                    "ic": ic_res.pearson_ic,
                    "rank_ic": ic_res.rank_ic
                })

        daily_ic = (
            pl.DataFrame(daily_ic_list)
            if daily_ic_list
            else pl.DataFrame(schema={"date": pl.Date, "ic": pl.Float64, "rank_ic": pl.Float64})
        )

        # Summary Metrics
        _mean_ic_raw = daily_ic["rank_ic"].mean() if not daily_ic.is_empty() else 0.0
        mean_ic: float = float(_mean_ic_raw) if isinstance(_mean_ic_raw, int | float) else 0.0
        icir_res = self._metrics.compute_icir(daily_ic)
        hit_rate = self._metrics.compute_hit_rate(daily_signals, daily_returns)

        # Coverage
        daily_cov = (
            daily_signals.group_by("date")
            .agg(pl.col("signal").is_not_null().mean().alias("cov"))
        )
        _coverage_raw = daily_cov["cov"].mean() if not daily_cov.is_empty() else 0.0
        coverage: float = float(_coverage_raw) if isinstance(_coverage_raw, int | float) else 0.0

        long_short = self._metrics.compute_long_short_spread(daily_signals, daily_returns)

        # Weights
        weight_converter = SignalToWeight(method=weight_method)
        daily_weights = weight_converter.convert(daily_signals)

        turnover_calc = TurnoverCalculator()
        turnover_result = turnover_calc.compute_turnover_result(daily_weights)

        daily_portfolio_returns = (
            daily_weights.join(daily_returns, on=["permno", "date"], how="inner")
            .group_by("date")
            .agg((pl.col("weight") * pl.col("return")).sum().alias("return"))
            .sort("date")
        )

        # Decay curve computation
        returns_by_horizon: dict[int, pl.DataFrame] = {}
        for h in decay_horizons:
            try:
                res_list = []
                for d in unique_dates:
                    try:
                        hr = self._compute_forward_returns(prices, d, h)
                        res_list.append(hr)
                    except MissingForwardReturnError:
                        pass
                if res_list:
                    returns_by_horizon[h] = pl.concat(res_list)
            except Exception:
                continue

        decay_result = self._metrics.compute_decay_curve(daily_signals, returns_by_horizon)

        # Autocorrelation
        mean_signal_ts = daily_signals.group_by("date").agg(
            pl.col("signal").mean().alias("signal")
        )
        autocorr = self._metrics.compute_autocorrelation(mean_signal_ts)

        # Get actual number of symbols with data
        actual_symbols = daily_signals["permno"].n_unique()

        return BacktestResult(
            alpha_name=alpha.name,
            backtest_id=backtest_id,
            start_date=start_date,
            end_date=end_date,
            # Deterministic snapshot_id for reproducibility
            snapshot_id=snapshot_id,
            # Clear API: provider_type distinguishes from PIT version hashes
            dataset_version_ids={
                "provider_type": "yfinance",
                "provider": self._fetcher.get_active_provider(),
                "version": "N/A",  # Yahoo Finance has no versioning
                "pit_compliant": "false",
            },
            daily_signals=daily_signals,
            daily_ic=daily_ic,
            mean_ic=mean_ic,
            icir=icir_res.icir or 0.0,
            hit_rate=hit_rate or 0.0,
            coverage=coverage,
            long_short_spread=long_short or 0.0,
            autocorrelation=autocorr,
            weight_method=weight_method,
            daily_weights=daily_weights,
            daily_portfolio_returns=daily_portfolio_returns,
            turnover_result=turnover_result,
            decay_curve=decay_result.decay_curve,
            decay_half_life=decay_result.half_life,
            daily_prices=daily_prices,
            daily_returns=daily_returns,
            # Use processed_days instead of len(trading_days) to avoid inflation
            n_days=processed_days,
            n_symbols_avg=actual_symbols,
        )
