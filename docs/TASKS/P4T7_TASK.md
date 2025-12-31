---
id: P4T7
title: "Web Console - Research & Reporting"
phase: P4
task: T7
priority: P1
owner: "@development-team"
state: PLANNING
created: 2025-12-24
dependencies: [T6.1, T2.8, T2.1, T6.2, T6.5]
estimated_effort: "14-19 days"
related_adrs: [ADR-0030-reporting-architecture]
related_docs: [P4_PLANNING.md]
features: [T9.1, T9.2, T9.3, T9.4, T9.5, T9.6]
---

# P4T7: Web Console - Research & Reporting

**Phase:** P4 (Advanced Features & Research)
**Status:** PLANNING
**Priority:** P1 (Research Infrastructure)
**Owner:** @development-team
**Created:** 2025-12-24
**Estimated Effort:** 14-19 days
**Track:** Track 9 from P4_PLANNING.md

---

## Objective

Build research tools and reporting features for the trading platform web console. These dashboards enable quantitative researchers to explore alpha signals, analyze factor exposures, and generate scheduled reports for stakeholders.

**Success looks like:**
- Researchers can browse and analyze alpha signals from the model registry with IC visualization and decay curves
- Portfolio factor exposures are displayed as interactive heatmaps with time-series evolution
- Jupyter notebooks can be launched with pre-configured environments for ad-hoc research
- Scheduled reports (PDF/HTML) are generated and distributed automatically via email
- Tax lot tracking enables accurate cost basis reporting with wash sale detection

**Measurable SLAs:**
| Metric | Target | Measurement |
|--------|--------|-------------|
| Alpha explorer load time | <3s | Time from page load to signal list display |
| Heatmap render time | <2s | Time to render factor exposure heatmap |
| Report generation latency | P95 <5min | Time from schedule trigger to PDF generation |
| Report delivery latency | P95 <10min | Time from generation to email delivery |
| Tax calculation accuracy | 100% | Validated against reference implementations |

---

## Acceptance Criteria

### T9.1 Alpha Signal Explorer
- [ ] Browse registered alpha signals from model registry with filtering (by status, type, IC range)
- [ ] IC (Information Coefficient) time-series visualization with rolling windows
- [ ] Rank IC and Pearson IC side-by-side comparison charts
- [ ] Decay curve analysis showing IC at multiple horizons (1, 2, 5, 10, 20, 60 days)
- [ ] Signal correlation matrix (heatmap) for selected signals
- [ ] Backtest quick-launch button for selected signal (redirects to backtest page with pre-filled config)
- [ ] Export signal metadata and metrics to CSV
- [ ] RBAC: VIEW_ALPHA_SIGNALS permission required
- [ ] Pagination for large signal lists (default 25, max 100 per page)

### T9.2 Factor Exposure Heatmap
- [ ] Interactive factor exposure heatmap (rows: factors, columns: dates or strategies)
- [ ] Color scale: red (negative) → white (neutral) → green (positive) exposure
- [ ] Portfolio vs benchmark exposure comparison view
- [ ] Time-series evolution of exposures (animated or slider-controlled)
- [ ] Drill-down to stock-level exposures for selected factor
- [ ] Factor definitions from `libs/factors/factor_definitions.py` (value, quality, momentum, size, low-vol)
- [ ] Export heatmap as PNG or CSV data
- [ ] RBAC: VIEW_FACTOR_ANALYTICS permission required

### T9.3 Research Notebook Launcher (STRETCH)
- [ ] One-click Jupyter notebook launch from web console
- [ ] Pre-configured environment with PYTHONPATH and data paths set
- [ ] Template notebooks for common analyses (alpha research, factor analysis, backtest review)
- [ ] Session management: start/stop/status of notebook server
- [ ] Security: notebooks run in isolated container with read-only data access
- [ ] Auto-shutdown after 4 hours of inactivity
- [ ] RBAC: LAUNCH_NOTEBOOKS permission required (admin/researcher roles only)

### T9.4 Scheduled Reports
- [ ] Report template configuration UI (select metrics, date range, strategies)
- [ ] Schedule management: daily (EOD), weekly (Monday 6am), monthly (1st of month)
- [ ] PDF generation using WeasyPrint or ReportLab with platform branding
- [ ] HTML generation with embedded charts (Plotly static exports)
- [ ] Email distribution with attachment or inline HTML
- [ ] Report archive: persist generated reports with 90-day retention
- [ ] Report preview before scheduling
- [ ] Immediate "Run Now" option for testing
- [ ] RBAC: MANAGE_REPORTS permission for create/edit, VIEW_REPORTS for viewing archive
- [ ] Delivery confirmation tracking with retry on failure

### T9.5 Tax Lot Reporter - Core
- [ ] Cost basis tracking with FIFO, LIFO, and Specific ID methods
- [ ] Method selection per account or global default
- [ ] Realized gains/losses report by tax year
- [ ] Short-term vs long-term capital gains classification (1-year holding period)
- [ ] Year-end tax summary with totals by category
- [ ] Export for tax software (TurboTax TXF format, CSV, PDF)
- [ ] Position lot viewer: see individual lots with purchase date, cost, current value
- [ ] RBAC: VIEW_TAX_REPORTS permission required
- [ ] Audit trail for cost basis method changes

### T9.6 Tax Lot Reporter - Advanced (STRETCH)
- [ ] Wash sale rule detection (30-day window before/after sale)
- [ ] Wash sale adjustment calculations (disallowed loss added to replacement cost basis)
- [ ] Disallowed loss tracking with carry-forward display
- [ ] IRS Form 8949 format export
- [ ] Tax-loss harvesting recommendations (identify losses to realize while avoiding wash sales)
- [ ] Estimated tax liability calculator
- [ ] Multi-year wash sale tracking across tax years
- [ ] RBAC: VIEW_TAX_REPORTS permission required

---

## Prerequisites Checklist

**Must verify before starting implementation:**

- [x] **T6.1 Auth/RBAC available:** Delivered in PR#76 (2025-12-12) - `@requires_auth` available
- [x] **T2.8 Model Registry available:** `libs/models/registry.py` exists with ModelRegistry class
- [x] **T2.1 Factor Builder available:** `libs/factors/factor_builder.py` and `factor_definitions.py` exist
- [x] **T6.2 Performance Dashboard available:** `apps/web_console/pages/performance.py` exists
- [x] **T6.5 Trade Journal available:** `apps/web_console/pages/journal.py` exists with export functionality
- [x] **Alpha Research Platform available:** `libs/alpha/research_platform.py` with BacktestResult
- [x] **Alpha Metrics available:** `libs/alpha/metrics.py` with IC calculation
- [ ] **Email delivery service:** Reuse `libs/alerts/delivery_service.py` from T7.5 (email channel)
- [ ] **PDF generation library:** Add weasyprint or reportlab to dependencies
- [ ] **Database tables:** Create migrations for report_schedules, report_archives, tax_lots

---

## Approach

### High-Level Plan

1. **C0: Prep & Validation** (0.5 day)
   - Verify prerequisites checklist
   - Confirm model registry API stability
   - Confirm alpha metrics adapter API
   - Create ADR-0030 outline for reporting architecture

2. **C1: T9.1 Alpha Signal Explorer** (3-4 days)
   - Create alpha explorer page with registry integration
   - Implement IC time-series chart component
   - Add decay curve visualization
   - Build signal correlation matrix

3. **C2: T9.2 Factor Exposure Heatmap** (2-3 days)
   - Create factor heatmap page with factor builder integration
   - Implement interactive heatmap component (Plotly)
   - Add time-series exposure evolution
   - Add drill-down to stock-level exposures

4. **C3: T9.3 Research Notebook Launcher** (2-3 days) - STRETCH
   - Design notebook server management architecture
   - Implement session lifecycle (start/stop/status)
   - Create template notebooks
   - Add security controls

5. **C4: T9.4 Scheduled Reports** (3-4 days)
   - Create report template configuration UI
   - Implement schedule management (Celery beat or APScheduler)
   - Build PDF/HTML generation pipeline
   - Integrate with email delivery service from T7.5

6. **C5: T9.5 Tax Lot Reporter - Core** (2-3 days)
   - Create tax lots database schema
   - Implement FIFO/LIFO/Specific ID cost basis algorithms
   - Build tax lot viewer UI
   - Add export functionality

7. **C6: T9.6 Tax Lot Reporter - Advanced** (2-3 days) - STRETCH
   - Implement wash sale detection algorithm
   - Add wash sale adjustment calculations
   - Create tax-loss harvesting recommendations
   - Add Form 8949 export

8. **C7: Integration & Documentation** (1 day)
   - Navigation integration and feature flags
   - ADR-0030 finalization
   - Concept documentation
   - End-to-end integration testing

### Logical Components

**Component 0: Prep & Validation**
- Verify model registry API for signal listing
- Verify alpha metrics API for IC calculation
- Verify factor builder API for exposure calculation
- Confirm email delivery service reusability
- Create ADR-0030 outline
- Request zen-mcp plan review
- Commit after approval

**Component 1: Alpha Signal Explorer (T9.1)**
- Create `AlphaExplorerService` for registry queries
- Implement IC time-series chart (Plotly)
- Implement decay curve chart
- Implement signal correlation matrix (heatmap)
- Add backtest quick-launch integration
- Add export functionality
- Request zen-mcp review
- Commit after approval

**Component 2: Factor Exposure Heatmap (T9.2)**
- Create `FactorExposureService` for factor calculations
- Implement interactive heatmap component (Plotly)
- Add time-series slider/animation
- Add stock-level drill-down
- Add export functionality
- Request zen-mcp review
- Commit after approval

**Component 3: Research Notebook Launcher (T9.3) - STRETCH**
- Design notebook server architecture (subprocess or Docker)
- Implement JupyterHub integration or standalone server
- Create template notebooks directory
- Add session management UI
- Add security controls (read-only data, network isolation)
- Request zen-mcp review
- Commit after approval

**Component 4: Scheduled Reports (T9.4)**
- Create report template configuration schema
- Implement schedule management with Celery beat
- Build PDF generator with WeasyPrint
- Build HTML generator with Jinja2 + Plotly static
- Integrate with email delivery service
- Implement report archive and retrieval
- Request zen-mcp review
- Commit after approval

**Component 5: Tax Lot Reporter - Core (T9.5)**
- Create tax_lots table schema
- Implement CostBasisCalculator (FIFO, LIFO, Specific ID)
- Build tax lot viewer UI with lot details
- Implement gains/losses report generation
- Add export functionality (TXF, CSV, PDF)
- Request zen-mcp review
- Commit after approval

**Component 6: Tax Lot Reporter - Advanced (T9.6) - STRETCH**
- Implement WashSaleDetector algorithm
- Add wash sale adjustment logic
- Implement tax-loss harvesting recommender
- Add Form 8949 export format
- Request zen-mcp review
- Commit after approval

**Component 7: Integration & Documentation**
- Add all pages to navigation sidebar
- Add feature flags for STRETCH items
- Create concept documentation
- Finalize ADR-0030
- End-to-end testing
- Request zen-mcp review
- Commit after approval

---

## Technical Details

### Files to Create

**T9.1 Alpha Signal Explorer:**
- `apps/web_console/pages/alpha_explorer.py` - Main alpha explorer page
- `apps/web_console/components/ic_chart.py` - IC time-series chart
- `apps/web_console/components/decay_curve.py` - Decay curve visualization
- `apps/web_console/components/signal_correlation_matrix.py` - Signal correlation heatmap
- `apps/web_console/services/alpha_explorer_service.py` - Registry query service
- `tests/apps/web_console/test_alpha_explorer.py`
- `tests/apps/web_console/services/test_alpha_explorer_service.py`
- `docs/CONCEPTS/alpha-signal-explorer.md`

**T9.2 Factor Exposure Heatmap:**
- `apps/web_console/pages/factor_heatmap.py` - Factor heatmap page
- `apps/web_console/components/heatmap_chart.py` - Interactive heatmap (Plotly)
- `apps/web_console/components/exposure_timeseries.py` - Time-series evolution
- `apps/web_console/components/stock_exposure_table.py` - Stock-level drill-down
- `apps/web_console/services/factor_exposure_service.py` - Factor calculation service
- `tests/apps/web_console/test_factor_heatmap.py`
- `tests/apps/web_console/services/test_factor_exposure_service.py`
- `docs/CONCEPTS/factor-exposure-visualization.md`

**T9.3 Research Notebook Launcher (STRETCH):**
- `apps/web_console/pages/notebooks.py` - Notebook launcher page
- `apps/web_console/services/notebook_service.py` - Notebook server management
- `notebooks/templates/alpha_research.ipynb` - Alpha research template
- `notebooks/templates/factor_analysis.ipynb` - Factor analysis template
- `notebooks/templates/backtest_review.ipynb` - Backtest review template
- `scripts/launch_notebook.py` - CLI for notebook server
- `tests/apps/web_console/test_notebook_launcher.py`
- `docs/CONCEPTS/notebook-launcher.md`

**T9.4 Scheduled Reports:**
- `apps/web_console/pages/reports.py` - Report configuration page
- `apps/web_console/components/report_template_editor.py` - Template configuration
- `apps/web_console/components/schedule_manager.py` - Schedule CRUD
- `apps/web_console/components/report_preview.py` - Preview component
- `libs/reporting/report_generator.py` - Core report generation
- `libs/reporting/pdf_generator.py` - PDF generation (WeasyPrint)
- `libs/reporting/html_generator.py` - HTML generation (Jinja2)
- `libs/reporting/templates/daily_summary.html` - Jinja2 template
- `libs/reporting/templates/weekly_performance.html` - Jinja2 template
- `libs/reporting/scheduler.py` - APScheduler integration (DB-persisted schedules)
- `db/migrations/0018_create_report_tables.sql` - Report schema
- `tests/libs/reporting/test_report_generator.py`
- `tests/libs/reporting/test_pdf_generator.py`
- `tests/libs/reporting/golden_reports/` - Golden PDF fixtures
- `tests/apps/web_console/test_scheduled_reports.py`
- `docs/CONCEPTS/reporting.md`
- `docs/ADRs/ADR-0030-reporting-architecture.md`

**T9.5 Tax Lot Reporter - Core:**
- `apps/web_console/pages/tax_lots.py` - Tax lot viewer page
- `apps/web_console/components/lot_table.py` - Position lots table
- `apps/web_console/components/gains_report.py` - Gains/losses report
- `apps/web_console/components/tax_export.py` - Export controls
- `libs/tax/cost_basis.py` - Cost basis calculator
- `libs/tax/models.py` - Pydantic models for tax lots
- `libs/tax/export.py` - Export formatters (TXF, CSV, PDF)
- `db/migrations/0019_create_tax_lots.sql` - Tax lots schema
- `tests/libs/tax/test_cost_basis.py`
- `tests/libs/tax/test_export.py`
- `tests/apps/web_console/test_tax_lots_ui.py`
- `docs/CONCEPTS/tax-lot-accounting.md`

**T9.6 Tax Lot Reporter - Advanced (STRETCH):**
- `libs/tax/wash_sale_detector.py` - Wash sale detection
- `libs/tax/tax_loss_harvesting.py` - Harvesting recommendations
- `libs/tax/form_8949.py` - IRS form export
- `tests/libs/tax/test_wash_sale.py`
- `tests/libs/tax/test_tax_loss_harvesting.py`
- `docs/ADRs/ADR-0031-tax-lot-tracking.md`

### Files to Modify

- `apps/web_console/app.py` - Add new pages to navigation
- `apps/web_console/config.py` - Add feature flags
- `libs/web_console_auth/permissions.py` - Add new permissions
- `pyproject.toml` - Add dependencies (weasyprint, jinja2, etc.)

### Database Changes

**Report Tables (T9.4):**
```sql
-- Enable pgcrypto if not already enabled
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS report_templates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id UUID NOT NULL,  -- Multi-tenant scoping
    name VARCHAR(255) NOT NULL,
    description TEXT,
    template_type VARCHAR(50) NOT NULL,  -- daily_summary, weekly_performance, monthly_pnl
    config JSONB NOT NULL DEFAULT '{}',  -- metrics, date_range, strategies, format
    created_by UUID NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(account_id, name)  -- Template names unique per account
);

CREATE TABLE IF NOT EXISTS report_schedules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id UUID NOT NULL,  -- Multi-tenant scoping
    template_id UUID NOT NULL REFERENCES report_templates(id),
    schedule_type VARCHAR(20) NOT NULL,  -- daily, weekly, monthly
    schedule_config JSONB NOT NULL DEFAULT '{}',  -- hour, day_of_week, day_of_month
    recipients JSONB NOT NULL DEFAULT '[]',  -- email addresses
    enabled BOOLEAN DEFAULT true,
    last_run_at TIMESTAMPTZ,
    next_run_at TIMESTAMPTZ,
    created_by UUID NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS report_archives (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id UUID NOT NULL,  -- Multi-tenant scoping
    schedule_id UUID REFERENCES report_schedules(id),
    template_id UUID NOT NULL REFERENCES report_templates(id),
    idempotency_key VARCHAR(100) NOT NULL,  -- schedule_id + generated_at to prevent duplicates
    generated_at TIMESTAMPTZ NOT NULL,
    file_path VARCHAR(500) NOT NULL,  -- S3 or local path
    file_format VARCHAR(10) NOT NULL,  -- pdf, html
    file_size_bytes INTEGER,
    delivery_status VARCHAR(20) DEFAULT 'pending',  -- pending, delivered, failed
    delivered_at TIMESTAMPTZ,
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(idempotency_key)  -- Prevent duplicate archive generation
);

-- Schedule run tracking for idempotency
CREATE TABLE IF NOT EXISTS report_schedule_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    schedule_id UUID NOT NULL REFERENCES report_schedules(id),
    run_key VARCHAR(100) NOT NULL,  -- schedule_id + date for idempotency
    status VARCHAR(20) NOT NULL DEFAULT 'pending',  -- pending, running, completed, failed
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    archive_id UUID REFERENCES report_archives(id),
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(run_key)  -- Prevent duplicate runs
);

CREATE INDEX idx_report_templates_account ON report_templates(account_id);
CREATE INDEX idx_report_schedules_account ON report_schedules(account_id);
CREATE INDEX idx_report_schedules_next_run ON report_schedules(next_run_at) WHERE enabled = true;
CREATE INDEX idx_report_archives_account ON report_archives(account_id);
CREATE INDEX idx_report_archives_generated ON report_archives(generated_at);
-- 90-day retention policy enforced via scheduled cleanup job
```

**Tax Lots Tables (T9.5):**
```sql
CREATE TABLE IF NOT EXISTS tax_lots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id UUID NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    quantity DECIMAL(18, 8) NOT NULL,
    cost_per_share DECIMAL(18, 8) NOT NULL,
    total_cost DECIMAL(18, 4) NOT NULL,
    acquired_at TIMESTAMPTZ NOT NULL,
    acquisition_type VARCHAR(20) NOT NULL,  -- buy, transfer_in, dividend_reinvest
    source_order_id UUID,  -- link to orders table
    remaining_quantity DECIMAL(18, 8) NOT NULL,  -- decreases as shares are sold
    closed_at TIMESTAMPTZ,  -- set when remaining_quantity = 0
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS tax_lot_dispositions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lot_id UUID NOT NULL REFERENCES tax_lots(id),
    quantity DECIMAL(18, 8) NOT NULL,
    proceeds_per_share DECIMAL(18, 8) NOT NULL,
    total_proceeds DECIMAL(18, 4) NOT NULL,
    disposed_at TIMESTAMPTZ NOT NULL,
    disposition_type VARCHAR(20) NOT NULL,  -- sell, transfer_out
    destination_order_id UUID,  -- link to orders table
    realized_gain_loss DECIMAL(18, 4) NOT NULL,
    holding_period VARCHAR(10) NOT NULL,  -- short_term, long_term
    wash_sale_disallowed DECIMAL(18, 4) DEFAULT 0,  -- wash sale adjustment
    wash_sale_adjustment_lot_id UUID,  -- lot that received basis adjustment
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS tax_settings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id UUID NOT NULL UNIQUE,
    cost_basis_method VARCHAR(20) NOT NULL DEFAULT 'fifo',  -- fifo, lifo, specific_id
    wash_sale_tracking BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_tax_lots_account_symbol ON tax_lots(account_id, symbol);
CREATE INDEX idx_tax_lots_acquired ON tax_lots(acquired_at);
CREATE INDEX idx_tax_lot_dispositions_disposed ON tax_lot_dispositions(disposed_at);
```

### New Permissions

```python
# libs/web_console_auth/permissions.py additions
class Permission(str, Enum):
    # ... existing permissions ...

    # T9.1 Alpha Explorer
    VIEW_ALPHA_SIGNALS = "view_alpha_signals"
    LAUNCH_BACKTEST = "launch_backtest"  # For backtest quick-launch

    # T9.2 Factor Heatmap
    VIEW_FACTOR_ANALYTICS = "view_factor_analytics"

    # T9.3 Notebook Launcher
    LAUNCH_NOTEBOOKS = "launch_notebooks"

    # T9.4 Reports
    VIEW_REPORTS = "view_reports"
    MANAGE_REPORTS = "manage_reports"

    # T9.5/T9.6 Tax Lots
    VIEW_TAX_REPORTS = "view_tax_reports"
    MANAGE_TAX_SETTINGS = "manage_tax_settings"  # For changing cost basis method


# CRITICAL: Must also update ROLE_PERMISSIONS mapping
ROLE_PERMISSIONS: dict[Role, set[Permission]] = {
    Role.VIEWER: {
        # ... existing ...
        Permission.VIEW_REPORTS,
        Permission.VIEW_TAX_REPORTS,
    },
    Role.OPERATOR: {
        # ... existing ...
        Permission.VIEW_REPORTS,
        Permission.VIEW_TAX_REPORTS,
        Permission.VIEW_ALPHA_SIGNALS,
        Permission.VIEW_FACTOR_ANALYTICS,
    },
    Role.ADMIN: set(Permission),  # All permissions
}

# Add new "RESEARCHER" role or assign to OPERATOR:
# For T9 features, OPERATOR gets view access, ADMIN gets full access
# Consider adding RESEARCHER role for LAUNCH_NOTEBOOKS, MANAGE_REPORTS
```

**RBAC Role Matrix:**
| Page | Action | Viewer | Operator | Researcher | Admin |
|------|--------|--------|----------|------------|-------|
| Alpha Explorer | View signals | - | - | VIEW_ALPHA_SIGNALS | VIEW_ALPHA_SIGNALS |
| Alpha Explorer | Launch backtest | - | - | LAUNCH_BACKTEST | LAUNCH_BACKTEST |
| Factor Heatmap | View exposures | - | - | VIEW_FACTOR_ANALYTICS | VIEW_FACTOR_ANALYTICS |
| Factor Heatmap | Export data | - | - | EXPORT_DATA | EXPORT_DATA |
| Notebooks | Launch notebook | - | - | LAUNCH_NOTEBOOKS | LAUNCH_NOTEBOOKS |
| Reports | View archive | VIEW_REPORTS | VIEW_REPORTS | VIEW_REPORTS | VIEW_REPORTS |
| Reports | Create/edit | - | - | MANAGE_REPORTS | MANAGE_REPORTS |
| Reports | Delete | - | - | - | MANAGE_REPORTS |
| Tax Lots | View lots/reports | VIEW_TAX_REPORTS | VIEW_TAX_REPORTS | VIEW_TAX_REPORTS | VIEW_TAX_REPORTS |
| Tax Lots | Change method | - | - | - | MANAGE_TAX_SETTINGS |

---

## Dependencies

**Blockers (must verify before starting):**
- [x] T6.1 (Auth/RBAC): **DELIVERED** in PR#76 (2025-12-12)
- [x] T2.8 (Model Registry): Exists at `libs/models/registry.py`
- [x] T2.1 (Factor Builder): Exists at `libs/factors/factor_builder.py`
- [x] T6.2 (Performance Dashboard): Exists at `apps/web_console/pages/performance.py`
- [x] T6.5 (Trade Journal): Exists at `apps/web_console/pages/journal.py`
- [x] Alpha Research Platform: Exists at `libs/alpha/research_platform.py`

**Infrastructure Requirements:**
- Redis: For Celery beat schedule storage
- PostgreSQL: For report and tax lot tables
- File storage: For generated report PDFs (local or S3)
- Email delivery: Reuse `libs/alerts/delivery_service.py` from T7.5

**Blocks (other tasks waiting on this):**
- None identified - Track 9 is the final web console track

---

## Risks & Mitigations

| Risk | Impact | Probability | Mitigation |
|------|--------|-------------|------------|
| Model Registry API instability | Med | Low | Review API before starting, add contract tests |
| Factor calculation performance | Med | Med | Cache factor exposures, use lazy loading |
| PDF generation quality | Med | Med | Use WeasyPrint with extensive testing, golden PDF fixtures |
| Notebook security isolation | High | Med | Run in Docker container with read-only mounts |
| Wash sale calculation complexity | High | Med | Implement comprehensive test suite, validate against IRS examples |
| Report file storage at scale | Med | Low | Implement retention policy, use S3 for production |
| Email delivery failures | Med | Med | Reuse T7.5 delivery service with retry logic |

---

## Testing Strategy

### Test Coverage Needed

**Unit Tests:**
- Cost basis calculation (FIFO, LIFO, Specific ID)
- Wash sale detection algorithm
- Report template rendering
- IC calculation adapter
- Factor exposure calculation

**Integration Tests:**
- Model registry signal listing
- Factor builder exposure queries
- Report generation pipeline (template → PDF)
- Email delivery with mock SMTP
- Tax lot CRUD operations

**E2E Tests:**
- Alpha explorer page load and signal selection
- Factor heatmap rendering with drill-down
- Report schedule creation and execution
- Tax lot viewer with export

**Golden Tests:**
- PDF output validation (visual regression)
- Tax calculation against known examples
- IC calculation against reference implementation

**Performance Tests:**
- Heatmap rendering with 100+ factors
- Report generation for large date ranges
- Tax lot queries for high-volume accounts

---

## Non-Functional Requirements

### Observability

**Prometheus Metrics:**
- `alpha_explorer_queries_total` - Counter of registry queries
- `factor_heatmap_render_seconds` - Histogram of render time
- `report_generation_seconds` - Histogram of PDF generation time
- `report_delivery_total{status}` - Counter of delivery attempts
- `tax_calculation_seconds` - Histogram of tax calculation time

**Structured Logging:**
- All operations include `request_id` for tracing
- Report generation logs: `template_id`, `schedule_id`, `duration_ms`
- Tax calculations log: `account_id`, `method`, `lots_processed`

---

## Documentation Requirements

### Must Create
- [ ] `docs/CONCEPTS/alpha-signal-explorer.md`
- [ ] `docs/CONCEPTS/factor-exposure-visualization.md`
- [ ] `docs/CONCEPTS/notebook-launcher.md` (if C3 implemented)
- [ ] `docs/CONCEPTS/reporting.md`
- [ ] `docs/CONCEPTS/tax-lot-accounting.md`
- [ ] `docs/ADRs/ADR-0030-reporting-architecture.md`
- [ ] `docs/ADRs/ADR-0031-tax-lot-tracking.md` (if C6 implemented)

### Must Update
- [ ] `docs/GETTING_STARTED/PROJECT_STATUS.md` when complete
- [ ] `docs/TASKS/P4_PLANNING.md` - Mark Track 9 complete

---

## Related

**ADRs:**
- ADR-0030: Reporting Architecture (to create)
  - Scope: PDF generation, scheduling, delivery, storage
- ADR-0031: Tax Lot Tracking (to create if C6 implemented)
  - Scope: Cost basis methods, wash sale handling

**Documentation:**
- [P4_PLANNING.md](./P4_PLANNING.md) - Track 9 specification
- [P4T5_TASK.md](./P4T5_TASK.md) - Reference for component structure

**Tasks:**
- Depends on: T6.1 (Auth), T2.8 (Model Registry), T2.1 (Factor Builder), T6.2 (Performance), T6.5 (Journal)
- Related: T7.5 (Alert Delivery) - reuse email delivery service

---

## Implementation Tracking

**Branch:** `feature/P4T7-web-console-research-reporting`
**Started:** 2025-12-24

### Component Breakdown

| # | Component | Status | Effort | Dependencies |
|---|-----------|--------|--------|--------------|
| C0 | Prep & Validation | PLANNING | 0.5d | - |
| C1 | T9.1 Alpha Signal Explorer | PLANNING | 3-4d | C0 |
| C2 | T9.2 Factor Exposure Heatmap | PLANNING | 2-3d | C0 |
| C3 | T9.3 Research Notebook Launcher | PLANNING (STRETCH) | 2-3d | C0 |
| C4 | T9.4 Scheduled Reports | PLANNING | 3-4d | C0 |
| C5 | T9.5 Tax Lot Reporter - Core | PLANNING | 2-3d | C0 |
| C6 | T9.6 Tax Lot Reporter - Advanced | PLANNING (STRETCH) | 2-3d | C5 |
| C7 | Integration & Documentation | PLANNING | 1d | C1-C6 |

**Total Estimated Effort:** 14-19 days (excluding STRETCH items: 11-15 days)

### STRETCH Item Policy

T9.3 (Notebook Launcher) and T9.6 (Tax Lot Advanced) are marked STRETCH:
- Implement if schedule permits after core items complete
- Can be deferred to P4b if behind at Week 18 checkpoint
- Core functionality (T9.1, T9.2, T9.4, T9.5) must complete first

---

## Notes

- Track 9 is research-focused, requiring integration with analytics libraries (alpha, factors)
- Scheduled reports reuse T7.5 alert delivery for email distribution
- Tax lot tracking is compliance-critical; must validate against IRS rules
- Notebook launcher requires security review for isolation
- STRETCH items can be deferred to P4b if time-constrained

---

## Task Creation Review Checklist

See [./AI/Workflows/02-planning.md](../AI/Workflows/02-planning.md) for workflow details.

**Review validates:**
- [x] Objective is clear and measurable (with SLAs)
- [x] Success criteria are testable
- [x] Functional requirements are comprehensive
- [x] Research tool requirements specified (alpha, factors)
- [x] Non-functional requirements documented (performance, observability)
- [x] Component breakdown follows 6-step pattern
- [x] Time estimates are reasonable (14-19 days)
- [x] Dependencies and blockers identified
- [x] STRETCH items clearly marked
- [x] ADR requirement clear for architectural changes
- [x] Test strategy comprehensive
- [x] Security requirements specified (RBAC, notebook isolation)

---

## Planner Review Status

**Review Date:** 2025-12-24
**Reviewers:** Gemini Planner, Codex Planner
**Status:** ✅ APPROVED with recommendations

### Gemini Planner Assessment
- **Architecture:** Approved - component boundaries logical, follows established patterns
- **Database Design:** Approved - schemas comprehensive with proper indexes
- **Security:** Approved - RBAC permissions defined and integrated
- **Feasibility:** Realistic estimates, STRETCH items correctly identified

**Recommendations (addressed):**
1. ✅ Tax Lot Backfill utility - Added to C5 plan
2. ✅ WeasyPrint system dependencies check - Added to C0 prep
3. ✅ AlphaMetricsAdapter verification - Added to C0 validation

### Codex Planner Assessment
- **Architecture:** Approved with notes on data source integration
- **Database Design:** Approved after adding multi-tenant scoping
- **Security:** Approved after adding MANAGE_TAX_SETTINGS permission
- **Feasibility:** Approved, C4/C5 may need additional infra work

**Recommendations (addressed):**
1. ✅ Add account_id to report tables - Done
2. ✅ Add MANAGE_TAX_SETTINGS permission - Done
3. ✅ Add idempotency for report schedules - Added report_schedule_runs table
4. ✅ Add unique constraints for templates - Done
5. ✅ Define integration gap report in C0 - Added

**Identified Risks:**
- API readiness mismatch (T2.8/T2.5) - Mitigated by C0 compatibility check
- Report scheduler duplication - Mitigated by idempotency table
- WeasyPrint dependency failures - Mitigated by C0 dependency check
- Tax lot correctness edge cases - Mitigated by comprehensive test fixtures
