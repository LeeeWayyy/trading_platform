"""CI governance tests for operations auth stub.

These tests ensure dev auth stub cannot leak to production/staging.
They run in CI and block merges if violated.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

# Regex to catch OPERATIONS_DEV_AUTH with truthy values in various formats:
# - OPERATIONS_DEV_AUTH=true (shell/env)
# - OPERATIONS_DEV_AUTH: true (YAML)
# - OPERATIONS_DEV_AUTH: "true" (YAML quoted)
# - operations_dev_auth: True (case variations)
_DEV_AUTH_TRUTHY_PATTERN = re.compile(
    r"operations_dev_auth\s*[=:]\s*['\"]?\s*(true|1|yes|on)\s*['\"]?",
    re.IGNORECASE,
)


class TestOperationsAuthGovernance:
    """Governance tests for OPERATIONS_DEV_AUTH."""

    @pytest.fixture()
    def project_root(self) -> Path:
        """Get project root directory."""
        # Path: tests/apps/web_console/test_operations_auth_governance.py
        # parents[0] = tests/apps/web_console, [1] = tests/apps, [2] = tests, [3] = project root
        return Path(__file__).parents[3]

    def test_no_dev_auth_in_prod_env(self, project_root: Path) -> None:
        """CI fails if OPERATIONS_DEV_AUTH is truthy in .env.prod."""
        prod_env = project_root / ".env.prod"
        if prod_env.exists():
            content = prod_env.read_text()
            match = _DEV_AUTH_TRUTHY_PATTERN.search(content)
            assert (
                match is None
            ), f"OPERATIONS_DEV_AUTH with truthy value found in .env.prod: '{match.group()}'"

    def test_no_dev_auth_in_staging_env(self, project_root: Path) -> None:
        """CI fails if OPERATIONS_DEV_AUTH is truthy in .env.staging."""
        staging_env = project_root / ".env.staging"
        if staging_env.exists():
            content = staging_env.read_text()
            match = _DEV_AUTH_TRUTHY_PATTERN.search(content)
            assert (
                match is None
            ), f"OPERATIONS_DEV_AUTH with truthy value found in .env.staging: '{match.group()}'"

    def test_no_dev_auth_in_docker_compose_prod(self, project_root: Path) -> None:
        """CI fails if OPERATIONS_DEV_AUTH is truthy in docker-compose.prod.yml."""
        compose_prod = project_root / "docker-compose.prod.yml"
        if compose_prod.exists():
            content = compose_prod.read_text()
            match = _DEV_AUTH_TRUTHY_PATTERN.search(content)
            assert match is None, (
                f"OPERATIONS_DEV_AUTH with truthy value found in docker-compose.prod.yml: "
                f"'{match.group()}'"
            )

    def test_no_dev_auth_in_docker_compose_staging(self, project_root: Path) -> None:
        """CI fails if OPERATIONS_DEV_AUTH is truthy in docker-compose.staging.yml."""
        compose_staging = project_root / "docker-compose.staging.yml"
        if compose_staging.exists():
            content = compose_staging.read_text()
            match = _DEV_AUTH_TRUTHY_PATTERN.search(content)
            assert match is None, (
                f"OPERATIONS_DEV_AUTH with truthy value found in docker-compose.staging.yml: "
                f"'{match.group()}'"
            )

    def test_no_dev_auth_in_infra_deploy_configs(self, project_root: Path) -> None:
        """CI fails if OPERATIONS_DEV_AUTH is truthy in any infra deploy configs."""
        infra_dir = project_root / "infra"
        if infra_dir.exists():
            # Scan both .yml and .yaml extensions
            for ext in ("*.yml", "*.yaml"):
                for config_file in infra_dir.rglob(ext):
                    if "prod" in config_file.name.lower() or "staging" in config_file.name.lower():
                        content = config_file.read_text()
                        match = _DEV_AUTH_TRUTHY_PATTERN.search(content)
                        assert match is None, (
                            f"OPERATIONS_DEV_AUTH with truthy value found in "
                            f"{config_file}: '{match.group()}'"
                        )

    @pytest.mark.parametrize("allowed_env", ["development", "dev", "local", "test", "ci"])
    def test_runtime_guard_allows_dev_environments(
        self, monkeypatch: pytest.MonkeyPatch, allowed_env: str
    ) -> None:
        """Runtime guard should allow dev auth in explicitly allowed environments."""
        monkeypatch.setenv("OPERATIONS_DEV_AUTH", "true")
        monkeypatch.setenv("ENVIRONMENT", allowed_env)

        # Import should NOT trigger sys.exit
        import importlib

        import apps.web_console.auth.operations_auth as ops_auth

        importlib.reload(ops_auth)
        # If we get here, test passes

    def _reload_operations_auth(self) -> None:
        """Helper to reload operations_auth module (triggers runtime guard)."""
        import importlib

        import apps.web_console.auth.operations_auth as ops_auth

        importlib.reload(ops_auth)

    @pytest.mark.parametrize(
        "blocked_env", ["production", "prod", "staging", "stage", "unknown", "prod1"]
    )
    def test_runtime_guard_blocks_non_allowed_environments(
        self, monkeypatch: pytest.MonkeyPatch, blocked_env: str
    ) -> None:
        """Runtime guard should block any environment not in allowlist (fail-closed)."""
        monkeypatch.setenv("OPERATIONS_DEV_AUTH", "true")
        monkeypatch.setenv("ENVIRONMENT", blocked_env)

        with pytest.raises(SystemExit) as exc_info:
            self._reload_operations_auth()

        assert exc_info.value.code == 1

    def test_runtime_guard_blocks_unset_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Runtime guard should block when ENVIRONMENT is unset (fail-closed)."""
        monkeypatch.setenv("OPERATIONS_DEV_AUTH", "true")
        monkeypatch.delenv("ENVIRONMENT", raising=False)

        with pytest.raises(SystemExit) as exc_info:
            self._reload_operations_auth()

        assert exc_info.value.code == 1

    def test_runtime_guard_blocks_empty_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Runtime guard should block when ENVIRONMENT is empty string."""
        monkeypatch.setenv("OPERATIONS_DEV_AUTH", "true")
        monkeypatch.setenv("ENVIRONMENT", "")

        with pytest.raises(SystemExit) as exc_info:
            self._reload_operations_auth()

        assert exc_info.value.code == 1


class TestOperationsAuthSessionContract:
    """Unit tests for auth stub session state contract."""

    def test_dev_stub_sets_full_session_state(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Dev stub must set all required session keys for RBAC parity."""
        monkeypatch.setenv("OPERATIONS_DEV_AUTH", "true")
        monkeypatch.setenv("ENVIRONMENT", "development")

        # Mock streamlit session_state
        mock_session: dict[str, Any] = {}
        monkeypatch.setattr("streamlit.session_state", mock_session)

        import importlib

        import apps.web_console.auth.operations_auth as ops_auth

        importlib.reload(ops_auth)

        # Create a dummy function and wrap it
        @ops_auth.operations_requires_auth
        def dummy_page() -> str:
            return "rendered"

        # Call the wrapped function
        result = dummy_page()

        # Verify all required session keys are set
        assert mock_session["authenticated"] is True
        assert mock_session["username"] == "dev_user"
        assert mock_session["user_id"] == "dev_user_id"
        assert mock_session["auth_method"] == "dev_stub"
        assert mock_session["session_id"] == "dev_session"
        assert mock_session["role"] == "admin"  # Admin for operations
        assert mock_session["strategies"] == ["*"]
        assert result == "rendered"

    def test_prod_mode_uses_real_auth(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When dev auth disabled, should delegate to real requires_auth."""
        monkeypatch.setenv("OPERATIONS_DEV_AUTH", "false")
        monkeypatch.setenv("ENVIRONMENT", "development")

        # Track whether real requires_auth was called
        sentinel_called = []

        def sentinel_requires_auth(func: Any) -> Any:
            """Sentinel wrapper to detect real auth decorator usage."""
            sentinel_called.append(func.__name__)
            return func

        # Patch the real requires_auth before reloading
        monkeypatch.setattr(
            "apps.web_console.auth.streamlit_helpers.requires_auth",
            sentinel_requires_auth,
        )

        import importlib

        import apps.web_console.auth.operations_auth as ops_auth

        importlib.reload(ops_auth)

        @ops_auth.operations_requires_auth
        def dummy_page() -> str:
            return "rendered"

        # Verify the real requires_auth was invoked (not the stub)
        assert (
            "dummy_page" in sentinel_called
        ), "Real requires_auth was not called - stub may be leaking to prod mode"

    def test_prod_mode_does_not_inject_stub_session(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When dev auth disabled, stub session keys should NOT be injected."""
        monkeypatch.setenv("OPERATIONS_DEV_AUTH", "false")
        monkeypatch.setenv("ENVIRONMENT", "development")

        # Mock streamlit session_state
        mock_session: dict[str, Any] = {}
        monkeypatch.setattr("streamlit.session_state", mock_session)

        # Patch requires_auth to a no-op so we can test the function
        monkeypatch.setattr(
            "apps.web_console.auth.streamlit_helpers.requires_auth",
            lambda f: f,
        )

        import importlib

        import apps.web_console.auth.operations_auth as ops_auth

        importlib.reload(ops_auth)

        @ops_auth.operations_requires_auth
        def dummy_page() -> str:
            return "rendered"

        # Call the function
        dummy_page()

        # Verify stub session keys were NOT injected
        assert (
            "auth_method" not in mock_session or mock_session.get("auth_method") != "dev_stub"
        ), "Dev stub session keys were injected when OPERATIONS_DEV_AUTH=false"


class TestAuthStubRemovalGate:
    """Gate test to ensure stub is removed after T6.1 ships."""

    @pytest.fixture()
    def project_root(self) -> Path:
        # Path: tests/apps/web_console/test_operations_auth_governance.py
        # parents[0] = tests/apps/web_console, [1] = tests/apps, [2] = tests, [3] = project root
        return Path(__file__).parents[3]

    @pytest.mark.skip(reason="Enable after T6.1 ships")
    def test_no_auth_stub_references_after_t61(self, project_root: Path) -> None:
        """After T6.1 ships, CI fails if operations_requires_auth is referenced."""
        import subprocess

        result = subprocess.run(
            ["grep", "-r", "operations_requires_auth", str(project_root / "apps")],
            capture_output=True,
            text=True,
        )
        assert (
            result.returncode != 0
        ), f"Found references to operations_requires_auth after T6.1:\n{result.stdout}"
