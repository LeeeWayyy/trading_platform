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
