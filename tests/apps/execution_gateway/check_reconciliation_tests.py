#!/usr/bin/env python3
"""
Quick syntax check for test_reconciliation_comprehensive.py

This script verifies:
1. File can be imported without syntax errors
2. All test functions are properly defined
3. Fixtures are correctly structured
"""

import ast
import sys
from pathlib import Path


def check_test_file(filepath: Path) -> tuple[bool, list[str]]:
    """Check test file for syntax and structure issues."""
    issues = []

    # Read file
    try:
        content = filepath.read_text()
    except Exception as e:
        return False, [f"Failed to read file: {e}"]

    # Parse AST
    try:
        tree = ast.parse(content, filename=str(filepath))
    except SyntaxError as e:
        return False, [f"Syntax error: {e}"]

    # Count test functions and fixtures
    test_count = 0
    fixture_count = 0
    class_count = 0

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            if node.name.startswith("test_"):
                test_count += 1
            # Check for pytest fixtures
            for decorator in node.decorator_list:
                if isinstance(decorator, ast.Name) and "fixture" in decorator.id:
                    fixture_count += 1
                elif isinstance(decorator, ast.Call):
                    if isinstance(decorator.func, ast.Attribute):
                        if "fixture" in decorator.func.attr:
                            fixture_count += 1

        if isinstance(node, ast.ClassDef):
            if node.name.startswith("Test"):
                class_count += 1

    print("✅ File parsed successfully")
    print("📊 Statistics:")
    print(f"   - Test classes: {class_count}")
    print(f"   - Test functions: {test_count}")
    print(f"   - Fixtures: {fixture_count}")

    # Validate minimum counts
    if test_count < 40:
        issues.append(f"Expected at least 40 tests, found {test_count}")

    if fixture_count < 4:
        issues.append(f"Expected at least 4 fixtures, found {fixture_count}")

    if class_count < 10:
        issues.append(f"Expected at least 10 test classes, found {class_count}")

    return len(issues) == 0, issues


def main():
    """Main entry point."""
    test_file = Path(__file__).parent / "test_reconciliation_comprehensive.py"

    if not test_file.exists():
        print(f"❌ Test file not found: {test_file}")
        return 1

    print(f"🔍 Checking test file: {test_file.name}")
    print()

    success, issues = check_test_file(test_file)

    if success:
        print()
        print("✅ All checks passed!")
        print()
        print("Next steps:")
        print("1. Run: pytest test_reconciliation_comprehensive.py --collect-only")
        print("2. Run: pytest test_reconciliation_comprehensive.py -v")
        print("3. Run: pytest test_reconciliation_comprehensive.py --cov=apps/execution_gateway/reconciliation")
        return 0
    else:
        print()
        print("❌ Issues found:")
        for issue in issues:
            print(f"   - {issue}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
