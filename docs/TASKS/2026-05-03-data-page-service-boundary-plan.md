# Data Page Service Boundary And Implementation Plan

Date: 2026-05-03
Companion: `docs/TASKS/2026-05-03-data-page-ui-optimization-plan.md`
Status: Planning

## Purpose

This document turns the Data Page UI optimization direction into implementation-sized service and UI slices. It is intentionally conservative: first make existing local data state visible and consistent, then add acquisition controls, then replace mock explorer and quality paths.

## Design Rules

1. Manifest-backed local parquet is the source of truth for local data availability.
2. Alpaca SIP canonical storage is raw. UI code must not imply adjusted storage.
3. Data Explorer and SQL Explorer must not implement separate path resolution rules.
4. RBAC and dataset-level authorization stay in services, not page code.
5. Heavy filesystem/DuckDB operations stay off the NiceGUI event loop.
6. Architecture changes that introduce shared provider/catalog services should get an ADR before implementation if the scope expands beyond UI/service read models.
7. Queryable local parquet and trusted manifest-backed availability are distinct states. SQL Explorer may expose tightly bounded fallback reads for local inspection, but readiness and provenance views must require manifests.
8. Alpaca SIP canonical datasets (`alpaca_sip_daily`, `alpaca_sip_corp_actions`) require manifests for `/data` preview, `/data` SQL handoff, readiness, and backtest handoff. Fallback parquet discovery is untrusted local inspection only and must not expose readiness or provenance claims.
9. Canonical storage mode and read-time adjustment mode are distinct fields. For Alpaca SIP after PR #218, canonical storage is `raw` and read-time adjustment is unavailable until a dedicated layer exists.

## Proposed Service Boundaries

Any `provider_signature` returned to UI/API consumers is a sanitized replay bundle. It must follow one shared allowlist contract across manifest summaries, acquisition jobs, dataset summaries, previews, and handoff payloads. Credentials, tokens, auth headers, raw URLs with secrets, and unbounded raw request payloads are never exposed.

### Provider Signature Contract

Candidate shared serializer:

- `libs/web_console_services/provider_signature.py`

Allowed keys:

- `provider_id`
- `provider_version`
- `source_feed`
- `adjustment_mode`
- `canonical_storage_mode`
- `read_time_adjustment_mode`
- `symbol_set_hash`
- `query_params_hash`
- `manifest_id`
- `manifest_reference`
- `manifest_checksum`
- `manifest_version`
- `schema_version`
- `sync_started_at`
- `sync_finished_at`
- `data_roles`
- `dataset_keys`

DTO shape:

```python
class ProviderSignatureDTO(BaseModel):
    provider_id: str | None = None
    provider_version: str | None = None
    source_feed: str | None = None
    adjustment_mode: str | None = None
    canonical_storage_mode: str | None = None
    read_time_adjustment_mode: str | None = None
    symbol_set_hash: str | None = None
    query_params_hash: str | None = None
    manifest_id: str | None = None
    manifest_reference: str | None = None
    manifest_checksum: str | None = None
    manifest_version: str | None = None
    schema_version: str | None = None
    sync_started_at: AwareDatetime | None = None
    sync_finished_at: AwareDatetime | None = None
    data_roles: dict[str, str] | None = None
    dataset_keys: list[str] | None = None
```

Rules:

- All UI/API-facing provider signatures must be produced by the shared serializer.
- Source manifests may store fields individually, but services must derive the same sanitized signature shape before returning them to the UI.
- The serializer must reject or drop credentials, tokens, auth headers, signed URLs, raw request URLs containing secrets, and oversized raw request bodies.

### 1. Manifest Summary Read Model

Candidate module:

- `libs/web_console_services/data_manifest_service.py`

Responsibilities:

- Load `SyncManifest` records for one dataset or a dataset group.
- Normalize manifest fields for UI display.
- Resolve file count, row count, checksum, schema version, validation status, sync timestamp, age, and missing companions.
- Return reversible provenance fields: `manifest_id`, manifest path/reference, manifest checksum, `provider_id`, `provider_version`, `source_feed`, `adjustment_mode`, `symbol_set_hash`, `sync_started_at`, `sync_finished_at`, `wrds_query_hash` or equivalent params hash, and sanitized `provider_signature` produced by the shared allowlist serializer.
- Define `provider_signature` as the canonical replay bundle for UI/API consumers. If a source manifest stores individual fields instead of a nested signature, the service must derive the signature from those fields and document the exact field list.
- For Alpaca SIP, return a grouped status for:
  - `alpaca_sip_daily`
  - `alpaca_sip_corp_actions`
- For Alpaca SIP, compute paired-manifest cohesion warnings for missing companions, materially different date coverage, and mismatched symbol-set hashes.
- Today, with hash-only symbol sets, symbol-set hash mismatch is a warning by default and end-date drift greater than one business day is `alpaca_sip_companion_manifest_stale`.
- Future manifests that expose full symbol sets can add a Jaccard overlap check; overlap below 0.99 is stale.
- Never execute DuckDB queries.
- Never mutate manifests.

Primary consumers:

- `DataSyncService`
- `DataSourceStatusService`
- `DataExplorerService`
- `/data` operations grid and detail drawer
- `/data/source-status`

Notes:

- The existing manifest logic in `DataSyncService` and `DataSourceStatusService` can move here first.
- SQL Explorer path safety is stricter than summary display. Share primitives carefully; do not weaken SQL Explorer validation.

### 2. Dataset Catalog Read Model

Candidate module:

- `libs/web_console_services/data_catalog_service.py`

Responsibilities:

- List authorized datasets.
- Combine dataset/table metadata with local availability from the manifest summary and SQL table availability.
- Link to static `ProviderSpec` metadata only where a dataset has a historical provider identity. Do not assume `ProviderSpec` covers Compustat, TAQ, Fama-French, or every future table family.
- Replace repeated `_SUPPORTED_DATASETS` tuples in sync/explorer/quality services.
- Return display metadata:
  - dataset key
  - provider ID
  - provider display name
  - tables
  - capabilities
  - production allowed
  - canonical storage mode
  - read-time adjustment mode
  - local availability
  - last sync

Primary consumers:

- `DataSyncService`
- `DataExplorerService`
- `DataQualityService`
- `/data` top context strip
- `/data/sql-explorer` dataset selector, eventually

### 3. Acquisition Job Interface

Candidate modules:

- `libs/web_console_services/data_sync_service.py`
- `libs/web_console_services/schemas/data_management.py`
- future worker adapter if sync execution moves out of process

New DTOs to consider:

```python
class DataAcquisitionPreflightRequestDTO(BaseModel):
    dataset: str
    provider_id: str | None = None
    source_feed: str | None = None
    adjustment: Literal["raw"] | None = None
    mode: Literal["incremental", "targeted_backfill", "full_backfill"]
    start_date: date | None = None
    end_date: date | None = None
    symbols: list[str] | None = None
    universe_id: str | None = None
    reason: str


class DataAcquisitionPreflightDTO(BaseModel):
    dataset: str
    provider_id: str | None = None
    source_feed: str | None = None
    adjustment: Literal["raw"] | None = None
    idempotency_key: str
    submit_token: str
    submit_token_expires_at: AwareDatetime
    normalized_scope: dict[str, Any]
    estimated_api_calls: int | None = None
    estimated_rows: int | None = None
    estimated_bytes: int | None = None
    required_manifests: list[str]
    output_paths: list[str]
    warnings: list[str]
    blocked_reason: str | None = None


class DataAcquisitionSubmitRequestDTO(BaseModel):
    dataset: str
    idempotency_key: str
    submit_token: str
    normalized_scope: dict[str, Any]
    reason: str


class DataAcquisitionJobDTO(BaseModel):
    id: str
    dataset: str
    provider_id: str | None = None
    source_feed: str | None = None
    adjustment: Literal["raw"] | None = None
    idempotency_key: str
    preflight_status: Literal["consumed", "expired"] | None = None
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    produced_manifest_ids: list[str]
    produced_manifest_references: list[str]
    provider_signature: ProviderSignatureDTO | None = None
    row_count: int | None = None
    started_at: AwareDatetime | None = None
    completed_at: AwareDatetime | None = None
    error: str | None = None
```

Rules:

- Trigger requires `TRIGGER_DATA_SYNC`.
- User must have dataset access.
- Reason is required for audit logging.
- Alpaca SIP canonical daily sync must display and enforce `adjustment=raw`.
- Alpaca SIP preflight must reject requests that omit `adjustment` or set anything other than `"raw"` for canonical raw-only datasets; the server must not silently coerce missing adjustment values.
- `adjustment=None` is permitted only for non-adjustment datasets such as corporate actions. The `Literal["raw"]` type should widen only when a real adjusted-mode contract exists.
- Corporate-actions sync carries provider/source provenance but does not require an `adjustment` field because it does not store adjusted price bars.
- Alpaca SIP daily sync currently supports symbol lists and year-partitioned `start_year`/`end_year` behavior. Arbitrary partial-date targeted backfills require either preflight normalization to year scope or a new sync-manager capability.
- The server calculates the idempotency key from normalized dataset, provider, source feed, adjustment, mode, symbol scope, and date/year scope.
- Submission must include the preflight idempotency key and submit token through `DataAcquisitionSubmitRequestDTO`; the server must verify they still match the current request parameters.
- Submit tokens are single-use and expire after five minutes. Expired, reused, or mismatched tokens return a stable `preflight_required` error so the UI can prompt a fresh preflight rather than silently re-preflighting.
- `submit_token` is a cryptographically random opaque value bound server-side to the idempotency key and requesting principal. It is never derivable from request inputs.
- Job DTOs, logs, and UI job state must never return the submit-token value after submission. They may expose token status or expiry only.
- Duplicate in-flight jobs for the same dataset/scope should return existing job state or fail clearly.
- Placeholder or queued adapters must enforce the same raw adjustment check, submit-token verification, and duplicate rejection as the eventual background worker path.

### 4. Explorer Preview Interface

Candidate modules:

- `libs/web_console_services/data_explorer_service.py`
- required public resolver/query-preview API extracted from `libs/web_console_services/sql_explorer_service.py` or placed in a dedicated shared module

Responsibilities:

- Provide safe preview, schema, and summaries for one authorized dataset/table.
- Use the same available-table validation, path validation, DuckDB memory/thread limits, blocked-function policy, and dataset authorization model as SQL Explorer.
- Use bounded limits for previews.
- Return manifest/provenance metadata alongside rows.
- Provide SQL Explorer handoff payloads rather than executing arbitrary cross-dataset SQL in the selected-row preview strip.

New DTOs to consider:

```python
class DatasetTableSummaryDTO(BaseModel):
    dataset: str
    table: str
    available: bool
    trusted_manifest_backed: bool
    manifest_id: str | None = None
    manifest_reference: str | None = None
    provider_id: str | None = None
    provider_version: str | None = None
    source_feed: str | None = None
    provider_signature: ProviderSignatureDTO | None = None
    row_count: int | None = None
    min_date: str | None = None
    max_date: str | None = None
    symbol_count: int | None = None
    columns: list[str]
    canonical_storage_mode: str | None = None
    read_time_adjustment_mode: str | None = None


class DatasetPreviewRequestDTO(BaseModel):
    dataset: str
    table: str
    limit: int = 100
    symbol: str | None = None
    start_date: date | None = None
    end_date: date | None = None


class DatasetPreviewDTO(BaseModel):
    columns: list[str]
    rows: list[dict[str, Any]]
    total_count: int | None
    has_more: bool
    manifest_id: str | None = None
    manifest_reference: str | None = None
    provider_id: str | None = None
    provider_version: str | None = None
    source_feed: str | None = None
    provider_signature: ProviderSignatureDTO | None = None
    canonical_storage_mode: str | None = None
    read_time_adjustment_mode: str | None = None
    null_column_reasons: dict[str, str]
    warnings: list[str]


class SqlExplorerHandoffDTO(BaseModel):
    dataset: str
    table: str
    query: str
    trusted_manifest_backed: bool
    manifest_id: str | None = None
    manifest_reference: str | None = None
    manifest_checksum: str | None = None
    provider_id: str | None = None
    provider_version: str | None = None
    source_feed: str | None = None
    adjustment_mode: str | None = None
    provider_signature: ProviderSignatureDTO | None = None
    warnings: list[str]


class BacktestHandoffPayloadDTO(BaseModel):
    workflow: str
    dataset_keys: list[str]
    data_roles: dict[str, str]
    manifest_ids: dict[str, str | None]
    manifest_references: dict[str, str | None]
    manifest_checksums: dict[str, str | None]
    provider_ids: dict[str, str | None]
    provider_versions: dict[str, str | None]
    source_feeds: dict[str, str | None]
    canonical_storage_modes: dict[str, str | None]
    read_time_adjustment_modes: dict[str, str | None]
    provider_signatures: dict[str, ProviderSignatureDTO]
    null_column_reasons: dict[str, str]
    blockers: list[str]
```

Rules:

- Default preview for `alpaca_sip_daily` must state that OHLC is raw and `adj_close`/`ret` are unavailable because canonical storage is raw.
- Preview must not derive returns from raw close.
- Exact row counts, min/max dates, and symbol counts must be manifest-derived, cached, or bounded by explicit timeout and memory limits. They may be null when an exact scan would be too expensive.
- `/data` preview and generated SQL handoff for Alpaca SIP canonical datasets require `trusted_manifest_backed=True`. Fallback parquet without manifests may be visible as untrusted local inspection state but must not expose `/data` preview/readiness/backtest actions.
- For advanced queries, hand off to SQL Explorer with a generated safe query and `SqlExplorerHandoffDTO`.
- SQL Explorer must verify the manifest reference/checksum in the handoff still matches current path resolution before treating the query as trusted. If it does not match, SQL Explorer must refuse the trusted handoff or downgrade it to explicit untrusted local inspection.
- Backtest handoff uses `BacktestHandoffPayloadDTO` so hybrid and adjustment-aware workflows carry per-role provenance for `universe`, `prices`, and `corp_actions`.
- `BacktestHandoffPayloadDTO` role-keyed maps use canonical role keys `universe`, `prices`, and `corp_actions`. `data_roles` maps each role to the selected dataset/provider source, while `dataset_keys` lists every referenced dataset key. The manifest/provider/feed/storage/adjustment maps and `provider_signatures` are keyed by the same role keys so UI, `/backtest`, and tests do not mix role keys with dataset keys or collapse hybrid replay provenance into one provider.

### 5. Readiness Checks

Candidate module:

- `libs/web_console_services/data_readiness_service.py`

Responsibilities:

- Answer whether a dataset/provider is usable for a workflow.
- Workflows:
  - data preview
  - simple backtest
  - hybrid research backtest
  - quality analysis
  - SQL exploration
- Return blockers and warnings with links/actions.
- Use stable reason codes so `/data`, `/backtest`, and tests can assert the same fail-closed states.

Example checks:

- Alpaca SIP daily manifest exists.
- Alpaca SIP corporate-actions manifest exists when adjustment-aware workflow is requested.
- Requested date range is within provider history.
- Provider is production allowed or the environment is explicitly non-production.
- CRSP universe availability exists for hybrid workflow.
- Selected adjustment mode is supported.
- `raw_sip_returns_unavailable`: block any workflow that would require `ret`, `adj_close`, or derived returns from raw Alpaca SIP OHLC while read-time adjustment is unavailable.
- `alpaca_sip_untrusted_without_manifest`: block trusted/readiness workflows when only fallback parquet files exist without manifests.
- `alpaca_sip_companion_manifest_stale`: warn or block when daily bars and corporate actions are materially out of date relative to each other for an adjustment-aware workflow.
- `alpaca_sip_companion_symbol_set_mismatch`: warn when paired daily bars and corporate actions have different symbol-set hashes under the current hash-only manifest format.
- `crsp_universe_unavailable`: block hybrid workflows when the CRSP universe role is required but unavailable.

## UI Component Split

Target files:

- `apps/web_console_ng/pages/data_management.py`
- new `apps/web_console_ng/components/data_operations_grid.py`
- new `apps/web_console_ng/components/data_context_ribbon.py`
- new `apps/web_console_ng/components/data_detail_drawer.py`
- new `apps/web_console_ng/components/data_acquisition_section.py`
- new `apps/web_console_ng/components/data_preview_strip.py`
- new `apps/web_console_ng/components/data_quality_section.py`
- new `apps/web_console_ng/components/data_readiness_section.py`

Initial split should preserve behavior before changing service contracts. This lowers risk because `data_management.py` is already large and timer-heavy. The final composition should be grid-first: context ribbon, dense operations grid, selected-row detail drawer, and compact preview/query strip.

## Implementation Slices

### Slice 1: Component Split Only

Files:

- `apps/web_console_ng/pages/data_management.py`
- new component files under `apps/web_console_ng/components/`
- existing tests for `/data`

Work:

- Move sync, explorer, and quality render helpers into components.
- Introduce the grid/detail-drawer scaffolding without changing service outputs.
- Keep existing service calls unchanged.
- Keep timers and cleanup ownership behavior intact.

Acceptance:

- Existing tests pass without service behavior changes.
- Page still renders for each permission combination.
- The page has a single primary dataset/table grid placeholder rather than independent top-level Sync, Explorer, and Quality surfaces.

### Slice 2: Manifest Summary Service

Files:

- `libs/web_console_services/data_manifest_service.py`
- `libs/web_console_services/data_sync_service.py`
- `libs/web_console_services/data_source_status_service.py`
- service tests

Work:

- Extract Alpaca SIP manifest loading and grouped status.
- Add tests for no manifests, one manifest missing, both present/passed, present/failed.
- Add tests for provenance fields and paired-manifest date/symbol cohesion warnings.
- Replace duplicated private helpers.

Acceptance:

- `/data` sync status and `/data/source-status` report the same Alpaca SIP status.
- File counts, row counts, and validation statuses are deterministic in tests.
- Provenance fields required by PR #218 are visible in the returned read model.

### Slice 3: Dense Operations Grid UI

Files:

- `apps/web_console_ng/components/data_context_ribbon.py`
- `apps/web_console_ng/components/data_operations_grid.py`
- `apps/web_console_ng/components/data_detail_drawer.py`
- `apps/web_console_ng/pages/data_management.py`
- page/component tests

Work:

- Add top context ribbon, dense dataset/table grid, and selected-row details drawer.
- Display provider capabilities and manifest details in grid columns and drawer sections.
- Add raw-data badge for Alpaca SIP.
- Display issue-count jump links that filter the operations grid.
- Move acquisition, quality, readiness, and preview affordances into selected-row drawer sections.

Acceptance:

- Users can see manifest IDs and missing companion manifests without opening SQL Explorer.
- UI never labels Alpaca SIP canonical data as adjusted.
- The primary page can be scanned from one grid without switching feature tabs.

### Slice 4: Acquisition Preflight

Files:

- `libs/web_console_services/schemas/data_management.py`
- `libs/web_console_services/data_sync_service.py`
- `apps/web_console_ng/components/data_acquisition_section.py`
- tests for permissions, validation, idempotency, and UI state

Work:

- Add preflight request/result DTOs.
- Add preflight endpoint/service method for Alpaca SIP daily and corporate actions.
- Add submit flow gated by preflight and reason.
- Keep execution as a queued/placeholder adapter if full background job integration is not ready, but the placeholder path must enforce raw adjustment, submit-token verification, and duplicate rejection.
- Resolve Open Question 1 before starting this slice; the chosen placeholder/script-backed/worker path must still meet the same safety rules.

Acceptance:

- No direct sync submission without a preflight result.
- UI shows `adjustment=raw` for canonical Alpaca SIP daily sync.
- Alpaca SIP preflight rejects missing or non-raw adjustment values for canonical raw-only datasets.
- Corporate-actions preflight does not require adjustment metadata.
- Preflight returns an idempotency key derived from normalized sync scope, plus a cryptographically random opaque submit token bound server-side to that idempotency key, normalized scope, and requesting principal.
- Submit request consumes the preflight token, and job responses/logs never expose the consumed token value.
- Alpaca SIP daily preflight documents current year-partitioned sync behavior or blocks unsupported targeted-date sync.
- Duplicate dataset/scope submissions are guarded.

### Slice 5: Explorer Backend Replacement

Files:

- `libs/web_console_services/data_explorer_service.py`
- extracted SQL/path helper module, if needed
- `apps/web_console_ng/components/data_preview_strip.py`
- tests for path safety, permission filtering, previews, and handoff

Work:

- Replace placeholder metadata with catalog/manifest/table availability.
- Add a local availability classifier that combines manifest summary state with SQL Explorer path resolution so the UI can distinguish trusted manifest-backed availability from `queryable fallback only`.
- Add bounded DuckDB preview using validated table paths.
- Add query templates.
- Add "Open in SQL Explorer" handoff route/query state with `SqlExplorerHandoffDTO`.
- Add SQL Explorer ingestion of `SqlExplorerHandoffDTO`, current-manifest re-resolution, checksum comparison, and trusted-to-untrusted downgrade/refusal behavior.

Acceptance:

- `alpaca_sip_daily` trusted previews read manifest-pinned parquet paths.
- Explorer availability matches SQL Explorer availability.
- Explorer distinguishes queryable fallback parquet from trusted manifest-backed data.
- Alpaca SIP fallback parquet without manifests is not previewable or eligible for generated `/data` SQL handoff.
- SQL Explorer handoff refuses or downgrades trusted state when manifest reference/checksum no longer matches.
- Unauthorized datasets are hidden or rejected server-side.

### Slice 6: Quality And Readiness

Files:

- `libs/web_console_services/data_quality_service.py`
- `libs/web_console_services/data_readiness_service.py`
- `apps/web_console_ng/components/data_quality_section.py`
- `apps/web_console_ng/components/data_readiness_section.py`
- tests

Work:

- Wire manifest/integrity/feed-delta result summaries.
- Add readiness checks for backtest workflows.
- Link blockers to acquisition or quality actions.
- Define concrete quality inputs before implementation: manifest validation status, `alpaca_sip_integrity` reports, `alpaca_feed_delta` reports, staleness rules, and persisted alert acknowledgments.

Acceptance:

- Missing Alpaca SIP manifests are readiness blockers.
- Raw-return fail-closed behavior is visible before backtest submission.
- Quality cards are not mock-only for Alpaca SIP.
- Quality cards identify their data source and timestamp, or render as unavailable rather than mock-derived.
- Alert acknowledgments persist server-side with actor, time, source, and issue scope before they affect operational state. If persistence is unavailable, acknowledgment controls render unavailable.

## Test Strategy

Service tests:

- Manifest summary for missing/partial/passed/failed manifests.
- Dataset catalog permission filtering.
- Preflight validation and idempotency keys.
- Submit-token lifecycle: cryptographically random opaque tokens bound to idempotency key and requesting principal, single use, five-minute expiry, stable `preflight_required` for expired/reused/mismatched/forged tokens, and no token values in job DTOs/logs.
- Provider signature allowlist serialization, including negative tests for credentials, auth headers, tokens, signed URLs, and oversized raw request bodies across manifest summaries, acquisition jobs, previews, SQL handoff, and backtest handoff.
- Preview path safety and row limits.
- SQL Explorer handoff provenance and manifest revalidation.
- Backtest handoff per-role manifest/provider/feed/storage/adjustment metadata, provider signatures, provider versions, and canonical map key convention.
- Readiness checks for Alpaca SIP and hybrid provider, including explicit assertions for `raw_sip_returns_unavailable`, `alpaca_sip_untrusted_without_manifest`, `alpaca_sip_companion_manifest_stale`, `alpaca_sip_companion_symbol_set_mismatch`, and `crsp_universe_unavailable`.
- Persisted quality acknowledgment behavior, including actor/time/source/scope metadata and unavailable UI state when persistence is unavailable.

Page/component tests:

- Permission-specific grid row, drawer section, and action visibility.
- Raw-data badges.
- Missing manifest warnings.
- Preflight-required submit behavior.
- SQL Explorer handoff URL/query payload and provenance payload.

Regression tests:

- Existing SQL Explorer safety tests remain authoritative for query execution.
- Existing source-status tests should be updated to use the shared manifest service.
- Backtest UI tests should assert per-role provider/provenance handoff once readiness links are added.

## Open Questions

1. Which execution mechanism should Slice 4 use first: in-process placeholder, script/sync-manager shellout, or a new background worker? Decision required before Slice 4; all options must enforce the same safety rules above.
2. Should `/data/source-status` remain a full page, or become an alias/deep link into `/data` once the hub has source-health parity?
3. Where should shared SQL table/path resolution live so SQL Explorer remains hardened while Data Explorer can reuse it?
4. Does the adjustment layer require a new ADR before any adjusted preview prototype? This plan assumes yes.
5. What is the first target operator workflow: Alpaca SIP daily backfill, corporate-actions backfill, or dataset readiness for backtests?

## Immediate Next Step

Start with Slice 1 and Slice 2. That creates a safer structure and removes duplicated Alpaca SIP manifest logic without changing sync execution or adding adjustment behavior.
