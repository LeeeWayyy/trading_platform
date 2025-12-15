"""
Tests for reviewers.py module.

Tests ReviewerOrchestrator for managing reviewer state and building MCP parameters.
"""

import json
from datetime import datetime
from unittest.mock import patch

import pytest

from ai_workflow.reviewers import (
    ReviewerOrchestrator,
    ReviewerType,
    ReviewResult,
    ReviewStatus,
)


class TestReviewerType:
    """Tests for ReviewerType enum."""

    def test_has_claude(self):
        """Should have CLAUDE type."""
        assert ReviewerType.CLAUDE.value == "claude"

    def test_has_gemini(self):
        """Should have GEMINI type."""
        assert ReviewerType.GEMINI.value == "gemini"

    def test_has_codex(self):
        """Should have CODEX type."""
        assert ReviewerType.CODEX.value == "codex"


class TestReviewStatus:
    """Tests for ReviewStatus enum."""

    def test_not_requested(self):
        """Should have NOT_REQUESTED status."""
        assert ReviewStatus.NOT_REQUESTED.value == "NOT_REQUESTED"

    def test_pending(self):
        """Should have PENDING status."""
        assert ReviewStatus.PENDING.value == "PENDING"

    def test_approved(self):
        """Should have APPROVED status."""
        assert ReviewStatus.APPROVED.value == "APPROVED"

    def test_changes_requested(self):
        """Should have CHANGES_REQUESTED status."""
        assert ReviewStatus.CHANGES_REQUESTED.value == "CHANGES_REQUESTED"

    def test_dismissed(self):
        """Should have DISMISSED status."""
        assert ReviewStatus.DISMISSED.value == "DISMISSED"

    def test_error(self):
        """Should have ERROR status."""
        assert ReviewStatus.ERROR.value == "ERROR"


class TestReviewResult:
    """Tests for ReviewResult dataclass."""

    def test_creates_with_required_fields(self):
        """Should create with required fields."""
        result = ReviewResult(reviewer="claude", status=ReviewStatus.APPROVED)

        assert result.reviewer == "claude"
        assert result.status == ReviewStatus.APPROVED

    def test_has_default_optional_fields(self):
        """Should have default values for optional fields."""
        result = ReviewResult(reviewer="gemini", status=ReviewStatus.PENDING)

        assert result.continuation_id == ""
        assert result.findings == []
        assert result.error_message == ""

    def test_accepts_optional_fields(self):
        """Should accept optional fields."""
        result = ReviewResult(
            reviewer="codex",
            status=ReviewStatus.CHANGES_REQUESTED,
            continuation_id="abc123",
            findings=["issue1", "issue2"],
            error_message="Some error",
        )

        assert result.continuation_id == "abc123"
        assert len(result.findings) == 2
        assert result.error_message == "Some error"


class TestReviewerOrchestratorInit:
    """Tests for ReviewerOrchestrator initialization."""

    def test_initializes_with_state(self, temp_dir):
        """Should initialize with provided state."""
        state = {"reviewers": {}}
        config_file = temp_dir / ".ai_workflow" / "config.json"

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            with patch("ai_workflow.config.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                orchestrator = ReviewerOrchestrator(state)

        assert orchestrator.state is state

    def test_ensures_reviewer_state(self, temp_dir):
        """Should ensure reviewer state structure exists."""
        state = {}  # No reviewers key
        config_file = temp_dir / ".ai_workflow" / "config.json"

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            with patch("ai_workflow.config.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                ReviewerOrchestrator(state)

        assert "reviewers" in state
        # Should have entries for enabled reviewers (default: gemini + codex)
        assert "gemini" in state["reviewers"]
        assert "codex" in state["reviewers"]

    def test_initializes_reviewer_defaults(self, temp_dir):
        """Should initialize reviewer defaults."""
        state = {}
        config_file = temp_dir / ".ai_workflow" / "config.json"

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            with patch("ai_workflow.config.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                ReviewerOrchestrator(state)

        # Default enabled reviewers are gemini + codex
        for reviewer in ["gemini", "codex"]:
            assert state["reviewers"][reviewer]["status"] == "NOT_REQUESTED"
            assert state["reviewers"][reviewer]["continuation_id"] is None


class TestGetContinuationId:
    """Tests for get_continuation_id method."""

    def test_returns_none_when_not_set(self, temp_dir):
        """Should return None when no continuation_id."""
        state = {"reviewers": {"claude": {"status": "NOT_REQUESTED"}}}
        config_file = temp_dir / ".ai_workflow" / "config.json"

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            with patch("ai_workflow.config.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                orchestrator = ReviewerOrchestrator(state)

        assert orchestrator.get_continuation_id("claude") is None

    def test_returns_continuation_id_when_set(self, temp_dir):
        """Should return continuation_id when set."""
        state = {"reviewers": {"claude": {"status": "PENDING", "continuation_id": "abc123"}}}
        config_file = temp_dir / ".ai_workflow" / "config.json"

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            with patch("ai_workflow.config.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                orchestrator = ReviewerOrchestrator(state)

        assert orchestrator.get_continuation_id("claude") == "abc123"


class TestSetContinuationId:
    """Tests for set_continuation_id method."""

    def test_sets_continuation_id(self, temp_dir):
        """Should set continuation_id in state."""
        state = {"reviewers": {}}
        config_file = temp_dir / ".ai_workflow" / "config.json"

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            with patch("ai_workflow.config.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                orchestrator = ReviewerOrchestrator(state)
                orchestrator.set_continuation_id("claude", "new-id-123")

        assert state["reviewers"]["claude"]["continuation_id"] == "new-id-123"

    def test_updates_last_updated(self, temp_dir):
        """Should update last_updated timestamp."""
        state = {"reviewers": {}}
        config_file = temp_dir / ".ai_workflow" / "config.json"

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            with patch("ai_workflow.config.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                orchestrator = ReviewerOrchestrator(state)
                orchestrator.set_continuation_id("claude", "id-123")

        assert "last_updated" in state["reviewers"]["claude"]
        # Should be valid ISO timestamp
        datetime.fromisoformat(state["reviewers"]["claude"]["last_updated"])


class TestRecordReviewResult:
    """Tests for record_review_result method."""

    def test_records_status(self, temp_dir):
        """Should record review status."""
        state = {"reviewers": {}}
        config_file = temp_dir / ".ai_workflow" / "config.json"

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            with patch("ai_workflow.config.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                orchestrator = ReviewerOrchestrator(state)
                orchestrator.record_review_result("claude", ReviewStatus.APPROVED, "cont-id-123")

        assert state["reviewers"]["claude"]["status"] == "APPROVED"
        assert state["reviewers"]["claude"]["continuation_id"] == "cont-id-123"

    def test_records_without_continuation_id(self, temp_dir):
        """Should record status without continuation_id."""
        state = {"reviewers": {}}
        config_file = temp_dir / ".ai_workflow" / "config.json"

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            with patch("ai_workflow.config.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                orchestrator = ReviewerOrchestrator(state)
                orchestrator.record_review_result("gemini", ReviewStatus.PENDING)

        assert state["reviewers"]["gemini"]["status"] == "PENDING"


class TestCheckAllApproved:
    """Tests for check_all_approved method."""

    def test_returns_true_when_approved(self, temp_dir):
        """Should return True when min_required approvals met."""
        state = {
            "reviewers": {
                "gemini": {"status": "APPROVED"},
                "codex": {"status": "APPROVED"},
            }
        }
        config_file = temp_dir / ".ai_workflow" / "config.json"

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            with patch("ai_workflow.config.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                orchestrator = ReviewerOrchestrator(state)
                approved, message = orchestrator.check_all_approved()

        assert approved is True
        assert "2/2" in message

    def test_returns_false_when_pending(self, temp_dir):
        """Should return False when approvals pending and min_required > approved."""
        state = {
            "reviewers": {
                "claude": {"status": "APPROVED"},
                "gemini": {"status": "PENDING"},
            }
        }
        config_file = temp_dir / ".ai_workflow" / "config.json"
        config_file.parent.mkdir(parents=True)

        # Set min_required=2 so one approved + one pending = not enough
        config = {
            "version": "1.0",
            "reviewers": {
                "enabled": ["claude", "gemini"],
                "available": ["claude", "gemini"],
                "min_required": 2,
                "username_mapping": {},
            },
        }
        with open(config_file, "w") as f:
            json.dump(config, f)

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            orchestrator = ReviewerOrchestrator(state)
            approved, message = orchestrator.check_all_approved()

        assert approved is False
        assert "gemini" in message.lower()

    def test_returns_false_on_error(self, temp_dir):
        """Should return False when reviewer has error."""
        state = {
            "reviewers": {
                "gemini": {"status": "ERROR"},
                "codex": {"status": "APPROVED"},
            }
        }
        config_file = temp_dir / ".ai_workflow" / "config.json"

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            with patch("ai_workflow.config.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                orchestrator = ReviewerOrchestrator(state)
                approved, message = orchestrator.check_all_approved()

        assert approved is False
        # With 1 approved + 1 error, min_required=2 fails, message says "waiting for"
        assert "gemini" in message.lower()

    def test_ignores_dismissed_reviewers(self, temp_dir):
        """Should ignore dismissed reviewers."""
        state = {
            "reviewers": {
                "gemini": {"status": "APPROVED"},
                "codex": {"status": "APPROVED"},  # Need 2 approved for min_required=2
            }
        }
        config_file = temp_dir / ".ai_workflow" / "config.json"

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            with patch("ai_workflow.config.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                orchestrator = ReviewerOrchestrator(state)
                approved, message = orchestrator.check_all_approved()

        # Should be approved (2/2 approved meets min_required=2)
        assert approved is True


class TestBuildClinkParams:
    """Tests for build_clink_params method."""

    def test_builds_basic_params(self, temp_dir):
        """Should build basic clink parameters."""
        state = {"reviewers": {}}
        config_file = temp_dir / ".ai_workflow" / "config.json"

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            with patch("ai_workflow.config.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                orchestrator = ReviewerOrchestrator(state)
                params = orchestrator.build_clink_params(
                    reviewer_name="claude", diff="some diff content"
                )

        assert params["cli_name"] == "claude"
        assert "diff" in params["prompt"]
        assert params["role"] == "codereviewer"

    def test_includes_file_paths(self, temp_dir):
        """Should include file paths when provided."""
        state = {"reviewers": {}}
        config_file = temp_dir / ".ai_workflow" / "config.json"

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            with patch("ai_workflow.config.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                orchestrator = ReviewerOrchestrator(state)
                params = orchestrator.build_clink_params(
                    reviewer_name="gemini",
                    diff="diff",
                    file_paths=["/path/to/file1.py", "/path/to/file2.py"],
                )

        assert "absolute_file_paths" in params
        assert len(params["absolute_file_paths"]) == 2

    def test_includes_continuation_id(self, temp_dir):
        """Should include continuation_id when provided."""
        state = {"reviewers": {}}
        config_file = temp_dir / ".ai_workflow" / "config.json"

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            with patch("ai_workflow.config.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                orchestrator = ReviewerOrchestrator(state)
                params = orchestrator.build_clink_params(
                    reviewer_name="claude",
                    diff="diff",
                    continuation_id="cont-123",
                )

        assert params["continuation_id"] == "cont-123"

    def test_rejects_invalid_cli_name(self, temp_dir):
        """Should reject invalid CLI names."""
        state = {"reviewers": {}}
        config_file = temp_dir / ".ai_workflow" / "config.json"

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            with patch("ai_workflow.config.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                orchestrator = ReviewerOrchestrator(state)

                with pytest.raises(ValueError, match="Invalid CLI name"):
                    orchestrator.build_clink_params(reviewer_name="invalid_cli", diff="diff")

    def test_truncates_long_diff(self, temp_dir):
        """Should truncate diff if too long."""
        state = {"reviewers": {}}
        config_file = temp_dir / ".ai_workflow" / "config.json"

        long_diff = "x" * 50000  # Longer than 30000 limit

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            with patch("ai_workflow.config.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                orchestrator = ReviewerOrchestrator(state)
                params = orchestrator.build_clink_params(reviewer_name="claude", diff=long_diff)

        # Prompt should not contain full diff
        assert len(params["prompt"]) < 35000


class TestGetValidCliNames:
    """Tests for _get_valid_cli_names method."""

    def test_returns_default_clis(self, temp_dir):
        """Should return default CLI names."""
        state = {"reviewers": {}}
        config_file = temp_dir / ".ai_workflow" / "config.json"

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            with patch("ai_workflow.config.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                orchestrator = ReviewerOrchestrator(state)
                valid = orchestrator._get_valid_cli_names()

        assert "claude" in valid
        assert "gemini" in valid
        assert "codex" in valid

    def test_uses_configured_clis(self, temp_dir):
        """Should use configured valid_clis if set."""
        state = {"reviewers": {}}
        config_file = temp_dir / ".ai_workflow" / "config.json"
        config_file.parent.mkdir(parents=True)

        custom_config = {
            "version": "1.0",
            "reviewers": {
                "enabled": ["custom_cli"],
                "available": ["custom_cli"],
                "min_required": 1,
                "valid_clis": ["custom_cli", "another_cli"],
                "username_mapping": {},
            },
        }
        with open(config_file, "w") as f:
            json.dump(custom_config, f)

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            orchestrator = ReviewerOrchestrator(state)
            valid = orchestrator._get_valid_cli_names()

        assert "custom_cli" in valid
        assert "another_cli" in valid
