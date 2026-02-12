---
id: P6T12
title: "Professional Trading Terminal - Backtest Tools"
phase: P6
task: T12
priority: P1
owner: "@development-team"
state: PLANNING
created: 2026-01-13
dependencies: [P5, P6T9]
related_adrs: [ADR-0031-nicegui-migration]
related_docs: [P6_PLANNING.md]
features: [T12.1-T12.4]
---

# P6T12: Backtest Tools

**Phase:** P6 (Professional Trading Terminal Upgrades)
**Status:** PLANNING
**Priority:** P1 (Research Platform)
**Owner:** @development-team
**Created:** 2026-01-13
**Track:** Track 12 of 18
**Dependency:** P5 complete, P6T9 (Cost Model for live overlay comparison)

---

## Objective

Build advanced backtest tools: config editor, comparison mode, live vs backtest overlay, and data health monitoring.

**Success looks like:**
- JSON config editor for power users
- Side-by-side backtest comparison with equity curve overlay
- Live vs backtest overlay for alpha decay detection
- Data health monitoring widget on dashboard

---

## Implementation Order

```
T12.1 Config Editor        (no dependency, standalone UI component)
T12.2 Backtest Comparison  (no dependency, extends existing results tab)
T12.4 Data Health Widget   (no dependency, standalone widget + backend monitor)
T12.3 Live vs Backtest     (depends on P6T9 cost model, highest complexity)
```

T12.1, T12.2, and T12.4 are independent and can be implemented in any order.
T12.3 is implemented last as it has the highest complexity and depends on P6T9.

**Prerequisite (before T12.2 or T12.3):** Fix `StrategyScopedDataAccess.verify_job_ownership()` to use `created_by` instead of non-existent `strategy_id` column (see T12.3 section for details). This is required before any code routes through `BacktestAnalyticsService`. Until this fix lands, T12.2 may use the existing `_verify_job_ownership()` in `backtest.py` which already correctly queries `created_by`.

---

## Tasks (4 total)

### T12.1: Config as Code (JSON Editor) - MEDIUM PRIORITY

**Goal:** Advanced mode toggle in backtest form that lets power users submit backtest configs as JSON.

**Approach:**
- Add an "Advanced Mode" `ui.switch` toggle in the existing `_render_new_backtest_form()` in `apps/web_console_ng/pages/backtest.py`.
- When toggled ON, hide the form fields and show a `ui.codemirror` (NiceGUI 2.12.1 has `ui.codemirror` with JSON language support).
- Pre-populate the editor with the current form state serialized as JSON.
- Validate the JSON via `BacktestJobConfig.from_dict()` (BacktestJobConfig is a dataclass with `to_dict()`/`from_dict()` helpers, not Pydantic). `from_dict()` accesses required fields directly (`data["alpha_name"]`, `data["start_date"]`, `data["end_date"]`) which raises `KeyError` if missing, and optional fields via `.get()` with defaults (e.g., `data.get("weight_method", "zscore")`). Unknown top-level keys in the input dict are naturally ignored - they don't cause errors but also don't get persisted. However, `extra_params` is preserved as-is (the entire dict is passed through), so unknown keys inside `extra_params` are retained. Catch `ValueError`/`TypeError`/`KeyError` and display errors inline.
- On submit, parse JSON back to `BacktestJobConfig` via `from_dict()` and use the same `_get_job_queue().enqueue()` path. **Priority selector remains visible** in Advanced Mode (it is separate from `BacktestJobConfig` and required by `queue.enqueue()`). Only the config form fields (alpha_name, dates, weight_method, provider) are replaced by the JSON editor.
- Add a "Copy Config" button that copies the current JSON to clipboard via `ui.run_javascript`.
- **Single source-of-truth:** When toggling to Advanced Mode, serialize current form state to JSON and populate editor (form -> JSON). When toggling back to Form Mode, attempt to parse JSON and populate form fields (JSON -> form). The form represents: top-level fields (`alpha_name`, `start_date`, `end_date`, `weight_method`, `provider`) AND `extra_params.universe` (mapped to the universe text input) and `extra_params.cost_model` (mapped to the cost model controls). **Round-trip mapping for extra_params:** When serializing form -> JSON, include `universe` and `cost_model` inside `extra_params`. When deserializing JSON -> form, populate the universe text input from `extra_params.universe` and cost model controls from `extra_params.cost_model`. **Provider value mapping:** JSON uses enum values (`"crsp"`, `"yfinance"`) while the form UI select uses display labels (`"CRSP (production)"`, `"Yahoo Finance (dev only)"`). Maintain a bidirectional mapping dict in `config_editor.py`: `PROVIDER_DISPLAY = {"crsp": "CRSP (production)", "yfinance": "Yahoo Finance (dev only)"}` and its inverse. Apply when toggling JSON → form (map value to label) and form → JSON (map label to value). If JSON contains an unrecognized provider string, show an inline validation error. Any other keys in `extra_params` beyond `universe` and `cost_model` are preserved in a hidden state variable and merged back on submit. **Key precedence:** Top-level fields always take precedence; `extra_params` cannot shadow top-level keys (if a key like `"alpha_name"` appears inside `extra_params`, it is ignored with a logged warning). If JSON contains `extra_params` with non-form-representable keys (other than `universe` and `cost_model`), show a warning: "Some extra_params fields are not editable in form mode." If JSON is invalid, block the toggle and show inline parse error - user must fix JSON before switching back. On submit, read from whichever mode is active. Unknown JSON keys are ignored for forward compatibility, but a non-blocking warning lists any ignored keys (e.g., "Ignored keys: foo, bar") so typos don't silently misconfigure backtests. **Detection mechanism:** Derive `KNOWN_CONFIG_KEYS` from `set(BacktestJobConfig.__dataclass_fields__.keys())` in `config_editor.py` to avoid manual maintenance. Before calling `from_dict()`, compute `input_keys - KNOWN_CONFIG_KEYS` and warn on any extras via `ui.notify(..., type="warning")`.

**JSON Schema (matches `BacktestJobConfig`):**
```json
{
  "alpha_name": "momentum_1m",
  "start_date": "2024-01-01",
  "end_date": "2025-12-31",
  "weight_method": "zscore",
  "provider": "crsp",
  "extra_params": {
    "universe": ["AAPL", "MSFT"],
    "cost_model": {
      "enabled": true,
      "bps_per_trade": 5.0,
      "impact_coefficient": 0.1,
      "participation_limit": 0.05,
      "adv_source": "crsp",
      "portfolio_value_usd": 1000000
    }
  }
}
```

**Validation:** Use `BacktestJobConfig.from_dict()` for deserialization and validation (it is a `@dataclass` with `to_dict()`/`from_dict()` helpers). Catch `ValueError`/`TypeError`/`KeyError` and display errors inline below the editor. No Pydantic migration needed. **Required fields:** `alpha_name`, `start_date`, `end_date` are required (no defaults in `from_dict()`). `weight_method` defaults to `"zscore"` and `provider` defaults to `"crsp"` if omitted. If a required field is missing, `from_dict()` raises `KeyError`; the editor catches this and reports "Missing required field: {field_name}". **JSON mode must enforce the same validation rules as the form:** `MIN_BACKTEST_PERIOD_DAYS`, future date bounds, 1990-2100 limits, `SYMBOL_PATTERN` for universe symbols, and provider-specific warnings (Yahoo + cost model). Extract a shared `validate_backtest_params(config: BacktestJobConfig) -> ValidationResult` helper in `backtest.py` that returns a `@dataclass ValidationResult(errors: list[str], warnings: list[str])`. Errors block submission (e.g., missing required fields, invalid date ranges, SYMBOL_PATTERN violations). Warnings are non-blocking and shown as `ui.notify(..., type="warning")` (e.g., "Yahoo Finance provider with cost model enabled - cost estimates may be inaccurate"). Call this helper from both form-mode `submit_job()` and JSON-mode submission. This avoids duplicating validation logic while preserving the distinction between hard errors and advisory warnings.

**Files to create:**
- `apps/web_console_ng/components/config_editor.py` - `render_config_editor()` function

**Files to modify:**
- `apps/web_console_ng/pages/backtest.py` - Add Advanced Mode toggle in `_render_new_backtest_form()`

**Acceptance Criteria:**
- [ ] "Advanced Mode" toggle in backtest form
- [ ] NiceGUI `ui.codemirror` with JSON syntax highlighting (verified available in NiceGUI 2.12.1)
- [ ] Pre-populate from current form state
- [ ] Schema validation via `BacktestJobConfig.from_dict()` before submission
- [ ] Validation errors displayed inline
- [ ] Copy config to clipboard button
- [ ] Submits through same code path as form mode

**Unit Tests:**
- JSON serialization round-trip (form state -> JSON -> `BacktestJobConfig.from_dict()`)
- Validation error handling for malformed JSON, missing required fields, invalid values
- SYMBOL_PATTERN validation still applies to universe symbols in JSON
- Toggle state sync: form values preserved when switching between form/JSON modes

**Test File:** `tests/apps/web_console_ng/components/test_config_editor.py`

---

### T12.2: Backtest Comparison Mode - MEDIUM PRIORITY

**Goal:** Enhanced comparison mode with equity curve overlay and metrics diff table.

**Current State Analysis:**
The existing `_render_backtest_results()` in `backtest.py` already has a basic comparison mode with:
- Comparison checkbox toggle
- Multi-select for backtests (max 5)
- `_render_comparison_table()` showing metrics side-by-side

**What's Missing (this task adds):**
1. **Equity curve overlay chart** - Plotly chart with overlaid cumulative return curves
2. **Metrics diff highlighting** - Color-code which backtest is better per metric (including Max Drawdown)
3. **Tracking error vs baseline** - Compare each selected backtest against the first-selected as baseline. Show annualized tracking error for each pair. This avoids a noisy N-by-N pairwise matrix. **Baseline fallback:** If the first-selected backtest is excluded (missing returns), use the next selected backtest with available returns as baseline. If no backtests have available returns, disable TE entirely and show "Tracking error unavailable - no return data".

**Approach:**
- Create `apps/web_console_ng/components/backtest_comparison_chart.py` with:
  - `render_comparison_equity_curves(returns_map: dict[str, pl.DataFrame])` - Plotly overlay chart of cumulative portfolio returns. Keys are job display labels, values are DataFrames with `{date, return}` schema (loaded via lightweight `BacktestAnalyticsService.get_portfolio_returns()`).
  - `render_comparison_metrics_diff(metrics_list: list[dict[str, Any]])` - Enhanced table with color-coded best/worst per metric. Each dict contains job label + pre-computed metric values (from DB summary fields + `CostSummary.from_dict()` when available + computed from return series when not). **T12.2 does NOT load full `BacktestResult` objects for comparison.** Instead, it loads: (a) DB job summary rows (already fetched by existing comparison code via `_get_user_jobs_sync()`), (b) `cost_summary` JSONB from DB - **note:** the current `_get_user_jobs_sync()` query does not include `cost_summary`; T12.2 must add `cost_summary` to the SELECT clause in `_get_user_jobs_sync()`, (c) return series via lightweight `load_portfolio_returns()`. This avoids reading signals/weights Parquets. **Metrics sources (from DB job summary rows, NOT full BacktestResult):** Mean IC (`job["mean_ic"]`), ICIR (`job["icir"]`), Hit Rate (`job["hit_rate"]`), Coverage (`job["coverage"]`), Avg Turnover (`job["average_turnover"]`). All these fields are already included in the `_get_user_jobs_sync()` SELECT query. All computed metrics must use the **selected basis** (net or gross) consistently. **Metric computation strategy:** Note: the `cost_summary` JSONB field from the DB row is `dict[str, Any] | None` (not a `CostSummary` object). Parse it via `CostSummary.from_dict(job["cost_summary"])` from `libs/trading/backtest/cost_model.py` before accessing fields, with a `try/except` returning `None` for corrupt data. (a) When `cost_summary` is present and parseable: use pre-computed values for the selected basis (net: `net_max_drawdown`, `net_sharpe`, `total_net_return`, `total_cost_usd`; gross: `gross_max_drawdown`, `gross_sharpe`, `total_gross_return`). **Missing key fallback:** `CostSummary.from_dict()` behavior: `gross_sharpe`, `net_sharpe`, `gross_max_drawdown`, `net_max_drawdown`, `total_gross_return`, `total_net_return` return `None` when missing (reliable sentinel for fallback). However, `total_cost_usd` defaults to `0.0` when missing, which is misleading for older jobs without cost data. **Detection:** Before parsing, check if the metric key exists in the raw `cost_summary` dict. For `total_cost_usd`: only display the value if `"total_cost_usd" in raw_dict`; otherwise show "N/A". For other metrics: if `None` after parsing, fall back to computing from the return series (same formulas as case (b)). If the return series is also unavailable, display "N/A". This ensures graceful degradation for older jobs. (b) When `cost_summary` is absent: compute from the selected return series - if basis is net and `net_portfolio_returns` exists, compute from `net_portfolio_returns["net_return"]`; if basis is gross (or net data unavailable), compute from `daily_portfolio_returns["return"]`. This ensures metrics always match the selected basis even when `cost_summary` is missing. Formulas: Max Drawdown (equity curve `E_t = cumprod(1+r_t)`, drawdown `DD_t = E_t / max(E_{0..t}) - 1`, max drawdown = `abs(min(DD_t))`; displayed as positive percentage e.g. "15.2%", lower-is-better; return `None` if series empty), Total Return (final `cumprod(1+r)-1`), Sharpe Ratio (`mean(r)/std(r,ddof=1)*sqrt(252)`; return `None` if `len < 2` or `std == 0`, display "N/A"). If basis is gross, omit Total Cost column. **Total Cost is only shown when `cost_summary` is present and parseable** - it cannot be derived from return series alone. When `cost_summary` is absent for a backtest, show "N/A" in the Total Cost cell for that backtest. Add a footnote: "Metrics reflect each backtest's full period; chart shows overlapping dates only." Full directionality map: higher-is-better: Mean IC, ICIR, Hit Rate, Coverage, Total Return, Sharpe; lower-is-better: Max Drawdown, Avg Turnover, Total Cost. "Tracking Error vs Baseline" is shown separately per backtest (computed from `compute_tracking_error()`), not in the diff table. Any unlisted metric uses neutral color (no highlighting) as fallback.
  - Tracking error computation uses the shared `compute_tracking_error()` from `libs/analytics/metrics.py` (see below). The app component imports and calls this function; it does NOT duplicate the logic.
- Integrate into existing comparison mode in `_render_backtest_results()`.
- **Note on existing direct `BacktestResultStorage` access:** The current `backtest.py` uses `BacktestResultStorage` directly (lines ~837, ~911) for loading results. T12's new comparison and overlay code will route through `BacktestAnalyticsService` (which enforces ownership checks). Refactoring the existing direct access paths to also use `BacktestAnalyticsService` is out of T12 scope but noted as a follow-up improvement.
- **Pool integration:** `BacktestAnalyticsService` requires two pools: async `get_db_pool()` for `StrategyScopedDataAccess` (async ownership checks, RBAC queries) and sync `get_sync_db_pool()` for `BacktestResultStorage` (sync Parquet/file I/O). The async pool follows the established pattern in dashboard.py (line 622), attribution.py (line 150), risk.py (line 129), and compare.py (line 262). Construction: `data_access = StrategyScopedDataAccess(async_pool, async_redis, user)`, `storage = BacktestResultStorage(sync_pool)`, `service = BacktestAnalyticsService(data_access, storage)`. **Important:** The `async_redis` parameter MUST be an async Redis client obtained via `get_redis_store().get_master_client()` from `apps/web_console_ng/core/redis_ha.py` (this is the established pattern in risk.py:131, attribution.py:152). Do NOT pass the store object itself (`get_redis_store()`) or the sync client from `get_sync_redis_client()` — `StrategyScopedDataAccess` uses `await self.redis.get(...)` for caching, and passing an incompatible type will silently disable caching. Pass `None` to explicitly disable Redis caching if async Redis is unavailable. If either pool is unavailable, show "Database not configured" and disable comparison/overlay features.
- **Return field selection:** Use a single basis per comparison session. **Two-pass approach:** First, call `get_portfolio_returns(job_id, basis="net")` for all selected backtests and check the returned tuples. **Missing return series handling:** If `get_portfolio_returns` returns `(None, ...)` for any backtest (missing/corrupt Parquet), exclude that backtest from the equity curve chart and TE calculations, show a warning listing excluded jobs (e.g., "Returns unavailable for job X - excluded from chart"), but still include it in the metrics diff table using DB summary rows with "N/A" for computed metrics (Sharpe, Max Drawdown, Total Return). **Basis selection:** Among backtests with available returns, if all return `"net"`, use net data. If any returns `"gross"` (fallback occurred), discard all loaded data and **re-fetch all with `basis="gross"`** to ensure uniform basis. Display a warning: "Some backtests lack cost model data; showing gross returns for all." This ensures no mixed-basis data enters the chart or metrics calculations. **Column schemas:** `daily_portfolio_returns` has `{date: Date, return: Float64}`; `net_portfolio_returns` has `{date: Date, gross_return: Float64, cost_drag: Float64, net_return: Float64}`. Normalize the selected column to a common `"return"` series before alignment/cumprod. Display a label "(net)" or "(gross)" on the chart title to indicate the active basis.
- **Date alignment policy (T12.2):** Chart uses inner join on dates across all selected backtests for visual overlay. The chart subtitle shows the intersection range and count (e.g., "Overlapping period: 2024-03-01 to 2025-06-30, 320 dates"). **Zero overlap:** If the inner join produces zero dates, show a warning "No overlapping dates for chart" and skip rendering the equity curve chart. **The metrics diff table is still rendered** since it shows each backtest's full-period metrics (which don't depend on overlap). Tracking error is computed per backtest using a **pairwise** inner join with the baseline only (independent of the global inner join), so TE is not distorted by a third backtest's shorter date range. A zero global overlap does NOT force all TEs to "N/A" — each pair is evaluated independently. If <2 dates overlap in any pairwise join, show "Insufficient data" for that specific TE.
- **NaN/null handling:** Drop rows with null/NaN returns before alignment and cumprod. This applies consistently to both T12.2 (comparison) and T12.3 (live overlay, where "no trades" days are zero-filled before this step, so only truly missing data is dropped).
- **Cumulative return calculation:** Use compounded cumulative return: `cumprod(1 + daily_return) - 1`. **Insertion order:** First perform date alignment (inner join across backtests for chart), then prepend a synthetic day-0 row with `return = 0` to each aligned series (using the day before the first aligned date), then compute cumprod. This ensures all curves start at exactly 0.0 on the baseline date regardless of which backtests participate in the join. The synthetic row is for display only and excluded from TE/metric calculations.

**Tracking Error Formula:**
```
TE = std(R_a - R_b, ddof=1) * sqrt(252)
```
Where `R_a` and `R_b` are daily portfolio returns aligned by date. Uses sample standard deviation (`ddof=1`) consistently across T12.2 and T12.3.

**Files to create:**
- `apps/web_console_ng/components/backtest_comparison_chart.py`

**Files to modify:**
- `apps/web_console_ng/pages/backtest.py` - Update `_render_comparison_table()` to use new components

**Acceptance Criteria:**
- [ ] Overlay equity curves for 2-5 selected backtests
- [ ] Color-coded metrics diff table (green = best, red = worst per metric) using full-period metrics (stored fields + computed from daily returns as specified above)
- [ ] Tracking error calculated vs first-selected baseline and displayed per backtest
- [ ] Cost-adjusted comparison when cost model data available (use net returns if present)
- [ ] Handles missing data gracefully (backtests with different date ranges)
- [ ] Ownership verification for all selected backtests (already exists in current code)

**Unit Tests:**
- Tracking error calculation with known inputs (pairwise vs baseline alignment)
- Date alignment for backtests with different date ranges
- Handling of None/NaN in portfolio returns
- Color-coding logic for metrics diff
- Net/gross basis selection: all-net uses `net_return`, mixed uses `return` with warning
- Computed metrics (Max Drawdown, Sharpe, Total Return) use selected basis consistently

**Test File:** `tests/apps/web_console_ng/components/test_backtest_comparison_chart.py`

---

### T12.3: Live vs Backtest Overlay - HIGH PRIORITY

**Goal:** Compare live trading performance against backtest expectations for alpha decay detection.

**Technical Risk:** HIGH - Requires alignment between backtest and live execution data.

**Approach:**

#### Shared Metrics: `libs/analytics/metrics.py`
Create a shared metrics module in `libs/analytics/` to avoid duplicating metric logic between T12.2 (app component) and T12.3 (libs analyzer). Both the app-level comparison component and `LiveVsBacktestAnalyzer` import from this module.

- `compute_tracking_error(returns_a: pl.DataFrame, returns_b: pl.DataFrame, pre_aligned: bool = False) -> float | None` - Annualized tracking error between two return series. Both inputs must have `{date, return}` schema. **When `pre_aligned=False` (default, used by T12.2):** performs internal alignment via inner-join on `date`, drops rows with null/NaN `return` values, then computes `std(diff, ddof=1) * sqrt(252)`. **When `pre_aligned=True` (used by T12.3):** assumes the caller has already aligned the series (e.g., via left-join + zero-fill) and skips the internal join. Only drops NaN/null values, then computes TE. This distinction is necessary because T12.3 uses left-join with zero-fill for no-trade days (different alignment semantics than T12.2's inner join). Returns `None` if <2 valid dates after filtering (UI shows "N/A"); returns `0.0` if `std == 0` (identical return series).

#### Backend: `libs/analytics/live_vs_backtest.py`
Create a new module in `libs/analytics/` with:

1. **`LiveVsBacktestAnalyzer` class (pure data-in/data-out, no I/O):**
   - Input: `live_returns: pl.DataFrame` (columns: `date`, `return`) + `backtest_returns: pl.DataFrame` (columns: `date`, `return`) + `config: OverlayConfig`. **Both inputs use standardized `{date, return}` schema.** The caller is responsible for converting and normalizing before passing to the analyzer:
     - **Live returns:** `StrategyScopedDataAccess.get_portfolio_returns()` returns `list[dict]` with keys `{date, daily_return}`. The caller must: (1) convert to `pl.DataFrame`, (2) rename `daily_return` → `return`, (3) cast `date` column to `pl.Date` if not already, (4) sort by `date` ascending. This ensures correct alignment and cumulative calculations.
     - **Backtest returns:** Already a `pl.DataFrame` from `load_portfolio_returns()` with `{date, return}` schema. Sort by `date` ascending before passing.
   - The **caller** (in `backtest.py`) fetches data: live returns via `await StrategyScopedDataAccess.get_portfolio_returns()` and backtest returns via `BacktestAnalyticsService` (which wraps `BacktestResultStorage` with ownership verification and async/sync bridging via `run_in_threadpool`).
   - **Storage layer:** Add a new `BacktestResultStorage.load_portfolio_returns(job_id: str, basis: str) -> pl.DataFrame | None` method that reads ONLY the return Parquet files (`daily_portfolio_returns.parquet` for gross, `net_portfolio_returns.parquet` for net) without loading `daily_signals.parquet` or `daily_weights.parquet`. This is critical for T12.2 (comparing 2-5 backtests) and T12.3 (live overlay) where only returns are needed. The method resolves the result path from the DB using the existing `_get_job_artifact_path()` helper (which validates against `base_dir` to prevent path traversal), reads the specific Parquet file, normalizes the return column to `"return"` (renaming `net_return` for net basis), and returns the DataFrame with `{date, return}` schema. **Error contract (matches existing storage patterns, e.g., `load_walk_forward_results`, `load_param_search_results`):** Delegates to `_get_job_artifact_path(job_id)` which: raises `JobNotFound` if job doesn't exist in DB; raises `ResultPathMissing` if result_path is invalid/outside allowed directory; returns `None` if `result_path` is NULL (job hasn't completed yet / no artifacts). If `_get_job_artifact_path()` returns `None`, `load_portfolio_returns()` returns `None` (no exception). If it returns a valid path but the specific Parquet file doesn't exist on disk or a Polars read error occurs, returns `None` (logged with `logger.warning`). `BacktestAnalyticsService` catches `JobNotFound`/`ResultPathMissing` from storage and returns `(None, basis)`; `None` from storage flows through as `(None, basis)` as well. This method has NO basis fallback logic — it reads exactly the requested file or returns `None`.
   - **Service layer:** Add a new `BacktestAnalyticsService.get_portfolio_returns(job_id: str, basis: Literal["net", "gross"] = "net") -> tuple[pl.DataFrame | None, Literal["net", "gross"]]` that verifies ownership and owns basis fallback logic. **Return contract:** Returns a tuple of `(DataFrame_with_{date,return}_columns | None, actual_basis_used)`. **Basis selection:** If `basis="net"`, first attempts `storage.load_portfolio_returns(job_id, "net")`; if that returns `None`, falls back to `storage.load_portfolio_returns(job_id, "gross")` and returns `(..., "gross")` so the caller knows a fallback occurred. If `basis="gross"`, calls `storage.load_portfolio_returns(job_id, "gross")` directly. If both net and gross return `None`, returns `(None, basis)`. **Error handling:** `verify_job_ownership()` raises only `PermissionError` (for both "not found" and "not authorized" — security best practice). `PermissionError` is NOT caught — it propagates to the caller. On `JobNotFound`/`ResultPathMissing` from storage methods, returns `(None, basis)` with logged warning (UI shows "Analytics unavailable"). Note: `verify_job_ownership()` raises `PermissionError` for both "job not found" and "not authorized" (security best practice). The UI catches `PermissionError` and shows "Access denied or job not found". **Callers use `basis_used` to:** (a) T12.2: probe all selected backtests, detect if any fell back to gross, then use all-net or all-gross with a warning; (b) T12.3: detect basis mismatch and disable alerts. This keeps the analytics module free of async/I/O dependencies and respects the security contract that all backtest artifact access goes through `BacktestAnalyticsService`.
   - **Pre-existing bug fix (prerequisite):** `StrategyScopedDataAccess.verify_job_ownership()` currently queries `SELECT strategy_id FROM backtest_jobs` but `backtest_jobs` has no `strategy_id` column (it has `created_by` for ownership and `alpha_name` for the alpha). T12 must fix this:
     1. **Shared identity helper:** Extract `_get_user_id(user)` from `backtest.py:84-93` into a shared location (e.g., `libs/web_console_data/strategy_scoped_queries.py` or a `utils` module). This function resolves identity as `user.get("user_id") or user.get("username")` with fail-closed behavior. **Identity mismatch note:** `StrategyScopedDataAccess.__init__` currently uses `user.get("user_id") or user.get("sub")` (line 113) which differs from job creation's `user.get("user_id") or user.get("username")`. To ensure the ownership comparison matches what was stored in `created_by`, the `verify_job_ownership()` method must use the same `user_id → username` fallback as the creation path. Either update `self.user_id` initialization to match, or use the shared helper specifically for ownership checks.
     2. **Fix query:** Change to `SELECT created_by FROM backtest_jobs WHERE job_id = %s`, then compare `created_by` against the resolved user identifier. Use a generic error message for both "not found" and "not authorized": `raise PermissionError("Access denied")` (do not reveal job existence to unauthorized users).
     3. **Update docstring:** Change from "checks strategy_id authorization" to "verifies user ownership via created_by".
     4. **Update existing tests:** `tests/libs/web_console_data/test_strategy_scoped_queries.py` has tests for `verify_job_ownership()` (lines ~1192-1291) that assert `strategy_id` rows and specific error messages. These must be updated to: use `created_by` in mock DB rows, assert the generic `PermissionError("Access denied")` message, and test the new identity resolution logic. Also update any dependent mocks/fixtures in `tests/libs/web_console_services/` if they rely on old `strategy_id` semantics.
     This is a prerequisite for any `BacktestAnalyticsService` method to work correctly. The existing `_verify_job_ownership()` in `backtest.py:198-223` already correctly queries `created_by` and can serve as a reference implementation.
   - **Live return source:** `get_portfolio_returns()` already returns daily returns computed as `daily_pnl / (nav - daily_pnl)` from the `pnl_daily` table. The SQL CASE expression returns `0.0` when `(nav - daily_pnl) <= 0`, so NaN/inf values cannot occur in the output. Empty live input is valid - the alignment step zero-fills missing live dates within the overlay window. Insufficiency is determined by the analyzer after alignment: if <2 aligned dates remain, return the "Insufficient data" result.
   - **Date alignment algorithm:**
     1. The UI provides explicit start/end date inputs for the overlay window (defaulting to the backtest's date range). This avoids inferring the window from PnL data which would miss zero-trade periods.
     2. Restrict backtest dates to `[overlay_start, overlay_end]` (inclusive).
     3. Left-join live returns onto those restricted backtest dates, filling missing live values with 0 return (no trades = flat). This ensures zero-trade days are represented as 0 return rather than being dropped.
     4. Use that single aligned DataFrame for all metrics (tracking error, cumulative divergence, alerts).
     If the backtest has date gaps within the window (e.g., missing holidays), those dates are absent from the aligned series. The UI shows the number of aligned dates and the date range so users can assess data completeness.
   - **Live data source:** Use `StrategyScopedDataAccess.get_portfolio_returns(strategy_id, start_date, end_date)` from `libs/web_console_data/strategy_scoped_queries.py`. This method already exists (added in P6T10 for attribution), computes daily returns from the `pnl_daily` table (`daily_pnl / (nav - daily_pnl)`), enforces RBAC via `authorized_strategies`, and returns `[{date, daily_return}]`. Dates are already UTC-normalized in the `pnl_daily` table. This is the same access path used by the attribution page, ensuring consistent data and authorization. Since it's already async, no `run.io_bound()` wrapping is needed.
   - **Strategy mapping:** The UI provides a strategy selector dropdown populated from `StrategyScopedDataAccess.authorized_strategies` (already RBAC-filtered). The user selects which live strategy to compare against which completed backtest. `get_portfolio_returns()` takes `strategy_id` directly. The `backtest_jobs` table stores `alpha_name` (not `strategy_id`), so the UI pre-selects the dropdown entry matching the backtest's `alpha_name` if one exists in `authorized_strategies`, otherwise leaves the dropdown unselected and prompts the user to choose.
   - Computes metrics:
     - **Tracking error**: `std(live_return - bt_return, ddof=1) * sqrt(252)` (annualized, sample std)
     - **Cumulative divergence**: absolute difference of compounded cumulative returns at end: `|cum_live[-1] - cum_bt[-1]|` where `cum = cumprod(1+r) - 1`. Threshold is applied to this absolute difference (e.g., 0.10 = 10 percentage points).
     - **Divergence start date**: Compute cumulative returns `cum_live_t = cumprod(1+r_live)-1` and `cum_bt_t = cumprod(1+r_bt)-1`, then `divergence_t = abs(cum_live_t - cum_bt_t)`. Compute `rolling_max_div_t = max(divergence_{t-N+1..t})` using `config.rolling_window_days` as N. Divergence start date = the **window end date** of the first window where `rolling_max_div_t > config.divergence_threshold`. Example: if `rolling_window_days=20` and the first breach occurs at index 25 (window covers dates 6-25), report date 25 as the divergence start date.


2. **`OverlayResult` dataclass:**
   ```python
   @dataclass
   class OverlayResult:
       live_cumulative: pl.DataFrame  # date, cumulative_return
       backtest_cumulative: pl.DataFrame  # date, cumulative_return
       tracking_error_annualized: float | None  # None if < 2 aligned dates
       cumulative_divergence: float | None  # None if < 2 aligned dates
       divergence_start_date: date | None
       alert_level: AlertLevel  # NONE, YELLOW, RED
       alert_message: str
   ```

3. **Alert Logic (rolling window approach):**
   - Compute a daily rolling tracking error series using `config.rolling_window_days` window with `min_periods=config.rolling_window_days`: `rolling_te_t = std(diff[-window:], ddof=1) * sqrt(252)` where `diff = live_return - bt_return`. Days with fewer than `rolling_window_days` observations produce `NaN` (not included in alert checks). If total aligned dates < 2, return `tracking_error_annualized = None`, `cumulative_divergence = None`, `divergence_start_date = None`, and the UI shows "Insufficient data".
   - **Precedence:** RED > YELLOW > NONE (check RED first, then YELLOW, then NONE). All thresholds come from `OverlayConfig`:
   - `AlertLevel.RED`: Absolute cumulative divergence > `config.divergence_threshold` (default 0.10 = 10pp)
   - `AlertLevel.YELLOW`: Rolling TE > `config.tracking_error_threshold` (default 0.05 = 5%) for `config.consecutive_days_for_yellow` (default 5) consecutive **aligned trading dates** (not calendar days). NaN values in the rolling TE series (from insufficient window data) are skipped without resetting the streak - only non-NaN values below threshold reset the streak. This prevents gaps from masking sustained decay.
   - `AlertLevel.NONE`: Neither RED nor YELLOW condition is met
   - **Minimum sample rule:** Require at least `config.rolling_window_days` overlapping dates before computing rolling TE alerts (YELLOW). Below minimum, set `AlertLevel.NONE` with message "Insufficient data for alert computation". **Exception:** RED alert (cumulative divergence) can still trigger with as few as 2 aligned dates, since it's a point-in-time check on cumulative returns and doesn't depend on rolling statistics. This ensures material divergence is not masked during early overlay periods.

4. **Configuration via Pydantic:**
   ```python
   class OverlayConfig(BaseModel):
       tracking_error_threshold: float = Field(0.05, ge=0.0)  # 5% annualized, non-negative
       divergence_threshold: float = Field(0.10, ge=0.0)  # 10pp, non-negative
       consecutive_days_for_yellow: int = Field(5, ge=1)  # At least 1 day
       rolling_window_days: int = Field(20, ge=2)  # Minimum 2 for ddof=1 to be meaningful

   OverlayConfig defaults are defined in `libs/analytics/live_vs_backtest.py`. The UI passes overrides from form inputs.
   ```

#### Frontend: `apps/web_console_ng/components/backtest_comparison_chart.py`
Add to the same component file created in T12.2:

- `render_live_vs_backtest_overlay(overlay_result: OverlayResult)` - Plotly chart with:
  - Cumulative returns use `cumprod(1 + daily_return) - 1` (compounded, consistent with T12.2). Both curves start at 0 via a synthetic day-0 baseline row (same convention as T12.2).
  - Solid line: Live cumulative returns
  - Dashed line: Backtest expected cumulative returns
  - Shaded region between curves for divergence visualization
  - Alert badge with color based on `alert_level`
  - Date coverage label showing the `[live_start, live_end]` alignment window

#### Data Sources:
- **Backtest returns:** Prefer `net_portfolio_returns["net_return"]` when available (cost model was applied), since live returns are inherently net of execution costs. If the selected backtest lacks `net_portfolio_returns`, fall back to `daily_portfolio_returns["return"]` (gross) but display a prominent warning: "Comparing live (net) vs backtest (gross) - divergence metrics may be inflated by execution costs" and **disable alert thresholds** (set `AlertLevel.NONE` with message "Alerts disabled: basis mismatch") to prevent false RED/YELLOW alerts. Normalize to a common `"return"` series. Label the chart basis "(net)" or "(gross)".
- **Live returns:** See "Live data source" above (`StrategyScopedDataAccess.get_portfolio_returns()`)
- **Corporate actions:** `libs/data/data_pipeline/corporate_actions.py` (`adjust_for_splits()`) - single source of truth, used by both backtest engine and data pipeline. Live fills are already adjusted at execution time via the execution gateway.
- **Slippage model:** `libs/trading/backtest/cost_model.py` (used for backtest; live uses actual fills from execution gateway webhooks)

#### Known Limitations (documented in code and displayed near overlay chart):
- **Live series from `pnl_daily`** includes total daily P&L (realized + unrealized) and NAV. Returns are computed as `daily_pnl / (nav - daily_pnl)`. Label the chart axis as "Cumulative Return".
- Backtest assumes instant fills at close; live has actual fill prices with slippage
- Market impact is estimated in backtest but real in live
- Corporate action reconciliation may have delays
- Trade timing: backtest is T, live is T+settlement

**Files to create:**
- `libs/analytics/__init__.py`
- `libs/analytics/live_vs_backtest.py`

**Files to modify:**
- `apps/web_console_ng/components/backtest_comparison_chart.py` (add overlay render function)
- `apps/web_console_ng/pages/backtest.py` (add "Live Overlay" tab or section)

**Acceptance Criteria:**
- [ ] Both live and backtest curves visible on same Plotly chart
- [ ] Tracking error calculated (annualized) and displayed
- [ ] Alert levels: NONE (green), YELLOW (warning), RED (alert)
- [ ] Cumulative divergence shown
- [ ] Divergence start date identified when applicable
- [ ] Timezone alignment: All dates in UTC internally
- [ ] Known limitations documented in module docstring
- [ ] Graceful handling when live data is unavailable (show message, not crash)
- [ ] Strategy authorization via `StrategyScopedDataAccess.authorized_strategies` (RBAC-filtered)
- [ ] Gate overlay behind data readiness: show "Insufficient data" if aligned series has <2 dates after zero-fill alignment
- [ ] Overlay window validation: if selected dates fall outside backtest range or yield zero overlap, show warning "Selected window has no overlapping dates with backtest" and disable overlay

**Unit Tests:**
- Tracking error calculation with known synthetic data
- Live return integration (verify `get_portfolio_returns()` output aligns correctly with backtest returns)
- Alert level thresholds (NONE/YELLOW/RED transitions)
- Date alignment with gaps (weekends, holidays) using left-join with zero-fill within overlay window
- Overlay window validation (outside backtest range, zero overlap)
- Divergence start date detection
- Empty/None input handling
- Strategy authorization gating (mock `StrategyScopedDataAccess.authorized_strategies`)

**Test File:** `tests/libs/analytics/test_live_vs_backtest.py`

---

### T12.4: Data Health Widget - MEDIUM PRIORITY

**Goal:** Dashboard widget showing data freshness status per data source.

**Current State Analysis:**
- `libs/data/data_pipeline/freshness.py` already provides `check_freshness()` and `check_freshness_safe()` for validating DataFrame timestamps.
- `libs/data/data_quality/` has schema validation and manifest checking.
- No centralized "health monitor" that aggregates freshness across multiple data sources.

**Approach:**

#### Backend: `libs/data/data_pipeline/health_monitor.py`
Place in existing `data_pipeline` module (not `feature_store` which doesn't exist) since it extends the existing freshness infrastructure.

1. **`DataSourceHealth` dataclass:**
   ```python
   @dataclass
   class DataSourceHealth:
       name: str
       category: str  # "price", "volume", "signal", "fundamental"
       last_update: datetime | None
       age_seconds: float | None
       status: HealthStatus  # OK, STALE, ERROR
       message: str
       last_checked: datetime  # When this source was last checked (for widget staleness display)
   ```

2. **`HealthStatus` enum:** `OK`, `STALE`, `ERROR`

3. **`HealthMonitor` class:**
   - Configurable staleness thresholds per category via Pydantic:
     ```python
     class HealthThresholds(BaseModel):
         price_stale_seconds: int = Field(900, ge=1)  # 15 min; must be positive
         volume_stale_seconds: int = Field(900, ge=1)  # 15 min; must be positive
         signal_stale_seconds: int = Field(600, ge=1)  # 10 min; must be positive
         fundamental_stale_seconds: int = Field(86400, ge=1)  # 24 hr; must be positive
     ```
   - **Threshold rationale:** Defaults are set to ~1.5x the expected ETL/pipeline cadence to avoid false STALE alerts. The global `data_freshness_minutes` in `config/settings.py` (default: 30 min) is for circuit-breaker-level staleness and is intentionally separate. Thresholds are configurable via `HealthThresholds` so operators can tune them to match actual job schedules. Document this distinction in the module docstring.
   - `check_all() -> list[DataSourceHealth]`: Check all registered data sources. **Exception handling:** Each check function is called inside a `try/except Exception` block. If a check function raises (e.g., Redis/DB outage), that source gets `HealthStatus.ERROR` with message "Check failed: {exception}" and the error is logged with `logger.warning`. Other sources continue to be checked independently - one failing source must not prevent the widget from displaying status for the rest.
   - `register_source(name, category, check_fn)`: Register a data source with an async callable that returns `datetime | None` (last update time). Check functions may perform Redis/DB I/O but should be quick (single key lookups); the singleton's caching layer prevents repeated calls. Since `check_all()` is async, it can be awaited directly in the async dashboard page context.
   - Uses `datetime.now(UTC)` for age calculation (consistent with existing `freshness.py`)

4. **Initial data source registrations:**
   | Name | Category | Timestamp Source | Staleness Default | Exists Today? |
   |------|----------|-----------------|-------------------|---------------|
   | Price Data | price | Redis key `market:last_update:prices` | 900s (15min) | No - add SET in data pipeline ETL |
   | Volume Data | volume | Redis key `market:last_update:volume` | 900s (15min) | No - add SET in data pipeline ETL |
   | Alpha Signals | signal | Redis key `signal:last_update:{strategy_id}` | 600s (10min) | No - add SET in `apps/signal_service/main.py` after `generate_signals()` completes (NOT inside `SignalGenerator` which lacks strategy context) |
   | Fundamental Data | fundamental | Redis key `market:last_update:fundamentals` | 86400s (24hr) | No - add SET when fundamental data refresh completes (Compustat data is file-based via `libs/data/data_providers/compustat_local_provider.py`, no DB table) |

   **Implementation note:** T12.4 must add Redis SET calls to the data pipeline ETL (`libs/data/data_pipeline/etl.py`) and signal service (`apps/signal_service/main.py`, after `generate_signals()` calls) to produce these timestamp keys. **Fundamental data** uses a Redis heartbeat key (`market:last_update:fundamentals`) rather than a DB query because Compustat data is file-based (Parquet via `CompustatLocalProvider`). There is no in-repo production fundamental data refresh pipeline; the heartbeat infrastructure is added so that when a refresh script is wired up, it can SET the key. Until then, the fundamental health check shows ERROR with "No heartbeat recorded".

   **Redis client access:**
   - **Signal service:** Place the heartbeat write in `apps/signal_service/main.py`, NOT inside `SignalGenerator` (which lacks strategy context). After each successful `generate_signals()` call (both the primary path at line ~1718 and the cached generator path at line ~1713), add a best-effort Redis SET: `redis_client.set(f"signal:last_update:{settings.default_strategy}", datetime.now(UTC).isoformat())` wrapped in `try/except` with `logger.warning`. This keeps the heartbeat logic in `main.py` where both `redis_client` (module-level `RedisClient` from `libs.core.redis_client`) and `settings.default_strategy` are already available. No changes needed to `SignalGenerator` class itself.
   - **ETL:** Add `redis_client: RedisClient | None = None` as a **keyword-only** parameter to `run_etl_pipeline()` in `libs/data/data_pipeline/etl.py` (current signature: `run_etl_pipeline(raw_data, splits_df, dividends_df, freshness_minutes, outlier_threshold, output_dir, run_date)`). Append it after `run_date` with `*` separator or as a keyword-only arg after the existing positional params. At the end of the pipeline, if `redis_client` is not None, SET the price/volume timestamp keys. Existing call sites (currently only tests) use positional args and remain untouched since `redis_client` is keyword-only with a `None` default. **Note:** There is no in-repo production caller of `run_etl_pipeline()` today - the ETL is invoked externally. T12.4 adds the heartbeat infrastructure (param + SET logic) so that when a production ETL runner is wired up, it can pass `redis_client=<client>` to enable heartbeat tracking. Until then, the price/volume health checks will show ERROR status with a message "No heartbeat recorded" so operators know to wire up the ETL caller. Creating a production ETL runner script (`scripts/etl_runner.py`) is out of T12 scope and noted as a follow-up task, since it requires defining data ingestion sources, CLI args, and output_dir configuration that are orthogonal to the health widget.

   **What is tracked:** The timestamp represents when the pipeline last **successfully completed** (pipeline heartbeat), not the timestamp of the latest data point. The widget header is labeled "Pipeline Activity" and each row shows "Last run: X ago" to clearly indicate this is pipeline execution recency, not upstream data freshness. Store as UTC ISO 8601 strings (e.g., `"2026-01-15T14:30:00+00:00"`) via `datetime.now(UTC).isoformat()`. These are **best-effort** writes wrapped in `try/except` with `logger.warning` on failure - a Redis write error must never break ETL or signal generation. The health monitor parses with `datetime.fromisoformat()`, catching `ValueError` and returning `ERROR` status with message "Invalid timestamp format" on parse failure. If a source key doesn't exist in Redis, the widget shows ERROR status for that source.

   Each check function reads from the appropriate Redis key or DB column and returns `datetime | None`. The monitor is agnostic to the storage backend - each registered check function handles its own lookup.

   **Dashboard signal freshness aggregation:** The Redis key format is `signal:last_update:{strategy_name}`. **Source of truth:** The dashboard page derives strategy names from `get_authorized_strategies(user)` (from `libs/platform/web_console_auth/permissions.py`), which is the same RBAC-based strategy list used by other web console pages (execution_quality.py, compare.py). For each authorized strategy, register a check function that reads `signal:last_update:{strategy_name}`. The signal service writes these keys using `settings.default_strategy` from `apps/signal_service/config.py` (defaults to `"alpha_baseline"`). **Do NOT use `config/settings.py`** for strategy IDs in the web console — the web console layer uses RBAC-derived strategies, not the global config. If no authorized strategies are available (empty list), skip signal check registration and show "No authorized strategies" in the widget. If any signal is stale, the "Alpha Signals" row shows STALE with the strategy name.

   **Instance lifetime:** `HealthMonitor` is a module-level singleton in `health_monitor.py` (created once via `get_health_monitor() -> HealthMonitor`). The dashboard page calls `get_health_monitor()` to reuse the same instance across all clients and timer ticks. Cache state lives on the singleton instance.

   **Caching:** The singleton caches results **per-source** with configurable TTLs. Fast-updating checks (price, volume, signal) use a 10s TTL. Slow-updating checks (fundamental data) use a 60s TTL since fundamentals update infrequently (daily at most). `check_all()` iterates over registered sources, returning cached results for sources within their TTL and calling check_fn only for expired entries. This avoids redundant lookups when multiple dashboard clients poll simultaneously while allowing different update frequencies per data category. The widget displays a "last checked" timestamp so users know data freshness of the widget itself.

   **Infrastructure access:** `HealthMonitor` is a pure library class in `libs/data/data_pipeline/` and MUST NOT import from `apps/`. It receives its dependencies via dependency injection: check functions are registered by the caller (dashboard page) which has access to Redis/DB clients. **Async-first design:** `HealthMonitor` supports async check functions (`check_fn: Callable[[], Awaitable[datetime | None]]`) and `check_all()` is an async method. This aligns with the dashboard's async architecture. The dashboard page registers async check functions using `get_db_pool()` (async pool) and `get_redis_store()` (async Redis from `apps/web_console_ng/core/redis_ha.py`), matching the established dashboard patterns. Example: `monitor.register_source("Price Data", "price", lambda: _check_redis_key(redis_client, "market:last_update:prices"))` where `_check_redis_key` is an async function. **Missing config handling:** `get_db_pool()` returns `None` (not raises) when `DATABASE_URL` is unset; `get_redis_store()` may return `None` or a store whose `get_master_client()` returns `None` without Redis config. Before registering check functions, explicitly check if the dependency is `None`: `async_pool = get_db_pool(); if async_pool is None: skip DB-based checks`. Similarly for Redis: wrap `get_redis_store().get_master_client()` in `try/except Exception` since `get_redis_store()` always returns a store instance but may raise during initialization if Redis is misconfigured; on failure, skip Redis-based checks. If any dependency is unavailable, skip registering its check functions and render the widget with a "Data health partially unavailable (missing config)" message for the unregistered sources. This prevents dashboard crashes in local/dev environments without full infrastructure. This keeps the library layer free of app-layer dependencies and avoids circular imports.

#### Frontend: `apps/web_console_ng/components/data_health_widget.py`

- `render_data_health(sources: list[DataSourceHealth])` - NiceGUI component:
  - Header row: "Data Health" with overall status badge (All OK / Issues Detected)
  - Per-source rows: status icon + name + age + status text
  - Status icons: checkmark (OK), warning (STALE), error (ERROR)
  - Color coding: green (OK), amber (STALE), red (ERROR)
  - Auto-refresh via `ui.timer` (every 10s)

#### Integration:
- Add widget to the dashboard page (`apps/web_console_ng/pages/dashboard.py`) in a card/expansion panel.
- Register default data sources (price, volume, signals) using existing Redis or DB timestamps.

**Files to create:**
- `libs/data/data_pipeline/health_monitor.py`
- `apps/web_console_ng/components/data_health_widget.py`

**Files to modify:**
- `apps/web_console_ng/pages/dashboard.py` - Add data health widget
- `apps/web_console_ng/components/__init__.py` - Export if class-based

**Acceptance Criteria:**
- [ ] Data health widget visible on dashboard
- [ ] Per-source status with age display (human-readable: "2s ago", "5m 32s ago")
- [ ] Staleness thresholds configurable via `HealthThresholds` Pydantic model
- [ ] Status icons and color coding (OK=green, STALE=amber, ERROR=red)
- [ ] Overall summary badge
- [ ] Auto-refresh via `ui.timer` (10s interval)
- [ ] Timer cleanup via `ClientLifecycleManager`

**Unit Tests:**
- HealthStatus determination based on age vs threshold
- Human-readable age formatting
- None/missing last_update handling (should show ERROR)
- Threshold configuration

**Test Files:**
- `tests/libs/data/data_pipeline/test_health_monitor.py`
- `tests/apps/web_console_ng/components/test_data_health_widget.py`

---

## Dependencies

```
P6T9.1 Cost Model ──> T12.3 Live vs Backtest (slippage comparison)

T12.1 Config Editor    (independent)
T12.2 Comparison       (independent, T12.3 reuses component file)
T12.3 Live Overlay     (depends on P6T9, extends T12.2 component file)
T12.4 Data Health      (independent)
```

---

## File Summary

### New Files (7)
| File | Task | Purpose |
|------|------|---------|
| `apps/web_console_ng/components/config_editor.py` | T12.1 | JSON config editor render function |
| `apps/web_console_ng/components/backtest_comparison_chart.py` | T12.2, T12.3 | Equity curve overlay + live vs backtest chart |
| `libs/analytics/__init__.py` | T12.2, T12.3 | Analytics package init |
| `libs/analytics/metrics.py` | T12.2, T12.3 | Shared metric helpers (compute_tracking_error) used by both comparison and overlay |
| `libs/analytics/live_vs_backtest.py` | T12.3 | LiveVsBacktestAnalyzer + OverlayResult |
| `libs/data/data_pipeline/health_monitor.py` | T12.4 | HealthMonitor + DataSourceHealth |
| `apps/web_console_ng/components/data_health_widget.py` | T12.4 | Data health dashboard widget |

### Modified Files (8)
| File | Task | Change |
|------|------|--------|
| `apps/web_console_ng/pages/backtest.py` | T12.1, T12.2, T12.3 | Advanced Mode toggle, enhanced comparison, live overlay tab |
| `libs/web_console_services/backtest_analytics_service.py` | T12.2, T12.3 | Add `get_portfolio_returns(job_id, basis)` method for secure backtest return access |
| `libs/trading/backtest/result_storage.py` | T12.2, T12.3 | Add lightweight `load_portfolio_returns(job_id, basis)` that reads only return Parquets |
| `libs/web_console_data/strategy_scoped_queries.py` | T12.3 (prereq) | Fix `verify_job_ownership()` to use `created_by` instead of non-existent `strategy_id` |
| `apps/web_console_ng/pages/dashboard.py` | T12.4 | Add data health widget |
| `apps/web_console_ng/components/__init__.py` | T12.4 | Export new components if class-based |
| `libs/data/data_pipeline/etl.py` | T12.4 | Add keyword-only `redis_client` param; Redis SET for price/volume timestamps |
| `apps/signal_service/main.py` | T12.4 | Add Redis SET for `signal:last_update:{strategy_id}` after `generate_signals()` calls |

### New Test Files (5)
| File | Task |
|------|------|
| `tests/apps/web_console_ng/components/test_config_editor.py` | T12.1 |
| `tests/apps/web_console_ng/components/test_backtest_comparison_chart.py` | T12.2 |
| `tests/libs/analytics/test_live_vs_backtest.py` | T12.3 |
| `tests/libs/data/data_pipeline/test_health_monitor.py` | T12.4 |
| `tests/apps/web_console_ng/components/test_data_health_widget.py` | T12.4 |

---

## Testing Strategy

### Unit Tests
- T12.1: JSON round-trip, validation errors, symbol pattern enforcement
- T12.2: Tracking error calculation, date alignment, metrics diff logic
- T12.3: Alert level transitions, divergence detection, empty data handling
- T12.4: Health status determination, age formatting, threshold config

### Integration Tests
- T12.1: Config editor submits through same path as form mode
- T12.3: Live vs backtest data alignment with backtest Parquet artifacts + mocked `StrategyScopedDataAccess.get_portfolio_returns()` return values
- T12.4: HealthMonitor with mocked Redis/DB source checks

### E2E Tests
- T12.2: Full comparison workflow (select backtests -> view chart -> see tracking error)
- T12.4: Data health widget updates on dashboard

---

## Patterns & Conventions

All implementations follow established codebase patterns:
- **Components:** Render functions (`def render_X(...) -> None`) with `__all__` exports
- **Error handling:** None/empty guard -> schema validation -> try/except with logger.warning
- **Charts:** Plotly `go.Figure()` + `ui.plotly(fig).classes("w-full")`
- **Pydantic models:** For all configuration (OverlayConfig, HealthThresholds)
- **Type hints:** Full typing, `TYPE_CHECKING` for heavy imports
- **Logging:** `logger = logging.getLogger(__name__)` with structured extras
- **Timers:** `ui.timer` + `ClientLifecycleManager` cleanup
- **Security:** Ownership verification for all user-scoped data access

---

## Definition of Done

- [ ] All 4 tasks implemented
- [ ] Config editor functional with JSON validation
- [ ] Backtest comparison with equity curve overlay and tracking error
- [ ] Live vs backtest overlay with alert levels
- [ ] Data health monitoring on dashboard
- [ ] Unit tests > 85% coverage per module
- [ ] E2E tests pass
- [ ] Code reviewed and approved
- [ ] No new mypy errors (`mypy --strict`)

---

**Last Updated:** 2026-02-09
**Status:** PLANNING
