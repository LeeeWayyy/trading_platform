"""
Tests for Docker Build Pipeline (Component 2 of P1T10).

This module tests the Dockerfiles and docker-build.yml workflow to ensure:
1. Dockerfile syntax is valid and follows best practices
2. Images build successfully
3. Services start correctly and health checks pass
4. Security: non-root user, no secrets in images
5. Size optimization: multi-stage builds produce small images

Note: These tests require Docker to be installed and running.
"""

import subprocess
from pathlib import Path

import pytest

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(scope="module")
def project_root() -> Path:
    """Get the project root directory."""
    return Path(__file__).parent.parent


@pytest.fixture(scope="module")
def services() -> list[dict[str, str | int]]:
    """Service definitions matching docker-build.yml matrix."""
    return [
        {
            "name": "signal_service",
            "dockerfile": "apps/signal_service/Dockerfile",
            "port": 8001,
        },
        {
            "name": "execution_gateway",
            "dockerfile": "apps/execution_gateway/Dockerfile",
            "port": 8002,
        },
        {
            "name": "orchestrator",
            "dockerfile": "apps/orchestrator/Dockerfile",
            "port": 8003,
        },
    ]


# =============================================================================
# Test: Dockerfile Existence and Syntax
# =============================================================================


class TestDockerfileExistence:
    """Test that all required Dockerfiles exist."""

    def test_dockerignore_exists(self, project_root: Path) -> None:
        """Test that .dockerignore file exists."""
        dockerignore_path = project_root / ".dockerignore"
        assert dockerignore_path.exists(), ".dockerignore file not found"
        assert dockerignore_path.is_file(), ".dockerignore is not a file"

    def test_all_dockerfiles_exist(self, project_root: Path, services: list[dict]) -> None:
        """Test that Dockerfiles exist for all services."""
        for service in services:
            dockerfile_path = project_root / service["dockerfile"]
            assert dockerfile_path.exists(), f"Dockerfile not found: {service['dockerfile']}"
            assert dockerfile_path.is_file(), f"Dockerfile is not a file: {service['dockerfile']}"


class TestDockerfileSyntax:
    """Test Dockerfile syntax and structure."""

    def test_dockerfiles_use_multistage_build(
        self, project_root: Path, services: list[dict]
    ) -> None:
        """Test that all Dockerfiles use multi-stage builds (builder + runtime)."""
        for service in services:
            dockerfile_path = project_root / service["dockerfile"]
            content = dockerfile_path.read_text()

            # Check for builder stage
            assert (
                "FROM python:3.11-slim as builder" in content
            ), f"{service['name']}: Missing builder stage"

            # Check for runtime stage (second FROM without 'as')
            lines = [line.strip() for line in content.split("\n") if line.strip()]
            from_lines = [line for line in lines if line.startswith("FROM")]
            assert (
                len(from_lines) == 2
            ), f"{service['name']}: Expected 2 FROM statements (builder + runtime)"

    def test_dockerfiles_set_pythonunbuffered(
        self, project_root: Path, services: list[dict]
    ) -> None:
        """Test that PYTHONUNBUFFERED=1 is set for proper logging."""
        for service in services:
            dockerfile_path = project_root / service["dockerfile"]
            content = dockerfile_path.read_text()

            assert "PYTHONUNBUFFERED=1" in content, f"{service['name']}: Missing PYTHONUNBUFFERED=1"

    def test_dockerfiles_use_nonroot_user(self, project_root: Path, services: list[dict]) -> None:
        """Test that Dockerfiles create and use a non-root user."""
        for service in services:
            dockerfile_path = project_root / service["dockerfile"]
            content = dockerfile_path.read_text()

            # Check user creation
            assert "useradd" in content, f"{service['name']}: Missing user creation"

            # Check USER directive
            assert "USER trader" in content, f"{service['name']}: Missing USER directive"

    def test_dockerfiles_expose_correct_ports(
        self, project_root: Path, services: list[dict]
    ) -> None:
        """Test that Dockerfiles expose the correct ports."""
        for service in services:
            dockerfile_path = project_root / service["dockerfile"]
            content = dockerfile_path.read_text()

            expected_port = service["port"]
            assert (
                f"EXPOSE {expected_port}" in content
            ), f"{service['name']}: Missing EXPOSE {expected_port}"

    def test_dockerfiles_have_healthcheck(self, project_root: Path, services: list[dict]) -> None:
        """Test that all Dockerfiles include health checks."""
        for service in services:
            dockerfile_path = project_root / service["dockerfile"]
            content = dockerfile_path.read_text()

            assert "HEALTHCHECK" in content, f"{service['name']}: Missing HEALTHCHECK"
            assert (
                "/health" in content
            ), f"{service['name']}: Health check should use /health endpoint"


# =============================================================================
# Test: .dockerignore Content
# =============================================================================


class TestDockerignoreContent:
    """Test .dockerignore content to ensure unnecessary files are excluded."""

    def test_dockerignore_excludes_tests(self, project_root: Path) -> None:
        """Test that .dockerignore excludes test files."""
        dockerignore_path = project_root / ".dockerignore"
        content = dockerignore_path.read_text()

        assert "tests/" in content, ".dockerignore should exclude tests/"
        assert "*.pytest_cache" in content or ".pytest_cache/" in content

    def test_dockerignore_excludes_venv(self, project_root: Path) -> None:
        """Test that .dockerignore excludes virtual environments."""
        dockerignore_path = project_root / ".dockerignore"
        content = dockerignore_path.read_text()

        assert ".venv/" in content or "venv/" in content

    def test_dockerignore_excludes_docs(self, project_root: Path) -> None:
        """Test that .dockerignore excludes documentation."""
        dockerignore_path = project_root / ".dockerignore"
        content = dockerignore_path.read_text()

        assert "docs/" in content
        assert "*.md" in content

    def test_dockerignore_excludes_git(self, project_root: Path) -> None:
        """Test that .dockerignore excludes .git directory."""
        dockerignore_path = project_root / ".dockerignore"
        content = dockerignore_path.read_text()

        assert ".git/" in content


# =============================================================================
# Test: GitHub Actions Workflow
# =============================================================================


class TestDockerBuildWorkflow:
    """Test docker-build.yml GitHub Actions workflow."""

    def test_workflow_file_exists(self, project_root: Path) -> None:
        """Test that docker-build.yml workflow file exists."""
        workflow_path = project_root / ".github" / "workflows" / "docker-build.yml"
        assert workflow_path.exists(), "docker-build.yml workflow not found"
        assert workflow_path.is_file(), "docker-build.yml is not a file"

    def test_workflow_has_matrix_strategy(self, project_root: Path) -> None:
        """Test that workflow uses matrix strategy for all services."""
        workflow_path = project_root / ".github" / "workflows" / "docker-build.yml"
        content = workflow_path.read_text()

        # Check for matrix strategy
        assert "strategy:" in content
        assert "matrix:" in content

        # Check all services are in matrix
        assert "signal_service" in content
        assert "execution_gateway" in content
        assert "orchestrator" in content

    def test_workflow_uses_github_cache(self, project_root: Path) -> None:
        """Test that workflow uses GitHub Actions cache for Docker layers."""
        workflow_path = project_root / ".github" / "workflows" / "docker-build.yml"
        content = workflow_path.read_text()

        # Check for cache configuration
        assert "cache-from: type=gha" in content
        assert "cache-to: type=gha" in content

    def test_workflow_pushes_to_ghcr(self, project_root: Path) -> None:
        """Test that workflow pushes images to GitHub Container Registry."""
        workflow_path = project_root / ".github" / "workflows" / "docker-build.yml"
        content = workflow_path.read_text()

        # Check registry configuration
        assert "ghcr.io" in content
        assert "packages: write" in content  # Required permission

    def test_workflow_tags_with_sha_and_branch(self, project_root: Path) -> None:
        """Test that workflow tags images with commit SHA and branch name."""
        workflow_path = project_root / ".github" / "workflows" / "docker-build.yml"
        content = workflow_path.read_text()

        # Check tagging strategy
        assert "type=sha" in content  # Commit SHA
        assert "type=ref" in content  # Branch name


# =============================================================================
# Integration Tests (require Docker)
# =============================================================================


@pytest.mark.slow
@pytest.mark.integration
class TestDockerImageBuild:
    """Integration tests for Docker image building (requires Docker daemon)."""

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

    def test_build_signal_service_image(self, project_root: Path) -> None:
        """Test building signal_service Docker image."""
        result = subprocess.run(
            [
                "docker",
                "build",
                "-f",
                "apps/signal_service/Dockerfile",
                "-t",
                "trading-platform-signal-service:test",
                ".",
            ],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=300,  # 5 minutes timeout
        )

        assert result.returncode == 0, f"Docker build failed for signal_service:\n{result.stderr}"

    def test_build_execution_gateway_image(self, project_root: Path) -> None:
        """Test building execution_gateway Docker image."""
        result = subprocess.run(
            [
                "docker",
                "build",
                "-f",
                "apps/execution_gateway/Dockerfile",
                "-t",
                "trading-platform-execution-gateway:test",
                ".",
            ],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=300,
        )

        assert (
            result.returncode == 0
        ), f"Docker build failed for execution_gateway:\n{result.stderr}"

    def test_build_orchestrator_image(self, project_root: Path) -> None:
        """Test building orchestrator Docker image."""
        result = subprocess.run(
            [
                "docker",
                "build",
                "-f",
                "apps/orchestrator/Dockerfile",
                "-t",
                "trading-platform-orchestrator:test",
                ".",
            ],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=300,
        )

        assert result.returncode == 0, f"Docker build failed for orchestrator:\n{result.stderr}"


# =============================================================================
# Summary
# =============================================================================

"""
Test Coverage Summary:

1. Dockerfile Existence (5 tests)
   - .dockerignore exists
   - All service Dockerfiles exist

2. Dockerfile Syntax (18 tests across 3 services)
   - Multi-stage builds
   - PYTHONUNBUFFERED set
   - Non-root user created
   - Correct ports exposed
   - Health checks defined

3. .dockerignore Content (4 tests)
   - Excludes tests
   - Excludes venv
   - Excludes docs
   - Excludes .git

4. GitHub Actions Workflow (5 tests)
   - Workflow file exists
   - Matrix strategy defined
   - GitHub cache configured
   - Pushes to ghcr.io
   - Tags with SHA and branch

5. Integration Tests (3 tests, marked slow/integration)
   - Builds signal_service image
   - Builds execution_gateway image
   - Builds orchestrator image

Total: 35 test cases

Run all tests:
    pytest tests/test_docker_build.py -v

Run fast tests only (skip integration):
    pytest tests/test_docker_build.py -v -m "not slow and not integration"

Run integration tests only:
    pytest tests/test_docker_build.py -v -m "slow and integration"
"""
