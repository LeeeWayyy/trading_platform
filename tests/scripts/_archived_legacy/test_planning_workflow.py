#!/usr/bin/env python3
"""
Test suite for PlanningWorkflow class in scripts/workflow_gate.py.

Tests integrated planning workflow including task creation, subfeature
breakdown, and state initialization.

Author: Claude Code
Date: 2025-11-08
"""

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add project root to Python path for imports
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

# Import class under test
from scripts.workflow_gate import PlanningWorkflow


class TestPlanningWorkflowInitialization:
    """Test PlanningWorkflow.__init__() method."""

    def test_init_default_params(self) -> None:
        """Test initialization with default parameters."""
        workflow = PlanningWorkflow()

        assert workflow._project_root is not None
        assert workflow._state_file is not None
        assert workflow._tasks_dir is not None
        assert str(workflow._state_file).endswith(".claude/workflow-state.json")
        assert str(workflow._tasks_dir).endswith("docs/TASKS")

    def test_init_custom_project_root(self, tmp_path: Path) -> None:
        """Test initialization with custom project root."""
        workflow = PlanningWorkflow(project_root=tmp_path)

        assert workflow._project_root == tmp_path
        assert workflow._state_file == tmp_path / ".claude" / "workflow-state.json"
        assert workflow._tasks_dir == tmp_path / "docs" / "TASKS"

    def test_init_custom_state_file(self, tmp_path: Path) -> None:
        """Test initialization with custom state file."""
        custom_state = tmp_path / "custom-state.json"
        workflow = PlanningWorkflow(state_file=custom_state)

        assert workflow._state_file == custom_state


class TestCreateTaskWithReview:
    """Test PlanningWorkflow.create_task_with_review() method."""

    def test_create_task_generates_file(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """Test task document creation."""
        workflow = PlanningWorkflow(project_root=tmp_path)

        result = workflow.create_task_with_review(
            task_id="P1T99", title="Test Task", description="Test description", estimated_hours=4.0
        )

        # Verify file was created
        task_file = tmp_path / "docs" / "TASKS" / "P1T99_TASK.md"
        assert task_file.exists()

        # Verify return value
        assert result == "docs/TASKS/P1T99_TASK.md"

        # Verify file content
        content = task_file.read_text()
        assert "P1T99: Test Task" in content
        assert "Test description" in content
        assert "Estimated Hours:** 4.0h" in content
        assert "Status:** DRAFT" in content

        # Verify output messages
        captured = capsys.readouterr()
        assert "Task document created" in captured.out
        assert "Requesting task creation review" in captured.out

    def test_create_task_with_zero_hours(self, tmp_path: Path) -> None:
        """Test task creation with 0 hour estimate."""
        workflow = PlanningWorkflow(project_root=tmp_path)

        workflow.create_task_with_review(
            task_id="P1T100", title="Quick fix", description="Minor fix", estimated_hours=0.0
        )

        task_file = tmp_path / "docs" / "TASKS" / "P1T100_TASK.md"
        assert task_file.exists()

        content = task_file.read_text()
        assert "Estimated Hours:** 0.0h" in content

    def test_create_task_creates_directory(self, tmp_path: Path) -> None:
        """Test task creation creates docs/TASKS directory if missing."""
        workflow = PlanningWorkflow(project_root=tmp_path)

        # Ensure directory doesn't exist
        tasks_dir = tmp_path / "docs" / "TASKS"
        assert not tasks_dir.exists()

        workflow.create_task_with_review(
            task_id="P1T101", title="Test", description="Test", estimated_hours=1.0
        )

        # Directory should be created
        assert tasks_dir.exists()
        assert tasks_dir.is_dir()


class TestPlanSubfeatures:
    """Test PlanningWorkflow.plan_subfeatures() method."""

    def test_simple_task_no_split(self, capsys: pytest.CaptureFixture) -> None:
        """Test task <4h returns no subfeatures."""
        workflow = PlanningWorkflow()

        components = [{"name": "Component 1", "hours": 2}, {"name": "Component 2", "hours": 1}]

        result = workflow.plan_subfeatures("P1T14", components)

        assert result == []
        captured = capsys.readouterr()
        assert "simple (<4h)" in captured.out
        assert "no subfeature split" in captured.out

    def test_complex_task_split(self, capsys: pytest.CaptureFixture) -> None:
        """Test task ≥8h triggers split."""
        workflow = PlanningWorkflow()

        components = [
            {"name": "Component 1", "hours": 3},
            {"name": "Component 2", "hours": 3},
            {"name": "Component 3", "hours": 3},
        ]

        result = workflow.plan_subfeatures("P1T14", components)

        assert result == ["P1T14-F1", "P1T14-F2", "P1T14-F3"]
        captured = capsys.readouterr()
        assert "complex (≥8h or ≥3 components)" in captured.out
        assert "P1T14-F1: Component 1 (3h)" in captured.out
        assert "P1T14-F2: Component 2 (3h)" in captured.out
        assert "P1T14-F3: Component 3 (3h)" in captured.out

    def test_moderate_task_recommended_split(self, capsys: pytest.CaptureFixture) -> None:
        """Test task 4-8h recommends split."""
        workflow = PlanningWorkflow()

        components = [{"name": "Component 1", "hours": 3}, {"name": "Component 2", "hours": 2}]

        result = workflow.plan_subfeatures("P1T15", components)

        assert result == ["P1T15-F1", "P1T15-F2"]
        captured = capsys.readouterr()
        assert "moderate (4-8h)" in captured.out
        assert "splitting recommended" in captured.out

    def test_exact_8_hour_split(self, capsys: pytest.CaptureFixture) -> None:
        """Test exactly 8h triggers split."""
        workflow = PlanningWorkflow()

        components = [{"name": "Component 1", "hours": 4}, {"name": "Component 2", "hours": 4}]

        result = workflow.plan_subfeatures("P1T16", components)

        assert len(result) == 2
        captured = capsys.readouterr()
        assert "complex" in captured.out

    def test_three_components_triggers_split(self, capsys: pytest.CaptureFixture) -> None:
        """Test ≥3 components triggers split even if <8h total."""
        workflow = PlanningWorkflow()

        components = [
            {"name": "Component 1", "hours": 2},
            {"name": "Component 2", "hours": 2},
            {"name": "Component 3", "hours": 2},
        ]

        result = workflow.plan_subfeatures("P1T17", components)

        assert len(result) == 3
        captured = capsys.readouterr()
        assert "complex (≥8h or ≥3 components)" in captured.out

    def test_components_without_hours(self, capsys: pytest.CaptureFixture) -> None:
        """Test components missing 'hours' key default to 0."""
        workflow = PlanningWorkflow()

        components = [{"name": "Component 1"}, {"name": "Component 2"}]  # Missing 'hours'

        result = workflow.plan_subfeatures("P1T18", components)

        # Total 0h → no split
        assert result == []
        captured = capsys.readouterr()
        assert "simple (<4h)" in captured.out

    def test_empty_components_list(self, capsys: pytest.CaptureFixture) -> None:
        """Test empty components list."""
        workflow = PlanningWorkflow()

        result = workflow.plan_subfeatures("P1T19", [])

        assert result == []
        captured = capsys.readouterr()
        assert "simple (<4h)" in captured.out


class TestStartTaskWithState:
    """Test PlanningWorkflow.start_task_with_state() method."""

    @patch("scripts.workflow_gate.subprocess.run")
    def test_start_task_creates_branch(self, mock_run: MagicMock, tmp_path: Path) -> None:
        """Test task start creates git branch."""
        mock_run.return_value = MagicMock(returncode=0)

        # Mock WorkflowGate to verify delegation
        mock_gate = MagicMock()
        workflow = PlanningWorkflow(project_root=tmp_path, workflow_gate=mock_gate)

        # Create task document with components
        task_doc = """# P1T20-F1: Test
## Components
- Component 1: Validator (2h)
- Component 2: API (3h)
"""
        task_file = tmp_path / "docs" / "TASKS" / "P1T20-F1_TASK.md"
        task_file.parent.mkdir(parents=True, exist_ok=True)
        task_file.write_text(task_doc)

        workflow.start_task_with_state("P1T20-F1", "feat/test")

        # Verify git checkout called
        mock_run.assert_called()
        call_args = mock_run.call_args_list[0][0][0]
        assert call_args[:3] == ["git", "checkout", "-b"]

        # Verify WorkflowGate methods called
        mock_gate.reset.assert_called_once()
        mock_gate.set_component.assert_called_once_with("Component 1: Validator")

    @patch("scripts.workflow_gate.subprocess.run")
    def test_start_task_branch_exists(self, mock_run: MagicMock, tmp_path: Path) -> None:
        """Test task start when branch already exists."""
        # First call fails (branch exists), second succeeds (checkout)
        mock_run.side_effect = [
            MagicMock(returncode=1),  # checkout -b fails
            MagicMock(returncode=0),  # checkout succeeds
        ]

        mock_gate = MagicMock()
        workflow = PlanningWorkflow(project_root=tmp_path, workflow_gate=mock_gate)
        task_file = tmp_path / "docs" / "TASKS" / "P1T21_TASK.md"
        task_file.parent.mkdir(parents=True, exist_ok=True)
        task_file.write_text("# P1T21: Test")

        workflow.start_task_with_state("P1T21", "existing-branch")

        # Verify two calls made
        assert mock_run.call_count == 2

    @patch("scripts.workflow_gate.subprocess.run")
    def test_start_task_initializes_state(self, mock_run: MagicMock, tmp_path: Path) -> None:
        """Test task start delegates state initialization to WorkflowGate."""
        mock_run.return_value = MagicMock(returncode=0)

        # Mock WorkflowGate to verify delegation
        mock_gate = MagicMock()
        workflow = PlanningWorkflow(project_root=tmp_path, workflow_gate=mock_gate)

        task_file = tmp_path / "docs" / "TASKS" / "P1T22_TASK.md"
        task_file.parent.mkdir(parents=True, exist_ok=True)
        task_file.write_text(
            """# P1T22: Test
## Components
- Component 1 (2h)
"""
        )

        workflow.start_task_with_state("P1T22", "feat/test")

        # Verify WorkflowGate.reset() called
        mock_gate.reset.assert_called_once()

        # Verify WorkflowGate.set_component() called with first component
        mock_gate.set_component.assert_called_once_with("Component 1")

    @patch("scripts.workflow_gate.subprocess.run")
    def test_start_task_branch_failure(
        self, mock_run: MagicMock, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """Test task start raises RuntimeError on branch creation failure."""
        mock_run.side_effect = [
            MagicMock(returncode=1, stderr="branch error"),  # checkout -b fails
            MagicMock(returncode=1, stderr="branch error"),  # checkout fails
        ]

        mock_gate = MagicMock()
        workflow = PlanningWorkflow(project_root=tmp_path, workflow_gate=mock_gate)
        task_file = tmp_path / "docs" / "TASKS" / "P1T23_TASK.md"
        task_file.parent.mkdir(parents=True, exist_ok=True)
        task_file.write_text("# P1T23: Test")

        # Should raise RuntimeError with stderr details instead of returning gracefully (P1 fix)
        with pytest.raises(
            RuntimeError, match=r"Failed to create/checkout branch bad-branch.*branch error"
        ):
            workflow.start_task_with_state("P1T23", "bad-branch")

        # Verify error message was printed before raising
        captured = capsys.readouterr()
        assert "Failed to create/checkout branch" in captured.out

    @patch("scripts.workflow_gate.subprocess.run")
    def test_start_task_invokes_update_state_script(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """Test task start invokes update_task_state.py with correct arguments."""
        # Create stub update_task_state.py script
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        update_script = scripts_dir / "update_task_state.py"
        update_script.write_text("#!/usr/bin/env python3\n# Stub script")

        # Mock subprocess.run to track calls
        mock_run.return_value = MagicMock(returncode=0)

        mock_gate = MagicMock()
        workflow = PlanningWorkflow(project_root=tmp_path, workflow_gate=mock_gate)

        task_file = tmp_path / "docs" / "TASKS" / "P1T24_TASK.md"
        task_file.parent.mkdir(parents=True, exist_ok=True)
        task_file.write_text(
            """# P1T24: Test
## Components
- Component 1 (2h)
- Component 2 (3h)
"""
        )

        workflow.start_task_with_state("P1T24", "feat/test")

        # Verify subprocess.run was called twice: git + update_task_state.py
        assert mock_run.call_count >= 2

        # Find the update_task_state.py invocation
        update_call = None
        for call in mock_run.call_args_list:
            args = call[0][0]  # First positional arg (command list)
            if "update_task_state.py" in str(args):
                update_call = call
                break

        assert update_call is not None, "update_task_state.py was not invoked"

        # Verify command structure
        cmd = update_call[0][0]
        assert cmd[0] == sys.executable, f"Should use sys.executable, got {cmd[0]}"
        assert str(update_script) in str(cmd[1]), "Should reference update_task_state.py"
        assert "start" in cmd, "Should include 'start' command"
        assert "--task" in cmd, "Should include --task argument"
        assert "P1T24" in cmd, "Should include task ID"
        assert "--branch" in cmd, "Should include --branch argument"
        assert "feat/test" in cmd, "Should include branch name"
        assert "--components" in cmd, "Should include --components argument"
        assert "2" in cmd, "Should include component count"

        # Verify check=True for error propagation
        assert update_call[1]["check"] is True, "Should use check=True for error handling"

    @patch("scripts.workflow_gate.subprocess.run")
    def test_start_task_handles_update_state_failure(
        self, mock_run: MagicMock, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """Test task start FAILS LOUDLY when update_task_state.py fails (prevents inconsistent state)."""
        # Create stub update_task_state.py script
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        update_script = scripts_dir / "update_task_state.py"
        update_script.write_text("#!/usr/bin/env python3\n# Stub script")

        # Mock subprocess.run: git succeeds, update_task_state.py fails
        def side_effect_fn(*args, **kwargs):
            cmd = args[0]
            if "update_task_state.py" in str(cmd):
                raise subprocess.CalledProcessError(
                    returncode=1, cmd=cmd, stderr="update_task_state.py failed"
                )
            return MagicMock(returncode=0)

        mock_run.side_effect = side_effect_fn

        mock_gate = MagicMock()
        workflow = PlanningWorkflow(project_root=tmp_path, workflow_gate=mock_gate)

        task_file = tmp_path / "docs" / "TASKS" / "P1T25_TASK.md"
        task_file.parent.mkdir(parents=True, exist_ok=True)
        task_file.write_text(
            """# P1T25: Test
## Components
- Component 1 (2h)
"""
        )

        # Should RAISE exception (fails loudly to prevent inconsistent state)
        # CRITICAL: Task tracking and workflow must stay synchronized
        with pytest.raises(subprocess.CalledProcessError):
            workflow.start_task_with_state("P1T25", "feat/test")

        # Verify WorkflowGate methods were NOT called (workflow aborted on error)
        mock_gate.reset.assert_not_called()
        mock_gate.set_component.assert_not_called()


class TestGenerateTaskDoc:
    """Test PlanningWorkflow._generate_task_doc() method."""

    def test_generate_task_doc_creates_file(self, tmp_path: Path) -> None:
        """Test task document generation creates file."""
        workflow = PlanningWorkflow(project_root=tmp_path)

        result = workflow._generate_task_doc(
            task_id="P1T50",
            title="Test Feature",
            description="Feature description here",
            estimated_hours=5.5,
        )

        assert result.exists()
        assert result.name == "P1T50_TASK.md"

    def test_generate_task_doc_content_format(self, tmp_path: Path) -> None:
        """Test generated task document has correct format."""
        workflow = PlanningWorkflow(project_root=tmp_path)

        task_file = workflow._generate_task_doc(
            task_id="P1T51",
            title="Position Monitoring",
            description="Monitor position limits",
            estimated_hours=8.0,
        )

        content = task_file.read_text()

        # Check required sections
        assert "# P1T51: Position Monitoring" in content
        assert "**Status:** DRAFT" in content
        assert "**Estimated Hours:** 8.0h" in content
        assert "Monitor position limits" in content
        assert "## Components" in content
        assert "## Acceptance Criteria" in content
        assert "## Implementation Notes" in content
        assert "## Testing Strategy" in content
        assert "## Dependencies" in content
        assert "PlanningWorkflow" in content  # Attribution

    def test_generate_task_doc_with_multiline_description(self, tmp_path: Path) -> None:
        """Test task document generation with multiline description."""
        workflow = PlanningWorkflow(project_root=tmp_path)

        description = """This is a multi-line
description with
several lines."""

        task_file = workflow._generate_task_doc(
            task_id="P1T52", title="Test", description=description, estimated_hours=1.0
        )

        content = task_file.read_text()
        assert "multi-line\ndescription" in content


class TestLoadTaskDoc:
    """Test PlanningWorkflow._load_task_doc() method."""

    def test_load_task_doc_existing_file(self, tmp_path: Path) -> None:
        """Test loading existing task document."""
        workflow = PlanningWorkflow(project_root=tmp_path)

        # Create task file
        task_file = tmp_path / "docs" / "TASKS" / "P1T60_TASK.md"
        task_file.parent.mkdir(parents=True, exist_ok=True)
        task_content = "# P1T60: Test\nContent here"
        task_file.write_text(task_content)

        result = workflow._load_task_doc("P1T60")

        assert result == task_content

    def test_load_task_doc_missing_file(self, tmp_path: Path) -> None:
        """Test loading non-existent task document returns empty string."""
        workflow = PlanningWorkflow(project_root=tmp_path)

        result = workflow._load_task_doc("P1T_NONEXISTENT")

        assert result == ""


class TestExtractComponents:
    """Test PlanningWorkflow._extract_components() method."""

    def test_extract_components_simple(self) -> None:
        """Test extracting components from simple task doc."""
        workflow = PlanningWorkflow()

        task_doc = """# Task
## Components
- Component 1: Validator (2h)
- Component 2: API endpoint (3h)
"""

        result = workflow._extract_components(task_doc)

        assert len(result) == 2
        assert result[0] == {"name": "Component 1: Validator", "hours": 2.0}
        assert result[1] == {"name": "Component 2: API endpoint", "hours": 3.0}

    def test_extract_components_no_hours(self) -> None:
        """Test extracting components without hours."""
        workflow = PlanningWorkflow()

        task_doc = """# Task
## Components
- Component without hours
- Another component
"""

        result = workflow._extract_components(task_doc)

        assert len(result) == 2
        assert result[0] == {"name": "Component without hours", "hours": 0.0}
        assert result[1] == {"name": "Another component", "hours": 0.0}

    def test_extract_components_decimal_hours(self) -> None:
        """Test extracting components with decimal hours."""
        workflow = PlanningWorkflow()

        task_doc = """# Task
## Components
- Component 1 (2.5h)
- Component 2 (0.5h)
"""

        result = workflow._extract_components(task_doc)

        assert len(result) == 2
        assert result[0]["hours"] == 2.5
        assert result[1]["hours"] == 0.5

    def test_extract_components_empty_section(self) -> None:
        """Test extracting from empty components section."""
        workflow = PlanningWorkflow()

        task_doc = """# Task
## Components

## Next Section
"""

        result = workflow._extract_components(task_doc)

        assert result == []

    def test_extract_components_no_section(self) -> None:
        """Test extracting when no components section exists."""
        workflow = PlanningWorkflow()

        task_doc = """# Task
Some content here
"""

        result = workflow._extract_components(task_doc)

        assert result == []

    def test_extract_components_invalid_hours(self) -> None:
        """Test extracting components with invalid hours format."""
        workflow = PlanningWorkflow()

        task_doc = """# Task
## Components
- Component 1 (invalid_hours)
- Component 2 (2h)
"""

        result = workflow._extract_components(task_doc)

        # Invalid hours default to 0
        assert result[0]["hours"] == 0.0
        assert result[1]["hours"] == 2.0

    def test_extract_components_multiple_parentheses(self) -> None:
        """Test extracting components with multiple parentheses."""
        workflow = PlanningWorkflow()

        task_doc = """# Task
## Components
- Component (with description) (2h)
"""

        result = workflow._extract_components(task_doc)

        # Should extract hours from last occurrence
        assert result[0]["name"] == "Component (with description)"
        assert result[0]["hours"] == 2.0


class TestEdgeCases:
    """Test PlanningWorkflow edge cases and error handling."""

    def test_create_task_special_characters(self, tmp_path: Path) -> None:
        """Test task creation with special characters in content."""
        workflow = PlanningWorkflow(project_root=tmp_path)

        workflow.create_task_with_review(
            task_id="P1T_SPECIAL",
            title="Test: Feature (v2.0)",
            description="Description with \"quotes\" and 'apostrophes'",
            estimated_hours=1.0,
        )

        task_file = tmp_path / "docs" / "TASKS" / "P1T_SPECIAL_TASK.md"
        assert task_file.exists()

        content = task_file.read_text()
        assert "Test: Feature (v2.0)" in content
        assert '"quotes"' in content

    def test_plan_subfeatures_very_large_task(self, capsys: pytest.CaptureFixture) -> None:
        """Test subfeature planning for very large task."""
        workflow = PlanningWorkflow()

        # 100h task with 20 components
        components = [{"name": f"Component {i}", "hours": 5} for i in range(20)]

        result = workflow.plan_subfeatures("P1T_BIG", components)

        assert len(result) == 20
        assert result[0] == "P1T_BIG-F1"
        assert result[-1] == "P1T_BIG-F20"

    @patch("scripts.workflow_gate.subprocess.run")
    def test_start_task_with_empty_task_doc(
        self, mock_run: MagicMock, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """Test task start with empty task document."""
        mock_run.return_value = MagicMock(returncode=0)

        workflow = PlanningWorkflow(project_root=tmp_path)
        task_file = tmp_path / "docs" / "TASKS" / "P1T_EMPTY_TASK.md"
        task_file.parent.mkdir(parents=True, exist_ok=True)
        task_file.write_text("")

        workflow.start_task_with_state("P1T_EMPTY", "feat/test")

        # Should handle gracefully
        captured = capsys.readouterr()
        assert "Task P1T_EMPTY started" in captured.out
        assert "Components: 0" in captured.out


class TestIntegration:
    """Integration tests with real file system."""

    def test_full_workflow_integration(self, tmp_path: Path) -> None:
        """Test complete workflow: create → plan → start."""
        # Mock WorkflowGate to verify delegation
        mock_gate = MagicMock()
        workflow = PlanningWorkflow(project_root=tmp_path, workflow_gate=mock_gate)

        # Step 1: Create task
        task_file = workflow.create_task_with_review(
            task_id="P1T99",
            title="Full Integration Test",
            description="End-to-end test",
            estimated_hours=10.0,
        )

        assert Path(tmp_path / task_file).exists()

        # Step 2: Plan subfeatures
        components = [
            {"name": "Component 1", "hours": 4},
            {"name": "Component 2", "hours": 3},
            {"name": "Component 3", "hours": 3},
        ]

        subfeatures = workflow.plan_subfeatures("P1T99", components)

        assert len(subfeatures) == 3
        assert subfeatures == ["P1T99-F1", "P1T99-F2", "P1T99-F3"]

        # Step 3: Add components to task doc manually
        task_path = tmp_path / task_file
        content = task_path.read_text()
        content = content.replace(
            "<!-- List logical components here -->",
            "- Component 1 (4h)\n- Component 2 (3h)\n- Component 3 (3h)",
        )
        task_path.write_text(content)

        # Step 4: Start task (with mocked git and WorkflowGate)
        with patch("scripts.workflow_gate.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            workflow.start_task_with_state("P1T99", "feat/integration-test")

        # Verify WorkflowGate methods called
        mock_gate.reset.assert_called_once()
        mock_gate.set_component.assert_called_once_with("Component 1")
