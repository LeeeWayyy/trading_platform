"""
Point-in-time backtesting engine for alpha research.

Provides PITBacktester that ensures all data access is strictly PIT-correct
through snapshot-locked data paths.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import polars as pl

from libs.alpha.alpha_definition import AlphaDefinition
from libs.alpha.exceptions import (
    MissingForwardReturnError,
    PITViolationError,
)
from libs.alpha.metrics import AlphaMetricsAdapter
from libs.alpha.portfolio import SignalToWeight, TurnoverCalculator, TurnoverResult

if TYPE_CHECKING:
    from libs.data_providers.compustat_local_provider import CompustatLocalProvider
    from libs.data_providers.crsp_local_provider import CRSPLocalProvider
    from libs.data_quality.versioning import DatasetVersionManager, SnapshotManifest

logger = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    """Complete result of alpha backtest with full metadata."""

    # Identification
    alpha_name: str
    backtest_id: str
    start_date: date
    end_date: date
    snapshot_id: str
    dataset_version_ids: dict[str, str]

    # Daily metrics
    daily_signals: pl.DataFrame  # [permno, date, signal]
    daily_ic: pl.DataFrame  # [date, ic, rank_ic]

    # Summary statistics
    mean_ic: float
    icir: float
    hit_rate: float
    coverage: float
    long_short_spread: float
    autocorrelation: dict[int, float]

    # Turnover (requires weight conversion)
    weight_method: str
    daily_weights: pl.DataFrame  # [permno, date, weight]
    turnover_result: TurnoverResult

    # Decay curve
    decay_curve: pl.DataFrame  # [horizon, ic, rank_ic]
    decay_half_life: float | None

    # Metadata
    computation_timestamp: datetime = field(
        default_factory=lambda: datetime.now(UTC)
    )
    n_days: int = 0
    n_symbols_avg: float = 0.0

    @property
    def average_turnover(self) -> float:
        """Average daily turnover."""
        return self.turnover_result.average_turnover


class PITBacktester:
    """Point-in-time correct backtesting engine.

    CRITICAL: All data access goes through snapshot-locked paths.
    No live provider access is permitted during backtest.

    Example:
        >>> backtester = PITBacktester(version_mgr, crsp, compustat, metrics)
        >>> result = backtester.run_backtest(
        ...     alpha=MomentumAlpha(),
        ...     start_date=date(2020, 1, 1),
        ...     end_date=date(2022, 12, 31),
        ...     weight_method="zscore",
        ... )
        >>> print(f"ICIR: {result.icir:.2f}")
    """

    def __init__(
        self,
        version_manager: DatasetVersionManager,
        crsp_provider: CRSPLocalProvider,
        compustat_provider: CompustatLocalProvider,
        metrics_adapter: AlphaMetricsAdapter | None = None,
    ):
        """Initialize PITBacktester.

        Args:
            version_manager: For snapshot management and PIT data access
            crsp_provider: CRSP provider (for snapshot-locked access only)
            compustat_provider: Compustat provider (for snapshot-locked access only)
            metrics_adapter: Alpha metrics adapter (created if not provided)
        """
        self._version_manager = version_manager
        self._crsp_provider = crsp_provider
        self._compustat_provider = compustat_provider
        self._metrics = metrics_adapter or AlphaMetricsAdapter()

        # Snapshot state (set during backtest)
        self._snapshot: SnapshotManifest | None = None
        self._prices_cache: pl.DataFrame | None = None
        self._fundamentals_cache: pl.DataFrame | None = None

    def _ensure_snapshot_locked(self) -> None:
        """Assert snapshot is locked before any data access."""
        if self._snapshot is None:
            raise PITViolationError(
                "No snapshot locked - call run_backtest first"
            )

    def _lock_snapshot(self, snapshot_id: str | None) -> SnapshotManifest:
        """Lock a snapshot for the backtest.

        Args:
            snapshot_id: Existing snapshot ID, or None to create new

        Returns:
            Locked SnapshotManifest
        """
        if snapshot_id:
            snapshot = self._version_manager.get_snapshot(snapshot_id)
            if snapshot is None:
                raise PITViolationError(f"Snapshot {snapshot_id} not found")
            logger.info(f"Locked existing snapshot: {snapshot_id}")
        else:
            # Create new snapshot
            tag = f"backtest_{uuid.uuid4().hex[:8]}"
            snapshot = self._version_manager.create_snapshot(
                tag, datasets=["crsp", "compustat"]
            )
            logger.info(f"Created new snapshot: {tag}")

        self._snapshot = snapshot
        return snapshot

    def _get_pit_prices(self, as_of_date: date) -> pl.DataFrame:
        """Get prices strictly from snapshot with date filter.

        FAIL-FAST: Raises PITViolationError if date exceeds snapshot.
        """
        self._ensure_snapshot_locked()
        assert self._snapshot is not None  # Guaranteed by _ensure_snapshot_locked
        crsp_snapshot = self._snapshot.datasets.get("crsp")

        if crsp_snapshot is None:
            raise PITViolationError("CRSP not in snapshot")

        # Hard cutoff: snapshot date range
        if as_of_date > crsp_snapshot.date_range_end:
            raise PITViolationError(
                f"Requested {as_of_date} but snapshot ends {crsp_snapshot.date_range_end}"
            )

        # Use cached prices if available
        if self._prices_cache is None:
            # Get data path from snapshot
            # Use scan_parquet for lazy loading, then collect with filter
            data_path = self._get_snapshot_data_path("crsp")
            self._prices_cache = pl.scan_parquet(data_path).collect()

        # Strict date filter: only data known at as_of_date
        return self._prices_cache.filter(pl.col("date") <= as_of_date)

    def _get_pit_fundamentals(self, as_of_date: date) -> pl.DataFrame | None:
        """Get fundamentals with filing lag from snapshot.

        Compustat data uses 90-day filing lag for PIT correctness.
        """
        self._ensure_snapshot_locked()
        assert self._snapshot is not None  # Guaranteed by _ensure_snapshot_locked
        compustat_snapshot = self._snapshot.datasets.get("compustat")

        if compustat_snapshot is None:
            logger.warning("Compustat not in snapshot, returning None")
            return None

        # Use cached fundamentals if available
        if self._fundamentals_cache is None:
            data_path = self._get_snapshot_data_path("compustat")
            self._fundamentals_cache = pl.scan_parquet(data_path).collect()

        # PIT filter: only data where datadate + 90 days <= as_of_date
        filing_lag_days = 90
        cutoff_datadate = as_of_date - timedelta(days=filing_lag_days)

        return self._fundamentals_cache.filter(
            pl.col("datadate") <= cutoff_datadate
        )

    def _get_pit_forward_returns(
        self, as_of_date: date, horizon: int = 1
    ) -> pl.DataFrame:
        """Get forward returns from snapshot.

        FAIL-FAST: Raises MissingForwardReturnError if horizon exceeds snapshot.

        Uses geometric compounding for accurate return calculation:
        forward_return = (1 + r1) * (1 + r2) * ... * (1 + rh) - 1
        """
        self._ensure_snapshot_locked()
        assert self._snapshot is not None  # Guaranteed by _ensure_snapshot_locked
        crsp_snapshot = self._snapshot.datasets.get("crsp")

        if crsp_snapshot is None:
            raise PITViolationError("CRSP not in snapshot")

        # Get all prices up to snapshot end
        prices = self._get_pit_prices(crsp_snapshot.date_range_end)

        # Get trading calendar to find exact horizon trading days
        future_dates = (
            prices.filter(pl.col("date") > as_of_date)
            .select("date")
            .unique()
            .sort("date")
            .head(horizon + 5)  # Buffer for safety
            .to_series()
            .to_list()
        )

        if len(future_dates) < horizon:
            raise MissingForwardReturnError(
                f"Only {len(future_dates)} trading days after {as_of_date}, "
                f"need at least {horizon}. Reduce backtest end_date or horizon."
            )

        # Target date is exactly 'horizon' trading days forward
        target_date = future_dates[horizon - 1]

        # Filter returns for the exact horizon period
        forward_data = prices.filter(
            (pl.col("date") > as_of_date) & (pl.col("date") <= target_date)
        )

        # Compute geometric return: (1 + r1) * (1 + r2) * ... - 1
        forward_returns = (
            forward_data.group_by("permno")
            .agg([
                ((pl.col("ret") + 1).product() - 1).alias("return"),
                pl.col("ret").count().alias("n_days"),
            ])
            # Require exact horizon observations to avoid biased returns
            .filter(pl.col("n_days") == horizon)
            .select(["permno", "return"])
        )

        # Add date column and format
        returns = forward_returns.with_columns([
            pl.lit(as_of_date).alias("date"),
        ]).select(["permno", "date", "return"])

        return returns

    def _get_snapshot_data_path(self, dataset: str) -> Path:
        """Get data path for dataset from snapshot."""
        self._ensure_snapshot_locked()
        assert self._snapshot is not None  # Guaranteed by _ensure_snapshot_locked
        ds_snapshot = self._snapshot.datasets.get(dataset)

        if ds_snapshot is None:
            raise PITViolationError(f"{dataset} not in snapshot")

        # Construct path based on version manager's storage
        # This depends on DatasetVersionManager implementation
        base_path = Path("data/snapshots") / self._snapshot.version_tag / dataset
        return base_path

    def _get_trading_calendar(
        self, start_date: date, end_date: date
    ) -> list[date]:
        """Get trading days from snapshot data."""
        self._ensure_snapshot_locked()
        prices = self._get_pit_prices(end_date)

        trading_days = (
            prices.filter(
                (pl.col("date") >= start_date) & (pl.col("date") <= end_date)
            )
            .select("date")
            .unique()
            .sort("date")
            .to_series()
            .to_list()
        )

        return trading_days

    def run_backtest(
        self,
        alpha: AlphaDefinition,
        start_date: date,
        end_date: date,
        snapshot_id: str | None = None,
        weight_method: Literal["zscore", "quantile", "rank"] = "zscore",
        decay_horizons: list[int] | None = None,
        batch_size: int = 252,
    ) -> BacktestResult:
        """Run PIT-correct backtest.

        Note: Execution is single-threaded to ensure PIT snapshot consistency.

        Args:
            alpha: Alpha signal definition
            start_date: Backtest start date
            end_date: Backtest end date
            snapshot_id: Existing snapshot ID (None = create new)
            weight_method: Signal-to-weight conversion method
            decay_horizons: Horizons for decay curve (default [1, 2, 5, 10, 20, 60])
            batch_size: Days per batch for memory efficiency

        Returns:
            BacktestResult with full analysis
        """
        if decay_horizons is None:
            decay_horizons = [1, 2, 5, 10, 20, 60]

        backtest_id = str(uuid.uuid4())
        logger.info(
            f"Starting backtest {backtest_id} for {alpha.name} "
            f"from {start_date} to {end_date}"
        )

        # Lock snapshot
        snapshot = self._lock_snapshot(snapshot_id)

        # Link backtest to snapshot for reproducibility
        self._version_manager.link_backtest(backtest_id, snapshot.version_tag)

        # Get trading calendar
        trading_days = self._get_trading_calendar(start_date, end_date)
        logger.info(f"Found {len(trading_days)} trading days")

        if not trading_days:
            raise PITViolationError(
                f"No trading days found between {start_date} and {end_date}"
            )

        # Batch processing
        all_signals: list[pl.DataFrame] = []
        all_returns: list[pl.DataFrame] = []
        backtest_stopped = False

        batches = [
            trading_days[i : i + batch_size]
            for i in range(0, len(trading_days), batch_size)
        ]

        for batch_idx, batch in enumerate(batches):
            if backtest_stopped:
                break

            logger.info(f"Processing batch {batch_idx + 1}/{len(batches)}")

            for as_of_date in batch:
                try:
                    # Get PIT data
                    prices = self._get_pit_prices(as_of_date)
                    fundamentals = self._get_pit_fundamentals(as_of_date)

                    # Get forward returns FIRST to ensure we can compute IC
                    # If this fails, don't add signal (keep signals/returns aligned)
                    fwd_returns = self._get_pit_forward_returns(as_of_date, horizon=1)

                    # Only compute signal if forward returns are available
                    signal = alpha.compute(prices, fundamentals, as_of_date)

                    # Both succeeded - add to results
                    all_signals.append(signal)
                    all_returns.append(fwd_returns)

                except MissingForwardReturnError:
                    # Stop COMPLETELY if we can't compute forward returns
                    # This prevents signals/returns misalignment
                    logger.warning(
                        f"Stopping backtest at {as_of_date}: forward returns unavailable"
                    )
                    backtest_stopped = True
                    break

        # Concatenate results
        if not all_signals:
            raise PITViolationError("No signals computed")

        daily_signals = pl.concat(all_signals)
        daily_returns = pl.concat(all_returns)

        # Compute daily IC
        daily_ic = self._compute_daily_ic(daily_signals, daily_returns)

        # Summary metrics
        # Use average of daily ICs (not pooled IC across all dates)
        mean_ic_value = daily_ic.select(pl.col("rank_ic").mean()).item()
        if mean_ic_value is None:
            mean_ic_value = float("nan")

        icir_result = self._metrics.compute_icir(daily_ic)
        hit_rate = self._metrics.compute_hit_rate(daily_signals, daily_returns)

        # Coverage: average daily coverage (fraction of universe with valid signal per day)
        # Compute per-date coverage then average to avoid scaling with backtest duration
        daily_coverage = (
            daily_signals.group_by("date")
            .agg([
                pl.col("signal").is_not_null().sum().alias("valid_count"),
                pl.col("signal").count().alias("total_count"),
            ])
            .with_columns([
                (pl.col("valid_count") / pl.col("total_count")).alias("daily_cov")
            ])
        )
        coverage = daily_coverage.select(pl.col("daily_cov").mean()).item()
        if coverage is None:
            coverage = 0.0
        long_short = self._metrics.compute_long_short_spread(daily_signals, daily_returns)

        # Autocorrelation (need cross-sectional mean signal per date)
        mean_signal_ts = daily_signals.group_by("date").agg(
            pl.col("signal").mean().alias("signal")
        )
        autocorr = self._metrics.compute_autocorrelation(mean_signal_ts)

        # Compute weights and turnover
        weight_converter = SignalToWeight(method=weight_method)
        daily_weights = weight_converter.convert(daily_signals)

        turnover_calc = TurnoverCalculator()
        turnover_result = turnover_calc.compute_turnover_result(daily_weights)

        # Decay curve
        returns_by_horizon = {}
        for horizon in decay_horizons:
            try:
                horizon_returns = self._compute_horizon_returns(
                    trading_days[0], horizon
                )
                returns_by_horizon[horizon] = horizon_returns
            except MissingForwardReturnError:
                logger.warning(f"Skipping decay horizon {horizon}: data unavailable")
                continue

        decay_result = self._metrics.compute_decay_curve(
            daily_signals, returns_by_horizon
        )

        # Build result
        result = BacktestResult(
            alpha_name=alpha.name,
            backtest_id=backtest_id,
            start_date=start_date,
            end_date=end_date,
            snapshot_id=snapshot.version_tag,
            dataset_version_ids={
                ds: str(snapshot.datasets[ds].sync_manifest_version)
                for ds in snapshot.datasets
            },
            daily_signals=daily_signals,
            daily_ic=daily_ic,
            mean_ic=mean_ic_value,
            icir=icir_result.icir,
            hit_rate=hit_rate,
            coverage=coverage,
            long_short_spread=long_short,
            autocorrelation=autocorr,
            weight_method=weight_method,
            daily_weights=daily_weights,
            turnover_result=turnover_result,
            decay_curve=decay_result.decay_curve,
            decay_half_life=decay_result.half_life,
            n_days=len(trading_days),
            n_symbols_avg=daily_signals.group_by("date").len().select(
                pl.col("len").mean()
            ).item() or 0.0,
        )

        logger.info(
            f"Backtest complete: ICIR={result.icir:.2f}, "
            f"Turnover={result.average_turnover:.2%}"
        )

        # Clear caches
        self._snapshot = None
        self._prices_cache = None
        self._fundamentals_cache = None

        return result

    def _compute_daily_ic(
        self, signals: pl.DataFrame, returns: pl.DataFrame
    ) -> pl.DataFrame:
        """Compute daily IC time series."""
        dates = signals.select("date").unique().sort("date").to_series().to_list()
        results = []

        for d in dates:
            day_signals = signals.filter(pl.col("date") == d)
            day_returns = returns.filter(pl.col("date") == d)

            if day_signals.height < 30:
                continue

            ic_result = self._metrics.compute_ic(day_signals, day_returns)
            results.append({
                "date": d,
                "ic": ic_result.pearson_ic,
                "rank_ic": ic_result.rank_ic,
            })

        if not results:
            return pl.DataFrame(
                schema={"date": pl.Date, "ic": pl.Float64, "rank_ic": pl.Float64}
            )

        return pl.DataFrame(results)

    def _compute_horizon_returns(
        self, base_date: date, horizon: int
    ) -> pl.DataFrame:
        """Compute returns at specific horizon for all trading dates.

        For each date in the backtest, computes the forward return over
        exactly 'horizon' trading days using geometric compounding.
        """
        self._ensure_snapshot_locked()
        assert self._snapshot is not None  # Guaranteed by _ensure_snapshot_locked

        prices = self._get_pit_prices(self._snapshot.datasets["crsp"].date_range_end)

        # Get all unique dates
        all_dates = (
            prices.filter(pl.col("date") >= base_date)
            .select("date")
            .unique()
            .sort("date")
            .to_series()
            .to_list()
        )

        if len(all_dates) <= horizon:
            raise MissingForwardReturnError(
                f"Only {len(all_dates)} trading days from {base_date}, "
                f"need at least {horizon + 1} for horizon {horizon}."
            )

        results = []
        # For each date, compute forward return at this horizon
        for i, as_of_date in enumerate(all_dates[:-horizon]):
            target_date = all_dates[i + horizon]

            # Get returns for this specific horizon
            forward_data = prices.filter(
                (pl.col("date") > as_of_date) & (pl.col("date") <= target_date)
            )

            # Geometric compounding with min-count filter
            horizon_returns = (
                forward_data.group_by("permno")
                .agg([
                    ((pl.col("ret") + 1).product() - 1).alias("return"),
                    pl.col("ret").count().alias("n_days"),
                ])
                # Require exact horizon observations to avoid inflated IC from empty products
                .filter(pl.col("n_days") == horizon)
                .with_columns([pl.lit(as_of_date).alias("date")])
                .select(["permno", "date", "return"])
            )

            results.append(horizon_returns)

        if not results:
            return pl.DataFrame(
                schema={"permno": pl.Int64, "date": pl.Date, "return": pl.Float64}
            )

        return pl.concat(results).select(["permno", "date", "return"])
