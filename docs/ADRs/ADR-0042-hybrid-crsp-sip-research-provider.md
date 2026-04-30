# ADR-0042: Hybrid CRSP Universe + Alpaca SIP Price Research Provider

## Status
Accepted

## Date
2026-04-30

## Context

ADR-0041 added Alpaca SIP as an explicit local historical data provider, but
left the hybrid CRSP-universe/SIP-prices provider deferred. The task plan later
identified one bounded hybrid use case that does not require refactoring the
PERMNO-based `PITBacktester`: a research-only `SimpleBacktester` path that uses
CRSP only to choose a static ticker universe at the backtest start date, then
uses local Alpaca SIP daily bars for price history.

This is not a production point-in-time backtest. It is a bridge for evaluating
execution-feed parity on post-2016 windows while retaining CRSP as the default
and production-safe research source.

## Decision

Add `hybrid_crsp_universe_sip_prices` as an explicit, research-only provider
mode with these semantics:

- `UnifiedDataFetcher` supports `ProviderType.HYBRID_CRSP_UNIVERSE_SIP_PRICES`.
- `HybridDataProviderAdapter` routes `get_universe(as_of_date)` to CRSP and
  routes `get_daily_prices(...)` to Alpaca SIP.
- Backtest jobs support
  `DataProvider.HYBRID_CRSP_UNIVERSE_SIP_PRICES`.
- The web form and JSON config editor expose the provider as research-only.
- The backtest worker only allows the provider in explicit non-production
  environments (`development`, `test`, `local`, or `ci`).
- If a job supplies `extra_params.universe`, that explicit universe is used.
- If a job omits `extra_params.universe`, the worker calls
  `get_universe(start_date)` and uses the resulting CRSP ticker list as a static
  `SimpleBacktester` universe.
- The worker pins the Alpaca SIP manifest for reproducibility and records
  metadata for the CRSP universe provider and Alpaca SIP price provider.
- Hybrid simple backtests require `start_date >= 2016-04-01` so the
  `SimpleBacktester` 90-day lookback stays within Alpaca SIP history that starts
  around 2016-01-01.
- Cost model computation remains skipped because the hybrid path does not yet
  join point-in-time ADV/volatility data to SIP-derived weights.

CRSP remains the default for ad-hoc backtests. `AUTO` provider selection does
not choose the hybrid provider implicitly.

## Consequences

### Positive

- Enables a concrete Phase 3 hybrid experiment without refactoring
  `PITBacktester`.
- Keeps the production PIT path unchanged and CRSP-first.
- Makes universe provenance and price provenance explicit in result metadata.
- Fails closed for pre-SIP-history windows instead of silently falling back.

### Negative

- The hybrid simple-backtest path still uses ticker-based pseudo-PERMNOs, not
  CRSP PERMNO identity.
- The CRSP universe is static as of `start_date`; it is not a full PIT universe
  refresh through the backtest.
- SIP price gaps for CRSP universe members remain possible and must be surfaced
  by provider/backtest errors or empty coverage metrics.
- Cost and capacity analysis are unavailable until a PIT ADV join is designed.

## Required Follow-Up

- Run Phase 0 bar/return reconciliation against live Alpaca SIP entitlement and
  CRSP comparison data before relying on hybrid results.
- Choose and implement the corporate-actions ingestion path for raw-bar
  reconstruction.
- Select a strategy and run the Phase 4 retrain/walk-forward comparison with
  synced SIP data.
- Revisit a production-grade hybrid only if a PITBacktester interface refactor
  or point-in-time PERMNO-to-symbol bridge is designed.

## References

- ADR-0041: Alpaca SIP Historical Data Provider
- ADR-016: Data Provider Protocol
- `docs/TASKS/2026-04-28-alpaca-sip-data-source-plan.md`
- `libs/data/data_providers/protocols.py`
- `libs/data/data_providers/unified_fetcher.py`
- `libs/trading/backtest/worker.py`
