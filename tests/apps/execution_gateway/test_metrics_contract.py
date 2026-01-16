"""Metrics contract test for Execution Gateway - Phase 0.

This test verifies the metrics module structure without importing the actual
metrics to avoid Prometheus registry conflicts with main.py.

Full contract testing (metric names/labels stability) will be enabled in Phase 1
when we integrate the metrics module by replacing main.py's inline metrics.

See REFACTOR_EXECUTION_GATEWAY_TASK.md Phase 0 for design decisions.
"""

import ast
import inspect
from pathlib import Path


def test_metrics_module_exists():
    """Verify the metrics.py module file exists."""
    metrics_path = Path("apps/execution_gateway/metrics.py")
    assert metrics_path.exists(), f"metrics.py not found at {metrics_path}"


def test_metrics_module_structure():
    """Verify the metrics module has expected structure by parsing the AST.

    This test inspects the module without importing it to avoid Prometheus
    registry conflicts with main.py (which also defines metrics at module level).
    """
    metrics_path = Path("apps/execution_gateway/metrics.py")
    with open(metrics_path) as f:
        source = f.read()

    tree = ast.parse(source)

    # Find all assignments at module level
    module_level_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    module_level_names.add(target.id)

    # Verify expected metrics are defined
    expected_metrics = [
        "orders_total",
        "order_placement_duration",
        "order_placement_duration_seconds",
        "fat_finger_warnings_total",
        "fat_finger_rejections_total",
        "positions_current",
        "pnl_dollars",
        "database_connection_status",
        "redis_connection_status",
        "alpaca_connection_status",
        "alpaca_api_requests_total",
        "webhook_received_total",
        "dry_run_mode",
    ]

    for metric_name in expected_metrics:
        assert metric_name in module_level_names, (
            f"Expected metric '{metric_name}' not found in metrics.py. "
            f"This may indicate a refactoring broke the module structure."
        )

    # Verify registries exist
    assert "METRIC_NAMES" in module_level_names, "METRIC_NAMES registry missing"
    assert "METRIC_LABELS" in module_level_names, "METRIC_LABELS registry missing"


def test_metrics_module_has_initialization_function():
    """Verify initialize_metrics function is defined.

    This test checks for the function without calling it to avoid
    Prometheus registry side effects.
    """
    metrics_path = Path("apps/execution_gateway/metrics.py")
    with open(metrics_path) as f:
        source = f.read()

    tree = ast.parse(source)

    # Find all function definitions
    function_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            function_names.add(node.name)

    assert "initialize_metrics" in function_names, (
        "initialize_metrics function not found. "
        "This function is required for Phase 1 integration."
    )


def test_config_module_exists():
    """Verify the config.py module file exists."""
    config_path = Path("apps/execution_gateway/config.py")
    assert config_path.exists(), f"config.py not found at {config_path}"


def test_app_context_module_exists():
    """Verify the app_context.py module file exists."""
    app_context_path = Path("apps/execution_gateway/app_context.py")
    assert app_context_path.exists(), f"app_context.py not found at {app_context_path}"


def test_dependencies_module_exists():
    """Verify the dependencies.py module file exists."""
    dependencies_path = Path("apps/execution_gateway/dependencies.py")
    assert dependencies_path.exists(), f"dependencies.py not found at {dependencies_path}"


def test_app_factory_module_exists():
    """Verify the app_factory.py module file exists."""
    app_factory_path = Path("apps/execution_gateway/app_factory.py")
    assert app_factory_path.exists(), f"app_factory.py not found at {app_factory_path}"


def test_phase_0_modules_are_importable():
    """Verify Phase 0 modules can be imported without errors.

    This test imports only the modules that don't cause Prometheus registry
    conflicts (app_context, config, dependencies, app_factory).
    """
    # These imports should work without conflicts
    from apps.execution_gateway import app_context, app_factory, config, dependencies

    # Verify key exports exist
    assert hasattr(app_context, "AppContext")
    assert hasattr(config, "ExecutionGatewayConfig")
    assert hasattr(config, "get_config")
    assert hasattr(dependencies, "get_context")
    assert hasattr(dependencies, "get_config")
    assert hasattr(app_factory, "create_app")


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
