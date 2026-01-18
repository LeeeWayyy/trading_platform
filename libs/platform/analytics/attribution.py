"""Factor Attribution Analysis for Fama-French factor models.

Implements Fama-French factor attribution with robust standard errors:
- FF3/FF5/FF6 factor regression
- Rolling factor exposure tracking (252 trading-day window)
- Microcap filter (20th percentile AND $100M)
- Currency filter with PIT validation
- Robust standard errors (OLS, HC3, Newey-West)
- VIF multicollinearity check
- Return decomposition

All outputs include dataset_version_id for reproducibility and PIT support.

References:
- Fama & French (1993): Common Risk Factors in the Returns on Stocks and Bonds
- Fama & French (2015): A Five-Factor Asset Pricing Model
- Carhart (1997): On Persistence in Mutual Fund Performance
- Newey & West (1987): HAC Standard Errors
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any, Literal

import numpy as np
import polars as pl

if TYPE_CHECKING:
    from libs.data.data_providers.crsp_local_provider import CRSPLocalProvider
    from libs.data.data_providers.fama_french_local_provider import FamaFrenchLocalProvider
    from libs.data.data_quality.versioning import DatasetVersionManager

logger = logging.getLogger(__name__)


# =============================================================================
# Constants - Factor Column Names
# =============================================================================

FF3_FACTOR_COLS: tuple[str, ...] = ("mkt_rf", "smb", "hml")
FF5_FACTOR_COLS: tuple[str, ...] = ("mkt_rf", "smb", "hml", "rmw", "cma")
FF6_FACTOR_COLS: tuple[str, ...] = ("mkt_rf", "smb", "hml", "rmw", "cma", "umd")
RISK_FREE_COL: str = "rf"

FACTOR_COLS_BY_MODEL: dict[str, tuple[str, ...]] = {
    "ff3": FF3_FACTOR_COLS,
    "ff5": FF5_FACTOR_COLS,
    "ff6": FF6_FACTOR_COLS,
}


# =============================================================================
# Exceptions
# =============================================================================


class FactorAttributionError(Exception):
    """Base exception for factor attribution errors."""

    pass


class InsufficientObservationsError(FactorAttributionError):
    """Raised when n_observations < min_observations."""

    pass


class DataMismatchError(FactorAttributionError):
    """Raised when portfolio dates don't overlap with factor dates."""

    pass


class PITViolationError(FactorAttributionError):
    """Raised when data extends beyond as_of_date (look-ahead bias)."""

    pass


# =============================================================================
# Configuration
# =============================================================================


@dataclass(frozen=True)
class FactorAttributionConfig:
    """Configuration for factor attribution analysis.

    Attributes:
        model: Factor model (ff3, ff5, or ff6).
        window_trading_days: Rolling window size in TRADING days (not calendar).
        rebalance_freq: Rebalancing frequency for rolling analysis.
        std_errors: Standard error estimation method.
        newey_west_lags: NW lag count (0 = auto-compute via rule-of-thumb).
        min_observations: Minimum observations for valid regression.
        vif_threshold: VIF threshold for multicollinearity warning.
        annualization_factor: Trading days per year for annualization.
        min_market_cap_usd: Minimum market cap in USD (None = no filter).
        market_cap_percentile: Exclude below this percentile (None = no filter).
        currency: Filter to this currency (None = no filter).
        aggregation_method: How to aggregate per-permno returns.
        rebalance_on_filter: Recalculate weights after filtering.
    """

    model: Literal["ff3", "ff5", "ff6"] = "ff5"
    window_trading_days: int = 252
    rebalance_freq: Literal["daily", "weekly", "monthly"] = "monthly"
    std_errors: Literal["ols", "hc3", "newey_west"] = "newey_west"
    newey_west_lags: int = 0  # 0 = auto-compute
    min_observations: int = 60
    vif_threshold: float = 5.0
    annualization_factor: int = 252
    min_market_cap_usd: float | None = 100_000_000  # $100M default
    market_cap_percentile: float | None = 0.20  # 20th percentile
    currency: str | None = "USD"
    aggregation_method: Literal["equal_weight", "value_weight"] = "equal_weight"
    rebalance_on_filter: bool = True


# =============================================================================
# Result Dataclasses
# =============================================================================


@dataclass(frozen=True)
class AttributionResult:
    """Factor attribution output for dashboard and registry.

    Contains regression results, factor loadings, diagnostics,
    and version tracking for reproducibility.
    """

    # Schema version for forward compatibility
    schema_version: str = "1.0.0"

    # Identification
    portfolio_id: str = ""
    as_of_date: date | None = None
    dataset_version_id: str = ""
    dataset_versions: dict[str, str | None] = field(default_factory=dict)
    snapshot_id: str | None = None
    regression_config: dict[str, Any] = field(default_factory=dict)

    # Core metrics
    alpha_annualized_bps: float = 0.0
    alpha_daily: float = 0.0
    alpha_t_stat: float = 0.0
    alpha_p_value: float = 0.0
    r_squared_adj: float = 0.0
    residual_vol_annualized: float = 0.0

    # Factor loadings
    betas: dict[str, float] = field(default_factory=dict)
    beta_t_stats: dict[str, float] = field(default_factory=dict)
    beta_p_values: dict[str, float] = field(default_factory=dict)

    # Diagnostics
    n_observations: int = 0
    multicollinearity_warnings: list[str] = field(default_factory=list)
    durbin_watson: float = 0.0
    filter_stats: dict[str, Any] = field(default_factory=dict)

    # Reproducibility
    computation_timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_registry_dict(self) -> dict[str, Any]:
        """Serialize result for registry storage."""
        return {
            "schema_version": self.schema_version,
            "portfolio_id": self.portfolio_id,
            "as_of_date": self.as_of_date.isoformat() if self.as_of_date else None,
            "dataset_version_id": self.dataset_version_id,
            "dataset_versions": self.dataset_versions,
            "snapshot_id": self.snapshot_id,
            "regression_config": self.regression_config,
            "alpha_annualized_bps": self._nan_to_none(self.alpha_annualized_bps),
            "alpha_daily": self._nan_to_none(self.alpha_daily),
            "alpha_t_stat": self._nan_to_none(self.alpha_t_stat),
            "alpha_p_value": self._nan_to_none(self.alpha_p_value),
            "r_squared_adj": self._nan_to_none(self.r_squared_adj),
            "residual_vol_annualized": self._nan_to_none(self.residual_vol_annualized),
            "betas": {k: self._nan_to_none(v) for k, v in self.betas.items()},
            "beta_t_stats": {k: self._nan_to_none(v) for k, v in self.beta_t_stats.items()},
            "beta_p_values": {k: self._nan_to_none(v) for k, v in self.beta_p_values.items()},
            "n_observations": self.n_observations,
            "multicollinearity_warnings": self.multicollinearity_warnings,
            "durbin_watson": self._nan_to_none(self.durbin_watson),
            "filter_stats": self.filter_stats,
            "computation_timestamp": self.computation_timestamp.isoformat(),
        }

    def to_dashboard_dict(self) -> dict[str, Any]:
        """Serialize result for dashboard display."""
        base = self.to_registry_dict()
        if self.alpha_annualized_bps is not None and not np.isnan(self.alpha_annualized_bps):
            base["alpha_display"] = f"{self.alpha_annualized_bps:.1f} bps"
        return base

    @staticmethod
    def _nan_to_none(val: float | None) -> float | None:
        """Convert NaN to None for JSON serialization."""
        if val is None:
            return None
        if isinstance(val, float) and np.isnan(val):
            return None
        return val


@dataclass(frozen=True)
class RollingExposureResult:
    """Rolling factor exposure output."""

    schema_version: str = "1.0.0"
    portfolio_id: str = ""
    exposures: pl.DataFrame | None = None  # [date, factor_name, beta, t_stat, p_value]
    skipped_windows: list[dict[str, Any]] = field(default_factory=list)
    config: dict[str, Any] = field(default_factory=dict)
    dataset_version_id: str = ""
    dataset_versions: dict[str, str | None] = field(default_factory=dict)
    snapshot_id: str | None = None
    computation_timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_registry_dict(self) -> dict[str, Any]:
        """Serialize result for registry storage."""
        exposures_list = None
        if self.exposures is not None:
            exposures_list = self.exposures.to_dicts()
            for row in exposures_list:
                for k, v in row.items():
                    if isinstance(v, float) and np.isnan(v):
                        row[k] = None

        return {
            "schema_version": self.schema_version,
            "portfolio_id": self.portfolio_id,
            "exposures": exposures_list,
            "skipped_windows": self.skipped_windows,
            "config": self.config,
            "dataset_version_id": self.dataset_version_id,
            "dataset_versions": self.dataset_versions,
            "snapshot_id": self.snapshot_id,
            "computation_timestamp": self.computation_timestamp.isoformat(),
        }

    def to_dashboard_dict(self) -> dict[str, Any]:
        """Serialize result for dashboard display."""
        return self.to_registry_dict()


@dataclass(frozen=True)
class ReturnDecompositionResult:
    """Return decomposition output."""

    schema_version: str = "1.0.0"
    portfolio_id: str = ""
    decomposition: pl.DataFrame | None = None
    attribution_result: AttributionResult | None = None
    dataset_version_id: str = ""
    computation_timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_registry_dict(self) -> dict[str, Any]:
        """Serialize result for registry storage."""
        decomp_list = None
        if self.decomposition is not None:
            decomp_list = self.decomposition.to_dicts()
            for row in decomp_list:
                for k, v in row.items():
                    if isinstance(v, float) and np.isnan(v):
                        row[k] = None

        return {
            "schema_version": self.schema_version,
            "portfolio_id": self.portfolio_id,
            "decomposition": decomp_list,
            "attribution_result": (
                self.attribution_result.to_registry_dict() if self.attribution_result else None
            ),
            "dataset_version_id": self.dataset_version_id,
            "computation_timestamp": self.computation_timestamp.isoformat(),
        }

    def to_dashboard_dict(self) -> dict[str, Any]:
        """Serialize result for dashboard display."""
        return self.to_registry_dict()


# =============================================================================
# Main Analyzer Class
# =============================================================================


class FactorAttribution:
    """Fama-French factor attribution with robust standard errors.

    This class implements factor attribution analysis using Fama-French
    factor models (3, 5, or 6-factor). It supports:
    - Robust standard errors (OLS, HC3, Newey-West HAC)
    - Rolling factor exposure tracking
    - Microcap and currency filtering
    - PIT compliance via DatasetVersionManager
    - VIF multicollinearity detection

    Example:
        >>> from libs.platform.analytics.attribution import FactorAttribution, FactorAttributionConfig
        >>> from libs.data.data_providers.fama_french_local_provider import FamaFrenchLocalProvider
        >>>
        >>> config = FactorAttributionConfig(model="ff5", std_errors="newey_west")
        >>> ff_provider = FamaFrenchLocalProvider(storage_path=Path("data/fama_french"))
        >>>
        >>> attribution = FactorAttribution(ff_provider=ff_provider, config=config)
        >>> result = attribution.fit(
        ...     portfolio_returns=portfolio_df,
        ...     start_date=date(2020, 1, 1),
        ...     end_date=date(2022, 12, 31),
        ... )
        >>> print(f"Alpha: {result.alpha_annualized_bps:.1f} bps")
    """

    def __init__(
        self,
        ff_provider: FamaFrenchLocalProvider,
        crsp_provider: CRSPLocalProvider | None = None,
        version_manager: DatasetVersionManager | None = None,
        config: FactorAttributionConfig | None = None,
    ) -> None:
        """Initialize factor attribution analyzer.

        Args:
            ff_provider: Fama-French factor data provider (required).
            crsp_provider: CRSP data provider for market caps (optional).
                Required if microcap filter is enabled without explicit data.
            version_manager: Dataset version manager for PIT compliance (optional).
                Required for reproducible backtests.
            config: Attribution configuration (uses defaults if None).
        """
        self.ff_provider = ff_provider
        self.crsp_provider = crsp_provider
        self.version_manager = version_manager
        self.config = config or FactorAttributionConfig()

    def fit(
        self,
        portfolio_returns: pl.DataFrame,
        start_date: date,
        end_date: date,
        portfolio_id: str = "portfolio",
        as_of_date: date | None = None,
        portfolio_version: str | None = None,
        market_caps: pl.DataFrame | None = None,
        currencies: pl.DataFrame | None = None,
        currency_version: str | None = None,
    ) -> AttributionResult:
        """Run factor attribution regression.

        Args:
            portfolio_returns: [date, return] for aggregated portfolio OR
                [date, permno, return] for individual stock filtering.
            start_date: Start of analysis period.
            end_date: End of analysis period.
            portfolio_id: Identifier for this portfolio.
            as_of_date: Point-in-time date for PIT compliance.
            portfolio_version: Version ID if portfolio from versioned registry.
            market_caps: Market cap data override [date, permno, market_cap].
            currencies: Currency data override [permno, currency].
            currency_version: Version ID for currency data.

        Returns:
            AttributionResult with regression coefficients and diagnostics.

        Raises:
            InsufficientObservationsError: If < min_observations after filtering.
            DataMismatchError: If portfolio and factor dates don't overlap.
            PITViolationError: If data extends beyond as_of_date.
            ValueError: If filters enabled but no data source available.
        """
        import statsmodels.api as sm  # type: ignore[import-untyped]
        from statsmodels.stats.stattools import durbin_watson  # type: ignore[import-untyped]

        # Validate PIT constraints and filter portfolio returns
        effective_end = min(end_date, as_of_date) if as_of_date else end_date
        portfolio_returns = self._validate_pit_dates(portfolio_returns, as_of_date)

        # Track if market_caps was externally provided (before internal computation)
        external_market_caps_provided = market_caps is not None

        # Get factor data
        ff_factors = self.ff_provider.get_factors(
            start_date=start_date,
            end_date=effective_end,
            model=self.config.model,
            frequency="daily",
        )

        # PIT-trim factors to effective_end to prevent look-ahead bias
        if "date" in ff_factors.columns:
            ff_factors = ff_factors.filter(pl.col("date") <= effective_end)

        # Get market caps for filtering if needed
        if market_caps is None and self.crsp_provider is not None:
            if self.config.min_market_cap_usd or self.config.market_cap_percentile:
                market_caps = self._compute_market_caps(start_date, effective_end)

        # Apply PIT filtering to market_caps and currencies if provided externally
        if market_caps is not None and as_of_date is not None:
            if "date" in market_caps.columns:
                market_caps = market_caps.filter(pl.col("date") <= as_of_date)

        if currencies is not None and as_of_date is not None:
            if "date" in currencies.columns:
                currencies = currencies.filter(pl.col("date") <= as_of_date)

        # Apply filters if portfolio has permno column
        filter_stats: dict[str, Any] = {"total": len(portfolio_returns)}
        if "permno" in portfolio_returns.columns:
            portfolio_returns, microcap_stats = self._apply_microcap_filter(
                portfolio_returns, market_caps
            )
            filter_stats.update(microcap_stats)

            portfolio_returns, currency_stats = self._apply_currency_filter(
                portfolio_returns, currencies
            )
            filter_stats.update(currency_stats)

            # Aggregate to portfolio level
            portfolio_returns = self._aggregate_returns(portfolio_returns, market_caps)

        filter_stats["after_filters"] = len(portfolio_returns)

        # Align data
        aligned_portfolio, aligned_factors = self._align_data(portfolio_returns, ff_factors)

        # Check minimum observations
        n_obs = len(aligned_portfolio)
        if n_obs < self.config.min_observations:
            raise InsufficientObservationsError(
                f"Only {n_obs} observations after filtering, "
                f"need {self.config.min_observations}"
            )

        # Compute excess returns
        excess_returns = (
            aligned_portfolio["return"].to_numpy() - aligned_factors[RISK_FREE_COL].to_numpy()
        )

        # Get factor matrix as pandas DataFrame for named coefficient extraction
        factor_cols = list(FACTOR_COLS_BY_MODEL[self.config.model])
        X_df = aligned_factors.select(factor_cols).to_pandas()
        X_with_const = sm.add_constant(X_df, has_constant="add")

        # Check multicollinearity
        vif_warnings = self._check_multicollinearity(aligned_factors.select(factor_cols))

        # Run regression
        model = sm.OLS(excess_returns, X_with_const)
        if self.config.std_errors == "ols":
            result = model.fit()
        elif self.config.std_errors == "hc3":
            result = model.fit(cov_type="HC3")
        else:  # newey_west
            nw_lags = self._compute_nw_lags(n_obs)
            result = model.fit(
                cov_type="HAC",
                cov_kwds={"maxlags": nw_lags, "use_correction": True},
            )

        # Extract coefficients by NAME (not position) for safety when columns are dropped
        # This prevents alpha/beta misassignment when statsmodels drops collinear columns
        if "const" not in result.params.index:
            vif_warnings.append("Constant term dropped due to collinearity. Alpha set to 0/NaN.")
            alpha_daily = 0.0
            alpha_t_stat = float("nan")
            alpha_p_value = float("nan")
        else:
            alpha_daily = float(result.params["const"])
            alpha_t_stat = float(result.tvalues["const"])
            alpha_p_value = float(result.pvalues["const"])

        alpha_annualized = alpha_daily * self.config.annualization_factor
        alpha_bps = alpha_annualized * 10000

        # Map coefficients by name - use NaN for dropped factors
        betas: dict[str, float] = {}
        beta_t_stats: dict[str, float] = {}
        beta_p_values: dict[str, float] = {}

        for factor in factor_cols:
            if factor in result.params.index:
                betas[factor] = float(result.params[factor])
                beta_t_stats[factor] = float(result.tvalues[factor])
                beta_p_values[factor] = float(result.pvalues[factor])
            else:
                # Factor was dropped due to collinearity
                vif_warnings.append(f"Factor '{factor}' dropped due to collinearity.")
                betas[factor] = float("nan")
                beta_t_stats[factor] = float("nan")
                beta_p_values[factor] = float("nan")

        # Compute version IDs
        portfolio_ver = portfolio_version or self._compute_content_hash(portfolio_returns)
        currency_ver = currency_version or (
            self._compute_content_hash(currencies) if currencies is not None else None
        )
        # Hash external market_caps when provided by caller (regardless of CRSP provider)
        external_mcap_ver = (
            self._compute_content_hash(market_caps)
            if external_market_caps_provided and market_caps is not None
            else None
        )
        dataset_versions = {
            "fama_french": self._get_ff_version(),
            "crsp": (
                self._get_crsp_version()
                if self.crsp_provider and not external_market_caps_provided
                else None
            ),
            "market_caps": external_mcap_ver,  # External market caps hash when provided
            "portfolio": portfolio_ver,
            "currencies": currency_ver,
        }
        # Use external market_caps version if provided, else CRSP version
        crsp_or_mcap_ver = external_mcap_ver or (
            self._get_crsp_version() if self.crsp_provider else None
        )
        dataset_version_id = self._build_dataset_version_id(
            ff_version=dataset_versions["fama_french"] or "unknown",
            crsp_version=crsp_or_mcap_ver,
            portfolio_version=portfolio_ver,
            currency_version=currency_ver,
            config_hash=self._compute_config_hash(),
        )

        return AttributionResult(
            portfolio_id=portfolio_id,
            as_of_date=as_of_date,
            dataset_version_id=dataset_version_id,
            dataset_versions=dataset_versions,
            regression_config={
                "model": self.config.model,
                "std_errors": self.config.std_errors,
                "newey_west_lags": nw_lags if self.config.std_errors == "newey_west" else None,
            },
            alpha_annualized_bps=alpha_bps,
            alpha_daily=alpha_daily,
            alpha_t_stat=alpha_t_stat,
            alpha_p_value=alpha_p_value,
            r_squared_adj=float(result.rsquared_adj),
            residual_vol_annualized=float(
                result.resid.std() * np.sqrt(self.config.annualization_factor)
            ),
            betas=betas,
            beta_t_stats=beta_t_stats,
            beta_p_values=beta_p_values,
            n_observations=n_obs,
            multicollinearity_warnings=vif_warnings,
            durbin_watson=float(durbin_watson(result.resid)),
            filter_stats=filter_stats,
        )

    def compute_rolling_exposures(
        self,
        portfolio_returns: pl.DataFrame,
        start_date: date,
        end_date: date,
        portfolio_id: str = "portfolio",
        as_of_date: date | None = None,
        market_caps: pl.DataFrame | None = None,
        currencies: pl.DataFrame | None = None,
    ) -> RollingExposureResult:
        """Compute rolling factor exposures over time.

        Args:
            portfolio_returns: [date, return] or [date, permno, return].
            start_date: Start of rolling analysis.
            end_date: End of rolling analysis.
            portfolio_id: Identifier for this portfolio.
            as_of_date: Point-in-time date for PIT compliance.
            market_caps: Market cap data override.
            currencies: Currency data override.

        Returns:
            RollingExposureResult with exposures DataFrame.

        Note:
            PIT compliance: If as_of_date provided, all windows bounded by as_of_date.
            Windows with insufficient observations output NaN and are logged.
        """
        # Validate PIT constraints on portfolio returns
        effective_end = min(end_date, as_of_date) if as_of_date else end_date
        portfolio_returns = self._validate_pit_dates(portfolio_returns, as_of_date)

        # Track if market_caps was externally provided (before internal computation)
        external_market_caps_provided = market_caps is not None

        # Get factor data for full range
        ff_factors = self.ff_provider.get_factors(
            start_date=start_date,
            end_date=effective_end,
            model=self.config.model,
            frequency="daily",
        )

        # PIT-trim factors to effective_end to prevent look-ahead bias
        if "date" in ff_factors.columns:
            ff_factors = ff_factors.filter(pl.col("date") <= effective_end)

        # Get rebalance dates
        rebalance_dates = self._get_rebalance_dates(start_date, effective_end, ff_factors)

        # Apply PIT filtering to market_caps and currencies if provided externally
        if market_caps is not None and as_of_date is not None:
            if "date" in market_caps.columns:
                market_caps = market_caps.filter(pl.col("date") <= as_of_date)
        if currencies is not None and as_of_date is not None:
            if "date" in currencies.columns:
                currencies = currencies.filter(pl.col("date") <= as_of_date)

        # Filter portfolio if needed
        if "permno" in portfolio_returns.columns:
            if market_caps is None and self.crsp_provider is not None:
                market_caps = self._compute_market_caps(start_date, effective_end)
            portfolio_returns, _ = self._apply_microcap_filter(portfolio_returns, market_caps)
            portfolio_returns, _ = self._apply_currency_filter(portfolio_returns, currencies)
            portfolio_returns = self._aggregate_returns(portfolio_returns, market_caps)

        # Build exposures for each window
        exposures_rows: list[dict[str, Any]] = []
        skipped_windows: list[dict[str, Any]] = []
        factor_cols = list(FACTOR_COLS_BY_MODEL[self.config.model])

        for window_end in rebalance_dates:
            # Get window data
            window_start = self._get_window_start(window_end, ff_factors)

            window_portfolio = portfolio_returns.filter(
                (pl.col("date") >= window_start) & (pl.col("date") <= window_end)
            )
            window_factors = ff_factors.filter(
                (pl.col("date") >= window_start) & (pl.col("date") <= window_end)
            )

            # Align
            try:
                aligned_p, aligned_f = self._align_data(window_portfolio, window_factors)
            except DataMismatchError:
                skipped_windows.append(
                    {
                        "date": window_end.isoformat(),
                        "n_obs": 0,
                        "reason": "no_overlap",
                    }
                )
                for factor in factor_cols:
                    exposures_rows.append(
                        {
                            "date": window_end,
                            "factor_name": factor,
                            "beta": float("nan"),
                            "t_stat": float("nan"),
                            "p_value": float("nan"),
                        }
                    )
                continue

            n_obs = len(aligned_p)
            if n_obs < self.config.min_observations:
                skipped_windows.append(
                    {
                        "date": window_end.isoformat(),
                        "n_obs": n_obs,
                        "reason": "insufficient_observations",
                    }
                )
                for factor in factor_cols:
                    exposures_rows.append(
                        {
                            "date": window_end,
                            "factor_name": factor,
                            "beta": float("nan"),
                            "t_stat": float("nan"),
                            "p_value": float("nan"),
                        }
                    )
                continue

            # Run regression for this window using pandas for named coefficient extraction
            import statsmodels.api as sm

            excess_ret = aligned_p["return"].to_numpy() - aligned_f[RISK_FREE_COL].to_numpy()
            X_df = aligned_f.select(factor_cols).to_pandas()
            X_with_const = sm.add_constant(X_df, has_constant="add")

            model = sm.OLS(excess_ret, X_with_const)
            result = model.fit()

            # Extract coefficients by name (not position) for safety
            for factor in factor_cols:
                if factor in result.params.index:
                    exposures_rows.append(
                        {
                            "date": window_end,
                            "factor_name": factor,
                            "beta": float(result.params[factor]),
                            "t_stat": float(result.tvalues[factor]),
                            "p_value": float(result.pvalues[factor]),
                        }
                    )
                else:
                    # Factor dropped due to collinearity
                    exposures_rows.append(
                        {
                            "date": window_end,
                            "factor_name": factor,
                            "beta": float("nan"),
                            "t_stat": float("nan"),
                            "p_value": float("nan"),
                        }
                    )

        if len(exposures_rows) == 0:
            raise InsufficientObservationsError("All windows skipped")

        exposures_df = pl.DataFrame(exposures_rows)

        # Compute content hashes for version tracking
        # Hash external market_caps when provided by caller (regardless of CRSP provider)
        external_mcap_ver = (
            self._compute_content_hash(market_caps)
            if external_market_caps_provided and market_caps is not None
            else None
        )
        currency_version = (
            self._compute_content_hash(currencies) if currencies is not None else None
        )

        # Use external market_caps version if provided, else CRSP version
        crsp_or_mcap_ver = external_mcap_ver or (
            self._get_crsp_version() if self.crsp_provider else None
        )

        return RollingExposureResult(
            portfolio_id=portfolio_id,
            exposures=exposures_df,
            skipped_windows=skipped_windows,
            config={
                "model": self.config.model,
                "window_trading_days": self.config.window_trading_days,
                "rebalance_freq": self.config.rebalance_freq,
            },
            dataset_version_id=self._build_dataset_version_id(
                ff_version=self._get_ff_version() or "unknown",
                crsp_version=crsp_or_mcap_ver,
                portfolio_version=self._compute_content_hash(portfolio_returns),
                currency_version=currency_version,
                config_hash=self._compute_config_hash(),
            ),
            dataset_versions={
                "fama_french": self._get_ff_version(),
                "crsp": (
                    self._get_crsp_version()
                    if self.crsp_provider and not external_market_caps_provided
                    else None
                ),
                "market_caps": external_mcap_ver,  # External market caps hash when provided
                "portfolio": self._compute_content_hash(portfolio_returns),
                "currencies": currency_version,
            },
        )

    def decompose_returns(
        self,
        portfolio_returns: pl.DataFrame,
        attribution_result: AttributionResult,
        ff_factors: pl.DataFrame | None = None,
    ) -> ReturnDecompositionResult:
        """Decompose portfolio returns into factor contributions.

        For each date t:
            portfolio_return[t] = rf[t] + alpha + sum(beta_i * factor_i[t]) + residual[t]

        Args:
            portfolio_returns: [date, return] aggregated portfolio.
            attribution_result: Result from fit() with betas.
            ff_factors: Factor data (if None, fetched from provider).

        Returns:
            ReturnDecompositionResult with daily decomposition.
        """
        if ff_factors is None:
            dates = portfolio_returns["date"].to_list()
            ff_factors = self.ff_provider.get_factors(
                start_date=min(dates),
                end_date=max(dates),
                model=self.config.model,
                frequency="daily",
            )

        # Align
        aligned_p, aligned_f = self._align_data(portfolio_returns, ff_factors)

        # Build decomposition using with_columns to avoid Expr in DataFrame
        factor_cols = list(FACTOR_COLS_BY_MODEL[self.config.model])
        betas = attribution_result.betas
        alpha_daily = attribution_result.alpha_daily
        n_rows = aligned_p.height

        # Start with base columns
        decomp_df = pl.DataFrame(
            {
                "date": aligned_p["date"],
                "portfolio_return": aligned_p["return"],
                "risk_free": aligned_f[RISK_FREE_COL],
            }
        )

        # Add computed columns
        decomp_df = decomp_df.with_columns(
            [
                (pl.col("portfolio_return") - pl.col("risk_free")).alias("excess_return"),
                pl.Series("alpha_contrib", [alpha_daily] * n_rows),
            ]
        )

        # Add factor contributions
        total_factor_contrib = np.zeros(n_rows)
        for factor in factor_cols:
            factor_vals = aligned_f[factor].to_numpy()
            contrib = factor_vals * betas[factor]
            total_factor_contrib += contrib
            decomp_df = decomp_df.with_columns(pl.Series(f"{factor}_contrib", contrib))

        decomp_df = decomp_df.with_columns(pl.Series("total_factor_contrib", total_factor_contrib))

        # Compute residual
        decomp_df = decomp_df.with_columns(
            (
                pl.col("excess_return") - pl.col("alpha_contrib") - pl.col("total_factor_contrib")
            ).alias("residual")
        )

        return ReturnDecompositionResult(
            portfolio_id=attribution_result.portfolio_id,
            decomposition=decomp_df,
            attribution_result=attribution_result,
            dataset_version_id=attribution_result.dataset_version_id,
        )

    def check_multicollinearity(self, ff_factors: pl.DataFrame) -> list[str]:
        """Check for multicollinearity using VIF."""
        return self._check_multicollinearity(ff_factors)

    # =========================================================================
    # Private Helper Methods
    # =========================================================================

    def _validate_pit_dates(self, data: pl.DataFrame, as_of_date: date | None) -> pl.DataFrame:
        """Filter data to respect as_of_date and validate.

        Returns:
            Filtered DataFrame with dates <= as_of_date.

        Raises:
            PITViolationError: If all data is after as_of_date (look-ahead bias).
        """
        if as_of_date is None:
            return data

        # Filter to as_of_date
        filtered = data.filter(pl.col("date") <= as_of_date)

        # Raise error if all data was filtered out (PIT violation)
        if filtered.height == 0 and data.height > 0:
            min_date = str(data["date"].min())
            max_date = str(data["date"].max())
            raise PITViolationError(
                f"All portfolio data ({min_date} to {max_date}) is after "
                f"as_of_date ({as_of_date}). This would cause look-ahead bias."
            )

        return filtered

    def _compute_market_caps(self, start_date: date, end_date: date) -> pl.DataFrame:
        """Compute market caps from CRSP prices."""
        if self.crsp_provider is None:
            raise ValueError("crsp_provider required for market cap computation")

        prices = self.crsp_provider.get_daily_prices(
            start_date=start_date,
            end_date=end_date,
            columns=["date", "permno", "prc", "shrout"],
            adjust_prices=False,  # Use spot prices with spot shares for accurate market cap
        )

        # CRSP shrout is in THOUSANDS
        return prices.with_columns(
            (pl.col("prc").abs() * pl.col("shrout") * 1000).alias("market_cap")
        ).select(["date", "permno", "market_cap"])

    def _apply_microcap_filter(
        self,
        portfolio_returns: pl.DataFrame,
        market_caps: pl.DataFrame | None,
    ) -> tuple[pl.DataFrame, dict[str, Any]]:
        """Filter out microcap stocks."""
        stats: dict[str, Any] = {"total": len(portfolio_returns)}

        if "permno" not in portfolio_returns.columns:
            stats["after_microcap"] = stats["total"]
            stats["microcap_filter_applied"] = False
            return portfolio_returns, stats

        if market_caps is None:
            if self.config.min_market_cap_usd or self.config.market_cap_percentile:
                raise ValueError("Microcap filter enabled but no market_caps data provided")
            stats["after_microcap"] = stats["total"]
            stats["microcap_filter_applied"] = False
            return portfolio_returns, stats

        # PIT-safe: Compute percentile PER DATE
        if self.config.market_cap_percentile is not None:
            daily_thresholds = market_caps.group_by("date").agg(
                pl.col("market_cap")
                .quantile(self.config.market_cap_percentile)
                .alias("percentile_threshold")
            )
            market_caps = market_caps.join(daily_thresholds, on="date")
            market_caps = market_caps.filter(pl.col("market_cap") >= pl.col("percentile_threshold"))

        # Apply absolute threshold
        if self.config.min_market_cap_usd is not None:
            market_caps = market_caps.filter(pl.col("market_cap") >= self.config.min_market_cap_usd)

        filtered = portfolio_returns.join(
            market_caps.select(["date", "permno"]).unique(),
            on=["date", "permno"],
            how="inner",
        )

        stats["after_microcap"] = len(filtered)
        stats["microcap_filter_applied"] = True
        return filtered, stats

    def _apply_currency_filter(
        self,
        portfolio_returns: pl.DataFrame,
        currencies: pl.DataFrame | None,
    ) -> tuple[pl.DataFrame, dict[str, Any]]:
        """Filter to specified currency.

        If currencies has a 'date' column, join on both permno and date
        to handle time-varying currency mappings and avoid PIT violations.
        """
        stats: dict[str, Any] = {}

        if self.config.currency is None:
            stats["currency_filter_applied"] = False
            return portfolio_returns, stats

        if "permno" not in portfolio_returns.columns:
            stats["currency_filter_applied"] = False
            return portfolio_returns, stats

        if currencies is None:
            raise ValueError(
                f"Currency filter for '{self.config.currency}' enabled but "
                "no currencies data provided"
            )

        # Filter currencies to target currency
        target_currencies = currencies.filter(pl.col("currency") == self.config.currency)

        # Determine join columns based on whether currencies has a date column
        if "date" in currencies.columns and "date" in portfolio_returns.columns:
            # Join on both permno and date to handle time-varying currency mappings
            filtered = portfolio_returns.join(
                target_currencies.select(["permno", "date"]).unique(),
                on=["permno", "date"],
                how="inner",
            )
        else:
            # Static currency mapping - join only on permno
            filtered = portfolio_returns.join(
                target_currencies.select(["permno"]).unique(),
                on="permno",
                how="inner",
            )

        stats["after_currency"] = len(filtered)
        stats["currency_filter_applied"] = True
        return filtered, stats

    def _aggregate_returns(
        self,
        portfolio_returns: pl.DataFrame,
        market_caps: pl.DataFrame | None,
    ) -> pl.DataFrame:
        """Aggregate per-permno returns to portfolio level."""
        if "permno" not in portfolio_returns.columns:
            return portfolio_returns

        if self.config.aggregation_method == "equal_weight":
            return portfolio_returns.group_by("date").agg(pl.col("return").mean().alias("return"))

        # Value weight
        if market_caps is None:
            raise ValueError("market_caps required for value_weight aggregation")

        weighted = portfolio_returns.join(market_caps, on=["date", "permno"], how="left")
        # Sort by permno and date before forward-fill to prevent future data leakage
        weighted = weighted.sort(["permno", "date"])
        weighted = weighted.with_columns(pl.col("market_cap").forward_fill().over("permno")).filter(
            pl.col("market_cap").is_not_null()
        )

        return weighted.group_by("date").agg(
            ((pl.col("return") * pl.col("market_cap")).sum() / pl.col("market_cap").sum()).alias(
                "return"
            )
        )

    def _align_data(
        self,
        portfolio_returns: pl.DataFrame,
        ff_factors: pl.DataFrame,
    ) -> tuple[pl.DataFrame, pl.DataFrame]:
        """Align portfolio and factor data on dates."""
        aligned = portfolio_returns.join(ff_factors, on="date", how="inner")

        if aligned.height == 0:
            p_min, p_max = (
                str(portfolio_returns["date"].min()),
                str(portfolio_returns["date"].max()),
            )
            f_min, f_max = str(ff_factors["date"].min()), str(ff_factors["date"].max())
            raise DataMismatchError(
                f"No overlapping dates between portfolio ({p_min} to {p_max}) "
                f"and factors ({f_min} to {f_max})"
            )

        if aligned.n_unique("date") != aligned.height:
            raise ValueError("Duplicate dates found after alignment")

        aligned = aligned.sort("date")

        aligned_portfolio = aligned.select(["date", "return"])
        factor_cols = [c for c in aligned.columns if c not in ["date", "return"]]
        aligned_factors = aligned.select(["date"] + factor_cols)

        return aligned_portfolio, aligned_factors

    def _check_multicollinearity(self, factors_df: pl.DataFrame) -> list[str]:
        """Check VIF for multicollinearity."""
        factor_cols = factors_df.columns
        X = factors_df.to_numpy()
        warnings_list: list[str] = []

        # Check condition number
        try:
            cond_number = np.linalg.cond(X)
            if cond_number > 1e10:
                warnings_list.append(
                    f"Factor matrix near-singular (condition number: {cond_number:.2e}). "
                    "VIF computation skipped."
                )
                return warnings_list
        except np.linalg.LinAlgError:
            warnings_list.append("Could not compute condition number")
            return warnings_list

        # Compute VIF for each factor
        for i, factor_name in enumerate(factor_cols):
            try:
                X_subset = np.delete(X, i, axis=1)
                y = X[:, i]

                if X_subset.shape[1] == 0:
                    continue

                X_with_const = np.column_stack([np.ones(len(y)), X_subset])

                beta, residuals, _, _ = np.linalg.lstsq(X_with_const, y, rcond=None)
                y_pred = X_with_const @ beta
                ss_res = np.sum((y - y_pred) ** 2)
                ss_tot = np.sum((y - np.mean(y)) ** 2)

                if ss_tot < 1e-10:
                    warnings_list.append(
                        f"Factor '{factor_name}': Constant column detected (VIF=inf)"
                    )
                    continue

                r_squared = 1 - (ss_res / ss_tot)
                r_squared = min(r_squared, 0.9999999)
                vif = 1 / (1 - r_squared)

                if vif > self.config.vif_threshold:
                    warnings_list.append(
                        f"Factor '{factor_name}': VIF={vif:.2f} exceeds threshold "
                        f"({self.config.vif_threshold})"
                    )
            except np.linalg.LinAlgError as e:
                logger.debug(
                    "VIF computation failed due to linear algebra error",
                    extra={
                        "factor": factor_name,
                        "error": str(e),
                        "error_type": type(e).__name__,
                    },
                )
                warnings_list.append(
                    f"Factor '{factor_name}': VIF computation failed (LinAlgError)"
                )
            except (ValueError, ZeroDivisionError) as e:
                logger.debug(
                    "VIF computation failed due to numerical error",
                    extra={
                        "factor": factor_name,
                        "error": str(e),
                        "error_type": type(e).__name__,
                    },
                )
                warnings_list.append(
                    f"Factor '{factor_name}': VIF computation failed ({type(e).__name__})"
                )

        return warnings_list

    def _compute_nw_lags(self, n_obs: int) -> int:
        """Compute Newey-West lags using rule of thumb."""
        if self.config.newey_west_lags > 0:
            return self.config.newey_west_lags

        # Rule of thumb: floor(4 * (T/100)^(2/9))
        lags = int(np.floor(4 * (n_obs / 100) ** (2 / 9)))
        return max(1, min(lags, 10))  # Clamp to [1, 10]

    def _get_rebalance_dates(
        self,
        start_date: date,
        end_date: date,
        ff_factors: pl.DataFrame,
    ) -> list[date]:
        """Get rebalance dates based on frequency."""
        trading_days = ff_factors.filter(
            (pl.col("date") >= start_date) & (pl.col("date") <= end_date)
        )["date"].to_list()

        if not trading_days:
            return []

        if self.config.rebalance_freq == "daily":
            return trading_days

        # Group by period
        dates_df = pl.DataFrame({"date": trading_days})
        if self.config.rebalance_freq == "weekly":
            grouped = dates_df.with_columns(
                pl.col("date").dt.week().alias("period"),
                pl.col("date").dt.year().alias("year"),
            )
        else:  # monthly
            grouped = dates_df.with_columns(
                pl.col("date").dt.month().alias("period"),
                pl.col("date").dt.year().alias("year"),
            )

        return (
            grouped.group_by(["year", "period"]).agg(pl.col("date").max())["date"].sort().to_list()
        )

    def _get_window_start(self, window_end: date, ff_factors: pl.DataFrame) -> date:
        """Get window start date (trading days before window_end)."""
        trading_days: list[date] = (
            ff_factors.filter(pl.col("date") <= window_end)["date"].sort(descending=True).to_list()
        )

        if len(trading_days) < self.config.window_trading_days:
            return trading_days[-1] if trading_days else window_end

        return trading_days[self.config.window_trading_days - 1]

    def _compute_content_hash(self, data: pl.DataFrame | None) -> str:
        """Compute content hash for versioning."""
        if data is None:
            return "none"
        sorted_df = data.sort(data.columns)
        content = sorted_df.write_csv()
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    def _compute_config_hash(self) -> str:
        """Compute hash of configuration including all fields affecting attribution."""
        config_str = (
            f"{self.config.model}|{self.config.std_errors}|"
            f"{self.config.min_market_cap_usd}|{self.config.market_cap_percentile}|"
            f"{self.config.currency}|{self.config.window_trading_days}|"
            f"{self.config.rebalance_freq}|{self.config.min_observations}|"
            f"{self.config.vif_threshold}|{self.config.aggregation_method}|"
            f"{self.config.newey_west_lags}|{self.config.rebalance_on_filter}|"
            f"{self.config.annualization_factor}"
        )
        return hashlib.sha256(config_str.encode()).hexdigest()[:8]

    def _build_dataset_version_id(
        self,
        ff_version: str,
        crsp_version: str | None,
        portfolio_version: str,
        currency_version: str | None,
        config_hash: str,
    ) -> str:
        """Build composite version ID for reproducibility."""
        crsp_part = f"crsp:{crsp_version}" if crsp_version else "crsp:none"
        currency_part = f"currency:{currency_version}" if currency_version else "currency:none"
        components = (
            f"ff:{ff_version}|{crsp_part}|portfolio:{portfolio_version}|"
            f"{currency_part}|config:{config_hash}"
        )
        return hashlib.sha256(components.encode()).hexdigest()[:16]

    def _get_ff_version(self) -> str | None:
        """Get Fama-French data version."""
        # Provider may have version info
        return getattr(self.ff_provider, "data_version", None)

    def _get_crsp_version(self) -> str | None:
        """Get CRSP data version."""
        if self.crsp_provider is None:
            return None
        return getattr(self.crsp_provider, "data_version", None)
