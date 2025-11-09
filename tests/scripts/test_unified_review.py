#!/usr/bin/env python3
"""
Test suite for UnifiedReviewSystem in scripts/workflow_gate.py.

Tests the unified review system that consolidates commit and PR reviews
with multi-iteration support and conservative override policy.

Component 4 of P1T13-F4: Workflow Intelligence & Context Efficiency

Author: Claude Code
Date: 2025-11-08
"""

import json
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Import class under test
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from scripts.workflow_gate import UnifiedReviewSystem


@pytest.fixture
def temp_state_file():
    """Create temporary state file for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "workflow-state.json"
        yield state_file


class TestUnifiedReviewSystemInit:
    """Test UnifiedReviewSystem initialization and state management."""

    def test_init_with_default_state_file(self):
        """Test initialization with default state file."""
        reviewer = UnifiedReviewSystem()
        # Should use STATE_FILE constant from workflow_gate.py
        assert reviewer._state_file is not None
        assert reviewer._project_root.name == "trading_platform"

    def test_init_with_custom_state_file(self, temp_state_file):
        """Test initialization with custom state file."""
        reviewer = UnifiedReviewSystem(state_file=temp_state_file)
        assert reviewer._state_file == temp_state_file

    def test_load_state_empty_when_file_not_exists(self, temp_state_file):
        """Test loading state when file doesn't exist returns empty dict."""
        reviewer = UnifiedReviewSystem(state_file=temp_state_file)
        state = reviewer._load_state()
        assert state == {}

    def test_save_and_load_state(self, temp_state_file):
        """Test saving and loading state persists data correctly."""
        reviewer = UnifiedReviewSystem(state_file=temp_state_file)

        test_state = {
            "unified_review": {
                "history": [{"iteration": 1, "status": "APPROVED"}]
            }
        }

        reviewer._save_state(test_state)
        loaded = reviewer._load_state()

        assert loaded == test_state

    def test_save_state_creates_parent_directory(self):
        """Test that save_state creates parent directory if needed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "nested" / "dir" / "state.json"
            reviewer = UnifiedReviewSystem(state_file=state_file)

            reviewer._save_state({"test": "data"})

            assert state_file.exists()
            assert state_file.parent.exists()


class TestCommitReview:
    """Test _commit_review() lightweight commit review."""

    def test_commit_review_returns_pending_status(self, temp_state_file):
        """Test commit review returns PENDING status (actual review via clink)."""
        reviewer = UnifiedReviewSystem(state_file=temp_state_file)
        result = reviewer._commit_review()

        assert result["scope"] == "commit"
        assert result["status"] == "PENDING"
        assert result["continuation_id"] is None
        assert result["issues"] == []

    def test_commit_review_prints_guidance(self, temp_state_file, capsys):
        """Test commit review prints workflow guidance."""
        reviewer = UnifiedReviewSystem(state_file=temp_state_file)
        reviewer._commit_review()

        captured = capsys.readouterr()
        assert "commit review" in captured.out.lower()
        assert "mcp__zen__clink" in captured.out
        assert "gemini" in captured.out
        assert "codex" in captured.out
        assert "2-3 minutes" in captured.out


class TestPRReview:
    """Test _pr_review() comprehensive PR review with multi-iteration."""

    def test_pr_review_iteration_1_returns_pending(self, temp_state_file):
        """Test PR review iteration 1 returns PENDING status."""
        reviewer = UnifiedReviewSystem(state_file=temp_state_file)
        result = reviewer._pr_review(iteration=1)

        assert result["scope"] == "pr"
        assert result["iteration"] == 1
        assert result["status"] == "PENDING"
        assert result["continuation_id"] is None
        assert result["max_iterations"] == 3

    def test_pr_review_iteration_3_shows_max_warning(self, temp_state_file, capsys):
        """Test PR review at max iteration shows override options."""
        reviewer = UnifiedReviewSystem(state_file=temp_state_file)
        reviewer._pr_review(iteration=3)

        captured = capsys.readouterr()
        assert "Max iterations reached (3)" in captured.out
        assert "--override" in captured.out
        assert "--justification" in captured.out

    def test_pr_review_prints_independent_warning_iteration_2(self, temp_state_file, capsys):
        """Test PR review iteration 2+ warns about independent review."""
        reviewer = UnifiedReviewSystem(state_file=temp_state_file)
        reviewer._pr_review(iteration=2)

        captured = capsys.readouterr()
        assert "INDEPENDENT REVIEW" in captured.out
        assert "no memory of iteration 1" in captured.out
        assert "Do NOT reuse continuation_id" in captured.out

    def test_pr_review_with_override_at_iteration_3(self, temp_state_file):
        """Test PR review with override at iteration 3 triggers override handler."""
        reviewer = UnifiedReviewSystem(state_file=temp_state_file)

        # Pre-populate state with review history containing only LOW issues
        state = {
            "unified_review": {
                "history": [{
                    "iteration": 2,
                    "status": "NEEDS_REVISION",
                    "issues": [
                        {"severity": "LOW", "summary": "Minor naming issue"}
                    ]
                }]
            }
        }
        reviewer._save_state(state)

        result = reviewer._pr_review(
            iteration=3,
            override_justification="Low priority naming change, deferring to post-PR"
        )

        # Should invoke override logic
        assert result["status"] == "OVERRIDE_APPROVED"
        assert "low_issues" in result


class TestReviewOverride:
    """Test _handle_review_override() conservative override policy."""

    def test_override_blocks_critical_severity(self, temp_state_file):
        """Test override blocks CRITICAL severity issues."""
        reviewer = UnifiedReviewSystem(state_file=temp_state_file)

        state = {
            "unified_review": {
                "history": [{
                    "iteration": 3,
                    "issues": [
                        {"severity": "CRITICAL", "summary": "Security vulnerability"}
                    ]
                }]
            }
        }

        result = reviewer._handle_review_override(state, 3, "Not applicable")

        assert "error" in result
        assert "Cannot override CRITICAL/HIGH/MEDIUM" in result["error"]
        assert "blocked_issues" in result
        assert len(result["blocked_issues"]) == 1

    def test_override_blocks_high_severity(self, temp_state_file):
        """Test override blocks HIGH severity issues."""
        reviewer = UnifiedReviewSystem(state_file=temp_state_file)

        state = {
            "unified_review": {
                "history": [{
                    "iteration": 3,
                    "issues": [
                        {"severity": "HIGH", "summary": "Major bug"}
                    ]
                }]
            }
        }

        result = reviewer._handle_review_override(state, 3, "Not applicable")

        assert "error" in result
        assert "blocked_issues" in result

    def test_override_blocks_medium_severity(self, temp_state_file):
        """Test override blocks MEDIUM severity issues."""
        reviewer = UnifiedReviewSystem(state_file=temp_state_file)

        state = {
            "unified_review": {
                "history": [{
                    "iteration": 3,
                    "issues": [
                        {"severity": "MEDIUM", "summary": "Moderate concern"}
                    ]
                }]
            }
        }

        result = reviewer._handle_review_override(state, 3, "Not applicable")

        assert "error" in result
        assert "blocked_issues" in result

    def test_override_allows_low_severity(self, temp_state_file):
        """Test override allows LOW severity issues with justification."""
        reviewer = UnifiedReviewSystem(state_file=temp_state_file)

        state = {
            "unified_review": {
                "history": [{
                    "iteration": 3,
                    "issues": [
                        {"severity": "LOW", "summary": "Minor naming suggestion"}
                    ]
                }]
            }
        }

        with patch('subprocess.run'):  # Mock gh pr comment
            result = reviewer._handle_review_override(
                state, 3, "Deferring minor naming changes to post-PR"
            )

        assert result["status"] == "OVERRIDE_APPROVED"
        assert len(result["low_issues"]) == 1
        assert result["override"]["justification"] == "Deferring minor naming changes to post-PR"
        assert result["override"]["policy"] == "block_critical_high_medium_allow_low"

    def test_override_persists_to_state(self, temp_state_file):
        """Test override saves justification and metadata to state."""
        reviewer = UnifiedReviewSystem(state_file=temp_state_file)

        state = {
            "unified_review": {
                "history": [{
                    "iteration": 3,
                    "issues": [
                        {"severity": "LOW", "summary": "Minor issue"}
                    ]
                }]
            }
        }
        reviewer._save_state(state)

        with patch('subprocess.run'):
            reviewer._handle_review_override(
                state, 3, "Test justification"
            )

        # Reload state and verify persistence
        loaded_state = reviewer._load_state()
        assert "override" in loaded_state["unified_review"]
        assert loaded_state["unified_review"]["override"]["justification"] == "Test justification"
        assert loaded_state["unified_review"]["override"]["iteration"] == 3
        assert "timestamp" in loaded_state["unified_review"]["override"]

    def test_override_mixed_severities_blocks_if_any_high(self, temp_state_file):
        """Test override blocks if ANY CRITICAL/HIGH/MEDIUM exist, even with LOW."""
        reviewer = UnifiedReviewSystem(state_file=temp_state_file)

        state = {
            "unified_review": {
                "history": [{
                    "iteration": 3,
                    "issues": [
                        {"severity": "LOW", "summary": "Minor issue"},
                        {"severity": "MEDIUM", "summary": "Moderate concern"},
                        {"severity": "LOW", "summary": "Another minor issue"}
                    ]
                }]
            }
        }

        result = reviewer._handle_review_override(state, 3, "Justification")

        # Should block due to MEDIUM severity
        assert "error" in result
        assert len(result["blocked_issues"]) == 1
        assert result["blocked_issues"][0]["severity"] == "MEDIUM"

    def test_override_no_history_returns_error(self, temp_state_file):
        """Test override without review history returns error."""
        reviewer = UnifiedReviewSystem(state_file=temp_state_file)

        state = {"unified_review": {"history": []}}

        result = reviewer._handle_review_override(state, 3, "Justification")

        assert "error" in result
        assert "No review history found" in result["error"]

    def test_override_no_issues_returns_approved(self, temp_state_file):
        """Test override with no issues returns APPROVED."""
        reviewer = UnifiedReviewSystem(state_file=temp_state_file)

        state = {
            "unified_review": {
                "history": [{
                    "iteration": 3,
                    "issues": []
                }]
            }
        }

        result = reviewer._handle_review_override(state, 3, "N/A")

        assert result["status"] == "APPROVED"
        assert result["issues"] == []

    def test_override_logs_to_pr_via_gh(self, temp_state_file):
        """Test override attempts to log justification to PR via gh pr comment."""
        reviewer = UnifiedReviewSystem(state_file=temp_state_file)

        state = {
            "unified_review": {
                "history": [{
                    "iteration": 3,
                    "issues": [
                        {"severity": "LOW", "summary": "Minor issue"}
                    ]
                }]
            }
        }

        with patch('subprocess.run') as mock_run:
            reviewer._handle_review_override(state, 3, "Test justification")

            # Verify gh pr comment was called
            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            assert args[0] == "gh"
            assert args[1] == "pr"
            assert args[2] == "comment"
            assert "REVIEW OVERRIDE" in args[4]
            assert "Test justification" in args[4]


class TestRequestReview:
    """Test request_review() entry point with scope validation."""

    def test_request_review_commit_scope(self, temp_state_file):
        """Test request_review routes to commit review correctly."""
        reviewer = UnifiedReviewSystem(state_file=temp_state_file)
        result = reviewer.request_review(scope="commit")

        assert result["scope"] == "commit"
        assert result["status"] == "PENDING"

    def test_request_review_pr_scope(self, temp_state_file):
        """Test request_review routes to PR review correctly."""
        reviewer = UnifiedReviewSystem(state_file=temp_state_file)
        result = reviewer.request_review(scope="pr", iteration=1)

        assert result["scope"] == "pr"
        assert result["iteration"] == 1
        assert result["status"] == "PENDING"

    def test_request_review_invalid_scope_raises_error(self, temp_state_file):
        """Test request_review raises ValueError for invalid scope."""
        reviewer = UnifiedReviewSystem(state_file=temp_state_file)

        with pytest.raises(ValueError) as excinfo:
            reviewer.request_review(scope="invalid")

        assert "Invalid scope: invalid" in str(excinfo.value)
        assert "Must be 'commit' or 'pr'" in str(excinfo.value)

    def test_request_review_pr_with_override(self, temp_state_file):
        """Test request_review passes override to PR review."""
        reviewer = UnifiedReviewSystem(state_file=temp_state_file)

        # Pre-populate state
        state = {
            "unified_review": {
                "history": [{
                    "iteration": 2,
                    "issues": [{"severity": "LOW", "summary": "Minor"}]
                }]
            }
        }
        reviewer._save_state(state)

        with patch('subprocess.run'):
            result = reviewer.request_review(
                scope="pr",
                iteration=3,
                override_justification="Test override"
            )

        assert result["status"] == "OVERRIDE_APPROVED"

    def test_request_review_default_iteration_is_1(self, temp_state_file):
        """Test request_review defaults to iteration 1 for PR scope."""
        reviewer = UnifiedReviewSystem(state_file=temp_state_file)
        result = reviewer.request_review(scope="pr")

        assert result["iteration"] == 1


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_multiple_low_severity_issues(self, temp_state_file):
        """Test override correctly counts multiple LOW severity issues."""
        reviewer = UnifiedReviewSystem(state_file=temp_state_file)

        state = {
            "unified_review": {
                "history": [{
                    "iteration": 3,
                    "issues": [
                        {"severity": "LOW", "summary": "Issue 1"},
                        {"severity": "LOW", "summary": "Issue 2"},
                        {"severity": "LOW", "summary": "Issue 3"}
                    ]
                }]
            }
        }

        with patch('subprocess.run'):
            result = reviewer._handle_review_override(state, 3, "Defer all")

        assert result["status"] == "OVERRIDE_APPROVED"
        assert len(result["low_issues"]) == 3
        assert result["override"]["low_issues_count"] == 3

    def test_unknown_severity_is_not_blocked(self, temp_state_file):
        """Test issues with unknown/missing severity are allowed to override."""
        reviewer = UnifiedReviewSystem(state_file=temp_state_file)

        state = {
            "unified_review": {
                "history": [{
                    "iteration": 3,
                    "issues": [
                        {"summary": "Issue without severity"}  # Missing severity field
                    ]
                }]
            }
        }

        result = reviewer._handle_review_override(state, 3, "Justification")

        # Should not be in low_issues or blocked_issues (UNKNOWN severity)
        # Returns APPROVED since it's not LOW and not in blocked categories
        assert result["status"] == "APPROVED"

    def test_iteration_boundary_2_to_3(self, temp_state_file, capsys):
        """Test transition from iteration 2 to 3 shows max warning."""
        reviewer = UnifiedReviewSystem(state_file=temp_state_file)

        # Iteration 2: no warning
        reviewer._pr_review(iteration=2)
        captured_2 = capsys.readouterr()
        assert "Max iterations reached" not in captured_2.out

        # Iteration 3: max warning
        reviewer._pr_review(iteration=3)
        captured_3 = capsys.readouterr()
        assert "Max iterations reached (3)" in captured_3.out

    def test_override_flag_before_max_iterations_ignored(self, temp_state_file):
        """Test override flag at iteration < 3 does not trigger override logic."""
        reviewer = UnifiedReviewSystem(state_file=temp_state_file)

        # Pre-populate state with review history
        state = {
            "unified_review": {
                "history": [{
                    "iteration": 1,
                    "issues": [{"severity": "LOW", "summary": "Minor issue"}]
                }]
            }
        }
        reviewer._save_state(state)

        # Request PR review with override at iteration 2 (< 3)
        result = reviewer._pr_review(
            iteration=2,
            override_justification="Attempting early override"
        )

        # Should NOT trigger override logic (iteration < 3)
        assert result["status"] == "PENDING"
        assert "override" not in result


# Mark as unit test
pytestmark = pytest.mark.unit
