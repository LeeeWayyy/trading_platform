# ADR-0040: TCA API Nullable Fee Fields (Fail-Closed)

**Status:** Accepted
**Date:** 2026-04-19
**Deciders:** @development-team
**Related:** Issue #158

## Context

The Transaction Cost Analysis (TCA) API reports fee-derived metrics (`fee_cost_bps`, `avg_fee_cost_bps`, `total_fees`) aggregated across an order's fills. Prior to this change, these fields were typed as non-nullable `float`.

Two data-quality scenarios produce arithmetically invalid aggregates:
1. **Mixed fee currencies:** Summing fills priced in different currencies (e.g., USD + EUR) without FX normalization yields a meaningless number.
2. **Non-USD fee currency:** The system's cost-bps formulas assume USD throughout (the order notional is in USD). A single non-USD fee silently breaks the dimensional analysis.

Before this change, the TCA service would emit a warning string and return the incorrect aggregate anyway. Downstream consumers (web console charts, reports) had no type-safe signal that the number was untrustworthy.

## Decision

Treat untrusted fee aggregates as missing data. Fail closed at the API contract by making fee fields nullable.

### Schema changes

`TCAOrderDetail` and `TCAAnalysisSummary` (in `apps/execution_gateway/routes/schemas.py`):

- `fee_cost_bps: float` → `float | None`
- `avg_fee_cost_bps: float` → `float | None`
- `total_fees: float` → `float | None`

When the analyzer detects mixed or non-USD fee currencies, these fields are returned as `null`. The `implementation_shortfall_bps` aggregate excludes the fee component for affected orders (price + opportunity only), and the summary includes a warning message listing the exclusion count.

### Internal representation

Inside `libs/platform/analytics/execution_quality.py`, `ExecutionAnalysisResult` keeps `fee_cost_bps: float` and uses `NaN` as the sentinel. `total_fees` is `float | None`. The mismatch is intentional: `fee_cost_bps` participates in float arithmetic throughout the cost decomposition, and NaN propagates naturally; `total_fees` is only ever consumed as a scalar. The route layer (`_result_to_order_detail`) converts NaN → None at the API boundary so clients see a single representation.

### Aggregate semantics

`_avg_fee_cost_bps` uses the total order count as the denominator (not the count of orders with valid fees), so `avg_price + avg_fee + avg_opportunity ≈ avg_implementation_shortfall` holds — consistent with how other components are averaged. Returns `None` when no orders have trustworthy fees.

## Consequences

### Positive

- Downstream type checkers flag code that assumes fee fields are always numeric
- Charts can distinguish "fee = 0 bps" from "fee unknown" and render accordingly
- The cost decomposition identity holds at the summary level
- Silent misaggregation is impossible — the API either returns a valid number or `null`

### Negative

- **Breaking change** for any typed client. Mitigations:
  - The ONLY current consumer is the in-repo NiceGUI web console, updated in the same PR
  - `rg api/v1/tca` repo-wide confirms no other typed consumers
  - The API is internal-only (not exposed beyond the deployment)

### Forward compatibility

If external consumers are later added, introduce `/api/v2/tca` with a backwards-compatible default (e.g., `0.0` instead of `None`, plus a sibling boolean `fee_trustworthy`) and run a deprecation period on v1. Do not retrofit nullable fields with magic sentinels in v1.

## Alternatives Considered

**1. Keep non-nullable, emit warning only.** Rejected: the previous behavior. Consumers cannot distinguish "real 0 bps fee" from "we summed EUR + USD and got a number, good luck." Warnings are not type-safe.

**2. Make `fee_cost_bps` NaN end-to-end (never None).** Rejected: `None` is the idiomatic "missing value" in JSON and in Pydantic schemas. NaN in JSON is non-standard (`null` vs `NaN` vs `"NaN"` depends on encoder). Keep NaN as an internal-only sentinel.

**3. Normalize non-USD fees to USD via FX.** Rejected for this change: FX normalization needs a trusted rate source, a policy for stale rates, and test coverage that didn't exist. Can be added later without another breaking change (nullable stays nullable — normalization just reduces how often `null` is returned).

## References

- Issue #158 — original report of mixed/non-USD fee currency handling
- `libs/platform/analytics/execution_quality.py` — analyzer fail-closed logic
- `apps/execution_gateway/routes/schemas.py` — nullable field definitions
- `apps/execution_gateway/routes/tca.py` — route-level conversion (NaN → None)
- `apps/web_console_ng/pages/execution_quality.py` — consumer adaptation
