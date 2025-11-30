"""
CLI integration tests for workflow_gate.py main() function.

Tests the command-line interface by mocking sys.argv and invoking main().
Catches bugs in argparse configuration and command dispatch logic.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add scripts to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))

from workflow_gate import PROJECT_ROOT, main


class TestWorkflowGateCLI:
    """Integration tests for workflow_gate CLI commands."""

    def test_no_command_shows_help(self, capsys):
        """Test that running with no command shows help and exits with code 1."""
        with patch.object(sys, "argv", ["workflow_gate.py"]):
            exit_code = main()

        assert exit_code == 1
        captured = capsys.readouterr()
        assert "usage:" in captured.out.lower()

    def test_status_command(self, tmp_path):
        """Test 'status' command invokes WorkflowGate.show_status()."""
        with patch("workflow_gate.WorkflowGate") as MockGate:
            mock_gate_instance = MagicMock()
            MockGate.return_value = mock_gate_instance

            with patch.object(sys, "argv", ["workflow_gate.py", "status"]):
                exit_code = main()

            assert exit_code == 0
            mock_gate_instance.show_status.assert_called_once()

    def test_set_component_command(self, tmp_path):
        """Test 'set-component' command calls WorkflowGate.set_component()."""
        with patch("workflow_gate.WorkflowGate") as MockGate:
            mock_gate_instance = MagicMock()
            MockGate.return_value = mock_gate_instance

            with patch.object(sys, "argv", ["workflow_gate.py", "set-component", "TestComponent"]):
                exit_code = main()

            assert exit_code == 0
            mock_gate_instance.set_component.assert_called_once_with("TestComponent")

    def test_run_ci_commit_command(self):
        """Test 'run-ci commit' command instantiates SmartTestRunner and runs tests."""
        with patch("workflow_gate.WorkflowGate"):
            with patch("workflow_gate.SmartTestRunner") as MockRunner:
                with patch("workflow_gate.subprocess.run") as mock_subprocess:
                    mock_runner_instance = MagicMock()
                    MockRunner.return_value = mock_runner_instance
                    mock_runner_instance.get_test_command.return_value = ["pytest", "tests/"]
                    mock_subprocess.return_value = MagicMock(returncode=0)

                    with patch.object(sys, "argv", ["workflow_gate.py", "run-ci", "commit"]):
                        exit_code = main()

                    assert exit_code == 0
                    MockRunner.assert_called_once()  # No arguments
                    mock_runner_instance.get_test_command.assert_called_once_with(context="commit")
                    mock_subprocess.assert_called_once_with(["pytest", "tests/"], cwd=PROJECT_ROOT)

    def test_run_ci_pr_command(self):
        """Test 'run-ci pr' command runs full test suite."""
        with patch("workflow_gate.WorkflowGate"):
            with patch("workflow_gate.SmartTestRunner") as MockRunner:
                with patch("workflow_gate.subprocess.run") as mock_subprocess:
                    mock_runner_instance = MagicMock()
                    MockRunner.return_value = mock_runner_instance
                    mock_runner_instance.get_test_command.return_value = ["make", "ci-local"]
                    mock_subprocess.return_value = MagicMock(returncode=0)

                    with patch.object(sys, "argv", ["workflow_gate.py", "run-ci", "pr"]):
                        exit_code = main()

                    assert exit_code == 0
                    mock_runner_instance.get_test_command.assert_called_once_with(context="pr")
                    mock_subprocess.assert_called_once_with(["make", "ci-local"], cwd=PROJECT_ROOT)

    def test_create_task_command(self):
        """Test 'create-task' command calls PlanningWorkflow.create_task_with_review()."""
        with patch("workflow_gate.WorkflowGate") as MockGate:
            with patch("workflow_gate.PlanningWorkflow") as MockPlanning:
                mock_planning_instance = MagicMock()
                MockPlanning.return_value = mock_planning_instance
                mock_planning_instance.create_task_with_review.return_value = "cont-id-123"

                with patch.object(
                    sys,
                    "argv",
                    [
                        "workflow_gate.py",
                        "create-task",
                        "--id",
                        "P1T99",
                        "--title",
                        "Test Task",
                        "--description",
                        "Test description",
                        "--hours",
                        "5.0",
                    ],
                ):
                    exit_code = main()

                assert exit_code == 0
                mock_planning_instance.create_task_with_review.assert_called_once_with(
                    task_id="P1T99",
                    title="Test Task",
                    description="Test description",
                    estimated_hours=5.0,
                )

    def test_start_task_command(self):
        """Test 'start-task' command calls PlanningWorkflow.start_task_with_state()."""
        with patch("workflow_gate.WorkflowGate") as MockGate:
            with patch("workflow_gate.PlanningWorkflow") as MockPlanning:
                mock_planning_instance = MagicMock()
                MockPlanning.return_value = mock_planning_instance

                with patch.object(
                    sys, "argv", ["workflow_gate.py", "start-task", "P1T99", "feature/test-branch"]
                ):
                    exit_code = main()

                assert exit_code == 0
                mock_planning_instance.start_task_with_state.assert_called_once_with(
                    task_id="P1T99", branch_name="feature/test-branch"
                )

    def test_check_context_command(self):
        """Test 'check-context' command uses DelegationRules correctly."""
        with patch("workflow_gate.WorkflowGate"):
            with patch("workflow_gate.DelegationRules") as MockRules:
                mock_rules_instance = MagicMock()
                MockRules.return_value = mock_rules_instance
                mock_rules_instance.get_context_snapshot.return_value = {"usage_pct": 50.0}
                mock_rules_instance.should_delegate_context.return_value = (
                    False,
                    "OK - 50.0%",
                    None,
                )
                mock_rules_instance.format_status.return_value = "Context: OK"

                with patch.object(sys, "argv", ["workflow_gate.py", "check-context"]):
                    exit_code = main()

                assert exit_code == 0
                mock_rules_instance.should_delegate_context.assert_called_once()
                mock_rules_instance.format_status.assert_called_once()

    def test_debug_rescue_command(self):
        """Test 'debug-rescue' command calls DebugRescue.request_debug_rescue()."""
        with patch("workflow_gate.WorkflowGate"):
            with patch("workflow_gate.DebugRescue") as MockDebug:
                mock_debug_instance = MagicMock()
                MockDebug.return_value = mock_debug_instance
                mock_debug_instance.request_debug_rescue.return_value = {"success": True}

                with patch.object(sys, "argv", ["workflow_gate.py", "debug-rescue"]):
                    exit_code = main()

                assert exit_code == 0
                mock_debug_instance.request_debug_rescue.assert_called_once_with(test_file=None)

    def test_advance_command(self):
        """Test 'advance' command calls WorkflowGate.advance()."""
        with patch("workflow_gate.WorkflowGate") as MockGate:
            mock_gate_instance = MagicMock()
            MockGate.return_value = mock_gate_instance

            with patch.object(sys, "argv", ["workflow_gate.py", "advance", "test"]):
                exit_code = main()

            assert exit_code == 0
            mock_gate_instance.advance.assert_called_once_with("test")

    def test_record_review_command(self):
        """Test 'record-review' command calls WorkflowGate.record_review()."""
        with patch("workflow_gate.WorkflowGate") as MockGate:
            mock_gate_instance = MagicMock()
            MockGate.return_value = mock_gate_instance

            with patch.object(
                sys, "argv", ["workflow_gate.py", "record-review", "cont-123", "APPROVED"]
            ):
                exit_code = main()

            assert exit_code == 0
            # Defaults to "codex" when cli_name not provided
            mock_gate_instance.record_review.assert_called_once_with(
                "cont-123", "APPROVED", "codex"
            )

    def test_record_ci_command(self):
        """Test 'record-ci' command calls WorkflowGate.record_ci()."""
        with patch("workflow_gate.WorkflowGate") as MockGate:
            mock_gate_instance = MagicMock()
            MockGate.return_value = mock_gate_instance

            with patch.object(sys, "argv", ["workflow_gate.py", "record-ci", "true"]):
                exit_code = main()

            assert exit_code == 0
            mock_gate_instance.record_ci.assert_called_once_with(True)

    def test_check_commit_command(self):
        """Test 'check-commit' command calls WorkflowGate.check_commit()."""
        with patch("workflow_gate.WorkflowGate") as MockGate:
            mock_gate_instance = MagicMock()
            MockGate.return_value = mock_gate_instance
            mock_gate_instance.check_commit.return_value = True

            with patch.object(sys, "argv", ["workflow_gate.py", "check-commit"]):
                exit_code = main()

            assert exit_code == 0
            mock_gate_instance.check_commit.assert_called_once()

    def test_record_delegation_command(self):
        """Test 'record-delegation' command calls DelegationRules.record_delegation()."""
        with patch("workflow_gate.WorkflowGate"):
            with patch("workflow_gate.DelegationRules") as MockRules:
                mock_rules_instance = MagicMock()
                MockRules.return_value = mock_rules_instance
                mock_rules_instance.record_delegation.return_value = {
                    "task_description": "Test delegation",
                    "timestamp": "2025-11-09T00:00:00Z",
                }

                with patch.object(
                    sys, "argv", ["workflow_gate.py", "record-delegation", "Test delegation task"]
                ):
                    exit_code = main()

                assert exit_code == 0
                mock_rules_instance.record_delegation.assert_called_once_with(
                    "Test delegation task"
                )

    def test_request_review_commit_command(self):
        """Test 'request-review commit' command calls UnifiedReviewSystem.request_review()."""
        with patch("workflow_gate.WorkflowGate"):
            with patch("workflow_gate.UnifiedReviewSystem") as MockReview:
                mock_review_instance = MagicMock()
                MockReview.return_value = mock_review_instance
                mock_review_instance.request_review.return_value = {}

                with patch.object(sys, "argv", ["workflow_gate.py", "request-review", "commit"]):
                    exit_code = main()

                assert exit_code == 0
                mock_review_instance.request_review.assert_called_once_with(
                    scope="commit", iteration=1, override_justification=None
                )


class TestCLIErrorHandling:
    """Test error handling in CLI commands."""

    def test_run_ci_failure_returns_error_code(self):
        """Test that failed CI run returns exit code 1."""
        with patch("workflow_gate.WorkflowGate"):
            with patch("workflow_gate.SmartTestRunner") as MockRunner:
                with patch("workflow_gate.subprocess.run") as mock_subprocess:
                    mock_runner_instance = MagicMock()
                    MockRunner.return_value = mock_runner_instance
                    # Return list (matches production behavior after iteration 6 fixes)
                    mock_runner_instance.get_test_command.return_value = [
                        "poetry",
                        "run",
                        "pytest",
                        "tests/",
                    ]
                    mock_subprocess.return_value = MagicMock(returncode=1)  # Failure

                    with patch.object(sys, "argv", ["workflow_gate.py", "run-ci", "commit"]):
                        exit_code = main()

                    assert exit_code == 1
                    # Verify subprocess.run was called with list (not string)
                    mock_subprocess.assert_called_once()
                    assert isinstance(mock_subprocess.call_args[0][0], list)

    def test_create_task_missing_required_args(self):
        """Test that create-task without required args exits with error."""
        with patch.object(sys, "argv", ["workflow_gate.py", "create-task"]):
            with pytest.raises(SystemExit) as exc_info:
                main()

            assert exc_info.value.code != 0

    def test_start_task_missing_branch_name(self):
        """Test that start-task without branch_name exits with error."""
        with patch.object(sys, "argv", ["workflow_gate.py", "start-task", "P1T99"]):
            with pytest.raises(SystemExit) as exc_info:
                main()

            assert exc_info.value.code != 0
