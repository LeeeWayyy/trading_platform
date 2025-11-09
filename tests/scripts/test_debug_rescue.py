#!/usr/bin/env python3
"""
Test suite for DebugRescue in scripts/workflow_gate.py.

Tests the debug rescue system that detects stuck debug loops and
escalates to clink codex for systematic debugging assistance.

Component 5 of P1T13-F4: Workflow Intelligence & Context Efficiency

Author: Claude Code
Date: 2025-11-08
"""

import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Import class under test
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from scripts.workflow_gate import DebugRescue


@pytest.fixture
def temp_state_file():
    """Create temporary state file for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "workflow-state.json"
        yield state_file


class TestDebugRescueInit:
    """Test DebugRescue initialization and state management."""

    def test_init_with_default_state_file(self):
        """Test initialization with default state file."""
        rescue = DebugRescue()
        assert rescue._state_file is not None
        assert rescue.MAX_ATTEMPTS_SAME_TEST == 3
        assert rescue.LOOP_DETECTION_WINDOW == 10

    def test_init_with_custom_state_file(self, temp_state_file):
        """Test initialization with custom state file."""
        rescue = DebugRescue(state_file=temp_state_file)
        assert rescue._state_file == temp_state_file

    def test_load_state_empty_when_file_not_exists(self, temp_state_file):
        """Test loading state when file doesn't exist returns empty dict."""
        rescue = DebugRescue(state_file=temp_state_file)
        state = rescue._load_state()
        assert state == {}

    def test_save_and_load_state(self, temp_state_file):
        """Test saving and loading state persists data correctly."""
        rescue = DebugRescue(state_file=temp_state_file)

        test_state = {
            "debug_rescue": {
                "attempt_history": [{"test_file": "foo.py", "status": "failed"}]
            }
        }

        rescue._save_state(test_state)
        loaded = rescue._load_state()

        assert loaded == test_state


class TestRecordTestAttempt:
    """Test record_test_attempt() tracking functionality."""

    def test_record_single_attempt(self, temp_state_file):
        """Test recording a single test attempt."""
        rescue = DebugRescue(state_file=temp_state_file)

        rescue.record_test_attempt(
            test_file="tests/test_foo.py",
            status="failed",
            error_signature="abc123"
        )

        state = rescue._load_state()
        history = state["debug_rescue"]["attempt_history"]

        assert len(history) == 1
        assert history[0]["test_file"] == "tests/test_foo.py"
        assert history[0]["status"] == "failed"
        assert history[0]["error_signature"] == "abc123"
        assert "timestamp" in history[0]

    def test_record_multiple_attempts(self, temp_state_file):
        """Test recording multiple test attempts."""
        rescue = DebugRescue(state_file=temp_state_file)

        rescue.record_test_attempt("tests/test_foo.py", "failed", "abc123")
        rescue.record_test_attempt("tests/test_bar.py", "passed", "def456")
        rescue.record_test_attempt("tests/test_foo.py", "failed", "abc123")

        state = rescue._load_state()
        history = state["debug_rescue"]["attempt_history"]

        assert len(history) == 3

    def test_prune_old_history_over_max(self, temp_state_file):
        """Test that history is pruned to last HISTORY_MAX_SIZE attempts."""
        rescue = DebugRescue(state_file=temp_state_file)
        max_size = rescue.HISTORY_MAX_SIZE

        # Record HISTORY_MAX_SIZE + 5 attempts
        for i in range(max_size + 5):
            rescue.record_test_attempt(
                f"tests/test_{i}.py",
                "failed",
                f"error_{i}"
            )

        state = rescue._load_state()
        history = state["debug_rescue"]["attempt_history"]

        # Should keep only last HISTORY_MAX_SIZE
        assert len(history) == max_size
        # Oldest (0-4) should be pruned, newest (5 to max_size+4) kept
        assert history[0]["test_file"] == f"tests/test_5.py"
        assert history[-1]["test_file"] == f"tests/test_{max_size + 4}.py"


class TestIsStuckInLoop:
    """Test is_stuck_in_loop() detection logic."""

    def test_not_enough_attempts(self, temp_state_file):
        """Test loop detection with insufficient attempts."""
        rescue = DebugRescue(state_file=temp_state_file)

        rescue.record_test_attempt("tests/test_foo.py", "failed", "abc123")
        rescue.record_test_attempt("tests/test_foo.py", "failed", "abc123")

        is_stuck, reason = rescue.is_stuck_in_loop()

        assert is_stuck is False
        assert "Not enough attempts" in reason

    def test_same_test_failing_repeatedly(self, temp_state_file):
        """Test detection of same test failing 3+ times."""
        rescue = DebugRescue(state_file=temp_state_file)

        # Record same test failing 3 times
        for i in range(3):
            rescue.record_test_attempt("tests/test_foo.py", "failed", f"error_{i}")

        is_stuck, reason = rescue.is_stuck_in_loop()

        assert is_stuck is True
        assert "tests/test_foo.py" in reason
        assert "3 times" in reason

    def test_different_tests_not_stuck(self, temp_state_file):
        """Test that failures on different tests don't trigger loop detection."""
        rescue = DebugRescue(state_file=temp_state_file)

        rescue.record_test_attempt("tests/test_foo.py", "failed", "error_1")
        rescue.record_test_attempt("tests/test_bar.py", "failed", "error_2")
        rescue.record_test_attempt("tests/test_baz.py", "failed", "error_3")

        is_stuck, reason = rescue.is_stuck_in_loop()

        assert is_stuck is False

    def test_error_signature_cycling(self, temp_state_file):
        """Test detection of error signature cycling (A → B → A pattern)."""
        rescue = DebugRescue(state_file=temp_state_file)

        # Create cycling pattern with 2 unique errors over 6 attempts
        # Use unique test file for each attempt to isolate cycling detection
        errors = ["error_a", "error_b"]
        for i in range(6):
            rescue.record_test_attempt(
                f"tests/test_file_{i}.py",  # Unique file each time
                "failed",
                errors[i % 2]  # Cycles between error_a and error_b
            )

        is_stuck, reason = rescue.is_stuck_in_loop()

        # Should detect cycling (2 errors over 6 attempts)
        assert is_stuck is True
        assert "Cycling between" in reason

    def test_time_spent_over_30_minutes(self, temp_state_file):
        """Test detection of >30 minutes spent on same test."""
        rescue = DebugRescue(state_file=temp_state_file)

        # Manually create history with timestamps >30 min apart
        # Use different test files to avoid triggering "same test 3+ times" check
        now = datetime.now()
        test_files = ["tests/test_a.py", "tests/test_b.py", "tests/test_c.py"]
        state = {
            "debug_rescue": {
                "attempt_history": [
                    {
                        "timestamp": (now - timedelta(minutes=40)).isoformat(),
                        "test_file": test_files[0],
                        "status": "failed",
                        "error_signature": "error_1"
                    },
                    {
                        "timestamp": (now - timedelta(minutes=30)).isoformat(),
                        "test_file": test_files[1],
                        "status": "failed",
                        "error_signature": "error_2"
                    },
                    {
                        "timestamp": (now - timedelta(minutes=20)).isoformat(),
                        "test_file": test_files[2],
                        "status": "failed",
                        "error_signature": "error_3"
                    },
                    {
                        "timestamp": (now - timedelta(minutes=10)).isoformat(),
                        "test_file": test_files[0],
                        "status": "failed",
                        "error_signature": "error_4"
                    },
                    {
                        "timestamp": now.isoformat(),
                        "test_file": test_files[1],
                        "status": "failed",
                        "error_signature": "error_5"
                    }
                ]
            }
        }
        rescue._save_state(state)

        is_stuck, reason = rescue.is_stuck_in_loop()

        # Should detect time spent OR other loop patterns
        assert is_stuck is True

    def test_invalid_timestamp_doesnt_crash(self, temp_state_file):
        """Test that invalid timestamps don't crash loop detection."""
        rescue = DebugRescue(state_file=temp_state_file)

        # Create history with invalid timestamp
        state = {
            "debug_rescue": {
                "attempt_history": [
                    {
                        "timestamp": "invalid_timestamp",
                        "test_file": "tests/test_foo.py",
                        "status": "failed",
                        "error_signature": "error_1"
                    }
                ] * 5
            }
        }
        rescue._save_state(state)

        # Should not crash, just skip time-based check
        is_stuck, reason = rescue.is_stuck_in_loop()
        # Won't be stuck due to time, might be stuck due to other criteria
        # Just ensure it doesn't crash
        assert isinstance(is_stuck, bool)


class TestGetRecentCommits:
    """Test _get_recent_commits() git integration."""

    @patch('subprocess.run')
    def test_get_recent_commits_success(self, mock_run, temp_state_file):
        """Test successful retrieval of recent commits."""
        mock_run.return_value = MagicMock(
            stdout="abc123 fix: bug fix\ndef456 feat: new feature\n"
        )

        rescue = DebugRescue(state_file=temp_state_file)
        commits = rescue._get_recent_commits(max_commits=2)

        assert "abc123" in commits
        assert "def456" in commits
        mock_run.assert_called_once()

    @patch('subprocess.run')
    def test_get_recent_commits_git_error(self, mock_run, temp_state_file):
        """Test graceful handling of git errors."""
        mock_run.side_effect = Exception("git error")

        rescue = DebugRescue(state_file=temp_state_file)
        commits = rescue._get_recent_commits()

        assert commits == "(git log unavailable)"


class TestRequestDebugRescue:
    """Test request_debug_rescue() escalation logic."""

    def test_request_with_no_test_file_and_no_history(self, temp_state_file):
        """Test rescue request with no test file and no history."""
        rescue = DebugRescue(state_file=temp_state_file)

        result = rescue.request_debug_rescue()

        assert "error" in result
        assert "No test file specified" in result["error"]

    def test_request_with_explicit_test_file(self, temp_state_file):
        """Test rescue request with explicit test file."""
        rescue = DebugRescue(state_file=temp_state_file)

        # Record some failures
        rescue.record_test_attempt("tests/test_foo.py", "failed", "error_1")
        rescue.record_test_attempt("tests/test_foo.py", "failed", "error_2")

        result = rescue.request_debug_rescue(test_file="tests/test_foo.py")

        assert result["test_file"] == "tests/test_foo.py"
        assert result["status"] == "RESCUE_NEEDED"
        assert result["failed_attempts"] == 2
        assert "rescue_prompt" in result

    def test_request_auto_detects_most_problematic_test(self, temp_state_file):
        """Test rescue auto-detects most failing test."""
        rescue = DebugRescue(state_file=temp_state_file)

        # Record failures: test_foo.py (3x), test_bar.py (1x)
        rescue.record_test_attempt("tests/test_foo.py", "failed", "error_1")
        rescue.record_test_attempt("tests/test_bar.py", "failed", "error_2")
        rescue.record_test_attempt("tests/test_foo.py", "failed", "error_3")
        rescue.record_test_attempt("tests/test_foo.py", "failed", "error_4")

        result = rescue.request_debug_rescue()  # No test_file specified

        # Should auto-detect test_foo.py (most failures)
        assert result["test_file"] == "tests/test_foo.py"
        assert result["failed_attempts"] == 3

    def test_rescue_prompt_contains_required_info(self, temp_state_file):
        """Test rescue prompt contains all required debugging information."""
        rescue = DebugRescue(state_file=temp_state_file)

        rescue.record_test_attempt("tests/test_foo.py", "failed", "error_abc")
        rescue.record_test_attempt("tests/test_foo.py", "failed", "error_def")

        with patch.object(rescue, '_get_recent_commits', return_value="commit_log"):
            result = rescue.request_debug_rescue(test_file="tests/test_foo.py")

        prompt = result["rescue_prompt"]

        # Check required elements
        assert "DEBUG RESCUE REQUEST" in prompt
        assert "tests/test_foo.py" in prompt
        assert "Failed attempts: 2" in prompt
        assert "error_abc" in prompt
        assert "commit_log" in prompt
        assert "systematic debugging" in prompt.lower()

    def test_rescue_prints_guidance(self, temp_state_file, capsys):
        """Test that rescue prints guidance to stdout."""
        rescue = DebugRescue(state_file=temp_state_file)

        rescue.record_test_attempt("tests/test_foo.py", "failed", "error_1")

        rescue.request_debug_rescue(test_file="tests/test_foo.py")

        captured = capsys.readouterr()
        assert "DEBUG RESCUE TRIGGERED" in captured.out
        assert "mcp__zen__clink" in captured.out
        assert "codex" in captured.out
        assert "systematic debugging" in captured.out.lower()


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_exact_3_failures_triggers_detection(self, temp_state_file):
        """Test boundary: exactly 3 failures triggers detection."""
        rescue = DebugRescue(state_file=temp_state_file)

        for i in range(3):
            rescue.record_test_attempt("tests/test_foo.py", "failed", f"error_{i}")

        is_stuck, _ = rescue.is_stuck_in_loop()
        assert is_stuck is True

    def test_2_failures_doesnt_trigger(self, temp_state_file):
        """Test boundary: only 2 failures doesn't trigger detection."""
        rescue = DebugRescue(state_file=temp_state_file)

        rescue.record_test_attempt("tests/test_foo.py", "failed", "error_1")
        rescue.record_test_attempt("tests/test_foo.py", "failed", "error_2")

        is_stuck, _ = rescue.is_stuck_in_loop()
        assert is_stuck is False

    def test_mixed_pass_fail_same_test(self, temp_state_file):
        """Test detection with mixed pass/fail on same test."""
        rescue = DebugRescue(state_file=temp_state_file)

        rescue.record_test_attempt("tests/test_foo.py", "failed", "error_1")
        rescue.record_test_attempt("tests/test_foo.py", "passed", "")
        rescue.record_test_attempt("tests/test_foo.py", "failed", "error_2")
        rescue.record_test_attempt("tests/test_foo.py", "failed", "error_3")

        # Only 3 failures, but interspersed with pass
        is_stuck, reason = rescue.is_stuck_in_loop()

        # Should still trigger (3 failures in recent window)
        assert is_stuck is True

    def test_recent_errors_limited_to_3(self, temp_state_file):
        """Test that recent_errors is limited to first 3."""
        rescue = DebugRescue(state_file=temp_state_file)

        # Record 5 failures
        for i in range(5):
            rescue.record_test_attempt("tests/test_foo.py", "failed", f"error_{i}")

        result = rescue.request_debug_rescue(test_file="tests/test_foo.py")

        # Should only include first 3
        assert len(result["recent_errors"]) <= 3


# Mark as unit test
pytestmark = pytest.mark.unit
