# P4T2 Track 1 Implementation Plan — T1.7 (TAQ Storage) & T1.8 (TAQ Query)

**Version:** 2.0 (regenerated with Codex+Gemini review fixes)  
**Owner:** Developer B  
**Timeline:** 8 working days (3–5d T1.7 + 2–3d T1.8)  
**Scope:** WRDS TAQ ingest, local storage, PIT versioning, unified query interface

---

## 1) Scope & Goals
- **T1.7 – TAQ Storage (WRDS → local Parquet/DuckDB)**
  - Reuse **SyncManager** extensibility instead of bespoke loops; implement a TAQ-specific `SyncJob`/`SyncTask` using existing pipelines.
  - Persist manifests under `data/manifests/taq` and snapshots under `data/snapshots/taq` using `ManifestManager` + `DatasetVersionManager`.
  - Two tiers: **aggregates** (1m bars, daily RV, spread stats) and **samples** (full tick for sample days).
  - Atomic writes, disk guards (80/90/95% with 2× file-size safety multiplier), quarantine for failures, retention cleanup.
- **T1.8 – TAQ Query**
  - Add `TAQLocalProvider` implementing the shared **DataProvider protocol** (see existing local providers).
  - Default execution engine: **DuckDB** (in-process) with option for Polars scans.
  - PIT-safe queries via `DatasetVersionManager` snapshots; schema validation through `DataValidator`/`SchemaRegistry`.
  - Integration into `UnifiedFetcher` registry as provider key `"taq"`.
  - CLIs: `scripts/taq_sync.py`, `scripts/taq_query.py` with WRDS-gated options and observability hooks.

Success criteria:
- Sync SLO: ≤30 min for SP500 × 1 month aggregates; queries <2s for 1 symbol × 1 day minute bars.
- PIT reproducibility: `as_of T` equals `as_of T+1` for identical snapshot tags.
- Tests: 90%+ unit coverage on new modules; integration + perf/soak paths runnable in CI (WRDS-dependent tests gated/skipped without creds).

---

## 2) Dependencies & Gates
- **Gate: WRDS TAQ access verified** (`SELECT * FROM taq.master LIMIT 1`) before implementation; otherwise pivot to raw TAQ + local aggregation.
- **P4T1 infra:** `SyncManager`, `AtomicFileLock`, `ManifestManager`, `DatasetVersionManager`, `SchemaRegistry`, `DataValidator`, `locking.py`, `wrds_client.py`, `unified_fetcher.py`.
- **Disk space:** ≥50GB free; guards at 80/90/95% usage with 2× incoming-size multiplier (block if predicted >95%).
- **Schema sources:** Define TAQ schemas in `SchemaRegistry` + `DataValidator` (minute bars, RV, spreads, tick).
- **Observability:** Structured logging + metrics hooks consistent with P4T1 (timers, row counts, file sizes, lock wait).

---

## 3) Architecture & Storage Layout
```
data/
  taq/
    aggregates/
      1min_bars/YYYYMM.parquet
      daily_rv/YYYYMM.parquet
      spread_stats/YYYYMM.parquet
    samples/YYYY-MM-DD/<SYMBOL>.parquet
    tmp/                         # staging for atomic writes
    quarantine/                  # failed writes
  manifests/taq/manifest.json    # ManifestManager root
  snapshots/taq/<tag>/           # DatasetVersionManager snapshots (hardlinks/copies)
```
- **Atomic write path:** tmp → fsync → atomic rename → manifest update → snapshot (if requested).
- **Locking:** `AtomicFileLock` per partition (`taq_sync_{tier}_{partition}.lock`); readers lock-free.
- **Disk guards:** Predict total write size (row_count × row_width × 2× multiplier) before start; abort if >95%, warn at 80/90.
- **Schema validation:** `DataValidator` checks schema + non-null cols; drift raises `SchemaDriftError`.
- **Snapshot safety:** Snapshots reference finalized files only; no temp/quarantine inclusion.

---

## 4) Module/Class/Function Checklist

### 4.1 libs/data_providers/taq_storage.py (built on SyncManager extension)
- **New Sync task definitions**
  - `TAQAggregatesSyncTask(SyncTask)`: params (symbols, start_date, end_date, incremental, tier="aggregates", dataset="1min_bars|daily_rv|spread_stats")  
  - `TAQSampleSyncTask(SyncTask)`: params (sample_date, symbols, tier="samples")
- **Factory / registration**
  - Register TAQ tasks with `SyncManager` (e.g., `SyncManager.register_task("taq_aggregates", TAQAggregatesSyncTask)`).
- **Helper functions**
  - `_build_wrds_query(dataset, symbols, start_date, end_date, chunk_size)`  
  - `_target_path(dataset, partition)` → month-based path
  - `_estimate_bytes(rows, schema)` for disk guard (×2 multiplier)
- **Retention**
  - `cleanup(retention_days: int | None = None) -> int` (reuse SyncManager cleanup utilities).
- **Observability**
  - Emit metrics: sync duration, rows, bytes, spill-to-disk, lock wait; structured logs include `component=taq_sync`.

### 4.2 libs/data_providers/taq_query_provider.py
- `class TAQLocalProvider(DataProvider):`
  - `__init__(self, root: Path = Path("data/taq"), snapshot_root: Path = Path("data/snapshots/taq"), engine: Literal["duckdb","polars"]="duckdb")`
  - `fetch_minute_bars(symbols: list[str], start_date: date, end_date: date, as_of: date | None = None) -> pl.DataFrame`
  - `fetch_realized_volatility(symbols: list[str], start_date: date, end_date: date, window: int = 5, as_of: date | None = None) -> pl.DataFrame`
  - `fetch_spread_metrics(symbols: list[str], start_date: date, end_date: date, as_of: date | None = None) -> pl.DataFrame`
  - `fetch_ticks(sample_date: date, symbols: list[str]) -> pl.DataFrame` (reads samples tier)
- PIT behavior: if `as_of`, resolve snapshot via `DatasetVersionManager.get_snapshot(tag)` → point paths to snapshot; otherwise latest.
- Validation: call `DataValidator.validate(df, schema="taq_1min_bars")` etc.
- Engine choice:
  - DuckDB default: `duckdb.query("SELECT * FROM parquet_scan(...) WHERE ...")`
  - Polars fallback: `pl.scan_parquet(...).filter(...).collect()`
- Observability: timers + row counts; log `as_of_tag`, `engine`, `partitions_scanned`.

### 4.3 libs/data_providers/unified_fetcher.py
- Add registry entry: `"taq": TAQLocalProvider`.
- Ensure provider wiring follows existing DataProvider protocol.

### 4.4 Schema additions (libs/data_quality/schema.py / validation.py)
- Register schemas:
  - `taq_1min_bars`: ts (datetime[ns, UTC]), symbol, open, high, low, close, volume (int64), vwap (float), date (date)
  - `taq_daily_rv`: date, symbol, rv_5m, rv_30m, obs
  - `taq_spread_stats`: date, symbol, qwap_spread, ewas, quotes, trades
  - `taq_ticks`: ts, symbol, bid, ask, bid_size, ask_size, trade_px, trade_size, cond
- DataValidator: enforce non-null on ts/symbol/price fields; monotonic date range checks.

### 4.5 Manifest & Snapshot locations
- `ManifestManager(root=Path("data/manifests/taq"))`
- `DatasetVersionManager(root=Path("data/snapshots/taq"))`
- Version tag pattern: `taq_{dataset}_{YYYYMMDD}` for aggregates; `taq_samples_{date}` for ticks.

---

## 5) CLI Design

### scripts/taq_sync.py
- Commands (Click):
  - `sync-aggregates --symbols SP500|AAPL,MSFT --dataset 1min_bars|daily_rv|spread_stats --start-date YYYY-MM-DD --end-date YYYY-MM-DD --incremental/--full`
  - `sync-sample --date YYYY-MM-DD --symbols AAPL,MSFT`
  - `cleanup --retention-days 365`
- Behavior:
  - Activates venv check; verifies WRDS credentials; runs disk guard preflight.
  - Delegates to `SyncManager.run("taq_aggregates", ...)` or `SyncManager.run("taq_sample", ...)`.
  - Optional `--snapshot` flag creates version tag post-sync.
  - Logging/metrics emitted (duration, rows, bytes, exit code).

### scripts/taq_query.py
- Commands:
  - `minute-bars --symbols AAPL --start 2024-01-15 --end 2024-01-15 --as-of 2024-01-16 --engine duckdb --output out.parquet`
  - `realized-volatility --symbols AAPL --start 2024-01-01 --end 2024-01-31 --window 5 --as-of 2024-02-01`
  - `spread-metrics --symbols SPY --start 2024-01-01 --end 2024-01-31`
  - `ticks --date 2024-01-15 --symbols AAPL,MSFT`
- Options:
  - `--engine` default `duckdb`; `--as-of` for PIT; `--no-validate` to skip schema validation (debug only).
  - `--metrics` toggle to print timing/row stats.

---

## 6) Versioning & PIT Integration
- After successful sync, call `DatasetVersionManager.create_snapshot(tag=tag, source_paths=[...], metadata={symbols, dataset, date_range, tier})`.
- Snapshots live under `data/snapshots/taq/<tag>`; use hardlinks where supported to save space.
- PIT queries resolve snapshot path; reject if snapshot missing; snapshots only created after manifest update to ensure consistency.
- `as_of` date maps to nearest snapshot on/after date; explicit tag option for deterministic runs.

---

## 7) UnifiedFetcher Integration
- Register `TAQLocalProvider` in `PROVIDER_REGISTRY`.
- Add routing cases for datasets: `"minute_bars"`, `"daily_rv"`, `"spread_stats"`, `"ticks"`.
- Ensure consistent argument names with CRSP/Compustat providers (start_date/end_date/symbols/as_of).
- Update any factor builders relying on unified interface to accept `"taq"` dataset key.

---

## 8) Test Plan

### Unit (target ≥90% on new modules)
- `tests/libs/data_providers/test_taq_sync_tasks.py`
  - atomic write via SyncManager (tmp→rename), manifest updated, tmp cleaned
  - disk guard aborts when predicted usage >95% (with 2× multiplier)
  - schema drift detection via DataValidator
- `tests/libs/data_providers/test_taq_query_provider.py`
  - schema validation passes/fails appropriately
  - PIT routing uses snapshots, not live data
  - DuckDB vs Polars engine parity

### Integration
- `tests/integration/test_taq_sync_query_flow.py`
  - WRDS-gated (skip if no creds): sync 1 day aggregates, create snapshot, query minute bars, verify checksum/row_count vs manifest.
  - Snapshot safety: delete live file; PIT still works via snapshot path.

### Performance / Soak
- `tests/perf/test_taq_sync_slo.py` (marker `perf`, manual run): SP500 × 1 month <30m.
- `tests/soak/test_taq_query_concurrency.py`: 10 parallel queries lock-free, latency <2s p50.

### Test gating
- Use `pytest.importorskip("wrds")` or env flag `WRDS_AVAILABLE` to gate live WRDS tests.
- CI runs unit + offline integration with fixtures; live WRDS tests in nightly.

Fixtures:
- Synthetic Parquet fixtures for minute bars/spreads; snapshot fixture under `tests/fixtures/taq_snapshots`.
- DuckDB temp db fixture for query provider.

---

## 9) Sequencing & Timeline (8 days)
1. **Day 1:** Verify WRDS TAQ access (gate); add schemas to `SchemaRegistry`/`DataValidator`; set up manifests/snapshots roots.
2. **Day 2:** Implement TAQ `SyncTask` classes + registration with SyncManager; disk guard logic.
3. **Day 3:** Wire CLIs for sync; add observability hooks; retention cleanup.
4. **Day 4:** Unit tests for sync tasks + disk guard + schema drift; offline fixtures.
5. **Day 5:** Integration (WRDS-gated) + snapshot safety; create initial snapshot.
6. **Day 6:** Implement `TAQLocalProvider` (DuckDB+Polars) + validation; UnifiedFetcher registration.
7. **Day 7:** Query CLI + PIT support; unit tests for provider.
8. **Day 8:** Perf/soak scripts, documentation touch-ups, run `make ci-local`.

---

## 10) Risks & Mitigations
- **WRDS TAQ API unavailable** → Pivot to raw TAQ + local aggregation; adjust timeline + storage estimate.
- **Schema drift** → DataValidator enforcement; fail fast with actionable error.
- **Disk exhaustion** → Guards at 80/90/95% with 2× multiplier; abort >95%, warn at 80/90.
- **Performance regressions** → Month-partitioned files; DuckDB scan with predicate pushdown; perf test baseline.
- **Snapshot corruption** → Snapshot only after manifest success; use hardlink copy; validation check on creation.
- **Concurrent syncs** → Per-partition locks; rely on SyncManager lock registry.

---

## 11) Acceptance Criteria
- `SyncManager`-based TAQ tasks land data under `data/taq/*` with manifests in `data/manifests/taq`.
- Snapshots created in `data/snapshots/taq` and usable for PIT queries; PIT results stable across runs.
- CLIs operational: sync (aggregates + samples), query (minute bars, RV, spreads, ticks), cleanup.
- Disk guard enforcement demonstrable; warnings at 80/90%, hard block at predicted >95%.
- Schema validation active via DataValidator; failures logged with field diffs.
- UnifiedFetcher returns TAQ datasets; DuckDB is default engine; Polars supported.
- Tests: unit + offline integration pass in CI; WRDS-gated tests pass when creds supplied.
- Observability: logs include component, dataset, tier, rows, bytes, duration, lock_wait, snapshot_tag.

---

## 12) Appendices
- **WRDS query templates:** parameterized SELECTs for aggregates and ticks (reuse WRDSClient chunking).  
- **Size estimates:** SP500 × 1 month aggregates ≈ 5GB compressed; samples ≈ 10–20GB/day for top symbols.  
- **CLI quick ref:** see section 5.  
- **Review checklist:** manifest updated, snapshot created, schema validated, disk guard checked, locks released, metrics emitted, PIT query verified.

---

End of plan.
