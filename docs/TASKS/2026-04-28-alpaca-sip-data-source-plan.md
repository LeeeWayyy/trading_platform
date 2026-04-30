# Alpaca SIP as a Peer Historical Data Source — Plan

Date: 2026-04-28
Owner: Codex
Status: IN PROGRESS (Phase 2 daily-bar sync foundation)

## 2026-04-29 Tuning Notes

Latest-`master` review found the plan is in good shape for Phase 1, with these
constraints made explicit before implementation:

- Phase 1 is local-only provider plumbing. It does not call Alpaca live APIs and
  does not implement bulk sync.
- The local daily parquet schema is normalized at sync/write time. It must
  include `date`, `symbol`, `open`, `high`, `low`, `close`, and `volume`.
  Optional columns are `trade_count`, `vwap`, `adj_close`, and `ret`.
- `AlpacaSIPDataProviderAdapter` computes `ret` from `adj_close` when present,
  otherwise from `close`. The first row per symbol is null unless the caller
  requests enough lookback data.
- Backtests use the same non-PIT `SimpleBacktester` path as yfinance and require
  an explicit non-production `ENVIRONMENT`. SIP is not production-ready until a
  survivorship strategy exists.
- Hybrid is deferred. `PITBacktester` is CRSP/PERMNO-coupled today, so hybrid
  needs either a PITBacktester interface refactor or a point-in-time
  PERMNO-to-symbol bridge.
- Cost model computation remains CRSP-only in this slice. Alpaca SIP jobs may
  carry cost-model config for provenance, but the worker skips cost computation
  until a PIT ADV/volatility source exists.
- Phase 1 provider plumbing is implemented: local DuckDB provider,
  `AlpacaSIPDataProviderAdapter`, explicit `UnifiedDataFetcher` selection,
  non-production backtest worker dispatch, config-editor/web-form support, and
  ADR-0041.
- Phase 2 daily-bar foundation is started: `AlpacaSIPSyncManager` fetches daily
  bars with Alpaca's paginated `StockBarsRequest`, writes deterministic yearly
  parquet partitions, saves `alpaca_sip_daily` manifests, and exposes
  `make sync-alpaca-sip`, `make sync-alpaca-sip-status`, and
  `make sync-alpaca-sip-verify`.
- Corporate-actions ingestion is still deferred. `alpaca-py==0.15.0` does not
  expose a corporate-actions request/client method in the installed SDK, so this
  should be implemented only after choosing either a direct REST wrapper or an
  SDK upgrade.

## Scope

Add Alpaca SIP (consolidated tape, daily and intraday bars) to the historical
data layer **alongside** CRSP. CRSP is not being deprecated. The motivation is
research/production parity: the bars used to train models and run backtests
should be the same bars the execution gateway sees in production.

This plan covers strategy training and backtesting. Live/intraday Alpaca usage
in `apps/market_data_service` and `apps/execution_gateway` is already in place
and is unaffected.

## Non-Goals

- Replacing CRSP. CRSP remains the long-history, survivorship-free authority.
- Replacing Compustat or Fama-French data (orthogonal — fundamentals/factors).
- Changing the live market-data path. `libs/data/market_data/` is unchanged.

## Current State (verified)

- Provider abstraction: `libs/data/data_providers/protocols.py` defines the
  `DataProvider` protocol with `YFinanceDataProviderAdapter` and
  `CRSPDataProviderAdapter`. ADR-016 governs.
- Selection: `libs/data/data_providers/unified_fetcher.py` picks one provider
  per environment via `ProviderType.{YFINANCE,CRSP,AUTO}`.
- **Second provider enum + multiple hardcoded 2-provider gates**:
  `libs/trading/backtest/job_queue.py:36` defines its own
  `DataProvider` enum with values `{CRSP, YFINANCE}` only. Adding SIP
  requires updates in **all** of:
  - `libs/trading/backtest/job_queue.py:36` — enum
  - `libs/trading/backtest/worker.py:403,442` — `if/elif` provider dispatch
  - `apps/web_console_ng/pages/backtest.py:534` — hardcoded 2-option dropdown
  - `apps/web_console_ng/pages/backtest.py:737,844` — binary
    `startswith("CRSP") else YFINANCE` mapping (sends any non-CRSP
    selection to YFINANCE)
  - `apps/web_console_ng/components/config_editor.py:37-38` —
    `PROVIDER_DISPLAY` dict
  - `apps/web_console_ng/components/config_editor.py:128-132,235-241` —
    provider validation that rejects unknown values
- CRSP local store: provider reads from `data/wrds/crsp/daily/`
  (`libs/data/data_providers/crsp_local_provider.py:87,109`); WRDS sync
  scripts write per `DATASET_NAME` (`crsp_daily` →
  `data/wrds/crsp_daily/`). Plan will follow the provider's read path
  convention; manifest indirection covers the difference.
- Alpaca live wiring: `libs/data/market_data/provider.py` already wraps
  `alpaca-py` `StockHistoricalDataClient`. `ALPACA_DATA_FEED` config
  **supports** `iex|sip|otc|boats` in `apps/market_data_service/config.py`
  and `apps/execution_gateway/config.py`, but the **default is `iex`**
  (`.env.example:14`, `docker-compose.yml:191,276`). There is **no
  historical-training path through Alpaca today**.

## Tradeoffs (SIP vs CRSP)

| Dimension | CRSP | Alpaca SIP |
|---|---|---|
| History depth | 1925+ | ~2016 |
| Survivorship bias | Free (PERMNO-tracked) | Partial; ticker-only, ticker reuse hazard |
| Permanent ID | PERMNO | Ticker (Alpaca asset_id is Alpaca-specific UUID) |
| Point-in-time universe | Built-in `get_universe(as_of_date)` | Not native |
| Adjustments | `ret` pre-adjusted; `prc` raw | API param `adjustment=raw\|split\|dividend\|all` |
| Corporate actions | Embedded in PERMNO history | Separate corporate-actions endpoint |
| Cost / friction | WRDS license; SAS-style access | Algo Trader Plus (~$99/mo); REST API |
| Intraday / quotes | Daily only on current tier | Minute bars + quotes + trades |
| Production parity | None (different source from execution) | **Yes** (same source as execution gateway) |

**Strategic value of SIP**: feature parity with execution. **Strategic value
of CRSP**: survivorship correctness and long history. Both are real; keeping
both is the right call.

## Architectural Decisions

### D1. Mirror the CRSP local-storage pattern, do not call Alpaca live during training

Rationale: bulk-sync once, then DuckDB over parquet. Avoids API rate limits
during training, gives reproducible snapshots via manifests, and reuses the
existing `data_quality` and `sync_manager` plumbing.

### D2. SIP ships without `get_universe()` initially

`AlpacaSIPDataProviderAdapter.supports_universe = False`,
`is_production_ready = False` until the survivorship layer (D4) lands.
This matches the existing yfinance pattern and keeps Phase 1 small.

### D3. Add a Hybrid provider (offered, recommended only after caller wiring)

```python
class HybridDataProviderAdapter:
    """CRSP for universe; Alpaca SIP for prices and SIP-side adjustments."""
    def __init__(self, universe_provider: CRSPDataProviderAdapter,
                 price_provider: AlpacaSIPDataProviderAdapter): ...
```

This configuration delivers the strategic win **on paper**:
**survivorship-correct universe (CRSP) + same bars as execution (SIP)**.

**Caller-wiring caveat**: today no production code path consumes
`get_universe()` — backtest entry points (`libs/trading/alpha/simple_backtester.py`,
`libs/data/data_pipeline/historical_etl.py`) take explicit symbol lists
from the caller. The hybrid provider therefore has no value until at
least one caller is wired to use its universe output. Phase 4 (per-strategy
migration) is the right place to do that wiring on a strategy-by-strategy
basis. **Until that wiring exists, hybrid is offered as an option, not
described as the recommended default.**

**Corporate actions policy**: prices and adjustments come from Alpaca
(`adjustment` API param + Alpaca corporate-actions endpoint). CRSP is used
only for `get_universe(as_of_date)` and PERMNO-side history. This avoids
needing a point-in-time PERMNO↔ticker mapping table to apply CRSP
distributions to SIP bars.

**Pre-SIP-history behavior**: when the requested date range starts before
SIP coverage (~2016), the hybrid provider raises an explicit error pointing
the caller at pure-CRSP mode. No silent fallback.

New ADR required — ADR-016 assumed one provider answers both methods.

### D4. Survivorship layer for SIP is deferred and optional

Anyone needing survivorship correctness uses CRSP or the hybrid. We do not
build a SIP-only survivorship layer in this plan. Revisit only if a strategy
explicitly needs SIP-only history with delisted coverage.

### D5. Selection becomes per-job, not per-environment

`UnifiedDataFetcher` keeps env-default selection for ad-hoc/CLI use. Strategy
and backtest configs gain an explicit `data_provider` field:
`crsp | alpaca_sip | hybrid_crsp_universe_sip_prices`. ADR-016 amended.

### D6. Storage layout — separate roots, no entanglement with CRSP

```
data/wrds/crsp/daily/                       # unchanged
data/alpaca/sip/daily/YYYY.parquet          # new
data/alpaca/sip/corp_actions/YYYY.parquet   # new
data/alpaca/sip/listings/listings.parquet   # new (optional, for D4 if pursued)
data/manifests/                             # both providers' manifests live here
```

Nothing in CRSP code paths changes.

## Phased Rollout

### Phase 0 — Spike (1–2 days)

**Success criterion**: bar-for-bar agreement with CRSP on a fixed survivor-set
within a defined tolerance. This validates D3 (hybrid) — pure replacement is
not the goal, so delisted coverage is not the gate.

- Verify SIP entitlement against the existing Alpaca account. The
  `ALPACA_DATA_FEED` env var **supports** `sip` but the repo default is
  `iex` (`.env.example:14`, `docker-compose.yml:191,276`); the spike must
  set it explicitly and confirm against a live API call.
- Pull 5 years of daily SIP bars for ~100 currently-listed symbols.
- Compare to CRSP bar-for-bar:
  - close-price agreement (modulo expected adjustment differences)
  - Alpaca-adjusted-close-derived returns vs CRSP `ret` (Risk #1)
  - split/dividend event alignment
- Document per-symbol residuals. Quantify rate-limit budget for full bulk sync.
- Output: a short comparison report and go/no-go on Phase 1.

### Phase 1 — Provider plumbing (1 week)

- New `libs/data/data_providers/alpaca_sip_local_provider.py` — sibling of
  `crsp_local_provider.py`. DuckDB over parquet, manifest pinning.
- `AlpacaSIPDataProviderAdapter` in `protocols.py`. Maps Alpaca bars to the
  unified schema, including computing the `ret` column from
  `adjustment=all` adjusted closes (Alpaca does not return `ret` directly).
  `supports_universe=False`, `is_production_ready=False` (D2).
- `ProviderType.ALPACA_SIP` enum value and selection wiring in
  `unified_fetcher.py`.
- **Backtest-path enum + dispatch + UI mapping updates** (required for
  Phase 4 to be executable): add `ALPACA_SIP` (and later
  `HYBRID_CRSP_UNIVERSE_SIP_PRICES`) to `libs/trading/backtest/job_queue.py:36`
  `DataProvider` enum; extend the `if/elif` provider dispatch in
  `libs/trading/backtest/worker.py:403,442`; add the option to the data-source
  dropdown at `apps/web_console_ng/pages/backtest.py:534`; update the
  binary mappers at `apps/web_console_ng/pages/backtest.py:737,844`; add
  the new entry to `PROVIDER_DISPLAY` in
  `apps/web_console_ng/components/config_editor.py:37-38` and ensure the
  validators at lines 128-132 and 235-241 accept it.
- New ADR (ADR-0041 or next available number) covering: SIP semantics,
  adjustment policy, hybrid-provider pattern, ADR-016 amendment.
- Tests in `tests/libs/data/data_providers/test_alpaca_sip_provider.py`
  mirroring `test_crsp_local_provider.py` structure.

### Phase 2 — Bulk sync + manifests (1 week)

- New `libs/data/data_providers/alpaca_sip_sync.py` — paginated bulk historical
  sync via `StockBarsRequest`, throttled, idempotent. Reuse patterns from
  `sync_manager.py`. **Daily-bar sync foundation implemented.**
- New `libs/data/data_providers/alpaca_corp_actions_sync.py` — splits,
  dividends, symbol changes. Persisted under `data/alpaca/sip/corp_actions/`.
  **Deferred pending SDK/REST decision.**
- Quarantine on validation failure via existing `data_quality` framework.
- `make` targets: `make sync-alpaca-sip`, `make sync-alpaca-sip-status`,
  `make sync-alpaca-sip-verify`; `make sync-alpaca-corp-actions` remains
  deferred with corporate-actions ingestion.
- Surface in the web-console-ng data-sync UI. Note: today
  `libs/web_console_services/data_sync_service.py:28` lists supported
  datasets as `crsp|compustat|taq|fama_french`. This phase adds
  `alpaca_sip` to that set. `alpaca_corp_actions` remains deferred.

### Phase 3 — Hybrid provider (3–5 days)

- `HybridDataProviderAdapter` per D3.
- `ProviderType.HYBRID_CRSP_UNIVERSE_SIP_PRICES`.
- Dedicated tests covering: universe queries route to CRSP, price queries
  route to SIP, schema unification, delisted-symbol behavior (price gap when
  SIP lacks the ticker — explicit error or empty rows, not silent fill).

### Phase 4 — Strategy migration (open-ended)

- Pick one strategy (recommend `research/strategies/momentum/`).
- Re-train a **copy** of the strategy with `data_provider: alpaca_sip`
  alongside the existing CRSP-trained version.
- Walk-forward compare on a fixed out-of-sample window. Monitor Sharpe,
  turnover, hit rate, max drawdown deltas.
- Promote SIP variant to production only if deltas stay within tolerance and
  the strategy benefits from execution-feed parity.
- Repeat per strategy. CRSP-trained variants remain available indefinitely.

### Phase 5 — Defaults

- No global default flip. CRSP stays default for ad-hoc backtests.
- Per-strategy default lives in the strategy config.
- Document the per-strategy recommendation: **CRSP for long-history
  research and any backtest that depends on point-in-time universe;
  Alpaca SIP for execution-feed parity post-2016; hybrid only for
  strategies whose backtest harness has been wired to consume
  `get_universe()`; yfinance for dev/CI**.

## Risks

1. **`ret` reconciliation (HIGH)**. CRSP `ret` is pre-adjusted and is the
   canonical performance signal. Alpaca returns are derived from
   `adjustment=all` adjusted close. Phase 0 must verify these match within
   rounding, otherwise hybrid backtests will silently diverge from pure-CRSP
   backtests on the same strategy.
2. **Bulk-sync time/cost (MEDIUM)**. Full universe × ~10 years could be
   hundreds of millions of bars. Phase 0 must produce a concrete time and
   API-quota estimate before committing to Phase 2.
3. **SIP entitlement (LOW, easily verified)**. `ALPACA_DATA_FEED` config
   supports `sip` but the repo default is `iex`; verify the account has
   the entitlement and that an explicit `sip` setting works against the
   live API before Phase 1.
4. **Per-job config plumbing (MEDIUM)**. `UnifiedDataFetcher` callers across
   the codebase assume a single fetcher. Per `libs/CLAUDE.md`, grep all call
   sites before changing signatures.
5. **Survivorship gap in hybrid (LOW)**. When SIP lacks a ticker that CRSP
   knows about, the hybrid provider will have a price gap. The behavior must
   be deterministic (explicit) and surfaced in backtest reports.

## Open Questions

- Adjustment policy: do we store SIP bars at `adjustment=raw` and adjust at
  read time (mirrors CRSP), or store at `adjustment=all` (simpler downstream)?
  Recommendation: store raw + corp-actions table, adjust at read — preserves
  point-in-time correctness.
- Manifest schema: does the existing `ManifestManager` need extension for
  SIP-specific metadata (e.g., feed=sip, adjustment_mode), or is a sibling
  manifest cleaner?
- Hybrid output `adj_close`: SIP has all OHLC; should the hybrid populate
  `adj_close` from SIP even when CRSP would have left it null? Likely yes —
  document it as one of the hybrid's value-adds.

## Out of Scope for This Plan

- IEX feed as a separate provider (free tier; not the same data)
- Quotes/trades historical features (Phase 1+ enables this but no specific
  microstructure feature is committed here)
- Replacing the live `MarketDataProvider` in `libs/data/market_data/`
- Any change to existing CRSP code paths

## References

- ADR-016: Data Provider Protocol — `docs/ADRs/ADR-016-data-provider-protocol.md`
- CRSP concepts — `docs/CONCEPTS/crsp-data.md`
- Unified fetcher concepts — `docs/CONCEPTS/unified-data-fetcher.md`
- Existing Alpaca wrapper — `libs/data/market_data/provider.py`
- Provider protocol — `libs/data/data_providers/protocols.py`
- CRSP local provider — `libs/data/data_providers/crsp_local_provider.py`
