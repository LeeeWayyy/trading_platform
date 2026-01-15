#!/usr/bin/env python3
"""Check for layer violations: libs should never import from apps.

This script scans all Python files in libs/ to ensure they don't import
from apps/. This prevents circular dependencies and maintains clean
architecture boundaries.

Exit codes:
    0 - No layer violations found
    1 - Layer violations detected
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def find_imports(file_path: Path) -> list[tuple[int, str]]:
    """Find all imports in a Python file.

    Returns:
        List of (line_number, module_name) tuples for imports from 'apps'.
    """
    violations: list[tuple[int, str]] = []

    try:
        source = file_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(file_path))
    except (SyntaxError, UnicodeDecodeError):
        return violations

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("apps.") or alias.name == "apps":
                    violations.append((node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom):
            if node.module and (node.module.startswith("apps.") or node.module == "apps"):
                violations.append((node.lineno, node.module))

    return violations


def check_libs_directory() -> dict[str, list[tuple[int, str]]]:
    """Check all Python files in libs/ for layer violations.

    Returns:
        Dict mapping file paths to list of violations (line_number, module_name).
    """
    libs_dir = PROJECT_ROOT / "libs"
    all_violations: dict[str, list[tuple[int, str]]] = {}

    for py_file in libs_dir.rglob("*.py"):
        # Skip __pycache__ directories
        if "__pycache__" in py_file.parts:
            continue

        violations = find_imports(py_file)
        if violations:
            rel_path = str(py_file.relative_to(PROJECT_ROOT))
            all_violations[rel_path] = violations

    return all_violations


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Check for layer violations (libs importing from apps)"
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Show detailed output")
    args = parser.parse_args()

    print("Checking for layer violations (libs → apps imports)...")

    violations = check_libs_directory()

    if not violations:
        print("✓ No layer violations found")
        return 0

    print(f"\n❌ Found layer violations in {len(violations)} file(s):\n")

    for file_path, file_violations in sorted(violations.items()):
        print(f"  {file_path}:")
        for line_no, module in file_violations:
            print(f"    Line {line_no}: imports '{module}'")

    print(
        "\nLayer violation: libs/ should never import from apps/."
        "\nUse dependency injection or move shared code to libs/."
    )

    return 1


if __name__ == "__main__":
    sys.exit(main())
