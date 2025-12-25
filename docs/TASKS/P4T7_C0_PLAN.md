# P4T7 C0: Prep & Validation - Component Plan

**Component:** C0 - Prep & Validation
**Parent Task:** P4T7 Web Console Research & Reporting
**Status:** PLANNING
**Estimated Effort:** 0.5 day

---

## Objective

Verify all prerequisites for Track 9 implementation and confirm API stability for model registry, alpha metrics, and factor builder integrations.

---

## Prerequisites Verification

### 1. Model Registry API (VERIFY)

**Location:** `libs/models/registry.py`, `libs/models/types.py`

**CRITICAL: Actual ModelType enum values (from codebase verification):**
```python
class ModelType(str, Enum):
    risk_model = "risk_model"
    alpha_weights = "alpha_weights"  # Use this for alpha signals
    factor_definitions = "factor_definitions"
    feature_transforms = "feature_transforms"

class ModelStatus(str, Enum):
    staged = "staged"
    production = "production"
    archived = "archived"
    failed = "failed"
```

**NOTE:** Plans must use `ModelType.alpha_weights` (NOT `ALPHA_SIGNAL`). The Alpha Explorer will work with `alpha_weights` model type which contains IC metrics in the `metrics` dict.

Key methods needed for Alpha Explorer:
```python
class ModelRegistry:
    def list_models(
        self,
        model_type: ModelType | None = None,
        status: ModelStatus | None = None,
    ) -> list[ModelMetadata]:
        """List all registered models with optional filtering."""
        ...

    def get_model_metadata(self, model_type: str, version: str) -> ModelMetadata | None:
        """Get metadata for specific model version (returns None if not found)."""
        ...
```

**Verification steps:**
- [ ] Confirm `list_models(ModelType.alpha_weights)` returns alpha models
- [ ] Confirm `ModelMetadata.metrics` dict contains IC metrics (set during registration)
- [ ] Confirm backtest linkage via `BacktestResultStorage` (separate from registry)
- [ ] Write contract test for API stability
- [ ] Document how to link ModelMetadata to BacktestResult (via run_id or experiment_id)

### 2. Alpha Metrics API (VERIFY)

**Location:** `libs/alpha/metrics.py`

Key classes/methods:
```python
class AlphaMetricsAdapter:
    def compute_ic(self, signals: pl.DataFrame, returns: pl.DataFrame) -> ICResult:
        """Compute IC for signals vs forward returns."""
        ...

    def compute_icir(self, daily_ic: pl.DataFrame) -> ICIRResult:
        """Compute ICIR from daily IC series."""
        ...

    def compute_decay_curve(
        self, signals: pl.DataFrame, returns_by_horizon: dict[int, pl.DataFrame]
    ) -> DecayCurveResult:
        """Compute IC at multiple horizons."""
        ...
```

**Verification steps:**
- [ ] Confirm `ICResult` structure (pearson_ic, rank_ic)
- [ ] Confirm `DecayCurveResult` structure (decay_curve DataFrame, half_life)
- [ ] Confirm integration with PITBacktester results

### 3. Factor Builder API (VERIFY)

**Location:** `libs/factors/factor_builder.py` and `factor_definitions.py`

**CRITICAL: Actual FactorBuilder API (from codebase verification):**
```python
class FactorBuilder:
    def compute_factor(
        self,
        factor_name: str,
        as_of_date: date,
        universe: list[int] | None = None,  # PERMNOs
        snapshot_date: date | None = None,
    ) -> FactorResult:
        """Compute single factor for given date."""
        ...

    def compute_all_factors(
        self,
        as_of_date: date,
        universe: list[int] | None = None,
        snapshot_date: date | None = None,
    ) -> FactorResult:
        """Compute all registered factors for given date."""
        ...

    def list_factors(self) -> list[str]:
        """Return list of registered factor names."""
        ...
```

**NOTE:** The API computes factors for a UNIVERSE of stocks (by PERMNO), NOT for portfolio positions directly. The Factor Heatmap service (C2) must:
1. Get portfolio holdings (PERMNOs + weights) from a holdings source
2. Pass PERMNOs as universe to `compute_all_factors()`
3. Weight-average the per-stock exposures to get portfolio-level exposure

**FactorResult structure:**
```python
@dataclass
class FactorResult:
    exposures: pl.DataFrame  # Columns: permno, date, factor_name, raw_value, zscore, percentile
    as_of_date: date
    dataset_version_ids: dict[str, str]
    computation_timestamp: datetime
    reproducibility_hash: str
```

**Verification steps:**
- [ ] Confirm factor list from `list_factors()`: momentum_12_1, value, quality, size, low_vol
- [ ] Confirm `compute_all_factors()` returns per-stock z-scores
- [ ] Define portfolio holdings data source for C2 (reconciler positions or journal)
- [ ] Implement weight-averaging logic in FactorExposureService

### 4. Email Delivery Service (VERIFY)

**Location:** `libs/alerts/delivery_service.py` (from T7.5)

**CRITICAL: Current API does NOT support attachments.**

The existing `DeliveryExecutor` and `EmailChannel` handle alert notifications but lack file attachment support. For C4 (Scheduled Reports), we need one of:

**Option A: Extend EmailChannel (Recommended)**
- Add `attachments: list[Path]` parameter to `EmailChannel.deliver()`
- Modify SMTP logic to attach files as MIME multipart
- Minimal change, reuses existing infrastructure

**Option B: Direct SMTP for Reports**
- Create `libs/reporting/email_sender.py` with direct smtplib usage
- Bypasses alert queue, simpler for reports
- Less reuse but more isolated

**Verification steps:**
- [ ] Confirm EmailChannel uses smtplib (can be extended for attachments)
- [ ] Review alert queue model - does it support large payloads?
- [ ] Decision: Extend EmailChannel vs. create separate sender
- [ ] Plan attachment support implementation in C4

### 5. Database Dependencies (VERIFY)

**Required extensions:**
```sql
CREATE EXTENSION IF NOT EXISTS pgcrypto;
```

**CRITICAL: Migration numbering (from codebase verification):**
Current migrations go up to `0017_alert_acknowledgments.sql`.
New migrations must use:
- `0018_create_report_tables.sql` (NOT 0012)
- `0019_create_tax_lots.sql` (NOT 0013)

**Database access pattern:**
Web console uses `psycopg` with `AsyncConnectionAdapter` (NOT asyncpg).
See `apps/web_console/utils/db_pool.py`:
```python
# Correct pattern for Streamlit pages:
from apps.web_console.utils.db_pool import get_db_pool
from apps.web_console.utils.run_async import run_async

db_adapter = get_db_pool()
async with db_adapter.connection() as conn:
    result = await conn.execute(query)
```

**Verification steps:**
- [ ] Confirm pgcrypto enabled (should exist from T7.5)
- [ ] Create migrations 0018 (reports) and 0019 (tax_lots)
- [ ] Use psycopg AsyncConnectionAdapter pattern in all services

### 6. WeasyPrint & Kaleido Dependencies (VERIFY)

**Required for PDF generation (C4):**

WeasyPrint requires OS-level libraries:
- `libpango` - Text layout
- `libcairo` - 2D graphics
- `libgdk-pixbuf` - Image loading
- `libffi` - Foreign function interface

**Kaleido required for Plotly static export:**
- Used by `fig.to_image()` for embedding charts in HTML/PDF reports
- Add to pyproject.toml: `kaleido>=0.2.1`

**Verification steps:**
```bash
# Check macOS (Homebrew)
brew list | grep -E "pango|cairo|gdk-pixbuf"

# Check Linux
dpkg -l | grep -E "pango|cairo|gdk-pixbuf"

# Test WeasyPrint import
python3 -c "from weasyprint import HTML; print('OK')"

# Test Kaleido/Plotly export
python3 -c "import plotly.graph_objects as go; fig = go.Figure(); fig.to_image(format='png')"
```

**Dependencies to add to pyproject.toml:**
```toml
weasyprint = ">=60.0"
kaleido = ">=0.2.1"
jinja2 = ">=3.1.0"
```

- [ ] Confirm system dependencies installed (or document install steps)
- [ ] Add weasyprint, kaleido, jinja2 to pyproject.toml
- [ ] Document Docker image updates needed for CI/production
- [ ] Create minimal PDF smoke test
- [ ] Create minimal Plotly export smoke test

### 7. Scheduler Architecture Decision (REQUIRED)

**CRITICAL: Celery is NOT in current dependencies.**

Current repo uses APScheduler in `apps/execution_gateway/slice_scheduler.py`.

**Options for C4 (Scheduled Reports):**

**Option A: APScheduler in-process (Recommended for MVP)**
- Reuse existing pattern from execution_gateway
- Runs within Streamlit or separate worker process
- Simpler, no new infrastructure
- Risk: Process restart loses schedule state (must persist to DB)

**Option B: Celery + Redis (Full solution)**
- Add celery, celery-beat dependencies
- Requires worker process deployment
- Better for production scale
- More infrastructure complexity

**Decision for P4T7:** Use APScheduler with DB-persisted schedules.
- Schedule config stored in `report_schedules` table
- Worker process polls DB for due schedules
- Simpler deployment, adequate for current scale

### 8. Integration Gap Analysis (PRODUCE)

**Required data sources for C1/C2:**

| Data Source | Purpose | Location | Status |
|-------------|---------|----------|--------|
| Holdings/Positions | Factor exposure calculation | Positions table via reconciler | Verify schema |
| Benchmark Constituents | Portfolio vs benchmark | Define benchmark data source | TBD |
| Backtest Results | IC time-series, decay curves | `libs/backtest/result_storage.py` | Via BacktestResultStorage |
| Factor Returns | Factor exposure analysis | Computed via FactorBuilder | Available |
| Alpha Model Metrics | IC/ICIR display | ModelMetadata.metrics dict | Set during registration |

**Backtest-Registry Linkage:**
- `ModelMetadata.run_id` or `experiment_id` can link to BacktestResultStorage
- C1 must query BacktestResultStorage using these IDs to get daily IC series

**Gap analysis output:**
- [ ] Document authoritative source for each data type
- [ ] Implement BacktestResultStorage query for IC series
- [ ] Define benchmark holdings source (S&P 500, custom)
- [ ] Define caching strategy for computed exposures
- [ ] Confirm portfolio holdings query from positions table

---

## Implementation Plan

### Step 1: Verify Model Registry API

Read and analyze:
- `libs/models/registry.py` - Core registry class
- `libs/models/types.py` - ModelMetadata definition
- `tests/libs/models/test_registry.py` - Existing tests

Document any gaps for Alpha Explorer needs.

### Step 2: Verify Alpha Metrics API

Read and analyze:
- `libs/alpha/metrics.py` - Metrics adapter
- `libs/alpha/research_platform.py` - BacktestResult integration
- `tests/libs/alpha/test_metrics.py` - Existing tests

Document API for IC charts and decay curves.

### Step 3: Verify Factor Builder API

Read and analyze:
- `libs/factors/factor_builder.py` - Factor computation
- `libs/factors/factor_definitions.py` - Factor definitions
- `libs/factors/factor_analytics.py` - Analytics helpers

Document exposure calculation API for heatmap.

### Step 4: Verify Email Delivery

Read and analyze:
- `libs/alerts/delivery_service.py` - Delivery service
- `libs/alerts/channels/email.py` - Email channel

Confirm attachment support or plan enhancement.

### Step 5: Create ADR-0030 Outline

Create outline for Reporting Architecture ADR:
```markdown
# ADR-0030: Reporting Architecture

## Status
Proposed

## Context
Track 9 introduces scheduled report generation and distribution...

## Decision
We will implement...

## Consequences
...
```

---

## Deliverables

1. **API Documentation:** Summary of registry, metrics, factor APIs
2. **Gap Analysis:** Any missing capabilities requiring enhancement
3. **Integration Gap Report:** Data source mapping and adapter requirements
4. **ADR-0030 Outline:** `docs/ADRs/ADR-0030-reporting-architecture.md`
5. **Migration Plan:** Outline for 0012 and 0013 migrations
6. **Dependency Verification:** Confirmed all prerequisites met
7. **WeasyPrint Smoke Test:** Minimal PDF generation test

---

## Verification Checklist

- [ ] Model registry API verified and documented
- [ ] Alpha metrics API verified and documented
- [ ] Factor builder API verified and documented
- [ ] Email delivery attachment support confirmed
- [ ] Database extension availability confirmed
- [ ] WeasyPrint system dependencies verified
- [ ] Integration gap report produced (data sources mapped)
- [ ] ADR-0030 outline created
- [ ] All gaps documented with mitigation plans
