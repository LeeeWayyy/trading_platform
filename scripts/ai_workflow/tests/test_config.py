"""
Tests for config.py module.

Tests WorkflowConfig class for loading, saving, and merging configuration.
"""

import json
import pytest
from pathlib import Path
from unittest.mock import patch

from ai_workflow.config import WorkflowConfig, DEFAULT_CONFIG


class TestDefaultConfig:
    """Tests for default configuration values."""

    def test_default_config_has_version(self):
        """Default config should have a version."""
        assert "version" in DEFAULT_CONFIG
        assert DEFAULT_CONFIG["version"] == "1.0"

    def test_default_config_has_reviewers(self):
        """Default config should have reviewers section."""
        assert "reviewers" in DEFAULT_CONFIG
        assert "enabled" in DEFAULT_CONFIG["reviewers"]
        assert "available" in DEFAULT_CONFIG["reviewers"]
        assert "min_required" in DEFAULT_CONFIG["reviewers"]

    def test_default_reviewers_enabled(self):
        """Default enabled reviewers should include gemini and codex (dual-review)."""
        enabled = DEFAULT_CONFIG["reviewers"]["enabled"]
        assert "gemini" in enabled
        assert "codex" in enabled

    def test_default_min_required(self):
        """Default min_required should be 2 (dual-review requirement)."""
        assert DEFAULT_CONFIG["reviewers"]["min_required"] == 2

    def test_default_config_has_ci(self):
        """Default config should have CI section."""
        assert "ci" in DEFAULT_CONFIG
        assert "wait_timeout_seconds" in DEFAULT_CONFIG["ci"]
        assert "poll_interval_seconds" in DEFAULT_CONFIG["ci"]

    def test_default_config_has_git(self):
        """Default config should have git section."""
        assert "git" in DEFAULT_CONFIG
        assert "push_retry_count" in DEFAULT_CONFIG["git"]
        assert "default_base_branch" in DEFAULT_CONFIG["git"]

    def test_default_config_has_delegation(self):
        """Default config should have delegation section."""
        assert "delegation" in DEFAULT_CONFIG
        assert "comment_threshold" in DEFAULT_CONFIG["delegation"]
        assert "file_threshold" in DEFAULT_CONFIG["delegation"]


class TestWorkflowConfigInit:
    """Tests for WorkflowConfig initialization."""

    def test_creates_default_when_no_file(self, temp_dir):
        """Should create default config when file doesn't exist."""
        config_file = temp_dir / ".ai_workflow" / "config.json"

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            with patch("ai_workflow.config.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                config = WorkflowConfig()

        assert config.config["version"] == "1.0"
        assert config_file.exists()

    def test_loads_existing_config(self, temp_dir):
        """Should load existing config file."""
        config_file = temp_dir / ".ai_workflow" / "config.json"
        config_file.parent.mkdir(parents=True)

        custom_config = {
            "version": "1.0",
            "reviewers": {
                "enabled": ["codex"],
                "available": ["codex"],
                "min_required": 2,
                "username_mapping": {},
            },
        }
        with open(config_file, "w") as f:
            json.dump(custom_config, f)

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            config = WorkflowConfig()

        assert config.config["reviewers"]["enabled"] == ["codex"]
        assert config.config["reviewers"]["min_required"] == 2

    def test_merges_with_defaults(self, temp_dir):
        """Missing fields should be filled from defaults."""
        config_file = temp_dir / ".ai_workflow" / "config.json"
        config_file.parent.mkdir(parents=True)

        # Partial config missing ci section
        partial_config = {
            "version": "1.0",
            "reviewers": {
                "enabled": ["claude"],
                "available": ["claude"],
                "min_required": 1,
                "username_mapping": {},
            },
        }
        with open(config_file, "w") as f:
            json.dump(partial_config, f)

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            config = WorkflowConfig()

        # Should have CI defaults
        assert "ci" in config.config
        assert "wait_timeout_seconds" in config.config["ci"]

    def test_handles_invalid_config_type(self, temp_dir, capsys):
        """Should handle invalid config gracefully and use defaults."""
        config_file = temp_dir / ".ai_workflow" / "config.json"
        config_file.parent.mkdir(parents=True)

        # Write array instead of dict
        with open(config_file, "w") as f:
            json.dump(["invalid", "config"], f)

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            with patch("ai_workflow.config.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                config = WorkflowConfig()

        # Should use defaults instead of raising
        assert config.config["version"] == "1.0"
        # Should have printed warning
        captured = capsys.readouterr()
        assert "Invalid config format" in captured.err


class TestGetEnabledReviewers:
    """Tests for get_enabled_reviewers method."""

    def test_returns_enabled_list(self, temp_dir):
        """Should return list of enabled reviewers."""
        config_file = temp_dir / ".ai_workflow" / "config.json"

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            with patch("ai_workflow.config.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                config = WorkflowConfig()

        enabled = config.get_enabled_reviewers()
        assert isinstance(enabled, list)
        assert "gemini" in enabled
        assert "codex" in enabled

    def test_returns_custom_enabled(self, temp_dir):
        """Should return custom enabled reviewers."""
        config_file = temp_dir / ".ai_workflow" / "config.json"
        config_file.parent.mkdir(parents=True)

        custom_config = {
            "version": "1.0",
            "reviewers": {
                "enabled": ["codex"],
                "available": ["codex"],
                "min_required": 1,
                "username_mapping": {},
            },
        }
        with open(config_file, "w") as f:
            json.dump(custom_config, f)

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            config = WorkflowConfig()

        enabled = config.get_enabled_reviewers()
        assert enabled == ["codex"]


class TestGetMinRequiredApprovals:
    """Tests for get_min_required_approvals method."""

    def test_returns_min_required(self, temp_dir):
        """Should return min_required value (default is 2 for dual-review)."""
        config_file = temp_dir / ".ai_workflow" / "config.json"

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            with patch("ai_workflow.config.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                config = WorkflowConfig()

        assert config.get_min_required_approvals() == 2

    def test_returns_custom_min_required(self, temp_dir):
        """Should return custom min_required value."""
        config_file = temp_dir / ".ai_workflow" / "config.json"
        config_file.parent.mkdir(parents=True)

        custom_config = {
            "version": "1.0",
            "reviewers": {
                "enabled": ["claude", "gemini"],
                "available": ["claude", "gemini"],
                "min_required": 2,
                "username_mapping": {},
            },
        }
        with open(config_file, "w") as f:
            json.dump(custom_config, f)

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            config = WorkflowConfig()

        assert config.get_min_required_approvals() == 2


class TestGetReviewerUsername:
    """Tests for get_reviewer_username method."""

    def test_returns_none_when_not_mapped(self, temp_dir):
        """Should return None for unmapped reviewer."""
        config_file = temp_dir / ".ai_workflow" / "config.json"

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            with patch("ai_workflow.config.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                config = WorkflowConfig()

        assert config.get_reviewer_username("claude") is None

    def test_returns_mapped_username(self, temp_dir):
        """Should return GitHub username when mapped."""
        config_file = temp_dir / ".ai_workflow" / "config.json"
        config_file.parent.mkdir(parents=True)

        custom_config = {
            "version": "1.0",
            "reviewers": {
                "enabled": ["claude"],
                "available": ["claude"],
                "min_required": 1,
                "username_mapping": {"claude": "claude-bot"},
            },
        }
        with open(config_file, "w") as f:
            json.dump(custom_config, f)

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            config = WorkflowConfig()

        assert config.get_reviewer_username("claude") == "claude-bot"


class TestIsReviewerEnabled:
    """Tests for is_reviewer_enabled method."""

    def test_returns_true_for_enabled(self, temp_dir):
        """Should return True for enabled reviewer (default: gemini + codex)."""
        config_file = temp_dir / ".ai_workflow" / "config.json"

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            with patch("ai_workflow.config.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                config = WorkflowConfig()

        assert config.is_reviewer_enabled("gemini") is True
        assert config.is_reviewer_enabled("codex") is True

    def test_returns_false_for_disabled(self, temp_dir):
        """Should return False for disabled reviewer (claude not in default enabled)."""
        config_file = temp_dir / ".ai_workflow" / "config.json"

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            with patch("ai_workflow.config.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                config = WorkflowConfig()

        assert config.is_reviewer_enabled("claude") is False
        assert config.is_reviewer_enabled("unknown") is False


class TestDeepUpdate:
    """Tests for _deep_update method."""

    def test_deep_update_nested_dicts(self, temp_dir):
        """Should recursively update nested dicts."""
        config_file = temp_dir / ".ai_workflow" / "config.json"

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            with patch("ai_workflow.config.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                config = WorkflowConfig()

        base = {"a": {"b": 1, "c": 2}, "d": 3}
        update = {"a": {"b": 10}}

        config._deep_update(base, update)

        assert base["a"]["b"] == 10
        assert base["a"]["c"] == 2  # Preserved
        assert base["d"] == 3  # Preserved

    def test_deep_update_replaces_non_dict(self, temp_dir):
        """Should replace non-dict values directly."""
        config_file = temp_dir / ".ai_workflow" / "config.json"

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            with patch("ai_workflow.config.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                config = WorkflowConfig()

        base = {"a": {"b": 1}}
        update = {"a": "replaced"}

        config._deep_update(base, update)

        assert base["a"] == "replaced"
