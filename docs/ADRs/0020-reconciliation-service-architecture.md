# ADR-0020: Reconciliation Service Architecture

**Status:** Accepted
**Date:** 2025-12-17
**Deciders:** AI Assistant (Claude Code), reviewed by Gemini and Codex
**Tags:** execution, reconciliation, safety, startup-gating

## Context

The execution gateway currently lacks a reconciliation mechanism between the local database and Alpaca broker state. This creates several reliability and safety issues:

1. **Split-brain risk:** If Alpaca fills/rejects an order but the webhook fails to deliver, the database remains stale
2. **Zombie slices:** TWAP slices use in-memory APScheduler; on restart, pending slices are lost
3. **Orphan orders:** Orders placed directly in Alpaca (or via other clients) are invisible to our system
4. **Double-fill risk:** Naive slice recovery could re-submit orders that Alpaca already received

The system needs:
- Startup reconciliation before accepting new orders
- Periodic reconciliation to detect drift
- Safe handling of orphan orders
- Startup gating to prevent unsafe operations during reconciliation

## Decision

Implement a reconciliation service with startup gating and orphan handling:

### Components

1. **ReconciliationService** (`apps/execution_gateway/reconciliation.py`):
   - Startup reconciliation: Query Alpaca open orders, reconcile with DB
   - Periodic reconciliation: Every 5 minutes, poll all orders and positions
   - Conflict resolution via CAS (Compare-And-Swap) updates with status ranking

2. **Startup Gating**:
   - Set `startup_reconciliation_complete = False` at startup
   - Block new order submissions (503) until reconciliation completes
   - Allow risk-reducing operations during gating:
     - Cancel orders
     - Reduce-only orders (computed from LIVE Alpaca data, not stale DB)

3. **Orphan Order Handling**:
   - Store orphans in dedicated `orphan_orders` table (not main `orders` table)
   - Quarantine by `{strategy_id}:{symbol}` - fail-closed with `*` when strategy unknown
   - Track orphan exposure separately for risk calculations

4. **Conflict Resolution (CAS)**:
   - Add columns: `last_updated_at`, `is_terminal`, `source_priority`, `status_rank`
   - Terminal state lock: Once filled/canceled/expired/failed, only allow fill qty updates
   - Status rank ordering ensures proper state progression

### Key Design Choices

**Startup Gating with Reduce-Only Mode:**
- During gating, compute `effective_position` from LIVE Alpaca data (not stale DB)
- Call `TradingClient.get_open_position(symbol)` - treat 404 as flat
- Reject all orders if broker API unavailable (fail closed)

**Fail-Closed Quarantine:**
- `client_order_id` is a 24-char SHA256 hash - strategy NOT decodable
- Unknown orphans default to `strategy_id = "external"` sentinel
- Quarantine scope defaults to `*:{symbol}` (blocks all strategies) until operator verifies

**Alpaca API Constraints:**
- `after` param filters by `created_at`, NOT `updated_at`
- No `client_order_id` pattern queries supported
- Reconciliation must: (1) pull all open orders, (2) individually query non-terminal DB orders

**Permission Model:**
- Manual override requires `Permission.MANAGE_RECONCILIATION`
- Operator identity logged from `request.state.user["user_id"]` (X-User-Id header)

## Consequences

### Positive

- **Safety:** Startup gating prevents orders based on stale state
- **Reliability:** Periodic reconciliation detects and heals drift
- **Observability:** Orphan orders are tracked and quarantined with alerts
- **Idempotency:** CAS updates prevent race conditions between webhooks and reconciliation

### Negative

- **Startup Delay:** Service unavailable until reconciliation completes (mitigated by timeout + manual override)
- **API Load:** Periodic polling adds Alpaca API calls (mitigated by 5-minute interval)
- **Complexity:** Orphan handling adds operational burden (mitigated by auto-cancel policy option)

### Risks

- **Alpaca API Changes:** Pagination/filtering behavior may change; tests should mock realistic responses
- **Clock Skew:** `last_updated_at` comparisons assume synchronized clocks

### Migration

1. Run migration to add conflict resolution columns + `orphan_orders` table
2. Backfill `is_terminal` for existing orders
3. Deploy reconciliation service with startup gating
4. Monitor `reconciliation_mismatches_total` metric

## Related

- [ADR-0014: Execution Gateway Architecture](./0014-execution-gateway-architecture.md)
- [ADR-0015: TWAP Order Slicer](./0015-twap-order-slicer.md)
- [BUGFIX_RELIABILITY_SAFETY_IMPROVEMENTS.md](../TASKS/BUGFIX_RELIABILITY_SAFETY_IMPROVEMENTS.md)
