# Data Page UI Optimization Plan (Gemini-Informed)

Date: 2026-05-03
Branch: `feature/data-page-ui-optimization-plan`
Base: latest `master` at PR #218 merge commit `c7fb3526`
Scope: `apps/web_console_ng/pages/data_management.py`, related `/data/*` pages, and `libs/web_console_services/*data*`

## Inputs Used

- Latest `master` after merged PR #218, "[codex] Harden Alpaca SIP raw semantics".
- GitHub PR #218 metadata and merge summary.
- Direct Gemini CLI discussion (`gemini -p`) focused on data acquisition, data utilization, and UI optimization.
- Local inspection of:
  - `apps/web_console_ng/pages/data_management.py`
  - `apps/web_console_ng/pages/data_source_status.py`
  - `apps/web_console_ng/pages/data_coverage.py`
  - `apps/web_console_ng/pages/data_inspector.py`
  - `apps/web_console_ng/pages/sql_explorer.py`
  - `libs/web_console_services/data_sync_service.py`
  - `libs/web_console_services/data_explorer_service.py`
  - `libs/web_console_services/data_quality_service.py`
  - `libs/web_console_services/data_source_status_service.py`
  - `libs/web_console_services/sql_explorer_service.py`
  - `libs/data/data_providers/registry.py`
  - `docs/ADRs/ADR-0041-alpaca-sip-historical-provider.md`
  - `docs/ADRs/ADR-0042-hybrid-crsp-sip-research-provider.md`
  - `docs/TASKS/2026-04-28-alpaca-sip-data-source-plan.md`

## PR #218 Constraints To Preserve

PR #218 changed the data surface in ways the UI must make obvious:

1. Alpaca SIP canonical sync stores raw daily OHLC.
2. `adj_close` and `ret` remain null until an explicit read-time adjustment layer exists.
3. Raw SIP-priced simple backtests fail closed instead of deriving split-unsafe returns from raw close.
4. Provider provenance/signature metadata is stricter and must remain reversible.
5. Alpaca SIP daily bars and corporate actions are local parquet datasets with manifests.
6. SQL Explorer already uses manifest-pinned Alpaca SIP paths; the rest of the data UI should not drift from that source of truth.
7. Alpaca SIP canonical datasets require manifests for `/data` previews, readiness, SQL handoff, and backtest handoff. Fallback parquet discovery without manifests is untrusted local inspection only and must not carry readiness or provenance claims.

## Current Backend Reality

The current data UI is useful but fragmented:

1. `/data` is a large tabbed page with Data Sync, Data Explorer, and Data Quality sections. The module already has a TODO to split sections into components.
2. `DataSyncService` is still mostly placeholder data, but it now reads Alpaca SIP manifests for `alpaca_sip` status.
3. `DataExplorerService` still returns placeholder dataset metadata and empty query/preview results.
4. `DataQualityService` still uses mock/in-memory results and acknowledgments.
5. `/data/source-status` uses `DataSourceStatusService`; Alpaca SIP status is manifest-backed.
6. `/data/sql-explorer` is the most production-like data UI today. It safely creates DuckDB views over authorized local parquet tables and already maps `alpaca_sip_daily` and `alpaca_sip_corp_actions` through manifest-aware path resolution.
7. `/data/coverage` and `/data/inspector` are useful but disconnected from manifest/provenance state and the newer provider registry.
8. Provider metadata is centralized in `libs/data/data_providers/registry.py`, including production readiness, history start, feed parity, survivorship safety, corporate-action support, and default adjustment mode.
9. `ProviderSpec` is not a complete dataset catalog. It covers historical provider identities such as CRSP, yfinance, Alpaca SIP, hybrid, and auto, but not every UI dataset/table family such as Compustat, TAQ, or Fama-French.

## Product Direction

Make `/data` the operator and researcher entry point for the complete local-data lifecycle:

1. See what data exists and whether it is trustworthy.
2. Acquire or refresh data with preflight checks.
3. Inspect exactly which manifest, files, provider, feed, and adjustment mode are being used.
4. Explore data through safe previews and SQL handoff.
5. Understand whether a dataset is ready for backtests or model research.

This is not a visual redesign for its own sake. The page needs to reduce bad research decisions caused by raw/adjusted confusion, stale manifests, missing corporate actions, or mock UI confidence.

## Information Architecture

Replace the current "three tabs plus separate pages" feel with a dense Dataset Operations Grid. The primary unit of the page is a dataset/table row, not a feature tab.

The old mental model is:

- Data Sync
- Data Explorer
- Data Quality
- Source Status
- Coverage
- SQL Explorer

The target mental model is:

- All authorized datasets/tables in one dense operational grid.
- Fast filters for `Needs Attention`, `Backtest Ready`, `Alpaca SIP`, `Missing Manifest`, `Untrusted`, `Quality Issues`, and `Queryable`.
- A right-side details drawer for the selected row.
- A compact bottom preview/query strip when the selected row is queryable.
- Deep links to `/data/source-status`, `/data/coverage`, `/data/inspector`, and `/data/sql-explorer` remain available, but the main `/data` page is the entry point and summary surface.

### Top Context Ribbon

Show global data state across all authorized datasets:

- Provider readiness: `ok`, `stale`, `error`, `missing`, `unknown`.
- Counts for healthy, stale, missing, untrusted, blocked-for-backtest, and quality-issue rows.
- Oldest/latest manifest age.
- Total local rows.
- Permission-scoped hidden dataset count.
- Issue count with jump links that filter the main grid.

For Alpaca SIP, always show a raw-data badge until an adjustment layer exists:

- `Raw OHLC`
- `canonical_storage_mode: raw`
- `read_time_adjustment_mode: unavailable`
- `adj_close: not available`
- `ret: not available`
- `corp actions: present/missing`

### Main Dataset Operations Grid

Render one row per authorized dataset/table or table group. This grid replaces low-information cards and most top-level tabs.

Column tiers:

- Always visible and pinned: dataset/table name, raw-data pill when canonical storage is raw, local state, manifest status, last sync age, backtest readiness, issue count, and quick actions.
- Visible on wide layouts or when the user enables columns: provider or dataset family, row count, approximate size, date range, symbol count, quality state, canonical storage mode, and read-time adjustment mode.
- Drawer-only by default: manifest path/reference, checksum, schema version, validation details, full provenance fields, detailed quality output, companion-manifest cohesion details, and acquisition job logs.

Grid behavior:

- Keep rows dense and sortable/filterable.
- Pin dataset/table and status columns.
- Use compact badges instead of large cards.
- Keep fixed row height by default. Long values truncate with tooltip disclosure, and full values live in the details drawer.
- Let the top ribbon filters apply directly to grid rows.
- Do not show large prose instructions in the UI; use tooltips and details drawer fields.

### Selected Row Details Drawer

Selecting a grid row opens a right-side drawer with compact sections. The drawer replaces separate low-density status, explorer, quality, readiness, and sync panels.

Sections:

- Summary: provider/dataset family, state, row count, date range, symbol count, readiness.
- Manifest and provenance: manifest version, manifest path/reference, checksum, row count, schema version, validation status, `manifest_id`, `provider_id`, `provider_version`, `source_feed`, `adjustment_mode`, `symbol_set_hash`, sync start/finish times, query/params hash, and sanitized `provider_signature` from the shared allowlist serializer sufficient to reconstruct provenance without relying on a hash alone.
- Alpaca SIP pairing: daily/corporate-actions companion status, date coverage, symbol-set hash, and cohesion warnings.
- Acquisition: preflight controls, estimated calls/bytes, output paths, idempotency key, submit-token expiry/status, and job state. Token values appear only in the immediate preflight response used by submit, not in job state or logs.
- Quality: latest validation, integrity, feed-delta, staleness, and alert state.
- Readiness: workflow-specific blockers and warnings, including stable reason codes.
- Query: safe preview, query templates, and SQL Explorer handoff.

Keep `/data/source-status` as a deep link or redirect while the main `/data` grid becomes the primary path.

### Acquisition Drawer Section

Turn "Manual Sync" from a dataset/reason form into a guarded selected-row acquisition workflow:

- Dataset/provider selection comes from the selected grid row.
- Mode: preflight, incremental sync, targeted backfill, full backfill.
- Inputs by dataset:
  - Alpaca SIP daily: symbols/universe, start date, end date, feed, adjustment policy locked to raw for canonical sync.
  - Alpaca SIP corporate actions: symbols/universe, start date, end date.
  - CRSP/Compustat/TAQ/Fama-French: future adapters as they become available.
- Current Alpaca SIP daily sync capabilities are year-partition oriented. The UI preflight must either round date ranges to supported `start_year`/`end_year` semantics or require a separate targeted-date implementation before exposing arbitrary partial-date sync.
- Preflight output:
  - estimated API calls or file scans
  - disk space estimate
  - expected output paths
  - server-calculated idempotency key, submit token, and token expiry
  - provider/feed/adjustment provenance
  - required credentials or unavailable reason
- Job status with logs, manifest produced, row count, validation result, and consumed/expired preflight status. Job state never shows the submit-token value.

Do not let the UI suggest adjusted SIP bars are being stored. Canonical Alpaca SIP acquisition remains raw.

### Preview And SQL Strip

Replace the mock `DataExplorerService` behavior with a DuckDB-backed, manifest-pinned explorer path:

- Dataset metadata comes from a shared catalog, not duplicated `_SUPPORTED_DATASETS` tuples.
- Preview is scoped to the selected grid row and uses the same table/path resolution as SQL Explorer.
- Alpaca SIP canonical previews and `/data` SQL handoffs require manifest-pinned paths. If only fallback parquet files are discovered, the grid may show `queryable fallback only`, but `/data` disables preview, readiness, and backtest handoff and points users to standalone SQL Explorer local inspection with an explicit untrusted warning.
- Schema view includes physical table, manifest ID, storage path, and provider.
- Preview results include provider/feed/provenance metadata and explicit null-column reason codes for `adj_close` and `ret`.
- Add "Open in SQL Explorer" action that pre-populates a safe query for the selected dataset/table and carries a provenance-bearing handoff payload.
- Provide curated query templates for common questions:
  - latest rows by symbol
  - date coverage by symbol
  - row count by partition year
  - Alpaca SIP corporate actions near a date range
  - daily bars with missing `adj_close`/`ret`

### Quality Drawer Section

Shift Data Quality from mock score cards toward manifest and check outputs:

- Alpaca SIP integrity check results.
- Alpaca feed delta check results.
- Manifest completeness and schema checks.
- Corporate-actions pairing checks.
- Manifest/date/symbol cohesion checks between paired Alpaca SIP datasets.
- Coverage gaps from the same local table mappings used by Explorer.
- Alert acknowledgments persisted server-side before treating them as operational state.

### Readiness Drawer Section

Add a focused "Can I use this data?" view:

- Backtest readiness for each provider.
- Missing conditions that would cause fail-closed behavior.
- History start and requested date compatibility.
- Survivorship safety and PIT support.
- Required manifests for the selected workflow.
- Stable blocker/warning reason codes, including `raw_sip_returns_unavailable`, `alpaca_sip_untrusted_without_manifest`, `alpaca_sip_companion_manifest_stale`, `alpaca_sip_companion_symbol_set_mismatch`, and `crsp_universe_unavailable`.

For Alpaca SIP:

- `alpaca_sip` simple price-only research is allowed only when the workflow does not require `ret`, `adj_close`, or derived returns.
- `alpaca_sip` and `hybrid_crsp_universe_sip_prices` simple backtests are blocked when they would compute returns from raw SIP close. They remain blocked until a read-time adjustment layer exists or trusted adjusted returns are available.
- `hybrid_crsp_universe_sip_prices` requires CRSP universe data plus SIP price data and remains research-only.
- Any UI handoff to backtest must carry role-keyed provider IDs/versions/signatures, manifest IDs/references/checksums, canonical storage modes, read-time adjustment modes, and data role provenance.

## Gemini Notes Incorporated

Gemini highlighted three useful corrections:

1. Treat manifest visibility as the center of the UI, not a side detail.
2. Make raw-versus-adjusted state explicit before adding an adjustment toggle.
3. Avoid a second explorer backend; use or share the SQL Explorer path that already enforces manifest-pinned reads.

The plan follows those points but keeps adjustment as a later phase because PR #218 intentionally leaves `adj_close` and `ret` null until a real read-time layer exists.

## Phased Plan

### Phase 1: Manifest Transparency

Goal: users can tell exactly what local data exists.

Work:

- Add shared manifest summary read model.
- Surface manifest version, checksum, row count, validation status, schema version, age, and file count.
- Surface provider/feed/provenance fields required for replay: `manifest_id`, manifest path/reference, manifest checksum, `provider_id`, `provider_version`, `source_feed`, `adjustment_mode`, `symbol_set_hash`, sync start/finish times, query/params hash, and sanitized `provider_signature` from the shared allowlist serializer.
- Add Alpaca SIP paired-manifest status for daily bars and corporate actions.
- Add Alpaca SIP raw-data badges and null-column reason text in the first operations-grid slice, not only in the later adjustment-design phase.
- Add source detail drawer to `/data`.
- Keep manual refresh guarded by existing RBAC and Redis lock semantics.

Acceptance:

- Alpaca SIP with no manifests shows a clear missing state.
- Alpaca SIP with only one of the two manifests shows which companion dataset is missing.
- Alpaca SIP warns when paired daily/corporate-action manifests exceed the configured cohesion thresholds from the service plan.
- Alpaca SIP always shows raw canonical storage and unavailable read-time adjustment state.
- Alpaca SIP provenance views expose enough sanitized provider/manifest metadata to reconstruct data provenance without relying on hash fields alone.
- Alpaca SIP without manifests does not make readiness or provenance claims. Queryable fallback labeling lands with the real explorer backend in Phase 3.
- Status shown in `/data` and `/data/source-status` matches.

### Phase 2: Acquisition Workflow

Goal: users can safely grab data from the UI without guessing what scripts do.

Work:

- Replace one-field manual sync with a preflight-first form.
- Add structured acquisition DTOs for dataset, date range, symbol source, mode, reason, and dry-run/preflight.
- Route Alpaca SIP requests to the existing sync managers or script-backed job adapter, with supported year-based daily sync semantics documented in the preflight result.
- Show job state, logs, produced manifest IDs, and validation output.
- Enforce single in-flight sync per dataset/scope using a server-calculated idempotency key and submit token.

Acceptance:

- Triggering Alpaca SIP daily or corporate-actions sync requires a reason and a preflight.
- The UI displays raw canonical storage policy before job submission.
- Alpaca SIP daily-bar submissions carry `provider_id`, `source_feed`, `adjustment=raw`, and the preflight idempotency key. Corporate-actions submissions carry provider/source provenance and do not require meaningless adjustment metadata.
- Submit tokens are consumed only by the immediate submit request. Job state and UI logs show token expiry/status but never display the token value.
- Duplicate submissions reuse or reject by idempotency key rather than creating duplicate jobs.

### Phase 3: Real Explorer Backend

Goal: users can utilize local data through safe previews and SQL handoff.

Work:

- Replace `DataExplorerService` mocks with a catalog plus DuckDB-backed preview path.
- Reuse a public SQL Explorer table validation and manifest-aware path resolution API. Do not call private SQL Explorer internals from Data Explorer.
- Add schema, row count, min/max date, symbols count, and partition summary when they are manifest-derived, cached, or bounded by timeout and memory limits.
- Add query templates and "Open in SQL Explorer" handoff.
- Keep exports on the hardened SQL Explorer/export path.

Acceptance:

- Preview rows for `alpaca_sip_daily` come from manifest-pinned parquet.
- Preview rows for `alpaca_sip_corp_actions` come from the paired manifest.
- Dataset availability distinguishes queryable local parquet from trusted manifest-backed availability. Alpaca SIP readiness remains missing/untrusted without manifests even if SQL Explorer can find fallback snapshot files.
- Alpaca SIP fallback parquet without manifests is labeled `queryable fallback only`/untrusted in the grid and drawer.
- Alpaca SIP canonical tables without manifests do not expose `/data` preview, readiness, SQL handoff, or backtest handoff actions; standalone SQL Explorer inspection, if available, is explicitly untrusted and never promoted to readiness.

### Phase 4: Raw/Adjusted UX And Read-Time Adjustment Design

Goal: design future adjusted previews after the raw state is already visible in Phase 1.

Work:

- Keep raw-data warning and adjustment-mode display in all data views.
- Keep adjusted preview controls disabled until an accepted ADR or task defines the read-time adjustment layer.
- Draft the adjustment service contract:
  - raw input remains immutable
  - corporate actions are read-only adjustment inputs
  - adjusted output is preview/result metadata, not stored canonical OHLC
  - return derivation is never from raw close
- Add backtest handoff metadata for role-keyed canonical storage and read-time adjustment modes.

Acceptance:

- The UI never labels raw SIP close as adjusted.
- Users can see why `adj_close` and `ret` are null.
- Backtest-handoff payloads include per-role/per-dataset provenance: `manifest_ids`, manifest references/checksums, provider IDs/versions/signatures, source feeds, canonical storage modes, read-time adjustment modes, and data roles for `universe`, `prices`, and `corp_actions` when an adjusted mode is selected or explicitly unavailable.
- Any future adjusted preview path is explicitly marked as derived.

### Phase 5: Quality And Backtest Readiness

Goal: users know whether data can be trusted for the intended workflow.

Work:

- Wire quality cards to real validation, integrity, feed-delta, and manifest outputs.
- Add readiness checks for provider/date/workflow, including raw-return dependency checks.
- Add links from failed readiness conditions to the selected-row acquisition or quality drawer sections.
- Add backtest preflight integration for provider, date range, manifests, and adjustment mode.

Acceptance:

- Alpaca SIP readiness blocks or warns on missing corporate-actions manifest where the workflow needs adjustments.
- Alpaca SIP readiness blocks simple backtests that require `ret`/`adj_close` while only raw OHLC is available.
- Alpaca SIP and hybrid readiness expose all stable reason codes used by the service contract: `raw_sip_returns_unavailable`, `alpaca_sip_untrusted_without_manifest`, `alpaca_sip_companion_manifest_stale`, `alpaca_sip_companion_symbol_set_mismatch`, and `crsp_universe_unavailable`.
- Hybrid readiness requires both CRSP universe availability and SIP price availability.
- Quality acknowledgments are persisted server-side with actor, time, source, and issue scope before they affect operational state; if persistence is unavailable, acknowledgment controls render unavailable.
- Fail-closed conditions are visible before the user submits a backtest job.

## Non-Goals

- No UI editing of local parquet data.
- No silent data correction from the console.
- No live Alpaca API calls during previews or backtests; live calls belong to explicit sync/acquisition jobs.
- No production-grade PIT hybrid design in this UI plan.
- No cross-dataset arbitrary join surface in the simplified Data Explorer; advanced SQL remains in SQL Explorer.
- No derivation of returns from raw SIP close.

## Risks

| Risk | Mitigation |
|---|---|
| Users confuse raw and adjusted prices | Always show canonical storage mode, read-time adjustment mode, and raw badges for Alpaca SIP. |
| Data UI drifts from SQL Explorer paths | Extract a public shared resolver/query-preview boundary with SQL Explorer safety tests as the authority. |
| Mock services create false confidence | Replace mocks in Explorer/Quality before expanding UI affordances. |
| Read-time adjustment becomes expensive | Limit preview rows and require a separate design before broad use. |
| Concurrent sync jobs duplicate work | Use dataset/scope idempotency keys and existing lock patterns. |
| Licensing/RBAC regressions | Keep service-level permission and dataset filtering as enforcement boundaries. |
| Large DuckDB previews block NiceGUI | Offload to worker threads and enforce row/time/memory limits. |
| Provider registry is mistaken for dataset catalog | Add a separate dataset/table catalog and only link to `ProviderSpec` where a dataset has a provider identity. |

## Suggested PR Order

1. Component split for `data_management.py` without behavior changes.
2. Manifest summary read model and `/data` operations grid with raw/provenance display.
3. Dataset/table catalog and public shared table/path resolver.
4. Data acquisition preflight DTOs and Alpaca SIP sync job UI.
5. Data Explorer service replacement using the shared resolver/query-preview boundary.
6. Quality/readiness wiring and backtest handoff metadata.
7. Adjustment-layer ADR and prototype only after raw/adjusted UI is explicit.
