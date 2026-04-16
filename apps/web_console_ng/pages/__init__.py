"""Pages for NiceGUI web console.

Note: This module does not re-export page functions to avoid shadowing
module names needed for testing. Import directly from submodules:
    from apps.web_console_ng.pages.dashboard import dashboard
    from apps.web_console_ng.pages.manual_order import manual_order_page

⚠️ CRITICAL (P5T7): Import page modules here to trigger @ui.page decorator registration.
Add imports as pages are implemented.
"""

# P5T4-T5 pages (already implemented)
# P5T6 pages
# P5T7 pages (add as implemented):
from apps.web_console_ng.pages import (
    admin,  # noqa: F401
    # P6T19: admin_users removed (single-admin model)
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
    exposure,  # noqa: F401 - P6T15
    feature_browser,  # noqa: F401 - P6T14
    forgot_password,  # noqa: F401 - Auth page
    health,  # noqa: F401
    journal,  # noqa: F401 - P5T8
    login,  # noqa: F401 - Auth page
    manual_order,  # noqa: F401
    mfa_verify,  # noqa: F401 - Auth page
    models,  # noqa: F401 - P6T17
    notebook_launcher,  # noqa: F401 - P5T8
    performance,  # noqa: F401 - P5T8
    position_management,  # noqa: F401
    research,  # noqa: F401 - Cockpit+Forge consolidation
    risk,  # noqa: F401
    scheduled_reports,  # noqa: F401 - P5T8
    shadow_results,  # noqa: F401 - P6T14
    sql_explorer,  # noqa: F401 - P6T14
    strategies,  # noqa: F401 - P6T17
    tax_lots,  # noqa: F401 - P6T16
    universes,  # noqa: F401 - P6T15
)

# Known optional third-party packages that individual page modules may depend on.
# If these are absent we skip the page gracefully; any other missing module is a
# genuine regression and must fail fast.
_OPTIONAL_PACKAGES = frozenset({
    "rq",           # backtest job queue
    "plotly",       # charting in some pages
    "pandas",       # data frames
    "polars",       # data frames
    "strategies",   # strategy helpers (not present in web-console image)
})

# Tracks which modules were skipped due to missing optional deps.
# Exposed for health/readiness diagnostics (see ``get_skipped_page_modules``).
_SKIPPED_MODULES: list[tuple[str, str]] = []

for module_name in _PAGE_MODULES:
    try:
        importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        # Only tolerate missing *optional* third-party packages.  If the page
        # module itself or one of its project-level transitive deps is missing,
        # that is a real regression and should fail fast.
        if exc.name is not None and any(
            exc.name == pkg or exc.name.startswith(f"{pkg}.")
            for pkg in _OPTIONAL_PACKAGES
        ):
            _SKIPPED_MODULES.append((module_name, exc.name))
            logger.warning(
                "page_module_skipped_missing_optional_dependency: module=%s missing_package=%s",
                module_name,
                exc.name,
            )
        else:
            raise


def get_skipped_page_modules() -> list[tuple[str, str]]:
    """Return list of (module_name, missing_package) for skipped page modules.

    Useful for health/readiness diagnostics to surface which pages
    are unavailable due to missing optional dependencies.
    """
    return list(_SKIPPED_MODULES)
