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

**Location:** `libs/models/registry.py`

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

    def get_model_metadata(self, model_type: str, version: str) -> ModelMetadata:
        """Get metadata for specific model version."""
        ...
```

**Verification steps:**
- [ ] Confirm `list_models()` returns alpha signal metadata
- [ ] Confirm `ModelMetadata` includes IC metrics (mean_ic, icir)
- [ ] Confirm backtest linkage via `backtest_id` field
- [ ] Write contract test for API stability

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

Key classes:
```python
class FactorBuilder:
    def compute_factor_exposures(
        self, portfolio: pl.DataFrame, factors: list[str], as_of_date: date
    ) -> pl.DataFrame:
        """Compute factor exposures for portfolio positions."""
        ...

class FactorDefinition:
    name: str
    category: str  # value, quality, momentum, size, low_vol
    description: str
```

**Verification steps:**
- [ ] Confirm factor list from `factor_definitions.py`
- [ ] Confirm exposure computation returns per-stock and aggregate exposures
- [ ] Confirm date range support for time-series

### 4. Email Delivery Service (VERIFY)

**Location:** `libs/alerts/delivery_service.py` (from T7.5)

Reuse for report distribution:
```python
class AlertDeliveryService:
    async def send_email(
        self, to: str, subject: str, body: str, attachments: list[Path] | None = None
    ) -> bool:
        """Send email with optional attachments."""
        ...
```

**Verification steps:**
- [ ] Confirm `send_email()` supports attachments (for PDF reports)
- [ ] Confirm retry logic and delivery tracking
- [ ] Confirm can be used outside alert context

### 5. Database Dependencies (VERIFY)

**Required extensions:**
```sql
CREATE EXTENSION IF NOT EXISTS pgcrypto;
```

**Verification steps:**
- [ ] Confirm pgcrypto enabled (should exist from T7.5)
- [ ] Plan migrations 0012 (reports) and 0013 (tax_lots)

### 6. WeasyPrint System Dependencies (VERIFY)

**Required for PDF generation (C4):**

WeasyPrint requires OS-level libraries:
- `libpango` - Text layout
- `libcairo` - 2D graphics
- `libgdk-pixbuf` - Image loading
- `libffi` - Foreign function interface

**Verification steps:**
```bash
# Check macOS (Homebrew)
brew list | grep -E "pango|cairo|gdk-pixbuf"

# Check Linux
dpkg -l | grep -E "pango|cairo|gdk-pixbuf"

# Test WeasyPrint import
python3 -c "from weasyprint import HTML; print('OK')"
```

- [ ] Confirm system dependencies installed (or document install steps)
- [ ] Add weasyprint to pyproject.toml if not present
- [ ] Document Docker image updates needed for CI/production
- [ ] Create minimal PDF smoke test

### 7. Integration Gap Analysis (PRODUCE)

**Required data sources for C1/C2:**

| Data Source | Purpose | Location | Status |
|-------------|---------|----------|--------|
| Holdings/Positions | Factor exposure calculation | `apps/reconciler/` | TBD |
| Benchmark Constituents | Portfolio vs benchmark | `data/benchmarks/` | TBD |
| Backtest Results | IC time-series, decay curves | `artifacts/backtests/` | TBD |
| Factor Returns | Factor exposure analysis | `data/factors/` | TBD |

**Gap analysis output:**
- [ ] Document authoritative source for each data type
- [ ] Identify any missing integrations requiring adapters
- [ ] Define caching strategy for computed exposures
- [ ] Confirm backtest result storage path

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
