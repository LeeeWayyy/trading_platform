"""
Tests for pr_workflow.py module.

Tests PRWorkflowHandler for PR review phase state machine.
"""

import json
from unittest.mock import MagicMock, patch

from ai_workflow.pr_workflow import (
    CIStatus,
    PRWorkflowHandler,
)


class TestCIStatus:
    """Tests for CIStatus enum."""

    def test_pending(self):
        """Should have PENDING status."""
        assert CIStatus.PENDING.value == "pending"

    def test_running(self):
        """Should have RUNNING status."""
        assert CIStatus.RUNNING.value == "running"

    def test_passed(self):
        """Should have PASSED status."""
        assert CIStatus.PASSED.value == "passed"

    def test_failed(self):
        """Should have FAILED status."""
        assert CIStatus.FAILED.value == "failed"

    def test_error(self):
        """Should have ERROR status."""
        assert CIStatus.ERROR.value == "error"


class TestPRWorkflowHandlerInit:
    """Tests for PRWorkflowHandler initialization."""

    def test_initializes_with_state(self, temp_dir):
        """Should initialize with provided state."""
        state = {}
        config_file = temp_dir / ".ai_workflow" / "config.json"

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            with patch("ai_workflow.config.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                handler = PRWorkflowHandler(state)

        assert handler.state is state

    def test_ensures_pr_state(self, temp_dir):
        """Should ensure pr_review state structure exists."""
        state = {}
        config_file = temp_dir / ".ai_workflow" / "config.json"

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            with patch("ai_workflow.config.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                PRWorkflowHandler(state)

        assert "pr_review" in state
        assert state["pr_review"]["step"] == "pr-pending"
        assert state["pr_review"]["iteration"] == 0


class TestPRSteps:
    """Tests for PR step constants."""

    def test_valid_steps(self, temp_dir):
        """Should have all expected steps."""
        expected = [
            "pr-pending",
            "pr-review-check",
            "pr-review-fix",
            "pr-local-review",
            "pr-local-test",
            "pr-commit",
            "pr-commit-failed",
            "pr-approved",
            "pr-ready",
            "merged",
        ]
        assert PRWorkflowHandler.PR_STEPS == expected

    def test_valid_transitions(self, temp_dir):
        """Should have valid transitions defined."""
        transitions = PRWorkflowHandler.VALID_TRANSITIONS

        assert transitions["pr-pending"] == ["pr-review-check"]
        assert "pr-review-fix" in transitions["pr-review-check"]
        assert "pr-approved" in transitions["pr-review-check"]
        assert transitions["pr-approved"] == ["pr-ready"]
        assert transitions["pr-ready"] == ["merged"]


class TestStartPRPhase:
    """Tests for start_pr_phase method."""

    def test_sets_pr_url(self, temp_dir):
        """Should set PR URL in state."""
        state = {}
        config_file = temp_dir / ".ai_workflow" / "config.json"

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            with patch("ai_workflow.config.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                handler = PRWorkflowHandler(state)
                result = handler.start_pr_phase(
                    pr_url="https://github.com/owner/repo/pull/123", pr_number=123
                )

        assert result is True
        assert state["phase"] == "pr-review"
        assert state["pr_review"]["pr_url"] == "https://github.com/owner/repo/pull/123"
        assert state["pr_review"]["pr_number"] == 123

    def test_resets_pr_state(self, temp_dir):
        """Should reset PR state when starting new phase."""
        state = {
            "pr_review": {
                "step": "pr-approved",
                "iteration": 5,
            }
        }
        config_file = temp_dir / ".ai_workflow" / "config.json"

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            with patch("ai_workflow.config.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                handler = PRWorkflowHandler(state)
                handler.start_pr_phase()

        assert state["pr_review"]["step"] == "pr-pending"
        assert state["pr_review"]["iteration"] == 0


class TestAdvanceStep:
    """Tests for advance_step method."""

    def test_valid_transition(self, temp_dir):
        """Should allow valid transitions."""
        state = {"pr_review": {"step": "pr-pending"}}
        config_file = temp_dir / ".ai_workflow" / "config.json"

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            with patch("ai_workflow.config.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                handler = PRWorkflowHandler(state)
                success, message = handler.advance_step("pr-review-check")

        assert success is True
        assert state["pr_review"]["step"] == "pr-review-check"

    def test_invalid_transition(self, temp_dir):
        """Should reject invalid transitions."""
        state = {"pr_review": {"step": "pr-pending"}}
        config_file = temp_dir / ".ai_workflow" / "config.json"

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            with patch("ai_workflow.config.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                handler = PRWorkflowHandler(state)
                success, message = handler.advance_step("merged")

        assert success is False
        assert "Invalid transition" in message

    def test_unknown_current_step(self, temp_dir):
        """Should handle unknown current step."""
        state = {"pr_review": {"step": "unknown-step"}}
        config_file = temp_dir / ".ai_workflow" / "config.json"

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            with patch("ai_workflow.config.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                handler = PRWorkflowHandler(state)
                success, message = handler.advance_step("pr-review-check")

        assert success is False
        assert "Unknown" in message


class TestCheckPRStatus:
    """Tests for check_pr_status method."""

    def test_returns_error_without_pr_number(self, temp_dir):
        """Should return error when no PR number set."""
        state = {"pr_review": {"step": "pr-pending"}, "reviewers": {}}
        config_file = temp_dir / ".ai_workflow" / "config.json"

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            with patch("ai_workflow.config.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                handler = PRWorkflowHandler(state)
                result = handler.check_pr_status()

        assert "error" in result
        assert result["all_approved"] is False

    def test_returns_status_info(self, temp_dir):
        """Should return status information."""
        state = {
            "pr_review": {"step": "pr-review-check", "pr_number": 123, "iteration": 1},
            "reviewers": {"claude": {"status": "APPROVED"}},
        }
        config_file = temp_dir / ".ai_workflow" / "config.json"

        # Mock GitHub API calls
        mock_result = MagicMock(returncode=0, stdout="", stderr="")

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            with patch("ai_workflow.config.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                with patch("ai_workflow.pr_workflow.gh_api", return_value=mock_result):
                    handler = PRWorkflowHandler(state)
                    result = handler.check_pr_status()

        assert "step" in result
        assert "iteration" in result
        assert "unresolved_count" in result
        assert "ci_status" in result


class TestFetchPRCommentMetadata:
    """Tests for fetch_pr_comment_metadata method."""

    def test_parses_comments(self, temp_dir):
        """Should parse PR comment metadata."""
        state = {"pr_review": {"step": "pr-pending"}}
        config_file = temp_dir / ".ai_workflow" / "config.json"

        comments_json = '{"id": 123, "file_path": "src/main.py", "resolved": false}\n{"id": 456, "file_path": "src/utils.py", "resolved": true}'
        mock_result = MagicMock(returncode=0, stdout=comments_json, stderr="")

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            with patch("ai_workflow.config.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                with patch("ai_workflow.pr_workflow.gh_api", return_value=mock_result):
                    handler = PRWorkflowHandler(state)
                    comments = handler.fetch_pr_comment_metadata(123)

        assert len(comments) == 2
        assert comments[0]["id"] == 123
        assert comments[0]["file_path"] == "src/main.py"
        assert comments[0]["resolved"] is False

    def test_returns_empty_on_error(self, temp_dir):
        """Should return empty list on API error."""
        state = {"pr_review": {"step": "pr-pending"}}
        config_file = temp_dir / ".ai_workflow" / "config.json"

        mock_result = MagicMock(returncode=1, stdout="", stderr="API error")

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            with patch("ai_workflow.config.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                with patch("ai_workflow.pr_workflow.gh_api", return_value=mock_result):
                    handler = PRWorkflowHandler(state)
                    comments = handler.fetch_pr_comment_metadata(123)

        assert comments == []


class TestRecordCommitAndPush:
    """Tests for record_commit_and_push method."""

    def test_verifies_commit_exists(self, temp_dir):
        """Should verify commit exists locally."""
        state = {"pr_review": {"step": "pr-review-fix"}, "git": {}}
        config_file = temp_dir / ".ai_workflow" / "config.json"

        # Mock git rev-parse failing
        mock_result = MagicMock(returncode=1, stdout="", stderr="fatal: not a commit")

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            with patch("ai_workflow.config.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                with patch("subprocess.run", return_value=mock_result):
                    handler = PRWorkflowHandler(state)
                    success, message = handler.record_commit_and_push("abc123", "test commit")

        assert success is False
        assert "not found" in message

    def test_records_commit_on_push_success(self, temp_dir):
        """Should record commit when push succeeds."""
        state = {
            "pr_review": {"step": "pr-review-fix"},
            "git": {"pr_commits": []},
        }
        config_file = temp_dir / ".ai_workflow" / "config.json"
        config_file.parent.mkdir(parents=True)

        # Write default config
        with open(config_file, "w") as f:
            json.dump(
                {
                    "version": "1.0",
                    "reviewers": {
                        "enabled": [],
                        "available": [],
                        "min_required": 1,
                        "username_mapping": {},
                    },
                    "git": {"push_retry_count": 3},
                },
                f,
            )

        # Mock git verify, branch name, and push success
        mock_verify = MagicMock(returncode=0, stdout="abc123\n", stderr="")
        mock_branch = MagicMock(returncode=0, stdout="feature/test\n", stderr="")
        mock_push = MagicMock(returncode=0, stdout="", stderr="")

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            with patch("subprocess.run") as mock_run:
                mock_run.side_effect = [mock_verify, mock_branch, mock_push]
                handler = PRWorkflowHandler(state)
                success, message = handler.record_commit_and_push("abc123", "fix: issue")

        assert success is True
        assert len(state["git"]["pr_commits"]) == 1
        assert state["git"]["pr_commits"][0]["hash"] == "abc123"

    def test_handles_push_conflict(self, temp_dir):
        """Should handle push conflicts gracefully."""
        state = {
            "pr_review": {"step": "pr-review-fix"},
            "git": {},
        }
        config_file = temp_dir / ".ai_workflow" / "config.json"
        config_file.parent.mkdir(parents=True)

        with open(config_file, "w") as f:
            json.dump(
                {
                    "version": "1.0",
                    "reviewers": {
                        "enabled": [],
                        "available": [],
                        "min_required": 1,
                        "username_mapping": {},
                    },
                    "git": {"push_retry_count": 3},
                },
                f,
            )

        mock_verify = MagicMock(returncode=0, stdout="abc123\n", stderr="")
        mock_branch = MagicMock(returncode=0, stdout="feature/test\n", stderr="")
        mock_push = MagicMock(returncode=1, stdout="", stderr="error: conflict detected")

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            with patch("subprocess.run") as mock_run:
                mock_run.side_effect = [mock_verify, mock_branch, mock_push]
                handler = PRWorkflowHandler(state)
                success, message = handler.record_commit_and_push("abc123", "fix")

        assert success is False
        assert "conflict" in message.lower()
        assert state["pr_review"]["step"] == "pr-commit-failed"


class TestResetForNewTask:
    """Tests for reset_for_new_task method."""

    def test_resets_only_from_merged(self, temp_dir):
        """Should only reset from merged state."""
        state = {"pr_review": {"step": "pr-review-check"}}
        config_file = temp_dir / ".ai_workflow" / "config.json"

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            with patch("ai_workflow.config.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                handler = PRWorkflowHandler(state)
                result = handler.reset_for_new_task()

        assert result is False

    def test_archives_completed_task(self, temp_dir):
        """Should archive completed task."""
        state = {
            "pr_review": {
                "step": "merged",
                "pr_url": "https://github.com/owner/repo/pull/123",
            },
            "git": {"branch": "feature/test"},
        }
        config_file = temp_dir / ".ai_workflow" / "config.json"

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            with patch("ai_workflow.config.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                handler = PRWorkflowHandler(state)
                result = handler.reset_for_new_task()

        assert result is True
        assert "completed_tasks" in state
        assert len(state["completed_tasks"]) == 1
        assert state["completed_tasks"][0]["pr_url"] == "https://github.com/owner/repo/pull/123"

    def test_resets_state(self, temp_dir):
        """Should reset state for new task."""
        state = {
            "pr_review": {"step": "merged"},
            "phase": "pr-review",
            "reviewers": {"claude": {"status": "APPROVED"}},
            "subtasks": {"queue": [{"id": "task-1"}], "completed": [], "failed": []},
        }
        config_file = temp_dir / ".ai_workflow" / "config.json"

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            with patch("ai_workflow.config.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                handler = PRWorkflowHandler(state)
                handler.reset_for_new_task()

        assert state["phase"] == "component"
        assert state["pr_review"]["step"] == "pr-pending"
        assert state["reviewers"] == {}
        assert state["subtasks"]["queue"] == []
