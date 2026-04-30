# ADR-0041: Alpaca SIP Historical Data Provider

## Status
Accepted

## Date
2026-04-29

## Context

The platform currently supports yfinance for development data and CRSP for
production-grade point-in-time historical data through the `DataProvider`
protocol and `UnifiedDataFetcher` from ADR-016. Live services already consume
Alpaca market data, but historical training and backtesting do not have a local
Alpaca SIP path.

CRSP remains the authority for long-history, survivorship-bias-free research.
Alpaca SIP adds value because it can provide post-2016 bars from the same feed
family used by production execution, improving research/production parity for
strategies where execution-feed parity matters more than long-history coverage.

## Decision

Add Alpaca SIP as an explicit, local historical data provider backed by parquet
files under `data/alpaca/sip/daily/` and manifests under `data/manifests/`.
Training and backtesting read local parquet snapshots; they do not call Alpaca
live APIs.

The Phase 1 provider has these semantics:

- Dataset name: `alpaca_sip_daily`.
- Local schema is normalized at sync/write time and must include `date`,
  `symbol`, `open`, `high`, `low`, `close`, and `volume`.
- Optional local columns include `trade_count`, `vwap`, `adj_close`, and `ret`.
- The adapter emits the existing unified schema from ADR-016.
- If `ret` is absent, the adapter derives it from `adj_close` when available,
  otherwise from `close`. The first row per symbol is null unless the caller
  requested sufficient lookback data.
- `supports_universe` is `False`.
- `is_production_ready` is `False` until survivorship and strategy migration
  work is complete.

Provider selection remains explicit for Alpaca SIP:

- `ProviderType.ALPACA_SIP` is available for explicit fetcher configuration.
- `AUTO` still uses CRSP in production and does not make Alpaca SIP a production
  fallback.
- Backtest jobs can choose `alpaca_sip`, but the worker only permits it in
  explicitly non-production environments.

The Phase 2 daily-bar sync foundation has these semantics:

- `AlpacaSIPSyncManager` fetches daily bars with Alpaca's paginated
  `StockBarsRequest`.
- Sync writes deterministic yearly parquet partitions under
  `data/alpaca/sip/daily/YYYY.parquet`.
- Sync uses the same `SyncManifest` format and dataset name consumed by
  `AlpacaSIPLocalProvider`.
- The default feed is `sip`; the default adjustment policy is `all`, and
  `adj_close` is populated from the returned close for that adjusted response.
- Corporate-actions ingestion is not included yet. The installed
  `alpaca-py==0.15.0` package does not expose a corporate-actions
  request/client method, so that follow-up needs either a direct REST wrapper or
  an SDK upgrade decision.

## Hybrid Provider Deferral

Do not implement the hybrid CRSP-universe/SIP-prices provider in Phase 1.
`PITBacktester` is currently coupled to CRSP/PERMNO data, while SIP prices are
ticker-based. A correct hybrid implementation needs either a PITBacktester
interface refactor or an explicit point-in-time PERMNO-to-symbol bridge. That is
Phase 3 work, not local-provider plumbing.

## Consequences

### Positive

- Adds execution-feed parity as an opt-in historical data source.
- Reuses existing manifest and DuckDB local-read patterns.
- Avoids rate limits and live API variability during training/backtests.
- Keeps CRSP as the default and production-safe data source.

### Negative

- Alpaca SIP is ticker-based and is not survivorship-bias-free.
- Derived returns may differ from CRSP `ret` due to adjustment policy and event
  timing differences.
- Alpaca SIP cannot provide point-in-time universes in Phase 1.
- Cost model computation remains CRSP-only in this slice.
- The Phase 2 sync foundation stores adjusted bars by default. Raw-bar plus
  corporate-action reconstruction remains future work.

## Required Follow-Up

- Run the Phase 0 entitlement and reconciliation spike before relying on SIP
  results for strategy decisions.
- Complete corporate-actions ingestion before broad usage.
- Add a hybrid ADR/update when PITBacktester or PERMNO bridging is designed.
- Update ADR-016 or replace it with a broader multi-provider selection ADR if
  hybrid becomes a first-class production pathway.

## References

- ADR-016: Data Provider Protocol
- `docs/TASKS/2026-04-28-alpaca-sip-data-source-plan.md`
- `libs/data/data_providers/protocols.py`
- `libs/data/data_providers/unified_fetcher.py`
