"""CI governance tests for backtest auth stub.

These tests ensure the dev auth stub (BACKTEST_DEV_AUTH) is never enabled
in production or staging environments.

SECURITY:
- test_no_dev_auth_in_prod: Fails if BACKTEST_DEV_AUTH=true in prod config
- test_no_dev_auth_in_staging: Fails if BACKTEST_DEV_AUTH=true in staging config
- test_no_auth_stub_references_after_t61: Fails if stub references remain after T6.1

These tests are part of the CI pipeline and prevent accidental security bypasses.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest


def load_environment_from_files(env_file: str, compose_file: str) -> dict[str, str]:
    """Load environment variables from env file and docker-compose.

    NOTE: Uses regex-based parsing for docker-compose to avoid PyYAML dependency.
    This is sufficient for checking BACKTEST_DEV_AUTH=true patterns.

    Args:
        env_file: Path to .env file (e.g., ".env.prod")
        compose_file: Path to docker-compose file (e.g., "docker-compose.prod.yml")

    Returns:
        Dict of environment variable names to values
    """
    env_vars: dict[str, str] = {}

    # Check .env file if exists
    env_path = Path(env_file)
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                key, value = line.split("=", 1)
                env_vars[key.strip()] = value.strip().strip('"').strip("'")

    # Also check docker-compose environment section (in project root)
    # Use regex instead of yaml to avoid PyYAML dependency
    compose_path = Path(compose_file)
    if compose_path.exists():
        content = compose_path.read_text()

        # Pattern 1: Direct assignment - BACKTEST_DEV_AUTH=true or "true"
        direct_pattern = re.compile(
            r"^\s*-?\s*([A-Z_][A-Z0-9_]*)(?:=|:\s*)([\"']?)(\w+)\2\s*$",
            re.MULTILINE,
        )
        for match in direct_pattern.finditer(content):
            key, _, value = match.groups()
            env_vars[key] = value

        # Pattern 2: Env substitution with defaults - ${BACKTEST_DEV_AUTH:-true}
        # This catches cases like: BACKTEST_DEV_AUTH=${BACKTEST_DEV_AUTH:-true}
        subst_pattern = re.compile(
            r"([A-Z_][A-Z0-9_]*)=\$\{[^}]*:-(\w+)\}",
            re.MULTILINE,
        )
        for match in subst_pattern.finditer(content):
            key, default_value = match.groups()
            # Only set if not already set by direct pattern (direct takes precedence)
            if key not in env_vars:
                env_vars[key] = default_value

    return env_vars


def _check_no_dev_auth(env_vars: dict[str, str], env_name: str) -> None:
    """Assert BACKTEST_DEV_AUTH is not enabled.

    Args:
        env_vars: Environment variables dict
        env_name: Environment name for error message (e.g., "production")

    Raises:
        AssertionError: If BACKTEST_DEV_AUTH=true is found
    """
    value = env_vars.get("BACKTEST_DEV_AUTH", "false").lower()
    assert value != "true", (
        f"BACKTEST_DEV_AUTH=true is set in {env_name} config! "
        "This must be removed before T5.3 goes to prod/staging."
    )


def test_no_dev_auth_in_prod() -> None:
    """CI guard: dev auth stub must not be enabled in production.

    Checks:
    - .env.prod file
    - docker-compose.prod.yml environment section

    Skips gracefully if config files don't exist.
    """
    prod_env = load_environment_from_files(".env.prod", "docker-compose.prod.yml")

    # If no config files found, skip (graceful handling for repos without prod config)
    if not prod_env:
        # Check if at least one file exists to determine if we should test
        if not Path(".env.prod").exists() and not Path("docker-compose.prod.yml").exists():
            pytest.skip("No production config files found (.env.prod, docker-compose.prod.yml)")

    _check_no_dev_auth(prod_env, "production")


def test_no_dev_auth_in_staging() -> None:
    """CI guard: dev auth stub must not be enabled in staging.

    Checks:
    - .env.staging file
    - docker-compose.staging.yml environment section

    Skips gracefully if config files don't exist.
    """
    staging_env = load_environment_from_files(".env.staging", "docker-compose.staging.yml")

    # If no config files found, skip (graceful handling)
    if not staging_env:
        if not Path(".env.staging").exists() and not Path("docker-compose.staging.yml").exists():
            pytest.skip(
                "No staging config files found (.env.staging, docker-compose.staging.yml)"
            )

    _check_no_dev_auth(staging_env, "staging")


def test_no_auth_stub_references_after_t61() -> None:
    """CI guard: After T6.1 ships, no code should reference backtest_requires_auth.

    This test detects manual import regressions where developers accidentally
    import the stub decorator instead of the real @requires_auth after T6.1.

    The test uses backtest_auth.py existence as the T6.1 completion marker:
    - File exists = T6.1 pending, stub is expected, skip test
    - File deleted = T6.1 complete, verify no stale references

    CRITICAL: Cannot use streamlit_helpers.py existence (it already exists).
    """
    # Check if T6.1 has shipped by looking for explicit completion marker.
    # When T6.1 ships, backtest_auth.py should be deleted per rollback path.
    t61_marker = Path("apps/web_console/auth/backtest_auth.py")
    if t61_marker.exists():
        # Stub file still exists = T6.1 not yet complete, skip this test
        pytest.skip("T6.1 not yet shipped; auth stub backtest_auth.py still exists")

    # T6.1 complete (stub deleted) - verify no stale references remain
    result = subprocess.run(
        ["grep", "-r", "backtest_requires_auth", "apps/"],
        capture_output=True,
        text=True,
    )

    # grep returns 0 if matches found, 1 if no matches, 2+ on error
    if result.returncode == 0:
        pytest.fail(
            f"Found backtest_requires_auth references after T6.1 shipped! "
            f"These must be replaced with @requires_auth:\n{result.stdout}"
        )
    # returncode 1 = no matches = test passes


def test_dev_auth_stub_sets_required_session_keys() -> None:
    """Verify dev auth stub sets all required session keys for RBAC parity.

    The stub must set the same keys as OAuth2 auth for get_user_info()
    and RBAC checks to work correctly.
    """
    # Skip if stub doesn't exist (T6.1 already shipped)
    stub_path = Path("apps/web_console/auth/backtest_auth.py")
    if not stub_path.exists():
        pytest.skip("Auth stub already removed (T6.1 complete)")

    content = stub_path.read_text()

    # Required session keys that must be set by the stub
    required_keys = [
        "authenticated",
        "username",
        "user_id",
        "auth_method",
        "session_id",
        "role",  # For RBAC
        "strategies",  # For strategy access control
    ]

    for key in required_keys:
        assert f'session_state["{key}"]' in content or f"session_state['{key}']" in content, (
            f"Dev auth stub missing required session key: {key}"
        )
