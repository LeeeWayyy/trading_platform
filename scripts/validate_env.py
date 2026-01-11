#!/usr/bin/env python3
"""Validate that local environment matches pyproject.toml dependencies.

This script ensures local and CI environments are in sync by checking that
all packages defined in pyproject.toml are installed with compatible versions.

Usage:
    python scripts/validate_env.py

Exit codes:
    0 - All packages installed and versions compatible
    1 - Missing or incompatible packages found
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[import-not-found,no-redef]


def get_installed_packages() -> dict[str, str]:
    """Get dict of installed packages and their versions."""
    result = subprocess.run(
        [sys.executable, "-m", "pip", "list", "--format=json"],
        capture_output=True,
        text=True,
        check=True,
    )
    import json

    packages = json.loads(result.stdout)
    # Normalize package names (pip uses - but poetry might use _)
    return {pkg["name"].lower().replace("_", "-"): pkg["version"] for pkg in packages}


def parse_pyproject() -> dict[str, str]:
    """Parse pyproject.toml and return dependencies."""
    pyproject_path = Path(__file__).parent.parent / "pyproject.toml"

    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)

    deps: dict[str, str] = {}

    # Main dependencies
    if "tool" in data and "poetry" in data["tool"]:
        poetry = data["tool"]["poetry"]
        if "dependencies" in poetry:
            for name, version in poetry["dependencies"].items():
                if name == "python":
                    continue
                # Normalize package name
                normalized = name.lower().replace("_", "-")
                if isinstance(version, str):
                    deps[normalized] = version
                elif isinstance(version, dict):
                    deps[normalized] = version.get("version", "*")

        # Dev dependencies
        if "group" in poetry:
            for group_data in poetry["group"].values():
                if "dependencies" in group_data:
                    for name, ver in group_data["dependencies"].items():
                        normalized = name.lower().replace("_", "-")
                        if isinstance(ver, str):
                            deps[normalized] = ver
                        elif isinstance(ver, dict):
                            deps[normalized] = ver.get("version", "*")

    return deps


def main() -> int:
    """Check that all pyproject.toml dependencies are installed."""
    print("Validating local environment against pyproject.toml...")
    print()

    try:
        required = parse_pyproject()
        installed = get_installed_packages()
    except FileNotFoundError as e:
        print(f"Error: Configuration file not found: {e}")
        return 2
    except subprocess.CalledProcessError as e:
        print(f"Error: Failed to list installed packages: {e}")
        return 3
    except OSError as e:
        print(f"Error: File I/O failed: {e}")
        return 4

    missing: list[str] = []
    found: list[str] = []

    for pkg in sorted(required.keys()):
        if pkg in installed:
            found.append(f"  {pkg} ({installed[pkg]})")
        else:
            missing.append(pkg)

    if missing:
        print(f"Missing {len(missing)} package(s):")
        for pkg in missing:
            print(f"  - {pkg}")
        print()
        print("To fix, run: poetry install")
        print()
        print("This ensures local environment matches CI.")
        return 1

    print(f"All {len(found)} packages installed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
