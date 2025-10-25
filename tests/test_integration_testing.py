"""
Tests for Component 3: Integration Testing in CI (P1T10).

This module tests the integration testing configuration:
1. docker-compose.ci.yml syntax and configuration
2. E2E test files existence
3. CI workflow integration test job
4. Safety: DRY_RUN=true enforced in CI environment
"""

import subprocess
from pathlib import Path

import pytest
import yaml

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(scope="module")
def project_root() -> Path:
    """Get the project root directory."""
    return Path(__file__).parent.parent


# =============================================================================
# Test: docker-compose.ci.yml
# =============================================================================


class TestDockerComposeCI:
    """Test docker-compose.ci.yml configuration."""

    def test_docker_compose_ci_exists(self, project_root: Path) -> None:
        """Test that docker-compose.ci.yml file exists."""
        compose_path = project_root / "docker-compose.ci.yml"
        assert compose_path.exists(), "docker-compose.ci.yml not found"
        assert compose_path.is_file(), "docker-compose.ci.yml is not a file"

    def test_docker_compose_ci_valid_yaml(self, project_root: Path) -> None:
        """Test that docker-compose.ci.yml is valid YAML."""
        compose_path = project_root / "docker-compose.ci.yml"
        with open(compose_path) as f:
            try:
                config = yaml.safe_load(f)
            except yaml.YAMLError as e:
                pytest.fail(f"docker-compose.ci.yml is not valid YAML: {e}")

        assert isinstance(config, dict), "docker-compose.ci.yml should be a dictionary"

    def test_docker_compose_ci_has_required_services(self, project_root: Path) -> None:
        """Test that docker-compose.ci.yml defines all required services."""
        compose_path = project_root / "docker-compose.ci.yml"
        with open(compose_path) as f:
            config = yaml.safe_load(f)

        required_services = {
            "postgres",
            "redis",
            "signal_service",
            "execution_gateway",
            "orchestrator",
        }

        assert "services" in config, "docker-compose.ci.yml missing 'services' key"
        actual_services = set(config["services"].keys())

        missing = required_services - actual_services
        assert not missing, f"Missing services: {missing}"

    def test_docker_compose_ci_enforces_dry_run(self, project_root: Path) -> None:
        """Test that docker-compose.ci.yml enforces DRY_RUN=true (safety)."""
        compose_path = project_root / "docker-compose.ci.yml"
        with open(compose_path) as f:
            config = yaml.safe_load(f)

        services = config["services"]

        # Check execution_gateway has DRY_RUN=true
        execution_gateway_env = services["execution_gateway"].get("environment", {})
        assert (
            "DRY_RUN" in execution_gateway_env
        ), "execution_gateway missing DRY_RUN environment variable"
        assert (
            execution_gateway_env["DRY_RUN"] == "true"
        ), f"execution_gateway DRY_RUN={execution_gateway_env['DRY_RUN']}, expected 'true'"

        # Check orchestrator has DRY_RUN=true
        orchestrator_env = services["orchestrator"].get("environment", {})
        assert "DRY_RUN" in orchestrator_env, "orchestrator missing DRY_RUN environment variable"
        assert (
            orchestrator_env["DRY_RUN"] == "true"
        ), f"orchestrator DRY_RUN={orchestrator_env['DRY_RUN']}, expected 'true'"

    def test_docker_compose_ci_has_health_checks(self, project_root: Path) -> None:
        """Test that all application services have health checks."""
        compose_path = project_root / "docker-compose.ci.yml"
        with open(compose_path) as f:
            config = yaml.safe_load(f)

        app_services = ["signal_service", "execution_gateway", "orchestrator"]

        for service_name in app_services:
            service = config["services"][service_name]
            assert "healthcheck" in service, f"{service_name} missing healthcheck configuration"

            healthcheck = service["healthcheck"]
            assert "test" in healthcheck, f"{service_name} healthcheck missing 'test'"
            assert "interval" in healthcheck, f"{service_name} healthcheck missing 'interval'"
            assert "retries" in healthcheck, f"{service_name} healthcheck missing 'retries'"

    def test_docker_compose_ci_uses_correct_ports(self, project_root: Path) -> None:
        """Test that services use non-conflicting ports."""
        compose_path = project_root / "docker-compose.ci.yml"
        with open(compose_path) as f:
            config = yaml.safe_load(f)

        services = config["services"]

        # Postgres should use 5433 (not 5432 to avoid conflicts with GitHub Actions)
        postgres_ports = services["postgres"]["ports"]
        assert "5433:5432" in postgres_ports, f"postgres should use port 5433, got {postgres_ports}"

        # Redis should use 6380 (not 6379 to avoid conflicts)
        redis_ports = services["redis"]["ports"]
        assert "6380:6379" in redis_ports, f"redis should use port 6380, got {redis_ports}"


# =============================================================================
# Test: E2E Test Files
# =============================================================================


class TestE2ETestFiles:
    """Test E2E test file existence and structure."""

    def test_e2e_directory_exists(self, project_root: Path) -> None:
        """Test that tests/e2e/ directory exists."""
        e2e_dir = project_root / "tests" / "e2e"
        assert e2e_dir.exists(), "tests/e2e/ directory not found"
        assert e2e_dir.is_dir(), "tests/e2e/ is not a directory"

    def test_e2e_init_file_exists(self, project_root: Path) -> None:
        """Test that tests/e2e/__init__.py exists."""
        init_file = project_root / "tests" / "e2e" / "__init__.py"
        assert init_file.exists(), "tests/e2e/__init__.py not found"

    def test_signal_to_execution_tests_exist(self, project_root: Path) -> None:
        """Test that test_signal_to_execution.py exists."""
        test_file = project_root / "tests" / "e2e" / "test_signal_to_execution.py"
        assert test_file.exists(), "test_signal_to_execution.py not found"
        assert test_file.is_file(), "test_signal_to_execution.py is not a file"

    def test_orchestrator_flow_tests_exist(self, project_root: Path) -> None:
        """Test that test_orchestrator_flow.py exists."""
        test_file = project_root / "tests" / "e2e" / "test_orchestrator_flow.py"
        assert test_file.exists(), "test_orchestrator_flow.py not found"
        assert test_file.is_file(), "test_orchestrator_flow.py is not a file"

    def test_e2e_tests_use_correct_marker(self, project_root: Path) -> None:
        """Test that E2E tests use @pytest.mark.e2e marker."""
        e2e_dir = project_root / "tests" / "e2e"

        for test_file in e2e_dir.glob("test_*.py"):
            content = test_file.read_text()

            # Check that file uses @pytest.mark.e2e
            assert (
                "@pytest.mark.e2e" in content
            ), f"{test_file.name} should use @pytest.mark.e2e marker"


# =============================================================================
# Test: CI Workflow Integration
# =============================================================================


class TestCIWorkflowIntegration:
    """Test CI workflow integration test job configuration."""

    def test_ci_workflow_has_integration_tests_job(self, project_root: Path) -> None:
        """Test that CI workflow includes integration-tests job."""
        workflow_path = project_root / ".github" / "workflows" / "ci-tests-coverage.yml"
        assert workflow_path.exists(), "ci-tests-coverage.yml not found"

        with open(workflow_path) as f:
            content = f.read()

        # Check for integration-tests job
        assert "integration-tests:" in content, "CI workflow missing 'integration-tests' job"

        # Check that it depends on test-and-coverage
        assert (
            "needs: test-and-coverage" in content
        ), "integration-tests job should depend on test-and-coverage"

    def test_ci_workflow_uses_docker_compose_ci(self, project_root: Path) -> None:
        """Test that CI workflow uses docker-compose.ci.yml."""
        workflow_path = project_root / ".github" / "workflows" / "ci-tests-coverage.yml"

        with open(workflow_path) as f:
            content = f.read()

        # Check that workflow uses docker-compose.ci.yml
        assert "docker-compose.ci.yml" in content, "CI workflow should use docker-compose.ci.yml"

    def test_ci_workflow_captures_logs_on_failure(self, project_root: Path) -> None:
        """Test that CI workflow captures service logs on failure."""
        workflow_path = project_root / ".github" / "workflows" / "ci-tests-coverage.yml"

        with open(workflow_path) as f:
            content = f.read()

        # Check that workflow captures logs on failure
        assert (
            "Capture service logs on failure" in content
        ), "CI workflow should capture service logs on failure"

        # Check that it captures logs for all services
        assert "docker compose -f docker-compose.ci.yml logs signal_service" in content
        assert "docker compose -f docker-compose.ci.yml logs execution_gateway" in content
        assert "docker compose -f docker-compose.ci.yml logs orchestrator" in content


# =============================================================================
# Integration Tests (require docker-compose)
# =============================================================================


@pytest.mark.slow()
@pytest.mark.integration()
class TestDockerComposeValidation:
    """Integration tests for docker-compose.ci.yml (requires Docker)."""

    @pytest.fixture(autouse=True)
    def _check_docker_available(self) -> None:
        """Skip tests if Docker is not available."""
        try:
            subprocess.run(
                ["docker", "info"],
                check=True,
                capture_output=True,
                timeout=5,
            )
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
            pytest.skip("Docker is not available")

    def test_docker_compose_ci_syntax_valid(self, project_root: Path) -> None:
        """Test that docker-compose.ci.yml syntax is valid."""
        result = subprocess.run(
            ["docker", "compose", "-f", "docker-compose.ci.yml", "config"],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=30,
        )

        assert result.returncode == 0, f"docker-compose.ci.yml syntax invalid:\n{result.stderr}"


# =============================================================================
# Summary
# =============================================================================

"""
Component 3 Test Coverage:

1. docker-compose.ci.yml (7 tests)
   - File exists
   - Valid YAML syntax
   - Required services defined
   - DRY_RUN=true enforced (safety)
   - Health checks configured
   - Non-conflicting ports

2. E2E Test Files (5 tests)
   - tests/e2e/ directory exists
   - __init__.py exists
   - test_signal_to_execution.py exists
   - test_orchestrator_flow.py exists
   - Tests use @pytest.mark.e2e marker

3. CI Workflow Integration (4 tests)
   - integration-tests job exists
   - Uses docker-compose.ci.yml
   - Captures logs on failure
   - Depends on test-and-coverage job

4. Integration Tests (1 test, marked slow/integration)
   - docker-compose.ci.yml syntax validation

Total: 17 test cases

Run all tests:
    pytest tests/test_integration_testing.py -v

Run fast tests only (skip integration):
    pytest tests/test_integration_testing.py -v -m "not slow and not integration"

Run integration tests only:
    pytest tests/test_integration_testing.py -v -m "slow and integration"
"""
