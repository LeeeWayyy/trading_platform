"""
Data validation for WRDS syncs.

This module provides:
- ValidationError: Structured validation error
- AnomalyAlert: Alert for anomalous data patterns
- DataValidator: Validates data quality
"""

from __future__ import annotations

import datetime
import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import polars as pl

if TYPE_CHECKING:
    from libs.data.data_quality.types import TradingCalendar

logger = logging.getLogger(__name__)


@dataclass
class ValidationError:
    """Structured validation error.

    Attributes:
        field: Field or column that failed validation.
        message: Description of the validation failure.
        severity: Error severity - "error" blocks operation, "warning" is logged.
        value: Optional value that caused the failure.
    """

    field: str
    message: str
    severity: Literal["error", "warning"]
    value: Any = None


@dataclass
class AnomalyAlert:
    """Alert for anomalous data patterns.

    Attributes:
        metric: Name of the metric that triggered the alert.
        current_value: Current observed value.
        expected_value: Expected or historical value.
        deviation_pct: Percentage deviation from expected.
        message: Human-readable alert message.
    """

    metric: str
    current_value: float
    expected_value: float
    deviation_pct: float
    message: str


class DataValidator:
    """Validates data quality for WRDS syncs.

    This validator provides methods for:
    - Row count validation
    - Null percentage detection
    - Schema validation
    - Date continuity checks
    - Checksum computation and verification
    - Anomaly detection
    """

    # Dtype mapping constant for schema validation
    # Maps lowercase string aliases to actual polars DataType objects
    # Using ClassVar for class-level dict with type alias to avoid mypy issues
    DTYPE_MAP: dict[str, pl.DataType] = {
        "int64": pl.Int64(),
        "int32": pl.Int32(),
        "int16": pl.Int16(),
        "int8": pl.Int8(),
        "float64": pl.Float64(),
        "float32": pl.Float32(),
        "str": pl.Utf8(),
        "utf8": pl.Utf8(),
        "string": pl.Utf8(),
        "bool": pl.Boolean(),
        "boolean": pl.Boolean(),
        "date": pl.Date(),
        "datetime": pl.Datetime("us"),  # Default microseconds
        "datetime[us]": pl.Datetime("us"),  # Explicit microseconds
        "datetime[ns]": pl.Datetime("ns"),  # Explicit nanoseconds
        "datetime[ms]": pl.Datetime("ms"),  # Explicit milliseconds
    }

    # Anomaly detection thresholds
    ROW_DROP_THRESHOLD = 0.10  # >10% row drop is anomalous
    NULL_SPIKE_THRESHOLD = 0.05  # >5% null increase is anomalous

    def validate_row_count(
        self,
        df: pl.DataFrame,
        expected: int,
        tolerance: float = 0.05,
    ) -> list[ValidationError]:
        """Validate row count within tolerance.

        Args:
            df: DataFrame to validate.
            expected: Expected row count.
            tolerance: Allowed deviation (0.05 = 5%).

        Returns:
            List of ValidationError if outside tolerance, empty otherwise.
        """
        errors: list[ValidationError] = []
        actual = len(df)

        if expected == 0:
            if actual != 0:
                errors.append(
                    ValidationError(
                        field="row_count",
                        message=f"Expected 0 rows, got {actual}",
                        severity="error",
                        value=actual,
                    )
                )
            return errors

        deviation = abs(actual - expected) / expected
        if deviation > tolerance:
            errors.append(
                ValidationError(
                    field="row_count",
                    message=(
                        f"Row count deviation {deviation:.1%} exceeds tolerance {tolerance:.1%}: "
                        f"expected {expected}, got {actual}"
                    ),
                    severity="error",
                    value=actual,
                )
            )

        return errors

    def validate_null_percentage(
        self,
        df: pl.DataFrame,
        columns: dict[str, float],
    ) -> list[ValidationError]:
        """Validate null percentages per column.

        Args:
            df: DataFrame to validate.
            columns: Dict mapping column name to max allowed null percentage.

        Returns:
            List of ValidationError for columns exceeding threshold.
        """
        errors: list[ValidationError] = []
        total_rows = len(df)

        if total_rows == 0:
            return errors

        for col, max_pct in columns.items():
            if col not in df.columns:
                errors.append(
                    ValidationError(
                        field=col,
                        message=f"Column '{col}' not found in DataFrame",
                        severity="error",
                    )
                )
                continue

            null_count = df[col].null_count()
            null_pct = null_count / total_rows

            if null_pct > max_pct:
                errors.append(
                    ValidationError(
                        field=col,
                        message=(
                            f"Null percentage {null_pct:.1%} exceeds threshold {max_pct:.1%}: "
                            f"{null_count} nulls out of {total_rows} rows"
                        ),
                        severity="error",
                        value=null_pct,
                    )
                )

        return errors

    def validate_schema(
        self,
        df: pl.DataFrame,
        expected_schema: dict[str, str],
    ) -> list[ValidationError]:
        """Validate DataFrame schema matches expected.

        Args:
            df: DataFrame to validate.
            expected_schema: Dict mapping column name to dtype string.

        Returns:
            List of ValidationError for schema mismatches.
        """
        errors: list[ValidationError] = []
        actual_schema = {col: df[col].dtype for col in df.columns}

        # Check for missing columns
        for col, dtype_str in expected_schema.items():
            if col not in actual_schema:
                errors.append(
                    ValidationError(
                        field=col,
                        message=f"Missing column '{col}' (expected type: {dtype_str})",
                        severity="error",
                    )
                )
                continue

            # Map dtype string to polars type
            dtype_key = dtype_str.lower()
            expected_dtype = self.DTYPE_MAP.get(dtype_key)

            if expected_dtype is None:
                errors.append(
                    ValidationError(
                        field=col,
                        message=f"Unknown dtype '{dtype_str}' for column '{col}'",
                        severity="error",
                    )
                )
                continue

            # Compare types
            actual_dtype = actual_schema[col]

            # Special handling for datetime types - accept any precision
            # when expected is generic "datetime" (not explicit datetime[us/ns/ms])
            types_match = actual_dtype == expected_dtype
            if not types_match and dtype_key == "datetime":
                # Accept any datetime precision (us, ns, ms) for generic "datetime"
                types_match = isinstance(actual_dtype, pl.Datetime)

            if not types_match:
                errors.append(
                    ValidationError(
                        field=col,
                        message=(
                            f"Type mismatch for column '{col}': "
                            f"expected {expected_dtype}, got {actual_dtype}"
                        ),
                        severity="error",
                        value=str(actual_dtype),
                    )
                )

        # Check for extra columns (warning only)
        for col in actual_schema:
            if col not in expected_schema:
                errors.append(
                    ValidationError(
                        field=col,
                        message=f"Unexpected column '{col}' (type: {actual_schema[col]})",
                        severity="warning",
                    )
                )

        return errors

    def validate_date_continuity(
        self,
        df: pl.DataFrame,
        date_col: str,
        calendar: TradingCalendar | None = None,
    ) -> list[ValidationError]:
        """Validate date continuity (no unexpected gaps).

        Args:
            df: DataFrame to validate.
            date_col: Name of the date column.
            calendar: Optional trading calendar to exclude holidays.

        Returns:
            List of ValidationError for unexpected gaps.
        """
        errors: list[ValidationError] = []

        if date_col not in df.columns:
            errors.append(
                ValidationError(
                    field=date_col,
                    message=f"Date column '{date_col}' not found",
                    severity="error",
                )
            )
            return errors

        # Get unique sorted dates, normalizing datetime to date
        col_dtype = df[date_col].dtype
        if col_dtype == pl.Datetime or str(col_dtype).startswith("Datetime"):
            # Cast datetime to date for comparison
            date_series = df[date_col].cast(pl.Date)
        else:
            date_series = df[date_col]

        raw_dates = date_series.unique().sort().to_list()

        # Normalize to datetime.date objects for consistent comparison
        dates: list[datetime.date] = []
        for d in raw_dates:
            if d is None:
                continue
            if isinstance(d, datetime.datetime):
                dates.append(d.date())
            elif isinstance(d, datetime.date):
                dates.append(d)
            # Skip other types

        if len(dates) < 2:
            return errors

        # Build expected dates set
        expected_dates: set[datetime.date]
        if calendar:
            expected_dates = set(calendar.trading_days_between(dates[0], dates[-1]))
        else:
            # Without calendar, expect all weekdays
            expected_dates = set()
            current = dates[0]
            while current <= dates[-1]:
                if current.weekday() < 5:  # Monday = 0, Friday = 4
                    expected_dates.add(current)
                current += datetime.timedelta(days=1)

        # Find missing dates
        actual_dates = set(dates)
        missing = expected_dates - actual_dates

        if missing:
            missing_sorted = sorted(missing)
            errors.append(
                ValidationError(
                    field=date_col,
                    message=f"Missing {len(missing)} dates: {missing_sorted[:5]}{'...' if len(missing) > 5 else ''}",
                    severity="error",
                    value=len(missing),
                )
            )

        return errors

    def compute_checksum(self, file_path: Path) -> str:
        """Compute SHA-256 checksum of single file.

        Args:
            file_path: Path to file.

        Returns:
            Hex-encoded SHA-256 checksum.
        """
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256.update(chunk)
        return sha256.hexdigest()

    def compute_aggregate_checksum(self, file_paths: list[Path]) -> str:
        """Compute aggregate SHA-256 checksum for multiple files.

        Algorithm:
        1. Sort file_paths alphabetically for determinism
        2. Compute individual SHA-256 for each file
        3. Concatenate hashes as: "path1:hash1\npath2:hash2\n..."
        4. Return SHA-256 of the concatenated string

        Args:
            file_paths: List of file paths.

        Returns:
            Hex-encoded aggregate SHA-256 checksum.
        """
        # Sort paths for determinism
        sorted_paths = sorted(file_paths, key=lambda p: str(p))

        # Build hash manifest
        lines = []
        for path in sorted_paths:
            file_hash = self.compute_checksum(path)
            lines.append(f"{path}:{file_hash}")

        manifest = "\n".join(lines)
        return hashlib.sha256(manifest.encode()).hexdigest()

    def verify_checksum(self, file_path: Path, expected: str) -> bool:
        """Verify single file checksum matches expected.

        Args:
            file_path: Path to file.
            expected: Expected checksum.

        Returns:
            True if matches, False otherwise.
        """
        actual = self.compute_checksum(file_path)
        return actual == expected

    def verify_aggregate_checksum(self, file_paths: list[Path], expected: str) -> bool:
        """Verify aggregate checksum for multiple files.

        Args:
            file_paths: List of file paths.
            expected: Expected aggregate checksum.

        Returns:
            True if matches, False otherwise.
        """
        actual = self.compute_aggregate_checksum(file_paths)
        return actual == expected

    def detect_anomalies(
        self,
        current_stats: dict[str, Any],
        prev_stats: dict[str, Any] | None,
    ) -> list[AnomalyAlert]:
        """Detect anomalous changes from previous sync.

        Checks:
        - Sudden row count drops (> 10%)
        - Null percentage spikes (> 5% increase)
        - Missing date ranges

        Args:
            current_stats: Current sync statistics.
            prev_stats: Previous sync statistics (None for first sync).

        Returns:
            List of AnomalyAlert for detected anomalies.
        """
        alerts: list[AnomalyAlert] = []

        if prev_stats is None:
            return alerts

        # Check row count drop
        current_rows = current_stats.get("row_count", 0)
        prev_rows = prev_stats.get("row_count", 0)

        if prev_rows > 0:
            drop_pct = (prev_rows - current_rows) / prev_rows
            if drop_pct > self.ROW_DROP_THRESHOLD:
                alerts.append(
                    AnomalyAlert(
                        metric="row_count",
                        current_value=float(current_rows),
                        expected_value=float(prev_rows),
                        deviation_pct=drop_pct * 100,
                        message=(
                            f"Row count dropped by {drop_pct:.1%}: "
                            f"{prev_rows} -> {current_rows}"
                        ),
                    )
                )

        # Check null percentage spikes
        current_nulls = current_stats.get("null_percentages", {})
        prev_nulls = prev_stats.get("null_percentages", {})

        for col, current_pct in current_nulls.items():
            prev_pct = prev_nulls.get(col, 0)
            increase = current_pct - prev_pct

            if increase > self.NULL_SPIKE_THRESHOLD:
                alerts.append(
                    AnomalyAlert(
                        metric=f"null_pct_{col}",
                        current_value=current_pct,
                        expected_value=prev_pct,
                        deviation_pct=increase * 100,
                        message=(
                            f"Null percentage spike in '{col}': "
                            f"{prev_pct:.1%} -> {current_pct:.1%} (+{increase:.1%})"
                        ),
                    )
                )

        # Check missing date ranges
        current_dates = current_stats.get("date_range", {})
        prev_dates = prev_stats.get("date_range", {})

        if current_dates and prev_dates:
            current_start = current_dates.get("start")
            prev_end = prev_dates.get("end")

            if current_start and prev_end:
                # Convert to date if strings
                if isinstance(current_start, str):
                    current_start = datetime.date.fromisoformat(current_start)
                if isinstance(prev_end, str):
                    prev_end = datetime.date.fromisoformat(prev_end)

                expected_start = prev_end + datetime.timedelta(days=1)
                if current_start > expected_start:
                    gap_days = (current_start - expected_start).days
                    alerts.append(
                        AnomalyAlert(
                            metric="date_gap",
                            current_value=float(gap_days),
                            expected_value=0.0,
                            deviation_pct=100.0,
                            message=(
                                f"Missing date range: {expected_start} to "
                                f"{current_start - datetime.timedelta(days=1)} "
                                f"({gap_days} days)"
                            ),
                        )
                    )

        return alerts


# ============================================================================
# Date Partition Validation (shared by T13.1, T13.2, T13.4)
# ============================================================================


def is_valid_date_partition(s: str) -> bool:
    """Check if string is a valid YYYY-MM-DD date.

    Used for partition directory name validation across PIT inspection,
    coverage analysis, and quarantine path validation.
    """
    try:
        datetime.date.fromisoformat(s)
        return True
    except ValueError:
        return False


# ============================================================================
# Quarantine Path Validation (T13.4)
# ============================================================================


def validate_quarantine_path(quarantine_path: str, data_dir: Path) -> Path:
    """Validate and return resolved quarantine directory path.

    Five-phase security validation:
    1. Fail-closed: reject absolute paths and traversal markers BEFORE normalization
    2. Normalize: strip known prefix, extract canonical date component
    3. Lexical containment: verify path is under quarantine_root
    4. Symlink check: verify no symlinks in intermediate components
    5. Post-resolve containment: verify resolved path stays under root

    Args:
        quarantine_path: Raw path from service (e.g., "data/quarantine/crsp/2024-10-16")
        data_dir: Base data directory (e.g., Path("data"))

    Returns:
        Resolved directory path suitable for globbing with ``*.parquet``.

    Raises:
        ValueError: On any validation failure (absolute path, traversal, symlink, escape).
    """
    quarantine_root = data_dir / "quarantine"
    raw = quarantine_path

    # Phase 0: Fail-closed — reject before ANY normalization
    if Path(raw).is_absolute():
        raise ValueError(f"Absolute path rejected: {quarantine_path}")
    if ".." in Path(raw).parts:
        raise ValueError(f"Path traversal rejected: {quarantine_path}")

    # Normalize: strip known 'data/quarantine/' prefix
    known_prefix = "data/quarantine/"
    if raw.startswith(known_prefix):
        raw = raw[len(known_prefix):]

    # Re-check after prefix stripping (defense in depth)
    if ".." in Path(raw).parts:
        raise ValueError(f"Path traversal after normalization: {quarantine_path}")

    # Extract canonical date directory (strict allowlist of accepted formats)
    parts = Path(raw).parts
    # Max 3 segments: e.g. "dataset", "2024-01-15", or "prefix/2024-01-15/file"
    if len(parts) > 3:
        raise ValueError(
            f"Unexpected quarantine path format (too many segments): {quarantine_path}"
        )

    date_part = None
    for part in reversed(parts):
        if is_valid_date_partition(part):
            date_part = part
            break
    if date_part is None:
        raise ValueError(
            f"No valid date component in quarantine path: {quarantine_path}"
        )

    candidate = quarantine_root / date_part

    # Phase 1: Lexical containment
    try:
        relative = candidate.relative_to(quarantine_root)
    except ValueError:
        raise ValueError(
            f"Path {quarantine_path} not under quarantine root"
        ) from None
    if ".." in relative.parts:
        raise ValueError(f"Path traversal detected in {quarantine_path}")

    # Phase 2: Symlink check on each component BEFORE resolve
    check = quarantine_root
    for part in relative.parts:
        check = check / part
        if check.is_symlink():
            raise ValueError(f"Symlink detected at {check}")

    # Phase 3: Post-resolve containment
    resolved_root = quarantine_root.resolve()
    resolved_path = candidate.resolve()
    if not resolved_path.is_relative_to(resolved_root):
        raise ValueError(f"Resolved path {resolved_path} escapes quarantine")

    # Phase 4: Directory-only contract
    if resolved_path.exists() and resolved_path.is_file():
        raise ValueError(f"Expected directory, got file: {resolved_path}")

    return resolved_path
