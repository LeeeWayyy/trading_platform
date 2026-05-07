# Read-Time Adjustment Service Contract Draft

Status: draft for Data Page phase 4. This is not an accepted ADR and does not enable adjusted previews or backtests by itself.

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
- `read_time_adjustment_mode`: requested mode. Phase 4 only supports `unavailable`; future accepted designs may add modes such as `split_adjusted`.

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

Draft/future companion-state codes:

- `alpaca_sip_companion_manifest_stale`
- `alpaca_sip_companion_symbol_set_mismatch`

## Phase 4 Behavior

- Data Explorer displays raw canonical storage and unavailable read-time adjustment state.
- Adjusted preview controls remain disabled with `read_time_adjustment_layer_not_defined`.
- Backtest handoff metadata may be built for inspection, but backtest submission must remain fail-closed while adjusted returns are unavailable.
