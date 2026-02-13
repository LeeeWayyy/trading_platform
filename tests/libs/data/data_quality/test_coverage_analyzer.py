"""Tests for coverage_analyzer.py (P6T13/T13.2).

Tests cover:
- CoverageAnalyzer.analyze() with fixture data
- Symbol derivation from filename (not Parquet column)
- Trading calendar: weekends excluded
- Status assignment: complete, missing, suspicious, no_expectation
- Resolution aggregation: daily, weekly, monthly
- Gap detection: contiguous missing trading days
- Export formats: CSV and JSON
- Edge cases: empty data, zero expected cells, symbol cap
- Per-file fault tolerance: corrupt files skipped
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

import polars as pl
import pytest

from libs.data.data_quality.coverage_analyzer import (
    MAX_SYMBOLS,
    CoverageAnalyzer,
    CoverageMatrix,
    CoverageStatus,
    _aggregate_monthly,
    _aggregate_status,
    _aggregate_weekly,
    _coerce_date,
    _find_gaps,
)

# ============================================================================
# Fixtures
# ============================================================================


def _create_parquet(
    path: Path,
    dates: list[str],
    close_values: list[float],
) -> None:
    """Create a minimal Parquet file with OHLCV data."""
    path.parent.mkdir(parents=True, exist_ok=True)
    df = pl.DataFrame(
        {
            "date": [datetime.date.fromisoformat(d) for d in dates],
            "open": close_values,
            "high": [v + 1 for v in close_values],
            "low": [v - 1 for v in close_values],
            "close": close_values,
            "volume": [1000] * len(dates),
        }
    )
    df.write_parquet(str(path))


@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    """Create test data with adjusted and quarantine directories."""
    adjusted = tmp_path / "adjusted"

    # 2024-01-15 (Monday): AAPL and MSFT
    _create_parquet(
        adjusted / "2024-01-15" / "AAPL.parquet",
        ["2024-01-10", "2024-01-11", "2024-01-12"],
        [150.0, 151.0, 152.0],
    )
    _create_parquet(
        adjusted / "2024-01-15" / "MSFT.parquet",
        ["2024-01-10", "2024-01-11", "2024-01-12"],
        [350.0, 351.0, 352.0],
    )

    # 2024-01-22 (Monday): AAPL only (MSFT missing = gap)
    _create_parquet(
        adjusted / "2024-01-22" / "AAPL.parquet",
        ["2024-01-16", "2024-01-17", "2024-01-18", "2024-01-19"],
        [153.0, 154.0, 155.0, 156.0],
    )

    # Quarantine: GOOG with issues
    quarantine = tmp_path / "quarantine"
    _create_parquet(
        quarantine / "2024-01-15" / "GOOG.parquet",
        ["2024-01-10", "2024-01-11"],
        [100.0, 101.0],
    )

    return tmp_path


@pytest.fixture()
def analyzer(data_dir: Path) -> CoverageAnalyzer:
    return CoverageAnalyzer(data_dir=data_dir)


# ============================================================================
# get_available_tickers
# ============================================================================


class TestGetAvailableTickers:
    def test_scans_all_partitions(self, analyzer: CoverageAnalyzer) -> None:
        tickers = analyzer.get_available_tickers()
        assert "AAPL" in tickers
        assert "MSFT" in tickers

    def test_sorted(self, analyzer: CoverageAnalyzer) -> None:
        tickers = analyzer.get_available_tickers()
        assert tickers == sorted(tickers)

    def test_empty_directory(self, tmp_path: Path) -> None:
        a = CoverageAnalyzer(data_dir=tmp_path)
        assert a.get_available_tickers() == []


# ============================================================================
# _aggregate_status
# ============================================================================


class TestAggregateStatus:
    def test_missing_wins(self) -> None:
        statuses = [
            CoverageStatus.COMPLETE,
            CoverageStatus.MISSING,
            CoverageStatus.SUSPICIOUS,
        ]
        assert _aggregate_status(statuses) == CoverageStatus.MISSING

    def test_suspicious_over_complete(self) -> None:
        statuses = [CoverageStatus.COMPLETE, CoverageStatus.SUSPICIOUS]
        assert _aggregate_status(statuses) == CoverageStatus.SUSPICIOUS

    def test_all_complete(self) -> None:
        statuses = [CoverageStatus.COMPLETE, CoverageStatus.COMPLETE]
        assert _aggregate_status(statuses) == CoverageStatus.COMPLETE

    def test_all_no_expectation(self) -> None:
        statuses = [
            CoverageStatus.NO_EXPECTATION,
            CoverageStatus.NO_EXPECTATION,
        ]
        assert _aggregate_status(statuses) == CoverageStatus.NO_EXPECTATION

    def test_empty(self) -> None:
        assert _aggregate_status([]) == CoverageStatus.NO_EXPECTATION


# ============================================================================
# analyze: basic
# ============================================================================


class TestAnalyzeBasic:
    def test_returns_matrix(self, analyzer: CoverageAnalyzer) -> None:
        result = analyzer.analyze(
            symbols=["AAPL"],
            start_date=datetime.date(2024, 1, 10),
            end_date=datetime.date(2024, 1, 12),
            resolution="daily",
        )
        assert isinstance(result, CoverageMatrix)
        assert result.symbols == ["AAPL"]
        assert len(result.dates) > 0
        assert len(result.matrix) == 1

    def test_complete_cells(self, analyzer: CoverageAnalyzer) -> None:
        """AAPL has data for Jan 10-12, all are trading days (Wed-Fri)."""
        result = analyzer.analyze(
            symbols=["AAPL"],
            start_date=datetime.date(2024, 1, 10),
            end_date=datetime.date(2024, 1, 12),
            resolution="daily",
        )
        # Filter to trading days only
        trading_statuses = [
            s for s in result.matrix[0] if s != CoverageStatus.NO_EXPECTATION
        ]
        assert all(s == CoverageStatus.COMPLETE for s in trading_statuses)

    def test_missing_cells(self, analyzer: CoverageAnalyzer) -> None:
        """MSFT has no data after Jan 12 — Jan 16-19 are missing."""
        result = analyzer.analyze(
            symbols=["MSFT"],
            start_date=datetime.date(2024, 1, 10),
            end_date=datetime.date(2024, 1, 19),
            resolution="daily",
        )
        has_missing = any(
            s == CoverageStatus.MISSING for s in result.matrix[0]
        )
        assert has_missing

    def test_weekend_no_expectation(self, analyzer: CoverageAnalyzer) -> None:
        """Jan 13-14 (Sat-Sun) should be NO_EXPECTATION."""
        result = analyzer.analyze(
            symbols=["AAPL"],
            start_date=datetime.date(2024, 1, 13),
            end_date=datetime.date(2024, 1, 14),
            resolution="daily",
        )
        assert all(
            s == CoverageStatus.NO_EXPECTATION for s in result.matrix[0]
        )

    def test_suspicious_from_quarantine(self, data_dir: Path) -> None:
        """GOOG in quarantine should be SUSPICIOUS."""
        # Add GOOG to adjusted too so it appears in symbols
        _create_parquet(
            data_dir / "adjusted" / "2024-01-15" / "GOOG.parquet",
            ["2024-01-10"],
            [99.0],
        )
        a = CoverageAnalyzer(data_dir=data_dir)
        result = a.analyze(
            symbols=["GOOG"],
            start_date=datetime.date(2024, 1, 10),
            end_date=datetime.date(2024, 1, 11),
            resolution="daily",
        )
        has_suspicious = any(
            s == CoverageStatus.SUSPICIOUS for s in result.matrix[0]
        )
        assert has_suspicious


# ============================================================================
# analyze: symbol derivation
# ============================================================================


class TestSymbolDerivation:
    def test_symbol_from_filename(self, analyzer: CoverageAnalyzer) -> None:
        """Symbols are derived from .parquet filenames, not column data."""
        result = analyzer.analyze(
            symbols=["AAPL"],
            start_date=datetime.date(2024, 1, 10),
            end_date=datetime.date(2024, 1, 12),
        )
        assert result.symbols == ["AAPL"]
        assert result.summary.total_present > 0


# ============================================================================
# analyze: empty / edge cases
# ============================================================================


class TestEdgeCases:
    def test_empty_data_dir(self, tmp_path: Path) -> None:
        a = CoverageAnalyzer(data_dir=tmp_path)
        result = a.analyze()
        assert result.symbols == []
        assert result.summary.total_expected == 0
        assert result.summary.coverage_pct == 0.0

    def test_no_adjusted_dir(self, tmp_path: Path) -> None:
        a = CoverageAnalyzer(data_dir=tmp_path / "nonexistent")
        result = a.analyze()
        assert result.symbols == []

    def test_inverted_date_range_raises(self, tmp_path: Path) -> None:
        """start_date > end_date raises ValueError."""
        adjusted = tmp_path / "adjusted" / "2024-01-15"
        adjusted.mkdir(parents=True)
        _create_parquet(adjusted / "TEST.parquet", ["2024-01-15"], [100.0])
        a = CoverageAnalyzer(data_dir=tmp_path)
        with pytest.raises(ValueError, match="start_date.*<=.*end_date"):
            a.analyze(
                start_date=datetime.date(2024, 2, 1),
                end_date=datetime.date(2024, 1, 1),
            )

    def test_zero_expected_no_division_error(self, tmp_path: Path) -> None:
        """With no trading days in range, coverage_pct = 0.0 (no ZeroDivisionError)."""
        adjusted = tmp_path / "adjusted" / "2024-01-13"
        adjusted.mkdir(parents=True)
        # Create file with Saturday-only data
        _create_parquet(
            adjusted / "TEST.parquet",
            ["2024-01-13"],
            [100.0],
        )
        a = CoverageAnalyzer(data_dir=tmp_path)
        result = a.analyze(
            symbols=["TEST"],
            start_date=datetime.date(2024, 1, 13),
            end_date=datetime.date(2024, 1, 14),
            resolution="daily",
        )
        # Both days are weekend, so total_expected = 0
        assert result.summary.total_expected == 0
        assert result.summary.coverage_pct == 0.0


# ============================================================================
# analyze: symbol cap
# ============================================================================


class TestSymbolCap:
    def test_cap_at_200(self, tmp_path: Path) -> None:
        """More than 200 symbols should be truncated."""
        adjusted = tmp_path / "adjusted" / "2024-01-15"
        adjusted.mkdir(parents=True)
        for i in range(210):
            name = f"SYM{i:04d}"
            _create_parquet(
                adjusted / f"{name}.parquet",
                ["2024-01-15"],
                [100.0],
            )
        a = CoverageAnalyzer(data_dir=tmp_path)
        result = a.analyze(
            start_date=datetime.date(2024, 1, 15),
            end_date=datetime.date(2024, 1, 15),
        )
        assert len(result.symbols) == MAX_SYMBOLS
        assert result.truncated is True
        assert result.total_symbol_count == 210
        assert any("200" in n for n in result.notices)

    def test_no_truncation_under_200(self, analyzer: CoverageAnalyzer) -> None:
        result = analyzer.analyze(
            start_date=datetime.date(2024, 1, 10),
            end_date=datetime.date(2024, 1, 12),
        )
        assert result.truncated is False


# ============================================================================
# Symbol Validation (path traversal prevention)
# ============================================================================


class TestSymbolValidation:
    def test_path_traversal_symbols_rejected(self, tmp_path: Path) -> None:
        """Crafted symbol names with path separators are silently rejected."""
        adjusted = tmp_path / "adjusted" / "2024-01-15"
        adjusted.mkdir(parents=True)
        _create_parquet(adjusted / "AAPL.parquet", ["2024-01-15"], [100.0])
        a = CoverageAnalyzer(data_dir=tmp_path)
        result = a.analyze(
            symbols=["../../etc/passwd", "AAPL", "../secret"],
            start_date=datetime.date(2024, 1, 15),
            end_date=datetime.date(2024, 1, 15),
        )
        # Only valid AAPL should remain
        assert result.symbols == ["AAPL"]
        assert result.total_symbol_count == 1

    def test_symbols_with_dots_rejected(self, tmp_path: Path) -> None:
        """Symbols containing dots (e.g., 'BRK.B') are rejected by the regex."""
        adjusted = tmp_path / "adjusted" / "2024-01-15"
        adjusted.mkdir(parents=True)
        _create_parquet(adjusted / "AAPL.parquet", ["2024-01-15"], [100.0])
        a = CoverageAnalyzer(data_dir=tmp_path)
        result = a.analyze(
            symbols=["BRK.B", "AAPL"],
            start_date=datetime.date(2024, 1, 15),
            end_date=datetime.date(2024, 1, 15),
        )
        assert result.symbols == ["AAPL"]

    def test_empty_and_long_symbols_rejected(self, tmp_path: Path) -> None:
        adjusted = tmp_path / "adjusted" / "2024-01-15"
        adjusted.mkdir(parents=True)
        a = CoverageAnalyzer(data_dir=tmp_path)
        result = a.analyze(
            symbols=["", "A" * 11],
            start_date=datetime.date(2024, 1, 15),
            end_date=datetime.date(2024, 1, 15),
        )
        assert result.symbols == []


# ============================================================================
# _coerce_date (defensive date normalization)
# ============================================================================


class TestCoerceDate:
    def test_date_passthrough(self) -> None:
        d = datetime.date(2024, 1, 15)
        assert _coerce_date(d) == d

    def test_datetime_to_date(self) -> None:
        dt = datetime.datetime(2024, 1, 15, 12, 0, 0)
        assert _coerce_date(dt) == datetime.date(2024, 1, 15)

    def test_iso_string(self) -> None:
        assert _coerce_date("2024-01-15") == datetime.date(2024, 1, 15)

    def test_invalid_string(self) -> None:
        assert _coerce_date("not-a-date") is None

    def test_none_returns_none(self) -> None:
        assert _coerce_date(None) is None

    def test_integer_returns_none(self) -> None:
        assert _coerce_date(20240115) is None


# ============================================================================
# analyze: resolution
# ============================================================================


class TestResolution:
    def test_daily_resolution(self, analyzer: CoverageAnalyzer) -> None:
        result = analyzer.analyze(
            symbols=["AAPL"],
            start_date=datetime.date(2024, 1, 10),
            end_date=datetime.date(2024, 1, 12),
            resolution="daily",
        )
        assert result.effective_resolution == "daily"
        # 3 calendar days: Wed, Thu, Fri
        assert len(result.dates) == 3

    def test_weekly_resolution(self, analyzer: CoverageAnalyzer) -> None:
        result = analyzer.analyze(
            symbols=["AAPL"],
            start_date=datetime.date(2024, 1, 8),
            end_date=datetime.date(2024, 1, 21),
            resolution="weekly",
        )
        assert result.effective_resolution == "weekly"
        # 2 ISO weeks: Jan 8 (Mon) and Jan 15 (Mon)
        assert len(result.dates) == 2

    def test_monthly_resolution(self, analyzer: CoverageAnalyzer) -> None:
        result = analyzer.analyze(
            symbols=["AAPL"],
            start_date=datetime.date(2024, 1, 1),
            end_date=datetime.date(2024, 1, 31),
            resolution="monthly",
        )
        assert result.effective_resolution == "monthly"
        assert len(result.dates) == 1
        assert result.dates[0] == datetime.date(2024, 1, 1)

    def test_daily_auto_upgrade_to_weekly(self, tmp_path: Path) -> None:
        """Daily with range > 180 days auto-upgrades to weekly."""
        adjusted = tmp_path / "adjusted" / "2024-01-15"
        adjusted.mkdir(parents=True)
        _create_parquet(
            adjusted / "AAPL.parquet",
            ["2024-01-15"],
            [100.0],
        )
        a = CoverageAnalyzer(data_dir=tmp_path)
        result = a.analyze(
            symbols=["AAPL"],
            start_date=datetime.date(2024, 1, 1),
            end_date=datetime.date(2024, 12, 31),
            resolution="daily",
        )
        assert result.effective_resolution == "weekly"
        assert any("180" in n for n in result.notices)


# ============================================================================
# Gap detection
# ============================================================================


class TestGapDetection:
    def test_contiguous_gaps(self, analyzer: CoverageAnalyzer) -> None:
        """MSFT is missing Jan 16-19 (trading days only)."""
        result = analyzer.analyze(
            symbols=["MSFT"],
            start_date=datetime.date(2024, 1, 10),
            end_date=datetime.date(2024, 1, 19),
            resolution="daily",
        )
        msft_gaps = [g for g in result.summary.gaps if g.symbol == "MSFT"]
        assert len(msft_gaps) >= 1
        assert all(g.gap_days > 0 for g in msft_gaps)

    def test_no_gaps_when_complete(self, analyzer: CoverageAnalyzer) -> None:
        """AAPL Jan 10-12 (all complete) should have no gaps."""
        result = analyzer.analyze(
            symbols=["AAPL"],
            start_date=datetime.date(2024, 1, 10),
            end_date=datetime.date(2024, 1, 12),
            resolution="daily",
        )
        aapl_gaps = [g for g in result.summary.gaps if g.symbol == "AAPL"]
        assert len(aapl_gaps) == 0

    def test_gaps_sorted_by_days_desc(self, analyzer: CoverageAnalyzer) -> None:
        result = analyzer.analyze(
            start_date=datetime.date(2024, 1, 10),
            end_date=datetime.date(2024, 1, 19),
            resolution="daily",
        )
        if len(result.summary.gaps) >= 2:
            for i in range(len(result.summary.gaps) - 1):
                assert (
                    result.summary.gaps[i].gap_days
                    >= result.summary.gaps[i + 1].gap_days
                )

    def test_weekends_dont_break_gaps(self) -> None:
        """Non-trading days between missing trading days = one contiguous gap."""
        # Friday missing, Saturday/Sunday no_expectation, Monday missing = 1 gap of 2
        dates = [
            datetime.date(2024, 1, 12),  # Fri
            datetime.date(2024, 1, 13),  # Sat
            datetime.date(2024, 1, 14),  # Sun
            datetime.date(2024, 1, 15),  # Mon
        ]
        trading_set = {dates[0], dates[3]}  # Fri and Mon are trading days
        matrix = [
            [
                CoverageStatus.MISSING,        # Fri
                CoverageStatus.NO_EXPECTATION,  # Sat
                CoverageStatus.NO_EXPECTATION,  # Sun
                CoverageStatus.MISSING,         # Mon
            ]
        ]
        gaps = _find_gaps(["SYM"], dates, matrix, trading_set)
        assert len(gaps) == 1
        assert gaps[0].gap_days == 2

    def test_gap_end_date_is_trading_day(self) -> None:
        """Gap end_date must be a trading day, not a weekend preceding closure."""
        # Missing Fri, Sat+Sun no_expectation, then Mon is COMPLETE (gap closes)
        dates = [
            datetime.date(2024, 1, 12),  # Fri — missing
            datetime.date(2024, 1, 13),  # Sat
            datetime.date(2024, 1, 14),  # Sun
            datetime.date(2024, 1, 15),  # Mon — complete (closes gap)
        ]
        trading_set = {dates[0], dates[3]}
        matrix = [
            [
                CoverageStatus.MISSING,
                CoverageStatus.NO_EXPECTATION,
                CoverageStatus.NO_EXPECTATION,
                CoverageStatus.COMPLETE,
            ]
        ]
        gaps = _find_gaps(["SYM"], dates, matrix, trading_set)
        assert len(gaps) == 1
        # end_date must be Friday (the last missing trading day), not Sunday
        assert gaps[0].end_date == datetime.date(2024, 1, 12)
        assert gaps[0].end_date.weekday() == 4  # Friday


# ============================================================================
# Fault tolerance
# ============================================================================


class TestFaultTolerance:
    def test_corrupt_file_skipped(self, data_dir: Path) -> None:
        """Corrupt Parquet file should be skipped, not fatal."""
        corrupt_path = data_dir / "adjusted" / "2024-01-15" / "BAD.parquet"
        corrupt_path.write_text("this is not a parquet file")

        a = CoverageAnalyzer(data_dir=data_dir)
        result = a.analyze(
            symbols=["BAD"],
            start_date=datetime.date(2024, 1, 10),
            end_date=datetime.date(2024, 1, 15),
        )
        assert result.skipped_file_count >= 1
        assert any("could not be read" in n for n in result.notices)


# ============================================================================
# Export
# ============================================================================


class TestExport:
    def test_csv_format(self, analyzer: CoverageAnalyzer) -> None:
        matrix = analyzer.analyze(
            symbols=["AAPL"],
            start_date=datetime.date(2024, 1, 10),
            end_date=datetime.date(2024, 1, 12),
            resolution="daily",
        )
        csv_str = analyzer.export_coverage_report(matrix, fmt="csv")
        lines = csv_str.strip().split("\n")
        assert lines[0] == "symbol,date,status"
        assert len(lines) > 1
        # Each line has 3 fields
        for line in lines[1:]:
            parts = line.split(",")
            assert len(parts) == 3
            assert parts[0] == "AAPL"

    def test_json_format(self, analyzer: CoverageAnalyzer) -> None:
        matrix = analyzer.analyze(
            symbols=["AAPL"],
            start_date=datetime.date(2024, 1, 10),
            end_date=datetime.date(2024, 1, 12),
            resolution="daily",
        )
        json_str = analyzer.export_coverage_report(matrix, fmt="json")
        data = json.loads(json_str)
        assert "summary" in data
        assert "gaps" in data
        assert "resolution" in data
        assert data["summary"]["coverage_pct"] >= 0


# ============================================================================
# Weekly aggregation
# ============================================================================


class TestWeeklyAggregation:
    def test_week_with_no_trading_days(self) -> None:
        """A week entirely of NO_EXPECTATION stays NO_EXPECTATION."""
        dates = [
            datetime.date(2024, 1, 13),  # Sat
            datetime.date(2024, 1, 14),  # Sun
        ]
        matrix = [[CoverageStatus.NO_EXPECTATION, CoverageStatus.NO_EXPECTATION]]
        week_dates, week_matrix = _aggregate_weekly(dates, matrix)
        assert len(week_dates) == 1
        assert week_matrix[0][0] == CoverageStatus.NO_EXPECTATION

    def test_empty_dates(self) -> None:
        week_dates, week_matrix = _aggregate_weekly([], [[]])
        assert week_dates == []


# ============================================================================
# Monthly aggregation
# ============================================================================


class TestMonthlyAggregation:
    def test_cross_month_boundary(self) -> None:
        """Dates spanning Jan and Feb produce 2 monthly buckets."""
        dates = [
            datetime.date(2024, 1, 31),
            datetime.date(2024, 2, 1),
        ]
        matrix = [[CoverageStatus.COMPLETE, CoverageStatus.MISSING]]
        month_dates, month_matrix = _aggregate_monthly(dates, matrix)
        assert len(month_dates) == 2
        assert month_dates[0] == datetime.date(2024, 1, 1)
        assert month_dates[1] == datetime.date(2024, 2, 1)

    def test_empty_dates(self) -> None:
        month_dates, month_matrix = _aggregate_monthly([], [[]])
        assert month_dates == []


# ============================================================================
# Summary computation
# ============================================================================


class TestSummary:
    def test_coverage_percentage(self, analyzer: CoverageAnalyzer) -> None:
        """Coverage % counts only trading day cells."""
        result = analyzer.analyze(
            symbols=["AAPL"],
            start_date=datetime.date(2024, 1, 10),
            end_date=datetime.date(2024, 1, 12),
            resolution="daily",
        )
        # Jan 10 (Wed), 11 (Thu), 12 (Fri) — 3 trading days, all complete
        assert result.summary.total_expected == 3
        assert result.summary.total_present == 3
        assert result.summary.coverage_pct == 100.0

    def test_mixed_coverage(self, analyzer: CoverageAnalyzer) -> None:
        """MSFT has 3 complete + some missing days in Jan 10-19 range."""
        result = analyzer.analyze(
            symbols=["MSFT"],
            start_date=datetime.date(2024, 1, 10),
            end_date=datetime.date(2024, 1, 19),
            resolution="daily",
        )
        assert result.summary.total_present > 0
        assert result.summary.total_missing > 0
        assert 0 < result.summary.coverage_pct < 100
