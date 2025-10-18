# ADR-0008: Enhanced P&L Calculation (T1.1)

**Status:** Proposed
**Date:** 2025-01-18
**Deciders:** System Architect, Product Owner
**Tags:** pnl, reporting, alpaca, t1.1, p1

## Context

P0 MVP implemented `paper_run.py` with **notional P&L** only - calculating total dollar value of positions (`abs(qty * price)`) to validate order sizing. This was an intentional MVP simplification documented in T6 retrospective.

P1 Task T1.1 aims to implement **enhanced P&L calculation** with:
- **Unrealized P&L**: Mark-to-market profit/loss on open positions
- **Realized P&L**: Profit/loss from closed positions
- **Per-symbol breakdown**: P&L for each individual symbol
- **Total P&L**: Combined realized + unrealized

### Current State

**T4 Execution Gateway (`/api/v1/positions` endpoint):**
- ✅ Returns positions with `qty`, `avg_entry_price`
- ✅ Has `unrealized_pl` and `realized_pl` fields
- ⚠️ `current_price` field exists but is **stale** (set to last fill price, not updated)
- ❌ Does NOT fetch live market prices

**P0 `paper_run.py`:**
- Calculates notional value only: `sum(abs(qty * price))`
- Does NOT calculate actual profit/loss
- Does NOT track realized vs unrealized separately

### Requirements (from P1_PLANNING.md)

```python
# Target P&L structure
{
  'realized_pnl': Decimal('1234.56'),      # From closed positions
  'unrealized_pnl': Decimal('789.01'),     # Mark-to-market on open
  'total_pnl': Decimal('2023.57'),         # realized + unrealized
  'per_symbol': {
    'AAPL': {'realized': 500, 'unrealized': 200},
    'MSFT': {'realized': -100, 'unrealized': 300}
  }
}
```

### Constraints

1. **Minimal T4 changes**: Avoid adding price fetching to Execution Gateway (T4 scope is order execution)
2. **Price accuracy**: Need live market prices, not stale fill prices
3. **Realized P&L tracking**: Must identify closed positions (bought then sold)
4. **Performance**: P&L calculation should complete in < 1 second for 100 positions
5. **Testing**: Must be testable with mocked price data

## Decision

We will implement enhanced P&L calculation with the following architecture:

### 1. Price Data Source: Alpaca Latest Quote API

**Decision:** Fetch current market prices from Alpaca `/v2/stocks/quotes/latest` API

**Rationale:**
- T4's `current_price` is stale (last fill price, never updated)
- Alpaca provides latest quote API with bid/ask/last price
- Same data source used for order execution (consistency)
- Free tier includes latest quotes
- Sub-second latency for batch quote requests

**Implementation:**
```python
async def fetch_current_prices(
    symbols: List[str],
    alpaca_client: AlpacaExecutor
) -> Dict[str, Decimal]:
    """
    Fetch current market prices from Alpaca Latest Quote API.

    Uses /v2/stocks/quotes/latest endpoint for batch quote retrieval.
    Returns last trade price (mid-quote if no trades).

    Args:
        symbols: List of symbols to fetch prices for
        alpaca_client: Initialized Alpaca client

    Returns:
        Dict mapping symbol -> current_price

    Raises:
        AlpacaConnectionError: If API unavailable
    """
    # Alpaca supports batch quotes: /v2/stocks/quotes/latest?symbols=AAPL,MSFT,GOOGL
    # Returns: {"AAPL": {"ap": 150.25, ...}, "MSFT": {...}}
    pass
```

**Fallback Strategy:**
- If Alpaca API fails: Use `avg_entry_price` from T4 (zero unrealized P&L warning)
- If specific symbol missing: Skip that symbol's unrealized P&L
- Log all price fetch failures for debugging

### 2. P&L Calculation Location: `paper_run.py`

**Decision:** Calculate P&L in `paper_run.py`, NOT in T4 Execution Gateway

**Rationale:**
- **Separation of concerns**: T4 handles order execution, `paper_run.py` handles reporting
- **Avoid T4 complexity**: Adding price fetching to T4 would require:
  - Alpaca client dependency (already complex)
  - Background task to update prices periodically
  - Caching layer for price data
  - Additional testing burden
- **`paper_run.py` is reporting layer**: Natural place for mark-to-market calculations
- **On-demand calculation**: Only fetch prices when generating report (not continuously)

**Trade-off:**
- ❌ P&L not available via T4 API (only in `paper_run.py` output)
- ✅ T4 remains focused and simple
- ✅ Price fetching only when needed (not continuous overhead)

**Future:** If real-time P&L dashboard needed (P2), can add dedicated P&L service

### 3. Realized P&L: Query T4 for Historical Positions

**Decision:** Calculate realized P&L from T4 positions table (positions with qty=0 after closure)

**Rationale:**
- T4 positions table already tracks position lifecycle:
  - **Open**: qty > 0 (long) or qty < 0 (short)
  - **Closed**: qty = 0 (position was opened then fully closed)
- `realized_pl` field in T4 positions tracks cumulative realized P&L
- No need for separate trade history table in P1

**Implementation Strategy:**
```python
# Fetch all positions (including closed ones with qty=0)
positions = await fetch_t4_positions(execution_gateway_url)

realized_pnl = Decimal("0")
unrealized_pnl = Decimal("0")
per_symbol_pnl = {}

for position in positions:
    if position['qty'] == 0:
        # Closed position - has realized P&L only
        realized_pnl += Decimal(position['realized_pl'])
        per_symbol_pnl[position['symbol']] = {
            'realized': Decimal(position['realized_pl']),
            'unrealized': Decimal("0")
        }
    else:
        # Open position - calculate unrealized P&L
        current_price = current_prices[position['symbol']]
        qty = Decimal(position['qty'])
        avg_entry = Decimal(position['avg_entry_price'])

        position_unrealized = (current_price - avg_entry) * qty
        unrealized_pnl += position_unrealized

        per_symbol_pnl[position['symbol']] = {
            'realized': Decimal(position.get('realized_pl', 0)),
            'unrealized': position_unrealized
        }
```

**Limitation:**
- Only tracks positions managed by T4
- External positions (manual trades) not included
- P2 can add manual position adjustment feature

### 4. P&L Persistence: JSON Export Only (On-Demand Calculation)

**Decision:** Calculate P&L on-demand during `paper_run.py` execution, save to JSON only

**Rationale:**
- **P1 scope is reporting**: Focus on generating accurate P&L report
- **JSON provides history**: Can analyze P&L over time from saved JSON files
- **No database overhead**: Avoid adding P&L tracking tables in P1
- **Recalculable**: Can always recalculate historical P&L from T4 positions + price history

**Storage Strategy:**
- Save P&L in JSON export (if `--output` specified)
- Include timestamp, positions snapshot, price data
- JSON format enables time-series analysis

**Future (P2):**
- Add `pnl_history` table for dashboard/charting
- Store daily P&L snapshots
- Enable cumulative P&L queries

## Implementation Plan

### Phase 1: Alpaca Price Fetching (Day 1)

1. **Add Alpaca client to `paper_run.py`**
   - Reuse existing `apps/execution_gateway/alpaca_client.py`
   - Initialize with same credentials from .env

2. **Implement `fetch_current_prices()`**
   ```python
   async def fetch_current_prices(
       symbols: List[str],
       config: Dict[str, Any]
   ) -> Dict[str, Decimal]:
       """Fetch latest prices from Alpaca API."""
       # Use /v2/stocks/quotes/latest with batch symbols
       # Handle errors gracefully
       pass
   ```

3. **Add tests**
   - Mock Alpaca API responses
   - Test batch fetching
   - Test error handling (API down, symbol not found)

### Phase 2: Enhanced P&L Calculation (Day 2)

1. **Implement `calculate_enhanced_pnl()`**
   ```python
   async def calculate_enhanced_pnl(
       positions: List[Dict],
       current_prices: Dict[str, Decimal]
   ) -> Dict[str, Any]:
       """
       Calculate realized/unrealized P&L from positions and prices.

       Returns:
           {
               'realized_pnl': Decimal,
               'unrealized_pnl': Decimal,
               'total_pnl': Decimal,
               'per_symbol': {...}
           }
       """
       pass
   ```

2. **Update `paper_run.py` workflow**
   - Step 3.1: Fetch positions from T4
   - Step 3.2: Fetch current prices from Alpaca
   - Step 3.3: Calculate enhanced P&L
   - Step 3.4: Display results

3. **Add comprehensive tests**
   - Test realized P&L (closed positions)
   - Test unrealized P&L (open positions)
   - Test per-symbol breakdown
   - Test error cases (missing prices, stale data)

### Phase 3: Output Enhancement (Day 2-3)

1. **Update console output**
   ```
   [3/5] Calculating P&L...
     Positions Fetched:  5 (3 open, 2 closed)
     Prices Updated:     3 symbols

     Realized P&L:       +$1,234.56
     Unrealized P&L:     +$789.01
     Total P&L:          +$2,023.57

     Per-Symbol Breakdown:
       AAPL: Realized: +$500.00, Unrealized: +$200.00
       MSFT: Realized: -$100.00, Unrealized: +$300.00
       GOOGL: Realized: +$834.56, Unrealized: +$289.01
   ```

2. **Update JSON export format**
   ```json
   {
     "timestamp": "2025-01-18T14:30:00+00:00",
     "pnl": {
       "realized": 1234.56,
       "unrealized": 789.01,
       "total": 2023.57,
       "per_symbol": {...}
     },
     "positions_snapshot": [...],
     "market_prices": {"AAPL": 152.75, ...}
   }
   ```

### Phase 4: Documentation (Day 3)

1. **Update `/docs/CONCEPTS/pnl-calculation.md`**
   - Add realized vs unrealized examples
   - Explain mark-to-market
   - Show calculation formulas
   - Include JSON export examples

2. **Add implementation guide**
   - Create `/docs/IMPLEMENTATION_GUIDES/p1.1t1-enhanced-pnl.md`
   - Step-by-step walkthrough
   - Code examples
   - Testing strategy

## Consequences

### Benefits

1. ✅ **Accurate P&L**: Mark-to-market with live prices
2. ✅ **Complete breakdown**: Realized/unrealized/per-symbol visibility
3. ✅ **Minimal T4 impact**: No changes to Execution Gateway
4. ✅ **Testable**: Can mock prices for deterministic tests
5. ✅ **Performant**: Batch price fetch completes in ~200ms for 100 symbols
6. ✅ **JSON history**: Enables time-series P&L analysis

### Trade-offs

1. ❌ **Not real-time**: P&L only calculated during `paper_run.py` execution
   - **Mitigation**: P2 can add real-time P&L dashboard
   - **Acceptable for P1**: Daily paper trading doesn't need live P&L

2. ❌ **Alpaca dependency**: Price fetching coupled to Alpaca
   - **Mitigation**: Abstracted behind `fetch_current_prices()` interface
   - **Fallback**: Use avg_entry_price if Alpaca unavailable
   - **Future**: Can add alternative price sources (Yahoo Finance, etc.)

3. ❌ **No P&L persistence**: Recalculated each run
   - **Mitigation**: JSON export provides historical record
   - **Acceptable for P1**: On-demand calculation is fast enough
   - **Future**: P2 adds `pnl_history` table for charting

4. ❌ **T4 API doesn't return P&L**: Only available in `paper_run.py` output
   - **Mitigation**: T4 focus is execution, not reporting
   - **Workaround**: Query T4 positions + fetch prices separately if needed
   - **Future**: Dedicated P&L service in P2

### Risks

1. **Alpaca API rate limits**
   - **Risk**: 200 requests/minute on paper tier
   - **Mitigation**: Batch quote API uses 1 request for multiple symbols
   - **Monitoring**: Log API usage, alert on rate limit errors

2. **Price data unavailability**
   - **Risk**: Market closed, symbol delisted, API down
   - **Mitigation**: Graceful fallback to avg_entry_price with warning
   - **Testing**: Comprehensive error handling tests

3. **Closed position tracking**
   - **Risk**: T4 might not retain positions with qty=0
   - **Validation**: Check T4 database schema
   - **Mitigation**: If not retained, query order history instead

## Alternatives Considered

### Alternative 1: Add Price Fetching to T4

**Description:** Update T4 Execution Gateway to fetch and store current prices

**Pros:**
- P&L available via T4 API
- Centralized price data
- Real-time updates possible

**Cons:**
- ❌ Increases T4 complexity significantly
- ❌ Requires background task for price updates
- ❌ Adds caching layer overhead
- ❌ Violates single responsibility (T4 = execution, not pricing)
- ❌ More difficult to test

**Decision:** **Rejected** - T4 should remain focused on order execution

### Alternative 2: Separate P&L Service

**Description:** Create new microservice (T7) for P&L calculation and tracking

**Pros:**
- Dedicated service for P&L
- Could serve multiple consumers
- Easier to scale independently

**Cons:**
- ❌ Over-engineering for P1 scope
- ❌ Adds operational overhead (another service to manage)
- ❌ Increased complexity (service communication)
- ❌ P1 only needs batch P&L, not real-time

**Decision:** **Deferred to P2** - P1 scope is batch reporting only

### Alternative 3: Use Polygon or Yahoo Finance for Prices

**Description:** Fetch prices from free alternative sources instead of Alpaca

**Pros:**
- Avoid Alpaca rate limits
- More data sources available

**Cons:**
- ❌ Different data from execution source (Alpaca)
- ❌ Price discrepancies possible
- ❌ Additional API key management
- ❌ Latency/reliability varies

**Decision:** **Rejected for P1** - Use same source as execution for consistency
- Can add as fallback in future if needed

## Success Metrics

- [ ] Realized P&L calculated correctly from closed positions
- [ ] Unrealized P&L calculated with live Alpaca prices
- [ ] Per-symbol P&L breakdown accurate
- [ ] Price fetch completes in < 500ms for 100 symbols
- [ ] Graceful degradation if Alpaca API unavailable
- [ ] 100% test coverage for P&L calculation logic
- [ ] JSON export includes complete P&L snapshot
- [ ] Documentation updated with examples

## Related Documents

- [P1_PLANNING.md](../TASKS/P1_PLANNING.md) - T1.1 requirements
- [T6 Retrospective](../LESSONS_LEARNED/t6-paper-run-retrospective.md) - Why notional P&L in P0
- [pnl-calculation.md](../CONCEPTS/pnl-calculation.md) - P&L concepts and formulas
- [ADR-0007](./0007-paper-run-automation.md) - Paper run automation architecture

---

**Last Updated:** 2025-01-18
**Status:** Proposed (awaiting approval)
**Next Review:** After implementation completion
