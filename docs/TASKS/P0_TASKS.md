# P0 Tickets (0–45 days)

## T1 — Data ETL with CA + Freshness + Quality Gate
- Output: adjusted parquet; quarantine on outliers; freshness error
- Tests: stale data raises; outlier quarantines

## T2 — Baseline Strategy + MLflow
- Output: trained model, metrics logged

## T3 — Signal Service (Model Registry Poll)
- Output: weights for TRADABLE_SYMBOLS only

## T4 — Execution Gateway (Idempotent + DRY_RUN)
- Output: POST /orders returns dry_run then real paper submit

## T5 — Position Tracker
- Output: positions table upserts on fills; risk reads it

## T6 — `paper_run.py` Orchestrator + P&L
- Output: one command completes full pipeline

### Acceptance: See Appendix A in main plan
