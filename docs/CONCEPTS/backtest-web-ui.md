# Backtest Web UI

Concise reference for the Streamlit-based backtest management interface that enables job submission, progress monitoring, and result visualization.

## Purpose
- Provide operators with a web interface to submit, monitor, and analyze backtest jobs.
- Display real-time progress via Redis-based tracking without polling the database.
- Enforce RBAC permissions (VIEW_PNL for access, EXPORT_DATA for exports).
- Support development mode via auth stub while T6.1 (OAuth2) completes.

## High-Level Architecture
- **Page Location:** `apps/web_console/pages/backtest.py` renders the main Backtest Manager page.
- **Components:** Modular components in `apps/web_console/components/` handle form input, charts, and results display.
- **Auth Layer:** `apps/web_console/auth/backtest_auth.py` provides `@backtest_requires_auth` decorator with dev stub support.
- **Connection Pools:** `apps/web_console/utils/sync_db_pool.py` provides sync pools required by `BacktestJobQueue.enqueue()`.
- **Feature Flag:** `FEATURE_BACKTEST_MANAGER` env var controls page visibility in navigation.

## Data Flow
1. User submits form → `BacktestJobConfig` created → `BacktestJobQueue.enqueue()` inserts job.
2. Worker picks up job, emits progress to Redis at `backtest:progress:{job_id}`.
3. UI polls Redis for progress (progressive backoff: 2s→5s→10s→30s).
4. On completion, UI fetches result from `BacktestResultStorage.get_result()`.
5. Charts render using Polars DataFrames from Parquet artifacts.

## Key Components

### Auth Stub (`apps/web_console/auth/backtest_auth.py`)
- `@backtest_requires_auth`: Decorator that switches between dev stub and real OAuth2.
- **Dev mode** (`BACKTEST_DEV_AUTH=true`): Sets `role="operator"`, `strategies=["*"]` in session.
- **Production mode**: Delegates to `@requires_auth` from core auth module.
- **T6.1 Marker:** Existence of this file indicates T6.1 gap; delete when OAuth2 sets role/strategies.

### Sync DB Pool (`apps/web_console/utils/sync_db_pool.py`)
- `get_sync_db_pool()`: Cached `psycopg_pool.ConnectionPool` for BacktestJobQueue.
- `get_sync_redis_client()`: Cached sync Redis client for progress tracking.
- `get_job_queue()`: Context manager returning configured `BacktestJobQueue`.
- **Async Separation:** BacktestJobQueue uses `with pool.connection():` which requires sync pools; other web console pages may use async adapters.

### Form Component (`apps/web_console/components/backtest_form.py`)
- `get_available_alphas()`: Returns canonical alpha names from alpha library.
- `render_backtest_form()`: Streamlit form with alpha selector, date range, weight method.
- **Weight Methods:** `zscore`, `quantile`, `rank` (must match `BacktestJobConfig` expectations).
- Returns `BacktestJobConfig` on submit or `None` if not submitted.

### Visualization Components
- `equity_curve_chart.py`: Cumulative return line chart with Plotly.
- `drawdown_chart.py`: Drawdown area chart (negative values filled).
- `ic_timeseries_chart.py`: IC and Rank IC with 20-day rolling mean overlay.
- `backtest_results.py`: Metrics summary (mean IC, ICIR, hit rate, coverage, turnover) plus export buttons.

### Main Page (`apps/web_console/pages/backtest.py`)
- `render_backtest_page()`: Entry point decorated with `@backtest_requires_auth`.
- `_get_user_with_role()`: Wrapper that adds role/strategies from session state (workaround for T6.1 gap).
- `get_poll_interval_ms()`: Progressive polling backoff logic.
- `get_user_jobs()`: Fetches jobs from DB, enriches with Redis progress.
- **Status Vocabulary:** `pending`, `running`, `completed`, `failed`, `cancelled` (NOT RQ vocabulary).

## RBAC Permissions
- **VIEW_PNL:** Required to access Backtest Manager page.
- **EXPORT_DATA:** Required for CSV/JSON export buttons.
- Export buttons are hidden (with info message) for users lacking permission.

## Progressive Polling Strategy
Reduces server load while maintaining responsive UX:

| Elapsed Time | Poll Interval |
|--------------|---------------|
| 0-30s        | 2 seconds     |
| 30-60s       | 5 seconds     |
| 60-300s      | 10 seconds    |
| 300s+        | 30 seconds    |

Implemented via `streamlit-autorefresh` with `get_poll_interval_ms()`.

## Redis Progress Contract
Workers emit progress to `backtest:progress:{job_id}`:

```json
{
  "pct": 75,
  "current_date": "2023-06-15",
  "message": "Computing IC..."
}
```

- `pct`: 0-100 integer progress percentage.
- `current_date`: Optional current processing date.
- `message`: Optional status message.
- Missing progress defaults to 0%.

## Configuration
- `FEATURE_BACKTEST_MANAGER`: Set to truthy value (`1`, `true`, `yes`, `on`) to show page in navigation.
- `BACKTEST_DEV_AUTH`: Set to `true` to enable dev auth stub (auto-authenticates as operator).
- `REDIS_URL`: Full Redis URL, or use `REDIS_HOST`, `REDIS_PORT`, `REDIS_DB` individually.
- `DB_*`: Standard database connection vars used by sync pool.

## Dependencies
Added to `apps/web_console/requirements.txt`:
- `streamlit-autorefresh>=1.0.1`: Progressive polling for job status.
- `psycopg-pool>=3.1.0`: Sync connection pool for BacktestJobQueue.
- `polars>=1.0.0,<2.0.0`: DataFrame operations for result display.

## Error Handling
- **Invalid status filter:** `get_user_jobs()` raises `ValueError` if status not in vocabulary.
- **Missing Redis progress:** Defaults to 0% (no error raised).
- **Auth failure:** Redirects to login page via `@backtest_requires_auth`.
- **Missing export permission:** Shows info message instead of export buttons.

## Governance & Testing

### CI Governance Tests
Located in `tests/apps/web_console/test_auth_governance.py`:
- Verify dev auth stub is disabled in CI (`BACKTEST_DEV_AUTH` unset).
- Prevent prod/staging from using auth stub.
- Validate T6.1 marker file consistency.

### Unit Tests
- `test_backtest_page.py`: Form validation, visualization rendering, polling logic.
- `test_backtest_job_status.py`: Job submission, status tracking, Redis progress integration.
- `test_auth_enforcement.py`: OAuth2 enforcement and RBAC permission checks.

## T6.1 Migration Path
When OAuth2 (T6.1) is complete and sessions include role/strategies:
1. Delete `apps/web_console/auth/backtest_auth.py`.
2. Update `pages/backtest.py` to use `@requires_auth` directly.
3. Remove `_get_user_with_role()` wrapper.
4. Delete T6.1 marker governance tests.

## Operational Guidance
- Activate `.venv` before running the web console: `source .venv/bin/activate`.
- Feature flag allows gradual rollout without code changes.
- Dev auth stub should NEVER be enabled in production (CI governance tests enforce this).
- Monitor Redis memory if many concurrent jobs emit frequent progress updates.

## Common Failure Modes & Responses
- **Page not visible:** Check `FEATURE_BACKTEST_MANAGER` is set to truthy value.
- **Auth redirect loop:** Verify OAuth2 config or enable `BACKTEST_DEV_AUTH` for local dev.
- **No export buttons:** User lacks `EXPORT_DATA` permission; contact admin for role upgrade.
- **Progress stuck at 0%:** Worker may not be running or Redis connection failed.
- **"Invalid statuses" error:** Code passed RQ vocabulary instead of database vocabulary.

## Related ADRs
- **ADR-0025:** Backtest UI-Worker Contract (status vocabulary, progress format, result storage).
