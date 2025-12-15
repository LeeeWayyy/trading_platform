"""
Tests for constants.py module.

Tests path definitions, step types, and transition rules.
"""

from pathlib import Path

import pytest

from ai_workflow.constants import (
    AUDIT_LOG,
    CONFIG_FILE,
    CONTEXT_CRITICAL_PCT,
    CONTEXT_WARN_PCT,
    DEFAULT_MAX_TOKENS,
    LEGACY_CLAUDE_DIR,
    LEGACY_STATE_FILE,
    PLACEHOLDER_PATTERNS,
    PROJECT_ROOT,
    REVIEW_APPROVED,
    REVIEW_NEEDS_REVISION,
    REVIEW_NOT_REQUESTED,
    STATE_FILE,
    STEP_DESCRIPTIONS,
    VALID_TRANSITIONS,
    WORKFLOW_DIR,
)


class TestPaths:
    """Tests for path constants."""

    def test_project_root_exists(self):
        """PROJECT_ROOT should point to a valid directory."""
        assert isinstance(PROJECT_ROOT, Path)
        # PROJECT_ROOT is parent.parent of constants.py

    def test_workflow_dir_is_relative(self):
        """WORKFLOW_DIR should be a relative path."""
        assert isinstance(WORKFLOW_DIR, Path)
        assert not WORKFLOW_DIR.is_absolute()
        assert str(WORKFLOW_DIR) == ".ai_workflow"

    def test_state_file_path(self):
        """STATE_FILE should be within WORKFLOW_DIR."""
        assert STATE_FILE == WORKFLOW_DIR / "workflow-state.json"

    def test_config_file_path(self):
        """CONFIG_FILE should be within WORKFLOW_DIR."""
        assert CONFIG_FILE == WORKFLOW_DIR / "config.json"

    def test_audit_log_path(self):
        """AUDIT_LOG should be within WORKFLOW_DIR."""
        assert AUDIT_LOG == WORKFLOW_DIR / "workflow-audit.log"

    def test_legacy_paths(self):
        """Legacy paths should point to .claude directory."""
        assert str(LEGACY_CLAUDE_DIR) == ".claude"
        assert LEGACY_STATE_FILE == LEGACY_CLAUDE_DIR / "workflow-state.json"


class TestTransitions:
    """Tests for workflow transition rules."""

    def test_valid_transitions_keys(self):
        """All workflow steps should have transition rules."""
        expected_steps = {"plan", "plan-review", "implement", "test", "review"}
        assert set(VALID_TRANSITIONS.keys()) == expected_steps

    def test_plan_transitions(self):
        """Plan step should only transition to plan-review."""
        assert VALID_TRANSITIONS["plan"] == ["plan-review"]

    def test_plan_review_transitions(self):
        """Plan-review can go to implement or back to plan."""
        assert set(VALID_TRANSITIONS["plan-review"]) == {"implement", "plan"}

    def test_implement_transitions(self):
        """Implement should transition to test."""
        assert VALID_TRANSITIONS["implement"] == ["test"]

    def test_test_transitions(self):
        """Test can go to review or back to implement."""
        assert set(VALID_TRANSITIONS["test"]) == {"review", "implement"}

    def test_review_transitions(self):
        """Review can only go back to implement (for fixes)."""
        assert VALID_TRANSITIONS["review"] == ["implement"]

    def test_no_circular_plan_to_plan(self):
        """Plan cannot transition directly to plan."""
        assert "plan" not in VALID_TRANSITIONS["plan"]

    def test_no_skip_to_review(self):
        """Cannot skip from plan directly to review."""
        assert "review" not in VALID_TRANSITIONS["plan"]
        assert "review" not in VALID_TRANSITIONS["plan-review"]


class TestStepDescriptions:
    """Tests for step descriptions."""

    def test_all_steps_have_descriptions(self):
        """Every transition step should have a description."""
        for step in VALID_TRANSITIONS.keys():
            assert step in STEP_DESCRIPTIONS
            assert len(STEP_DESCRIPTIONS[step]) > 0

    def test_descriptions_are_strings(self):
        """All descriptions should be non-empty strings."""
        for _step, desc in STEP_DESCRIPTIONS.items():
            assert isinstance(desc, str)
            assert len(desc.strip()) > 0


class TestReviewConstants:
    """Tests for review status constants."""

    def test_review_approved(self):
        """REVIEW_APPROVED should be APPROVED."""
        assert REVIEW_APPROVED == "APPROVED"

    def test_review_needs_revision(self):
        """REVIEW_NEEDS_REVISION should be NEEDS_REVISION."""
        assert REVIEW_NEEDS_REVISION == "NEEDS_REVISION"

    def test_review_not_requested(self):
        """REVIEW_NOT_REQUESTED should be NOT_REQUESTED."""
        assert REVIEW_NOT_REQUESTED == "NOT_REQUESTED"


class TestPlaceholderPatterns:
    """Tests for placeholder ID detection patterns."""

    def test_patterns_are_regex(self):
        """All patterns should be valid regex strings."""
        import re

        for pattern in PLACEHOLDER_PATTERNS:
            assert isinstance(pattern, str)
            # Should compile without error
            re.compile(pattern)

    def test_test_prefix_blocked(self):
        """IDs starting with 'test-' should be detected."""
        import re

        for pattern in PLACEHOLDER_PATTERNS:
            if re.match(pattern, "test-123"):
                return  # Found a matching pattern
        pytest.fail("'test-' prefix not blocked by any pattern")

    def test_placeholder_prefix_blocked(self):
        """IDs starting with 'placeholder-' should be detected."""
        import re

        for pattern in PLACEHOLDER_PATTERNS:
            if re.match(pattern, "placeholder-abc"):
                return
        pytest.fail("'placeholder-' prefix not blocked by any pattern")

    def test_fake_prefix_blocked(self):
        """IDs starting with 'fake-' should be detected."""
        import re

        for pattern in PLACEHOLDER_PATTERNS:
            if re.match(pattern, "fake-id"):
                return
        pytest.fail("'fake-' prefix not blocked by any pattern")

    def test_valid_id_not_blocked(self):
        """Valid continuation IDs should not match patterns."""
        import re

        valid_ids = [
            "abc123def456",
            "zenreview-abc123",
            "claude-session-xyz",
            "a1b2c3d4",
        ]
        for valid_id in valid_ids:
            for pattern in PLACEHOLDER_PATTERNS:
                assert not re.match(
                    pattern, valid_id.lower()
                ), f"Valid ID '{valid_id}' incorrectly matched pattern '{pattern}'"


class TestContextThresholds:
    """Tests for context usage thresholds."""

    def test_warn_threshold(self):
        """Warning threshold should be 70%."""
        assert CONTEXT_WARN_PCT == 70

    def test_critical_threshold(self):
        """Critical threshold should be 85%."""
        assert CONTEXT_CRITICAL_PCT == 85

    def test_warn_less_than_critical(self):
        """Warning threshold should be less than critical."""
        assert CONTEXT_WARN_PCT < CONTEXT_CRITICAL_PCT

    def test_default_max_tokens(self):
        """Default max tokens should be a reasonable value."""
        assert DEFAULT_MAX_TOKENS == 200000
        assert DEFAULT_MAX_TOKENS > 0
