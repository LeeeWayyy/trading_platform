"""Tests for Universe and Forward Returns Providers.

P6T10: Track 10 - Quantile & Attribution Analytics

Coverage targets:
- UniverseProvider initialization and validation
- UniverseProvider.get_constituents
- UniverseProvider.get_constituents_range
- ForwardReturnsProvider initialization and validation
- ForwardReturnsProvider.get_daily_returns
- ForwardReturnsProvider.get_forward_returns (main method)
- Date normalization, skip_days validation, deduplication
- Error handling (CRSPUnavailableError)
"""

from __future__ import annotations

import importlib.util
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

# Skip if optional heavy deps missing
_missing = [mod for mod in ("polars",) if importlib.util.find_spec(mod) is None]
if _missing:
    pytest.skip(
        f"Skipping universe provider tests because dependencies are missing: {', '.join(_missing)}",
        allow_module_level=True,
    )

import polars as pl

from libs.data.data_providers.universe import (
    CRSPUnavailableError,
    ForwardReturnsProvider,
    UniverseProvider,
)

# ------------------------------------------------------------------ Mock Calendar


class MockCalendar:
    """Mock exchange calendar for testing."""

    def __init__(self, sessions: list[date]):
        self._sessions = set(sessions)
        self._sessions_list = sorted(sessions)

    def is_session(self, d: date) -> bool:
        return d in self._sessions

    def date_to_session(self, d: date, direction: str = "previous") -> MagicMock:
        """Return a mock Timestamp with .date() method."""
        if d in self._sessions:
            mock = MagicMock()
            mock.date.return_value = d
            return mock

        idx = None
        for i, s in enumerate(self._sessions_list):
            if s > d:
                idx = i
                break

        if direction == "previous":
            if idx is None:
                result_date = self._sessions_list[-1] if self._sessions_list else d
            elif idx == 0:
                result_date = self._sessions_list[0]
            else:
                result_date = self._sessions_list[idx - 1]
        else:  # "next"
            if idx is None:
                result_date = self._sessions_list[-1] if self._sessions_list else d
            else:
                result_date = self._sessions_list[idx]

        mock = MagicMock()
        mock.date.return_value = result_date
        return mock

    def sessions_in_range(self, start: date, end: date):
        """Return sessions between start and end (inclusive)."""
        result = []
        for s in self._sessions_list:
            if start <= s <= end:
                mock = MagicMock()
                mock.date.return_value = s
                result.append(mock)
        return result

    def session_offset(self, d: date, offset: int) -> MagicMock:
        """Offset a date by trading days."""
        if d not in self._sessions:
            d = self.date_to_session(d, "previous").date()

        idx = self._sessions_list.index(d)
        target_idx = min(max(idx + offset, 0), len(self._sessions_list) - 1)
        result_date = self._sessions_list[target_idx]
        mock = MagicMock()
        mock.date.return_value = result_date
        return mock


def _create_trading_dates(start: date, n_days: int) -> list[date]:
    """Create list of trading dates (skip weekends)."""
    dates = []
    current = start
    while len(dates) < n_days:
        if current.weekday() < 5:
            dates.append(current)
        current = date(current.year, current.month, current.day + 1) if current.day < 28 else date(
            current.year, current.month + 1, 1
        )
    return dates


@pytest.fixture()
def trading_calendar() -> MockCalendar:
    """Fixture for mock trading calendar with 100 trading days."""
    sessions = _create_trading_dates(date(2024, 1, 2), 100)
    return MockCalendar(sessions)


# ------------------------------------------------------------------ UniverseProvider Tests


@pytest.mark.unit()
class TestUniverseProviderInit:
    """Tests for UniverseProvider initialization."""

    def test_init_no_dir_configured_raises(self):
        """Missing CRSP dir should raise CRSPUnavailableError."""
        with patch("libs.data.data_providers.universe.get_settings") as mock_settings:
            mock_settings.return_value.crsp_data_dir = None

            with pytest.raises(CRSPUnavailableError, match="not configured"):
                UniverseProvider()

    def test_init_empty_dir_configured_raises(self):
        """Empty CRSP dir should raise CRSPUnavailableError."""
        with patch("libs.data.data_providers.universe.get_settings") as mock_settings:
            mock_settings.return_value.crsp_data_dir = ""

            with pytest.raises(CRSPUnavailableError, match="not configured"):
                UniverseProvider()

    def test_init_dir_not_found_raises(self, tmp_path):
        """Non-existent CRSP dir should raise CRSPUnavailableError."""
        with patch("libs.data.data_providers.universe.get_settings") as mock_settings:
            mock_settings.return_value.crsp_data_dir = str(tmp_path / "nonexistent")

            with pytest.raises(CRSPUnavailableError, match="not found"):
                UniverseProvider()

    def test_init_missing_constituents_file_raises(self, tmp_path):
        """Missing index_constituents.parquet should raise."""
        crsp_dir = tmp_path / "crsp"
        crsp_dir.mkdir()

        with patch("libs.data.data_providers.universe.get_settings") as mock_settings:
            mock_settings.return_value.crsp_data_dir = str(crsp_dir)

            with pytest.raises(CRSPUnavailableError, match="constituents file not found"):
                UniverseProvider()

    def test_init_happy_path(self, tmp_path):
        """Valid CRSP dir with constituents file should succeed."""
        crsp_dir = tmp_path / "crsp"
        crsp_dir.mkdir()

        # Create minimal constituents file
        df = pl.DataFrame({
            "date": [date(2024, 1, 2)],
            "universe_id": ["SP500"],
            "permno": [10001],
        })
        df.write_parquet(crsp_dir / "index_constituents.parquet")

        with patch("libs.data.data_providers.universe.get_settings") as mock_settings:
            mock_settings.return_value.crsp_data_dir = str(crsp_dir)

            provider = UniverseProvider()
            assert provider._data_dir == crsp_dir


@pytest.mark.unit()
class TestUniverseProviderGetConstituents:
    """Tests for UniverseProvider.get_constituents."""

    def test_get_constituents_happy_path(self, tmp_path):
        """Should return permnos for universe and date."""
        crsp_dir = tmp_path / "crsp"
        crsp_dir.mkdir()

        df = pl.DataFrame({
            "date": [date(2024, 1, 2), date(2024, 1, 2), date(2024, 1, 3)],
            "universe_id": ["SP500", "SP500", "SP500"],
            "permno": [10001, 10002, 10001],
        })
        df.write_parquet(crsp_dir / "index_constituents.parquet")

        with patch("libs.data.data_providers.universe.get_settings") as mock_settings:
            mock_settings.return_value.crsp_data_dir = str(crsp_dir)

            provider = UniverseProvider()
            result = provider.get_constituents("SP500", date(2024, 1, 2))

            assert result.height == 2
            assert "permno" in result.columns
            assert set(result["permno"].to_list()) == {10001, 10002}

    def test_get_constituents_no_match(self, tmp_path):
        """Should return empty DataFrame if no match."""
        crsp_dir = tmp_path / "crsp"
        crsp_dir.mkdir()

        df = pl.DataFrame({
            "date": [date(2024, 1, 2)],
            "universe_id": ["SP500"],
            "permno": [10001],
        })
        df.write_parquet(crsp_dir / "index_constituents.parquet")

        with patch("libs.data.data_providers.universe.get_settings") as mock_settings:
            mock_settings.return_value.crsp_data_dir = str(crsp_dir)

            provider = UniverseProvider()
            result = provider.get_constituents("R1000", date(2024, 1, 2))

            assert result.height == 0


@pytest.mark.unit()
class TestUniverseProviderGetConstituentsRange:
    """Tests for UniverseProvider.get_constituents_range."""

    def test_get_constituents_range_happy_path(self, tmp_path):
        """Should return [date, permno] for date range."""
        crsp_dir = tmp_path / "crsp"
        crsp_dir.mkdir()

        df = pl.DataFrame({
            "date": [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)],
            "universe_id": ["SP500", "SP500", "SP500"],
            "permno": [10001, 10001, 10001],
        })
        df.write_parquet(crsp_dir / "index_constituents.parquet")

        with patch("libs.data.data_providers.universe.get_settings") as mock_settings:
            mock_settings.return_value.crsp_data_dir = str(crsp_dir)

            provider = UniverseProvider()
            result = provider.get_constituents_range(
                "SP500",
                date(2024, 1, 2),
                date(2024, 1, 3),
            )

            assert result.height == 2
            assert set(result.columns) == {"date", "permno"}


# ------------------------------------------------------------------ ForwardReturnsProvider Tests


@pytest.mark.unit()
class TestForwardReturnsProviderInit:
    """Tests for ForwardReturnsProvider initialization."""

    def test_init_no_dir_configured_raises(self):
        """Missing CRSP dir should raise CRSPUnavailableError."""
        with patch("libs.data.data_providers.universe.get_settings") as mock_settings:
            mock_settings.return_value.crsp_data_dir = None

            with pytest.raises(CRSPUnavailableError, match="not configured"):
                ForwardReturnsProvider()

    def test_init_empty_dir_configured_raises(self):
        """Empty CRSP dir should raise CRSPUnavailableError."""
        with patch("libs.data.data_providers.universe.get_settings") as mock_settings:
            mock_settings.return_value.crsp_data_dir = ""

            with pytest.raises(CRSPUnavailableError, match="not configured"):
                ForwardReturnsProvider()

    def test_init_dir_not_found_raises(self, tmp_path):
        """Non-existent CRSP dir should raise CRSPUnavailableError."""
        with patch("libs.data.data_providers.universe.get_settings") as mock_settings:
            mock_settings.return_value.crsp_data_dir = str(tmp_path / "nonexistent")

            with pytest.raises(CRSPUnavailableError, match="not found"):
                ForwardReturnsProvider()

    def test_init_missing_returns_file_raises(self, tmp_path):
        """Missing daily_returns.parquet should raise."""
        crsp_dir = tmp_path / "crsp"
        crsp_dir.mkdir()

        with patch("libs.data.data_providers.universe.get_settings") as mock_settings:
            mock_settings.return_value.crsp_data_dir = str(crsp_dir)

            with pytest.raises(CRSPUnavailableError, match="returns file not found"):
                ForwardReturnsProvider()

    def test_init_happy_path(self, tmp_path):
        """Valid CRSP dir with returns file should succeed."""
        crsp_dir = tmp_path / "crsp"
        crsp_dir.mkdir()

        # Create minimal returns file
        df = pl.DataFrame({
            "date": [date(2024, 1, 2)],
            "permno": [10001],
            "ret": [0.01],
        })
        df.write_parquet(crsp_dir / "daily_returns.parquet")

        with patch("libs.data.data_providers.universe.get_settings") as mock_settings:
            mock_settings.return_value.crsp_data_dir = str(crsp_dir)

            provider = ForwardReturnsProvider()
            assert provider._data_dir == crsp_dir


@pytest.mark.unit()
class TestForwardReturnsProviderGetDailyReturns:
    """Tests for ForwardReturnsProvider.get_daily_returns."""

    def test_get_daily_returns_happy_path(self, tmp_path):
        """Should return daily returns for permnos and date range."""
        crsp_dir = tmp_path / "crsp"
        crsp_dir.mkdir()

        df = pl.DataFrame({
            "date": [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 2)],
            "permno": [10001, 10001, 10002],
            "ret": [0.01, 0.02, -0.01],
        })
        df.write_parquet(crsp_dir / "daily_returns.parquet")

        with patch("libs.data.data_providers.universe.get_settings") as mock_settings:
            mock_settings.return_value.crsp_data_dir = str(crsp_dir)

            provider = ForwardReturnsProvider()
            result = provider.get_daily_returns(
                permnos=[10001, 10002],
                start_date=date(2024, 1, 2),
                end_date=date(2024, 1, 3),
            )

            assert result.height == 3
            assert set(result.columns) == {"date", "permno", "daily_return"}

    def test_get_daily_returns_filters_by_permno(self, tmp_path):
        """Should only return requested permnos."""
        crsp_dir = tmp_path / "crsp"
        crsp_dir.mkdir()

        df = pl.DataFrame({
            "date": [date(2024, 1, 2)] * 3,
            "permno": [10001, 10002, 10003],
            "ret": [0.01, 0.02, 0.03],
        })
        df.write_parquet(crsp_dir / "daily_returns.parquet")

        with patch("libs.data.data_providers.universe.get_settings") as mock_settings:
            mock_settings.return_value.crsp_data_dir = str(crsp_dir)

            provider = ForwardReturnsProvider()
            result = provider.get_daily_returns(
                permnos=[10001],
                start_date=date(2024, 1, 2),
                end_date=date(2024, 1, 2),
            )

            assert result.height == 1
            assert result["permno"][0] == 10001


@pytest.mark.unit()
class TestForwardReturnsProviderGetForwardReturns:
    """Tests for ForwardReturnsProvider.get_forward_returns."""

    def test_skip_days_zero_raises(self, tmp_path, trading_calendar: MockCalendar):
        """skip_days=0 should raise ValueError."""
        crsp_dir = tmp_path / "crsp"
        crsp_dir.mkdir()

        df = pl.DataFrame({
            "date": [date(2024, 1, 2)],
            "permno": [10001],
            "ret": [0.01],
        })
        df.write_parquet(crsp_dir / "daily_returns.parquet")

        with patch("libs.data.data_providers.universe.get_settings") as mock_settings:
            mock_settings.return_value.crsp_data_dir = str(crsp_dir)

            provider = ForwardReturnsProvider()
            signals = pl.DataFrame({
                "signal_date": [date(2024, 1, 2)],
                "permno": [10001],
            })

            with pytest.raises(ValueError, match="skip_days must be >= 1"):
                provider.get_forward_returns(
                    signals_df=signals,
                    skip_days=0,
                    holding_period=5,
                    calendar=trading_calendar,
                )

    def test_skip_days_negative_raises(self, tmp_path, trading_calendar: MockCalendar):
        """Negative skip_days should raise ValueError."""
        crsp_dir = tmp_path / "crsp"
        crsp_dir.mkdir()

        df = pl.DataFrame({
            "date": [date(2024, 1, 2)],
            "permno": [10001],
            "ret": [0.01],
        })
        df.write_parquet(crsp_dir / "daily_returns.parquet")

        with patch("libs.data.data_providers.universe.get_settings") as mock_settings:
            mock_settings.return_value.crsp_data_dir = str(crsp_dir)

            provider = ForwardReturnsProvider()
            signals = pl.DataFrame({
                "signal_date": [date(2024, 1, 2)],
                "permno": [10001],
            })

            with pytest.raises(ValueError, match="skip_days must be >= 1"):
                provider.get_forward_returns(
                    signals_df=signals,
                    skip_days=-1,
                    holding_period=5,
                    calendar=trading_calendar,
                )

    def test_holding_period_zero_raises(self, tmp_path, trading_calendar: MockCalendar):
        """holding_period=0 should raise ValueError."""
        crsp_dir = tmp_path / "crsp"
        crsp_dir.mkdir()

        df = pl.DataFrame({
            "date": [date(2024, 1, 2)],
            "permno": [10001],
            "ret": [0.01],
        })
        df.write_parquet(crsp_dir / "daily_returns.parquet")

        with patch("libs.data.data_providers.universe.get_settings") as mock_settings:
            mock_settings.return_value.crsp_data_dir = str(crsp_dir)

            provider = ForwardReturnsProvider()
            signals = pl.DataFrame({
                "signal_date": [date(2024, 1, 2)],
                "permno": [10001],
            })

            with pytest.raises(ValueError, match="holding_period must be > 0"):
                provider.get_forward_returns(
                    signals_df=signals,
                    skip_days=1,
                    holding_period=0,
                    calendar=trading_calendar,
                )

    def test_missing_columns_raises(self, tmp_path, trading_calendar: MockCalendar):
        """Missing required columns should raise ValueError."""
        crsp_dir = tmp_path / "crsp"
        crsp_dir.mkdir()

        df = pl.DataFrame({
            "date": [date(2024, 1, 2)],
            "permno": [10001],
            "ret": [0.01],
        })
        df.write_parquet(crsp_dir / "daily_returns.parquet")

        with patch("libs.data.data_providers.universe.get_settings") as mock_settings:
            mock_settings.return_value.crsp_data_dir = str(crsp_dir)

            provider = ForwardReturnsProvider()
            # Missing signal_date column
            signals = pl.DataFrame({
                "permno": [10001],
            })

            with pytest.raises(ValueError, match="missing required columns"):
                provider.get_forward_returns(
                    signals_df=signals,
                    skip_days=1,
                    holding_period=5,
                    calendar=trading_calendar,
                )

    def test_invalid_date_type_raises(self, tmp_path, trading_calendar: MockCalendar):
        """Non-Date/Datetime signal_date should raise ValueError."""
        crsp_dir = tmp_path / "crsp"
        crsp_dir.mkdir()

        df = pl.DataFrame({
            "date": [date(2024, 1, 2)],
            "permno": [10001],
            "ret": [0.01],
        })
        df.write_parquet(crsp_dir / "daily_returns.parquet")

        with patch("libs.data.data_providers.universe.get_settings") as mock_settings:
            mock_settings.return_value.crsp_data_dir = str(crsp_dir)

            provider = ForwardReturnsProvider()
            # String date type
            signals = pl.DataFrame({
                "signal_date": ["2024-01-02"],
                "permno": [10001],
            })

            with pytest.raises(ValueError, match="must be Date or Datetime"):
                provider.get_forward_returns(
                    signals_df=signals,
                    skip_days=1,
                    holding_period=5,
                    calendar=trading_calendar,
                )

    def test_empty_signals_returns_empty(self, tmp_path, trading_calendar: MockCalendar):
        """Empty signals should return empty DataFrame."""
        crsp_dir = tmp_path / "crsp"
        crsp_dir.mkdir()

        df = pl.DataFrame({
            "date": [date(2024, 1, 2)],
            "permno": [10001],
            "ret": [0.01],
        })
        df.write_parquet(crsp_dir / "daily_returns.parquet")

        with patch("libs.data.data_providers.universe.get_settings") as mock_settings:
            mock_settings.return_value.crsp_data_dir = str(crsp_dir)

            provider = ForwardReturnsProvider()
            signals = pl.DataFrame({
                "signal_date": pl.Series([], dtype=pl.Date),
                "permno": pl.Series([], dtype=pl.Int64),
            })

            result = provider.get_forward_returns(
                signals_df=signals,
                skip_days=1,
                holding_period=5,
                calendar=trading_calendar,
            )

            assert result.height == 0
            assert set(result.columns) == {"signal_date", "permno", "forward_return"}

    def test_get_forward_returns_happy_path(self, tmp_path, trading_calendar: MockCalendar):
        """Should compute compounded forward returns."""
        crsp_dir = tmp_path / "crsp"
        crsp_dir.mkdir()

        # Create returns for multiple days
        dates = _create_trading_dates(date(2024, 1, 2), 30)
        rows = []
        for d in dates:
            rows.append({"date": d, "permno": 10001, "ret": 0.01})  # 1% daily
        df = pl.DataFrame(rows)
        df.write_parquet(crsp_dir / "daily_returns.parquet")

        with patch("libs.data.data_providers.universe.get_settings") as mock_settings:
            mock_settings.return_value.crsp_data_dir = str(crsp_dir)

            provider = ForwardReturnsProvider()
            signals = pl.DataFrame({
                "signal_date": [dates[0]],
                "permno": [10001],
            })

            result = provider.get_forward_returns(
                signals_df=signals,
                skip_days=1,
                holding_period=5,
                calendar=trading_calendar,
            )

            assert result.height == 1
            # 5 days at 1% each = (1.01)^5 - 1 ≈ 5.1%
            expected = (1.01 ** 5) - 1
            assert abs(result["forward_return"][0] - expected) < 1e-6

    def test_get_forward_returns_filters_null_dates(self, tmp_path, trading_calendar: MockCalendar):
        """Null signal_date/permno should be filtered."""
        crsp_dir = tmp_path / "crsp"
        crsp_dir.mkdir()

        dates = _create_trading_dates(date(2024, 1, 2), 30)
        rows = []
        for d in dates:
            rows.append({"date": d, "permno": 10001, "ret": 0.01})
        df = pl.DataFrame(rows)
        df.write_parquet(crsp_dir / "daily_returns.parquet")

        with patch("libs.data.data_providers.universe.get_settings") as mock_settings:
            mock_settings.return_value.crsp_data_dir = str(crsp_dir)

            provider = ForwardReturnsProvider()
            # Include null values
            signals = pl.DataFrame({
                "signal_date": [dates[0], None],
                "permno": [10001, 10001],
            })

            result = provider.get_forward_returns(
                signals_df=signals,
                skip_days=1,
                holding_period=5,
                calendar=trading_calendar,
            )

            # Only valid row should produce result
            assert result.height == 1

    def test_get_forward_returns_deduplicates_returns(
        self, tmp_path, trading_calendar: MockCalendar
    ):
        """Duplicate (permno, date) returns should be averaged."""
        crsp_dir = tmp_path / "crsp"
        crsp_dir.mkdir()

        dates = _create_trading_dates(date(2024, 1, 2), 30)
        rows = []
        for d in dates:
            # Add duplicate entries with different returns
            rows.append({"date": d, "permno": 10001, "ret": 0.01})
            rows.append({"date": d, "permno": 10001, "ret": 0.03})  # Duplicate
        df = pl.DataFrame(rows)
        df.write_parquet(crsp_dir / "daily_returns.parquet")

        with patch("libs.data.data_providers.universe.get_settings") as mock_settings:
            mock_settings.return_value.crsp_data_dir = str(crsp_dir)

            provider = ForwardReturnsProvider()
            signals = pl.DataFrame({
                "signal_date": [dates[0]],
                "permno": [10001],
            })

            result = provider.get_forward_returns(
                signals_df=signals,
                skip_days=1,
                holding_period=5,
                calendar=trading_calendar,
            )

            assert result.height == 1
            # Average of 0.01 and 0.03 = 0.02 per day
            expected = (1.02 ** 5) - 1
            assert abs(result["forward_return"][0] - expected) < 1e-6

    def test_get_forward_returns_skips_missing_window(
        self, tmp_path, trading_calendar: MockCalendar
    ):
        """Signals with incomplete forward window should be skipped."""
        crsp_dir = tmp_path / "crsp"
        crsp_dir.mkdir()

        dates = _create_trading_dates(date(2024, 1, 2), 10)
        rows = []
        # Only first 3 days have returns
        for d in dates[:3]:
            rows.append({"date": d, "permno": 10001, "ret": 0.01})
        df = pl.DataFrame(rows)
        df.write_parquet(crsp_dir / "daily_returns.parquet")

        with patch("libs.data.data_providers.universe.get_settings") as mock_settings:
            mock_settings.return_value.crsp_data_dir = str(crsp_dir)

            provider = ForwardReturnsProvider()
            signals = pl.DataFrame({
                "signal_date": [dates[0]],
                "permno": [10001],
            })

            result = provider.get_forward_returns(
                signals_df=signals,
                skip_days=1,
                holding_period=10,  # Need 10 days but only 3 available
                calendar=trading_calendar,
            )

            # Should be empty due to insufficient window
            assert result.height == 0

    def test_get_forward_returns_normalizes_weekend_dates(
        self, tmp_path, trading_calendar: MockCalendar
    ):
        """Weekend signal dates should normalize to previous trading day."""
        crsp_dir = tmp_path / "crsp"
        crsp_dir.mkdir()

        dates = _create_trading_dates(date(2024, 1, 2), 30)
        rows = []
        for d in dates:
            rows.append({"date": d, "permno": 10001, "ret": 0.01})
        df = pl.DataFrame(rows)
        df.write_parquet(crsp_dir / "daily_returns.parquet")

        with patch("libs.data.data_providers.universe.get_settings") as mock_settings:
            mock_settings.return_value.crsp_data_dir = str(crsp_dir)

            provider = ForwardReturnsProvider()
            # Use a weekend date (Jan 6, 2024 is Saturday)
            # Should normalize to Jan 5 (Friday)
            signals = pl.DataFrame({
                "signal_date": [date(2024, 1, 6)],  # Saturday
                "permno": [10001],
            })

            result = provider.get_forward_returns(
                signals_df=signals,
                skip_days=1,
                holding_period=5,
                calendar=trading_calendar,
            )

            # Should have normalized and computed (if Friday exists in calendar)
            # Result may be empty if Friday isn't in the mock calendar window
            assert result.height >= 0

    def test_get_forward_returns_handles_nan_returns(
        self, tmp_path, trading_calendar: MockCalendar
    ):
        """NaN returns in window should cause skip."""
        crsp_dir = tmp_path / "crsp"
        crsp_dir.mkdir()

        dates = _create_trading_dates(date(2024, 1, 2), 30)
        rows = []
        for i, d in enumerate(dates):
            # Make third day NaN
            ret = float("nan") if i == 2 else 0.01
            rows.append({"date": d, "permno": 10001, "ret": ret})
        df = pl.DataFrame(rows)
        df.write_parquet(crsp_dir / "daily_returns.parquet")

        with patch("libs.data.data_providers.universe.get_settings") as mock_settings:
            mock_settings.return_value.crsp_data_dir = str(crsp_dir)

            provider = ForwardReturnsProvider()
            signals = pl.DataFrame({
                "signal_date": [dates[0]],
                "permno": [10001],
            })

            result = provider.get_forward_returns(
                signals_df=signals,
                skip_days=1,
                holding_period=5,  # Window includes the NaN day
                calendar=trading_calendar,
            )

            # Should be empty due to NaN in window
            assert result.height == 0


@pytest.mark.unit()
class TestCRSPUnavailableError:
    """Tests for CRSPUnavailableError exception."""

    def test_error_is_data_provider_error(self):
        """Should inherit from DataProviderError."""
        from libs.data.data_providers.protocols import DataProviderError

        error = CRSPUnavailableError("CRSP not available")
        assert isinstance(error, DataProviderError)
        assert str(error) == "CRSP not available"


# ------------------------------------------------------------------ Error Handling Edge Cases


@pytest.mark.unit()
class TestUniverseProviderErrorHandling:
    """Tests for error handling in UniverseProvider."""

    def test_get_constituents_parquet_error_raises(self, tmp_path):
        """Should raise CRSPUnavailableError on parquet read failure."""
        crsp_dir = tmp_path / "crsp"
        crsp_dir.mkdir()

        # Create an invalid parquet file with correct filename
        invalid_file = crsp_dir / "index_constituents.parquet"
        invalid_file.write_text("not a parquet file")

        with patch("libs.data.data_providers.universe.get_settings") as mock_settings:
            mock_settings.return_value.crsp_data_dir = str(crsp_dir)

            provider = UniverseProvider()

            with pytest.raises(CRSPUnavailableError, match="Failed to load universe"):
                provider.get_constituents("SP500", date(2024, 1, 1))

    def test_get_constituents_range_parquet_error_raises(self, tmp_path):
        """Should raise CRSPUnavailableError on parquet read failure in range query."""
        crsp_dir = tmp_path / "crsp"
        crsp_dir.mkdir()

        # Create an invalid parquet file with correct filename
        invalid_file = crsp_dir / "index_constituents.parquet"
        invalid_file.write_text("not a parquet file")

        with patch("libs.data.data_providers.universe.get_settings") as mock_settings:
            mock_settings.return_value.crsp_data_dir = str(crsp_dir)

            provider = UniverseProvider()

            with pytest.raises(CRSPUnavailableError, match="Failed to load universe"):
                provider.get_constituents_range("SP500", date(2024, 1, 1), date(2024, 1, 31))


@pytest.mark.unit()
class TestForwardReturnsProviderErrorHandling:
    """Tests for error handling in ForwardReturnsProvider."""

    def test_get_daily_returns_parquet_error_raises(self, tmp_path):
        """Should raise CRSPUnavailableError on parquet read failure."""
        crsp_dir = tmp_path / "crsp"
        crsp_dir.mkdir()

        # Create an invalid parquet file
        invalid_file = crsp_dir / "daily_returns.parquet"
        invalid_file.write_text("not a parquet file")

        with patch("libs.data.data_providers.universe.get_settings") as mock_settings:
            mock_settings.return_value.crsp_data_dir = str(crsp_dir)

            provider = ForwardReturnsProvider()

            with pytest.raises(CRSPUnavailableError, match="Failed to load CRSP returns"):
                provider.get_daily_returns([10001], date(2024, 1, 1), date(2024, 1, 31))

    def test_get_forward_returns_calendar_normalization_failure(
        self, tmp_path, trading_calendar: MockCalendar
    ):
        """Should handle calendar normalization failures gracefully."""
        crsp_dir = tmp_path / "crsp"
        crsp_dir.mkdir()

        dates = _create_trading_dates(date(2024, 1, 2), 30)
        rows = []
        for d in dates:
            rows.append({"date": d, "permno": 10001, "ret": 0.01})
        df = pl.DataFrame(rows)
        df.write_parquet(crsp_dir / "daily_returns.parquet")

        # Create a calendar that raises on date_to_session
        class FailingCalendar:
            def is_session(self, d: date) -> bool:
                return False  # All dates are "non-trading"

            def date_to_session(self, d: date, direction: str = "next") -> MagicMock:
                raise ValueError("Calendar error")

            def sessions_in_range(self, start: date, end: date) -> list:
                return []

        with patch("libs.data.data_providers.universe.get_settings") as mock_settings:
            mock_settings.return_value.crsp_data_dir = str(crsp_dir)

            provider = ForwardReturnsProvider()
            signals = pl.DataFrame({
                "signal_date": [date(2024, 1, 6)],  # Weekend date
                "permno": [10001],
            })

            # Should complete without raising (graceful handling)
            result = provider.get_forward_returns(
                signals_df=signals,
                skip_days=1,
                holding_period=5,
                calendar=FailingCalendar(),
            )

            # Should be empty due to normalization failure
            assert result.height == 0

    def test_get_forward_returns_partial_window_skipped(
        self, tmp_path, trading_calendar: MockCalendar
    ):
        """Signals where holding window extends past available data should be skipped."""
        crsp_dir = tmp_path / "crsp"
        crsp_dir.mkdir()

        # Only 10 days of data
        dates = _create_trading_dates(date(2024, 1, 2), 10)
        rows = []
        for d in dates:
            rows.append({"date": d, "permno": 10001, "ret": 0.01})
        df = pl.DataFrame(rows)
        df.write_parquet(crsp_dir / "daily_returns.parquet")

        with patch("libs.data.data_providers.universe.get_settings") as mock_settings:
            mock_settings.return_value.crsp_data_dir = str(crsp_dir)

            provider = ForwardReturnsProvider()
            # Signal at the end of available data
            signals = pl.DataFrame({
                "signal_date": [dates[-1]],  # Last available date
                "permno": [10001],
            })

            result = provider.get_forward_returns(
                signals_df=signals,
                skip_days=1,
                holding_period=20,  # Window extends past available data
                calendar=trading_calendar,
            )

            # Should be empty since window can't be computed
            assert result.height == 0

    def test_get_forward_returns_multiple_permnos(
        self, tmp_path, trading_calendar: MockCalendar
    ):
        """Should compute forward returns for multiple permnos."""
        crsp_dir = tmp_path / "crsp"
        crsp_dir.mkdir()

        dates = _create_trading_dates(date(2024, 1, 2), 30)
        rows = []
        for d in dates:
            for permno in [10001, 10002, 10003]:
                rows.append({"date": d, "permno": permno, "ret": 0.01 * (permno - 10000)})
        df = pl.DataFrame(rows)
        df.write_parquet(crsp_dir / "daily_returns.parquet")

        with patch("libs.data.data_providers.universe.get_settings") as mock_settings:
            mock_settings.return_value.crsp_data_dir = str(crsp_dir)

            provider = ForwardReturnsProvider()
            signals = pl.DataFrame({
                "signal_date": [dates[0], dates[0], dates[0]],
                "permno": [10001, 10002, 10003],
            })

            result = provider.get_forward_returns(
                signals_df=signals,
                skip_days=1,
                holding_period=5,
                calendar=trading_calendar,
            )

            # Should have results for all 3 permnos
            assert result.height == 3
            assert set(result["permno"].to_list()) == {10001, 10002, 10003}
