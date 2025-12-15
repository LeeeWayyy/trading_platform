"""Regression tests against golden backtest results.

These tests verify that alpha signals and backtest metrics remain stable
across code changes. Any drift > 0.1% triggers a failure.

Current Status:
- This module provides the regression testing infrastructure (manifest validation,
  checksum verification, metric drift detection).
- Golden results are currently placeholders that will be replaced with actual
  backtest outputs when dataset access is configured (T5.1/T5.2 dependencies).
- Integration with PITBacktester will be added once the job queue infrastructure
  is complete (see P4T4_TASK.md for dependencies).

TODO: Add TestBacktestRegression class that:
1. Loads configs from golden_results/*_config.json
2. Runs actual backtests via PITBacktester
3. Compares outputs against golden metrics using assert_metrics_match
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from libs.common.file_utils import hash_file_sha256
from tests.regression.conftest import (
    METRIC_TOLERANCE,
    load_golden_config,
    load_golden_result,
)


def assert_metrics_match(
    actual: dict[str, Any],
    expected: dict[str, Any],
    tolerance: float = METRIC_TOLERANCE,
) -> None:
    """Assert all key metrics match within tolerance.

    Args:
        actual: Actual metrics from backtest result
        expected: Expected metrics from golden result
        tolerance: Maximum allowed relative difference (default 0.1%)

    Raises:
        AssertionError: If any metric drifts beyond tolerance
    """
    expected_keys = set(expected.keys())
    actual_keys = set(actual.keys())

    if expected_keys != actual_keys:
        missing_in_actual = expected_keys - actual_keys
        extra_in_actual = actual_keys - expected_keys
        if missing_in_actual:
            pytest.fail(f"Metrics missing from actual result: {missing_in_actual}")
        if extra_in_actual:
            pytest.fail(f"Unexpected metrics in actual result: {extra_in_actual}")

    for metric in expected_keys:
        actual_val = actual[metric]
        expected_val = expected[metric]

        is_actual_numeric = isinstance(actual_val, int | float)
        is_expected_numeric = isinstance(expected_val, int | float)

        if is_expected_numeric and is_actual_numeric:
            # Use math.isclose for robust floating-point comparison
            # This handles zero/near-zero values correctly with both rel and abs tolerance
            abs_tolerance = max(1e-6, tolerance * abs(expected_val))
            is_close = math.isclose(
                actual_val, expected_val, rel_tol=tolerance, abs_tol=abs_tolerance
            )

            if expected_val != 0:
                diff = abs(actual_val - expected_val) / abs(expected_val)
                assert is_close, (
                    f"Metric {metric} drifted: expected {expected_val}, got {actual_val} "
                    f"(relative diff={diff:.6f}, tolerance={tolerance})"
                )
            else:
                assert is_close, (
                    f"Metric {metric} drifted: expected {expected_val}, got {actual_val} "
                    f"(abs diff={abs(actual_val - expected_val):.6e}, tolerance={tolerance})"
                )
        elif actual_val != expected_val:
            # Handle None vs non-None, string mismatches, etc.
            pytest.fail(
                f"Metric {metric} mismatch: expected {expected_val!r}, got {actual_val!r}"
            )


class TestGoldenManifest:
    """Tests for golden manifest integrity."""

    def test_manifest_exists(self, golden_results_dir: Path) -> None:
        """Verify manifest.json exists."""
        manifest_path = golden_results_dir / "manifest.json"
        assert manifest_path.exists(), "manifest.json not found"

    def test_manifest_has_required_fields(self, golden_results_dir: Path) -> None:
        """Verify manifest has all required fields."""
        manifest_path = golden_results_dir / "manifest.json"
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)

        required_fields = [
            "version",
            "created_at",
            "dataset_snapshot_id",
            "last_regenerated",
            "regenerated_by",
            "storage_size_mb",
            "golden_files",
        ]

        for field in required_fields:
            assert field in manifest, f"Manifest missing required field: {field}"

    def test_manifest_checksums_valid(self, golden_results_dir: Path) -> None:
        """Verify all golden file checksums match actual files."""
        manifest_path = golden_results_dir / "manifest.json"
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)

        for entry in manifest["golden_files"]:
            file_path = golden_results_dir / entry["name"]
            assert file_path.exists(), f"Golden file not found: {entry['name']}"

            actual_checksum = f"sha256:{hash_file_sha256(file_path)}"
            expected_checksum = entry["checksum"]

            assert actual_checksum == expected_checksum, (
                f"Checksum mismatch for {entry['name']}: "
                f"expected {expected_checksum}, got {actual_checksum}"
            )

    def test_manifest_file_completeness(self, golden_results_dir: Path) -> None:
        """Verify manifest lists all golden files and no extra files exist."""
        manifest_path = golden_results_dir / "manifest.json"
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)

        # Get files listed in manifest
        manifest_files = {entry["name"] for entry in manifest["golden_files"]}

        # Get actual JSON files on disk (excluding manifest itself and README)
        disk_files = {
            f.name
            for f in golden_results_dir.glob("*.json")
            if f.name != "manifest.json"
        }

        # Verify manifest matches disk
        missing_from_manifest = disk_files - manifest_files
        extra_in_manifest = manifest_files - disk_files

        assert not missing_from_manifest, (
            f"Files on disk not in manifest: {missing_from_manifest}"
        )
        assert not extra_in_manifest, (
            f"Files in manifest not on disk: {extra_in_manifest}"
        )

    def test_manifest_staleness_check(self, golden_results_dir: Path) -> None:
        """Fail if manifest is older than 90 days (stale golden results)."""
        manifest_path = golden_results_dir / "manifest.json"
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)

        last_regenerated = datetime.fromisoformat(
            manifest["last_regenerated"].replace("Z", "+00:00")
        )
        age_days = (datetime.now(UTC) - last_regenerated).days

        assert age_days <= 90, (
            f"Golden manifest is {age_days} days old (>90 days). "
            "Regenerate with: python scripts/generate_golden_results.py --use-placeholders"
        )


class TestGoldenResults:
    """Tests for golden result file integrity."""

    def test_momentum_golden_exists(self, golden_results_dir: Path) -> None:
        """Verify momentum golden result exists."""
        path = golden_results_dir / "momentum_2020_2022.json"
        assert path.exists(), "momentum_2020_2022.json not found"

    def test_value_golden_exists(self, golden_results_dir: Path) -> None:
        """Verify value golden result exists."""
        path = golden_results_dir / "value_2020_2022.json"
        assert path.exists(), "value_2020_2022.json not found"

    def test_momentum_config_exists(self, golden_results_dir: Path) -> None:
        """Verify momentum config exists."""
        path = golden_results_dir / "momentum_2020_2022_config.json"
        assert path.exists(), "momentum_2020_2022_config.json not found"

    def test_value_config_exists(self, golden_results_dir: Path) -> None:
        """Verify value config exists."""
        path = golden_results_dir / "value_2020_2022_config.json"
        assert path.exists(), "value_2020_2022_config.json not found"

    def test_golden_has_required_metrics(self, golden_results_dir: Path) -> None:
        """Verify golden results have all required metrics."""
        required_metrics = [
            "mean_ic",
            "icir",
            "hit_rate",
            "coverage",
            "long_short_spread",
            "average_turnover",
            "decay_half_life",
        ]

        for golden_file in ["momentum_2020_2022.json", "value_2020_2022.json"]:
            result = load_golden_result(golden_file)
            for metric in required_metrics:
                assert metric in result, (
                    f"Golden {golden_file} missing metric: {metric}"
                )

    def test_config_snapshot_consistency(self, golden_results_dir: Path) -> None:
        """Verify all config snapshot_ids match manifest dataset_snapshot_id."""
        manifest_path = golden_results_dir / "manifest.json"
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)

        manifest_snapshot = manifest["dataset_snapshot_id"]

        for config_file in golden_results_dir.glob("*_config.json"):
            with open(config_file, encoding="utf-8") as f:
                config = json.load(f)

            config_snapshot = config.get("snapshot_id")
            assert config_snapshot == manifest_snapshot, (
                f"Config {config_file.name} snapshot_id ({config_snapshot}) "
                f"does not match manifest ({manifest_snapshot})"
            )


class TestMetricsMatching:
    """Tests for metrics matching functionality."""

    def test_assert_metrics_match_identical(self) -> None:
        """Test that identical metrics pass."""
        actual = {"mean_ic": 0.025, "icir": 0.85, "hit_rate": 0.52}
        expected = {"mean_ic": 0.025, "icir": 0.85, "hit_rate": 0.52}

        # Should not raise
        assert_metrics_match(actual, expected)

    def test_assert_metrics_match_within_tolerance(self) -> None:
        """Test that metrics within tolerance pass."""
        actual = {"mean_ic": 0.02502, "icir": 0.85}  # 0.08% diff
        expected = {"mean_ic": 0.025, "icir": 0.85}

        # Should not raise (0.08% < 0.1% tolerance)
        assert_metrics_match(actual, expected)

    def test_assert_metrics_match_exceeds_tolerance(self) -> None:
        """Test that metrics exceeding tolerance fail."""
        actual = {"mean_ic": 0.026, "icir": 0.85}  # 4% diff
        expected = {"mean_ic": 0.025, "icir": 0.85}

        with pytest.raises(AssertionError, match="drifted"):
            assert_metrics_match(actual, expected)

    def test_assert_metrics_match_missing_metric(self) -> None:
        """Test that missing actual metric fails."""
        actual = {"icir": 0.85}  # missing mean_ic
        expected = {"mean_ic": 0.025, "icir": 0.85}

        with pytest.raises(pytest.fail.Exception, match="missing"):
            assert_metrics_match(actual, expected)


class TestHelperFunctions:
    """Tests for helper functions."""

    def test_hash_file(self, golden_results_dir: Path) -> None:
        """Test file hashing produces consistent results."""
        manifest_path = golden_results_dir / "manifest.json"
        hash1 = hash_file_sha256(manifest_path)
        hash2 = hash_file_sha256(manifest_path)

        assert hash1 == hash2
        assert len(hash1) == 64  # SHA256 hex length

    def test_load_golden_result(self) -> None:
        """Test loading golden result."""
        result = load_golden_result("momentum_2020_2022.json")
        assert "mean_ic" in result
        assert isinstance(result["mean_ic"], float)

    def test_load_golden_config(self) -> None:
        """Test loading golden config."""
        config = load_golden_config("momentum_2020_2022_config.json")
        assert "alpha_name" in config
        assert config["alpha_name"] == "momentum"
