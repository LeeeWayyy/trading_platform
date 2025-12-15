"""Tests for libs.data_quality.validation module."""

from __future__ import annotations

import datetime
import tempfile
from pathlib import Path

import polars as pl
import pytest

from libs.data_quality.validation import AnomalyAlert, DataValidator, ValidationError


class MockTradingCalendar:
    """Mock trading calendar for testing."""

    def __init__(self, holidays: list[datetime.date] | None = None):
        self.holidays = set(holidays or [])

    def is_trading_day(self, date: datetime.date) -> bool:
        if date.weekday() >= 5:  # Weekend
            return False
        return date not in self.holidays

    def trading_days_between(self, start: datetime.date, end: datetime.date) -> list[datetime.date]:
        days = []
        current = start
        while current <= end:
            if self.is_trading_day(current):
                days.append(current)
            current += datetime.timedelta(days=1)
        return days


class TestValidationError:
    """Tests for ValidationError dataclass."""

    def test_error_creation(self) -> None:
        """Test ValidationError creation."""
        error = ValidationError(
            field="test_field",
            message="Test message",
            severity="error",
            value=42,
        )

        assert error.field == "test_field"
        assert error.message == "Test message"
        assert error.severity == "error"
        assert error.value == 42


class TestAnomalyAlert:
    """Tests for AnomalyAlert dataclass."""

    def test_alert_creation(self) -> None:
        """Test AnomalyAlert creation."""
        alert = AnomalyAlert(
            metric="row_count",
            current_value=100.0,
            expected_value=200.0,
            deviation_pct=50.0,
            message="Row count dropped",
        )

        assert alert.metric == "row_count"
        assert alert.current_value == 100.0
        assert alert.expected_value == 200.0
        assert alert.deviation_pct == 50.0


class TestDataValidator:
    """Tests for DataValidator class."""

    @pytest.fixture()
    def validator(self) -> DataValidator:
        """Create DataValidator instance."""
        return DataValidator()

    # Row count validation tests

    def test_row_count_within_tolerance_passes(self, validator: DataValidator) -> None:
        """Test row count validation passes within tolerance."""
        df = pl.DataFrame({"a": range(100)})

        errors = validator.validate_row_count(df, expected=100, tolerance=0.05)

        assert len(errors) == 0

    def test_row_count_at_tolerance_boundary_passes(self, validator: DataValidator) -> None:
        """Test row count at exactly tolerance boundary passes."""
        df = pl.DataFrame({"a": range(95)})  # 5% below 100

        errors = validator.validate_row_count(df, expected=100, tolerance=0.05)

        assert len(errors) == 0

    def test_row_count_exceeds_tolerance_fails(self, validator: DataValidator) -> None:
        """Test row count validation fails when exceeding tolerance."""
        df = pl.DataFrame({"a": range(80)})  # 20% below 100

        errors = validator.validate_row_count(df, expected=100, tolerance=0.05)

        assert len(errors) == 1
        assert errors[0].severity == "error"
        assert "deviation" in errors[0].message.lower()

    # Null percentage tests

    def test_null_percentage_below_threshold_passes(self, validator: DataValidator) -> None:
        """Test null percentage detection passes below threshold."""
        df = pl.DataFrame(
            {
                "a": [1, 2, 3, None, 5],  # 20% null
            }
        )

        errors = validator.validate_null_percentage(df, {"a": 0.25})

        assert len(errors) == 0

    def test_null_percentage_above_threshold_fails(self, validator: DataValidator) -> None:
        """Test null percentage detection fails above threshold."""
        df = pl.DataFrame(
            {
                "a": [1, None, None, None, 5],  # 60% null
            }
        )

        errors = validator.validate_null_percentage(df, {"a": 0.10})

        assert len(errors) == 1
        assert errors[0].field == "a"
        assert errors[0].severity == "error"

    # Checksum tests

    def test_sha256_checksum_computation(self, validator: DataValidator) -> None:
        """Test SHA-256 checksum computation."""
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"test content for checksum")
            f.flush()
            path = Path(f.name)

        try:
            checksum = validator.compute_checksum(path)

            assert len(checksum) == 64  # SHA-256 hex length
            assert checksum.isalnum()
        finally:
            path.unlink()

    def test_sha256_checksum_verification_success(self, validator: DataValidator) -> None:
        """Test SHA-256 checksum verification succeeds."""
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"test content")
            f.flush()
            path = Path(f.name)

        try:
            checksum = validator.compute_checksum(path)
            result = validator.verify_checksum(path, checksum)

            assert result is True
        finally:
            path.unlink()

    def test_sha256_checksum_verification_failure(self, validator: DataValidator) -> None:
        """Test SHA-256 checksum verification fails on mismatch."""
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"test content")
            f.flush()
            path = Path(f.name)

        try:
            result = validator.verify_checksum(path, "wrong_checksum")

            assert result is False
        finally:
            path.unlink()

    def test_aggregate_checksum_multiple_files(self, validator: DataValidator) -> None:
        """Test aggregate checksum computation for multiple files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = []
            for i in range(3):
                path = Path(tmpdir) / f"file{i}.txt"
                path.write_text(f"content {i}")
                paths.append(path)

            checksum = validator.compute_aggregate_checksum(paths)

            assert len(checksum) == 64
            assert checksum.isalnum()

    def test_aggregate_checksum_deterministic_ordering(self, validator: DataValidator) -> None:
        """Test aggregate checksum is deterministic regardless of input order."""
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = []
            for i in range(3):
                path = Path(tmpdir) / f"file{i}.txt"
                path.write_text(f"content {i}")
                paths.append(path)

            # Different input orders should produce same checksum
            checksum1 = validator.compute_aggregate_checksum(paths)
            checksum2 = validator.compute_aggregate_checksum(list(reversed(paths)))

            assert checksum1 == checksum2

    def test_aggregate_checksum_verification_success(self, validator: DataValidator) -> None:
        """Test aggregate checksum verification succeeds."""
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = []
            for i in range(2):
                path = Path(tmpdir) / f"file{i}.txt"
                path.write_text(f"content {i}")
                paths.append(path)

            checksum = validator.compute_aggregate_checksum(paths)
            result = validator.verify_aggregate_checksum(paths, checksum)

            assert result is True

    def test_aggregate_checksum_verification_failure_on_change(
        self, validator: DataValidator
    ) -> None:
        """Test aggregate checksum verification fails when one file changes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = []
            for i in range(2):
                path = Path(tmpdir) / f"file{i}.txt"
                path.write_text(f"content {i}")
                paths.append(path)

            checksum = validator.compute_aggregate_checksum(paths)

            # Modify one file
            paths[0].write_text("modified content")

            result = validator.verify_aggregate_checksum(paths, checksum)

            assert result is False

    # Schema validation tests

    def test_schema_validation_matches_expected(self, validator: DataValidator) -> None:
        """Test schema validation passes when schema matches."""
        df = pl.DataFrame(
            {
                "int_col": [1, 2, 3],
                "str_col": ["a", "b", "c"],
            }
        ).cast({"int_col": pl.Int64, "str_col": pl.Utf8})

        errors = validator.validate_schema(
            df,
            {
                "int_col": "int64",
                "str_col": "str",
            },
        )

        # Filter to only errors (not warnings)
        schema_errors = [e for e in errors if e.severity == "error"]
        assert len(schema_errors) == 0

    def test_schema_validation_missing_columns(self, validator: DataValidator) -> None:
        """Test schema validation detects missing columns."""
        df = pl.DataFrame({"a": [1, 2, 3]})

        errors = validator.validate_schema(
            df,
            {
                "a": "int64",
                "b": "str",  # Missing
            },
        )

        assert len(errors) >= 1
        assert any("Missing column" in e.message for e in errors)

    def test_schema_validation_extra_columns_warning(self, validator: DataValidator) -> None:
        """Test schema validation warns on extra columns."""
        df = pl.DataFrame(
            {
                "a": [1, 2, 3],
                "b": ["x", "y", "z"],  # Extra
            }
        )

        errors = validator.validate_schema(df, {"a": "int64"})

        assert any(e.severity == "warning" for e in errors)
        assert any("Unexpected column" in e.message for e in errors)

    def test_schema_validation_dtype_mapping(self, validator: DataValidator) -> None:
        """Test schema validation maps string dtype to polars type."""
        df = pl.DataFrame(
            {
                "col": [1, 2, 3],
            }
        ).cast({"col": pl.Int64})

        # Test various aliases
        for dtype_str in ["int64", "Int64", "INT64"]:
            errors = validator.validate_schema(df, {"col": dtype_str})
            schema_errors = [e for e in errors if e.severity == "error"]
            assert len(schema_errors) == 0

    def test_schema_validation_unknown_dtype_error(self, validator: DataValidator) -> None:
        """Test schema validation raises error for unknown dtype."""
        df = pl.DataFrame({"col": [1, 2, 3]})

        errors = validator.validate_schema(df, {"col": "unknown_type"})

        assert any("Unknown dtype" in e.message for e in errors)

    # Date continuity tests

    def test_date_continuity_no_gaps_passes(self, validator: DataValidator) -> None:
        """Test date continuity passes with no gaps."""
        dates = [
            datetime.date(2024, 1, 1),  # Monday
            datetime.date(2024, 1, 2),  # Tuesday
            datetime.date(2024, 1, 3),  # Wednesday
            datetime.date(2024, 1, 4),  # Thursday
            datetime.date(2024, 1, 5),  # Friday
        ]
        df = pl.DataFrame({"date": dates})

        errors = validator.validate_date_continuity(df, "date")

        assert len(errors) == 0

    def test_date_continuity_with_gaps_fails(self, validator: DataValidator) -> None:
        """Test date continuity fails with gaps."""
        dates = [
            datetime.date(2024, 1, 1),  # Monday
            datetime.date(2024, 1, 2),  # Tuesday
            # Missing Wednesday
            datetime.date(2024, 1, 4),  # Thursday
            datetime.date(2024, 1, 5),  # Friday
        ]
        df = pl.DataFrame({"date": dates})

        errors = validator.validate_date_continuity(df, "date")

        assert len(errors) == 1
        assert "Missing" in errors[0].message

    def test_date_continuity_excludes_holidays(self, validator: DataValidator) -> None:
        """Test date continuity excludes holidays from gap detection."""
        dates = [
            datetime.date(2024, 1, 1),  # Monday
            datetime.date(2024, 1, 2),  # Tuesday - Holiday
            # Jan 3 is holiday
            datetime.date(2024, 1, 4),  # Thursday
            datetime.date(2024, 1, 5),  # Friday
        ]
        df = pl.DataFrame({"date": dates})

        calendar = MockTradingCalendar(holidays=[datetime.date(2024, 1, 3)])

        errors = validator.validate_date_continuity(df, "date", calendar=calendar)

        # Should pass - Jan 3 is a holiday
        assert len(errors) == 0

    def test_date_continuity_handles_datetime_column(self, validator: DataValidator) -> None:
        """Test date continuity correctly normalizes datetime columns to date."""
        # Use datetime values instead of date values
        datetimes = [
            datetime.datetime(2024, 1, 1, 10, 30, 0),  # Monday
            datetime.datetime(2024, 1, 2, 14, 0, 0),  # Tuesday
            datetime.datetime(2024, 1, 3, 9, 15, 0),  # Wednesday
            datetime.datetime(2024, 1, 4, 11, 45, 0),  # Thursday
            datetime.datetime(2024, 1, 5, 16, 0, 0),  # Friday
        ]
        df = pl.DataFrame({"timestamp": datetimes})

        errors = validator.validate_date_continuity(df, "timestamp")

        # Should pass - all weekdays present despite different times
        assert len(errors) == 0

    def test_date_continuity_handles_datetime_with_gaps(self, validator: DataValidator) -> None:
        """Test date continuity detects gaps in datetime columns."""
        # Use datetime values with a gap on Wednesday
        datetimes = [
            datetime.datetime(2024, 1, 1, 10, 30, 0),  # Monday
            datetime.datetime(2024, 1, 2, 14, 0, 0),  # Tuesday
            # Missing Wednesday
            datetime.datetime(2024, 1, 4, 11, 45, 0),  # Thursday
            datetime.datetime(2024, 1, 5, 16, 0, 0),  # Friday
        ]
        df = pl.DataFrame({"timestamp": datetimes})

        errors = validator.validate_date_continuity(df, "timestamp")

        # Should fail - Wednesday is missing
        assert len(errors) == 1
        assert "Missing" in errors[0].message

    # Anomaly detection tests

    def test_anomaly_detection_row_count_drop(self, validator: DataValidator) -> None:
        """Test anomaly detection catches row count drop > 10%."""
        current = {"row_count": 80}
        previous = {"row_count": 100}  # 20% drop

        alerts = validator.detect_anomalies(current, previous)

        assert len(alerts) >= 1
        assert any(a.metric == "row_count" for a in alerts)

    def test_anomaly_detection_null_spike(self, validator: DataValidator) -> None:
        """Test anomaly detection catches null spike > 5%."""
        current = {
            "row_count": 100,
            "null_percentages": {"col_a": 0.15},  # 15% null
        }
        previous = {
            "row_count": 100,
            "null_percentages": {"col_a": 0.05},  # 5% null -> 10% increase
        }

        alerts = validator.detect_anomalies(current, previous)

        assert len(alerts) >= 1
        assert any("null" in a.metric.lower() for a in alerts)

    def test_anomaly_detection_missing_date_ranges(self, validator: DataValidator) -> None:
        """Test anomaly detection catches missing date ranges."""
        current = {
            "row_count": 100,
            "date_range": {
                "start": "2024-01-10",  # Gap of 5 days
                "end": "2024-01-15",
            },
        }
        previous = {
            "row_count": 100,
            "date_range": {
                "start": "2024-01-01",
                "end": "2024-01-04",  # Previous ended Jan 4
            },
        }

        alerts = validator.detect_anomalies(current, previous)

        assert len(alerts) >= 1
        assert any(a.metric == "date_gap" for a in alerts)

    def test_anomaly_detection_combined_anomalies(self, validator: DataValidator) -> None:
        """Test anomaly detection catches multiple issues together."""
        current = {
            "row_count": 50,  # 50% drop
            "null_percentages": {"col_a": 0.20},  # Spike
        }
        previous = {
            "row_count": 100,
            "null_percentages": {"col_a": 0.05},
        }

        alerts = validator.detect_anomalies(current, previous)

        # Should have at least 2 alerts
        assert len(alerts) >= 2
        metrics = [a.metric for a in alerts]
        assert "row_count" in metrics
        assert any("null" in m for m in metrics)

    def test_anomaly_detection_first_sync_no_alerts(self, validator: DataValidator) -> None:
        """Test anomaly detection returns empty for first sync (no previous)."""
        current = {"row_count": 100}

        alerts = validator.detect_anomalies(current, None)

        assert len(alerts) == 0
