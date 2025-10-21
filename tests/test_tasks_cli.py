"""
Tests for Task Lifecycle Management CLI (scripts/tasks.py).

Tests the phase management and task lifecycle commands.
"""

import subprocess
import tempfile
from pathlib import Path

import pytest


def test_create_phase_command_exists():
    """Test that create-phase command is available."""
    result = subprocess.run(
        ["python", "scripts/tasks.py", "--help"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).parent.parent,
    )
    assert result.returncode == 0
    assert "create-phase" in result.stdout


def test_generate_tasks_from_phase_command_exists():
    """Test that generate-tasks-from-phase command is available."""
    result = subprocess.run(
        ["python", "scripts/tasks.py", "--help"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).parent.parent,
    )
    assert result.returncode == 0
    assert "generate-tasks-from-phase" in result.stdout


def test_create_phase_requires_valid_phase():
    """Test that create-phase validates phase ID."""
    result = subprocess.run(
        ["python", "scripts/tasks.py", "create-phase", "P3"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).parent.parent,
    )
    # Should fail because P3 is not valid (only P0, P1, P2)
    assert result.returncode != 0


def test_generate_tasks_dry_run():
    """Test generate-tasks-from-phase with --dry-run flag."""
    # This test assumes P1_PLANNING.md exists
    result = subprocess.run(
        ["python", "scripts/tasks.py", "generate-tasks-from-phase", "P1", "--dry-run"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).parent.parent,
    )
    # Should succeed and show what would be created
    assert result.returncode == 0
    assert "Would create" in result.stdout or "No new tasks" in result.stdout


def test_phase_template_exists():
    """Test that phase planning template exists."""
    template_path = Path(__file__).parent.parent / "docs" / "TASKS" / "00-TEMPLATE_PHASE_PLANNING.md"
    assert template_path.exists()

    # Verify template has expected structure
    content = template_path.read_text()
    assert "# P0 Planning:" in content
    assert "## Progress Summary" in content
    assert "## Tasks Breakdown" in content
    assert "## Success Metrics" in content


def test_task_template_exists():
    """Test that task templates exist."""
    templates_dir = Path(__file__).parent.parent / "docs" / "TASKS"

    assert (templates_dir / "00-TEMPLATE_TASK.md").exists()
    assert (templates_dir / "00-TEMPLATE_PROGRESS.md").exists()
    assert (templates_dir / "00-TEMPLATE_DONE.md").exists()
    assert (templates_dir / "00-TEMPLATE_FEATURE.md").exists()


def test_cli_help():
    """Test that CLI help shows all commands."""
    result = subprocess.run(
        ["python", "scripts/tasks.py", "--help"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).parent.parent,
    )
    assert result.returncode == 0

    # Verify all commands are documented
    assert "create" in result.stdout
    assert "start" in result.stdout
    assert "complete" in result.stdout
    assert "list" in result.stdout
    assert "sync-status" in result.stdout
    assert "lint" in result.stdout
    assert "create-phase" in result.stdout
    assert "generate-tasks-from-phase" in result.stdout


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
