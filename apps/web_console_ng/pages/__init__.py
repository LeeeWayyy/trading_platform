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
    backtest,  # noqa: F401
    circuit_breaker,  # noqa: F401
    compare,  # noqa: F401 - P5T8
    dashboard,  # noqa: F401
    data_management,  # noqa: F401
    health,  # noqa: F401
    journal,  # noqa: F401 - P5T8
    kill_switch,  # noqa: F401
    manual_order,  # noqa: F401
    notebook_launcher,  # noqa: F401 - P5T8
    performance,  # noqa: F401 - P5T8
    position_management,  # noqa: F401
    risk,  # noqa: F401
    scheduled_reports,  # noqa: F401 - P5T8
)
