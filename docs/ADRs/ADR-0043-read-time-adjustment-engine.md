# ADR-0043: Read-Time Adjustment Engine

## Status
Accepted

## Date
2026-05-11

## Context

Alpaca SIP daily bars are stored as immutable raw canonical OHLCV data. PR #218
and the data-page phases made that raw state visible and blocked workflows that
would derive returns directly from raw `close`.

The data page now needs an explicit adjustment layer so operators can preview
derived prices and backtest handoff metadata without rewriting canonical
storage or implying that raw SIP closes are adjusted.

## Decision

Add a shared read-time adjustment engine that derives `split_adjusted` outputs
from trusted `alpaca_sip_daily` bars plus trusted `alpaca_sip_corp_actions`
inputs.

The initial accepted scope is:

- Raw canonical parquet remains unchanged.
- The engine derives preview/result columns at read time: adjusted OHLCV,
  `adj_close`, `ret`, adjustment factor, derivation mode, and provenance reason
  codes.
- Split ratios come from corporate-action rows whose `ca_type` contains
  `split`, with `new_rate / old_rate` applied backward before the action date.
- Missing or invalid split rows are skipped with explicit reason codes.
- If trusted corporate actions show no split events in scope, the engine still
  derives an identity adjusted path and computes returns from that derived path.
- Data Explorer may enable adjusted previews only when both SIP manifests are
  trusted and companion-manifest checks are clean.
- The Alpaca SIP data-provider adapter applies the same derivation before
  simple backtests consume local SIP bars, so readiness and execution agree.
- Backtest handoff metadata must continue to carry role-keyed manifest IDs,
  references, checksums, provider signatures, canonical storage modes, and
  read-time adjustment modes.

Out of scope for this ADR:

- Rewriting canonical raw OHLCV parquet.
- Treating raw `close` as an adjusted return source.
- A persisted adjusted dataset.
- Production-grade dividend total-return adjustment. Cash-dividend handling
  remains future work and must be explicitly accepted before total-return
  claims are made.

## Consequences

The data page can now show a dense raw-vs-derived workflow without creating a
second physical dataset. Backtest readiness can distinguish raw canonical
storage from a derived read-time price path.

The initial engine is intentionally conservative. It unblocks split-adjusted
price-return previews, but dividend-aware total-return semantics still require
a follow-up design and validation pass before production research uses them as
authoritative total returns.
