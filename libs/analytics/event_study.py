"""Event Study Framework for analyzing stock price reactions to corporate events.

Implements standard academic event study methodology:
- Market model estimation for expected returns
- Multiple expected return models (Market, Mean-Adjusted, FF3, FF5)
- Cumulative Abnormal Return (CAR) calculation with configurable windows
- Multiple significance tests (Newey-West, Patell, BMP)
- PEAD (Post-Earnings Announcement Drift) analysis
- Index rebalance effect analysis

All outputs include dataset_version_id for reproducibility and PIT support.

References:
- MacKinlay (1997): Event Studies in Economics and Finance
- Patell (1976): Corporate Forecasts of Earnings Per Share and Stock Price Behavior
- Boehmer, Musumeci, Poulsen (1991): Event-Study Methodology Under Conditions of
  Event-Induced Variance
- Newey & West (1987): A Simple, Positive Semi-definite, Heteroskedasticity and
  Autocorrelation Consistent Covariance Matrix
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Literal, cast

import numpy as np
import polars as pl
from scipy.stats import t as t_dist  # type: ignore[import-untyped]

from libs.analytics.microstructure import CompositeVersionInfo
from libs.data_quality.exceptions import DataNotFoundError

if TYPE_CHECKING:
    from libs.data_providers.crsp_local_provider import CRSPLocalProvider
    from libs.data_providers.fama_french_local_provider import FamaFrenchLocalProvider

logger = logging.getLogger(__name__)


# =============================================================================
# Enums
# =============================================================================


class ExpectedReturnModel(str, Enum):
    """Expected return model for abnormal return calculation."""

    MARKET = "market"  # CAPM / market model
    MEAN_ADJUSTED = "mean_adjusted"  # Simple mean
    FF3 = "ff3"  # Fama-French 3-factor
    FF5 = "ff5"  # Fama-French 5-factor


class SignificanceTest(str, Enum):
    """Statistical test for CAR significance."""

    T_TEST = "t_test"  # Standard t-test with Newey-West SE
    PATELL = "patell"  # Patell standardized residual test
    BMP = "bmp"  # Boehmer-Musumeci-Poulsen test


class OverlapPolicy(str, Enum):
    """Policy for handling overlapping events.

    Note: AGGREGATE removed - academically undefined how to combine events.
    """

    DROP_LATER = "drop_later"  # Keep first event, drop later overlapping events
    DROP_EARLIER = "drop_earlier"  # Keep last event, drop earlier overlapping events
    WARN_ONLY = "warn_only"  # Include all events, add warning, record overlap count


class ClusteringMitigation(str, Enum):
    """Strategy for handling cross-sectional correlation from same-day events."""

    NONE = "none"  # Ignore clustering (when events well-dispersed)
    CALENDAR_TIME = "calendar_time"  # Calendar-time portfolio regression
    CLUSTERED_SE = "clustered_se"  # Cluster standard errors by date
    WARN_ONLY = "warn_only"  # Compute tests, add warning
    AUTO = "auto"  # Auto-detect and choose appropriate method


# =============================================================================
# Configuration
# =============================================================================


@dataclass
class EventStudyConfig:
    """Configuration for event study analysis.

    Includes validation to prevent invalid combinations.
    """

    # Window configuration
    estimation_window: int = 120  # Trading days
    gap_days: int = 5  # Days between estimation and event window (min 1)
    pre_window: int = 5  # Days before event
    post_window: int = 20  # Days after event
    min_estimation_obs: int = 60  # Minimum observations for estimation

    # Model selection
    expected_return_model: ExpectedReturnModel = ExpectedReturnModel.MARKET
    significance_test: SignificanceTest = SignificanceTest.T_TEST

    # Newey-West settings
    newey_west_lags: int | None = None  # None = auto-select, bounds: 1 ≤ L ≤ T-1

    # Overlap handling
    overlap_policy: OverlapPolicy = OverlapPolicy.DROP_LATER
    min_days_between_events: int | None = None  # None = auto (pre_window + 1 + post_window)

    # Cross-sectional correlation handling
    clustering_mitigation: ClusteringMitigation = ClusteringMitigation.AUTO

    # Data quality
    winsorize_ar_percentile: float = 0.99  # Winsorize extreme AR at this percentile
    cap_beta: float = 5.0  # Cap |beta| at this value

    # Trading day handling
    roll_nontrading_direction: Literal["forward", "backward"] = "forward"

    def __post_init__(self) -> None:
        """Validate configuration after initialization."""
        errors = []

        # Compute event window length (used in multiple validations)
        event_window_len = self.pre_window + 1 + self.post_window

        # Auto-compute min_days_between_events if not specified (window-aware default)
        if self.min_days_between_events is None:
            # Use object.__setattr__ since dataclass may be frozen
            object.__setattr__(self, "min_days_between_events", event_window_len)

        # gap_days must be >= 1
        if self.gap_days < 1:
            errors.append("gap_days must be >= 1")

        # min_days_between_events must be > 0
        if self.min_days_between_events is not None and self.min_days_between_events < 1:
            errors.append("min_days_between_events must be >= 1")

        # newey_west_lags must be >= 1 if specified
        if self.newey_west_lags is not None and self.newey_west_lags < 1:
            errors.append("newey_west_lags must be >= 1 (or None for auto)")

        # newey_west_lags must be <= event_window - 1
        if self.newey_west_lags is not None and self.newey_west_lags >= event_window_len:
            errors.append(
                f"newey_west_lags ({self.newey_west_lags}) must be < "
                f"event_window_length ({event_window_len})"
            )

        # estimation_window must be >= min_estimation_obs
        if self.estimation_window < self.min_estimation_obs:
            errors.append(
                f"estimation_window ({self.estimation_window}) must be >= "
                f"min_estimation_obs ({self.min_estimation_obs})"
            )

        # estimation_window must be > 4 for BMP test DOF correction
        if self.significance_test == SignificanceTest.BMP and self.estimation_window <= 4:
            errors.append(
                f"estimation_window ({self.estimation_window}) must be > 4 "
                f"for BMP test (DOF correction requires T₁ > 4)"
            )

        # Patell/BMP tests ONLY work with market model
        if self.significance_test in (SignificanceTest.PATELL, SignificanceTest.BMP):
            if self.expected_return_model != ExpectedReturnModel.MARKET:
                errors.append(
                    f"significance_test={self.significance_test.value} requires "
                    f"expected_return_model=MARKET (forecast-error adjustment is "
                    f"only defined for single-factor OLS). Use T_TEST for "
                    f"{self.expected_return_model.value}."
                )

        # pre_window and post_window must be >= 0
        if self.pre_window < 0:
            errors.append("pre_window must be >= 0")
        if self.post_window < 0:
            errors.append("post_window must be >= 0")

        # cap_beta must be positive
        if self.cap_beta <= 0:
            errors.append("cap_beta must be > 0")

        # winsorize_ar_percentile must be in (0.5, 1.0)
        if not 0.5 < self.winsorize_ar_percentile <= 1.0:
            errors.append("winsorize_ar_percentile must be in (0.5, 1.0]")

        if errors:
            raise ValueError(f"Invalid EventStudyConfig: {'; '.join(errors)}")


# =============================================================================
# Result Dataclasses
# =============================================================================


@dataclass
class EventStudyResult:
    """Base result with versioning metadata (per MicrostructureResult pattern)."""

    dataset_version_id: str
    dataset_versions: dict[str, str] | None
    computation_timestamp: datetime
    as_of_date: date | None


@dataclass
class MarketModelResult(EventStudyResult):
    """Result of market model estimation for a single security."""

    symbol: str
    permno: int
    estimation_start: date
    estimation_end: date
    n_observations: int

    # Model configuration
    model_type: ExpectedReturnModel

    # Model parameters
    alpha: float  # Intercept
    beta: float  # Market sensitivity (or first factor beta for FF3/FF5)
    factor_betas: dict[str, float] | None  # For FF3/FF5: {SMB: x, HML: y, ...}
    alpha_tstat: float
    beta_tstat: float

    # Model fit
    r_squared: float
    residual_std: float  # For SE calculation

    # Estimation period stats (for Patell forecast error)
    market_mean: float  # Mean market return over estimation window
    market_sxx: float  # Sum of squared deviations of market returns

    # Warnings
    warnings: list[str] = field(default_factory=list)


@dataclass
class EventStudyAnalysis(EventStudyResult):
    """Result of single event study analysis."""

    # Event identification
    event_id: str
    symbol: str
    permno: int
    event_date: date  # Original event date
    adjusted_event_date: date  # After rolling to trading day
    event_type: str

    # Configuration used
    config: EventStudyConfig

    # Market model parameters
    alpha: float
    beta: float
    model_type: ExpectedReturnModel

    # Abnormal returns
    car_pre: float  # CAR days [-w_pre, -1]
    car_event: float  # CAR on event day
    car_post: float  # CAR days [+1, +w_post]
    car_window: float  # CAR full window

    # Daily AR series
    daily_ar: pl.DataFrame  # [relative_day, date, return, rf, expected_return, ar]

    # Volume analysis
    abnormal_volume: float | None
    volume_estimation_avg: float | None

    # Statistical tests
    t_statistic: float
    p_value: float
    is_significant: bool  # p < 0.05

    # Standard errors (must come before optional fields)
    se_car: float
    newey_west_lags: int

    # Additional tests (if computed) - optional fields with defaults
    patell_z: float | None = None  # Z-statistic, ~N(0,1) for large N
    bmp_t: float | None = None  # t-statistic, ~t(N-1) distribution

    # Delisting handling
    is_delisted: bool = False
    delisting_return: float | None = None

    # Warnings
    warnings: list[str] = field(default_factory=list)


@dataclass
class PEADAnalysisResult(EventStudyResult):
    """Result of Post-Earnings Announcement Drift analysis."""

    holding_period_days: int
    n_events: int
    n_events_excluded: int  # Due to overlap, data issues
    analysis_start: date
    analysis_end: date

    # Configuration
    config: EventStudyConfig

    # Quintile results
    quintile_results: pl.DataFrame  # [quintile, n_events, avg_surprise, car, se, t_stat, p_value]

    # Summary statistics
    drift_magnitude: float  # Q5 CAR - Q1 CAR
    drift_t_stat: float
    drift_significant: bool

    # Overlap statistics
    n_overlapping_dropped: int

    # Clustering info
    clustering_mitigation_used: ClusteringMitigation
    clustering_info: dict[str, int | bool] | None

    # Warnings
    warnings: list[str] = field(default_factory=list)


@dataclass
class IndexRebalanceResult(EventStudyResult):
    """Result of index rebalance event study."""

    index_name: str

    # Configuration
    config: EventStudyConfig

    # Event counts
    n_additions: int
    n_deletions: int

    # Addition effects (using effective_date by default)
    addition_car_pre: float
    addition_car_post: float
    addition_t_stat: float
    addition_significant: bool

    # Deletion effects
    deletion_car_pre: float
    deletion_car_post: float
    deletion_t_stat: float
    deletion_significant: bool

    # Volume effects
    addition_volume_change: float
    deletion_volume_change: float

    # Announcement vs effective date analysis
    uses_announcement_date: bool
    announcement_effective_gap_days: float | None  # Average gap

    # Detailed results
    addition_results: pl.DataFrame
    deletion_results: pl.DataFrame

    # Clustering info
    clustering_mitigation_used: ClusteringMitigation
    clustering_info: dict[str, int | bool] | None

    # Warnings
    warnings: list[str] = field(default_factory=list)


# =============================================================================
# Helper Functions
# =============================================================================


def _compute_newey_west_se(
    ar_series: np.ndarray[Any, np.dtype[np.floating[Any]]],
    n_lags: int | None = None,
) -> tuple[float, int]:
    """Compute Newey-West HAC standard error for CAR.

    Uses Long-Run Variance (LRV) approach with Bartlett kernel:
    Var(CAR) = T × LRV, where LRV = γ₀ + 2 × Σ w_j × γ_j

    Args:
        ar_series: Array of abnormal returns in event window.
        n_lags: Number of lags (None = auto-select). Bounds: 1 ≤ L ≤ T-1.

    Returns:
        Tuple of (HAC-corrected standard error for sum of AR (CAR), lags used).
    """
    T = len(ar_series)
    if T < 2:
        return float("nan"), 0

    # Auto-select lags with bounds: 1 ≤ L ≤ T-1
    if n_lags is None:
        n_lags = max(1, min(T - 1, int(np.floor(4 * (T / 100) ** (2 / 9)))))
    else:
        # Enforce bounds on user-specified lags
        n_lags = max(1, min(T - 1, n_lags))

    # Mean-center the AR series (CRITICAL for correct autocovariance)
    ar_centered = ar_series - np.mean(ar_series)

    # Compute autocovariances with mean-centered data
    # γ_j = (1/T) × Σ_{t=j+1}^{T} ar_t × ar_{t-j}
    gamma = np.zeros(n_lags + 1)
    for j in range(n_lags + 1):
        if j == 0:
            gamma[0] = np.sum(ar_centered**2) / T
        else:
            gamma[j] = np.sum(ar_centered[j:] * ar_centered[:-j]) / T

    # Long-Run Variance: LRV = γ₀ + 2 × Σ_{j=1}^{L} w_j × γ_j
    # Note: NO (T-j) factor - that would double-scale
    lrv = gamma[0]
    for j in range(1, n_lags + 1):
        weight = 1 - j / (n_lags + 1)  # Bartlett kernel
        lrv += 2 * weight * gamma[j]

    # Var(CAR) = T × LRV
    var_car = T * lrv

    return np.sqrt(max(var_car, 0)), n_lags  # Ensure non-negative


def _run_ols_regression(
    y: np.ndarray[Any, np.dtype[np.floating[Any]]],
    X: np.ndarray[Any, np.dtype[np.floating[Any]]],
) -> tuple[
    np.ndarray[Any, np.dtype[np.floating[Any]]],
    np.ndarray[Any, np.dtype[np.floating[Any]]],
    float,
    float,
]:
    """Run OLS regression: y = X @ beta + epsilon.

    Args:
        y: Dependent variable (excess returns).
        X: Independent variables (with intercept column).

    Returns:
        Tuple of (coefficients, t_stats, r_squared, residual_std).
    """
    n, k = X.shape

    # OLS: beta = (X'X)^{-1} X'y
    # Use lstsq for numerical stability instead of explicit matrix inversion
    beta, _, _, _ = np.linalg.lstsq(X, y, rcond=None)

    # For SE calculation, use pseudoinverse for robustness to multicollinearity
    XtX = X.T @ X
    XtX_inv = np.linalg.pinv(XtX)

    # Residuals and fit statistics
    y_hat = X @ beta
    residuals = y - y_hat

    # Degrees of freedom
    dof = n - k

    # Residual standard error (MSE)
    mse = np.sum(residuals**2) / dof if dof > 0 else float("nan")
    residual_std = np.sqrt(mse)

    # R-squared
    ss_res = np.sum(residuals**2)
    ss_tot = np.sum((y - np.mean(y)) ** 2)
    r_squared = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0

    # Standard errors and t-statistics
    var_beta = mse * np.diag(XtX_inv)
    se_beta = np.sqrt(var_beta)
    t_stats = beta / se_beta

    return beta, t_stats, r_squared, residual_std


def _compute_trading_days_offset(
    base_date: date,
    offset_days: int,
    trading_calendar: pl.DataFrame,
) -> date:
    """Get date that is offset_days trading days from base_date.

    Args:
        base_date: Starting date.
        offset_days: Number of trading days (positive = forward, negative = backward).
        trading_calendar: DataFrame with 'date' column of trading days.

    Returns:
        Target trading date.

    Raises:
        ValueError: If offset goes beyond calendar bounds.
    """
    dates = trading_calendar.sort("date")["date"].to_list()

    # Find base_date index
    try:
        base_idx = dates.index(base_date)
    except ValueError:
        # base_date not in calendar - find nearest
        for i, d in enumerate(dates):
            if d >= base_date:
                base_idx = i
                break
        else:
            raise ValueError(f"base_date {base_date} is beyond calendar range")

    target_idx = base_idx + offset_days

    if target_idx < 0 or target_idx >= len(dates):
        raise ValueError(
            f"Offset {offset_days} from {base_date} goes beyond calendar bounds "
            f"[{dates[0]}, {dates[-1]}]"
        )

    return cast(date, dates[target_idx])


def _detect_event_clustering(
    events: pl.DataFrame, date_col: str = "event_date"
) -> dict[str, int | bool]:
    """Detect calendar-date clustering in events.

    Args:
        events: DataFrame with event dates.
        date_col: Name of the date column.

    Returns:
        Dict with clustering statistics.
    """
    date_counts = events.group_by(date_col).agg(pl.len().alias("n_events"))
    total_dates = date_counts.height  # Number of unique event dates
    max_same_day = date_counts.select(pl.col("n_events").max()).item() or 0
    n_clustered_dates = date_counts.filter(pl.col("n_events") > 1).height

    return {
        "max_events_same_day": max_same_day,
        "n_clustered_dates": n_clustered_dates,
        "total_dates": total_dates,
        "clustering_severe": max_same_day > 5
        or (n_clustered_dates > total_dates * 0.1 if total_dates > 0 else False),
    }


def _select_clustering_mitigation(
    clustering_info: dict[str, int | bool],
    config: EventStudyConfig,
) -> ClusteringMitigation:
    """Select clustering mitigation strategy based on detection results.

    Thresholds:
    - NONE: max_events_same_day <= 1 (no clustering)
    - CLUSTERED_SE: 2 <= max_events_same_day <= 10 AND n_clustered_dates < 20%
    - CALENDAR_TIME: max_events_same_day > 10 OR n_clustered_dates >= 20%

    Args:
        clustering_info: Output from _detect_event_clustering.
        config: Event study configuration.

    Returns:
        Selected ClusteringMitigation strategy.
    """
    if config.clustering_mitigation != ClusteringMitigation.AUTO:
        return config.clustering_mitigation

    max_same_day = clustering_info["max_events_same_day"]
    n_clustered = clustering_info["n_clustered_dates"]
    total_dates = clustering_info.get("total_dates", 1)
    cluster_pct = n_clustered / total_dates if total_dates > 0 else 0

    if max_same_day <= 1:
        return ClusteringMitigation.NONE
    elif max_same_day <= 10 and cluster_pct < 0.20:
        return ClusteringMitigation.CLUSTERED_SE
    else:
        return ClusteringMitigation.CALENDAR_TIME


def _compute_clustered_se(
    scars: np.ndarray[Any, np.dtype[np.floating[Any]]],
    cluster_ids: np.ndarray[Any, np.dtype[np.int64]],
) -> tuple[float, float, int]:
    """Compute cluster-robust standard error for mean.

    Uses Cameron-Miller (2015) cluster-robust variance estimator:
    Var(mean) = (G/(G-1)) × (1/N²) × Σ u_g²

    Args:
        scars: Array of SCAR values for each event.
        cluster_ids: Array of cluster IDs (e.g., date ordinals).

    Returns:
        Tuple of (clustered_se, t_stat, df).
    """
    N = len(scars)
    scar_mean = np.mean(scars)
    unique_clusters = np.unique(cluster_ids)
    G = len(unique_clusters)

    if G < 2:
        return float("nan"), float("nan"), 0

    # Compute cluster residual sums
    u_sq_sum = 0.0
    for cluster in unique_clusters:
        mask = cluster_ids == cluster
        cluster_resids = scars[mask] - scar_mean
        u_d = np.sum(cluster_resids)
        u_sq_sum += u_d**2

    # Cluster-robust variance of mean: (G/(G-1)) × (1/N²) × Σ u_d²
    var_mean = (G / (G - 1)) * u_sq_sum / (N**2)

    se_clustered = np.sqrt(var_mean)
    t_stat = scar_mean / se_clustered if se_clustered > 0 else float("nan")

    return se_clustered, t_stat, G - 1


def _get_dlret_fallback(dlstcd: int | None) -> float:
    """Get DLRET fallback based on delisting code.

    Per Shumway (1997):
    - 500-599: Dropped for cause → -30%
    - 400-499: Liquidation → -100%
    - 200-399: Mergers → 0%

    Args:
        dlstcd: CRSP delisting code.

    Returns:
        Fallback DLRET value.
    """
    if dlstcd is None:
        return -0.30  # Conservative default

    if 500 <= dlstcd < 600:
        return -0.30  # Dropped for cause
    elif 400 <= dlstcd < 500:
        return -1.0  # Liquidation
    else:
        return 0.0  # Merger or other


def _winsorize(
    arr: np.ndarray[Any, np.dtype[np.floating[Any]]], percentile: float = 0.99
) -> tuple[np.ndarray[Any, np.dtype[np.floating[Any]]], int, int]:
    """Winsorize array at given percentile.

    Args:
        arr: Input array.
        percentile: Upper percentile (lower = 1 - percentile).

    Returns:
        Tuple of (winsorized array, n_lower_clipped, n_upper_clipped).
    """
    lower = np.nanpercentile(arr, (1 - percentile) * 100)
    upper = np.nanpercentile(arr, percentile * 100)

    n_lower = np.sum(arr < lower)
    n_upper = np.sum(arr > upper)

    winsorized = np.clip(arr, lower, upper)
    return winsorized, int(n_lower), int(n_upper)


# =============================================================================
# Main Framework Class
# =============================================================================


class EventStudyFramework:
    """Framework for event study analysis.

    Uses CRSP for stock returns (including delisting) and Fama-French
    for market returns and risk-free rate.
    """

    DATASET_CRSP = "crsp_daily"
    DATASET_FF = "fama_french"

    def __init__(
        self,
        crsp_provider: CRSPLocalProvider,
        fama_french_provider: FamaFrenchLocalProvider,
        config: EventStudyConfig | None = None,
    ) -> None:
        """Initialize framework with data providers and config.

        Args:
            crsp_provider: Provider for CRSP daily stock data.
            fama_french_provider: Provider for Fama-French factors.
            config: Event study configuration (uses defaults if None).
        """
        self.crsp = crsp_provider
        self.ff = fama_french_provider
        self.config = config or EventStudyConfig()
        self._trading_calendar: pl.DataFrame | None = None

    def _get_trading_calendar(
        self, start: date, end: date, as_of: date | None = None
    ) -> pl.DataFrame:
        """Get trading calendar from CRSP data.

        Infers trading days from CRSP daily data (dates with valid returns).

        Args:
            start: Start date for calendar range.
            end: End date for calendar range.
            as_of: Point-in-time date for PIT queries.

        Returns:
            DataFrame with 'date' column of trading days.
        """
        # Query CRSP for any data in range to get trading dates
        df = self.crsp.get_daily_prices(
            start_date=start,
            end_date=end,
            columns=["date"],
            as_of_date=as_of,
        )

        if df.is_empty():
            raise DataNotFoundError(f"No CRSP data found for trading calendar [{start}, {end}]")

        # Get unique dates
        calendar = df.select("date").unique().sort("date")
        return calendar

    def _roll_to_trading_day(
        self,
        event_date: date,
        trading_calendar: pl.DataFrame,
        direction: str = "forward",
    ) -> date:
        """Roll non-trading date to next (forward) or previous (backward) trading day.

        Args:
            event_date: Original event date.
            trading_calendar: DataFrame with 'date' column of trading days.
            direction: "forward" or "backward".

        Returns:
            Adjusted trading date.

        Raises:
            ValueError: If no valid trading day found.
        """
        dates = trading_calendar.sort("date")["date"].to_list()

        if event_date in dates:
            return event_date

        if direction == "forward":
            for d in dates:
                if d >= event_date:
                    return cast(date, d)
            raise ValueError(f"No trading day on or after {event_date}")
        else:  # backward
            for d in reversed(dates):
                if d <= event_date:
                    return cast(date, d)
            raise ValueError(f"No trading day on or before {event_date}")

    def _handle_overlapping_events(
        self,
        events: pl.DataFrame,
        symbol_col: str = "symbol",
        date_col: str = "event_date",
        config: EventStudyConfig | None = None,
    ) -> tuple[pl.DataFrame, int]:
        """Handle overlapping events per symbol based on config.overlap_policy.

        Args:
            events: DataFrame with event data.
            symbol_col: Column name for symbol.
            date_col: Column name for event date.
            config: Optional config to use (defaults to self.config).

        Returns:
            Tuple of (filtered_events, n_dropped).
        """
        active_config = config if config is not None else self.config

        if active_config.overlap_policy == OverlapPolicy.WARN_ONLY:
            return events, 0

        min_days = active_config.min_days_between_events or (
            active_config.pre_window + 1 + active_config.post_window
        )

        events = events.sort([symbol_col, date_col])
        keep_mask = []
        prev_symbol = None
        prev_date = None

        for row in events.iter_rows(named=True):
            symbol = row[symbol_col]
            event_date = row[date_col]

            if symbol != prev_symbol:
                # New symbol - no overlap possible
                keep_mask.append(True)
            else:
                # Same symbol - check gap
                days_gap = (event_date - prev_date).days if prev_date else float("inf")

                if days_gap >= min_days:
                    keep_mask.append(True)
                else:
                    # Overlap detected
                    if active_config.overlap_policy == OverlapPolicy.DROP_LATER:
                        keep_mask.append(False)
                    else:  # DROP_EARLIER
                        # Mark previous as dropped
                        if keep_mask:
                            keep_mask[-1] = False
                        keep_mask.append(True)

            if keep_mask[-1]:
                prev_date = event_date
            prev_symbol = symbol

        filtered = events.filter(pl.Series(keep_mask))
        n_dropped = events.height - filtered.height

        return filtered, n_dropped

    def _get_version_info(self, as_of: date | None = None) -> CompositeVersionInfo:
        """Get version info from both providers.

        Args:
            as_of: Point-in-time date for PIT queries.

        Returns:
            CompositeVersionInfo with combined version IDs.
        """
        # Get CRSP version
        crsp_manifest = self.crsp.manifest_manager.load_manifest(self.DATASET_CRSP)
        crsp_version = crsp_manifest.checksum if crsp_manifest else "unknown"

        # Get FF version from manifest file
        ff_manifest_path = self.ff._storage_path / "fama_french_manifest.json"
        if ff_manifest_path.exists():
            import json

            with open(ff_manifest_path) as f:
                ff_manifest = json.load(f)
            ff_version = ff_manifest.get("aggregate_checksum", "unknown")
        else:
            ff_version = "unknown"

        return CompositeVersionInfo(
            versions={self.DATASET_CRSP: crsp_version, self.DATASET_FF: ff_version},
            snapshot_id=None,
            is_pit=as_of is not None,
        )

    def _compute_excess_returns(
        self,
        returns: pl.DataFrame,
        ff_data: pl.DataFrame,
    ) -> pl.DataFrame:
        """Compute excess returns (R_i - RF) using Fama-French RF.

        Args:
            returns: DataFrame with 'date' and 'ret' columns.
            ff_data: DataFrame with 'date' and 'rf' columns.

        Returns:
            DataFrame with 'excess_ret' column added.
        """
        # Join on date
        merged = returns.join(ff_data.select(["date", "rf"]), on="date", how="left")

        # Handle missing RF
        if merged.filter(pl.col("rf").is_null()).height > 0:
            logger.warning("Missing RF values, using 0 for excess return calculation")
            merged = merged.with_columns(pl.col("rf").fill_null(0))

        # Compute excess return
        merged = merged.with_columns((pl.col("ret") - pl.col("rf")).alias("excess_ret"))

        return merged

    def estimate_market_model(
        self,
        symbol: str,
        estimation_end: date,
        model: ExpectedReturnModel | None = None,
        as_of: date | None = None,
    ) -> MarketModelResult:
        """Estimate expected return model for a security.

        Enforces gap_days before estimation_end for event window separation.
        Uses excess returns (R - RF) for all models.

        Args:
            symbol: Stock ticker symbol.
            estimation_end: End date of estimation window (gap_days before event).
            model: Expected return model to use (defaults to config).
            as_of: Point-in-time date for PIT queries.

        Returns:
            MarketModelResult with model parameters.

        Raises:
            DataNotFoundError: If insufficient data for estimation.
        """
        model = model or self.config.expected_return_model
        warnings: list[str] = []

        # Get version info
        version_info = self._get_version_info(as_of)

        # Calculate estimation window dates
        # Need to get trading calendar first to compute correct dates
        buffer_start = date(estimation_end.year - 1, estimation_end.month, estimation_end.day)
        calendar = self._get_trading_calendar(buffer_start, estimation_end, as_of)

        estimation_start = _compute_trading_days_offset(
            estimation_end, -self.config.estimation_window + 1, calendar
        )

        # Get stock returns
        returns_df = self.crsp.get_daily_prices(
            start_date=estimation_start,
            end_date=estimation_end,
            symbols=[symbol],
            columns=["date", "permno", "ret"],
            as_of_date=as_of,
        )

        if returns_df.is_empty():
            raise DataNotFoundError(
                f"No CRSP data for {symbol} in [{estimation_start}, {estimation_end}]"
            )

        permno = returns_df["permno"][0]

        # Remove missing returns
        returns_df = returns_df.filter(pl.col("ret").is_not_null())

        if returns_df.height < self.config.min_estimation_obs:
            raise DataNotFoundError(
                f"Insufficient observations for {symbol}: "
                f"{returns_df.height} < {self.config.min_estimation_obs}"
            )

        # Get Fama-French data
        ff_df = self.ff.get_factors(
            start_date=estimation_start,
            end_date=estimation_end,
            frequency="daily",
            model="ff5" if model in (ExpectedReturnModel.FF3, ExpectedReturnModel.FF5) else "ff3",
        )

        if ff_df.is_empty():
            raise DataNotFoundError(
                f"No Fama-French data for [{estimation_start}, {estimation_end}]"
            )

        # Compute excess returns
        returns_df = self._compute_excess_returns(returns_df, ff_df)

        # Merge with factors
        merged = returns_df.join(ff_df, on="date", how="inner", suffix="_ff")
        merged = merged.filter(pl.col("excess_ret").is_not_null())

        n_obs = merged.height
        if n_obs < self.config.min_estimation_obs:
            raise DataNotFoundError(
                f"Insufficient observations after merge for {symbol}: "
                f"{n_obs} < {self.config.min_estimation_obs}"
            )

        # Prepare regression data
        y = merged["excess_ret"].to_numpy()

        if model == ExpectedReturnModel.MARKET:
            # Market model: R_i - RF = alpha + beta * (MKT-RF)
            mkt_rf = merged["mkt_rf"].to_numpy()
            X = np.column_stack([np.ones(n_obs), mkt_rf])
            factor_betas = None

            # Compute market stats for Patell forecast error
            market_mean = float(np.mean(mkt_rf))
            market_sxx = float(np.sum((mkt_rf - market_mean) ** 2))

        elif model == ExpectedReturnModel.MEAN_ADJUSTED:
            # Mean-adjusted: just intercept
            X = np.ones((n_obs, 1))
            factor_betas = None
            market_mean = 0.0
            market_sxx = 0.0

        elif model == ExpectedReturnModel.FF3:
            # FF3: alpha + beta_mkt*MKT + beta_smb*SMB + beta_hml*HML
            mkt_rf = merged["mkt_rf"].to_numpy()
            smb = merged["smb"].to_numpy()
            hml = merged["hml"].to_numpy()
            X = np.column_stack([np.ones(n_obs), mkt_rf, smb, hml])
            market_mean = float(np.mean(mkt_rf))
            market_sxx = float(np.sum((mkt_rf - market_mean) ** 2))

        else:  # FF5
            # FF5: alpha + beta_mkt*MKT + beta_smb*SMB + beta_hml*HML + beta_rmw*RMW + beta_cma*CMA
            mkt_rf = merged["mkt_rf"].to_numpy()
            smb = merged["smb"].to_numpy()
            hml = merged["hml"].to_numpy()
            rmw = merged["rmw"].to_numpy()
            cma = merged["cma"].to_numpy()
            X = np.column_stack([np.ones(n_obs), mkt_rf, smb, hml, rmw, cma])
            market_mean = float(np.mean(mkt_rf))
            market_sxx = float(np.sum((mkt_rf - market_mean) ** 2))

        # Run OLS regression
        beta_hat, t_stats, r_squared, residual_std = _run_ols_regression(y, X)

        alpha = float(beta_hat[0])
        alpha_tstat = float(t_stats[0])

        if model == ExpectedReturnModel.MEAN_ADJUSTED:
            beta = 0.0
            beta_tstat = 0.0
        else:
            beta = float(beta_hat[1])
            beta_tstat = float(t_stats[1])

            # Cap extreme betas
            if abs(beta) > self.config.cap_beta:
                warnings.append(f"Beta capped from {beta:.3f} to ±{self.config.cap_beta}")
                beta = np.clip(beta, -self.config.cap_beta, self.config.cap_beta)

        # Extract factor betas for multi-factor models
        if model == ExpectedReturnModel.FF3:
            factor_betas = {
                "MKT": float(beta_hat[1]),
                "SMB": float(beta_hat[2]),
                "HML": float(beta_hat[3]),
            }
        elif model == ExpectedReturnModel.FF5:
            factor_betas = {
                "MKT": float(beta_hat[1]),
                "SMB": float(beta_hat[2]),
                "HML": float(beta_hat[3]),
                "RMW": float(beta_hat[4]),
                "CMA": float(beta_hat[5]),
            }
        else:
            factor_betas = None

        return MarketModelResult(
            dataset_version_id=version_info.composite_version_id,
            dataset_versions=version_info.versions,
            computation_timestamp=datetime.now(UTC),
            as_of_date=as_of,
            symbol=symbol,
            permno=permno,
            estimation_start=estimation_start,
            estimation_end=estimation_end,
            n_observations=n_obs,
            model_type=model,
            alpha=alpha,
            beta=beta,
            factor_betas=factor_betas,
            alpha_tstat=alpha_tstat,
            beta_tstat=beta_tstat,
            r_squared=r_squared,
            residual_std=residual_std,
            market_mean=market_mean,
            market_sxx=market_sxx,
            warnings=warnings,
        )

    def compute_car(
        self,
        symbol: str,
        event_date: date,
        event_type: str = "custom",
        event_id: str | None = None,
        config: EventStudyConfig | None = None,
        as_of: date | None = None,
    ) -> EventStudyAnalysis:
        """Compute cumulative abnormal return around event.

        Features:
        - Enforces gap_days between estimation and event windows
        - Rolls non-trading event dates to next trading day
        - Handles delisting returns for stocks that delist during event window
        - Computes multiple significance tests if configured

        Args:
            symbol: Stock ticker symbol.
            event_date: Date of the event.
            event_type: Type of event (for labeling).
            event_id: Optional unique event identifier.
            config: Override default config.
            as_of: Point-in-time date for PIT queries.

        Returns:
            EventStudyAnalysis with CAR and statistics.

        Raises:
            DataNotFoundError: If insufficient data.
        """
        config = config or self.config
        warnings: list[str] = []
        event_id = event_id or f"{symbol}_{event_date}_{event_type}"

        # Get version info
        version_info = self._get_version_info(as_of)

        # Calculate date ranges
        # Buffer to get trading calendar
        buffer_start = date(event_date.year - 2, 1, 1)
        buffer_end = date(event_date.year + 1, 12, 31)

        calendar = self._get_trading_calendar(buffer_start, buffer_end, as_of)

        # Roll event date to trading day if needed
        adjusted_event_date = self._roll_to_trading_day(
            event_date, calendar, config.roll_nontrading_direction
        )
        if adjusted_event_date != event_date:
            warnings.append(f"Event date rolled from {event_date} to {adjusted_event_date}")

        # Calculate estimation window end (gap_days before event window start)
        event_window_start = _compute_trading_days_offset(
            adjusted_event_date, -config.pre_window, calendar
        )
        estimation_end = _compute_trading_days_offset(
            event_window_start, -config.gap_days, calendar
        )

        # Try to compute event_window_end - if it fails, stock may have delisted
        early_delisting_detected = False
        try:
            event_window_end = _compute_trading_days_offset(
                adjusted_event_date, config.post_window, calendar
            )
        except ValueError as e:
            # Calendar doesn't extend far enough - potential delisting
            if "goes beyond calendar bounds" in str(e):
                early_delisting_detected = True
                # Use the last available calendar date as event_window_end
                event_window_end = calendar.select(pl.col("date").max()).item()
                warnings.append(
                    f"Calendar ends at {event_window_end}, which is before expected "
                    f"event window end. Stock may have delisted."
                )
            else:
                raise

        # Estimate market model
        model_result = self.estimate_market_model(
            symbol=symbol,
            estimation_end=estimation_end,
            model=config.expected_return_model,
            as_of=as_of,
        )
        warnings.extend(model_result.warnings)

        # Get event window returns
        returns_df = self.crsp.get_daily_prices(
            start_date=event_window_start,
            end_date=event_window_end,
            symbols=[symbol],
            columns=["date", "permno", "ret", "vol"],
            as_of_date=as_of,
        )

        if returns_df.is_empty():
            raise DataNotFoundError(
                f"No CRSP data for {symbol} in event window "
                f"[{event_window_start}, {event_window_end}]"
            )

        permno = returns_df["permno"][0]

        # Check for delisting (truncated data)
        is_delisted = early_delisting_detected  # Start with early detection
        delisting_return = None
        last_return_date = returns_df.select(pl.col("date").max()).item()

        # Also check if data within event window is truncated
        if early_delisting_detected or last_return_date < event_window_end:
            is_delisted = True
            warnings.append(
                f"Stock may have delisted: last return on {last_return_date}, "
                f"event window ends {event_window_end}"
            )

            # Try to get delisting return from CRSP if available
            # Check if provider has get_delisting method
            if hasattr(self.crsp, "get_delisting") and callable(self.crsp.get_delisting):
                try:
                    delist_info = self.crsp.get_delisting(
                        permno=permno,
                        as_of_date=as_of,
                    )
                    if delist_info is not None:
                        dlret_raw = delist_info.get("dlret")
                        dlstcd = delist_info.get("dlstcd")

                        # Normalize dlret to a scalar while being robust to arrays/Series
                        dlret_scalar: float | None
                        if dlret_raw is None:
                            dlret_scalar = None
                        else:
                            dlret_arr = np.asarray(dlret_raw)
                            if dlret_arr.size == 0:
                                dlret_scalar = None
                            else:
                                # Use first element if array/Series, otherwise cast directly
                                dlret_scalar = float(dlret_arr.flat[0])

                        if dlret_scalar is not None and not np.isnan(dlret_scalar):
                            delisting_return = dlret_scalar
                        elif dlstcd is not None:
                            # Use fallback based on delisting code
                            delisting_return = _get_dlret_fallback(dlstcd)
                            warnings.append(
                                f"DLRET missing, using Shumway fallback {delisting_return:.0%} "
                                f"based on DLSTCD={dlstcd}"
                            )
                except Exception as e:
                    logger.debug(f"Could not fetch delisting info: {e}")
                    # Use conservative fallback
                    delisting_return = _get_dlret_fallback(None)
                    warnings.append(
                        f"Could not fetch DLRET, using conservative fallback {delisting_return:.0%}"
                    )
            else:
                # Provider doesn't support delisting data, use fallback
                delisting_return = _get_dlret_fallback(None)
                warnings.append(
                    f"CRSP provider lacks delisting data, using conservative fallback "
                    f"{delisting_return:.0%}"
                )

            # Combine last return with delisting return if available
            # Combined return: (1 + R_last) × (1 + DLRET) - 1
            if delisting_return is not None:
                last_ret = returns_df.filter(pl.col("date") == last_return_date)["ret"][0]
                if last_ret is not None and not np.isnan(last_ret):
                    combined_ret = (1 + last_ret) * (1 + delisting_return) - 1
                    returns_df = returns_df.with_columns(
                        pl.when(pl.col("date") == last_return_date)
                        .then(pl.lit(combined_ret))
                        .otherwise(pl.col("ret"))
                        .alias("ret")
                    )
                    warnings.append(
                        f"Applied DLRET {delisting_return:.2%} to last trading day "
                        f"(combined return: {combined_ret:.2%})"
                    )

        # Get Fama-French data for event window
        ff_df = self.ff.get_factors(
            start_date=event_window_start,
            end_date=event_window_end,
            frequency="daily",
            model=(
                "ff5"
                if config.expected_return_model
                in (ExpectedReturnModel.FF3, ExpectedReturnModel.FF5)
                else "ff3"
            ),
        )

        # Compute excess returns
        returns_df = self._compute_excess_returns(returns_df, ff_df)

        # Merge with factors
        merged = returns_df.join(ff_df, on="date", how="inner", suffix="_ff")

        # Filter out null returns to prevent NaN propagation in AR calculations
        null_count = merged.filter(
            pl.col("ret").is_null() | pl.col("rf").is_null() | pl.col("mkt_rf").is_null()
        ).height
        if null_count > 0:
            warnings.append(f"Filtered {null_count} rows with null returns/factors")
            merged = merged.filter(
                pl.col("ret").is_not_null()
                & pl.col("rf").is_not_null()
                & pl.col("mkt_rf").is_not_null()
            )

        # Compute expected returns and abnormal returns
        if config.expected_return_model == ExpectedReturnModel.MEAN_ADJUSTED:
            merged = merged.with_columns(pl.lit(model_result.alpha).alias("expected_ret"))
        elif config.expected_return_model == ExpectedReturnModel.MARKET:
            merged = merged.with_columns(
                (pl.lit(model_result.alpha) + pl.lit(model_result.beta) * pl.col("mkt_rf")).alias(
                    "expected_ret"
                )
            )
        elif config.expected_return_model == ExpectedReturnModel.FF3:
            betas = model_result.factor_betas or {}
            merged = merged.with_columns(
                (
                    pl.lit(model_result.alpha)
                    + pl.lit(betas.get("MKT", 0)) * pl.col("mkt_rf")
                    + pl.lit(betas.get("SMB", 0)) * pl.col("smb")
                    + pl.lit(betas.get("HML", 0)) * pl.col("hml")
                ).alias("expected_ret")
            )
        else:  # FF5
            betas = model_result.factor_betas or {}
            merged = merged.with_columns(
                (
                    pl.lit(model_result.alpha)
                    + pl.lit(betas.get("MKT", 0)) * pl.col("mkt_rf")
                    + pl.lit(betas.get("SMB", 0)) * pl.col("smb")
                    + pl.lit(betas.get("HML", 0)) * pl.col("hml")
                    + pl.lit(betas.get("RMW", 0)) * pl.col("rmw")
                    + pl.lit(betas.get("CMA", 0)) * pl.col("cma")
                ).alias("expected_ret")
            )

        # Compute abnormal returns
        merged = merged.with_columns((pl.col("excess_ret") - pl.col("expected_ret")).alias("ar"))

        # Compute relative day
        merged = merged.sort("date")
        date_list = merged["date"].to_list()
        event_idx = date_list.index(adjusted_event_date) if adjusted_event_date in date_list else 0
        relative_days = [i - event_idx for i in range(len(date_list))]
        merged = merged.with_columns(pl.Series("relative_day", relative_days))

        # Winsorize ARs if configured
        ar_array = merged["ar"].to_numpy()
        ar_winsorized, n_lower, n_upper = _winsorize(ar_array, config.winsorize_ar_percentile)
        if n_lower > 0 or n_upper > 0:
            warnings.append(f"Winsorized {n_lower} lower, {n_upper} upper AR values")
            merged = merged.with_columns(pl.Series("ar", ar_winsorized))

        # Compute CARs for different windows
        pre_mask = merged["relative_day"] < 0
        event_mask = merged["relative_day"] == 0
        post_mask = merged["relative_day"] > 0

        car_pre = float(merged.filter(pre_mask)["ar"].sum() or 0)
        car_event = float(merged.filter(event_mask)["ar"].sum() or 0)
        car_post = float(merged.filter(post_mask)["ar"].sum() or 0)
        car_window = car_pre + car_event + car_post

        # Compute Newey-West standard error
        ar_series = merged["ar"].to_numpy()
        se_car, lags_used = _compute_newey_west_se(ar_series, config.newey_west_lags)

        # T-statistic and p-value
        t_stat = car_window / se_car if se_car > 0 else float("nan")
        p_value = (
            2 * (1 - t_dist.cdf(abs(t_stat), df=len(ar_series) - 1))
            if not np.isnan(t_stat)
            else float("nan")
        )
        is_significant = p_value < 0.05 if not np.isnan(p_value) else False

        # Volume analysis
        vol_data = merged.select(["vol"]).filter(pl.col("vol").is_not_null())
        abnormal_volume = None
        volume_estimation_avg = None

        if vol_data.height > 0:
            # Get estimation period volume for comparison
            est_vol_df = self.crsp.get_daily_prices(
                start_date=model_result.estimation_start,
                end_date=model_result.estimation_end,
                symbols=[symbol],
                columns=["vol"],
                as_of_date=as_of,
            )
            if not est_vol_df.is_empty():
                vol_mean = est_vol_df["vol"].mean()
                volume_estimation_avg = (
                    float(cast("float | int", vol_mean)) if vol_mean is not None else 0.0
                )
                if volume_estimation_avg > 0:
                    event_vol = float(merged.filter(event_mask)["vol"].sum() or 0)
                    abnormal_volume = (event_vol - volume_estimation_avg) / volume_estimation_avg

        # Prepare daily AR DataFrame
        daily_ar = merged.select(
            [
                "relative_day",
                "date",
                "ret",
                "rf",
                "expected_ret",
                "ar",
            ]
        )

        # Additional tests (Patell, BMP) only for market model
        patell_z = None
        bmp_t = None

        # Compute Patell/BMP if configured and using market model
        if (
            config.significance_test in (SignificanceTest.PATELL, SignificanceTest.BMP)
            and config.expected_return_model == ExpectedReturnModel.MARKET
        ):
            # Compute Standardized Abnormal Returns (SAR) with forecast-error adjustment
            mkt_rf_evt = merged["mkt_rf"].to_numpy()
            T1 = model_result.n_observations
            T2 = len(ar_series)
            sigma_hat = model_result.residual_std
            mkt_mean = model_result.market_mean
            Sxx = model_result.market_sxx

            # SAR_i,t = AR_i,t / (σ × sqrt(C_t))
            # C_t = 1 + 1/T1 + (R_m,t - R̄_m)² / Sxx
            sar = np.zeros(T2)
            for t in range(T2):
                C_t = 1 + 1 / T1 + (mkt_rf_evt[t] - mkt_mean) ** 2 / Sxx
                sar[t] = ar_series[t] / (sigma_hat * np.sqrt(C_t)) if sigma_hat > 0 else 0

            # SCAR = (1/sqrt(T2)) × Σ SAR_t
            scar = np.sum(sar) / np.sqrt(T2)

            if config.significance_test == SignificanceTest.PATELL:
                # For single event, Z_patell = SCAR (~ N(0,1) under H0)
                patell_z = float(scar)
            else:  # BMP
                # BMP DOF correction: sqrt((T1-2)/(T1-4))
                if T1 > 4:
                    dof_correction = np.sqrt((T1 - 2) / (T1 - 4))
                    scar_bmp = dof_correction * scar
                    # For single event, t_bmp = SCAR_bmp (interpret as standardized)
                    bmp_t = float(scar_bmp)
                else:
                    warnings.append(f"BMP requires T1 > 4, got {T1}")

        return EventStudyAnalysis(
            dataset_version_id=version_info.composite_version_id,
            dataset_versions=version_info.versions,
            computation_timestamp=datetime.now(UTC),
            as_of_date=as_of,
            event_id=event_id,
            symbol=symbol,
            permno=permno,
            event_date=event_date,
            adjusted_event_date=adjusted_event_date,
            event_type=event_type,
            config=config,
            alpha=model_result.alpha,
            beta=model_result.beta,
            model_type=config.expected_return_model,
            car_pre=car_pre,
            car_event=car_event,
            car_post=car_post,
            car_window=car_window,
            daily_ar=daily_ar,
            abnormal_volume=abnormal_volume,
            volume_estimation_avg=volume_estimation_avg,
            t_statistic=t_stat,
            p_value=p_value,
            is_significant=is_significant,
            patell_z=patell_z,
            bmp_t=bmp_t,
            se_car=se_car,
            newey_west_lags=lags_used,
            is_delisted=is_delisted,
            delisting_return=delisting_return,
            warnings=warnings,
        )

    def analyze_pead(
        self,
        earnings_events: pl.DataFrame,
        holding_period_days: int = 60,
        config: EventStudyConfig | None = None,
        as_of: date | None = None,
    ) -> PEADAnalysisResult:
        """Analyze post-earnings announcement drift.

        Args:
            earnings_events: DataFrame with columns [symbol, event_date, surprise_pct].
                            event_date should be announcement date; analysis starts
                            from first trading day after announcement.
            holding_period_days: Days to hold after announcement.
            config: Override default config.
            as_of: Point-in-time date for PIT queries.

        Returns:
            PEADAnalysisResult with quintile results.
        """
        config = config or self.config
        warnings: list[str] = []

        # Validate required columns
        required_cols = {"symbol", "event_date", "surprise_pct"}
        if not required_cols.issubset(set(earnings_events.columns)):
            raise ValueError(f"earnings_events must have columns: {required_cols}")

        # Get version info
        version_info = self._get_version_info(as_of)

        # Remove nulls
        events = earnings_events.filter(
            pl.col("surprise_pct").is_not_null()
            & pl.col("event_date").is_not_null()
            & pl.col("symbol").is_not_null()
        )

        n_total = events.height
        if n_total == 0:
            raise DataNotFoundError("No valid earnings events after filtering nulls")

        # Handle overlapping events using the input config
        events, n_overlapping = self._handle_overlapping_events(
            events, symbol_col="symbol", date_col="event_date", config=config
        )

        if n_overlapping > 0:
            warnings.append(f"Dropped {n_overlapping} overlapping events")

        # Detect clustering
        clustering_info = _detect_event_clustering(events, date_col="event_date")
        mitigation = _select_clustering_mitigation(clustering_info, config)

        if clustering_info["clustering_severe"]:
            warnings.append(
                f"Severe event clustering detected: max {clustering_info['max_events_same_day']} "
                f"events on same day, using {mitigation.value} mitigation"
            )

        # Create PEAD config with adjusted post_window
        pead_config = EventStudyConfig(
            estimation_window=config.estimation_window,
            gap_days=config.gap_days,
            pre_window=0,  # PEAD starts from day after announcement
            post_window=holding_period_days,
            min_estimation_obs=config.min_estimation_obs,
            expected_return_model=config.expected_return_model,
            significance_test=config.significance_test,
            newey_west_lags=config.newey_west_lags,
            overlap_policy=config.overlap_policy,
            min_days_between_events=config.min_days_between_events,
            clustering_mitigation=mitigation,
            winsorize_ar_percentile=config.winsorize_ar_percentile,
            cap_beta=config.cap_beta,
            roll_nontrading_direction=config.roll_nontrading_direction,
        )

        # Compute CAR for each event
        results_list = []
        n_excluded = 0

        for row in events.iter_rows(named=True):
            symbol = row["symbol"]
            event_date = row["event_date"]
            surprise = row["surprise_pct"]

            try:
                car_result = self.compute_car(
                    symbol=symbol,
                    event_date=event_date,
                    event_type="earnings",
                    config=pead_config,
                    as_of=as_of,
                )
                results_list.append(
                    {
                        "symbol": symbol,
                        "event_date": event_date,
                        "surprise_pct": surprise,
                        "car": car_result.car_window,
                        "se": car_result.se_car,
                        "t_stat": car_result.t_statistic,
                    }
                )
            except DataNotFoundError as e:
                logger.debug(f"Skipping {symbol} {event_date}: {e}")
                n_excluded += 1
                continue

        if not results_list:
            raise DataNotFoundError("No valid event results computed")

        results_df = pl.DataFrame(results_list)
        n_events = results_df.height

        # Form quintiles by surprise
        # Check if we have enough events per quintile
        min_per_quintile = 5
        use_quintiles = n_events >= 5 * min_per_quintile

        if use_quintiles:
            n_groups = 5
            group_name = "quintile"
        else:
            n_groups = 3
            group_name = "tercile"
            warnings.append(f"Insufficient events for quintiles ({n_events}), using terciles")

        # Assign groups
        results_df = results_df.sort("surprise_pct")
        group_size = n_events // n_groups
        groups = []
        for i in range(n_events):
            g = min(i // group_size + 1, n_groups)
            groups.append(g)
        results_df = results_df.with_columns(pl.Series(group_name, groups))

        # Compute quintile/tercile statistics
        # Base aggregation
        quintile_stats = (
            results_df.group_by(group_name)
            .agg(
                [
                    pl.len().alias("n_events"),
                    pl.col("surprise_pct").mean().alias("avg_surprise"),
                    pl.col("car").mean().alias("car"),
                    pl.col("car").std().alias("car_std"),
                ]
            )
            .sort(group_name)
        )

        # Compute SE and t-stats per quintile using appropriate method
        if mitigation == ClusteringMitigation.CLUSTERED_SE:
            # Use cluster-robust standard errors (clustered by event date)
            clustered_se_list = []
            clustered_t_list = []

            for q in range(1, n_groups + 1):
                quintile_data = results_df.filter(pl.col(group_name) == q)
                if quintile_data.height < 2:
                    clustered_se_list.append(float("nan"))
                    clustered_t_list.append(float("nan"))
                    continue

                cars = quintile_data["car"].to_numpy()
                # Use event dates as cluster IDs (convert to ordinals)
                dates = quintile_data["event_date"].to_list()
                cluster_ids = np.array([d.toordinal() for d in dates])

                se_clustered, t_stat_clustered, _ = _compute_clustered_se(cars, cluster_ids)
                clustered_se_list.append(se_clustered)
                clustered_t_list.append(t_stat_clustered)

            quintile_stats = quintile_stats.with_columns(
                [
                    pl.Series("se", clustered_se_list),
                    pl.Series("t_stat", clustered_t_list),
                ]
            )
        else:
            # Standard SE: σ / sqrt(n)
            quintile_stats = quintile_stats.with_columns(
                [
                    (pl.col("car_std") / pl.col("n_events").sqrt()).alias("se"),
                ]
            )
            quintile_stats = quintile_stats.with_columns(
                [
                    (pl.col("car") / pl.col("se")).alias("t_stat"),
                ]
            )

        # Compute p-values using actual degrees of freedom (n_events - 1)
        # Use struct to pass both t_stat and n_events to the lambda
        def compute_p_value(row: dict[str, Any]) -> float:
            t_val = row["t_stat"]
            n = row["n_events"]
            if t_val is None or np.isnan(t_val) or n < 2:
                return float("nan")
            df = max(n - 1, 1)  # df = n - 1, minimum 1
            return float(2 * (1 - t_dist.cdf(abs(t_val), df=df)))

        quintile_stats = quintile_stats.with_columns(
            [
                pl.struct(["t_stat", "n_events"])
                .map_elements(compute_p_value, return_dtype=pl.Float64)
                .alias("p_value"),
            ]
        )

        # Rename quintile column for consistency
        quintile_stats = quintile_stats.rename({group_name: "quintile"})

        # Compute drift statistics (high - low)
        low_car = quintile_stats.filter(pl.col("quintile") == 1)["car"][0]
        high_car = quintile_stats.filter(pl.col("quintile") == n_groups)["car"][0]
        drift_magnitude = (
            float(high_car - low_car) if low_car is not None and high_car is not None else 0.0
        )

        # Compute drift t-stat (difference of means test)
        low_se = quintile_stats.filter(pl.col("quintile") == 1)["se"][0]
        high_se = quintile_stats.filter(pl.col("quintile") == n_groups)["se"][0]

        # Handle None/NaN SE values
        if low_se is None or high_se is None or np.isnan(low_se) or np.isnan(high_se):
            drift_t_stat = float("nan")
            drift_significant = False
            warnings.append("Unable to compute drift t-stat due to insufficient data in quintiles")
        else:
            drift_se = np.sqrt(low_se**2 + high_se**2)
            drift_t_stat = drift_magnitude / drift_se if drift_se > 0 else float("nan")
            drift_significant = abs(drift_t_stat) > 1.96 if not np.isnan(drift_t_stat) else False

        # Get date range
        analysis_start = events.select(pl.col("event_date").min()).item()
        analysis_end = events.select(pl.col("event_date").max()).item()

        return PEADAnalysisResult(
            dataset_version_id=version_info.composite_version_id,
            dataset_versions=version_info.versions,
            computation_timestamp=datetime.now(UTC),
            as_of_date=as_of,
            holding_period_days=holding_period_days,
            n_events=n_events,
            n_events_excluded=n_excluded + n_overlapping,
            analysis_start=analysis_start,
            analysis_end=analysis_end,
            config=pead_config,
            quintile_results=quintile_stats,
            drift_magnitude=drift_magnitude,
            drift_t_stat=drift_t_stat,
            drift_significant=drift_significant,
            n_overlapping_dropped=n_overlapping,
            clustering_mitigation_used=mitigation,
            clustering_info=clustering_info,
            warnings=warnings,
        )

    def analyze_index_rebalance(
        self,
        index_changes: pl.DataFrame,
        index_name: str = "SP500",
        use_announcement_date: bool = False,
        config: EventStudyConfig | None = None,
        as_of: date | None = None,
    ) -> IndexRebalanceResult:
        """Analyze price impact of index additions/deletions.

        Args:
            index_changes: DataFrame with columns:
                          [symbol, effective_date, action, announcement_date (optional)]
                          action: 'add' or 'drop'
            index_name: Name of index for labeling.
            use_announcement_date: If True, use announcement_date as event date.
            config: Override default config.
            as_of: Point-in-time date for PIT queries.

        Returns:
            IndexRebalanceResult with addition/deletion effects.
        """
        config = config or self.config
        warnings: list[str] = []

        # Validate required columns
        required_cols = {"symbol", "effective_date", "action"}
        if not required_cols.issubset(set(index_changes.columns)):
            raise ValueError(f"index_changes must have columns: {required_cols}")

        # Get version info
        version_info = self._get_version_info(as_of)

        # Determine event date column
        if use_announcement_date:
            if "announcement_date" not in index_changes.columns:
                warnings.append(
                    "announcement_date requested but not available, using effective_date"
                )
                date_col = "effective_date"
            else:
                date_col = "announcement_date"
        else:
            date_col = "effective_date"

        # Add event_date column
        index_changes = index_changes.with_columns(pl.col(date_col).alias("event_date"))

        # Split into additions and deletions
        additions = index_changes.filter(pl.col("action").str.to_lowercase() == "add")
        deletions = index_changes.filter(
            pl.col("action").str.to_lowercase().is_in(["drop", "delete", "remove"])
        )

        n_additions = additions.height
        n_deletions = deletions.height

        if n_additions == 0 and n_deletions == 0:
            raise DataNotFoundError("No additions or deletions found in index_changes")

        # Detect clustering
        all_events = (
            pl.concat([additions, deletions])
            if n_additions > 0 and n_deletions > 0
            else (additions if n_additions > 0 else deletions)
        )
        clustering_info = _detect_event_clustering(all_events, date_col="event_date")
        mitigation = _select_clustering_mitigation(clustering_info, config)

        if clustering_info["clustering_severe"]:
            warnings.append(
                f"Severe event clustering detected: using {mitigation.value} mitigation"
            )

        # Process additions
        addition_results_list = []
        for row in additions.iter_rows(named=True):
            symbol = row["symbol"]
            event_date = row["event_date"]

            try:
                car_result = self.compute_car(
                    symbol=symbol,
                    event_date=event_date,
                    event_type="index_add",
                    config=config,
                    as_of=as_of,
                )
                addition_results_list.append(
                    {
                        "symbol": symbol,
                        "event_date": event_date,
                        "car_pre": car_result.car_pre,
                        "car_post": car_result.car_post,
                        "car_window": car_result.car_window,
                        "t_stat": car_result.t_statistic,
                        "abnormal_volume": car_result.abnormal_volume,
                    }
                )
            except DataNotFoundError as e:
                logger.debug(f"Skipping addition {symbol} {event_date}: {e}")
                continue

        # Process deletions
        deletion_results_list = []
        for row in deletions.iter_rows(named=True):
            symbol = row["symbol"]
            event_date = row["event_date"]

            try:
                car_result = self.compute_car(
                    symbol=symbol,
                    event_date=event_date,
                    event_type="index_drop",
                    config=config,
                    as_of=as_of,
                )
                deletion_results_list.append(
                    {
                        "symbol": symbol,
                        "event_date": event_date,
                        "car_pre": car_result.car_pre,
                        "car_post": car_result.car_post,
                        "car_window": car_result.car_window,
                        "t_stat": car_result.t_statistic,
                        "abnormal_volume": car_result.abnormal_volume,
                    }
                )
            except DataNotFoundError as e:
                logger.debug(f"Skipping deletion {symbol} {event_date}: {e}")
                continue

        # Create result DataFrames
        addition_results = (
            pl.DataFrame(addition_results_list)
            if addition_results_list
            else pl.DataFrame(
                schema={
                    "symbol": pl.Utf8,
                    "event_date": pl.Date,
                    "car_pre": pl.Float64,
                    "car_post": pl.Float64,
                    "car_window": pl.Float64,
                    "t_stat": pl.Float64,
                    "abnormal_volume": pl.Float64,
                }
            )
        )
        deletion_results = (
            pl.DataFrame(deletion_results_list)
            if deletion_results_list
            else pl.DataFrame(
                schema={
                    "symbol": pl.Utf8,
                    "event_date": pl.Date,
                    "car_pre": pl.Float64,
                    "car_post": pl.Float64,
                    "car_window": pl.Float64,
                    "t_stat": pl.Float64,
                    "abnormal_volume": pl.Float64,
                }
            )
        )

        # Compute aggregate statistics with optional clustered SE
        def _compute_aggregate_stats(
            results: pl.DataFrame,
            use_clustered_se: bool = False,
        ) -> tuple[float, float, float, bool, float]:
            if results.is_empty():
                return 0.0, 0.0, 0.0, False, 0.0

            car_pre_mean = results["car_pre"].mean()
            car_pre = (
                float(cast("float | int", car_pre_mean)) if car_pre_mean is not None else 0.0
            )
            car_post_mean = results["car_post"].mean()
            car_post = (
                float(cast("float | int", car_post_mean)) if car_post_mean is not None else 0.0
            )
            if "abnormal_volume" in results.columns:
                vol_mean = results["abnormal_volume"].mean()
                vol_change = (
                    float(cast("float | int", vol_mean)) if vol_mean is not None else 0.0
                )
            else:
                vol_change = 0.0

            # Compute t-stat for aggregate CAR
            if use_clustered_se and results.height >= 2:
                # Use clustered SE (by event date)
                cars = results["car_window"].to_numpy()
                dates = results["event_date"].to_list()
                cluster_ids = np.array([d.toordinal() for d in dates])

                _, t_stat, _ = _compute_clustered_se(cars, cluster_ids)
                if np.isnan(t_stat):
                    # Fallback to simple t-stat
                    car_mean_val = results["car_window"].mean()
                    car_mean = (
                        float(cast("float | int", car_mean_val))
                        if car_mean_val is not None
                        else 0.0
                    )
                    car_std_val = results["car_window"].std()
                    car_std = (
                        float(cast("float | int", car_std_val))
                        if car_std_val is not None
                        else 0.0
                    )
                    n = results.height
                    se = car_std / np.sqrt(n) if n > 0 else float("nan")
                    t_stat = car_mean / se if se > 0 else float("nan")
            else:
                # Simple t-stat: mean / (std / sqrt(n))
                car_mean_val = results["car_window"].mean()
                car_mean = (
                    float(cast("float | int", car_mean_val))
                    if car_mean_val is not None
                    else 0.0
                )
                car_std_val = results["car_window"].std()
                car_std = (
                    float(cast("float | int", car_std_val))
                    if car_std_val is not None
                    else 0.0
                )
                n = results.height
                se = car_std / np.sqrt(n) if n > 0 else float("nan")
                t_stat = car_mean / se if se > 0 else float("nan")

            is_sig = abs(t_stat) > 1.96 if not np.isnan(t_stat) else False

            return car_pre, car_post, t_stat, is_sig, vol_change

        use_clustered = mitigation == ClusteringMitigation.CLUSTERED_SE
        add_pre, add_post, add_t, add_sig, add_vol = _compute_aggregate_stats(
            addition_results, use_clustered_se=use_clustered
        )
        del_pre, del_post, del_t, del_sig, del_vol = _compute_aggregate_stats(
            deletion_results, use_clustered_se=use_clustered
        )

        # Compute announcement-effective gap if applicable
        announcement_effective_gap = None
        if "announcement_date" in index_changes.columns and not use_announcement_date:
            gaps = index_changes.filter(
                pl.col("announcement_date").is_not_null() & pl.col("effective_date").is_not_null()
            ).with_columns(
                (pl.col("effective_date") - pl.col("announcement_date"))
                .dt.total_days()
                .alias("gap_days")
            )
            if gaps.height > 0:
                gap_mean = gaps["gap_days"].mean()
                announcement_effective_gap = (
                    float(cast("float | int", gap_mean)) if gap_mean is not None else 0.0
                )

        return IndexRebalanceResult(
            dataset_version_id=version_info.composite_version_id,
            dataset_versions=version_info.versions,
            computation_timestamp=datetime.now(UTC),
            as_of_date=as_of,
            index_name=index_name,
            config=config,
            n_additions=len(addition_results_list),
            n_deletions=len(deletion_results_list),
            addition_car_pre=add_pre,
            addition_car_post=add_post,
            addition_t_stat=add_t,
            addition_significant=add_sig,
            deletion_car_pre=del_pre,
            deletion_car_post=del_post,
            deletion_t_stat=del_t,
            deletion_significant=del_sig,
            addition_volume_change=add_vol,
            deletion_volume_change=del_vol,
            uses_announcement_date=use_announcement_date,
            announcement_effective_gap_days=announcement_effective_gap,
            addition_results=addition_results,
            deletion_results=deletion_results,
            clustering_mitigation_used=mitigation,
            clustering_info=clustering_info,
            warnings=warnings,
        )
