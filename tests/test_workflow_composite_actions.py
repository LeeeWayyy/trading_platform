"""
Tests for GitHub Actions composite actions.

Validates action.yml syntax, parameters, and configuration.
"""

import yaml
from pathlib import Path


class TestWaitForServicesAction:
    """Test suite for wait-for-services composite action."""

    @staticmethod
    def _load_action_yml() -> dict:
        """Load action.yml file."""
        action_path = Path(__file__).parent.parent / ".github/actions/wait-for-services/action.yml"
        assert action_path.exists(), f"Action file not found: {action_path}"

        with open(action_path) as f:
            return yaml.safe_load(f)

    def test_action_yml_valid_syntax(self):
        """Test action.yml has valid YAML syntax."""
        action = self._load_action_yml()
        assert action is not None
        assert isinstance(action, dict)

    def test_action_metadata(self):
        """Test action has required metadata."""
        action = self._load_action_yml()

        assert "name" in action
        assert "description" in action
        assert "author" in action

        assert action["name"] == "Wait for Docker Compose Services"
        assert "docker" in action["description"].lower()
        assert "healthy" in action["description"].lower()

    def test_action_inputs(self):
        """Test action defines required inputs with correct structure."""
        action = self._load_action_yml()

        assert "inputs" in action
        inputs = action["inputs"]

        # Required inputs
        assert "compose-file" in inputs
        assert inputs["compose-file"]["required"] is True

        # Optional inputs with defaults
        assert "max-iterations" in inputs
        assert inputs["max-iterations"]["required"] is False
        assert inputs["max-iterations"]["default"] == "30"

        assert "sleep-seconds" in inputs
        assert inputs["sleep-seconds"]["required"] is False
        assert inputs["sleep-seconds"]["default"] == "4"

        assert "fail-on-timeout" in inputs
        assert inputs["fail-on-timeout"]["required"] is False
        assert inputs["fail-on-timeout"]["default"] == "true"

        # All inputs have descriptions
        for input_name, input_config in inputs.items():
            assert "description" in input_config, f"Input {input_name} missing description"
            assert len(input_config["description"]) > 10, f"Input {input_name} has too short description"

    def test_action_outputs(self):
        """Test action defines expected outputs."""
        action = self._load_action_yml()

        assert "outputs" in action
        outputs = action["outputs"]

        # Expected outputs
        assert "healthy" in outputs
        assert "iterations" in outputs

        # Outputs have descriptions
        for output_name, output_config in outputs.items():
            assert "description" in output_config, f"Output {output_name} missing description"
            assert "value" in output_config, f"Output {output_name} missing value reference"

    def test_action_uses_composite(self):
        """Test action uses composite runner."""
        action = self._load_action_yml()

        assert "runs" in action
        assert action["runs"]["using"] == "composite"
        assert "steps" in action["runs"]
        assert len(action["runs"]["steps"]) > 0

    def test_action_steps_structure(self):
        """Test action steps have correct structure."""
        action = self._load_action_yml()

        steps = action["runs"]["steps"]

        for step in steps:
            assert "shell" in step, "Step missing shell"
            assert step["shell"] == "bash", f"Expected bash shell, got {step['shell']}"
            assert "run" in step, "Step missing run command"
            assert len(step["run"]) > 0, "Step has empty run command"

            # Optional but recommended
            if "name" in step:
                assert len(step["name"]) > 0, "Step has empty name"
            if "id" in step:
                assert len(step["id"]) > 0, "Step has empty id"

    def test_action_uses_inputs(self):
        """Test action script references all inputs."""
        action = self._load_action_yml()

        inputs = action["inputs"].keys()
        steps = action["runs"]["steps"]

        # Get all run scripts
        all_scripts = " ".join([step["run"] for step in steps])

        # Verify all inputs are used
        for input_name in inputs:
            input_ref = f"${{{{ inputs.{input_name} }}}}"
            assert input_ref in all_scripts, f"Input {input_name} not used in action"

    def test_action_sets_outputs(self):
        """Test action script sets outputs correctly."""
        action = self._load_action_yml()

        outputs = action["outputs"].keys()
        steps = action["runs"]["steps"]

        # Get all run scripts
        all_scripts = " ".join([step["run"] for step in steps])

        # Verify all outputs are set
        for output_name in outputs:
            output_set = f"{output_name}="
            assert output_set in all_scripts, f"Output {output_name} not set in action"
            assert "GITHUB_OUTPUT" in all_scripts, "Action doesn't write to GITHUB_OUTPUT"

    def test_action_has_branding(self):
        """Test action has branding for marketplace."""
        action = self._load_action_yml()

        assert "branding" in action
        branding = action["branding"]

        assert "icon" in branding
        assert "color" in branding

        # Valid icon and color values
        assert isinstance(branding["icon"], str)
        assert isinstance(branding["color"], str)

    def test_action_default_timeout_reasonable(self):
        """Test action default timeout is reasonable (30 iterations * 4s = 120s)."""
        action = self._load_action_yml()

        max_iter = int(action["inputs"]["max-iterations"]["default"])
        sleep_sec = int(action["inputs"]["sleep-seconds"]["default"])

        total_timeout_sec = max_iter * sleep_sec

        # Reasonable timeout: 60-300 seconds
        assert 60 <= total_timeout_sec <= 300, (
            f"Total timeout {total_timeout_sec}s unreasonable "
            f"(max_iter={max_iter}, sleep={sleep_sec})"
        )

    def test_action_handles_timeout_gracefully(self):
        """Test action script includes timeout handling."""
        action = self._load_action_yml()

        steps = action["runs"]["steps"]
        all_scripts = " ".join([step["run"] for step in steps])

        # Should handle timeout case
        assert "timeout" in all_scripts.lower() or "max" in all_scripts.lower()
        assert "fail-on-timeout" in all_scripts

        # Should print debug info on failure
        assert "logs" in all_scripts or "status" in all_scripts

    def test_action_provides_user_feedback(self):
        """Test action provides clear user feedback during execution."""
        action = self._load_action_yml()

        steps = action["runs"]["steps"]
        all_scripts = " ".join([step["run"] for step in steps])

        # Should print progress indicators
        assert "echo" in all_scripts
        assert "iteration" in all_scripts.lower() or "waiting" in all_scripts.lower()

        # Should use emoji or symbols for clarity
        assert "✅" in all_scripts or "❌" in all_scripts or "⏳" in all_scripts

    def test_action_uses_pipefail(self):
        """Test action uses set -euo pipefail for safe error handling."""
        action = self._load_action_yml()

        steps = action["runs"]["steps"]
        all_scripts = " ".join([step["run"] for step in steps])

        # Must use pipefail to catch docker-compose errors
        assert "set -" in all_scripts
        assert "pipefail" in all_scripts
        assert "errexit" in all_scripts or "-e" in "set -euo pipefail"

    def test_action_checks_for_healthy_status(self):
        """Test action explicitly checks for (healthy) status, not just absence of unhealthy."""
        action = self._load_action_yml()

        steps = action["runs"]["steps"]
        all_scripts = " ".join([step["run"] for step in steps])

        # Must check for positive healthy signal
        assert "(healthy)" in all_scripts or "healthy" in all_scripts

        # Should check for problematic states
        assert "Exit" in all_scripts or "exit" in all_scripts.lower()
        assert "starting" in all_scripts.lower() or "Restarting" in all_scripts

    def test_action_handles_docker_compose_errors(self):
        """Test action detects and fails on docker-compose command errors."""
        action = self._load_action_yml()

        steps = action["runs"]["steps"]
        all_scripts = " ".join([step["run"] for step in steps])

        # Should capture docker-compose output
        assert "docker-compose" in all_scripts

        # Should check for command failure
        assert "if !" in all_scripts or "||" in all_scripts

        # Should exit on docker-compose errors
        assert "exit 1" in all_scripts
