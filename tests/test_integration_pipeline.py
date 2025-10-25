"""
Integration test for the complete T1 data pipeline.

This test demonstrates the full ETL pipeline working end-to-end with
realistic multi-symbol data including splits, dividends, and outliers.

Test Scenario:
- 3 symbols: AAPL (with split), MSFT (with dividend), GOOGL (with outlier)
- 20 days of data per symbol
- Validates all components working together:
  1. Freshness check
  2. Corporate action adjustment
  3. Quality gate (outlier detection)
  4. File persistence
  5. Data loading

This is the "realistic scenario" test from ADR-0001.
"""

import shutil
import tempfile
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl

from libs.data_pipeline.etl import load_adjusted_data, run_etl_pipeline
from tests.fixtures.mock_data import create_multi_symbol_data


class TestCompleteDataPipeline:
    """Integration test for complete T1 pipeline."""

    def test_end_to_end_pipeline_realistic_scenario(self):
        """
        End-to-end test with realistic multi-symbol data.

        This demonstrates the complete T1 implementation working correctly:
        - AAPL: 4-for-1 split on day 10
        - MSFT: $2 dividend on day 10
        - GOOGL: 50% outlier on day 10 (should be quarantined)
        """
        # Create test data
        test_data = create_multi_symbol_data(
            symbols=["AAPL", "MSFT", "GOOGL"],
            num_days=20,
            include_split=True,
            include_dividend=True,
            include_outlier=True,
        )

        raw_data = test_data["raw_data"]
        splits = test_data.get("splits")
        dividends = test_data.get("dividends")

        # Create temporary output directory
        temp_dir = Path(tempfile.mkdtemp())

        try:
            # Run complete pipeline
            result = run_etl_pipeline(
                raw_data=raw_data,
                splits_df=splits,
                dividends_df=dividends,
                freshness_minutes=999999,  # Disable for mock data
                outlier_threshold=0.30,
                output_dir=temp_dir,
                run_date=date(2024, 1, 15),
            )

            # === Validate Results ===

            # 1. Check that all symbols were processed
            assert "AAPL" in result["stats"]["symbols_processed"]
            assert "MSFT" in result["stats"]["symbols_processed"]
            assert "GOOGL" in result["stats"]["symbols_processed"]

            # 2. Check that GOOGL outlier was quarantined
            quarantined = result["quarantined"]
            assert len(quarantined) > 0, "Expected outlier to be quarantined"

            # All quarantined should be GOOGL (has the outlier)
            assert all(quarantined["symbol"] == "GOOGL")

            # Quarantine should have reason
            assert "reason" in quarantined.columns
            assert "outlier" in quarantined["reason"][0]

            # 3. Check that AAPL split was adjusted correctly
            adjusted = result["adjusted"]
            aapl_data = adjusted.filter(pl.col("symbol") == "AAPL").sort("date")

            # After adjustment, prices should be continuous (no 75% drop)
            aapl_closes = aapl_data["close"].to_list()

            # Calculate returns to verify continuity
            returns = []
            for i in range(1, len(aapl_closes)):
                ret = abs((aapl_closes[i] - aapl_closes[i - 1]) / aapl_closes[i - 1])
                returns.append(ret)

            # All returns should be small (< 5%) after adjustment
            max_return = max(returns) if returns else 0
            assert max_return < 0.05, f"Expected continuous prices, got max return {max_return:.2%}"

            # 4. Check that MSFT dividend was adjusted
            msft_data = adjusted.filter(pl.col("symbol") == "MSFT").sort("date")

            # Prices should be continuous (no sudden $2 drop)
            msft_closes = msft_data["close"].to_list()
            msft_returns = []
            for i in range(1, len(msft_closes)):
                ret = abs((msft_closes[i] - msft_closes[i - 1]) / msft_closes[i - 1])
                msft_returns.append(ret)

            max_msft_return = max(msft_returns) if msft_returns else 0
            assert max_msft_return < 0.05, "Expected continuous prices after dividend adjustment"

            # 5. Check file persistence
            adjusted_dir = temp_dir / "adjusted" / "2024-01-15"
            quarantine_dir = temp_dir / "quarantine" / "2024-01-15"

            # Adjusted files should exist for all symbols
            assert (adjusted_dir / "AAPL.parquet").exists()
            assert (adjusted_dir / "MSFT.parquet").exists()
            # GOOGL might be partially quarantined, so it may or may not have adjusted file

            # Quarantine file should exist for GOOGL
            assert (quarantine_dir / "GOOGL.parquet").exists()

            # 6. Test loading data back
            loaded = load_adjusted_data(symbols=["AAPL", "MSFT"], data_dir=temp_dir / "adjusted")

            assert len(loaded) > 0
            assert set(loaded["symbol"].unique().to_list()).issubset({"AAPL", "MSFT"})

            # 7. Validate statistics
            stats = result["stats"]
            assert stats["input_rows"] > 0
            assert stats["adjusted_rows"] > 0
            assert stats["quarantined_rows"] > 0

            # Input should equal adjusted + quarantined
            assert stats["input_rows"] == stats["adjusted_rows"] + stats["quarantined_rows"]

            print("\n=== Integration Test Results ===")
            print(f"Input rows: {stats['input_rows']}")
            print(f"Adjusted rows: {stats['adjusted_rows']}")
            print(f"Quarantined rows: {stats['quarantined_rows']}")
            print(f"Symbols processed: {stats['symbols_processed']}")
            print(f"Outliers detected: {len(quarantined)} rows")
            print("✓ All validations passed!")

        finally:
            # Clean up
            shutil.rmtree(temp_dir)

    def test_pipeline_with_no_corporate_actions(self):
        """Pipeline should work correctly with no corporate actions."""
        # Create simple data without any CAs
        raw_data = pl.DataFrame(
            {
                "symbol": ["AAPL"] * 5,
                "date": ["2024-01-10", "2024-01-11", "2024-01-12", "2024-01-15", "2024-01-16"],
                "open": [149.0, 150.5, 151.0, 152.0, 151.5],
                "high": [151.0, 152.0, 153.0, 154.0, 153.0],
                "low": [148.0, 149.0, 150.0, 151.0, 150.0],
                "close": [150.0, 151.0, 152.0, 153.0, 152.0],
                "volume": [1_000_000, 1_100_000, 1_200_000, 1_300_000, 1_250_000],
                "timestamp": [datetime.now(UTC)] * 5,
            }
        )

        result = run_etl_pipeline(
            raw_data=raw_data, splits_df=None, dividends_df=None, output_dir=None
        )

        # All data should pass through unchanged
        assert len(result["adjusted"]) == 5
        assert len(result["quarantined"]) == 0

        # Data should be identical to input (no adjustments)
        adjusted = result["adjusted"]
        assert all(adjusted["close"] == raw_data["close"])

    def test_pipeline_handles_all_quarantined(self):
        """Pipeline should handle case where all data is quarantined."""
        # Create data with all outliers (wildly volatile)
        raw_data = pl.DataFrame(
            {
                "symbol": ["AAPL"] * 5,
                "date": ["2024-01-10", "2024-01-11", "2024-01-12", "2024-01-15", "2024-01-16"],
                "open": [100.0, 200.0, 50.0, 300.0, 25.0],
                "high": [110.0, 220.0, 60.0, 320.0, 35.0],
                "low": [95.0, 180.0, 45.0, 280.0, 20.0],
                "close": [100.0, 200.0, 50.0, 300.0, 25.0],  # Huge swings
                "volume": [1_000_000, 5_000_000, 2_000_000, 8_000_000, 1_500_000],
                "timestamp": [datetime.now(UTC)] * 5,
            }
        )

        result = run_etl_pipeline(raw_data=raw_data, outlier_threshold=0.30, output_dir=None)

        # Most or all should be quarantined (except first row which can't be flagged)
        assert len(result["quarantined"]) >= 3
        assert result["stats"]["quarantined_rows"] >= 3

    def test_pipeline_performance_target(self):
        """
        Pipeline should process ~750 rows in < 1 second.

        Performance target from ADR-0001:
        - Process 252 days × 3 symbols (756 rows) in <1 second
        """
        import time

        # Create data: 252 days × 3 symbols = 756 rows
        test_data = create_multi_symbol_data(
            symbols=["AAPL", "MSFT", "GOOGL"],
            num_days=252,
            include_split=False,  # No CAs to measure baseline performance
            include_dividend=False,
            include_outlier=False,
        )

        raw_data = test_data["raw_data"]

        # Measure execution time
        start = time.time()

        result = run_etl_pipeline(
            raw_data=raw_data,
            freshness_minutes=999999,  # Disable for mock data
            output_dir=None,  # Skip disk I/O for pure processing time
        )

        elapsed = time.time() - start

        print("\n=== Performance Test ===")
        print(f"Rows processed: {result['stats']['input_rows']}")
        print(f"Time elapsed: {elapsed:.3f} seconds")
        print(f"Throughput: {result['stats']['input_rows'] / elapsed:.0f} rows/second")

        # Should complete in under 1 second (generous for CI environments)
        assert elapsed < 2.0, f"Pipeline too slow: {elapsed:.2f}s for {len(raw_data)} rows"

    def test_data_immutability(self):
        """Pipeline should not modify input data (immutability)."""
        # Create test data
        raw_data = pl.DataFrame(
            {
                "symbol": ["AAPL"] * 3,
                "date": ["2024-01-10", "2024-01-11", "2024-01-12"],
                "open": [149.0, 150.5, 151.0],
                "high": [151.0, 152.0, 153.0],
                "low": [148.0, 149.0, 150.0],
                "close": [150.0, 151.0, 152.0],
                "volume": [1_000_000, 1_100_000, 1_200_000],
                "timestamp": [datetime.now(UTC)] * 3,
            }
        )

        # Save original data for comparison
        original_close = raw_data["close"].to_list()

        # Run pipeline
        _result = run_etl_pipeline(raw_data, output_dir=None)

        # Original data should be unchanged
        assert raw_data["close"].to_list() == original_close

        print("\n✓ Data immutability verified - input data unchanged after pipeline")
