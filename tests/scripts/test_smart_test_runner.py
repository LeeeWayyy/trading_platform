#!/usr/bin/env python3
"""
Test suite for SmartTestRunner class in scripts/workflow_gate.py.

Tests intelligent test selection based on git changes.

Author: Claude Code
Date: 2025-11-08
"""

import builtins
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add project root to Python path for imports
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

# Import class under test
from scripts.workflow_gate import SmartTestRunner


class TestSmartTestRunnerInitialization:
    """Test SmartTestRunner.__init__() method."""

    def test_init_successful_import(self) -> None:
        """Test successful initialization with git_utils imports."""
        # SmartTestRunner imports from scripts.git_utils at runtime
        # No need to patch - just verify the lazy imports work
        runner = SmartTestRunner()

        # Verify lazy imports are set
        assert runner._get_staged_files is not None
        assert runner._requires_full_ci is not None
        assert runner._detect_changed_modules is not None

    def test_init_fallback_on_import_error(self, capsys: pytest.CaptureFixture) -> None:
        """Test graceful fallback when git_utils import fails."""
        # Intercept the import statement itself to trigger ImportError
        real_import = builtins.__import__

        def fake_import(name: str, *args, **kwargs):
            if name == "scripts.git_utils":
                raise ImportError("git_utils not found")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            runner = SmartTestRunner()

            # Verify fallback functions are set correctly
            # CRITICAL: Fallback returns dummy file to force full CI (fail-safe)
            assert runner._get_staged_files() == ["DUMMY_FILE_TO_FORCE_CI"]
            assert runner._requires_full_ci(["DUMMY_FILE_TO_FORCE_CI"]) is True
            assert runner._detect_changed_modules(["DUMMY_FILE_TO_FORCE_CI"]) == set()

            # Verify warning message matches implementation
            captured = capsys.readouterr()
            assert "Warning: Could not import git_utils" in captured.out
            assert "Smart testing features will be disabled" in captured.out
            assert "Defaulting to full CI for safety" in captured.out


class TestShouldRunFullCI:
    """Test SmartTestRunner.should_run_full_ci() method."""

    def test_no_staged_files(self) -> None:
        """Test with no staged files (edge case)."""
        runner = SmartTestRunner()
        runner._get_staged_files = MagicMock(return_value=[])

        result = runner.should_run_full_ci()

        assert result is False

    def test_git_command_failed(self) -> None:
        """Test fail-safe when git command fails (returns None)."""
        runner = SmartTestRunner()
        runner._get_staged_files = MagicMock(return_value=None)

        result = runner.should_run_full_ci()

        # Should run full CI as fail-safe
        assert result is True
        # Should set git_failed flag
        assert runner._git_failed is True

    def test_core_package_changed(self) -> None:
        """Test when core package changed (requires full CI)."""
        runner = SmartTestRunner()
        runner._get_staged_files = MagicMock(return_value=["libs/common/types.py"])
        runner._requires_full_ci = MagicMock(return_value=True)

        result = runner.should_run_full_ci()

        assert result is True
        runner._requires_full_ci.assert_called_once_with(["libs/common/types.py"])

    def test_many_modules_changed(self) -> None:
        """Test when >5 modules changed (requires full CI)."""
        files = [
            "apps/app1/foo.py",
            "apps/app2/bar.py",
            "apps/app3/baz.py",
            "libs/lib1/x.py",
            "libs/lib2/y.py",
            "libs/lib3/z.py",
        ]
        runner = SmartTestRunner()
        runner._get_staged_files = MagicMock(return_value=files)
        runner._requires_full_ci = MagicMock(return_value=True)

        result = runner.should_run_full_ci()

        assert result is True

    def test_single_module_changed(self) -> None:
        """Test when single module changed (targeted tests OK)."""
        runner = SmartTestRunner()
        runner._get_staged_files = MagicMock(
            return_value=["apps/execution_gateway/order_placer.py"]
        )
        runner._requires_full_ci = MagicMock(return_value=False)

        result = runner.should_run_full_ci()

        assert result is False

    def test_few_modules_changed(self) -> None:
        """Test when <=5 modules changed (targeted tests OK)."""
        files = [
            "apps/app1/foo.py",
            "apps/app2/bar.py",
            "strategies/strat1/x.py",
        ]
        runner = SmartTestRunner()
        runner._get_staged_files = MagicMock(return_value=files)
        runner._requires_full_ci = MagicMock(return_value=False)

        result = runner.should_run_full_ci()

        assert result is False


class TestGetTestTargets:
    """Test SmartTestRunner.get_test_targets() method."""

    def test_no_staged_files(self) -> None:
        """Test with no staged files (returns empty list)."""
        runner = SmartTestRunner()
        runner._get_staged_files = MagicMock(return_value=[])

        result = runner.get_test_targets()

        assert result == []

    def test_single_module_libs(self) -> None:
        """Test single libs/ module (returns correct test path)."""
        runner = SmartTestRunner()
        runner._get_staged_files = MagicMock(return_value=["libs/allocation/multi_alpha.py"])
        runner._detect_changed_modules = MagicMock(return_value={"libs/allocation"})

        result = runner.get_test_targets()

        assert result == ["tests/libs/allocation/"]

    def test_single_module_apps(self) -> None:
        """Test single apps/ module (returns correct test path + integration tests)."""
        runner = SmartTestRunner()
        runner._get_staged_files = MagicMock(
            return_value=["apps/execution_gateway/order_placer.py"]
        )
        runner._detect_changed_modules = MagicMock(return_value={"apps/execution_gateway"})

        result = runner.get_test_targets()

        # App changes trigger both module tests and integration tests
        assert result == ["tests/apps/execution_gateway/", "tests/integration/"]

    def test_multiple_modules(self) -> None:
        """Test multiple modules (returns sorted test paths)."""
        files = [
            "libs/allocation/multi_alpha.py",
            "apps/execution_gateway/order_placer.py",
            "strategies/alpha_baseline/strategy.py",
        ]
        runner = SmartTestRunner()
        runner._get_staged_files = MagicMock(return_value=files)
        runner._detect_changed_modules = MagicMock(
            return_value={
                "apps/execution_gateway",
                "libs/allocation",
                "strategies/alpha_baseline",
            }
        )

        result = runner.get_test_targets()

        # Should be sorted and include integration tests (due to apps/ change)
        assert result == [
            "tests/apps/execution_gateway/",
            "tests/integration/",
            "tests/libs/allocation/",
            "tests/strategies/alpha_baseline/",
        ]

    def test_non_code_files_ignored(self) -> None:
        """Test non-code files are ignored (returns empty list)."""
        files = [
            "docs/README.md",
            "scripts/workflow_gate.py",
            ".github/workflows/ci.yml",
        ]
        runner = SmartTestRunner()
        runner._get_staged_files = MagicMock(return_value=files)
        runner._detect_changed_modules = MagicMock(return_value=set())

        result = runner.get_test_targets()

        assert result == []

    def test_deduplication(self) -> None:
        """Test duplicate modules are deduplicated."""
        files = [
            "libs/allocation/multi_alpha.py",
            "libs/allocation/base.py",
            "libs/allocation/optimizer.py",
        ]
        runner = SmartTestRunner()
        runner._get_staged_files = MagicMock(return_value=files)
        runner._detect_changed_modules = MagicMock(return_value={"libs/allocation"})

        result = runner.get_test_targets()

        # Should only have one entry
        assert result == ["tests/libs/allocation/"]
        assert len(result) == 1


class TestGetTestCommand:
    """Test SmartTestRunner.get_test_command() method."""

    def test_pr_context_always_full_ci(self) -> None:
        """Test context='pr' always returns full CI command."""
        runner = SmartTestRunner()
        runner._get_staged_files = MagicMock(
            return_value=["apps/execution_gateway/order_placer.py"]
        )
        runner._requires_full_ci = MagicMock(return_value=False)

        result = runner.get_test_command(context="pr")

        assert result == ["make", "ci-local"]

    def test_git_failure_requires_full_ci(self) -> None:
        """Test git command failure triggers fail-safe full CI."""
        runner = SmartTestRunner()
        runner._get_staged_files = MagicMock(return_value=None)

        result = runner.get_test_command(context="commit")

        # Should return full CI command as fail-safe
        assert result == ["make", "ci-local"]

    def test_core_package_requires_full_ci(self) -> None:
        """Test core package change requires full CI even on commit."""
        runner = SmartTestRunner()
        runner._get_staged_files = MagicMock(return_value=["libs/common/types.py"])
        runner._requires_full_ci = MagicMock(return_value=True)

        result = runner.get_test_command(context="commit")

        assert result == ["make", "ci-local"]

    def test_no_changes_returns_echo(self) -> None:
        """Test no changes returns echo message."""
        runner = SmartTestRunner()
        runner._get_staged_files = MagicMock(return_value=[])
        runner._requires_full_ci = MagicMock(return_value=False)
        runner._detect_changed_modules = MagicMock(return_value=set())

        result = runner.get_test_command(context="commit")

        assert result == ["echo", "No Python tests needed (no code changes detected)"]

    def test_targeted_tests_single_module(self) -> None:
        """Test targeted tests for single module."""
        runner = SmartTestRunner()
        runner._get_staged_files = MagicMock(return_value=["libs/allocation/multi_alpha.py"])
        runner._requires_full_ci = MagicMock(return_value=False)
        runner._detect_changed_modules = MagicMock(return_value={"libs/allocation"})

        result = runner.get_test_command(context="commit")

        assert result == ["poetry", "run", "pytest", "tests/libs/allocation/"]

    def test_targeted_tests_multiple_modules(self) -> None:
        """Test targeted tests for multiple modules."""
        files = [
            "libs/allocation/multi_alpha.py",
            "apps/execution_gateway/order_placer.py",
        ]
        runner = SmartTestRunner()
        runner._get_staged_files = MagicMock(return_value=files)
        runner._requires_full_ci = MagicMock(return_value=False)
        runner._detect_changed_modules = MagicMock(
            return_value={"libs/allocation", "apps/execution_gateway"}
        )

        result = runner.get_test_command(context="commit")

        # Should include both paths (order may vary due to set)
        assert result[0] == "poetry"
        assert result[1] == "run"
        assert result[2] == "pytest"
        assert "tests/libs/allocation/" in result
        assert "tests/apps/execution_gateway/" in result


class TestPrintTestStrategy:
    """Test SmartTestRunner.print_test_strategy() method."""

    def test_full_ci_required_message(self, capsys: pytest.CaptureFixture) -> None:
        """Test full CI required message."""
        runner = SmartTestRunner()
        runner._get_staged_files = MagicMock(return_value=["libs/common/types.py"])
        runner._requires_full_ci = MagicMock(return_value=True)

        runner.print_test_strategy()

        captured = capsys.readouterr()
        assert "🔍 Full CI Required" in captured.out
        assert "Reason: Core package changed OR >5 modules changed" in captured.out
        assert "Command: make ci-local" in captured.out

    def test_no_changes_message(self, capsys: pytest.CaptureFixture) -> None:
        """Test no changes message."""
        runner = SmartTestRunner()
        runner._get_staged_files = MagicMock(return_value=[])
        runner._requires_full_ci = MagicMock(return_value=False)
        runner._detect_changed_modules = MagicMock(return_value=set())

        runner.print_test_strategy()

        captured = capsys.readouterr()
        assert "✓ No tests needed (no code changes)" in captured.out

    def test_targeted_testing_message(self, capsys: pytest.CaptureFixture) -> None:
        """Test targeted testing message."""
        runner = SmartTestRunner()
        runner._get_staged_files = MagicMock(return_value=["libs/allocation/multi_alpha.py"])
        runner._requires_full_ci = MagicMock(return_value=False)
        runner._detect_changed_modules = MagicMock(return_value={"libs/allocation"})

        runner.print_test_strategy()

        captured = capsys.readouterr()
        assert "🎯 Targeted Testing" in captured.out
        assert "Modules:" in captured.out
        assert "tests/libs/allocation/" in captured.out
        assert "Command:" in captured.out
        assert "pytest" in captured.out


class TestSmartTestRunnerEdgeCases:
    """Test SmartTestRunner edge cases and error handling."""

    def test_fallback_functions_behavior(self) -> None:
        """Test fallback functions return safe defaults."""
        runner = SmartTestRunner()
        # Manually set fallback functions (simulating import failure)
        runner._get_staged_files = lambda: ["DUMMY_FILE_TO_FORCE_CI"]
        runner._requires_full_ci = lambda files: True
        runner._detect_changed_modules = lambda files: set()

        # Fail-safe: should force full CI when git_utils unavailable
        assert runner.should_run_full_ci() is True
        assert runner.get_test_command(context="commit") == ["make", "ci-local"]

    def test_empty_module_set(self) -> None:
        """Test handling of empty module set from detect_changed_modules."""
        runner = SmartTestRunner()
        runner._get_staged_files = MagicMock(return_value=["README.md"])
        runner._requires_full_ci = MagicMock(return_value=False)
        runner._detect_changed_modules = MagicMock(return_value=set())

        targets = runner.get_test_targets()
        command = runner.get_test_command(context="commit")

        assert targets == []
        assert command == ["echo", "No Python tests needed (no code changes detected)"]

    def test_context_default_value(self) -> None:
        """Test get_test_command with default context parameter."""
        runner = SmartTestRunner()
        runner._get_staged_files = MagicMock(return_value=["libs/allocation/multi_alpha.py"])
        runner._requires_full_ci = MagicMock(return_value=False)
        runner._detect_changed_modules = MagicMock(return_value={"libs/allocation"})

        # Default context should be "commit"
        result = runner.get_test_command()

        assert result == ["poetry", "run", "pytest", "tests/libs/allocation/"]


class TestSmartTestRunnerIntegration:
    """Integration tests using real git_utils functions."""

    @patch("scripts.git_utils.subprocess.run")
    def test_integration_with_real_git_utils(self, mock_run: MagicMock) -> None:
        """Test SmartTestRunner with real git_utils integration."""
        # Mock git diff output with NON-core packages only
        # (libs/ is in CORE_PACKAGES, so use apps/ and strategies/ instead)
        mock_run.return_value = MagicMock(
            stdout="apps/execution_gateway/order_placer.py\n"
            "strategies/alpha_baseline/strategy.py\n"
        )

        runner = SmartTestRunner()

        # Test methods work with real git_utils
        # Should NOT require full CI: <5 modules, no core packages
        assert runner.should_run_full_ci() is False
        assert "tests/apps/execution_gateway/" in runner.get_test_targets()
        assert "tests/strategies/alpha_baseline/" in runner.get_test_targets()

    @patch("scripts.git_utils.subprocess.run")
    def test_integration_core_package_detection(self, mock_run: MagicMock) -> None:
        """Test core package detection with real git_utils."""
        # Mock git diff with core package change
        mock_run.return_value = MagicMock(stdout="libs/common/types.py\n")

        runner = SmartTestRunner()

        # Should require full CI (libs/ is core package)
        assert runner.should_run_full_ci() is True
        assert runner.get_test_command(context="commit") == ["make", "ci-local"]
