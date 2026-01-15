"""Market microstructure analytics using TAQ tick data.

Implements:
- Realized volatility calculation (5-min sampling)
- VPIN (Volume-synchronized PIN) using Bulk Volume Classification (BVC)
- Intraday volatility patterns (U-shape analysis)
- Spread and depth statistics with data quality flags

All outputs include dataset_version_id for reproducibility and PIT support.
"""

from __future__ import annotations

import hashlib
import logging
import math
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from numbers import Real
from typing import TYPE_CHECKING, Any

import numpy as np
import polars as pl
from numpy.lib.stride_tricks import sliding_window_view
from numpy.typing import NDArray
from scipy.stats import norm  # type: ignore[import-untyped]

try:  # Optional acceleration path
    from numba import List as NumbaList
    from numba import njit

    NUMBA_AVAILABLE = True
except Exception:  # pragma: no cover - fallback when numba not installed
    # JUSTIFIED: Numba is an optional dependency for performance acceleration.
    # Graceful fallback to pure Python implementation when unavailable.
    NUMBA_AVAILABLE = False
    njit = None

from libs.data.data_quality.exceptions import DataNotFoundError

if TYPE_CHECKING:
    from libs.data.data_providers.taq_query_provider import TAQLocalProvider

logger = logging.getLogger(__name__)


def _resolve_mean(frame: pl.DataFrame) -> float:
    """Resolve Polars mean output to a float or NaN for type safety."""
    mean_value = frame["vpin"].mean()
    if isinstance(mean_value, Real):
        return float(mean_value)
    return float("nan")


@dataclass
class MicrostructureResult:
    """Base result with versioning metadata."""

    dataset_version_id: str
    dataset_versions: dict[str, str] | None
    computation_timestamp: datetime
    as_of_date: date | None


@dataclass
class CompositeVersionInfo:
    """Version info for methods that use multiple datasets."""

    versions: dict[str, str]
    snapshot_id: str | None
    is_pit: bool

    @property
    def composite_version_id(self) -> str:
        """Deterministic composite version ID using SHA256."""
        parts = [f"{ds}:{v}" for ds, v in sorted(self.versions.items())]
        if self.snapshot_id:
            parts.append(f"snapshot:{self.snapshot_id}")
        combined = "|".join(parts)
        return hashlib.sha256(combined.encode()).hexdigest()[:32]


@dataclass
class RealizedVolatilityResult(MicrostructureResult):
    """Result of realized volatility computation."""

    symbol: str
    date: date
    rv_daily: float
    rv_annualized: float
    sampling_freq_minutes: int
    num_observations: int


@dataclass
class VPINResult(MicrostructureResult):
    """Result of VPIN computation."""

    symbol: str
    date: date
    data: pl.DataFrame
    num_buckets: int
    num_valid_vpin: int
    avg_vpin: float
    warnings: list[str] = field(default_factory=list)


@dataclass
class IntradayPatternResult(MicrostructureResult):
    """Result of intraday pattern analysis."""

    symbol: str
    start_date: date
    end_date: date
    data: pl.DataFrame


@dataclass
class SpreadDepthResult(MicrostructureResult):
    """Result of spread and depth statistics computation."""

    symbol: str
    date: date
    qwap_spread: float
    ewas: float
    avg_bid_depth: float
    avg_ask_depth: float
    avg_total_depth: float
    depth_imbalance: float
    quotes: int
    trades: int
    has_locked_markets: bool
    has_crossed_markets: bool
    locked_pct: float
    crossed_pct: float
    stale_quote_pct: float
    depth_is_estimated: bool


class MicrostructureAnalyzer:
    """Analyze market microstructure from TAQ data.

    This class uses taq_provider as the SINGLE SOURCE for both data
    and version resolution to avoid manifest/version divergence.
    """

    DATASET_1MIN = "taq_1min_bars"
    DATASET_DAILY_RV = "taq_daily_rv"
    DATASET_SPREAD_STATS = "taq_spread_stats"

    def __init__(self, taq_provider: TAQLocalProvider) -> None:
        """Initialize with TAQ data provider.

        Args:
            taq_provider: TAQLocalProvider instance for data access and version resolution.
        """
        self.taq = taq_provider

    def _get_version_id(self, dataset: str, as_of: date | None = None) -> str:
        """Get dataset version ID from taq_provider.

        Args:
            dataset: Dataset name.
            as_of: Point-in-time date for snapshot query.

        Returns:
            Version ID string.

        Raises:
            ValueError: If as_of provided but version_manager not configured.
            DataNotFoundError: If dataset not found in snapshot.
        """
        if as_of:
            if self.taq.version_manager is None:
                raise ValueError("version_manager required for PIT queries")
            _path, snapshot = self.taq.version_manager.query_as_of(dataset, as_of)
            if dataset not in snapshot.datasets:
                raise DataNotFoundError(f"Dataset '{dataset}' not found in snapshot at {as_of}")
            return str(snapshot.datasets[dataset].sync_manifest_version)
        else:
            manifest = self.taq.manifest_manager.load_manifest(dataset)
            return manifest.checksum if manifest else "unknown"

    def _get_multi_version_id(
        self,
        datasets: list[str],
        as_of: date | None = None,
    ) -> CompositeVersionInfo:
        """Get version IDs for multiple datasets from SINGLE SNAPSHOT.

        Args:
            datasets: List of dataset names.
            as_of: Point-in-time date for snapshot query.

        Returns:
            CompositeVersionInfo with version IDs and snapshot metadata.

        Raises:
            ValueError: If as_of provided but version_manager not configured.
            DataNotFoundError: If any dataset not found in snapshot.
        """
        if as_of:
            if self.taq.version_manager is None:
                raise ValueError("version_manager required for PIT queries")

            _path, snapshot = self.taq.version_manager.query_as_of(datasets[0], as_of)

            versions = {}
            for ds in datasets:
                if ds not in snapshot.datasets:
                    raise DataNotFoundError(
                        f"Dataset '{ds}' not found in snapshot at {as_of}. "
                        f"Available: {list(snapshot.datasets.keys())}"
                    )
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

    def compute_realized_volatility(
        self,
        symbol: str,
        target_date: date,
        sampling_freq_minutes: int = 5,
        as_of: date | None = None,
    ) -> RealizedVolatilityResult:
        """Compute realized volatility for a symbol on a given date.

        Args:
            symbol: Stock symbol (e.g., "AAPL").
            target_date: Date to compute RV for.
            sampling_freq_minutes: Sampling frequency in minutes (default: 5).
            as_of: Point-in-time date for snapshot query.

        Returns:
            RealizedVolatilityResult with daily and annualized RV.
        """
        symbol = symbol.upper()

        if sampling_freq_minutes in (5, 30):
            try:
                rv_df = self.taq.fetch_realized_volatility(
                    symbols=[symbol],
                    start_date=target_date,
                    end_date=target_date,
                    window=sampling_freq_minutes,
                    as_of=as_of,
                )
                if not rv_df.is_empty():
                    rv_row = rv_df.row(0, named=True)
                    rv_daily = rv_row["rv"]
                    version_id = self._get_version_id(self.DATASET_DAILY_RV, as_of)

                    return RealizedVolatilityResult(
                        dataset_version_id=version_id,
                        dataset_versions=None,
                        computation_timestamp=datetime.now(UTC),
                        as_of_date=as_of,
                        symbol=symbol,
                        date=target_date,
                        rv_daily=rv_daily,
                        rv_annualized=rv_daily * math.sqrt(252),
                        sampling_freq_minutes=sampling_freq_minutes,
                        num_observations=rv_row.get("obs", 0),
                    )
            except (DataNotFoundError, KeyError) as e:
                logger.debug(
                    "Pre-computed RV not available for %s, will compute from bars: %s",
                    symbol,
                    e,
                )

        bars_df = self.taq.fetch_minute_bars(
            symbols=[symbol],
            start_date=target_date,
            end_date=target_date,
            as_of=as_of,
        )

        if bars_df.is_empty() or bars_df.height < 10:
            version_id = self._get_version_id(self.DATASET_1MIN, as_of)
            logger.warning(
                "Insufficient data for RV computation",
                extra={"symbol": symbol, "date": str(target_date), "rows": bars_df.height},
            )
            return RealizedVolatilityResult(
                dataset_version_id=version_id,
                dataset_versions=None,
                computation_timestamp=datetime.now(UTC),
                as_of_date=as_of,
                symbol=symbol,
                date=target_date,
                rv_daily=float("nan"),
                rv_annualized=float("nan"),
                sampling_freq_minutes=sampling_freq_minutes,
                num_observations=bars_df.height,
            )

        if sampling_freq_minutes > 1:
            bars_df = bars_df.filter(pl.col("ts").dt.minute() % sampling_freq_minutes == 0)

        prices = bars_df["close"].to_numpy()
        log_returns = np.diff(np.log(prices))

        rv_daily = float(np.sqrt(np.sum(log_returns**2)))
        rv_annualized = rv_daily * math.sqrt(252)

        version_id = self._get_version_id(self.DATASET_1MIN, as_of)

        return RealizedVolatilityResult(
            dataset_version_id=version_id,
            dataset_versions=None,
            computation_timestamp=datetime.now(UTC),
            as_of_date=as_of,
            symbol=symbol,
            date=target_date,
            rv_daily=rv_daily,
            rv_annualized=rv_annualized,
            sampling_freq_minutes=sampling_freq_minutes,
            num_observations=len(log_returns),
        )

    def compute_vpin(
        self,
        symbol: str,
        target_date: date,
        volume_per_bucket: int = 10000,
        window_buckets: int = 50,
        sigma_lookback: int = 20,
        as_of: date | None = None,
    ) -> VPINResult:
        """Compute VPIN using Bulk Volume Classification (BVC).

        Args:
            symbol: Stock symbol.
            target_date: Date to compute VPIN for.
            volume_per_bucket: Fixed volume per bucket (default: 10000).
            window_buckets: Rolling window of buckets for VPIN (default: 50).
            sigma_lookback: Number of trades for rolling sigma (default: 20).
            as_of: Point-in-time date for snapshot query.

        Returns:
            VPINResult with bucket-level VPIN data.
        """
        symbol = symbol.upper()
        warnings: list[str] = []

        dataset_name = f"taq_samples_{target_date.strftime('%Y%m%d')}"

        # Gracefully handle missing PIT snapshots - return empty result instead of raising
        try:
            version_id = self._get_version_id(dataset_name, as_of)
        except DataNotFoundError as e:
            warnings.append(f"PIT snapshot unavailable: {e}")
            return VPINResult(
                dataset_version_id="snapshot_unavailable",
                dataset_versions=None,
                computation_timestamp=datetime.now(UTC),
                as_of_date=as_of,
                symbol=symbol,
                date=target_date,
                data=self._empty_vpin_df(),
                num_buckets=0,
                num_valid_vpin=0,
                avg_vpin=float("nan"),
                warnings=warnings,
            )

        # PIT-compliant tick loading: pass as_of for snapshot resolution
        ticks_df = self.taq.fetch_ticks(sample_date=target_date, symbols=[symbol], as_of=as_of)

        if ticks_df.is_empty():
            warnings.append("Empty day - no tick data")
            return VPINResult(
                dataset_version_id=version_id,
                dataset_versions=None,
                computation_timestamp=datetime.now(UTC),
                as_of_date=as_of,
                symbol=symbol,
                date=target_date,
                data=self._empty_vpin_df(),
                num_buckets=0,
                num_valid_vpin=0,
                avg_vpin=float("nan"),
                warnings=warnings,
            )

        ticks_df = ticks_df.sort("ts")

        trade_rows = ticks_df.filter(pl.col("trade_size") > 0)
        if trade_rows.is_empty():
            warnings.append("No valid trades")
            return VPINResult(
                dataset_version_id=version_id,
                dataset_versions=None,
                computation_timestamp=datetime.now(UTC),
                as_of_date=as_of,
                symbol=symbol,
                date=target_date,
                data=self._empty_vpin_df(),
                num_buckets=0,
                num_valid_vpin=0,
                avg_vpin=float("nan"),
                warnings=warnings,
            )

        prices = trade_rows["trade_px"].to_numpy()
        sizes = trade_rows["trade_size"].to_numpy()
        timestamps = trade_rows["ts"].to_list()

        zero_vol_count = np.sum(sizes == 0)
        if zero_vol_count > 0:
            zero_vol_pct = zero_vol_count / len(sizes)
            if zero_vol_pct > 0.05:
                warnings.append(f">{zero_vol_pct*100:.1f}% zero-volume trades skipped")
            mask = sizes > 0
            prices = prices[mask]
            sizes = sizes[mask]
            timestamps = [t for t, m in zip(timestamps, mask, strict=False) if m]

        if len(prices) < sigma_lookback + 1:
            warnings.append("Day ended during warmup period - no valid buckets")
            return VPINResult(
                dataset_version_id=version_id,
                dataset_versions=None,
                computation_timestamp=datetime.now(UTC),
                as_of_date=as_of,
                symbol=symbol,
                date=target_date,
                data=self._empty_vpin_df(),
                num_buckets=0,
                num_valid_vpin=0,
                avg_vpin=float("nan"),
                warnings=warnings,
            )

        log_returns = np.log(prices[1:] / prices[:-1])

        buckets = self._compute_vpin_buckets(
            log_returns=log_returns,
            sizes=sizes[1:],
            timestamps=timestamps[1:],
            volume_per_bucket=volume_per_bucket,
            window_buckets=window_buckets,
            sigma_lookback=sigma_lookback,
            warnings=warnings,
        )

        if not buckets:
            warnings.append("No valid buckets constructed")
            return VPINResult(
                dataset_version_id=version_id,
                dataset_versions=None,
                computation_timestamp=datetime.now(UTC),
                as_of_date=as_of,
                symbol=symbol,
                date=target_date,
                data=self._empty_vpin_df(),
                num_buckets=0,
                num_valid_vpin=0,
                avg_vpin=float("nan"),
                warnings=warnings,
            )

        bucket_df = pl.DataFrame(buckets)
        valid_vpin = bucket_df.filter(~pl.col("vpin").is_nan())

        return VPINResult(
            dataset_version_id=version_id,
            dataset_versions=None,
            computation_timestamp=datetime.now(UTC),
            as_of_date=as_of,
            symbol=symbol,
            date=target_date,
            data=bucket_df,
            num_buckets=len(buckets),
            num_valid_vpin=valid_vpin.height,
            avg_vpin=_resolve_mean(valid_vpin) if valid_vpin.height > 0 else float("nan"),
            warnings=warnings,
        )

    def _compute_vpin_buckets(
        self,
        log_returns: np.ndarray[Any, np.dtype[np.floating[Any]]],
        sizes: np.ndarray[Any, np.dtype[np.floating[Any]]],
        timestamps: list[Any],
        volume_per_bucket: int,
        window_buckets: int,
        sigma_lookback: int,
        warnings: list[str],
    ) -> list[dict[str, Any]]:
        """Compute VPIN buckets using BVC method.

        Optimized implementation using vectorized pre-computation of rolling
        sigma and BVC probabilities to minimize per-iteration overhead.
        The bucket filling loop remains sequential due to volume split logic.
        """
        # Pre-compute rolling sigma using vectorized operations (PERFORMANCE CRITICAL)
        # This replaces O(n * sigma_lookback) with O(n) complexity
        sigma_arr = self._compute_rolling_sigma_vectorized(log_returns, sigma_lookback)

        # Pre-compute z-scores and BVC probabilities in batch (PERFORMANCE CRITICAL)
        # Handle sigma=0 cases: use 0.5 probability (neutral)
        sigma_zero_mask = sigma_arr <= 0
        has_sigma_zero = np.any(sigma_zero_mask)

        # Safe division: replace zero sigma with 1 to avoid division by zero, then fix
        safe_sigma = np.where(sigma_zero_mask, 1.0, sigma_arr)
        z_scores = log_returns / safe_sigma
        z_scores[sigma_zero_mask] = 0.0  # sigma=0 -> z=0 -> cdf(0)=0.5

        # Vectorized norm.cdf is much faster than per-element calls
        v_buy_ratios = norm.cdf(z_scores)
        v_buy_ratios[sigma_zero_mask] = 0.5  # sigma=0 means equal split

        # Pre-compute v_buy and v_sell for all trades
        v_buy_arr = sizes * v_buy_ratios
        v_sell_arr = sizes - v_buy_arr

        # Pre-compute cumulative volume for fast bucket cumsum lookups
        cumulative_volume = np.cumsum(sizes)

        if has_sigma_zero:
            warnings.append("sigma=0 detected")

        # Delegate bucket loop to numba-accelerated implementation when available
        if NUMBA_AVAILABLE:
            bucket_arrays, has_partial = _compute_vpin_buckets_numba(
                v_buy_arr,
                v_sell_arr,
                sizes,
                cumulative_volume,
                sigma_zero_mask,
                np.array(timestamps[sigma_lookback:], dtype="datetime64[ns]").astype("int64"),
                volume_per_bucket,
                window_buckets,
                sigma_lookback,
            )
            buckets = _bucket_arrays_to_dicts(bucket_arrays, timestamps)
        else:
            buckets, has_partial = self._compute_vpin_buckets_python(
                v_buy_arr,
                v_sell_arr,
                sizes,
                cumulative_volume,
                sigma_zero_mask,
                timestamps,
                volume_per_bucket,
                window_buckets,
                sigma_lookback,
            )

        if has_partial and "partial bucket at EOD" not in warnings:
            warnings.append("partial bucket at EOD")

        return buckets

    def _compute_vpin_buckets_python(
        self,
        v_buy_arr: np.ndarray[Any, np.dtype[np.floating[Any]]],
        v_sell_arr: np.ndarray[Any, np.dtype[np.floating[Any]]],
        sizes: np.ndarray[Any, np.dtype[np.floating[Any]]],
        cumulative_volume: np.ndarray[Any, np.dtype[np.floating[Any]]],
        sigma_zero_mask: np.ndarray[Any, np.dtype[np.bool_]],
        timestamps: list[Any],
        volume_per_bucket: int,
        window_buckets: int,
        sigma_lookback: int,
    ) -> tuple[list[dict[str, Any]], bool]:
        """Pure-Python fallback for VPIN bucketization (original implementation)."""

        buckets: list[dict[str, Any]] = []
        current_bucket_volume = 0.0
        current_bucket_v_buy = 0.0
        current_bucket_v_sell = 0.0
        current_bucket_ts = None
        sigma_zero_contaminated = False

        v_buy_history: list[float] = []
        v_sell_history: list[float] = []

        for i in range(sigma_lookback, len(v_buy_arr)):
            trade_size = float(sizes[i])
            trade_ts = timestamps[i]
            v_buy = float(v_buy_arr[i])
            v_sell = float(v_sell_arr[i])

            if sigma_zero_mask[i]:
                sigma_zero_contaminated = True

            remaining_volume = trade_size
            remaining_v_buy = v_buy
            remaining_v_sell = v_sell

            while remaining_volume > 0:
                remaining_capacity = volume_per_bucket - current_bucket_volume

                if remaining_volume <= remaining_capacity:
                    current_bucket_volume += remaining_volume
                    current_bucket_v_buy += remaining_v_buy
                    current_bucket_v_sell += remaining_v_sell
                    current_bucket_ts = trade_ts
                    remaining_volume = 0
                else:
                    if remaining_capacity > 0:
                        split_ratio = remaining_capacity / remaining_volume
                        current_bucket_volume += remaining_capacity
                        current_bucket_v_buy += remaining_v_buy * split_ratio
                        current_bucket_v_sell += remaining_v_sell * split_ratio
                        current_bucket_ts = trade_ts

                        remaining_volume -= remaining_capacity
                        remaining_v_buy *= 1 - split_ratio
                        remaining_v_sell *= 1 - split_ratio

                    v_buy_history.append(current_bucket_v_buy)
                    v_sell_history.append(current_bucket_v_sell)

                    vpin_val = self._compute_vpin_value(
                        v_buy_history,
                        v_sell_history,
                        window_buckets,
                        sigma_zero_contaminated,
                    )

                    buckets.append(
                        {
                            "bucket_id": len(buckets),
                            "vpin": vpin_val,
                            "cumulative_volume": float(cumulative_volume[i]),
                            "imbalance": abs(current_bucket_v_buy - current_bucket_v_sell),
                            "timestamp": current_bucket_ts,
                            "is_partial": False,
                            "is_warmup": len(buckets) < window_buckets - 1,
                        }
                    )

                    current_bucket_volume = 0.0
                    current_bucket_v_buy = 0.0
                    current_bucket_v_sell = 0.0
                    sigma_zero_contaminated = False

        has_partial = False
        if current_bucket_volume > 0:
            v_buy_history.append(current_bucket_v_buy)
            v_sell_history.append(current_bucket_v_sell)

            vpin_val = self._compute_vpin_value(
                v_buy_history, v_sell_history, window_buckets, sigma_zero_contaminated
            )

            buckets.append(
                {
                    "bucket_id": len(buckets),
                    "vpin": vpin_val,
                    "cumulative_volume": float(cumulative_volume[-1]) if len(sizes) > 0 else 0.0,
                    "imbalance": abs(current_bucket_v_buy - current_bucket_v_sell),
                    "timestamp": current_bucket_ts,
                    "is_partial": True,
                    "is_warmup": len(buckets) < window_buckets - 1,
                }
            )
            has_partial = True

        return buckets, has_partial

    def _compute_rolling_sigma_vectorized(
        self,
        log_returns: np.ndarray[Any, np.dtype[np.floating[Any]]],
        lookback: int,
    ) -> np.ndarray[Any, np.dtype[np.floating[Any]]]:
        """Compute rolling standard deviation using fully vectorized operations.

        Uses cumulative sums for O(n) complexity instead of O(n * lookback).
        This is approximately 100x faster than the naive loop approach for
        typical tick data sizes (500k-1M rows).
        """
        n = len(log_returns)
        sigma_arr = np.zeros(n, dtype=np.float64)

        if n < lookback or lookback < 2:
            return sigma_arr

        # Two-pass approach using sliding windows to avoid catastrophic cancellation
        windows = sliding_window_view(log_returns.astype(np.float64), lookback)
        window_variances = windows.var(axis=1, ddof=1)

        sigma_arr[lookback - 1 :] = np.sqrt(np.maximum(window_variances, 0.0))

        return sigma_arr

    def _compute_vpin_value(
        self,
        v_buy_history: list[float],
        v_sell_history: list[float],
        window_buckets: int,
        sigma_zero_contaminated: bool,
    ) -> float:
        """Compute VPIN value from bucket history."""
        if sigma_zero_contaminated:
            return float("nan")

        if len(v_buy_history) < window_buckets:
            return float("nan")

        window_v_buy = sum(v_buy_history[-window_buckets:])
        window_v_sell = sum(v_sell_history[-window_buckets:])
        total_volume = window_v_buy + window_v_sell

        if total_volume <= 0:
            return float("nan")

        return abs(window_v_buy - window_v_sell) / total_volume

    def _empty_vpin_df(self) -> pl.DataFrame:
        """Return empty VPIN DataFrame with correct schema."""
        return pl.DataFrame(
            schema={
                "bucket_id": pl.Int64,
                "vpin": pl.Float64,
                "cumulative_volume": pl.Float64,
                "imbalance": pl.Float64,
                "timestamp": pl.Datetime,
                "is_partial": pl.Boolean,
                "is_warmup": pl.Boolean,
            }
        )

    def analyze_intraday_pattern(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
        bucket_minutes: int = 30,
        as_of: date | None = None,
    ) -> IntradayPatternResult:
        """Analyze average intraday volatility pattern.

        Args:
            symbol: Stock symbol.
            start_date: Start date of analysis period.
            end_date: End date of analysis period.
            bucket_minutes: Time bucket size in minutes (default: 30).
            as_of: Point-in-time date for snapshot query.

        Returns:
            IntradayPatternResult with time-bucket averages.
        """
        symbol = symbol.upper()
        version_id = self._get_version_id(self.DATASET_1MIN, as_of)

        bars_df = self.taq.fetch_minute_bars(
            symbols=[symbol],
            start_date=start_date,
            end_date=end_date,
            as_of=as_of,
        )

        if bars_df.is_empty():
            return IntradayPatternResult(
                dataset_version_id=version_id,
                dataset_versions=None,
                computation_timestamp=datetime.now(UTC),
                as_of_date=as_of,
                symbol=symbol,
                start_date=start_date,
                end_date=end_date,
                data=pl.DataFrame(
                    schema={
                        "time_bucket": pl.Time,
                        "avg_volatility": pl.Float64,
                        "avg_spread": pl.Float64,
                        "avg_volume": pl.Float64,
                        "n_days": pl.Int64,
                    }
                ),
            )

        bars_df = bars_df.with_columns(
            [
                pl.col("ts").dt.time().alias("time_of_day"),
                (
                    pl.col("ts").dt.hour().cast(pl.Int32) * 60
                    + pl.col("ts").dt.minute().cast(pl.Int32)
                ).alias("minute_of_day"),
            ]
        )

        bars_df = bars_df.with_columns(
            [
                ((pl.col("minute_of_day") // bucket_minutes) * bucket_minutes)
                .cast(pl.Int32)
                .alias("bucket_minute"),
            ]
        )

        bars_df = bars_df.with_columns(
            [
                ((pl.col("high") - pl.col("low")) / pl.col("open")).alias("intrabar_vol"),
            ]
        )

        pattern_df = (
            bars_df.group_by("bucket_minute")
            .agg(
                [
                    pl.col("intrabar_vol").mean().alias("avg_volatility"),
                    ((pl.col("high") - pl.col("low")) / 2).mean().alias("avg_spread"),
                    pl.col("volume").mean().alias("avg_volume"),
                    pl.col("date").n_unique().alias("n_days"),
                ]
            )
            .sort("bucket_minute")
        )

        pattern_df = pattern_df.with_columns(
            [
                pl.time(
                    hour=pl.col("bucket_minute").cast(pl.Int32) // 60,
                    minute=pl.col("bucket_minute").cast(pl.Int32) % 60,
                ).alias("time_bucket"),
            ]
        )

        pattern_df = pattern_df.select(
            [
                "time_bucket",
                "avg_volatility",
                "avg_spread",
                "avg_volume",
                "n_days",
            ]
        )

        return IntradayPatternResult(
            dataset_version_id=version_id,
            dataset_versions=None,
            computation_timestamp=datetime.now(UTC),
            as_of_date=as_of,
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            data=pattern_df,
        )

    def compute_spread_depth_stats(
        self,
        symbol: str,
        target_date: date,
        stale_threshold_seconds: int = 60,
        as_of: date | None = None,
    ) -> SpreadDepthResult:
        """Compute spread and depth statistics.

        Args:
            symbol: Stock symbol.
            target_date: Date to compute stats for.
            stale_threshold_seconds: Threshold for stale quote detection (default: 60).
            as_of: Point-in-time date for snapshot query.

        Returns:
            SpreadDepthResult with spread metrics and depth statistics.
        """
        symbol = symbol.upper()
        dataset_samples = f"taq_samples_{target_date.strftime('%Y%m%d')}"

        version_info = self._get_multi_version_id(
            [self.DATASET_SPREAD_STATS, dataset_samples], as_of
        )

        spread_df = self.taq.fetch_spread_metrics(
            symbols=[symbol],
            start_date=target_date,
            end_date=target_date,
            as_of=as_of,
        )

        if spread_df.is_empty():
            raise DataNotFoundError(f"No spread stats found for {symbol} on {target_date}")

        spread_row = spread_df.row(0, named=True)
        qwap_spread = spread_row["qwap_spread"]
        ewas = spread_row["ewas"]
        quotes_count = spread_row.get("quotes", 0)
        trades_count = spread_row.get("trades", 0)

        depth_is_estimated = False
        avg_bid_depth = float("nan")
        avg_ask_depth = float("nan")
        has_locked = False
        has_crossed = False
        locked_pct = 0.0
        crossed_pct = 0.0
        stale_quote_pct = 0.0

        try:
            # PIT-compliant tick loading: pass as_of for snapshot resolution
            ticks_df = self.taq.fetch_ticks(sample_date=target_date, symbols=[symbol], as_of=as_of)

            if not ticks_df.is_empty():
                quotes_df = self._filter_quotes(ticks_df)

                if not quotes_df.is_empty():
                    avg_bid_depth, avg_ask_depth = self._compute_depth_from_ticks(quotes_df)

                    has_locked, locked_pct = self._detect_locked_markets(quotes_df)
                    has_crossed, crossed_pct = self._detect_crossed_markets(quotes_df)
                    stale_quote_pct = self._compute_stale_quote_pct(
                        quotes_df, stale_threshold_seconds
                    )

                    if stale_quote_pct > 0.50:
                        logger.warning(
                            "High stale quote percentage",
                            extra={
                                "symbol": symbol,
                                "date": str(target_date),
                                "stale_pct": stale_quote_pct,
                            },
                        )
                else:
                    depth_is_estimated = True
            else:
                depth_is_estimated = True

        except DataNotFoundError:
            depth_is_estimated = True

        avg_total_depth = avg_bid_depth + avg_ask_depth

        if math.isnan(avg_bid_depth) or math.isnan(avg_ask_depth):
            depth_is_estimated = True
            depth_imbalance = float("nan")
        elif avg_total_depth > 0:
            depth_imbalance = (avg_bid_depth - avg_ask_depth) / avg_total_depth
        else:
            depth_is_estimated = True
            depth_imbalance = float("nan")

        return SpreadDepthResult(
            dataset_version_id=version_info.composite_version_id,
            dataset_versions=version_info.versions,
            computation_timestamp=datetime.now(UTC),
            as_of_date=as_of,
            symbol=symbol,
            date=target_date,
            qwap_spread=qwap_spread,
            ewas=ewas,
            avg_bid_depth=avg_bid_depth,
            avg_ask_depth=avg_ask_depth,
            avg_total_depth=avg_total_depth,
            depth_imbalance=depth_imbalance,
            quotes=quotes_count,
            trades=trades_count,
            has_locked_markets=has_locked,
            has_crossed_markets=has_crossed,
            locked_pct=locked_pct,
            crossed_pct=crossed_pct,
            stale_quote_pct=stale_quote_pct,
            depth_is_estimated=depth_is_estimated,
        )

    def _filter_quotes(self, ticks_df: pl.DataFrame) -> pl.DataFrame:
        """Filter tick data to quote-only rows using hierarchical logic."""
        if "record_type" in ticks_df.columns:
            return ticks_df.filter(pl.col("record_type") == "quote")

        return ticks_df.filter((pl.col("bid_size") > 0) & (pl.col("ask_size") > 0))

    def _compute_depth_from_ticks(self, quotes_df: pl.DataFrame) -> tuple[float, float]:
        """Compute time-weighted L1 depth from quote data."""
        quotes_df = quotes_df.filter(
            (pl.col("bid") > 0) & (pl.col("ask") > 0) & (pl.col("bid") <= pl.col("ask"))
        )

        if quotes_df.is_empty():
            return float("nan"), float("nan")

        quotes_df = quotes_df.sort("ts")
        quotes_df = quotes_df.with_columns(
            [
                pl.col("ts").shift(-1).alias("next_ts"),
            ]
        )

        quotes_df = quotes_df.with_columns(
            [
                (
                    pl.when(pl.col("next_ts").is_null())
                    .then(pl.duration(seconds=1))
                    .otherwise(pl.col("next_ts") - pl.col("ts"))
                ).alias("duration"),
            ]
        )

        quotes_df = quotes_df.with_columns(
            [
                pl.col("duration").dt.total_seconds().alias("duration_seconds"),
            ]
        )

        quotes_df = quotes_df.filter(pl.col("duration_seconds") > 0)

        if quotes_df.is_empty():
            return float("nan"), float("nan")

        total_duration = quotes_df["duration_seconds"].sum()
        avg_bid = (quotes_df["bid_size"] * quotes_df["duration_seconds"]).sum() / total_duration
        avg_ask = (quotes_df["ask_size"] * quotes_df["duration_seconds"]).sum() / total_duration

        return float(avg_bid), float(avg_ask)

    def _detect_locked_markets(self, quotes_df: pl.DataFrame) -> tuple[bool, float]:
        """Detect locked markets (bid == ask)."""
        locked = quotes_df.filter(pl.col("bid") == pl.col("ask"))
        locked_pct = locked.height / quotes_df.height if quotes_df.height > 0 else 0.0
        return locked.height > 0, locked_pct

    def _detect_crossed_markets(self, quotes_df: pl.DataFrame) -> tuple[bool, float]:
        """Detect crossed markets (bid > ask)."""
        crossed = quotes_df.filter(pl.col("bid") > pl.col("ask"))
        crossed_pct = crossed.height / quotes_df.height if quotes_df.height > 0 else 0.0
        return crossed.height > 0, crossed_pct

    def _compute_stale_quote_pct(
        self, quotes_df: pl.DataFrame, threshold_seconds: int = 60
    ) -> float:
        """Compute percentage of stale quotes."""
        if quotes_df.height < 2:
            return 0.0

        quotes_df = quotes_df.sort("ts")
        quotes_df = quotes_df.with_columns(
            [
                pl.col("bid").shift(1).alias("prev_bid"),
                pl.col("ask").shift(1).alias("prev_ask"),
                pl.col("bid_size").shift(1).alias("prev_bid_size"),
                pl.col("ask_size").shift(1).alias("prev_ask_size"),
                pl.col("ts").shift(1).alias("prev_ts"),
            ]
        )

        quotes_df = quotes_df.filter(pl.col("prev_ts").is_not_null())

        quotes_df = quotes_df.with_columns(
            [
                ((pl.col("ts") - pl.col("prev_ts")).dt.total_seconds()).alias("time_diff"),
            ]
        )

        stale = quotes_df.filter(
            (pl.col("bid") == pl.col("prev_bid"))
            & (pl.col("ask") == pl.col("prev_ask"))
            & (pl.col("bid_size") == pl.col("prev_bid_size"))
            & (pl.col("ask_size") == pl.col("prev_ask_size"))
            & (pl.col("time_diff") > threshold_seconds)
        )

        return stale.height / quotes_df.height if quotes_df.height > 0 else 0.0


# =========================================================================
# Numba acceleration helpers (module-level to keep njit friendly)
# =========================================================================


def _bucket_arrays_to_dicts(
    bucket_arrays: tuple[
        NDArray[np.int64],
        NDArray[np.float64],
        NDArray[np.float64],
        NDArray[np.float64],
        NDArray[np.int64],
        NDArray[np.bool_],
        NDArray[np.bool_],
    ],
    timestamps: list[Any],
) -> list[dict[str, Any]]:
    bucket_ids, vpins, cumvols, imbalances, ts_ns, partial_flags, warmup_flags = bucket_arrays

    # Convert nanosecond ints back to datetime64 for polars compatibility
    ts_array = np.array(ts_ns, dtype="datetime64[ns]")
    buckets: list[dict[str, Any]] = []

    for idx in range(len(bucket_ids)):
        buckets.append(
            {
                "bucket_id": int(bucket_ids[idx]),
                "vpin": float(vpins[idx]),
                "cumulative_volume": float(cumvols[idx]),
                "imbalance": float(imbalances[idx]),
                "timestamp": ts_array[idx].astype(object),
                "is_partial": bool(partial_flags[idx]),
                "is_warmup": bool(warmup_flags[idx]),
            }
        )

    return buckets


if NUMBA_AVAILABLE:

    @njit
    def _compute_vpin_buckets_numba(  # type: ignore[no-untyped-def]
        v_buy_arr,
        v_sell_arr,
        sizes,
        cumulative_volume,
        sigma_zero_mask,
        timestamps_ns,
        volume_per_bucket,
        window_buckets,
        sigma_lookback,
    ):
        bucket_ids = NumbaList()
        vpins = NumbaList()
        cumvols = NumbaList()
        imbalances = NumbaList()
        ts_out = NumbaList()
        partial_flags = NumbaList()
        warmup_flags = NumbaList()

        current_bucket_volume = 0.0
        current_bucket_v_buy = 0.0
        current_bucket_v_sell = 0.0
        sigma_zero_contaminated = False

        v_buy_history = NumbaList()
        v_sell_history = NumbaList()

        n = len(v_buy_arr)
        for i in range(sigma_lookback, n):
            trade_size = float(sizes[i])
            trade_ts = int(timestamps_ns[i - sigma_lookback])
            v_buy = float(v_buy_arr[i])
            v_sell = float(v_sell_arr[i])

            if sigma_zero_mask[i]:
                sigma_zero_contaminated = True

            remaining_volume = trade_size
            remaining_v_buy = v_buy
            remaining_v_sell = v_sell

            while remaining_volume > 0:
                remaining_capacity = volume_per_bucket - current_bucket_volume

                if remaining_volume <= remaining_capacity:
                    current_bucket_volume += remaining_volume
                    current_bucket_v_buy += remaining_v_buy
                    current_bucket_v_sell += remaining_v_sell
                    remaining_volume = 0
                    ts_out_current = trade_ts
                else:
                    ts_out_current = trade_ts
                    if remaining_capacity > 0:
                        split_ratio = remaining_capacity / remaining_volume
                        current_bucket_volume += remaining_capacity
                        current_bucket_v_buy += remaining_v_buy * split_ratio
                        current_bucket_v_sell += remaining_v_sell * split_ratio

                        remaining_volume -= remaining_capacity
                        remaining_v_buy *= 1 - split_ratio
                        remaining_v_sell *= 1 - split_ratio

                    v_buy_history.append(current_bucket_v_buy)
                    v_sell_history.append(current_bucket_v_sell)

                    # Compute VPIN value
                    if sigma_zero_contaminated or len(v_buy_history) < window_buckets:
                        vpin_val = np.nan
                    else:
                        window_v_buy = 0.0
                        window_v_sell = 0.0
                        for j in range(len(v_buy_history) - window_buckets, len(v_buy_history)):
                            window_v_buy += v_buy_history[j]
                            window_v_sell += v_sell_history[j]
                        total_volume = window_v_buy + window_v_sell
                        if total_volume <= 0:
                            vpin_val = np.nan
                        else:
                            vpin_val = abs(window_v_buy - window_v_sell) / total_volume

                    bucket_ids.append(len(bucket_ids))
                    vpins.append(vpin_val)
                    cumvols.append(float(cumulative_volume[i]))
                    imbalances.append(abs(current_bucket_v_buy - current_bucket_v_sell))
                    ts_out.append(ts_out_current)
                    partial_flags.append(False)
                    warmup_flags.append(len(bucket_ids) < window_buckets - 1)

                    current_bucket_volume = 0.0
                    current_bucket_v_buy = 0.0
                    current_bucket_v_sell = 0.0
                    sigma_zero_contaminated = False

        has_partial = False
        if current_bucket_volume > 0:
            v_buy_history.append(current_bucket_v_buy)
            v_sell_history.append(current_bucket_v_sell)

            if sigma_zero_contaminated or len(v_buy_history) < window_buckets:
                vpin_val = np.nan
            else:
                window_v_buy = 0.0
                window_v_sell = 0.0
                for j in range(len(v_buy_history) - window_buckets, len(v_buy_history)):
                    window_v_buy += v_buy_history[j]
                    window_v_sell += v_sell_history[j]
                total_volume = window_v_buy + window_v_sell
                if total_volume <= 0:
                    vpin_val = np.nan
                else:
                    vpin_val = abs(window_v_buy - window_v_sell) / total_volume

            bucket_ids.append(len(bucket_ids))
            vpins.append(vpin_val)
            cumvols.append(float(cumulative_volume[-1]) if n > 0 else 0.0)
            imbalances.append(abs(current_bucket_v_buy - current_bucket_v_sell))
            ts_out.append(int(timestamps_ns[-1]) if len(timestamps_ns) > 0 else 0)
            partial_flags.append(True)
            warmup_flags.append(len(bucket_ids) < window_buckets - 1)
            has_partial = True

        return (
            np.array(bucket_ids),
            np.array(vpins),
            np.array(cumvols),
            np.array(imbalances),
            np.array(ts_out),
            np.array(partial_flags),
            np.array(warmup_flags),
        ), has_partial

else:

    def _compute_vpin_buckets_numba(
        *_args: Any, **_kwargs: Any
    ) -> None:  # pragma: no cover - fallback stub
        raise RuntimeError("Numba not available")
