---
id: P5T8
title: "NiceGUI Migration - Remaining Streamlit Pages"
phase: P5
task: T8
priority: P1
owner: "@development-team"
state: PLANNING
created: 2026-01-04
dependencies: [P5T1, P5T2, P5T3, P5T4, P5T5, P5T6, P5T7]
estimated_effort: "5-7 days"
related_adrs: [ADR-0031-nicegui-migration]
related_docs: [P5_PLANNING.md, P5T7_TASK.md]
features: [T8.1, T8.2, T8.3, T8.4, T8.5, T8.6]
---

# P5T8: NiceGUI Migration - Remaining Streamlit Pages

**Phase:** P5 (Web Console Modernization)
**Status:** PLANNING
**Priority:** P1 (Feature Parity Completion)
**Owner:** @development-team
**Created:** 2026-01-04
**Estimated Effort:** 5-7 days
**Track:** Final feature parity before Streamlit deprecation
**Dependency:** P5T1-P5T7 complete

---

## Objective

Port the 6 remaining Streamlit-only pages to NiceGUI to achieve 100% feature parity before deprecating Streamlit.

**Success looks like:**
- All 6 remaining pages ported to NiceGUI patterns
- Feature flags preserved for each page
- Permission checks preserved (RBAC parity)
- Services reused from existing `apps/web_console/services/`
- Auto-refresh patterns replaced with `ui.timer`
- `st.session_state` patterns replaced with async + app.storage
- All pages tested with unit tests

**Pages to Port:**

| # | Streamlit Page | Feature Flag | Permission |
|---|----------------|--------------|------------|
| 1 | `alpha_explorer.py` | `FEATURE_ALPHA_EXPLORER` | `VIEW_ALPHA_SIGNALS` |
| 2 | `compare.py` | `FEATURE_STRATEGY_COMPARISON` | `VIEW_PNL` |
| 3 | `journal.py` | `FEATURE_TRADE_JOURNAL` | `VIEW_TRADES` |
| 4 | `notebook_launcher.py` | N/A | `LAUNCH_NOTEBOOKS` |
| 5 | `performance.py` | `FEATURE_PERFORMANCE_DASHBOARD` | `VIEW_PNL` |
| 6 | `scheduled_reports.py` | N/A | `VIEW_REPORTS` |

**Key Pattern Changes (same as P5T7):**
| Streamlit | NiceGUI |
|-----------|---------|
| `st_autorefresh(interval=5000)` | `ui.timer(5.0, callback)` |
| `st.tabs(["Tab1", "Tab2"])` | `with ui.tabs(): ui.tab("Tab1")` |
| `st.expander("Title")` | `with ui.expansion("Title"):` |
| `st.session_state["key"]` | `app.storage.user["key"]` or async state |
| `st.stop()` | `return` (early exit from async function) |
| `st.rerun()` | `.refresh()` on `@ui.refreshable` sections |
| `st.cache_data` | Manual caching or `app.storage` |
| `st.download_button()` | `ui.download()` with bytes |

---

## Acceptance Criteria

### T8.1 Alpha Signal Explorer (1 day)

**Port from:** `apps/web_console/pages/alpha_explorer.py`

**Feature Flag:** `FEATURE_ALPHA_EXPLORER`
**Permission Required:** `VIEW_ALPHA_SIGNALS`

**Deliverables:**
- [ ] Alpha model list with status indicators
- [ ] Model selection dropdown (filtered by status)
- [ ] IC chart component (reuse `ic_chart.py` patterns)
- [ ] Signal correlation matrix display
- [ ] Decay curve visualization
- [ ] Pagination support (DEFAULT_PAGE_SIZE=25, MAX_PAGE_SIZE=100)
- [ ] Feature flag check at page load
- [ ] Permission check with graceful denial message
- [ ] Auto-refresh via `ui.timer`

**Services to Reuse:**
- `apps/web_console/services/alpha_explorer_service.py` → `AlphaExplorerService`
- `libs/models/registry.py` → `ModelRegistry`
- `libs/alpha/metrics.py` → `AlphaMetricsAdapter`

**Implementation Pattern:**
```python
# apps/web_console_ng/pages/alpha_explorer.py
from nicegui import run, ui

from apps.web_console_ng.auth.middleware import get_current_user, requires_auth
from apps.web_console_ng.config import FEATURE_ALPHA_EXPLORER
from apps.web_console_ng.ui.layout import main_layout
from apps.web_console.services.alpha_explorer_service import AlphaExplorerService
from libs.web_console_auth.permissions import Permission, has_permission

DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100


@ui.page("/alpha-explorer")
@requires_auth
@main_layout
async def alpha_explorer_page() -> None:
    """Alpha Signal Explorer page."""
    user = get_current_user()

    if not FEATURE_ALPHA_EXPLORER:
        ui.label("Alpha Explorer feature is disabled.").classes("text-gray-500 p-8")
        return

    if not has_permission(user, Permission.VIEW_ALPHA_SIGNALS):
        ui.notify("Permission denied: VIEW_ALPHA_SIGNALS required", type="negative")
        return

    # Initialize service (sync, wrapped with run.io_bound)
    service = _get_alpha_service()

    ui.label("Alpha Signal Explorer").classes("text-2xl font-bold mb-4")

    # Model selection and display logic...
    # Use @ui.refreshable for data sections
    # Use run.io_bound() for sync service calls
```

**Files to Create:**
- `apps/web_console_ng/pages/alpha_explorer.py`
- `apps/web_console_ng/components/ic_chart.py` (if not exists)
- `apps/web_console_ng/components/correlation_matrix.py`
- `apps/web_console_ng/components/decay_curve.py`
- `tests/apps/web_console_ng/pages/test_alpha_explorer.py`

---

### T8.2 Strategy Comparison Tool (1 day)

**Port from:** `apps/web_console/pages/compare.py`

**Feature Flag:** `FEATURE_STRATEGY_COMPARISON`
**Permission Required:** `VIEW_PNL`

**Deliverables:**
- [ ] Strategy multi-select (2-4 strategies, filtered by user authorization)
- [ ] Date range picker (default 30 days lookback)
- [ ] Equity comparison chart
- [ ] Metrics comparison table
- [ ] Correlation heatmap between strategies
- [ ] Portfolio simulator (combined strategy weights)
- [ ] Feature flag check
- [ ] Strategy scoping via `get_authorized_strategies()`

**Services to Reuse:**
- `apps/web_console/services/comparison_service.py` → `ComparisonService`
- `apps/web_console/data/strategy_scoped_queries.py` → `StrategyScopedDataAccess`

**Implementation Pattern:**
```python
# apps/web_console_ng/pages/compare.py
from datetime import date, timedelta

from nicegui import run, ui

from apps.web_console_ng.auth.middleware import get_current_user, requires_auth
from apps.web_console_ng.config import FEATURE_STRATEGY_COMPARISON
from apps.web_console_ng.ui.layout import main_layout
from apps.web_console.services.comparison_service import ComparisonService
from libs.web_console_auth.permissions import Permission, get_authorized_strategies, has_permission

DEFAULT_LOOKBACK_DAYS = 30
MAX_STRATEGIES = 4
MIN_STRATEGIES = 2


@ui.page("/compare")
@requires_auth
@main_layout
async def strategy_comparison_page() -> None:
    """Strategy Comparison Tool page."""
    user = get_current_user()

    if not FEATURE_STRATEGY_COMPARISON:
        ui.label("Strategy Comparison is disabled.").classes("text-gray-500 p-8")
        return

    if not has_permission(user, Permission.VIEW_PNL):
        ui.notify("Permission denied: VIEW_PNL required", type="negative")
        return

    # Get authorized strategies for this user
    authorized = get_authorized_strategies(user)

    # Strategy selection, date range, comparison logic...
```

**Files to Create:**
- `apps/web_console_ng/pages/compare.py`
- `apps/web_console_ng/components/equity_comparison_chart.py`
- `apps/web_console_ng/components/metrics_comparison_table.py`
- `apps/web_console_ng/components/portfolio_simulator.py`
- `tests/apps/web_console_ng/pages/test_compare.py`

---

### T8.3 Trade Journal (1 day)

**Port from:** `apps/web_console/pages/journal.py`

**Feature Flag:** `FEATURE_TRADE_JOURNAL`
**Permission Required:** `VIEW_TRADES`

**Deliverables:**
- [ ] Trade history table with pagination (DEFAULT_PAGE_SIZE=50, MAX_PAGE_SIZE=100)
- [ ] Date range filter (MAX_RANGE_DAYS=365)
- [ ] Strategy filter (scoped by user authorization)
- [ ] Trade statistics summary cards
- [ ] Export functionality (CSV download)
- [ ] Audit logging on data access
- [ ] Feature flag check
- [ ] Strategy scoping via `get_authorized_strategies()`

**Services to Reuse:**
- `apps/web_console/data/strategy_scoped_queries.py` → `StrategyScopedDataAccess`
- `apps/web_console/components/trade_stats.py` → `render_trade_stats` patterns
- `apps/web_console/components/trade_table.py` → `render_trade_table` patterns

**Implementation Pattern:**
```python
# apps/web_console_ng/pages/journal.py
from nicegui import run, ui

from apps.web_console_ng.auth.middleware import get_current_user, requires_auth
from apps.web_console_ng.config import FEATURE_TRADE_JOURNAL
from apps.web_console_ng.core.audit import log_audit_event
from apps.web_console_ng.ui.layout import main_layout
from libs.web_console_auth.permissions import Permission, get_authorized_strategies, has_permission

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100
MAX_RANGE_DAYS = 365


@ui.page("/journal")
@requires_auth
@main_layout
async def trade_journal_page() -> None:
    """Trade Journal & Analysis page."""
    user = get_current_user()

    if not FEATURE_TRADE_JOURNAL:
        ui.label("Trade Journal feature is disabled.").classes("text-gray-500 p-8")
        return

    if not has_permission(user, Permission.VIEW_TRADES):
        ui.notify("Permission denied: VIEW_TRADES required", type="negative")
        return

    # Log audit event for data access
    await log_audit_event(user, "TRADE_JOURNAL_ACCESS", {})

    # Trade table, filters, stats...
```

**Files to Create:**
- `apps/web_console_ng/pages/journal.py`
- `apps/web_console_ng/components/trade_table.py`
- `apps/web_console_ng/components/trade_stats.py`
- `tests/apps/web_console_ng/pages/test_journal.py`

---

### T8.4 Research Notebook Launcher (1 day)

**Port from:** `apps/web_console/pages/notebook_launcher.py`

**Permission Required:** `LAUNCH_NOTEBOOKS`

**Deliverables:**
- [ ] Notebook template selector dropdown
- [ ] Parameters form (dynamic based on template)
- [ ] Launch button with confirmation
- [ ] Active sessions table with status
- [ ] Session terminate button
- [ ] Error handling for template loading failures
- [ ] Permission check

**Services to Reuse:**
- `apps/web_console/services/notebook_launcher_service.py` → `NotebookLauncherService`

**Implementation Pattern:**
```python
# apps/web_console_ng/pages/notebook_launcher.py
from nicegui import run, ui

from apps.web_console_ng.auth.middleware import get_current_user, requires_auth
from apps.web_console_ng.ui.layout import main_layout
from apps.web_console.services.notebook_launcher_service import (
    NotebookLauncherService,
    SessionStatus,
)
from libs.web_console_auth.permissions import Permission, has_permission


@ui.page("/notebooks")
@requires_auth
@main_layout
async def notebook_launcher_page() -> None:
    """Research Notebook Launcher page."""
    user = get_current_user()

    if not has_permission(user, Permission.LAUNCH_NOTEBOOKS):
        ui.notify("Permission denied: LAUNCH_NOTEBOOKS required", type="negative")
        return

    # Template selector, parameters form, sessions table...
```

**Files to Create:**
- `apps/web_console_ng/pages/notebook_launcher.py`
- `apps/web_console_ng/components/notebook_template_selector.py`
- `apps/web_console_ng/components/notebook_parameters_form.py`
- `apps/web_console_ng/components/active_sessions_table.py`
- `tests/apps/web_console_ng/pages/test_notebook_launcher.py`

---

### T8.5 Performance Dashboard (1 day)

**Port from:** `apps/web_console/pages/performance.py`

**Feature Flag:** `FEATURE_PERFORMANCE_DASHBOARD`
**Permission Required:** `VIEW_PNL`

**Deliverables:**
- [ ] Date range selector (DEFAULT_RANGE_DAYS=30, MAX_RANGE_DAYS=90)
- [ ] Strategy selector (scoped by user authorization)
- [ ] Equity curve chart (reuse existing component)
- [ ] Drawdown chart (reuse existing component)
- [ ] Performance metrics cards (Sharpe, Sortino, Max DD, etc.)
- [ ] Auto-refresh via `ui.timer`
- [ ] Feature flag check
- [ ] Strategy scoping

**Services to Reuse:**
- `apps/web_console/data/strategy_scoped_queries.py` → `StrategyScopedDataAccess`
- Existing NiceGUI components: `equity_curve_chart.py`, `drawdown_chart.py`, `pnl_chart.py`

**Implementation Pattern:**
```python
# apps/web_console_ng/pages/performance.py
from nicegui import run, ui

from apps.web_console_ng.auth.middleware import get_current_user, requires_auth
from apps.web_console_ng.components.drawdown_chart import render_drawdown_chart
from apps.web_console_ng.components.equity_curve_chart import render_equity_curve
from apps.web_console_ng.config import FEATURE_PERFORMANCE_DASHBOARD
from apps.web_console_ng.ui.layout import main_layout
from libs.web_console_auth.permissions import Permission, get_authorized_strategies, has_permission

DEFAULT_RANGE_DAYS = 30
MAX_RANGE_DAYS = 90


@ui.page("/performance")
@requires_auth
@main_layout
async def performance_dashboard_page() -> None:
    """Performance Dashboard page."""
    user = get_current_user()

    if not FEATURE_PERFORMANCE_DASHBOARD:
        ui.label("Performance Dashboard is disabled.").classes("text-gray-500 p-8")
        return

    if not has_permission(user, Permission.VIEW_PNL):
        ui.notify("Permission denied: VIEW_PNL required", type="negative")
        return

    # Charts, metrics, auto-refresh...
```

**Files to Create:**
- `apps/web_console_ng/pages/performance.py`
- `apps/web_console_ng/components/performance_metrics.py`
- `tests/apps/web_console_ng/pages/test_performance.py`

---

### T8.6 Scheduled Reports (1 day)

**Port from:** `apps/web_console/pages/scheduled_reports.py`

**Permission Required:** `VIEW_REPORTS`, `MANAGE_REPORTS` (for create/edit/delete)

**Deliverables:**
- [ ] Report schedules list table
- [ ] Schedule selector dropdown
- [ ] Schedule form (create/edit)
- [ ] Report history table with download links
- [ ] Delete schedule with confirmation
- [ ] Permission checks (view vs manage)

**Services to Reuse:**
- `apps/web_console/services/scheduled_reports_service.py` → `ScheduledReportsService`

**Implementation Pattern:**
```python
# apps/web_console_ng/pages/scheduled_reports.py
from nicegui import run, ui

from apps.web_console_ng.auth.middleware import get_current_user, requires_auth
from apps.web_console_ng.ui.layout import main_layout
from apps.web_console.services.scheduled_reports_service import (
    ReportSchedule,
    ScheduledReportsService,
)
from libs.web_console_auth.permissions import Permission, has_permission


@ui.page("/reports")
@requires_auth
@main_layout
async def scheduled_reports_page() -> None:
    """Scheduled Reports Management page."""
    user = get_current_user()

    if not has_permission(user, Permission.VIEW_REPORTS):
        ui.notify("Permission denied: VIEW_REPORTS required", type="negative")
        return

    can_manage = has_permission(user, Permission.MANAGE_REPORTS)

    # Schedules list, form, history...
```

**Files to Create:**
- `apps/web_console_ng/pages/scheduled_reports.py`
- `apps/web_console_ng/components/report_schedule_form.py`
- `apps/web_console_ng/components/report_history_table.py`
- `tests/apps/web_console_ng/pages/test_scheduled_reports.py`

---

## Pre-Implementation Verification

### Page Inventory Check (REQUIRED before starting)

Before implementing, verify that the 6 pages listed are the **only** remaining Streamlit pages not yet ported:

```bash
# List all Streamlit pages
ls -1 apps/web_console/pages/*.py | grep -v __pycache__ | sort

# List all NiceGUI pages
ls -1 apps/web_console_ng/pages/*.py | grep -v __pycache__ | sort

# Find any pages in Streamlit NOT in NiceGUI
# Expected result: Only the 6 pages listed above (alpha_explorer, compare, journal, notebook_launcher, performance, scheduled_reports)
```

**Pre-check Results (document here before proceeding):**
- [ ] Total Streamlit pages: ____
- [ ] Total NiceGUI pages: ____
- [ ] Unmigrated pages match expected 6: YES/NO
- [ ] Any unexpected pages found: ____

---

## Implementation Approach

### High-Level Plan

1. **C0: Page Inventory Verification** - Pre-check
2. **C1: Alpha Explorer** (T8.1) - 1 day
2. **C2: Strategy Comparison** (T8.2) - 1 day
3. **C3: Trade Journal** (T8.3) - 1 day
4. **C4: Notebook Launcher** (T8.4) - 1 day
5. **C5: Performance Dashboard** (T8.5) - 1 day
6. **C6: Scheduled Reports** (T8.6) - 1 day
7. **C7: Integration Testing** - 1 day

### Component Cycle Pattern

For each component:
1. Create page file following NiceGUI patterns
2. Create required component files
3. Add page route to `pages/__init__.py`
4. Add navigation link to layout
5. Write unit tests
6. Request review

### Testing Strategy

- Unit tests for each page (feature flag, permissions, rendering)
- Component tests for new UI components
- Integration tests for service calls
- Verify `run.io_bound()` used for sync service methods
- **Navigation Testing (C7 Integration):**
  - Verify all 6 page routes registered in `pages/__init__.py`
  - Verify sidebar links added to `ui/layout.py`
  - Smoke test: pages load without errors
  - Verify navigation links functional (click → page renders)

---

## Files to Create/Modify

### New Files (Pages)
```
apps/web_console_ng/pages/
├── alpha_explorer.py
├── compare.py
├── journal.py
├── notebook_launcher.py
├── performance.py
└── scheduled_reports.py
```

### New Files (Components)
```
apps/web_console_ng/components/
├── ic_chart.py
├── correlation_matrix.py
├── decay_curve.py
├── equity_comparison_chart.py
├── metrics_comparison_table.py
├── portfolio_simulator.py
├── trade_table.py
├── trade_stats.py
├── notebook_template_selector.py
├── notebook_parameters_form.py
├── active_sessions_table.py
├── performance_metrics.py
├── report_schedule_form.py
└── report_history_table.py
```

### New Files (Tests)
```
tests/apps/web_console_ng/pages/
├── test_alpha_explorer.py
├── test_compare.py
├── test_journal.py
├── test_notebook_launcher.py
├── test_performance.py
└── test_scheduled_reports.py
```

### Modified Files
- `apps/web_console_ng/pages/__init__.py` - Add new page imports
- `apps/web_console_ng/ui/layout.py` - Add navigation links
- `apps/web_console_ng/config.py` - Ensure feature flags defined

---

## Prerequisites Checklist

- [ ] P5T7 complete (remaining pages ported)
- [ ] All P5T1-P5T6 done
- [ ] Feature flags defined in config
- [ ] Permissions defined in `libs/web_console_auth/permissions.py`

---

## Definition of Done

- [ ] All 6 pages ported to NiceGUI
- [ ] Feature flags working correctly
- [ ] Permission checks enforced
- [ ] Services reused (no duplication)
- [ ] Unit tests passing
- [ ] Navigation links added
- [ ] `make ci-local` passes
- [ ] Code reviewed and approved
- [ ] Ready for P5T9 (Streamlit deprecation)

---

**Last Updated:** 2026-01-04
**Status:** PLANNING
