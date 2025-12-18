# ADR-0025: Backtest UI-Worker Contract

## Status
Accepted

## Context
The Backtest Web UI (T5.3) needs to communicate with the backtest worker (T5.4) through a well-defined contract. The UI submits jobs, monitors progress, and retrieves results. This ADR defines the contract between these components.

## Decision

### Job Submission Contract

Jobs are submitted via `BacktestJobQueue.enqueue()` with the following config:

```python
@dataclass
class BacktestJobConfig:
    alpha_name: str          # Must exist in CANONICAL_ALPHAS registry
    start_date: date         # Backtest start (inclusive)
    end_date: date           # Backtest end (inclusive)
    weight_method: str       # One of: "zscore", "quantile", "rank"
```

### Job Status Vocabulary

The database uses these status values (NOT RQ vocabulary):
- `pending` - Job queued, not yet started
- `running` - Worker is processing the job
- `completed` - Job finished successfully
- `failed` - Job failed (includes timeouts)
- `cancelled` - Job cancelled by user

### Progress Tracking Contract

Workers emit progress to Redis at key `backtest:progress:{job_id}`:

```json
{
  "pct": 75,                    // 0-100 integer
  "current_date": "2023-06-15", // Current processing date
  "message": "Computing IC..."  // Optional status message
}
```

Progress is emitted at most every 30 seconds to avoid Redis spam.

### Result Storage Contract

Completed jobs store results at `result_path` with this structure:
```
{result_path}/
├── daily_signals.parquet     # date, permno, signal
├── daily_weights.parquet     # date, permno, weight
├── daily_ic.parquet          # date, ic, rank_ic
├── daily_portfolio_returns.parquet  # date, return
└── summary.json              # Metrics + reproducibility metadata
```

### Sync vs Async Pool Usage

- **UI (Streamlit)**: Uses sync `ConnectionPool` for `BacktestJobQueue`
- **Worker (background)**: Uses sync `ConnectionPool` for job processing
- **Web Console (other pages)**: Uses async `AsyncConnectionAdapter`

This is intentional - BacktestJobQueue's `with pool.connection():` syntax requires sync connections.

### RBAC Permissions

- `VIEW_PNL` - Required to access Backtest Manager page
- `EXPORT_DATA` - Required to export backtest results (CSV/JSON)

### Auth Stub for Development

While T6.1 (OAuth2) is pending, use `BACKTEST_DEV_AUTH=true` to enable dev stub:
- Sets `role="operator"` and `strategies=["*"]` in session
- CI governance tests prevent this in prod/staging

## Consequences

### Positive
- Clear separation between UI and worker responsibilities
- Redis-based progress enables real-time updates without polling DB
- Parquet artifacts are space-efficient and fast to load
- RBAC gating prevents unauthorized access

### Negative
- Sync/async pool split adds complexity
- Dev auth stub requires cleanup when T6.1 ships

### Risks
- OAuth2 sessions don't set role/strategies yet (T6.1 gap)
- Export buttons hidden for authenticated users until T6.1

## References
- T5.1: Job Queue Implementation
- T5.2: Result Storage Implementation
- T5.3: Backtest Web UI (this task)
- T5.4: Background Worker (future)
- T6.1: OAuth2 Authentication
