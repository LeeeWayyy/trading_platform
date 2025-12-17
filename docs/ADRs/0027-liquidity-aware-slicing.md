# ADR-0027: Liquidity-Aware TWAP Slicing

**Status:** Accepted
**Date:** 2025-12-17
**Deciders:** AI Assistant (Claude Code), reviewed by Gemini and Codex
**Tags:** execution, TWAP, liquidity, market-impact

## Context

The TWAP slicer (ADR-0015) divides orders evenly across time intervals without considering market liquidity. This causes issues in thin markets:

1. **Market Impact:** A single slice could consume entire bid/ask depth
2. **Slippage:** Large slices in illiquid symbols cause adverse price movement
3. **TWAP Purpose Defeated:** The algorithm's goal (minimize market impact) is undermined

However, modifying slice sizes at execution time would break `client_order_id` determinism and cause duplicate orders.

## Decision

Add liquidity-aware slice sizing at PLAN time, keeping TWAPSlicer stateless:

### Components

1. **LiquidityService** (`apps/execution_gateway/liquidity_service.py`):
   - Fetch ADV (Average Daily Volume) from Alpaca Market Data API
   - Cache ADV with 24-hour TTL (refresh at market open)
   - Provide `get_adv(symbol)` method

2. **Service Layer Integration** (`main.py`):
   - Fetch ADV BEFORE calling TWAPSlicer
   - Calculate `max_slice_qty = ADV * MAX_SLICE_PCT_OF_ADV`
   - Pass `max_slice_qty` as argument to `TWAPSlicer.plan()`

3. **TWAPSlicer Enhancement**:
   - Accept optional `max_slice_qty` parameter
   - If slice would exceed max, create more smaller slices
   - All sizing happens at PLAN time, not execution time

4. **Persistence**:
   - Store `liquidity_constraints` with slicing plan in database
   - Recovery uses persisted constraints (not fresh ADV)
   - Preserves `client_order_id` determinism

### Key Design Choices

**Keep TWAPSlicer Pure:**
- No I/O inside slicer (remains stateless and testable)
- Liquidity data fetched in service layer
- Slicer receives constraints as arguments

**Plan-Time Only:**
- Slice sizes fixed when plan is created
- Execution uses planned sizes regardless of current liquidity
- `client_order_id` remains deterministic

**ADV Data Source:**
- Alpaca Market Data API: `GET /v1beta1/stocks/{symbol}/bars?timeframe=1Day&limit=20`
- 20-day average volume calculation
- Graceful degradation: If API unavailable, skip liquidity check with WARNING

**Conservative Default:**
- `MAX_SLICE_PCT_OF_ADV = 0.01` (1% of daily volume per slice)
- Configurable per deployment

## Consequences

### Positive

- **Reduced Slippage:** Slices sized appropriately for market depth
- **Maintained Idempotency:** `client_order_id` determinism preserved
- **Testability:** TWAPSlicer remains stateless and pure
- **Graceful Degradation:** Works without liquidity data (with warning)

### Negative

- **Additional API Calls:** ADV lookup adds latency to TWAP order creation
- **Stale ADV:** 24-hour cache may not reflect intraday liquidity changes
- **More Slices:** Illiquid symbols result in more (smaller) slices

### Risks

- **ADV Accuracy:** Historical ADV may not predict current liquidity
- **Cache Staleness:** Daily refresh may miss significant liquidity events

### Configuration

```python
LIQUIDITY_CHECK_ENABLED = True    # default
MAX_SLICE_PCT_OF_ADV = 0.01       # 1% of ADV per slice
ADV_CACHE_TTL_HOURS = 24          # refresh daily
ADV_LOOKBACK_DAYS = 20            # 20-day average
```

## Related

- [ADR-0015: TWAP Order Slicer](./0015-twap-order-slicer.md)
- [BUGFIX_RELIABILITY_SAFETY_IMPROVEMENTS.md](../TASKS/BUGFIX_RELIABILITY_SAFETY_IMPROVEMENTS.md)
