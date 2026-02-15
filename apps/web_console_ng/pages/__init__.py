"""Pages for NiceGUI web console.

Note: This module does not re-export page functions to avoid shadowing
module names needed for testing. Import directly from submodules:
    from apps.web_console_ng.pages.dashboard import dashboard
    from apps.web_console_ng.pages.manual_order import manual_order_page

⚠️ CRITICAL (P5T7): Import page modules here to trigger @ui.page decorator registration.
Add imports as pages are implemented:
"""

# P5T4-T5 pages (already implemented)
# P5T6 pages
# P5T7 pages (add as implemented):
from apps.web_console_ng.pages import (
    admin,  # noqa: F401
    alerts,  # noqa: F401
    alpha_explorer,  # noqa: F401 - P5T8
    attribution,  # noqa: F401 - P6T10
    backtest,  # noqa: F401
    circuit_breaker,  # noqa: F401
    compare,  # noqa: F401 - P5T8
    dashboard,  # noqa: F401
    data_coverage,  # noqa: F401 - P6T13
    data_inspector,  # noqa: F401 - P6T13
    data_management,  # noqa: F401
    data_source_status,  # noqa: F401 - P6T14
    execution_quality,  # noqa: F401 - P6T8
    feature_browser,  # noqa: F401 - P6T14
    forgot_password,  # noqa: F401 - Auth page
    health,  # noqa: F401
    journal,  # noqa: F401 - P5T8
    login,  # noqa: F401 - Auth page
    manual_order,  # noqa: F401
    mfa_verify,  # noqa: F401 - Auth page
    notebook_launcher,  # noqa: F401 - P5T8
    performance,  # noqa: F401 - P5T8
    position_management,  # noqa: F401
    risk,  # noqa: F401
    scheduled_reports,  # noqa: F401 - P5T8
    shadow_results,  # noqa: F401 - P6T14
    sql_explorer,  # noqa: F401 - P6T14
)
