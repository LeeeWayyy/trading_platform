"""Pages for NiceGUI web console.

Note: This module does not re-export page functions to avoid shadowing
module names needed for testing. Import directly from submodules:
    from apps.web_console_ng.pages.dashboard import dashboard
    from apps.web_console_ng.pages.manual_order import manual_order_page

⚠️ CRITICAL (P5T7): Import page modules here to trigger @ui.page decorator registration.
Add imports as pages are implemented.
"""

from __future__ import annotations

import importlib
import logging

logger = logging.getLogger(__name__)

_PAGE_MODULES = (
    "apps.web_console_ng.pages.admin",
    "apps.web_console_ng.pages.alerts",
    "apps.web_console_ng.pages.alpha_explorer",
    "apps.web_console_ng.pages.attribution",
    "apps.web_console_ng.pages.backtest",
    "apps.web_console_ng.pages.circuit_breaker",
    "apps.web_console_ng.pages.compare",
    "apps.web_console_ng.pages.dashboard",
    "apps.web_console_ng.pages.data_coverage",
    "apps.web_console_ng.pages.data_inspector",
    "apps.web_console_ng.pages.data_management",
    "apps.web_console_ng.pages.data_source_status",
    "apps.web_console_ng.pages.execution_quality",
    "apps.web_console_ng.pages.exposure",
    "apps.web_console_ng.pages.feature_browser",
    "apps.web_console_ng.pages.forgot_password",
    "apps.web_console_ng.pages.health",
    "apps.web_console_ng.pages.journal",
    "apps.web_console_ng.pages.login",
    "apps.web_console_ng.pages.manual_order",
    "apps.web_console_ng.pages.mfa_verify",
    "apps.web_console_ng.pages.models",
    "apps.web_console_ng.pages.notebook_launcher",
    "apps.web_console_ng.pages.performance",
    "apps.web_console_ng.pages.position_management",
    "apps.web_console_ng.pages.risk",
    "apps.web_console_ng.pages.scheduled_reports",
    "apps.web_console_ng.pages.shadow_results",
    "apps.web_console_ng.pages.sql_explorer",
    "apps.web_console_ng.pages.strategies",
    "apps.web_console_ng.pages.tax_lots",
    "apps.web_console_ng.pages.universes",
)

for module_name in _PAGE_MODULES:
    try:
        importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        logger.warning(
            "page_module_skipped_missing_dependency: module=%s error=%s",
            module_name,
            exc,
        )
