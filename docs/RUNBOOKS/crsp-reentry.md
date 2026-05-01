# CRSP Re-Entry Runbook

Status: stub for Alpaca SIP rollout

Use this runbook when CRSP access/data is restored after a SIP-first outage
period. The target is config-only re-entry: strategy code should not change.

## Preconditions

- CRSP sync path is restored and verified.
- `data/wrds/**` and `libs/data/data_providers/crsp_*` semantics were not
  changed by the SIP rollout.
- SIP manifests include `provider_id`, `provider_version`, `manifest_id`,
  `source_feed`, `adjustment_mode`, `symbol_set_hash`, and sync timestamps.
- Backtest outputs include `data_signature` and role metadata.

## Re-Entry Steps

1. Set `CRSP_AVAILABLE=true`.
2. Set `HISTORICAL_UNIVERSE_SOURCE_DEFAULT=crsp`.
3. Keep `HISTORICAL_PRICE_SOURCE_DEFAULT` strategy-specific:
   - `crsp` for long-history research.
   - `alpaca_sip` for production-feed parity.
   - `yfinance` only for dev/CI.
4. Keep `HISTORICAL_CORP_ACTIONS_SOURCE_DEFAULT` strategy-specific.
5. Run a provider-role dry run with `provider=auto` and no code changes.
6. Confirm resolved roles are visible in logs, manifests, and backtest reports.

## Deferred Gates

- Run CRSP-vs-SIP reconciliation on fixed symbol sets and affected strategies.
- Until CRSP is available, keep using the IEX-vs-SIP monitor only as a SIP
  data-quality signal:

  ```bash
  make alpaca-feed-delta ARGS="--symbols AAPL,MSFT --start 2026-04-20 --end 2026-04-24 --timeframe 5Min --output data/quality/alpaca_iex_sip_delta.json"
  ```

  This monitor reports coverage gaps, timestamp/session alignment, OHLC sanity,
  price deltas, and liquidity-bucket volume outliers. It must not be treated as
  a bar-for-bar equivalence gate because IEX is venue-specific and SIP is
  consolidated tape.
- Compare coverage gaps, date/session alignment, return/adjustment semantics,
  OHLC sanity, volume outliers, and strategy metrics.
- Confirm strategies declaring `requires_pit_universe=true` resolve universe
  source to `crsp` before promotion.
- Confirm SIP-only reports remain marked as non-survivorship-safe.
- Re-run one SIP-era strategy with CRSP universe by config only and compare
  `data_signature` inputs.

## Rollback

If CRSP data fails validation, revert only environment defaults:

```env
CRSP_AVAILABLE=false
HISTORICAL_UNIVERSE_SOURCE_DEFAULT=explicit_symbols
HISTORICAL_PRICE_SOURCE_DEFAULT=alpaca_sip
HISTORICAL_CORP_ACTIONS_SOURCE_DEFAULT=alpaca_sip
```

Do not modify strategy code or CRSP provider code as part of rollback.
