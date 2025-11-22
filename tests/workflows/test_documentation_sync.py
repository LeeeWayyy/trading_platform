"""
Test documentation sync for 6-step workflow pattern.

Component A2.3 - P1T13-F5
Verifies that workflow documentation accurately reflects the 6-step pattern.
"""
from pathlib import Path

import pytest


@pytest.fixture
def project_root() -> Path:
    """Get project root directory."""
    return Path(__file__).parent.parent.parent


def test_readme_has_6_step_pattern(project_root: Path) -> None:
    """Verify README.md references 6-step pattern, not 4-step."""
    readme = project_root / "docs" / "AI" / "Workflows" / "README.md"
    content = readme.read_text(encoding="utf-8")

    # Should mention 6-step pattern
    assert "6-step pattern" in content, "README should reference 6-step pattern"
    assert "6 steps:" in content, "README should list 6 steps"

    # Should NOT mention outdated 4-step pattern
    assert "4-step pattern" not in content, "README should not reference outdated 4-step pattern"
    assert "4 steps:" not in content, "README should not list 4 steps"


def test_component_cycle_has_6_steps(project_root: Path) -> None:
    """Verify 12-component-cycle.md documents all 6 steps."""
    component_cycle = project_root / "docs" / "AI" / "Workflows" / "12-component-cycle.md"
    content = component_cycle.read_text(encoding="utf-8")

    # Should mention 6-step pattern
    assert "6-Step Pattern" in content, "Component cycle should reference 6-step pattern"
    assert "The Six Steps" in content, "Component cycle should have 'Six Steps' section"

    # Should list all 6 steps
    assert "1. Plan" in content
    assert "2. Plan Review" in content
    assert "3. Implement" in content
    assert "4. Test" in content
    assert "5. Code Review" in content
    assert "6. Commit" in content

    # Should NOT mention outdated 4-step pattern
    assert "4-Step Pattern" not in content
    assert "The Four Steps" not in content


def test_workflow_transitions_updated(project_root: Path) -> None:
    """Verify workflow transition diagram shows complete 6-step flow."""
    component_cycle = project_root / "docs" / "AI" / "Workflows" / "12-component-cycle.md"
    content = component_cycle.read_text(encoding="utf-8")

    # Should show complete transition: plan → plan-review → ... → commit → plan
    assert "plan → plan-review → implement → test → review → (commit) → plan" in content


def test_claude_md_references_6_step(project_root: Path) -> None:
    """Verify CLAUDE.md correctly references 6-step pattern."""
    claude_md = project_root / "CLAUDE.md"
    content = claude_md.read_text(encoding="utf-8")

    # Should reference 6-step pattern
    assert "6-step pattern" in content, "CLAUDE.md should reference 6-step pattern"
    assert "plan → plan-review → implement → test → review → commit" in content

    # Acceptance criteria
    assert "Plan → Plan Review → Implement → Test → Code Review → Commit" in content
