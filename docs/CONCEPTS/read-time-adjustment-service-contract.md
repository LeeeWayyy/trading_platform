# Read-Time Adjustment Service Contract Draft

Status: superseded by ADR-0043 for the initial `split_adjusted` read-time engine.

## Purpose

Define the future contract for deriving adjusted preview/result views from raw Alpaca SIP canonical data without mutating canonical storage or implying that raw closes are adjusted.

## Invariants

- Raw input remains immutable. `alpaca_sip_daily` canonical OHLC is stored and labeled as `canonical_storage_mode=raw`.
- Corporate actions are read-only adjustment inputs. `alpaca_sip_corp_actions` may be used to derive adjusted output, but it is not rewritten into canonical OHLC.
- Adjusted output is derived preview/result metadata, not stored canonical OHLC.
- Return derivation is never allowed from raw `close`. `ret` remains unavailable until the service can derive it from an accepted adjusted price path or a trusted provider-supplied return column.
- Any adjusted preview/result path must be explicitly marked as derived and must carry the raw input and corporate-action provenance used to derive it.

## Request Shape

Required fields:

- `dataset`: logical dataset family, initially `alpaca_sip`.
- `roles`: role-keyed inputs for `universe`, `prices`, and `corp_actions`.
- `start_date` and `end_date`: requested date bounds.
- `symbols` or `symbol_source`: explicit symbol set or reproducible source.
- `read_time_adjustment_mode`: requested mode. ADR-0043 accepts `split_adjusted`
  for trusted Alpaca SIP daily bars plus trusted corporate actions.

## Response Shape

Required fields:

- `derived`: boolean, always true for adjusted outputs.
- `canonical_storage_mode_by_role`: role-keyed canonical storage modes.
- `read_time_adjustment_mode_by_role`: role-keyed read-time modes.
- `manifest_ids`, `manifest_references`, and `manifest_checksums` by role.
- `provider_ids`, `provider_versions`, `source_feeds`, and sanitized provider signatures by role.
- `columns`: output columns and per-column derivation state.
- `reason_codes`: fail-closed or warning reasons.

Phase 4 wired codes:

- `raw_sip_returns_unavailable`
- `read_time_adjustment_layer_not_defined`
- `alpaca_sip_untrusted_without_manifest`
- `alpaca_sip_manifest_validation_failed`
- `alpaca_sip_manifest_summary_unavailable`

ADR-0043 wired codes:

- `split_adjusted_read_time_available`
- `split_adjusted_no_split_actions_in_scope`
- `split_adjusted_invalid_split_actions_skipped`

Draft/future companion-state codes:

- `alpaca_sip_companion_manifest_stale`
- `alpaca_sip_companion_symbol_set_mismatch`

## Phase 4 Behavior

- Data Explorer displays raw canonical storage and unavailable read-time adjustment state.
- Adjusted preview controls remain disabled with `read_time_adjustment_layer_not_defined`.
- Backtest handoff metadata may be built for inspection, but backtest submission must remain fail-closed while adjusted returns are unavailable.

## ADR-0043 Behavior

- Data Explorer can request a derived `split_adjusted` preview when both
  Alpaca SIP manifests are trusted and companion checks are clean.
- Alpaca SIP simple-backtest fetches use the same split-adjusted read-time
  derivation before enforcing `adj_close`/`ret` availability.
- Raw canonical storage remains labeled `raw`.
- Derived previews carry read-time mode, manifest provenance, provider
  signatures, and derivation reason codes.
- Dividend-aware total-return adjustment remains out of scope.
