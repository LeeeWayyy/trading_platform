"""Metrics contract test for Execution Gateway.

This test verifies:
1. Metrics module structure via AST parsing (avoids import conflicts)
2. Metric names and labels stability (contract testing)
3. METRIC_NAMES and METRIC_LABELS registries consistency

Contract testing ensures that metric names/labels remain stable across
refactorings, preventing dashboard and alert breakage.

See REFACTOR_EXECUTION_GATEWAY_TASK.md for design decisions.
"""

import ast
from pathlib import Path

# ============================================================================
# Contract Constants - These are the expected stable metric names and labels
# Changing these will break dashboards and alerts!
# ============================================================================

EXPECTED_METRIC_NAMES = [
    "execution_gateway_orders_total",
    "execution_gateway_order_placement_duration_seconds",
    "order_placement_duration_seconds",
    "execution_gateway_fat_finger_warnings_total",
    "execution_gateway_fat_finger_rejections_total",
    "execution_gateway_positions_current",
    "execution_gateway_pnl_dollars",
    "execution_gateway_database_connection_status",
    "execution_gateway_redis_connection_status",
    "execution_gateway_alpaca_connection_status",
    "execution_gateway_alpaca_api_requests_total",
    "execution_gateway_webhook_received_total",
    "execution_gateway_dry_run_mode",
]

EXPECTED_METRIC_LABELS = {
    "execution_gateway_orders_total": ["symbol", "side", "status"],
    "execution_gateway_order_placement_duration_seconds": ["symbol", "side"],
    "order_placement_duration_seconds": [],
    "execution_gateway_fat_finger_warnings_total": ["threshold_type"],
    "execution_gateway_fat_finger_rejections_total": ["threshold_type"],
    "execution_gateway_positions_current": ["symbol"],
    "execution_gateway_pnl_dollars": ["type"],
    "execution_gateway_database_connection_status": [],
    "execution_gateway_redis_connection_status": [],
    "execution_gateway_alpaca_connection_status": [],
    "execution_gateway_alpaca_api_requests_total": ["operation", "status"],
    "execution_gateway_webhook_received_total": ["event_type"],
    "execution_gateway_dry_run_mode": [],
}


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


# ============================================================================
# Metric Names Contract Tests
# ============================================================================


def test_metric_names_contract():
    """Verify METRIC_NAMES registry matches expected contract.

    This test ensures metric names remain stable. Changing metric names
    breaks Prometheus queries, Grafana dashboards, and alerts.
    """
    metrics_path = Path("apps/execution_gateway/metrics.py")
    with open(metrics_path) as f:
        source = f.read()

    tree = ast.parse(source)

    # Find METRIC_NAMES list in AST
    metric_names_value = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "METRIC_NAMES":
                    if isinstance(node.value, ast.List):
                        metric_names_value = [
                            elt.value for elt in node.value.elts if isinstance(elt, ast.Constant)
                        ]

    assert metric_names_value is not None, "METRIC_NAMES not found or not a list"

    # Verify all expected metrics are present
    for expected_name in EXPECTED_METRIC_NAMES:
        assert expected_name in metric_names_value, (
            f"CONTRACT VIOLATION: Expected metric '{expected_name}' missing from METRIC_NAMES. "
            f"This will break dashboards and alerts!"
        )

    # Verify no unexpected metrics (warn only - new metrics are okay)
    for actual_name in metric_names_value:
        if actual_name not in EXPECTED_METRIC_NAMES:
            # This is informational - new metrics are allowed
            pass


def test_metric_labels_contract():
    """Verify METRIC_LABELS registry matches expected contract.

    This test ensures metric labels remain stable. Changing labels
    breaks Prometheus queries that filter by label.
    """
    metrics_path = Path("apps/execution_gateway/metrics.py")
    with open(metrics_path) as f:
        source = f.read()

    tree = ast.parse(source)

    # Find METRIC_LABELS dict in AST
    metric_labels_value = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "METRIC_LABELS":
                    if isinstance(node.value, ast.Dict):
                        metric_labels_value = {}
                        for key, value in zip(node.value.keys, node.value.values, strict=False):
                            if isinstance(key, ast.Constant) and isinstance(value, ast.List):
                                labels = [
                                    elt.value for elt in value.elts if isinstance(elt, ast.Constant)
                                ]
                                metric_labels_value[key.value] = labels

    assert metric_labels_value is not None, "METRIC_LABELS not found or not a dict"

    # Verify all expected metrics have correct labels
    for metric_name, expected_labels in EXPECTED_METRIC_LABELS.items():
        assert (
            metric_name in metric_labels_value
        ), f"CONTRACT VIOLATION: Metric '{metric_name}' missing from METRIC_LABELS"
        actual_labels = metric_labels_value[metric_name]
        assert actual_labels == expected_labels, (
            f"CONTRACT VIOLATION: Metric '{metric_name}' has labels {actual_labels}, "
            f"expected {expected_labels}. This will break dashboard queries!"
        )


def test_metric_names_and_labels_consistency():
    """Verify METRIC_NAMES and METRIC_LABELS are consistent.

    Every metric in METRIC_NAMES should have a corresponding entry in METRIC_LABELS.
    """
    metrics_path = Path("apps/execution_gateway/metrics.py")
    with open(metrics_path) as f:
        source = f.read()

    tree = ast.parse(source)

    # Extract both registries
    metric_names = None
    metric_labels_keys = None

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    if target.id == "METRIC_NAMES" and isinstance(node.value, ast.List):
                        metric_names = [
                            elt.value for elt in node.value.elts if isinstance(elt, ast.Constant)
                        ]
                    elif target.id == "METRIC_LABELS" and isinstance(node.value, ast.Dict):
                        metric_labels_keys = [
                            key.value for key in node.value.keys if isinstance(key, ast.Constant)
                        ]

    assert metric_names is not None, "METRIC_NAMES not found"
    assert metric_labels_keys is not None, "METRIC_LABELS not found"

    # Every name should have labels defined
    for name in metric_names:
        assert (
            name in metric_labels_keys
        ), f"CONSISTENCY ERROR: Metric '{name}' is in METRIC_NAMES but missing from METRIC_LABELS"

    # Every label entry should have a corresponding name
    for key in metric_labels_keys:
        assert (
            key in metric_names
        ), f"CONSISTENCY ERROR: Metric '{key}' is in METRIC_LABELS but missing from METRIC_NAMES"


def test_business_metrics_defined():
    """Verify critical business metrics are defined with correct types."""
    metrics_path = Path("apps/execution_gateway/metrics.py")
    with open(metrics_path) as f:
        source = f.read()

    tree = ast.parse(source)

    # Find Counter, Gauge, Histogram definitions
    metric_types = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    if isinstance(node.value, ast.Call):
                        if isinstance(node.value.func, ast.Name):
                            metric_types[target.id] = node.value.func.id

    # Verify critical metrics have correct types
    expected_types = {
        "orders_total": "Counter",
        "order_placement_duration": "Histogram",
        "order_placement_duration_seconds": "Histogram",
        "fat_finger_warnings_total": "Counter",
        "fat_finger_rejections_total": "Counter",
        "positions_current": "Gauge",
        "pnl_dollars": "Gauge",
        "database_connection_status": "Gauge",
        "redis_connection_status": "Gauge",
        "alpaca_connection_status": "Gauge",
        "dry_run_mode": "Gauge",
    }

    for metric_var, expected_type in expected_types.items():
        assert metric_var in metric_types, f"Metric variable '{metric_var}' not defined"
        assert (
            metric_types[metric_var] == expected_type
        ), f"Metric '{metric_var}' should be {expected_type}, got {metric_types[metric_var]}"


def test_service_health_metrics_defined():
    """Verify service health metrics are defined for monitoring."""
    metrics_path = Path("apps/execution_gateway/metrics.py")
    with open(metrics_path) as f:
        source = f.read()

    tree = ast.parse(source)

    # Find all module-level variable names
    defined_metrics = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    defined_metrics.add(target.id)

    # Critical health metrics that must exist
    health_metrics = [
        "database_connection_status",
        "redis_connection_status",
        "alpaca_connection_status",
        "alpaca_api_requests_total",
        "webhook_received_total",
    ]

    for metric in health_metrics:
        assert metric in defined_metrics, (
            f"Health metric '{metric}' is missing. " f"Service monitoring requires this metric."
        )


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
