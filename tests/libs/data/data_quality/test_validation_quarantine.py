"""Tests for quarantine path validation and date partition helpers (P6T13/T13.4).

Tests cover:
- is_valid_date_partition: valid/invalid date strings
- validate_quarantine_path: traversal, absolute, symlink, containment, format normalization
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from libs.data.data_quality.validation import (
    is_valid_date_partition,
    validate_quarantine_path,
)

# ============================================================================
# is_valid_date_partition
# ============================================================================


class TestIsValidDatePartition:
    def test_valid_date(self) -> None:
        assert is_valid_date_partition("2024-01-15") is True

    def test_valid_date_edge(self) -> None:
        assert is_valid_date_partition("2000-12-31") is True

    def test_invalid_month(self) -> None:
        assert is_valid_date_partition("2024-13-01") is False

    def test_invalid_day(self) -> None:
        assert is_valid_date_partition("2024-02-30") is False

    def test_not_a_date(self) -> None:
        assert is_valid_date_partition("hello") is False

    def test_empty_string(self) -> None:
        assert is_valid_date_partition("") is False

    def test_partial_date(self) -> None:
        assert is_valid_date_partition("2024-01") is False

    def test_date_with_time(self) -> None:
        # fromisoformat accepts this in Python 3.11+
        # but we only want YYYY-MM-DD for directory names
        # fromisoformat("2024-01-15T12:00:00") returns a datetime, not date
        # date.fromisoformat raises for datetime strings
        assert is_valid_date_partition("2024-01-15T12:00:00") is False


# ============================================================================
# validate_quarantine_path
# ============================================================================


class TestValidateQuarantinePath:
    """Test quarantine path validation with tmp_path fixture."""

    @pytest.fixture()
    def data_dir(self, tmp_path: Path) -> Path:
        """Create a temporary data directory with quarantine structure."""
        qdir = tmp_path / "quarantine" / "2024-10-16"
        qdir.mkdir(parents=True)
        # Create a sample parquet file
        (qdir / "AAPL.parquet").write_bytes(b"fake")
        return tmp_path

    def test_etl_format(self, data_dir: Path) -> None:
        """ETL format: bare date string."""
        result = validate_quarantine_path("2024-10-16", data_dir)
        expected = (data_dir / "quarantine" / "2024-10-16").resolve()
        assert result == expected

    def test_service_mock_format(self, data_dir: Path) -> None:
        """Service mock format: data/quarantine/crsp/2024-10-16."""
        result = validate_quarantine_path(
            "data/quarantine/crsp/2024-10-16", data_dir
        )
        expected = (data_dir / "quarantine" / "2024-10-16").resolve()
        assert result == expected

    def test_stripped_format(self, data_dir: Path) -> None:
        """Already-stripped format: crsp/2024-10-16."""
        result = validate_quarantine_path("crsp/2024-10-16", data_dir)
        expected = (data_dir / "quarantine" / "2024-10-16").resolve()
        assert result == expected

    def test_all_formats_resolve_same(self, data_dir: Path) -> None:
        """ETL, service mock, and stripped formats all resolve to same directory."""
        r1 = validate_quarantine_path("2024-10-16", data_dir)
        r2 = validate_quarantine_path("data/quarantine/crsp/2024-10-16", data_dir)
        r3 = validate_quarantine_path("crsp/2024-10-16", data_dir)
        assert r1 == r2 == r3

    def test_absolute_path_rejected(self, data_dir: Path) -> None:
        """Absolute paths are rejected fail-closed."""
        with pytest.raises(ValueError, match="Absolute path rejected"):
            validate_quarantine_path("/etc/passwd", data_dir)

    def test_traversal_rejected_before_normalization(self, data_dir: Path) -> None:
        """Path traversal is rejected before any normalization."""
        with pytest.raises(ValueError, match="Path traversal rejected"):
            validate_quarantine_path("../../2024-10-16", data_dir)

    def test_traversal_after_prefix_strip(self, data_dir: Path) -> None:
        """Traversal hidden after prefix stripping is caught."""
        with pytest.raises(ValueError, match="Path traversal"):
            validate_quarantine_path("data/quarantine/../../../etc/2024-10-16", data_dir)

    def test_no_date_component(self, data_dir: Path) -> None:
        """Path with no valid date component is rejected."""
        with pytest.raises(ValueError, match="No valid date component"):
            validate_quarantine_path("crsp/not-a-date", data_dir)

    def test_too_many_segments(self, data_dir: Path) -> None:
        """Paths with >3 segments after normalization are rejected."""
        with pytest.raises(ValueError, match="too many segments"):
            validate_quarantine_path("a/b/c/d/2024-10-16", data_dir)

    def test_symlink_detected(self, data_dir: Path) -> None:
        """Symlink in intermediate component is rejected."""
        quarantine_root = data_dir / "quarantine"
        # Create a symlink: quarantine/evil -> /tmp
        evil_link = quarantine_root / "2024-11-01"
        try:
            os.symlink("/tmp", str(evil_link))
        except OSError:
            pytest.skip("Cannot create symlinks on this platform")

        with pytest.raises(ValueError, match="Symlink detected"):
            validate_quarantine_path("2024-11-01", data_dir)

    def test_file_path_rejected(self, data_dir: Path) -> None:
        """File path (not directory) is rejected."""
        # Create a file where a directory would be expected
        quarantine_root = data_dir / "quarantine"
        file_as_dir = quarantine_root / "2024-12-25"
        file_as_dir.write_text("I am a file, not a directory")

        with pytest.raises(ValueError, match="Expected directory, got file"):
            validate_quarantine_path("2024-12-25", data_dir)

    def test_nonexistent_date_dir_allowed(self, data_dir: Path) -> None:
        """Non-existent date directory is allowed (returns resolved path)."""
        result = validate_quarantine_path("2025-01-01", data_dir)
        expected = (data_dir / "quarantine" / "2025-01-01").resolve()
        assert result == expected

    def test_returns_resolved_path(self, data_dir: Path) -> None:
        """Return value is a fully resolved path."""
        result = validate_quarantine_path("2024-10-16", data_dir)
        assert result.is_absolute()
        assert result == result.resolve()
