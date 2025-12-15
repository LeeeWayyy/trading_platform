"""
Factor Builder for multi-factor model construction.

This module provides the FactorBuilder class that computes factor exposures
from the local data warehouse using CRSP and Compustat data.

All computations are point-in-time (PIT) correct with full reproducibility.
"""

import hashlib
import logging
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Literal

import polars as pl

from libs.data_providers.compustat_local_provider import CompustatLocalProvider
from libs.data_providers.crsp_local_provider import CRSPLocalProvider
from libs.data_quality.manifest import ManifestManager, SyncManifest
from libs.data_quality.versioning import DatasetVersionManager, SnapshotManifest
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
        version_manager: DatasetVersionManager | None = None,
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
        self.version_manager = version_manager
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
                f"Unknown factor: {factor_name}. " f"Available: {list(self._registry.keys())}"
            )

        factor_def = self._registry[factor_name]

        # Resolve PIT snapshot context if requested
        snapshot_ctx: _SnapshotContext | None = None
        if snapshot_date is not None:
            if self.version_manager is None:
                raise ValueError("snapshot_date requires DatasetVersionManager for PIT time-travel")
            snapshot_ctx = self._build_snapshot_context(snapshot_date, factor_def)

        crsp_manifest: SyncManifest | None
        compustat_manifest: SyncManifest | None

        if snapshot_ctx:
            crsp_manifest = snapshot_ctx.crsp_manifest
            compustat_manifest = snapshot_ctx.compustat_manifest
        else:
            crsp_manifest = self.manifest.load_manifest("crsp_daily")
            compustat_manifest = self.manifest.load_manifest("compustat_annual")

        # Get version strings (handle None manifests)
        crsp_version = crsp_manifest.manifest_version if crsp_manifest else "unknown"
        compustat_version = compustat_manifest.manifest_version if compustat_manifest else "unknown"
        snapshot_id = snapshot_ctx.snapshot_id if snapshot_ctx else None

        # Compute reproducibility hash (includes config and universe for full reproducibility)
        universe_hash = hashlib.sha256(
            str(sorted(universe) if universe else []).encode()
        ).hexdigest()[:16]
        config_hash = hashlib.sha256(
            f"{self.config.winsorize_pct}:{self.config.neutralize_sector}:"
            f"{self.config.min_stocks_per_sector}:{self.config.lookback_days}:"
            f"{self.config.report_date_column or ''}".encode()
        ).hexdigest()[:16]
        snapshot_component = f":{snapshot_id}" if snapshot_id else ""
        snapshot_date_component = f":{snapshot_date}" if snapshot_date else ""
        input_hash = hashlib.sha256(
            f"{factor_name}:{as_of_date}:{crsp_version}:"
            f"{compustat_version}:{config_hash}:{universe_hash}"
            f"{snapshot_component}{snapshot_date_component}".encode()
        ).hexdigest()

        manifest_cm = (
            self._use_snapshot_manifests(snapshot_ctx.manifest_adapter)
            if snapshot_ctx
            else nullcontext()
        )

        with manifest_cm:
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
                **({"snapshot": snapshot_id} if snapshot_id else {}),
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
            result = self.compute_factor(factor_name, as_of_date, universe, snapshot_date)
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
            results.append(self.compute_factor(name, as_of_date, universe, snapshot_date))

        # Determine weights
        if weights == "equal":
            w = [1.0 / len(factor_names)] * len(factor_names)
        elif weights == "ic_weighted":
            raise NotImplementedError(
                "IC-weighted composite not implemented; supply explicit weights or use 'equal'."
            )
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
            factor_df = result.exposures.select(["permno", pl.col("zscore").alias(factor_names[i])])
            combined_df = combined_df.join(factor_df, on="permno", how="inner")

        # Compute weighted sum (build expression explicitly to ensure proper typing)
        composite_expr: pl.Expr = pl.col(factor_names[0]) * w[0]
        for name, weight in zip(factor_names[1:], w[1:], strict=False):
            composite_expr = composite_expr + pl.col(name) * weight

        composite_df = combined_df.with_columns(composite_expr.alias("raw_value")).select(
            ["permno", "raw_value"]
        )

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
            result = result.with_columns((pl.col("zscore").rank() / n).alias("percentile"))
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

        return df.with_columns(((pl.col(col) - mean_val) / std_val).alias("zscore"))

    def _get_pit_sector_mappings(
        self,
        permnos: list[int],
        as_of_date: date,
        report_date_column: str | None = None,
    ) -> pl.DataFrame:
        """
        Get point-in-time sector mappings for securities.

        PIT Sector Retrieval:
        - Uses Compustat GICS codes from the most recent AVAILABLE filing
        - Uses the provided report date column when available to gate PIT exposure
        - Falls back to a conservative filing lag when report dates are unavailable

        Args:
            permnos: List of CRSP PERMNOs
            as_of_date: Point-in-time date
            report_date_column: Optional actual report/public date column (e.g., rdq).
                When provided and present, PIT filtering uses this column; otherwise a
                conservative 90-day filing lag is applied.

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
        # Prefer actual report/public dates when available; otherwise use filing lag.
        if report_date_column and report_date_column in fundamentals.columns:
            fundamentals = fundamentals.filter(
                pl.col(report_date_column).is_not_null()
                & (pl.col(report_date_column) <= pl.lit(as_of_date))
            )
        else:
            if report_date_column:
                logger.warning(
                    "report_date_column '%s' not found; applying %s-day filing lag fallback",
                    report_date_column,
                    FILING_LAG_DAYS,
                )

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

        sectors = fundamentals.group_by("permno").agg(
            pl.col(sector_col).last().alias("gics_sector")
        )

        return sectors

    def _neutralize_sector(self, df: pl.DataFrame, col: str, as_of_date: date) -> pl.DataFrame:
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
        sectors = self._get_pit_sector_mappings(
            permnos,
            as_of_date,
            report_date_column=self.config.report_date_column,
        )

        # Join sector mappings
        df_with_sector = df.join(sectors, on="permno", how="left")

        # Check minimum stocks per sector
        sector_counts = df_with_sector.group_by("gics_sector").agg(pl.len().alias("n"))

        # Neutralize within sectors that have enough stocks
        # Exclude None/null sectors to avoid masking data quality issues
        valid_sectors = sector_counts.filter(
            (pl.col("n") >= self.config.min_stocks_per_sector) & pl.col("gics_sector").is_not_null()
        ).select("gics_sector")

        # For securities in valid sectors, demean within sector
        result = df_with_sector.with_columns(
            pl.when(pl.col("gics_sector").is_in(valid_sectors["gics_sector"].to_list()))
            .then(pl.col(col) - pl.col(col).mean().over("gics_sector"))
            .otherwise(pl.col(col))
            .alias(col)
        ).drop("gics_sector")

        return result

    # =========================================================================
    # Snapshot helpers
    # =========================================================================

    def _build_snapshot_context(
        self, snapshot_date: date, factor_def: FactorDefinition
    ) -> "_SnapshotContext":
        """Build PIT snapshot context using DatasetVersionManager.

        Returns manifest adapter that rewires providers to snapshot file paths
        without mutating the underlying provider instances.
        """

        assert self.version_manager is not None  # enforced by caller

        crsp_path, crsp_snapshot = self.version_manager.query_as_of("crsp_daily", snapshot_date)
        if "crsp_daily" not in crsp_snapshot.datasets:
            raise ValueError("crsp_daily not present in snapshot for PIT query")

        comp_snapshot: SnapshotManifest | None = None
        comp_path: Path | None = None
        if factor_def.requires_fundamentals:
            comp_path, comp_snapshot = self.version_manager.query_as_of(
                "compustat_annual", snapshot_date
            )
            if "compustat_annual" not in comp_snapshot.datasets:
                raise ValueError("compustat_annual not present in snapshot")

        adapter = SnapshotManifestAdapter(
            manifests={
                "crsp_daily": _build_sync_manifest_from_snapshot(
                    dataset="crsp_daily",
                    snapshot=crsp_snapshot,
                    data_path=crsp_path,
                ),
                **(
                    {
                        "compustat_annual": _build_sync_manifest_from_snapshot(
                            dataset="compustat_annual",
                            snapshot=comp_snapshot,  # Narrowed by conditional
                            data_path=comp_path,
                        )
                    }
                    if comp_snapshot is not None and comp_path is not None
                    else {}
                ),
            }
        )

        return _SnapshotContext(
            manifest_adapter=adapter,
            snapshot_id=crsp_snapshot.aggregate_checksum,
            crsp_manifest=adapter.manifests["crsp_daily"],
            compustat_manifest=adapter.manifests.get("compustat_annual"),
        )

    @contextmanager
    def _use_snapshot_manifests(self, adapter: "SnapshotManifestAdapter") -> Iterator[None]:
        """Temporarily swap provider manifest managers to snapshot adapter."""

        original_crsp = getattr(self.crsp, "manifest_manager", None)
        original_comp = getattr(self.compustat, "manifest_manager", None)

        self.crsp.manifest_manager = adapter  # type: ignore[assignment]
        self.compustat.manifest_manager = adapter  # type: ignore[assignment]

        try:
            yield
        finally:
            if original_crsp is not None:
                self.crsp.manifest_manager = original_crsp
            if original_comp is not None:
                self.compustat.manifest_manager = original_comp


class SnapshotManifestAdapter:
    """Minimal manifest adapter that serves snapshot-backed SyncManifests."""

    def __init__(self, manifests: dict[str, SyncManifest]):
        self.manifests = manifests

    def load_manifest(self, dataset_name: str) -> SyncManifest | None:  # pragma: no cover - trivial
        return self.manifests.get(dataset_name)


@dataclass
class _SnapshotContext:
    """Resolved snapshot state for PIT factor computation."""

    manifest_adapter: SnapshotManifestAdapter
    snapshot_id: str
    crsp_manifest: SyncManifest
    compustat_manifest: SyncManifest | None


def _build_sync_manifest_from_snapshot(
    dataset: str, snapshot: SnapshotManifest, data_path: Path
) -> SyncManifest:
    """Convert DatasetSnapshot to SyncManifest for provider consumption."""

    if dataset not in snapshot.datasets:
        raise ValueError(f"Dataset {dataset} missing from snapshot {snapshot.version_tag}")

    ds_snapshot = snapshot.datasets[dataset]
    file_paths = [str((data_path / f.path).resolve()) for f in ds_snapshot.files]

    checksum = snapshot.aggregate_checksum

    return SyncManifest(
        dataset=dataset,
        sync_timestamp=snapshot.created_at,
        start_date=ds_snapshot.date_range_start,
        end_date=ds_snapshot.date_range_end,
        row_count=ds_snapshot.row_count,
        checksum=checksum,
        checksum_algorithm="sha256",
        schema_version="snapshot",
        wrds_query_hash="snapshot",
        file_paths=file_paths,
        validation_status="passed",
        manifest_version=ds_snapshot.sync_manifest_version,
        previous_checksum=None,
    )
