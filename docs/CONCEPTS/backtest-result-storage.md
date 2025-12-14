# Backtest Result Storage

Concise reference for the hybrid Postgres + Parquet storage that powers backtest retrieval, audits, and retention.

## Purpose
- Persist reproducible backtest outputs beyond worker lifetime and Postgres restart.
- Provide fast, schema-stable access for dashboards, APIs, and troubleshooting without rerunning compute.
- Enforce explicit failure modes when metadata or artifacts go missing (no silent `None` returns).
- Bound storage growth with retention that prefers deleting disk artifacts before DB rows.

## High-Level Architecture
- **Metadata in Postgres (`backtest_jobs`):** lifecycle state, idempotency key (`job_id`), alpha + date range, summary metrics, reproducibility fields, `result_path`.
- **Artifacts on disk (`data/backtest_results/{job_id}/`):** Parquet bundle + `summary.json` produced by the worker.
- **Access Layer:** `BacktestResultStorage` (sync, psycopg3 + polars) exposes read/list/cleanup operations. It is used by the web tier and workers; no async dependency chain.
- **Idempotency:** `job_id` is SHA256 hash of config+user (see `BacktestJobConfig.compute_job_id`) and is the primary key for both DB row and directory name.

## Data Flow
1. Worker completes backtest, writes Parquet bundle + `summary.json`, and updates `backtest_jobs.result_path` and metrics.
2. Web/API calls `get_result(job_id)` → fetches DB row → loads Parquet → reconstructs `BacktestResult`.
3. Ops/cron runs `cleanup_old_results()` → removes Parquet first → deletes terminal DB rows → commits.
4. Observability dashboards use `list_jobs()` for filtered listings (paging via limit/offset).

## Key Classes (exports live in `libs/backtest/__init__.py`)
- `BacktestResultStorage` (`libs/backtest/result_storage.py`): synchronous DAO for loading results, listing jobs, and retention cleanup.
- `BacktestJob` (`libs/backtest/models.py`): dataclass mirror of `backtest_jobs` schema; includes status, metrics, reproducibility fields, retry_count.
- `row_to_backtest_job`: mapper from psycopg `dict_row` to `BacktestJob`.

## API Methods (BacktestResultStorage)
- `get_result(job_id: str) -> BacktestResult`  
  - SELECT row via `dict_row`; raises `JobNotFound` if absent.  
  - Raises `ResultPathMissing` when `result_path` null/empty or directory absent.  
  - Loads Parquet/JSON bundle, recomputes turnover/coverage when missing, and falls back to signal extents for `start_date`/`end_date`.
- `list_jobs(created_by=None, alpha_name=None, status=None, limit=100, offset=0) -> list[dict]`  
  - Builds simple WHERE clauses; ordered by `created_at DESC`.  
  - Returns primitive dicts with ISO-formatted datetimes for JSON APIs.
- `cleanup_old_results(retention_days=90) -> int`  
  - Cutoff = `datetime.now(UTC) - retention_days`.  
  - Filters terminal statuses only (`completed`, `failed`, `cancelled`).  
  - Deletes Parquet directories first with `shutil.rmtree`, then issues DELETE; commits once.

## Error Handling
- `JobNotFound`: DB row missing for supplied `job_id`.
- `ResultPathMissing`: row exists but `result_path` null/empty or directory not found on disk.
- `ValueError`: thrown when `summary.json` missing or reproducibility fields absent (`snapshot_id`, `dataset_version_ids`).

## Retention Policy
- Default window: 90 days (`BacktestResultStorage.DEFAULT_RETENTION_DAYS`).
- Only terminal jobs are eligible; pending/running jobs are never deleted to avoid orphaning active work.
- Parquet deletion precedes DB DELETE to guarantee disk cleanup even if SQL fails; deletion count is returned for observability.

## Parquet Bundle Schema (authoritative)
Root: `data/backtest_results/{job_id}/`

- `daily_signals.parquet`  
  - Columns: `permno` (int), `date` (date), `signal` (float).  
  - One row per symbol-day signal used for IC/coverage.
- `daily_weights.parquet`  
  - Columns: `permno` (int), `date` (date), `weight` (float).  
  - Derived from signals using selected weight method; feeds turnover.
- `daily_ic.parquet`  
  - Columns: `date` (date), `ic` (float), `rank_ic` (float).  
  - Supports mean IC/ICIR computation; missing summary fields are recomputed from here.
- `summary.json`  
  - Required: `snapshot_id` (str), `dataset_version_ids` (dict[str,str]) for reproducibility.  
  - Optional (recomputed if absent): `mean_ic`, `icir`.  
  - Additional fields: `hit_rate`, `coverage`, `long_short_spread`, `decay_half_life`, `weight_method`. Missing coverage/start/end dates fall back to signal extents.

## Lifecycle & Idempotency Notes
- `job_id` is the stable join key across Redis, Postgres, and filesystem; never mutate once created.
- Writers must ensure Parquet and `summary.json` are flushed before updating DB status to `completed` to avoid `ResultPathMissing` on readers.
- Coverage/start/end fallbacks prevent API failures when older rows lack these fields.
- ICIR recomputation guards against legacy runs where it was not persisted.

## Query & Pagination Patterns
- Prefer `list_jobs(limit=100, offset=N)` for UIs; keep limit modest to avoid wide scans.  
- Filter by `created_by` and `alpha_name` to narrow scope; status filter keeps dashboards responsive.
- All timestamps returned in ISO8601 with timezone when present; date fields returned as strings.

## Operational Guidance
- Activate `.venv` before running scripts that touch storage: `source .venv/bin/activate`.
- Retention should run during off-peak; never parallelize `make ci-local` with cleanup tasks per CI-local single-instance rule.
- Do not bypass review gates (`ZEN_REVIEW_OVERRIDE`) for storage changes—seek human approval if zen-mcp unavailable.
- For corruption investigations: check DB row, then `summary.json`, then Parquet files; re-run backtest if reproducibility metadata missing.

## Tests & Expectations
- Unit coverage lives in `tests/libs/backtest/test_result_storage.py`; heavy deps (polars, psycopg) are optional—tests skip if missing.
- Acceptance criteria require `ValueError` on missing reproducibility metadata and idempotent differentiation between missing row vs missing path.
- Round-trip parity is validated via BacktestResult reconstruction (Parquet → BacktestResult) with consistent metrics.

## Schema Snapshot (Postgres)
- Primary key: `job_id` (VARCHAR(32)), unique idempotency key.
- Core fields: `alpha_name`, `start_date`, `end_date`, `weight_method`, `config_json`.
- Execution metadata: `status`, `progress_pct`, `started_at`, `completed_at`, `job_timeout`, `worker_id`, `retry_count`.
- Metrics: `mean_ic`, `icir`, `hit_rate`, `coverage`, `long_short_spread`, `average_turnover`, `decay_half_life`.
- Reproducibility: `snapshot_id`, `dataset_version_ids` (JSONB dict).
- Artifacts pointer: `result_path` (filesystem location of Parquet bundle).

## Common Failure Modes & Responses
- **Missing DB row:** raise `JobNotFound`; caller should present "job not found" to user or trigger rerun.
- **Missing/empty result_path:** raise `ResultPathMissing`; reconcile by re-running or restoring artifacts.
- **Missing `summary.json` or reproducibility keys:** raise `ValueError`; repair by rerunning backtest to regenerate reproducible metadata.
- **Heartbeat loss (outside this layer):** job watchdog marks job failed; storage reads will still succeed if artifacts exist.
