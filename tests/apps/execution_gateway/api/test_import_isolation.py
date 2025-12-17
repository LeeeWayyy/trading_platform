"""Ensure execution_gateway does not import from apps.web_console."""

from __future__ import annotations

import ast
from pathlib import Path


def test_no_web_console_imports():
    gateway_path = Path("apps/execution_gateway")
    for py_file in gateway_path.rglob("*.py"):
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith(
                        "apps.web_console"
                    ), f"Forbidden import: {alias.name} in {py_file}"
            if isinstance(node, ast.ImportFrom):
                if node.module and node.module.startswith("apps.web_console"):
                    raise AssertionError(f"Forbidden import: {node.module} in {py_file}")
