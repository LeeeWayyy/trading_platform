"""
Factor Builder for multi-factor model construction.

This module provides the FactorBuilder class that computes factor exposures
from the local data warehouse using CRSP and Compustat data.

All computations are point-in-time (PIT) correct with full reproducibility.
"""

import hashlib
import logging
from datetime import UTC, date, datetime, timedelta
from typing import Literal

import polars as pl

from libs.data_providers.compustat_local_provider import CompustatLocalProvider
from libs.data_providers.crsp_local_provider import CRSPLocalProvider
from libs.data_quality.manifest import ManifestManager
from libs.factors.factor_definitions import (
    CANONICAL_FACTORS,
    FactorConfig,
    FactorDefinition,
    FactorResult,
)

logger = logging.getLogger(__name__)


class FactorBuilder:
    """
    Build factor exposures from local data warehouse.

    Uses CRSP for prices/returns and Compustat for fundamentals.
    All computations are point-in-time correct with reproducibility tracking.

    Example:
        >>> builder = FactorBuilder(crsp_provider, compustat_provider, manifest_mgr)
        >>> result = builder.compute_factor("momentum_12_1", date(2023, 6, 30))
        >>> print(result.exposures.head())
    """

    def __init__(
        self,
        crsp_provider: CRSPLocalProvider,
        compustat_provider: CompustatLocalProvider,
        manifest_manager: ManifestManager,
        config: FactorConfig | None = None,
    ):
        """
        Initialize FactorBuilder.

        Args:
            crsp_provider: Provider for CRSP daily price data
            compustat_provider: Provider for Compustat fundamental data
            manifest_manager: Manager for dataset versioning/manifests
            config: Optional configuration (uses defaults if None)
        """
        self.crsp = crsp_provider
        self.compustat = compustat_provider
        self.manifest = manifest_manager
        self.config = config or FactorConfig()
        self._registry: dict[str, FactorDefinition] = {}

        # Register canonical factors on init
        self._register_canonical_factors()

    def _register_canonical_factors(self) -> None:
        """Register the 5 canonical factors."""
        for name, factor_cls in CANONICAL_FACTORS.items():
            self._registry[name] = factor_cls()

    def register_factor(self, factor: FactorDefinition) -> None:
        """
        Register a custom factor definition.

        Args:
            factor: Factor implementing FactorDefinition protocol
        """
        self._registry[factor.name] = factor
        logger.info(f"Registered factor: {factor.name} ({factor.category})")

    def list_factors(self) -> list[str]:
        """Return list of registered factor names."""
        return list(self._registry.keys())

    def compute_factor(
        self,
        factor_name: str,
        as_of_date: date,
        universe: list[int] | None = None,
        snapshot_date: date | None = None,
    ) -> FactorResult:
        """
        Compute single factor for given date.

        Args:
            factor_name: Registered factor name
            as_of_date: Point-in-time date for computation
            universe: Optional list of PERMNOs (None = all stocks)
            snapshot_date: Override data snapshot date for time-travel queries.
                          If None, uses current manifest versions.
                          Used for PIT regression testing and reproducibility.

        Returns:
            FactorResult with exposures and metadata
        """
        if factor_name not in self._registry:
            raise ValueError(
                f"Unknown factor: {factor_name}. "
                f"Available: {list(self._registry.keys())}"
            )

        factor_def = self._registry[factor_name]

        # Get manifest versions
        # snapshot_date time-travel requires ManifestManager.load_manifest_at_date
        if snapshot_date is not None:
            raise NotImplementedError(
                "snapshot_date time-travel not yet supported. "
                "ManifestManager.load_manifest_at_date is required for this feature."
            )
        crsp_manifest = self.manifest.load_manifest("crsp_daily")
        compustat_manifest = self.manifest.load_manifest("compustat_annual")

        # Get version strings (handle None manifests)
        crsp_version = crsp_manifest.manifest_version if crsp_manifest else "unknown"
        compustat_version = (
            compustat_manifest.manifest_version if compustat_manifest else "unknown"
        )

        # Compute reproducibility hash (includes config and universe for full reproducibility)
        universe_hash = hashlib.sha256(
            str(sorted(universe) if universe else []).encode()
        ).hexdigest()[:16]
        config_hash = hashlib.sha256(
            f"{self.config.winsorize_pct}:{self.config.neutralize_sector}:"
            f"{self.config.min_stocks_per_sector}:{self.config.lookback_days}".encode()
        ).hexdigest()[:16]
        input_hash = hashlib.sha256(
            f"{factor_name}:{as_of_date}:{crsp_version}:"
            f"{compustat_version}:{config_hash}:{universe_hash}".encode()
        ).hexdigest()

        # Get price data with PIT correctness
        prices = self.crsp.get_daily_prices(
            start_date=as_of_date - timedelta(days=self.config.lookback_days),
            end_date=as_of_date,
            as_of_date=as_of_date,
        ).sort(["permno", "date"])

        # Filter universe if specified
        if universe is not None:
            prices = prices.filter(pl.col("permno").is_in(universe))

        # Get fundamentals if needed
        fundamentals = None
        if factor_def.requires_fundamentals:
            fundamentals = self.compustat.get_annual_fundamentals(
                start_date=as_of_date - timedelta(days=365 * 3),
                end_date=as_of_date,
                as_of_date=as_of_date,
            )
            # Sort fundamentals for deterministic aggregation
            if "datadate" in fundamentals.columns:
                fundamentals = fundamentals.sort(["permno", "datadate"])
            elif "date" in fundamentals.columns:
                fundamentals = fundamentals.sort(["permno", "date"])

            if universe is not None:
                fundamentals = fundamentals.filter(pl.col("permno").is_in(universe))

        # Compute raw factor values
        raw_values = factor_def.compute(prices, fundamentals, as_of_date)

        # Apply transformations
        transformed = self._transform_factor(raw_values, as_of_date)

        # Add metadata columns
        exposures = transformed.with_columns(
            [
                pl.lit(as_of_date).alias("date"),
                pl.lit(factor_name).alias("factor_name"),
            ]
        )

        # Build result with version tracking
        result = FactorResult(
            exposures=exposures,
            as_of_date=as_of_date,
            dataset_version_ids={
                "crsp": f"v{crsp_version}",
                "compustat": f"v{compustat_version}",
            },
            computation_timestamp=datetime.now(UTC),
            reproducibility_hash=input_hash,
        )

        # Validate result for data quality issues
        validation_errors = result.validate()
        if validation_errors:
            logger.warning(
                "Factor %s has validation issues: %s",
                factor_name,
                validation_errors,
            )

        return result

    def compute_all_factors(
        self,
        as_of_date: date,
        universe: list[int] | None = None,
        snapshot_date: date | None = None,
    ) -> FactorResult:
        """
        Compute all registered factors for given date.

        Args:
            as_of_date: Point-in-time date for computation
            universe: Optional list of PERMNOs
            snapshot_date: Override for time-travel queries

        Returns:
            FactorResult with all factor exposures combined
        """
        all_exposures: list[pl.DataFrame] = []
        version_ids: dict[str, str] = {}

        for factor_name in self._registry:
            result = self.compute_factor(
                factor_name, as_of_date, universe, snapshot_date
            )
            all_exposures.append(result.exposures)
            version_ids.update(result.dataset_version_ids)

        combined = pl.concat(all_exposures)

        # Compute comprehensive reproducibility hash (include config and universe)
        universe_hash = hashlib.sha256(
            str(sorted(universe) if universe else []).encode()
        ).hexdigest()[:16]
        config_hash = hashlib.sha256(
            f"{self.config.winsorize_pct}:{self.config.neutralize_sector}:"
            f"{self.config.min_stocks_per_sector}:{self.config.lookback_days}".encode()
        ).hexdigest()[:16]
        combined_hash = hashlib.sha256(
            f"all_factors:{as_of_date}:{sorted(version_ids.items())}:"
            f"{config_hash}:{universe_hash}".encode()
        ).hexdigest()

        return FactorResult(
            exposures=combined,
            as_of_date=as_of_date,
            dataset_version_ids=version_ids,
            computation_timestamp=datetime.now(UTC),
            reproducibility_hash=combined_hash,
        )

    def compute_composite(
        self,
        factor_names: list[str],
        weights: list[float] | Literal["equal", "ic_weighted"],
        as_of_date: date,
        universe: list[int] | None = None,
        snapshot_date: date | None = None,
    ) -> FactorResult:
        """
        Compute composite factor from multiple factors.

        Args:
            factor_names: List of factor names to combine
            weights: Explicit weights, "equal", or "ic_weighted"
            as_of_date: Point-in-time date
            universe: Optional list of PERMNOs
            snapshot_date: Override for time-travel

        Returns:
            FactorResult with composite exposures
        """
        # Compute individual factors
        results: list[FactorResult] = []
        for name in factor_names:
            results.append(
                self.compute_factor(name, as_of_date, universe, snapshot_date)
            )

        # Determine weights
        if weights == "equal":
            w = [1.0 / len(factor_names)] * len(factor_names)
        elif weights == "ic_weighted":
            # TODO: Implement IC-weighted combination
            # For now, fall back to equal weights
            logger.warning("IC-weighted not yet implemented, using equal weights")
            w = [1.0 / len(factor_names)] * len(factor_names)
        else:
            if len(weights) != len(factor_names):
                raise ValueError(
                    f"Weights length ({len(weights)}) must match "
                    f"factors length ({len(factor_names)})"
                )
            w = weights

        # Combine z-scores with weights
        # Use inner join to only keep securities with ALL factor exposures
        # This prevents null values from propagating through the composite
        combined_df: pl.DataFrame = results[0].exposures.select(
            ["permno", pl.col("zscore").alias(factor_names[0])]
        )
        for i, result in enumerate(results[1:], start=1):
            factor_df = result.exposures.select(
                ["permno", pl.col("zscore").alias(factor_names[i])]
            )
            combined_df = combined_df.join(factor_df, on="permno", how="inner")

        # Compute weighted sum (build expression explicitly to ensure proper typing)
        composite_expr: pl.Expr = pl.col(factor_names[0]) * w[0]
        for name, weight in zip(factor_names[1:], w[1:], strict=False):
            composite_expr = composite_expr + pl.col(name) * weight

        composite_df = combined_df.with_columns(
            composite_expr.alias("raw_value")
        ).select(["permno", "raw_value"])

        # Re-standardize
        transformed = self._transform_factor(composite_df, as_of_date, col="raw_value")
        transformed = transformed.with_columns(
            [
                pl.lit(as_of_date).alias("date"),
                pl.lit("composite").alias("factor_name"),
            ]
        )

        # Combine version IDs
        all_versions: dict[str, str] = {}
        for result in results:
            all_versions.update(result.dataset_version_ids)

        # Compute comprehensive reproducibility hash (include config and universe)
        universe_hash = hashlib.sha256(
            str(sorted(universe) if universe else []).encode()
        ).hexdigest()[:16]
        config_hash = hashlib.sha256(
            f"{self.config.winsorize_pct}:{self.config.neutralize_sector}:"
            f"{self.config.min_stocks_per_sector}:{self.config.lookback_days}".encode()
        ).hexdigest()[:16]
        composite_hash = hashlib.sha256(
            f"composite:{factor_names}:{w}:{as_of_date}:"
            f"{sorted(all_versions.items())}:{config_hash}:{universe_hash}".encode()
        ).hexdigest()

        result = FactorResult(
            exposures=transformed,
            as_of_date=as_of_date,
            dataset_version_ids=all_versions,
            computation_timestamp=datetime.now(UTC),
            reproducibility_hash=composite_hash,
        )

        # Validate composite result for data quality issues
        validation_errors = result.validate()
        if validation_errors:
            logger.warning(
                "Composite factor has validation issues: %s",
                validation_errors,
            )

        return result

    def _transform_factor(
        self, df: pl.DataFrame, as_of_date: date, col: str = "factor_value"
    ) -> pl.DataFrame:
        """
        Apply winsorization, z-score, and optional sector neutralization.

        Args:
            df: DataFrame with permno and factor_value columns
            as_of_date: Date for PIT sector lookup
            col: Column name containing raw factor values

        Returns:
            DataFrame with raw_value, zscore, percentile columns
        """
        # Rename input column to raw_value
        result = df.rename({col: "raw_value"})

        # Winsorize
        result = self._winsorize(result, "raw_value")

        # Compute z-score
        result = self._compute_zscore(result, "raw_value")

        # Optional sector neutralization
        if self.config.neutralize_sector:
            result = self._neutralize_sector(result, "zscore", as_of_date)

        # Compute percentile from z-scores
        n = result.height
        if n > 0:
            result = result.with_columns(
                (pl.col("zscore").rank() / n).alias("percentile")
            )
        else:
            result = result.with_columns(pl.lit(None).alias("percentile"))

        return result

    def _winsorize(self, df: pl.DataFrame, col: str) -> pl.DataFrame:
        """
        Winsorize column at configured percentiles.

        Args:
            df: Input DataFrame
            col: Column to winsorize

        Returns:
            DataFrame with winsorized values
        """
        # Handle empty dataframe
        if df.height == 0:
            return df

        # Handle all-null column
        non_null_count = df.select(pl.col(col).drop_nulls().len()).item()
        if non_null_count == 0:
            return df

        lower_pct = self.config.winsorize_pct
        upper_pct = 1.0 - self.config.winsorize_pct

        lower_val = df.select(pl.col(col).quantile(lower_pct)).item()
        upper_val = df.select(pl.col(col).quantile(upper_pct)).item()

        # Handle case where quantiles return None
        if lower_val is None or upper_val is None:
            return df

        return df.with_columns(pl.col(col).clip(lower_val, upper_val).alias(col))

    def _compute_zscore(self, df: pl.DataFrame, col: str) -> pl.DataFrame:
        """
        Compute cross-sectional z-score.

        Args:
            df: Input DataFrame
            col: Column to standardize

        Returns:
            DataFrame with zscore column added
        """
        mean_val = df.select(pl.col(col).mean()).item()
        std_val = df.select(pl.col(col).std()).item()

        if std_val == 0 or std_val is None:
            # All same value - set z-scores to 0
            return df.with_columns(pl.lit(0.0).alias("zscore"))

        return df.with_columns(
            ((pl.col(col) - mean_val) / std_val).alias("zscore")
        )

    def _get_pit_sector_mappings(
        self, permnos: list[int], as_of_date: date
    ) -> pl.DataFrame:
        """
        Get point-in-time sector mappings for securities.

        PIT Sector Retrieval:
        - Uses Compustat GICS codes from the most recent AVAILABLE filing
        - Respects filing lag: sector = sector from filing where datadate + lag <= as_of_date
        - This prevents leaking future sector reclassifications into historical analysis

        Args:
            permnos: List of CRSP PERMNOs
            as_of_date: Point-in-time date

        Returns:
            DataFrame with columns: permno, gics_sector (2-digit code)
        """
        # Filing lag: fundamentals are not public until ~90 days after fiscal period end
        FILING_LAG_DAYS = 90
        filing_cutoff = as_of_date - timedelta(days=FILING_LAG_DAYS)

        # Get fundamentals with PIT correctness
        fundamentals = self.compustat.get_annual_fundamentals(
            start_date=as_of_date - timedelta(days=365 * 3),
            end_date=as_of_date,
            as_of_date=as_of_date,
        )

        # Filter to requested permnos
        if permnos:
            fundamentals = fundamentals.filter(pl.col("permno").is_in(permnos))

        # CRITICAL: Apply filing lag to prevent look-ahead bias in sector assignments
        # Only use fundamentals where datadate <= filing_cutoff (90 days before as_of_date)
        if "datadate" in fundamentals.columns:
            fundamentals = fundamentals.filter(pl.col("datadate") <= filing_cutoff)
        elif "date" in fundamentals.columns:
            fundamentals = fundamentals.filter(pl.col("date") <= filing_cutoff)

        # Handle empty fundamentals
        if fundamentals.height == 0:
            logger.warning("No fundamentals data available for sector mapping")
            return pl.DataFrame({"permno": permnos, "gics_sector": [None] * len(permnos)})

        # Get most recent GICS sector per security
        # GICS sector is first 2 digits of gsector or gics code
        if "gsector" in fundamentals.columns:
            sector_col = "gsector"
        elif "gics" in fundamentals.columns:
            # Extract first 2 digits
            fundamentals = fundamentals.with_columns(
                pl.col("gics").cast(str).str.slice(0, 2).alias("gsector")
            )
            sector_col = "gsector"
        else:
            # No sector data available
            logger.warning("No GICS sector data available in Compustat")
            return pl.DataFrame({"permno": permnos, "gics_sector": [None] * len(permnos)})

        # CRITICAL: Sort by datadate before taking last to ensure PIT correctness
        # This prevents picking a future sector reclassification if data is unsorted
        if "datadate" in fundamentals.columns:
            fundamentals = fundamentals.sort(["permno", "datadate"])
        elif "date" in fundamentals.columns:
            fundamentals = fundamentals.sort(["permno", "date"])

        sectors = (
            fundamentals.group_by("permno")
            .agg(pl.col(sector_col).last().alias("gics_sector"))
        )

        return sectors

    def _neutralize_sector(
        self, df: pl.DataFrame, col: str, as_of_date: date
    ) -> pl.DataFrame:
        """
        Sector-neutralize factor using PIT-correct GICS codes.

        Uses _get_pit_sector_mappings() to ensure sector assignments
        are point-in-time correct (no future reclassifications leaked).

        Args:
            df: DataFrame with factor values
            col: Column to neutralize
            as_of_date: Point-in-time date for sector lookup

        Returns:
            DataFrame with sector-neutralized values
        """
        permnos = df.select("permno").to_series().to_list()
        sectors = self._get_pit_sector_mappings(permnos, as_of_date)

        # Join sector mappings
        df_with_sector = df.join(sectors, on="permno", how="left")

        # Check minimum stocks per sector
        sector_counts = df_with_sector.group_by("gics_sector").agg(
            pl.len().alias("n")
        )

        # Neutralize within sectors that have enough stocks
        # Exclude None/null sectors to avoid masking data quality issues
        valid_sectors = sector_counts.filter(
            (pl.col("n") >= self.config.min_stocks_per_sector)
            & pl.col("gics_sector").is_not_null()
        ).select("gics_sector")

        # For securities in valid sectors, demean within sector
        result = df_with_sector.with_columns(
            pl.when(pl.col("gics_sector").is_in(valid_sectors["gics_sector"].to_list()))
            .then(
                pl.col(col) - pl.col(col).mean().over("gics_sector")
            )
            .otherwise(pl.col(col))
            .alias(col)
        ).drop("gics_sector")

        return result
