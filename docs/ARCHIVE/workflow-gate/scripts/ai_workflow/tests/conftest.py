"""
Pytest configuration and shared fixtures for AI workflow tests.
"""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture()
def temp_dir():
    """Create a temporary directory for test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture()
def mock_workflow_dir(temp_dir):
    """Create a mock .ai_workflow directory."""
    workflow_dir = temp_dir / ".ai_workflow"
    workflow_dir.mkdir(parents=True, exist_ok=True)
    return workflow_dir


@pytest.fixture()
def mock_state_file(mock_workflow_dir):
    """Create a mock state file path."""
    return mock_workflow_dir / "workflow-state.json"


@pytest.fixture()
def mock_config_file(mock_workflow_dir):
    """Create a mock config file path."""
    return mock_workflow_dir / "config.json"


@pytest.fixture()
def default_state():
    """Return default workflow state (v2 schema)."""
    return {
        "version": "2.0",
        "phase": "component",
        "component": {
            "current": "",
            "step": "plan",
            "list": [],
        },
        "pr_review": {
            "step": "pr-pending",
            "iteration": 0,
        },
        "reviewers": {},
        "ci": {},
        "git": {
            "commits": [],
            "pr_commits": [],
        },
        "subtasks": {
            "queue": [],
            "completed": [],
            "failed": [],
        },
    }


@pytest.fixture()
def default_config():
    """Return default workflow config."""
    return {
        "version": "1.0",
        "reviewers": {
            "enabled": ["claude", "gemini"],
            "available": ["claude", "gemini", "codex"],
            "min_required": 1,
            "username_mapping": {},
        },
        "ci": {
            "wait_timeout_seconds": 600,
            "poll_interval_seconds": 30,
            "retry_on_flaky": True,
        },
        "git": {
            "push_retry_count": 3,
            "default_base_branch": "master",
        },
        "delegation": {
            "comment_threshold": 10,
            "file_threshold": 20,
        },
    }


@pytest.fixture()
def sample_state_with_component(default_state):
    """Return state with a component set."""
    state = default_state.copy()
    state["component"] = {
        "current": "TestComponent",
        "step": "implement",
        "list": ["TestComponent", "AnotherComponent"],
    }
    return state


@pytest.fixture()
def sample_state_in_review(default_state):
    """Return state in review step."""
    state = default_state.copy()
    state["component"] = {
        "current": "TestComponent",
        "step": "review",
        "list": ["TestComponent"],
    }
    state["reviewers"] = {
        "claude": {"status": "APPROVED", "continuation_id": "abc123"},
        "gemini": {"status": "APPROVED", "continuation_id": "def456"},
    }
    state["ci"] = {"component_passed": True}
    return state


@pytest.fixture()
def sample_pr_review_state(default_state):
    """Return state in PR review phase."""
    state = default_state.copy()
    state["phase"] = "pr-review"
    state["pr_review"] = {
        "step": "pr-review-check",
        "iteration": 1,
        "pr_url": "https://github.com/owner/repo/pull/123",
        "pr_number": 123,
        "iterations": [],
        "unresolved_comments": [],
        "ci_status": None,
    }
    return state


@pytest.fixture()
def mock_git_repo(temp_dir):
    """Create a mock git repository for testing."""
    repo_dir = temp_dir / "test_repo"
    repo_dir.mkdir()

    # Initialize git repo
    os.system(f"cd {repo_dir} && git init -q")
    os.system(f"cd {repo_dir} && git config user.email 'test@test.com'")
    os.system(f"cd {repo_dir} && git config user.name 'Test User'")

    # Create initial commit
    (repo_dir / "README.md").write_text("# Test Repo\n")
    os.system(f"cd {repo_dir} && git add . && git commit -q -m 'Initial commit'")

    # Add remote
    os.system(f"cd {repo_dir} && git remote add origin git@github.com:testowner/testrepo.git")

    return repo_dir


@pytest.fixture(name="patch_constants")
def _patch_constants(temp_dir, mock_workflow_dir, mock_state_file, mock_config_file):
    """Patch constants to use temporary directories."""
    with patch.multiple(
        "ai_workflow.constants",
        PROJECT_ROOT=temp_dir,
        WORKFLOW_DIR=mock_workflow_dir,
        STATE_FILE=mock_state_file,
        CONFIG_FILE=mock_config_file,
        AUDIT_LOG=mock_workflow_dir / "workflow-audit.log",
        AUDIT_LOG_FILE=mock_workflow_dir / "workflow-audit.log",
    ):
        yield


def write_state(state_file: Path, state: dict) -> None:
    """Helper to write state to file."""
    state_file.parent.mkdir(parents=True, exist_ok=True)
    with open(state_file, "w") as f:
        json.dump(state, f, indent=2)


def read_state(state_file: Path) -> dict:
    """Helper to read state from file."""
    with open(state_file) as f:
        return json.load(f)


def write_config(config_file: Path, config: dict) -> None:
    """Helper to write config to file."""
    config_file.parent.mkdir(parents=True, exist_ok=True)
    with open(config_file, "w") as f:
        json.dump(config, f, indent=2)
