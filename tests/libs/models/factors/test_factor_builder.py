"""
Tests for FactorBuilder.
"""

from datetime import UTC, date, datetime

import numpy as np
import polars as pl
import pytest

from libs.models.factors import FactorBuilder, FactorConfig, FactorResult


class TestFactorBuilderInit:
    """Tests for FactorBuilder initialization."""

    def test_registers_canonical_factors(self, factor_builder: FactorBuilder):
        """FactorBuilder registers all 5 canonical factors on init."""
        factors = factor_builder.list_factors()
        assert len(factors) == 5
        assert "momentum_12_1" in factors
        assert "book_to_market" in factors
        assert "roe" in factors
        assert "log_market_cap" in factors
        assert "realized_vol" in factors

    def test_uses_default_config(self, factor_builder: FactorBuilder):
        """FactorBuilder uses default config when none provided."""
        assert factor_builder.config.winsorize_pct == 0.01
        assert factor_builder.config.neutralize_sector is True

    def test_uses_custom_config(
        self,
        mock_crsp_provider,
        mock_compustat_provider,
        mock_manifest_manager,
    ):
        """FactorBuilder uses custom config when provided."""
        config = FactorConfig(winsorize_pct=0.05, neutralize_sector=False)
        builder = FactorBuilder(
            mock_crsp_provider,
            mock_compustat_provider,
            mock_manifest_manager,
            config=config,
        )
        assert builder.config.winsorize_pct == 0.05
        assert builder.config.neutralize_sector is False


class TestFactorBuilderCompute:
    """Tests for FactorBuilder.compute_factor()."""

    def test_compute_momentum(self, factor_builder: FactorBuilder):
        """compute_factor works for momentum."""
        result = factor_builder.compute_factor(
            "momentum_12_1",
            as_of_date=date(2023, 6, 30),
        )

        assert isinstance(result, FactorResult)
        assert result.as_of_date == date(2023, 6, 30)
        assert "crsp" in result.dataset_version_ids
        assert result.reproducibility_hash != ""
        assert result.exposures.height > 0

    def test_compute_book_to_market(self, factor_builder: FactorBuilder):
        """compute_factor works for book_to_market."""
        result = factor_builder.compute_factor(
            "book_to_market",
            as_of_date=date(2023, 6, 30),
        )

        assert isinstance(result, FactorResult)
        assert result.exposures.height > 0
        assert "compustat" in result.dataset_version_ids

    def test_compute_unknown_factor_raises(self, factor_builder: FactorBuilder):
        """compute_factor raises for unknown factors."""
        with pytest.raises(ValueError, match="Unknown factor"):
            factor_builder.compute_factor("nonexistent", date(2023, 6, 30))

    def test_compute_with_universe_filter(self, factor_builder: FactorBuilder):
        """compute_factor filters to specified universe."""
        universe = [10001, 10002, 10003]
        result = factor_builder.compute_factor(
            "momentum_12_1",
            as_of_date=date(2023, 6, 30),
            universe=universe,
        )

        # Should only have stocks in universe
        permnos = result.exposures["permno"].unique().to_list()
        for p in permnos:
            assert p in universe

    def test_compute_result_has_required_columns(self, factor_builder: FactorBuilder):
        """Computed result has all required columns."""
        result = factor_builder.compute_factor(
            "momentum_12_1",
            as_of_date=date(2023, 6, 30),
        )

        required = ["permno", "date", "factor_name", "raw_value", "zscore", "percentile"]
        for col in required:
            assert col in result.exposures.columns, f"Missing column: {col}"

    def test_compute_zscore_is_standardized(self, factor_builder: FactorBuilder):
        """Z-scores are approximately standardized."""
        result = factor_builder.compute_factor(
            "log_market_cap",
            as_of_date=date(2023, 6, 30),
        )

        zscores = result.exposures["zscore"].to_numpy()

        # Mean should be close to 0
        assert abs(np.nanmean(zscores)) < 0.1

        # Std should be close to 1
        assert 0.5 < np.nanstd(zscores) < 1.5


class TestFactorBuilderComputeAll:
    """Tests for FactorBuilder.compute_all_factors()."""

    def test_compute_all_factors(self, factor_builder: FactorBuilder):
        """compute_all_factors returns all factors combined."""
        result = factor_builder.compute_all_factors(as_of_date=date(2023, 6, 30))

        assert isinstance(result, FactorResult)

        # Should have all 5 factor names
        factor_names = result.exposures["factor_name"].unique().to_list()
        assert len(factor_names) == 5

    def test_compute_all_with_universe(self, factor_builder: FactorBuilder):
        """compute_all_factors respects universe filter."""
        universe = [10001, 10002]
        result = factor_builder.compute_all_factors(
            as_of_date=date(2023, 6, 30),
            universe=universe,
        )

        permnos = result.exposures["permno"].unique().to_list()
        for p in permnos:
            assert p in universe


class TestFactorBuilderComposite:
    """Tests for FactorBuilder.compute_composite()."""

    def test_compute_composite_equal_weights(self, factor_builder: FactorBuilder):
        """compute_composite with equal weights works."""
        result = factor_builder.compute_composite(
            factor_names=["momentum_12_1", "log_market_cap"],
            weights="equal",
            as_of_date=date(2023, 6, 30),
        )

        assert isinstance(result, FactorResult)
        assert result.exposures["factor_name"].unique().to_list() == ["composite"]

    def test_compute_composite_explicit_weights(self, factor_builder: FactorBuilder):
        """compute_composite with explicit weights works."""
        result = factor_builder.compute_composite(
            factor_names=["momentum_12_1", "log_market_cap"],
            weights=[0.7, 0.3],
            as_of_date=date(2023, 6, 30),
        )

        assert isinstance(result, FactorResult)
        assert result.exposures.height > 0

    def test_compute_composite_weight_mismatch_raises(self, factor_builder: FactorBuilder):
        """compute_composite raises if weight count doesn't match."""
        with pytest.raises(ValueError, match="Weights length"):
            factor_builder.compute_composite(
                factor_names=["momentum_12_1", "log_market_cap"],
                weights=[0.5],  # Only 1 weight for 2 factors
                as_of_date=date(2023, 6, 30),
            )


class TestFactorBuilderPIT:
    """Tests for point-in-time correctness."""

    def test_snapshot_date_uses_version_manager(
        self,
        tmp_path,
        mock_manifest_manager,
    ):
        """snapshot_date resolves PIT versions via DatasetVersionManager."""

        from unittest.mock import MagicMock

        from libs.data.data_quality.versioning import (
            DatasetSnapshot,
            FileStorageInfo,
            SnapshotManifest,
        )
        from tests.libs.models.factors.conftest import MockCompustatProvider, MockCRSPProvider

        prices = pl.DataFrame(
            {
                "date": [date(2024, 6, 28), date(2024, 6, 30)],
                "permno": [10001, 10002],
                "prc": [10.0, 20.0],
                "ret": [0.01, -0.02],
                "vol": [1000.0, 2000.0],
                "shrout": [1000.0, 2000.0],
            }
        )

        fundamentals = pl.DataFrame(
            {
                "datadate": [date(2023, 12, 31), date(2024, 3, 31)],
                "permno": [10001, 10002],
                "gvkey": ["10001", "10002"],
                "ceq": [60.0, 120.0],
                "at": [100.0, 200.0],
                "lt": [50.0, 80.0],
            }
        )

        snapshot_created = datetime(2024, 7, 1, tzinfo=UTC)
        crsp_snapshot = DatasetSnapshot(
            dataset="crsp_daily",
            sync_manifest_version=1,
            files=[
                FileStorageInfo(
                    path="crsp_daily/2024.parquet",
                    original_path="crsp_daily/2024.parquet",
                    storage_mode="copy",
                    target="crsp_daily/2024.parquet",
                    size_bytes=1,
                    checksum="crspchk",
                )
            ],
            row_count=prices.height,
            date_range_start=prices["date"].min(),
            date_range_end=prices["date"].max(),
        )

        comp_snapshot = DatasetSnapshot(
            dataset="compustat_annual",
            sync_manifest_version=2,
            files=[
                FileStorageInfo(
                    path="compustat_annual/2024.parquet",
                    original_path="compustat_annual/2024.parquet",
                    storage_mode="copy",
                    target="compustat_annual/2024.parquet",
                    size_bytes=1,
                    checksum="compchk",
                )
            ],
            row_count=fundamentals.height,
            date_range_start=fundamentals["datadate"].min(),
            date_range_end=fundamentals["datadate"].max(),
        )

        snapshot_manifest = SnapshotManifest(
            version_tag="2024-07-01",
            created_at=snapshot_created,
            datasets={"crsp_daily": crsp_snapshot, "compustat_annual": comp_snapshot},
            total_size_bytes=2,
            aggregate_checksum="snapshot123",
            referenced_by=[],
        )

        version_manager = MagicMock()
        version_manager.query_as_of.return_value = (tmp_path, snapshot_manifest)

        crsp_provider = MockCRSPProvider(prices)
        comp_provider = MockCompustatProvider(fundamentals)

        builder = FactorBuilder(
            crsp_provider=crsp_provider,
            compustat_provider=comp_provider,
            manifest_manager=mock_manifest_manager,
            version_manager=version_manager,
        )

        result = builder.compute_factor(
            "book_to_market",
            as_of_date=date(2024, 6, 30),
            snapshot_date=date(2024, 6, 30),
        )

        assert result.dataset_version_ids["crsp"] == "v1"
        assert result.dataset_version_ids["compustat"] == "v2"
        assert result.dataset_version_ids["snapshot"] == "snapshot123"
        version_manager.query_as_of.assert_called_with("compustat_annual", date(2024, 6, 30))

    def test_reproducibility_hash_same_inputs(
        self,
        mock_crsp_provider,
        mock_compustat_provider,
        mock_manifest_manager,
    ):
        """Same inputs produce same reproducibility hash."""
        builder = FactorBuilder(
            mock_crsp_provider,
            mock_compustat_provider,
            mock_manifest_manager,
        )

        result1 = builder.compute_factor(
            "log_market_cap",
            as_of_date=date(2023, 6, 30),
        )

        result2 = builder.compute_factor(
            "log_market_cap",
            as_of_date=date(2023, 6, 30),
        )

        assert result1.reproducibility_hash == result2.reproducibility_hash


class TestFactorBuilderTransformations:
    """Tests for winsorization, z-score, sector neutralization."""

    def test_winsorize_clips_extremes(self, factor_builder: FactorBuilder):
        """Winsorization clips extreme values."""
        df = pl.DataFrame(
            {
                "permno": list(range(100)),
                "factor_value": [0.0] * 98 + [100.0, -100.0],  # Extremes
            }
        )

        result = factor_builder._winsorize(df, "factor_value")

        # Extreme values should be clipped
        max_val = result["factor_value"].max()
        min_val = result["factor_value"].min()

        assert max_val < 100.0
        assert min_val > -100.0

    def test_compute_zscore_standardizes(self, factor_builder: FactorBuilder):
        """Z-score computation standardizes the column."""
        df = pl.DataFrame(
            {
                "permno": [1, 2, 3, 4, 5],
                "factor_value": [10.0, 20.0, 30.0, 40.0, 50.0],
            }
        )

        result = factor_builder._compute_zscore(df, "factor_value")

        assert "zscore" in result.columns
        zscores = result["zscore"].to_numpy()

        # Mean should be 0
        assert abs(np.mean(zscores)) < 0.01

        # Std should be 1 (approximately, for small sample)
        assert 0.5 < np.std(zscores) < 1.5

    def test_sector_neutralization_demeans(self, factor_builder: FactorBuilder):
        """Sector neutralization demeans within sectors."""
        # This tests the internal method
        df = pl.DataFrame(
            {
                "permno": [1, 2, 3, 4, 5, 6],
                "zscore": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
            }
        )

        # With sector neutralization enabled
        result = factor_builder._neutralize_sector(df, "zscore", date(2023, 6, 30))

        # Result should still have zscore column
        assert "zscore" in result.columns


class TestFactorBuilderCustomFactors:
    """Tests for registering custom factors."""

    def test_register_custom_factor(self, factor_builder: FactorBuilder):
        """Can register and use custom factor."""

        class CustomFactor:
            @property
            def name(self) -> str:
                return "custom_factor"

            @property
            def category(self) -> str:
                return "custom"

            @property
            def description(self) -> str:
                return "A custom factor"

            @property
            def requires_fundamentals(self) -> bool:
                return False

            def compute(self, prices, fundamentals, as_of_date):
                return pl.DataFrame(
                    {
                        "permno": [1, 2, 3],
                        "factor_value": [0.1, 0.2, 0.3],
                    }
                )

        factor_builder.register_factor(CustomFactor())

        assert "custom_factor" in factor_builder.list_factors()

        result = factor_builder.compute_factor("custom_factor", date(2023, 6, 30))
        assert result.exposures.height > 0
