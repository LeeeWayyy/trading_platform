#!/usr/bin/env python3
"""
Git Utilities - Shared git operations for workflow automation.

Provides common git operations used by multiple workflow components:
- Detecting staged files
- Analyzing changed modules
- Identifying core packages that trigger full CI

Used by:
- SmartTestRunner (Component 1): Module detection for targeted testing
- DelegationRules (Component 2): Change analysis for delegation decisions
- DebugRescue (Component 5): Git history analysis for rescue context

Author: Claude Code
Date: 2025-11-07
"""

import subprocess
from pathlib import Path
from typing import List, Set, Optional

# Project root
PROJECT_ROOT = Path(__file__).parent.parent

# Core packages that always trigger full CI
# These are foundational components where changes may have cross-cutting impact
CORE_PACKAGES = {
    "libs/",          # Shared libraries used across apps
    "config/",        # Configuration files
    "infra/",         # Infrastructure configs (docker, prometheus, etc.)
    "tests/fixtures/",  # Shared test fixtures
    "scripts/",       # Build and automation scripts
}


def get_staged_files() -> Optional[List[str]]:
    """
    Get list of staged files from git.

    Returns:
        List of file paths relative to project root.
        Empty list if no files staged.
        None if git command failed (fail-safe: triggers full CI).

    Example:
        >>> get_staged_files()
        ['libs/allocation/multi_alpha.py', 'tests/libs/allocation/test_multi_alpha.py']
    """
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,  # Prevent hanging on stalled git processes
        )
        files = result.stdout.strip().split("\n")
        return [f for f in files if f]  # Filter empty strings
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        # Git command failed or timed out
        # Return None to trigger fail-safe full CI (not empty list)
        return None


def detect_changed_modules(files: List[str]) -> Set[str]:
    """
    Analyze files to determine impacted modules.

    Extracts module paths from changed files. A module is defined as the
    first two path components for code under libs/, apps/, or strategies/.

    Args:
        files: List of file paths (e.g., from get_staged_files())

    Returns:
        Set of module paths (e.g., {"libs/allocation", "apps/execution_gateway"})

    Example:
        >>> detect_changed_modules([
        ...     "libs/allocation/multi_alpha.py",
        ...     "libs/allocation/base.py",
        ...     "apps/execution_gateway/order_placer.py"
        ... ])
        {'libs/allocation', 'apps/execution_gateway'}
    """
    modules = set()
    for file in files:
        # Normalize to POSIX path for consistent handling
        posix_path = Path(file).as_posix()

        # Extract module path for code directories
        if posix_path.startswith(("libs/", "apps/", "strategies/")):
            # Extract first two components: "libs/allocation/multi_alpha.py" → "libs/allocation"
            parts = posix_path.split("/")
            if len(parts) >= 2:
                module = "/".join(parts[:2])
                modules.add(module)

    return modules


def is_core_package(file: str) -> bool:
    """
    Check if file belongs to a core package that triggers full CI.

    Core packages are foundational components where changes may have
    cross-cutting impact across the entire codebase.

    Args:
        file: File path (e.g., from get_staged_files())

    Returns:
        True if file is in a core package, False otherwise

    Example:
        >>> is_core_package("libs/common/types.py")
        True
        >>> is_core_package("apps/execution_gateway/order_placer.py")
        False
        >>> is_core_package("scripts/workflow_gate.py")
        True
    """
    # Normalize to POSIX path for consistent prefix matching
    posix_path = Path(file).as_posix()

    # Check if file starts with any core package prefix
    # Note: CORE_PACKAGES entries have trailing slashes to avoid false matches
    # (e.g., libs/ matches libs/common but not libs_special/)
    return posix_path.startswith(tuple(CORE_PACKAGES))


def requires_full_ci(staged_files: List[str]) -> bool:
    """
    Check if any staged file requires full CI.

    Full CI is required if:
    1. Any file is in a CORE_PACKAGE (foundational component)
    2. More than 5 modules changed (likely a refactor)

    Args:
        staged_files: List of staged files (from get_staged_files())

    Returns:
        True if full CI required, False if targeted testing OK

    Example:
        >>> requires_full_ci(["libs/common/types.py"])  # Core package
        True
        >>> requires_full_ci(["apps/exec/order.py"])  # Single app module
        False
        >>> requires_full_ci([  # >5 modules → likely refactor
        ...     "apps/app1/foo.py", "apps/app2/bar.py", "apps/app3/baz.py",
        ...     "libs/lib1/x.py", "libs/lib2/y.py", "libs/lib3/z.py"
        ... ])
        True
    """
    # Check 1: Any core package changed?
    for file in staged_files:
        if is_core_package(file):
            return True

    # Check 2: >5 modules changed?
    modules = detect_changed_modules(staged_files)
    if len(modules) > 5:
        return True

    return False
