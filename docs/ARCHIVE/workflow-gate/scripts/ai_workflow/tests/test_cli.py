"""
Integration tests for workflow_gate.py CLI.

Tests the CLI commands for the AI workflow enforcement system.

Note: pytest.ini configures pythonpath = . which adds project root to sys.path.
This enables imports like 'from scripts.workflow_gate import ...' without sys.path manipulation.
"""

import json
from unittest.mock import MagicMock, patch

# Gemini MEDIUM fix: Removed sys.path manipulation - pytest.ini handles pythonpath


class TestCLIStatus:
    """Tests for status command."""

    def test_status_returns_json(self, temp_dir):
        """Should return JSON status."""
        state_file = temp_dir / ".ai_workflow" / "workflow-state.json"
        state_file.parent.mkdir(parents=True)

        state = {
            "version": "2.0",
            "phase": "component",
            "component": {"current": "TestComp", "step": "implement", "list": []},
        }
        with open(state_file, "w") as f:
            json.dump(state, f)

        with patch("scripts.admin.workflow_gate.STATE_FILE", state_file):
            with patch("scripts.admin.workflow_gate.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                # Capture stdout
                import io
                from contextlib import redirect_stdout

                from scripts.admin.workflow_gate import cmd_status

                f = io.StringIO()
                with redirect_stdout(f):
                    result = cmd_status(MagicMock())

                output = f.getvalue()
                parsed = json.loads(output)

        assert result == 0
        assert parsed["phase"] == "component"
        assert parsed["step"] == "implement"


class TestCLIStartTask:
    """Tests for start-task command."""

    def test_start_task_creates_state(self, temp_dir):
        """Should create initial state for task."""
        state_file = temp_dir / ".ai_workflow" / "workflow-state.json"
        temp_dir / ".ai_workflow" / "config.json"
        state_file.parent.mkdir(parents=True)

        # Write initial empty state
        with open(state_file, "w") as f:
            json.dump(
                {
                    "version": "2.0",
                    "phase": "component",
                    "component": {},
                    "git": {},
                },
                f,
            )

        with patch("scripts.admin.workflow_gate.STATE_FILE", state_file):
            with patch("scripts.admin.workflow_gate.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                from scripts.admin.workflow_gate import cmd_start_task

                args = MagicMock()
                args.task_file = "task-123.md"
                args.branch = "feature/test"
                args.base_branch = "master"

                import io
                from contextlib import redirect_stdout

                f = io.StringIO()
                with redirect_stdout(f):
                    result = cmd_start_task(args)

        assert result == 0

        with open(state_file) as f:
            saved = json.load(f)
        assert saved["task_file"] == "task-123.md"
        assert saved["git"]["branch"] == "feature/test"


class TestCLISetComponent:
    """Tests for set-component command."""

    def test_sets_component_name(self, temp_dir):
        """Should set current component name."""
        state_file = temp_dir / ".ai_workflow" / "workflow-state.json"
        state_file.parent.mkdir(parents=True)

        state = {
            "version": "2.0",
            "phase": "component",
            "component": {"current": "", "step": "plan", "list": []},
        }
        with open(state_file, "w") as f:
            json.dump(state, f)

        with patch("scripts.admin.workflow_gate.STATE_FILE", state_file):
            with patch("scripts.admin.workflow_gate.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                from scripts.admin.workflow_gate import cmd_set_component

                args = MagicMock()
                args.name = "NewComponent"

                result = cmd_set_component(args)

        assert result == 0

        with open(state_file) as f:
            saved = json.load(f)
        assert saved["component"]["current"] == "NewComponent"
        assert "NewComponent" in saved["component"]["list"]


class TestCLIAdvance:
    """Tests for advance command."""

    def test_advance_valid_transition(self, temp_dir):
        """Should advance to valid next step."""
        state_file = temp_dir / ".ai_workflow" / "workflow-state.json"
        state_file.parent.mkdir(parents=True)

        state = {
            "version": "2.0",
            "component": {"current": "Test", "step": "plan", "list": []},
        }
        with open(state_file, "w") as f:
            json.dump(state, f)

        # Need to patch both STATE_FILE and _gate since cmd_advance delegates to _gate.advance()
        from ai_workflow.core import WorkflowGate

        test_gate = WorkflowGate(state_file=state_file)

        with patch("scripts.admin.workflow_gate.STATE_FILE", state_file):
            with patch("scripts.admin.workflow_gate.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                with patch("scripts.admin.workflow_gate._gate", test_gate):
                    from scripts.admin.workflow_gate import cmd_advance

                    args = MagicMock()
                    args.step = "plan-review"

                    result = cmd_advance(args)

        assert result == 0

        with open(state_file) as f:
            saved = json.load(f)
        assert saved["component"]["step"] == "plan-review"

    def test_advance_invalid_transition(self, temp_dir, capsys):
        """Should reject invalid transition."""
        state_file = temp_dir / ".ai_workflow" / "workflow-state.json"
        state_file.parent.mkdir(parents=True)

        state = {
            "version": "2.0",
            "component": {"current": "Test", "step": "plan", "list": []},
        }
        with open(state_file, "w") as f:
            json.dump(state, f)

        with patch("scripts.admin.workflow_gate.STATE_FILE", state_file):
            with patch("scripts.admin.workflow_gate.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                from scripts.admin.workflow_gate import cmd_advance

                args = MagicMock()
                args.step = "review"  # Invalid: plan -> review

                result = cmd_advance(args)

        assert result == 1


class TestCLIRecordReview:
    """Tests for record-review command."""

    def test_records_review_status(self, temp_dir):
        """Should record review status."""
        state_file = temp_dir / ".ai_workflow" / "workflow-state.json"
        audit_log = temp_dir / ".ai_workflow" / "audit.log"
        state_file.parent.mkdir(parents=True)

        state = {
            "version": "2.0",
            "phase": "component",
            "component": {"step": "review"},
            "reviews": {},
            "reviewers": {},
        }
        with open(state_file, "w") as f:
            json.dump(state, f)

        # Need to patch _gate since cmd_record_review delegates to _gate.record_review()
        from ai_workflow.core import WorkflowGate

        test_gate = WorkflowGate(state_file=state_file)

        with patch("scripts.admin.workflow_gate.STATE_FILE", state_file):
            with patch("scripts.admin.workflow_gate.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                with patch("scripts.admin.workflow_gate._gate", test_gate):
                    with patch("ai_workflow.core.AUDIT_LOG_FILE", audit_log):
                        from scripts.admin.workflow_gate import cmd_record_review

                        args = MagicMock()
                        args.reviewer = "claude"
                        args.status = "approved"
                        args.continuation_id = "cont-123"

                        import io
                        from contextlib import redirect_stdout

                        f = io.StringIO()
                        with redirect_stdout(f):
                            result = cmd_record_review(args)

        assert result == 0

        with open(state_file) as f:
            saved = json.load(f)
        assert saved["reviews"]["claude"]["status"] == "APPROVED"


class TestCLIRecordCI:
    """Tests for record-ci command."""

    def test_records_ci_passed(self, temp_dir):
        """Should record CI passed status."""
        state_file = temp_dir / ".ai_workflow" / "workflow-state.json"
        state_file.parent.mkdir(parents=True)

        state = {
            "version": "2.0",
            "phase": "component",
            "ci": {},
        }
        with open(state_file, "w") as f:
            json.dump(state, f)

        with patch("scripts.admin.workflow_gate.STATE_FILE", state_file):
            with patch("scripts.admin.workflow_gate.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                from scripts.admin.workflow_gate import cmd_record_ci

                args = MagicMock()
                args.passed = "true"

                import io
                from contextlib import redirect_stdout

                f = io.StringIO()
                with redirect_stdout(f):
                    result = cmd_record_ci(args)

        assert result == 0

        with open(state_file) as f:
            saved = json.load(f)
        assert saved["ci"]["component_passed"] is True


class TestCLICheckCommit:
    """Tests for check-commit command."""

    def test_check_commit_not_ready(self, temp_dir):
        """Should return not ready when checks fail."""
        state_file = temp_dir / ".ai_workflow" / "workflow-state.json"
        config_file = temp_dir / ".ai_workflow" / "config.json"
        state_file.parent.mkdir(parents=True)

        state = {
            "version": "2.0",
            "component": {"current": "", "step": "implement"},
            "ci": {},
            "reviews": {},
        }
        with open(state_file, "w") as f:
            json.dump(state, f)

        # Need to patch _gate since cmd_check_commit delegates to _gate.get_commit_status()
        from ai_workflow.core import WorkflowGate

        test_gate = WorkflowGate(state_file=state_file)

        with patch("scripts.admin.workflow_gate.STATE_FILE", state_file):
            with patch("scripts.admin.workflow_gate.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                with patch("scripts.admin.workflow_gate._gate", test_gate):
                    with patch("ai_workflow.config.CONFIG_FILE", config_file):
                        with patch("ai_workflow.config.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                            from scripts.admin.workflow_gate import cmd_check_commit

                            args = MagicMock()

                            import io
                            from contextlib import redirect_stdout

                            f = io.StringIO()
                            with redirect_stdout(f):
                                result = cmd_check_commit(args)

                            output = f.getvalue()
                            parsed = json.loads(output)

        assert result == 1
        assert parsed["ready"] is False

    def test_check_commit_ready(self, temp_dir):
        """Should return ready when all checks pass."""
        state_file = temp_dir / ".ai_workflow" / "workflow-state.json"
        config_file = temp_dir / ".ai_workflow" / "config.json"
        state_file.parent.mkdir(parents=True)

        state = {
            "version": "2.0",
            "component": {"current": "TestComp", "step": "review", "list": []},
            "ci": {"component_passed": True},
            "reviews": {
                "gemini": {"status": "APPROVED", "continuation_id": "real-gemini-id"},
                "codex": {"status": "APPROVED", "continuation_id": "real-codex-id"},
            },
        }
        with open(state_file, "w") as f:
            json.dump(state, f)

        # Need to patch _gate since cmd_check_commit delegates to _gate.get_commit_status()
        from ai_workflow.core import WorkflowGate

        test_gate = WorkflowGate(state_file=state_file)

        with patch("scripts.admin.workflow_gate.STATE_FILE", state_file):
            with patch("scripts.admin.workflow_gate.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                with patch("scripts.admin.workflow_gate._gate", test_gate):
                    with patch("ai_workflow.config.CONFIG_FILE", config_file):
                        with patch("ai_workflow.config.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                            from scripts.admin.workflow_gate import cmd_check_commit

                            args = MagicMock()

                            import io
                            from contextlib import redirect_stdout

                            f = io.StringIO()
                            with redirect_stdout(f):
                                result = cmd_check_commit(args)

                            output = f.getvalue()
                            parsed = json.loads(output)

        assert result == 0
        assert parsed["ready"] is True


class TestCLIRecordCommit:
    """Tests for record-commit command."""

    def test_records_commit(self, temp_dir):
        """Should record commit and reset state."""
        state_file = temp_dir / ".ai_workflow" / "workflow-state.json"
        state_file.parent.mkdir(parents=True)

        state = {
            "version": "2.0",
            "component": {"current": "TestComp", "step": "review", "list": []},
            "ci": {"component_passed": True},
            "reviews": {
                "gemini": {"status": "APPROVED", "continuation_id": "real-gemini-id"},
                "codex": {"status": "APPROVED", "continuation_id": "real-codex-id"},
            },
            "git": {"commits": []},
        }
        with open(state_file, "w") as f:
            json.dump(state, f)

        with patch("scripts.admin.workflow_gate.STATE_FILE", state_file):
            with patch("scripts.admin.workflow_gate.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                from scripts.admin.workflow_gate import cmd_record_commit

                args = MagicMock()
                args.hash = "abc123def456"
                args.message = "feat: test commit"

                import io
                from contextlib import redirect_stdout

                f = io.StringIO()
                with redirect_stdout(f):
                    result = cmd_record_commit(args)

        assert result == 0

        with open(state_file) as f:
            saved = json.load(f)
        assert len(saved["git"]["commits"]) == 1
        assert saved["git"]["commits"][0]["hash"] == "abc123def456"
        assert saved["component"]["step"] == "plan"  # Reset


class TestCLIPRPhase:
    """Tests for PR phase commands."""

    def test_start_pr_phase(self, temp_dir):
        """Should start PR review phase."""
        state_file = temp_dir / ".ai_workflow" / "workflow-state.json"
        config_file = temp_dir / ".ai_workflow" / "config.json"
        state_file.parent.mkdir(parents=True)

        state = {
            "version": "2.0",
            "phase": "component",
            "pr_review": {},
            "reviewers": {},
        }
        with open(state_file, "w") as f:
            json.dump(state, f)

        with patch("scripts.admin.workflow_gate.STATE_FILE", state_file):
            with patch("scripts.admin.workflow_gate.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                with patch("ai_workflow.config.CONFIG_FILE", config_file):
                    with patch("ai_workflow.config.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                        from scripts.admin.workflow_gate import cmd_start_pr_phase

                        args = MagicMock()
                        args.pr_url = "https://github.com/owner/repo/pull/123"
                        args.pr_number = None

                        import io
                        from contextlib import redirect_stdout

                        f = io.StringIO()
                        with redirect_stdout(f):
                            result = cmd_start_pr_phase(args)

                        output = f.getvalue()
                        parsed = json.loads(output)

        assert result == 0
        assert parsed["success"] is True
        assert parsed["pr_number"] == 123


class TestCLISubtasks:
    """Tests for subtask commands."""

    def test_subtask_status(self, temp_dir):
        """Should return subtask status."""
        state_file = temp_dir / ".ai_workflow" / "workflow-state.json"
        config_file = temp_dir / ".ai_workflow" / "config.json"
        state_file.parent.mkdir(parents=True)

        state = {
            "version": "2.0",
            "subtasks": {
                "queue": [
                    {"id": "task-1", "status": "queued"},
                    {"id": "task-2", "status": "delegated"},
                ],
                "completed": [{"id": "task-0"}],
                "failed": [],
            },
        }
        with open(state_file, "w") as f:
            json.dump(state, f)

        with patch("scripts.admin.workflow_gate.STATE_FILE", state_file):
            with patch("scripts.admin.workflow_gate.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                with patch("ai_workflow.config.CONFIG_FILE", config_file):
                    with patch("ai_workflow.config.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                        from scripts.admin.workflow_gate import cmd_subtask_status

                        args = MagicMock()

                        import io
                        from contextlib import redirect_stdout

                        f = io.StringIO()
                        with redirect_stdout(f):
                            result = cmd_subtask_status(args)

                        output = f.getvalue()
                        parsed = json.loads(output)

        assert result == 0
        assert parsed["total"] == 2
        assert parsed["queued"] == 1
        assert parsed["delegated"] == 1
        assert parsed["completed"] == 1


class TestCLIConfig:
    """Tests for config commands."""

    def test_config_show(self, temp_dir):
        """Should show configuration."""
        config_file = temp_dir / ".ai_workflow" / "config.json"
        config_file.parent.mkdir(parents=True)

        config = {
            "version": "1.0",
            "reviewers": {
                "enabled": ["claude"],
                "available": ["claude"],
                "min_required": 1,
                "username_mapping": {},
            },
        }
        with open(config_file, "w") as f:
            json.dump(config, f)

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            from scripts.admin.workflow_gate import cmd_config_show

            args = MagicMock()

            import io
            from contextlib import redirect_stdout

            f = io.StringIO()
            with redirect_stdout(f):
                result = cmd_config_show(args)

            output = f.getvalue()
            parsed = json.loads(output)

        assert result == 0
        assert parsed["reviewers"]["enabled"] == ["claude"]

    def test_check_reviewers(self, temp_dir):
        """Should check reviewer availability."""
        config_file = temp_dir / ".ai_workflow" / "config.json"
        config_file.parent.mkdir(parents=True)

        config = {
            "version": "1.0",
            "reviewers": {
                "enabled": ["claude", "gemini"],
                "available": ["claude", "gemini", "codex"],
                "min_required": 2,
                "username_mapping": {},
            },
        }
        with open(config_file, "w") as f:
            json.dump(config, f)

        with patch("ai_workflow.config.CONFIG_FILE", config_file):
            from scripts.admin.workflow_gate import cmd_check_reviewers

            args = MagicMock()

            import io
            from contextlib import redirect_stdout

            f = io.StringIO()
            with redirect_stdout(f):
                result = cmd_check_reviewers(args)

            output = f.getvalue()
            parsed = json.loads(output)

        assert result == 0
        assert "claude" in parsed["enabled"]
        assert "gemini" in parsed["enabled"]
        assert parsed["min_required"] == 2


class TestCLIStateTransaction:
    """Tests for atomic state transaction."""

    def test_transaction_rollback_on_error(self, temp_dir):
        """Should rollback state on error."""
        state_file = temp_dir / ".ai_workflow" / "workflow-state.json"
        state_file.parent.mkdir(parents=True)

        original_state = {
            "version": "2.0",
            "component": {"current": "Original", "step": "plan", "list": []},
        }
        with open(state_file, "w") as f:
            json.dump(original_state, f)

        with patch("scripts.admin.workflow_gate.STATE_FILE", state_file):
            with patch("scripts.admin.workflow_gate.WORKFLOW_DIR", temp_dir / ".ai_workflow"):
                from scripts.admin.workflow_gate import state_transaction

                try:
                    with state_transaction() as state:
                        state["component"]["current"] = "Modified"
                        raise ValueError("Simulated error")
                except ValueError:
                    pass

        # State should be unchanged
        with open(state_file) as f:
            saved = json.load(f)
        assert saved["component"]["current"] == "Original"
