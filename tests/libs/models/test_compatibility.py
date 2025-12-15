"""Tests for version compatibility checking."""

import os

from libs.models.compatibility import (
    CompatibilityResult,
    VersionCompatibilityChecker,
    VersionDriftError,
)


class TestVersionCompatibilityChecker:
    """Tests for VersionCompatibilityChecker."""

    def test_exact_match(self) -> None:
        """Test exact version match returns compatible."""
        checker = VersionCompatibilityChecker(strict_mode=True)
        model_versions = {"crsp": "v1.2.3", "compustat": "v1.0.1"}
        current_versions = {"crsp": "v1.2.3", "compustat": "v1.0.1"}

        result = checker.check_compatibility(model_versions, current_versions)

        assert result.compatible is True
        assert result.level == "exact"
        assert len(result.warnings) == 0

    def test_drift_strict_mode_blocks(self) -> None:
        """Test version drift blocks in strict mode."""
        checker = VersionCompatibilityChecker(strict_mode=True)
        model_versions = {"crsp": "v1.2.3", "compustat": "v1.0.1"}
        current_versions = {"crsp": "v1.2.4", "compustat": "v1.0.1"}  # crsp version changed

        result = checker.check_compatibility(model_versions, current_versions)

        assert result.compatible is False
        assert result.level == "drift"
        assert len(result.warnings) == 1
        assert "crsp" in result.warnings[0]

    def test_drift_non_strict_mode_allows(self) -> None:
        """Test version drift allows in non-strict mode."""
        checker = VersionCompatibilityChecker(strict_mode=False)
        model_versions = {"crsp": "v1.2.3", "compustat": "v1.0.1"}
        current_versions = {"crsp": "v1.2.4", "compustat": "v1.0.1"}

        result = checker.check_compatibility(model_versions, current_versions)

        assert result.compatible is True
        assert result.level == "drift"
        assert len(result.warnings) == 1

    def test_missing_dataset_always_blocks(self) -> None:
        """Test missing dataset blocks regardless of mode."""
        checker = VersionCompatibilityChecker(strict_mode=False)  # Even in non-strict
        model_versions = {"crsp": "v1.2.3", "compustat": "v1.0.1"}
        current_versions = {"crsp": "v1.2.3"}  # compustat missing

        result = checker.check_compatibility(model_versions, current_versions)

        assert result.compatible is False
        assert result.level == "missing"
        assert "compustat" in result.warnings[0]

    def test_strict_mode_override_per_call(self) -> None:
        """Test strict mode can be overridden per call."""
        checker = VersionCompatibilityChecker(strict_mode=True)
        model_versions = {"crsp": "v1.2.3"}
        current_versions = {"crsp": "v1.2.4"}

        # Override to non-strict
        result = checker.check_compatibility(model_versions, current_versions, strict_mode=False)

        assert result.compatible is True
        assert result.level == "drift"

    def test_environment_variable_control(self) -> None:
        """Test strict mode from environment variable."""
        # Set environment variable
        os.environ["STRICT_VERSION_MODE"] = "false"
        try:
            checker = VersionCompatibilityChecker()
            assert checker.strict_mode is False
        finally:
            del os.environ["STRICT_VERSION_MODE"]

    def test_multiple_drift_warnings(self) -> None:
        """Test multiple drifts generate multiple warnings."""
        checker = VersionCompatibilityChecker(strict_mode=False)
        model_versions = {"crsp": "v1.2.3", "compustat": "v1.0.1"}
        current_versions = {"crsp": "v1.2.4", "compustat": "v1.0.2"}  # Both changed

        result = checker.check_compatibility(model_versions, current_versions)

        assert result.compatible is True
        assert result.level == "drift"
        assert len(result.warnings) == 2


class TestCompatibilityResult:
    """Tests for CompatibilityResult dataclass."""

    def test_creation(self) -> None:
        """Test result creation."""
        result = CompatibilityResult(
            compatible=True,
            level="exact",
            warnings=[],
        )
        assert result.compatible is True
        assert result.level == "exact"

    def test_with_warnings(self) -> None:
        """Test result with warnings."""
        result = CompatibilityResult(
            compatible=False,
            level="drift",
            warnings=["crsp: v1.2.3 -> v1.2.4"],
        )
        assert result.compatible is False
        assert len(result.warnings) == 1


class TestVersionDriftError:
    """Tests for VersionDriftError."""

    def test_error_message(self) -> None:
        """Test error message contains warnings."""
        warnings = ["crsp: v1.2.3 -> v1.2.4", "compustat: v1.0.1 -> v1.0.2"]
        error = VersionDriftError(warnings)

        assert "Version drift" in str(error)
        assert "crsp" in str(error)
        assert "compustat" in str(error)
