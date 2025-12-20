"""UI components for web console."""

from .audit_log_viewer import render_audit_log_viewer
from .bulk_operations import render_bulk_role_change, render_bulk_strategy_operations
from .config_editor import (
    PositionLimitsConfig,
    SystemDefaultsConfig,
    TradingHoursConfig,
    get_config,
    render_config_editor,
    save_config,
)
from .csrf_protection import (
    CSRF_TOKEN_KEY,
    generate_csrf_token,
    get_csrf_input,
    rotate_csrf_token,
    verify_csrf_token,
)
from .factor_exposure_chart import FACTOR_DISPLAY_NAMES, render_factor_exposure
from .session_status import render_session_status
from .strategy_assignment import render_strategy_assignment
from .stress_test_results import (
    SCENARIO_DISPLAY_ORDER,
    SCENARIO_INFO,
    render_factor_waterfall,
    render_scenario_table,
    render_stress_tests,
)
from .user_role_editor import render_role_editor
from .var_chart import (
    DEFAULT_VAR_LIMIT,
    DEFAULT_WARNING_THRESHOLD,
    render_var_gauge,
    render_var_history,
    render_var_metrics,
)

__all__ = [
    "render_session_status",
    "CSRF_TOKEN_KEY",
    "generate_csrf_token",
    "verify_csrf_token",
    "rotate_csrf_token",
    "get_csrf_input",
    "render_role_editor",
    "render_strategy_assignment",
    "render_bulk_role_change",
    "render_bulk_strategy_operations",
    # Risk dashboard components
    "render_factor_exposure",
    "FACTOR_DISPLAY_NAMES",
    "render_var_metrics",
    "render_var_gauge",
    "render_var_history",
    "DEFAULT_VAR_LIMIT",
    "DEFAULT_WARNING_THRESHOLD",
    "render_stress_tests",
    "render_scenario_table",
    "render_factor_waterfall",
    "SCENARIO_DISPLAY_ORDER",
    "SCENARIO_INFO",
    "render_config_editor",
    "TradingHoursConfig",
    "PositionLimitsConfig",
    "SystemDefaultsConfig",
    "get_config",
    "save_config",
    "render_audit_log_viewer",
]
