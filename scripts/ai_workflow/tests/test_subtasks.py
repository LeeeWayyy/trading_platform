"""
Tests for subtasks.py module.

Tests SubtaskOrchestrator for context-isolated subtask management.
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from ai_workflow.subtasks import (
    AgentInstruction,
    SubagentPrompts,
    SubtaskOrchestrator,
    SubtaskStatus,
    SubtaskType,
    validate_file_path,
)


class TestSubtaskType:
    """Tests for SubtaskType enum."""

    def test_review_files(self):
        """Should have REVIEW_FILES type."""
        assert SubtaskType.REVIEW_FILES.value == "review_files"

    def test_fix_comments(self):
        """Should have FIX_COMMENTS type."""
        assert SubtaskType.FIX_COMMENTS.value == "fix_comments"

    def test_run_tests(self):
        """Should have RUN_TESTS type."""
        assert SubtaskType.RUN_TESTS.value == "run_tests"


class TestSubtaskStatus:
    """Tests for SubtaskStatus enum."""

    def test_queued(self):
        """Should have QUEUED status."""
        assert SubtaskStatus.QUEUED.value == "queued"

    def test_delegated(self):
        """Should have DELEGATED status."""
        assert SubtaskStatus.DELEGATED.value == "delegated"

    def test_completed(self):
        """Should have COMPLETED status."""
        assert SubtaskStatus.COMPLETED.value == "completed"

    def test_failed(self):
        """Should have FAILED status."""
        assert SubtaskStatus.FAILED.value == "failed"


class TestAgentInstruction:
    """Tests for AgentInstruction dataclass."""

    def test_creates_instruction(self):
        """Should create instruction with all fields."""
        instruction = AgentInstruction(
            id="task-123",
            action="delegate_to_subagent",
            tool="mcp__zen__clink",
            params={"cli_name": "claude", "prompt": "test"},
        )

        assert instruction.id == "task-123"
        assert instruction.action == "delegate_to_subagent"
        assert instruction.tool == "mcp__zen__clink"
        assert instruction.params["cli_name"] == "claude"

    def test_to_dict(self):
        """Should convert to dictionary."""
        instruction = AgentInstruction(
            id="task-456",
            action="delegate",
            tool="some_tool",
            params={"key": "value"},
        )

        result = instruction.to_dict()

        assert isinstance(result, dict)
        assert result["id"] == "task-456"
        assert result["action"] == "delegate"
        assert result["tool"] == "some_tool"
        assert result["params"]["key"] == "value"


class TestValidateFilePath:
    """Tests for validate_file_path function."""

    def test_validates_path_within_project(self, temp_dir):
        """Should accept paths within project directory."""
        test_file = temp_dir / "src" / "main.py"
        test_file.parent.mkdir(parents=True)
        test_file.touch()

        result = validate_file_path(str(test_file), temp_dir)
        assert result == str(test_file.resolve())

    def test_rejects_path_outside_project(self, temp_dir):
        """Should reject paths outside project directory."""
        outside_path = "/tmp/outside.py"

        with pytest.raises(ValueError, match="Security"):
            validate_file_path(outside_path, temp_dir)

    def test_rejects_path_traversal(self, temp_dir):
        """Should reject path traversal attempts."""
        traversal_path = str(temp_dir / ".." / ".." / "etc" / "passwd")

        with pytest.raises(ValueError, match="Security"):
            validate_file_path(traversal_path, temp_dir)

    def test_resolves_relative_path(self, temp_dir):
        """Should resolve relative paths to absolute when in project dir."""
        test_file = temp_dir / "file.py"
        test_file.touch()

        # Use absolute path within project directory
        result = validate_file_path(str(test_file), temp_dir)

        assert Path(result).is_absolute()
        assert result == str(test_file.resolve())


class TestSubagentPrompts:
    """Tests for SubagentPrompts class."""

    def test_fix_comments_prompt(self, temp_dir):
        """Should generate fix comments prompt."""
        test_file = temp_dir / "src" / "main.py"
        test_file.parent.mkdir(parents=True)
        test_file.touch()

        with patch("pathlib.Path.cwd", return_value=temp_dir):
            prompt = SubagentPrompts.fix_comments_prompt(str(test_file), [123, 456], pr_number=789)

        assert "sub-agent" in prompt.lower()
        assert "[123, 456]" in prompt
        assert "789" in prompt
        assert "JSON" in prompt

    def test_review_files_prompt(self, temp_dir):
        """Should generate review files prompt."""
        file1 = temp_dir / "src" / "main.py"
        file2 = temp_dir / "src" / "utils.py"
        file1.parent.mkdir(parents=True)
        file1.touch()
        file2.touch()

        with patch("pathlib.Path.cwd", return_value=temp_dir):
            prompt = SubagentPrompts.review_files_prompt([str(file1), str(file2)])

        assert "sub-agent" in prompt.lower()
        assert "security" in prompt.lower()
        assert "JSON" in prompt


class TestSubtaskOrchestratorInit:
    """Tests for SubtaskOrchestrator initialization."""

    def test_initializes_with_state(self, temp_dir):
        """Should initialize with provided state."""
        state = {}
        config_file = temp_dir / ".ai_workflow" / "config.json"

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            with patch("ai_workflow.config.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                orchestrator = SubtaskOrchestrator(state)

        assert orchestrator.state is state

    def test_ensures_subtask_state(self, temp_dir):
        """Should ensure subtasks state structure exists."""
        state = {}
        config_file = temp_dir / ".ai_workflow" / "config.json"

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            with patch("ai_workflow.config.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                SubtaskOrchestrator(state)

        assert "subtasks" in state
        assert "queue" in state["subtasks"]
        assert "completed" in state["subtasks"]
        assert "failed" in state["subtasks"]


class TestGetPreferredCLI:
    """Tests for _get_preferred_cli method."""

    def test_returns_first_enabled_reviewer(self, temp_dir):
        """Should return first enabled reviewer."""
        state = {}
        config_file = temp_dir / ".ai_workflow" / "config.json"

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            with patch("ai_workflow.config.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                orchestrator = SubtaskOrchestrator(state)
                cli = orchestrator._get_preferred_cli()

        # Default enabled is ["gemini", "codex"]
        assert cli == "gemini"

    def test_raises_when_no_reviewers(self, temp_dir):
        """Should raise when no reviewers enabled."""
        state = {}
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
                },
                f,
            )

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            orchestrator = SubtaskOrchestrator(state)

            with pytest.raises(ValueError, match="No reviewers enabled"):
                orchestrator._get_preferred_cli()


class TestCreateAgentInstructions:
    """Tests for create_agent_instructions method."""

    def test_creates_instructions(self, temp_dir):
        """Should create agent instructions for each file."""
        state = {"subtasks": {"queue": [], "completed": [], "failed": []}}
        config_file = temp_dir / ".ai_workflow" / "config.json"

        # Create test files
        file1 = temp_dir / "src" / "main.py"
        file2 = temp_dir / "src" / "utils.py"
        file1.parent.mkdir(parents=True)
        file1.touch()
        file2.touch()

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            with patch("ai_workflow.config.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                with patch("pathlib.Path.cwd", return_value=temp_dir):
                    orchestrator = SubtaskOrchestrator(state)
                    instructions = orchestrator.create_agent_instructions(
                        pr_number=123,
                        comments_by_file={
                            str(file1): [1, 2, 3],
                            str(file2): [4, 5],
                        },
                    )

        assert len(instructions) == 2
        assert all(isinstance(i, AgentInstruction) for i in instructions)
        assert all(i.tool == "mcp__zen__clink" for i in instructions)

    def test_adds_tasks_to_queue(self, temp_dir):
        """Should add tasks to queue in state."""
        state = {"subtasks": {"queue": [], "completed": [], "failed": []}}
        config_file = temp_dir / ".ai_workflow" / "config.json"

        test_file = temp_dir / "main.py"
        test_file.touch()

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            with patch("ai_workflow.config.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                with patch("pathlib.Path.cwd", return_value=temp_dir):
                    orchestrator = SubtaskOrchestrator(state)
                    orchestrator.create_agent_instructions(
                        pr_number=123, comments_by_file={str(test_file): [1, 2]}
                    )

        assert len(state["subtasks"]["queue"]) == 1
        task = state["subtasks"]["queue"][0]
        assert task["type"] == "fix_comments"
        assert task["comment_count"] == 2
        assert task["status"] == "queued"

    def test_uses_specified_cli(self, temp_dir):
        """Should use specified CLI name."""
        state = {"subtasks": {"queue": [], "completed": [], "failed": []}}
        config_file = temp_dir / ".ai_workflow" / "config.json"

        test_file = temp_dir / "main.py"
        test_file.touch()

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            with patch("ai_workflow.config.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                with patch("pathlib.Path.cwd", return_value=temp_dir):
                    orchestrator = SubtaskOrchestrator(state)
                    instructions = orchestrator.create_agent_instructions(
                        pr_number=123,
                        comments_by_file={str(test_file): [1]},
                        cli_name="gemini",
                    )

        assert instructions[0].params["cli_name"] == "gemini"


class TestOutputInstructionsJson:
    """Tests for output_instructions_json method."""

    def test_outputs_valid_json(self, temp_dir):
        """Should output valid JSON."""
        state = {"subtasks": {"queue": [], "completed": [], "failed": []}}
        config_file = temp_dir / ".ai_workflow" / "config.json"

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            with patch("ai_workflow.config.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                orchestrator = SubtaskOrchestrator(state)
                instructions = [
                    AgentInstruction(
                        id="task-1",
                        action="delegate",
                        tool="test_tool",
                        params={"key": "value"},
                    )
                ]
                output = orchestrator.output_instructions_json(instructions)

        parsed = json.loads(output)
        assert parsed["action"] == "delegate_subtasks"
        assert len(parsed["tasks"]) == 1


class TestMarkDelegated:
    """Tests for mark_delegated method."""

    def test_marks_task_delegated(self, temp_dir):
        """Should mark task as delegated."""
        state = {
            "subtasks": {
                "queue": [{"id": "task-123", "status": "queued"}],
                "completed": [],
                "failed": [],
            }
        }
        config_file = temp_dir / ".ai_workflow" / "config.json"

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            with patch("ai_workflow.config.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                orchestrator = SubtaskOrchestrator(state)
                result = orchestrator.mark_delegated("task-123")

        assert result is True
        assert state["subtasks"]["queue"][0]["status"] == "delegated"
        assert "delegated_at" in state["subtasks"]["queue"][0]

    def test_returns_false_for_unknown_task(self, temp_dir):
        """Should return False for unknown task."""
        state = {"subtasks": {"queue": [], "completed": [], "failed": []}}
        config_file = temp_dir / ".ai_workflow" / "config.json"

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            with patch("ai_workflow.config.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                orchestrator = SubtaskOrchestrator(state)
                result = orchestrator.mark_delegated("unknown-task")

        assert result is False


class TestParseSubagentResponse:
    """Tests for parse_subagent_response method."""

    def test_parses_valid_json(self, temp_dir):
        """Should parse valid JSON response."""
        state = {"subtasks": {"queue": [], "completed": [], "failed": []}}
        config_file = temp_dir / ".ai_workflow" / "config.json"

        response = '```json\n{"summary": "Fixed 2 issues", "fixed": 2}\n```'

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            with patch("ai_workflow.config.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                orchestrator = SubtaskOrchestrator(state)
                result = orchestrator.parse_subagent_response(response)

        assert result["success"] is True
        assert result["summary"] == "Fixed 2 issues"

    def test_parses_raw_json(self, temp_dir):
        """Should parse raw JSON without markdown."""
        state = {"subtasks": {"queue": [], "completed": [], "failed": []}}
        config_file = temp_dir / ".ai_workflow" / "config.json"

        response = '{"summary": "Done", "status": "ok"}'

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            with patch("ai_workflow.config.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                orchestrator = SubtaskOrchestrator(state)
                result = orchestrator.parse_subagent_response(response)

        assert result["success"] is True
        assert result["summary"] == "Done"

    def test_handles_invalid_json(self, temp_dir):
        """Should handle invalid JSON gracefully."""
        state = {"subtasks": {"queue": [], "completed": [], "failed": []}}
        config_file = temp_dir / ".ai_workflow" / "config.json"

        response = "This is not JSON"

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            with patch("ai_workflow.config.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                orchestrator = SubtaskOrchestrator(state)
                result = orchestrator.parse_subagent_response(response)

        assert result["success"] is False
        assert "No JSON found" in result["error"]

    def test_handles_error_status(self, temp_dir):
        """Should detect error status in response."""
        state = {"subtasks": {"queue": [], "completed": [], "failed": []}}
        config_file = temp_dir / ".ai_workflow" / "config.json"

        response = '{"summary": "Failed", "error": "Something went wrong"}'

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            with patch("ai_workflow.config.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                orchestrator = SubtaskOrchestrator(state)
                result = orchestrator.parse_subagent_response(response)

        assert result["success"] is False
        assert "Something went wrong" in result["error"]


class TestRecordCompletion:
    """Tests for record_completion method."""

    def test_records_successful_completion(self, temp_dir):
        """Should record successful completion."""
        state = {
            "subtasks": {
                "queue": [{"id": "task-123", "status": "delegated"}],
                "completed": [],
                "failed": [],
            }
        }
        config_file = temp_dir / ".ai_workflow" / "config.json"

        response = '{"summary": "Fixed all issues"}'

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            with patch("ai_workflow.config.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                orchestrator = SubtaskOrchestrator(state)
                success = orchestrator.record_completion("task-123", response)

        assert success is True
        assert state["subtasks"]["queue"][0]["status"] == "completed"
        assert len(state["subtasks"]["completed"]) == 1

    def test_records_failed_completion(self, temp_dir):
        """Should record failed completion."""
        state = {
            "subtasks": {
                "queue": [{"id": "task-456", "status": "delegated"}],
                "completed": [],
                "failed": [],
            }
        }
        config_file = temp_dir / ".ai_workflow" / "config.json"

        response = "Invalid response"

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            with patch("ai_workflow.config.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                orchestrator = SubtaskOrchestrator(state)
                success = orchestrator.record_completion("task-456", response)

        assert success is False
        assert state["subtasks"]["queue"][0]["status"] == "failed"
        assert len(state["subtasks"]["failed"]) == 1


class TestGetStatusSummary:
    """Tests for get_status_summary method."""

    def test_returns_status_counts(self, temp_dir):
        """Should return status counts."""
        state = {
            "subtasks": {
                "queue": [
                    {"id": "task-1", "status": "queued"},
                    {"id": "task-2", "status": "delegated"},
                    {"id": "task-3", "status": "completed"},
                ],
                "completed": [{"id": "task-3"}],
                "failed": [],
            }
        }
        config_file = temp_dir / ".ai_workflow" / "config.json"

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            with patch("ai_workflow.config.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                orchestrator = SubtaskOrchestrator(state)
                summary = orchestrator.get_status_summary()

        assert summary["total"] == 3
        assert summary["queued"] == 1
        assert summary["delegated"] == 1
        assert summary["completed"] == 1
        assert summary["failed"] == 0
