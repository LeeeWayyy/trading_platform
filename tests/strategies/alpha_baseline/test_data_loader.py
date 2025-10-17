"""
Unit tests for T1 data provider.

Tests cover:
- Loading data from Parquet files
- Date range filtering
- Multi-symbol handling
- Empty data cases
- MultiIndex structure
- Column name standardization
"""

from datetime import date
from pathlib import Path
import tempfile
import shutil

import pytest
import polars as pl
import pandas as pd

from strategies.alpha_baseline.data_loader import T1DataProvider


class TestT1DataProvider:
    """Tests for T1DataProvider class."""

    def setup_method(self) -> None:
        """Create temporary test data directory."""
        self.temp_dir = Path(tempfile.mkdtemp())
        self.provider = T1DataProvider(data_dir=self.temp_dir)

    def teardown_method(self) -> None:
        """Clean up temporary directory."""
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)

    def _create_test_data(self, symbol: str, dates: list[str], closes: list[float]) -> None:
        """
        Helper to create test Parquet files.

        Args:
            symbol: Stock symbol
            dates: List of date strings (YYYY-MM-DD)
            closes: List of close prices
        """
        # Create date partition directory
        partition_dir = self.temp_dir / "2024-01-01"
        partition_dir.mkdir(parents=True, exist_ok=True)

        # Create test DataFrame
        df = pl.DataFrame(
            {
                "symbol": [symbol] * len(dates),
                "date": [date.fromisoformat(d) for d in dates],
                "open": [c * 0.98 for c in closes],
                "high": [c * 1.02 for c in closes],
                "low": [c * 0.97 for c in closes],
                "close": closes,
                "volume": [1_000_000.0] * len(dates),
            }
        )

        # Write to Parquet
        df.write_parquet(partition_dir / f"{symbol}.parquet")

    def test_load_single_symbol_success(self) -> None:
        """Load data for single symbol successfully."""
        # Create test data
        self._create_test_data("AAPL", ["2024-01-01", "2024-01-02", "2024-01-03"], [150.0, 151.0, 152.0])

        # Load data
        df = self.provider.load_data(
            symbols=["AAPL"], start_date=date(2024, 1, 1), end_date=date(2024, 1, 3)
        )

        # Check structure
        assert isinstance(df, pd.DataFrame)
        assert df.index.names == ["date", "symbol"]
        assert list(df.columns) == ["open", "high", "low", "close", "volume"]

        # Check data
        assert len(df) == 3
        assert all(df.reset_index()["symbol"] == "AAPL")
        # Access with Timestamp (Pandas converts date to Timestamp)
        assert df.loc[(pd.Timestamp("2024-01-01"), "AAPL"), "close"] == 150.0

    def test_load_multiple_symbols(self) -> None:
        """Load data for multiple symbols."""
        # Create test data for two symbols
        self._create_test_data("AAPL", ["2024-01-01", "2024-01-02"], [150.0, 151.0])
        self._create_test_data("MSFT", ["2024-01-01", "2024-01-02"], [350.0, 355.0])

        # Load data
        df = self.provider.load_data(
            symbols=["AAPL", "MSFT"], start_date=date(2024, 1, 1), end_date=date(2024, 1, 2)
        )

        # Check we got both symbols
        assert len(df) == 4  # 2 symbols × 2 dates
        symbols = df.reset_index()["symbol"].unique()
        assert set(symbols) == {"AAPL", "MSFT"}

    def test_date_range_filtering(self) -> None:
        """Filter data by date range."""
        # Create data spanning 5 days
        dates = ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]
        self._create_test_data("AAPL", dates, [150.0, 151.0, 152.0, 153.0, 154.0])

        # Load only middle 3 days
        df = self.provider.load_data(
            symbols=["AAPL"], start_date=date(2024, 1, 2), end_date=date(2024, 1, 4)
        )

        # Check filtering worked
        assert len(df) == 3
        dates_loaded = [d.date() for d in df.reset_index()["date"].tolist()]
        assert dates_loaded == [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]

    def test_field_selection(self) -> None:
        """Select specific fields only."""
        self._create_test_data("AAPL", ["2024-01-01"], [150.0])

        # Load only close and volume
        df = self.provider.load_data(
            symbols=["AAPL"],
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 1),
            fields=["close", "volume"],
        )

        # Check only requested fields returned
        assert list(df.columns) == ["close", "volume"]

    def test_empty_result_when_no_data(self) -> None:
        """Return empty DataFrame when no data found."""
        # Don't create any data

        df = self.provider.load_data(
            symbols=["AAPL"], start_date=date(2024, 1, 1), end_date=date(2024, 1, 31)
        )

        # Check structure is correct even when empty
        assert isinstance(df, pd.DataFrame)
        assert df.index.names == ["date", "symbol"]
        assert len(df) == 0

    def test_empty_result_when_date_out_of_range(self) -> None:
        """Return empty DataFrame when date range doesn't match data."""
        self._create_test_data("AAPL", ["2024-01-01", "2024-01-02"], [150.0, 151.0])

        # Request dates that don't exist
        df = self.provider.load_data(
            symbols=["AAPL"], start_date=date(2024, 2, 1), end_date=date(2024, 2, 28)
        )

        assert len(df) == 0

    def test_symbol_not_found_returns_empty(self) -> None:
        """Symbol not in data returns empty (not error)."""
        self._create_test_data("AAPL", ["2024-01-01"], [150.0])

        # Request symbol that doesn't exist
        df = self.provider.load_data(
            symbols=["GOOGL"], start_date=date(2024, 1, 1), end_date=date(2024, 1, 1)
        )

        assert len(df) == 0

    def test_data_sorted_by_symbol_and_date(self) -> None:
        """Data should be sorted by symbol then date."""
        # Create data for two symbols with dates in random order
        self._create_test_data("MSFT", ["2024-01-02", "2024-01-01"], [350.0, 345.0])
        self._create_test_data("AAPL", ["2024-01-02", "2024-01-01"], [151.0, 150.0])

        df = self.provider.load_data(
            symbols=["AAPL", "MSFT"], start_date=date(2024, 1, 1), end_date=date(2024, 1, 2)
        )

        # Check sort order
        reset_df = df.reset_index()
        # Convert Timestamp to date for comparison
        dates = [d.date() for d in reset_df["date"]]
        symbols = reset_df["symbol"].tolist()
        index_values = list(zip(dates, symbols))

        expected = [
            (date(2024, 1, 1), "AAPL"),
            (date(2024, 1, 2), "AAPL"),
            (date(2024, 1, 1), "MSFT"),
            (date(2024, 1, 2), "MSFT"),
        ]
        assert index_values == expected

    def test_column_names_lowercase(self) -> None:
        """Column names should be lowercase for Qlib compatibility."""
        self._create_test_data("AAPL", ["2024-01-01"], [150.0])

        df = self.provider.load_data(
            symbols=["AAPL"], start_date=date(2024, 1, 1), end_date=date(2024, 1, 1)
        )

        # All columns should be lowercase
        for col in df.columns:
            assert col == col.lower()

    def test_invalid_fields_raises_error(self) -> None:
        """Invalid field names should raise ValueError."""
        self._create_test_data("AAPL", ["2024-01-01"], [150.0])

        with pytest.raises(ValueError) as exc_info:
            self.provider.load_data(
                symbols=["AAPL"],
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 1),
                fields=["close", "invalid_field"],
            )

        assert "Invalid fields" in str(exc_info.value)

    def test_nonexistent_data_dir_raises_error(self) -> None:
        """Non-existent data directory should raise ValueError."""
        provider = T1DataProvider(data_dir=Path("/nonexistent/path"))

        with pytest.raises(ValueError) as exc_info:
            provider.load_data(
                symbols=["AAPL"], start_date=date(2024, 1, 1), end_date=date(2024, 1, 1)
            )

        assert "Data directory not found" in str(exc_info.value)

    def test_get_available_symbols(self) -> None:
        """Get list of available symbols."""
        self._create_test_data("AAPL", ["2024-01-01"], [150.0])
        self._create_test_data("MSFT", ["2024-01-01"], [350.0])
        self._create_test_data("GOOGL", ["2024-01-01"], [140.0])

        symbols = self.provider.get_available_symbols()

        assert set(symbols) == {"AAPL", "GOOGL", "MSFT"}
        assert symbols == sorted(symbols)  # Should be sorted

    def test_get_available_symbols_empty_dir(self) -> None:
        """Get available symbols from empty directory."""
        symbols = self.provider.get_available_symbols()
        assert symbols == []

    def test_get_date_range(self) -> None:
        """Get date range for a symbol."""
        self._create_test_data(
            "AAPL", ["2024-01-01", "2024-01-15", "2024-01-31"], [150.0, 155.0, 160.0]
        )

        min_date, max_date = self.provider.get_date_range("AAPL")

        assert min_date == date(2024, 1, 1)
        assert max_date == date(2024, 1, 31)

    def test_get_date_range_symbol_not_found(self) -> None:
        """Get date range for non-existent symbol."""
        min_date, max_date = self.provider.get_date_range("NONEXISTENT")

        assert min_date is None
        assert max_date is None

    def test_multiindex_structure(self) -> None:
        """Verify MultiIndex structure is correct for Qlib."""
        self._create_test_data("AAPL", ["2024-01-01"], [150.0])

        df = self.provider.load_data(
            symbols=["AAPL"], start_date=date(2024, 1, 1), end_date=date(2024, 1, 1)
        )

        # Check MultiIndex properties
        assert isinstance(df.index, pd.MultiIndex)
        assert df.index.nlevels == 2
        assert df.index.names == ["date", "symbol"]

        # Check index types (Pandas converts date to Timestamp)
        assert isinstance(df.index.get_level_values("symbol")[0], str)
        # Date will be pd.Timestamp after Pandas conversion
        first_date = df.index.get_level_values("date")[0]
        assert hasattr(first_date, 'date')  # Can convert to date
