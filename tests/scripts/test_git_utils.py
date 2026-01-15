#!/usr/bin/env python3
"""
Test suite for scripts/admin/git_utils.py.

Tests all shared git utility functions used by workflow automation components.

Author: Claude Code
Date: 2025-11-07
"""

import subprocess
from unittest.mock import MagicMock, patch

# Import functions under test
from scripts.admin.git_utils import (
    CORE_PACKAGES,
    detect_changed_modules,
    get_staged_files,
    is_core_package,
    requires_full_ci,
)


class TestGetStagedFiles:
    """Test get_staged_files() function."""

    @patch("scripts.admin.git_utils.subprocess.run")
    def test_get_staged_files_success(self, mock_run: MagicMock) -> None:
        """Test successful retrieval of staged files."""
        # Mock git diff output
        mock_run.return_value = MagicMock(
            stdout="libs/allocation/multi_alpha.py\n"
            "apps/execution_gateway/order_placer.py\n"
            "tests/libs/allocation/test_multi_alpha.py\n"
        )

        result = get_staged_files()

        assert result == [
            "libs/allocation/multi_alpha.py",
            "apps/execution_gateway/order_placer.py",
            "tests/libs/allocation/test_multi_alpha.py",
        ]
        mock_run.assert_called_once()

    @patch("scripts.admin.git_utils.subprocess.run")
    def test_get_staged_files_empty(self, mock_run: MagicMock) -> None:
        """Test when no files are staged."""
        mock_run.return_value = MagicMock(stdout="")

        result = get_staged_files()

        assert result == []

    @patch("scripts.admin.git_utils.subprocess.run")
    def test_get_staged_files_git_error(self, mock_run: MagicMock) -> None:
        """Test when git command fails (returns None to trigger fail-safe full CI)."""
        mock_run.side_effect = subprocess.CalledProcessError(128, "git")

        result = get_staged_files()

        # Should return None on git error to trigger fail-safe full CI
        assert result is None

    @patch("scripts.admin.git_utils.subprocess.run")
    def test_get_staged_files_filters_empty_lines(self, mock_run: MagicMock) -> None:
        """Test that empty lines are filtered out."""
        mock_run.return_value = MagicMock(stdout="libs/common/types.py\n\n\napps/cli/main.py\n")

        result = get_staged_files()

        assert result == [
            "libs/common/types.py",
            "apps/cli/main.py",
        ]


class TestDetectChangedModules:
    """Test detect_changed_modules() function."""

    def test_detect_modules_libs_and_apps(self) -> None:
        """Test module detection for libs/ and apps/ files."""
        files = [
            "libs/allocation/multi_alpha.py",
            "libs/allocation/base.py",
            "libs/common/types.py",
            "apps/execution_gateway/order_placer.py",
            "apps/execution_gateway/twap.py",
        ]

        result = detect_changed_modules(files)

        assert result == {
            "libs/allocation",
            "libs/common",
            "apps/execution_gateway",
        }

    def test_detect_modules_strategies(self) -> None:
        """Test module detection for strategies/ files (production strategies only)."""
        files = [
            "strategies/alpha_baseline/strategy.py",
            "strategies/alpha_baseline/features.py",
            "strategies/ensemble/strategy.py",
        ]

        result = detect_changed_modules(files)

        assert result == {
            "strategies/alpha_baseline",
            "strategies/ensemble",
        }

    def test_detect_modules_ignores_non_code_paths(self) -> None:
        """Test that non-code directories are ignored."""
        files = [
            "docs/GETTING_STARTED/README.md",
            "tests/fixtures/sample_data.csv",
            "scripts/workflow_gate.py",
            ".github/workflows/ci.yml",
            "libs/allocation/multi_alpha.py",
        ]

        result = detect_changed_modules(files)

        # Only libs/allocation should be detected
        assert result == {"libs/allocation"}

    def test_detect_modules_empty_list(self) -> None:
        """Test with empty file list."""
        result = detect_changed_modules([])

        assert result == set()

    def test_detect_modules_single_component_path(self) -> None:
        """Test files with only one path component (edge case)."""
        files = [
            "README.md",
            "Makefile",
            "libs/",  # Directory with trailing slash
        ]

        result = detect_changed_modules(files)

        # Should not detect any modules (need at least 2 components)
        assert result == set()

    def test_detect_modules_deduplicates(self) -> None:
        """Test that duplicate modules are deduplicated."""
        files = [
            "libs/allocation/multi_alpha.py",
            "libs/allocation/base.py",
            "libs/allocation/optimizer.py",
            "libs/allocation/weights.py",
        ]

        result = detect_changed_modules(files)

        # Should only have one entry for libs/allocation
        assert result == {"libs/allocation"}
        assert len(result) == 1


class TestIsCorePackage:
    """Test is_core_package() function."""

    def test_libs_is_core(self) -> None:
        """Test that libs/ files are core packages."""
        assert is_core_package("libs/allocation/multi_alpha.py") is True
        assert is_core_package("libs/common/types.py") is True
        assert is_core_package("libs/feature_store/registry.py") is True

    def test_config_is_core(self) -> None:
        """Test that config/ files are core packages."""
        assert is_core_package("config/settings.py") is True
        assert is_core_package("config/production.yaml") is True

    def test_infra_is_core(self) -> None:
        """Test that infra/ files are core packages."""
        assert is_core_package("infra/docker-compose.yml") is True
        assert is_core_package("infra/prometheus/config.yml") is True

    def test_test_fixtures_are_core(self) -> None:
        """Test that tests/fixtures/ files are core packages."""
        assert is_core_package("tests/fixtures/sample_signals.csv") is True
        assert is_core_package("tests/fixtures/positions.json") is True

    def test_scripts_are_core(self) -> None:
        """Test that scripts/ files are core packages."""
        assert is_core_package("scripts/workflow_gate.py") is True
        assert is_core_package("scripts/git_utils.py") is True

    def test_apps_not_core(self) -> None:
        """Test that apps/ files are NOT core packages."""
        assert is_core_package("apps/execution_gateway/order_placer.py") is False
        assert is_core_package("apps/cli/main.py") is False

    def test_strategies_not_core(self) -> None:
        """Test that strategies/ files (production strategies) are NOT core packages."""
        assert is_core_package("strategies/alpha_baseline/strategy.py") is False
        assert is_core_package("strategies/ensemble/features.py") is False

    def test_tests_not_core(self) -> None:
        """Test that tests/ (non-fixtures) are NOT core packages."""
        assert is_core_package("tests/libs/allocation/test_multi_alpha.py") is False
        assert is_core_package("tests/apps/cli/test_main.py") is False

    def test_docs_not_core(self) -> None:
        """Test that docs/ files are NOT core packages."""
        assert is_core_package("docs/GETTING_STARTED/README.md") is False
        assert is_core_package("docs/API/execution_gateway.openapi.yaml") is False

    def test_trailing_slash_prevention(self) -> None:
        """Test that CORE_PACKAGES trailing slashes prevent false positives."""
        # libs/ should match libs/common/types.py but NOT libs_special/foo.py
        assert is_core_package("libs/common/types.py") is True
        assert is_core_package("libs_special/foo.py") is False

        # scripts/ should match scripts/foo.py but NOT scripts_backup/bar.py
        assert is_core_package("scripts/workflow_gate.py") is True
        assert is_core_package("scripts_backup/workflow_gate.py") is False


class TestRequiresFullCI:
    """Test requires_full_ci() function."""

    def test_requires_full_ci_for_core_package(self) -> None:
        """Test that core package changes trigger full CI."""
        files = ["libs/common/types.py"]
        assert requires_full_ci(files) is True

        files = ["config/settings.py"]
        assert requires_full_ci(files) is True

        files = ["scripts/workflow_gate.py"]
        assert requires_full_ci(files) is True

    def test_requires_full_ci_for_many_modules(self) -> None:
        """Test that >5 modules trigger full CI (likely a refactor)."""
        files = [
            "apps/app1/foo.py",
            "apps/app2/bar.py",
            "apps/app3/baz.py",
            "libs/lib1/x.py",
            "libs/lib2/y.py",
            "libs/lib3/z.py",
        ]

        # 6 modules: apps/app1, apps/app2, apps/app3, libs/lib1, libs/lib2, libs/lib3
        assert requires_full_ci(files) is True

    def test_not_requires_full_ci_for_single_app(self) -> None:
        """Test that single app module does NOT trigger full CI."""
        files = [
            "apps/execution_gateway/order_placer.py",
            "apps/execution_gateway/twap.py",
            "apps/execution_gateway/config.py",
        ]

        # Only 1 module: apps/execution_gateway
        assert requires_full_ci(files) is False

    def test_not_requires_full_ci_for_few_modules(self) -> None:
        """Test that <=5 modules do NOT trigger full CI."""
        files = [
            "apps/app1/foo.py",
            "apps/app2/bar.py",
            "apps/app3/baz.py",
            "strategies/strat1/x.py",
            "strategies/strat2/y.py",
        ]

        # 5 modules exactly - should NOT trigger (>5 required)
        assert requires_full_ci(files) is False

    def test_empty_file_list(self) -> None:
        """Test with empty file list."""
        assert requires_full_ci([]) is False

    def test_mixed_core_and_non_core(self) -> None:
        """Test that core package presence overrides module count."""
        files = [
            "apps/execution_gateway/order_placer.py",  # Not core
            "libs/common/types.py",  # CORE - triggers full CI
        ]

        # Only 2 modules, but one is core
        assert requires_full_ci(files) is True

    def test_boundary_exactly_five_modules(self) -> None:
        """Test boundary condition: exactly 5 modules."""
        files = [
            "apps/app1/foo.py",
            "apps/app2/bar.py",
            "apps/app3/baz.py",
            "strategies/strat1/x.py",
            "strategies/strat2/y.py",
        ]

        # Exactly 5 modules - should NOT trigger (>5 required)
        assert requires_full_ci(files) is False

    def test_boundary_six_modules(self) -> None:
        """Test boundary condition: exactly 6 modules."""
        files = [
            "apps/app1/foo.py",
            "apps/app2/bar.py",
            "apps/app3/baz.py",
            "strategies/strat1/x.py",
            "strategies/strat2/y.py",
            "strategies/strat3/z.py",
        ]

        # 6 modules - should trigger full CI
        assert requires_full_ci(files) is True


class TestCorePackagesConstant:
    """Test CORE_PACKAGES constant definition."""

    def test_core_packages_has_trailing_slashes(self) -> None:
        """Verify all CORE_PACKAGES entries have trailing slashes."""
        for package in CORE_PACKAGES:
            assert package.endswith("/"), f"{package} should have trailing slash"

    def test_core_packages_expected_entries(self) -> None:
        """Verify CORE_PACKAGES contains expected entries."""
        expected = {
            "libs/",
            "config/",
            "infra/",
            "tests/fixtures/",
            "scripts/",
        }
        assert CORE_PACKAGES == expected
