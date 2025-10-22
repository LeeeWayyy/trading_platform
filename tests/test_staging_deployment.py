"""
Tests for Component 4: Staging Deployment Automation (P1T10).

This module tests the staging deployment configuration:
1. deploy-staging.yml workflow syntax and safety checks
2. docker-compose.staging.yml configuration
3. Credential validation logic
4. DRY_RUN enforcement
5. Smoke test procedures
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
# Test: deploy-staging.yml Workflow
# =============================================================================


class TestDeployStagingWorkflow:
    """Test deploy-staging.yml GitHub Actions workflow."""

    def test_deploy_staging_workflow_exists(self, project_root: Path) -> None:
        """Test that deploy-staging.yml workflow file exists."""
        workflow_path = project_root / ".github" / "workflows" / "deploy-staging.yml"
        assert workflow_path.exists(), "deploy-staging.yml workflow not found"
        assert workflow_path.is_file(), "deploy-staging.yml is not a file"

    def test_workflow_valid_yaml(self, project_root: Path) -> None:
        """Test that deploy-staging.yml is valid YAML."""
        workflow_path = project_root / ".github" / "workflows" / "deploy-staging.yml"
        with open(workflow_path) as f:
            try:
                config = yaml.safe_load(f)
            except yaml.YAMLError as e:
                pytest.fail(f"deploy-staging.yml is not valid YAML: {e}")

        assert isinstance(config, dict), "deploy-staging.yml should be a dictionary"

    def test_workflow_uses_staging_environment(self, project_root: Path) -> None:
        """Test that workflow uses 'staging' GitHub Environment."""
        workflow_path = project_root / ".github" / "workflows" / "deploy-staging.yml"
        with open(workflow_path) as f:
            content = f.read()

        # Check for environment: staging
        assert (
            "environment: staging" in content
        ), "Workflow must use 'environment: staging' for credential isolation"

    def test_workflow_has_credential_validation_job(self, project_root: Path) -> None:
        """Test that workflow has credential validation job (safety check)."""
        workflow_path = project_root / ".github" / "workflows" / "deploy-staging.yml"
        with open(workflow_path) as f:
            config = yaml.safe_load(f)

        assert "jobs" in config, "Workflow missing 'jobs' key"
        jobs = config["jobs"]

        # Check for validate-credentials job
        assert (
            "validate-credentials" in jobs
        ), "Workflow missing 'validate-credentials' job (safety check)"

    def test_workflow_validates_paper_api_keys(self, project_root: Path) -> None:
        """Test that workflow validates paper API keys exist."""
        workflow_path = project_root / ".github" / "workflows" / "deploy-staging.yml"
        with open(workflow_path) as f:
            content = f.read()

        # Check that workflow validates paper API credentials
        assert "ALPACA_PAPER_API_KEY" in content, "Workflow should validate ALPACA_PAPER_API_KEY"
        assert (
            "ALPACA_PAPER_API_SECRET" in content
        ), "Workflow should validate ALPACA_PAPER_API_SECRET"

    def test_workflow_blocks_live_api_keys(self, project_root: Path) -> None:
        """Test that workflow blocks live API keys (critical safety check)."""
        workflow_path = project_root / ".github" / "workflows" / "deploy-staging.yml"
        with open(workflow_path) as f:
            content = f.read()

        # Check for live API key blocking logic
        assert "ALPACA_LIVE_API_KEY" in content, "Workflow should check for ALPACA_LIVE_API_KEY"

        # Check that workflow exits with error if live keys found
        assert "exit 1" in content, "Workflow should exit with error if live API keys detected"

    def test_workflow_deployment_depends_on_validation(self, project_root: Path) -> None:
        """Test that deployment job depends on credential validation."""
        workflow_path = project_root / ".github" / "workflows" / "deploy-staging.yml"
        with open(workflow_path) as f:
            config = yaml.safe_load(f)

        jobs = config["jobs"]

        # Check for deploy-staging job
        assert "deploy-staging" in jobs, "Workflow missing 'deploy-staging' job"

        deploy_job = jobs["deploy-staging"]

        # Check that deploy-staging depends on validate-credentials
        assert "needs" in deploy_job, "deploy-staging job must have 'needs' dependency"

        needs = deploy_job["needs"]
        if isinstance(needs, str):
            needs = [needs]

        assert (
            "validate-credentials" in needs
        ), "deploy-staging must depend on validate-credentials (safety check)"

    def test_workflow_runs_smoke_tests(self, project_root: Path) -> None:
        """Test that workflow runs smoke tests after deployment."""
        workflow_path = project_root / ".github" / "workflows" / "deploy-staging.yml"
        with open(workflow_path) as f:
            content = f.read()

        # Check for smoke test step
        assert "smoke tests" in content.lower(), "Workflow should include smoke tests"

        # Check that smoke tests verify health endpoints
        assert "/health" in content, "Smoke tests should verify /health endpoints"


# =============================================================================
# Test: docker-compose.staging.yml
# =============================================================================


class TestDockerComposeStagingConfig:
    """Test docker-compose.staging.yml configuration."""

    def test_docker_compose_staging_exists(self, project_root: Path) -> None:
        """Test that docker-compose.staging.yml file exists."""
        compose_path = project_root / "docker-compose.staging.yml"
        assert compose_path.exists(), "docker-compose.staging.yml not found"
        assert compose_path.is_file(), "docker-compose.staging.yml is not a file"

    def test_docker_compose_staging_valid_yaml(self, project_root: Path) -> None:
        """Test that docker-compose.staging.yml is valid YAML."""
        compose_path = project_root / "docker-compose.staging.yml"
        with open(compose_path) as f:
            try:
                config = yaml.safe_load(f)
            except yaml.YAMLError as e:
                pytest.fail(f"docker-compose.staging.yml is not valid YAML: {e}")

        assert isinstance(config, dict), "docker-compose.staging.yml should be a dictionary"

    def test_docker_compose_staging_has_app_services(self, project_root: Path) -> None:
        """Test that docker-compose.staging.yml defines all app services."""
        compose_path = project_root / "docker-compose.staging.yml"
        with open(compose_path) as f:
            config = yaml.safe_load(f)

        required_services = {
            "signal_service",
            "execution_gateway",
            "orchestrator",
        }

        assert "services" in config, "docker-compose.staging.yml missing 'services' key"
        actual_services = set(config["services"].keys())

        missing = required_services - actual_services
        assert not missing, f"Missing app services: {missing}"

    def test_docker_compose_staging_enforces_dry_run(self, project_root: Path) -> None:
        """Test that docker-compose.staging.yml enforces DRY_RUN=true (safety)."""
        compose_path = project_root / "docker-compose.staging.yml"
        with open(compose_path) as f:
            config = yaml.safe_load(f)

        services = config["services"]

        # Check execution_gateway has DRY_RUN=true
        execution_gateway_env = services["execution_gateway"].get("environment", {})
        assert (
            "DRY_RUN" in execution_gateway_env
        ), "execution_gateway missing DRY_RUN environment variable"
        # Check it's a string "true" (not boolean)
        assert (
            execution_gateway_env["DRY_RUN"] == "true"
        ), f"execution_gateway DRY_RUN={execution_gateway_env['DRY_RUN']}, expected 'true'"

        # Check orchestrator has DRY_RUN=true
        orchestrator_env = services["orchestrator"].get("environment", {})
        assert "DRY_RUN" in orchestrator_env, "orchestrator missing DRY_RUN environment variable"
        assert (
            orchestrator_env["DRY_RUN"] == "true"
        ), f"orchestrator DRY_RUN={orchestrator_env['DRY_RUN']}, expected 'true'"

    def test_docker_compose_staging_enforces_alpaca_paper(self, project_root: Path) -> None:
        """Test that docker-compose.staging.yml enforces ALPACA_PAPER=true."""
        compose_path = project_root / "docker-compose.staging.yml"
        with open(compose_path) as f:
            config = yaml.safe_load(f)

        services = config["services"]

        # Check execution_gateway has ALPACA_PAPER=true
        execution_gateway_env = services["execution_gateway"].get("environment", {})
        assert (
            "ALPACA_PAPER" in execution_gateway_env
        ), "execution_gateway missing ALPACA_PAPER environment variable"
        assert execution_gateway_env["ALPACA_PAPER"] == "true", (
            f"execution_gateway ALPACA_PAPER={execution_gateway_env['ALPACA_PAPER']}, "
            f"expected 'true'"
        )

    def test_docker_compose_staging_uses_environment_variables(self, project_root: Path) -> None:
        """Test that sensitive values use environment variables (not hardcoded)."""
        compose_path = project_root / "docker-compose.staging.yml"
        with open(compose_path) as f:
            config = yaml.safe_load(f)

        services = config["services"]
        execution_gateway_env = services["execution_gateway"].get("environment", {})

        # Check that API keys use ${} syntax (not hardcoded values)
        alpaca_key = execution_gateway_env.get("ALPACA_API_KEY", "")
        alpaca_secret = execution_gateway_env.get("ALPACA_API_SECRET", "")

        assert (
            alpaca_key.startswith("${") or alpaca_key == ""
        ), "ALPACA_API_KEY should use ${ALPACA_API_KEY} syntax (not hardcoded)"
        assert (
            alpaca_secret.startswith("${") or alpaca_secret == ""
        ), "ALPACA_API_SECRET should use ${ALPACA_API_SECRET} syntax (not hardcoded)"

    def test_docker_compose_staging_has_health_checks(self, project_root: Path) -> None:
        """Test that all app services have health checks."""
        compose_path = project_root / "docker-compose.staging.yml"
        with open(compose_path) as f:
            config = yaml.safe_load(f)

        app_services = ["signal_service", "execution_gateway", "orchestrator"]

        for service_name in app_services:
            service = config["services"][service_name]
            assert "healthcheck" in service, f"{service_name} missing healthcheck configuration"

    def test_docker_compose_staging_uses_restart_policy(self, project_root: Path) -> None:
        """Test that app services use restart policy for resilience."""
        compose_path = project_root / "docker-compose.staging.yml"
        with open(compose_path) as f:
            config = yaml.safe_load(f)

        app_services = ["signal_service", "execution_gateway", "orchestrator"]

        for service_name in app_services:
            service = config["services"][service_name]
            assert "restart" in service, f"{service_name} missing restart policy"
            # Should be "unless-stopped" or "always"
            assert service["restart"] in [
                "unless-stopped",
                "always",
            ], f"{service_name} restart policy should be 'unless-stopped' or 'always'"

    def test_docker_compose_staging_pulls_from_ghcr(self, project_root: Path) -> None:
        """Test that app services pull images from GitHub Container Registry."""
        compose_path = project_root / "docker-compose.staging.yml"
        with open(compose_path) as f:
            config = yaml.safe_load(f)

        app_services = ["signal_service", "execution_gateway", "orchestrator"]

        for service_name in app_services:
            service = config["services"][service_name]
            assert "image" in service, f"{service_name} missing image configuration"

            image = service["image"]
            # Should reference ghcr.io or use ${REGISTRY} variable
            assert (
                "ghcr.io" in image or "${REGISTRY" in image
            ), f"{service_name} should pull from GitHub Container Registry"


# =============================================================================
# Test: Staging Deployment Documentation
# =============================================================================


class TestStagingDeploymentDocumentation:
    """Test staging deployment documentation."""

    def test_staging_deployment_runbook_exists(self, project_root: Path) -> None:
        """Test that staging deployment runbook exists."""
        runbook_path = project_root / "docs" / "RUNBOOKS" / "staging-deployment.md"
        assert runbook_path.exists(), "staging-deployment.md runbook not found"
        assert runbook_path.is_file(), "staging-deployment.md is not a file"

    def test_runbook_covers_credential_management(self, project_root: Path) -> None:
        """Test that runbook covers credential management."""
        runbook_path = project_root / "docs" / "RUNBOOKS" / "staging-deployment.md"
        content = runbook_path.read_text()

        # Check for key credential management topics
        assert "Credential Management" in content, "Runbook should cover credential management"
        assert "ALPACA_PAPER_API_KEY" in content, "Runbook should document paper API key setup"
        assert (
            "rotation" in content.lower()
        ), "Runbook should document credential rotation procedures"

    def test_runbook_covers_safety_procedures(self, project_root: Path) -> None:
        """Test that runbook covers safety procedures."""
        runbook_path = project_root / "docs" / "RUNBOOKS" / "staging-deployment.md"
        content = runbook_path.read_text()

        # Check for safety procedures
        assert "DRY_RUN" in content, "Runbook should document DRY_RUN enforcement"
        assert "paper trading" in content.lower(), "Runbook should document paper trading mode"
        assert (
            "live" in content.lower() or "production" in content.lower()
        ), "Runbook should warn about live/production credentials"

    def test_runbook_covers_rollback_procedures(self, project_root: Path) -> None:
        """Test that runbook covers rollback procedures."""
        runbook_path = project_root / "docs" / "RUNBOOKS" / "staging-deployment.md"
        content = runbook_path.read_text()

        # Check for rollback procedures
        assert "rollback" in content.lower(), "Runbook should cover rollback procedures"
        assert "emergency" in content.lower(), "Runbook should document emergency procedures"


# =============================================================================
# Integration Tests (require docker-compose)
# =============================================================================


@pytest.mark.slow
@pytest.mark.integration
class TestDockerComposeStagingValidation:
    """Integration tests for docker-compose.staging.yml (requires Docker)."""

    @pytest.fixture(autouse=True)
    def check_docker_available(self) -> None:
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

    def test_docker_compose_staging_syntax_valid(self, project_root: Path) -> None:
        """Test that docker-compose.staging.yml syntax is valid."""
        result = subprocess.run(
            ["docker-compose", "-f", "docker-compose.staging.yml", "config"],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=30,
        )

        assert (
            result.returncode == 0
        ), f"docker-compose.staging.yml syntax invalid:\n{result.stderr}"


# =============================================================================
# Summary
# =============================================================================

"""
Component 4 Test Coverage:

1. deploy-staging.yml Workflow (9 tests)
   - File exists
   - Valid YAML syntax
   - Uses staging environment
   - Has credential validation job
   - Validates paper API keys
   - Blocks live API keys (safety)
   - Deployment depends on validation
   - Runs smoke tests

2. docker-compose.staging.yml (9 tests)
   - File exists
   - Valid YAML syntax
   - App services defined
   - DRY_RUN=true enforced
   - ALPACA_PAPER=true enforced
   - Uses environment variables (not hardcoded)
   - Health checks configured
   - Restart policy set
   - Pulls from ghcr.io

3. Documentation (3 tests)
   - Runbook exists
   - Covers credential management
   - Covers safety procedures
   - Covers rollback procedures

4. Integration Tests (1 test, marked slow/integration)
   - docker-compose.staging.yml syntax validation

Total: 22 test cases

Run all tests:
    pytest tests/test_staging_deployment.py -v

Run fast tests only (skip integration):
    pytest tests/test_staging_deployment.py -v -m "not slow and not integration"

Run integration tests only:
    pytest tests/test_staging_deployment.py -v -m "slow and integration"
"""
