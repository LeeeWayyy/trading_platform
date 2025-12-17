# Bugfix: Reliability and Safety Improvements

**Branch:** `bugfix/reliability-and-safety-improvements`
**Created:** 2025-12-16
**Priority:** HIGH
**Review Status:** Revision 15 - Addressing plan review corrections (ADR numbering, orphan strategy resolution, Alpaca reconciliation query notes, permission decorator usage)

---

## Executive Summary

This document analyzes the reliability and safety concerns raised in a comprehensive architecture review. After thorough investigation of the codebase, we have validated which issues are genuine bugs requiring fixes and which are either already addressed or represent acceptable design tradeoffs.

**Post-Review Updates:** Incorporated feedback from Gemini and Codex reviews. Key changes:
- Reconciliation promoted to HIGH (prerequisite for safe Zombie Slice recovery)
- Zombie Slice recovery must respect client_order_id idempotency and include grace period
- Liquidity constraints folded into slicing plan (keep TWAPSlicer pure)
- Shadow validation moved to signal_service layer (access to feature data)
- Corrected ADR numbering per ADR guide + clarified orphan strategy resolution (client_order_id is hashed)
- Replaced unsupported Alpaca "client_order_id pattern" query with feasible reconciliation fallback

---

## Bug Analysis Results

### Validated Issues (Require Fixes)

| Priority | Issue | Location | Risk Level |
|----------|-------|----------|------------|
| **HIGH** | Zombie Slices - TWAP slices lost on restart | `apps/execution_gateway/slice_scheduler.py` | Critical for trading operations |
| **HIGH** | Cold Start - Signal service has no startup hydration | `apps/signal_service/main.py` | First signals may be invalid |
| **HIGH** | Missing Reconciliation Loop - No periodic Alpaca state sync | `apps/execution_gateway/` | Split-brain risk; **prerequisite for T1** |
| **MEDIUM** | No Model Shadow Mode - Unsafe hot-swap | `apps/signal_service/` | Bad model goes live immediately |
| **MEDIUM** | No Liquidity-Aware Slicing - TWAP ignores order book | `apps/execution_gateway/order_slicer.py` | Slippage in thin markets |
| **LOW** | Redis Fallback Missing - No local buffer for quotes | `apps/market_data_service/` | Potential blindness |
| **LOW** | Fat-Finger Size Warnings Missing - No size-based UI limits | `apps/web_console/app.py` | Manual order errors |

### Not Valid / Already Addressed

| Issue | Status | Reason |
|-------|--------|--------|
| Feature Parity / Training-Serving Skew | **Not a bug** | `strategies/alpha_baseline/features.py` shared; verified by `test_feature_parity.py` |
| Microservice Latency | **Design tradeoff** | Documented in ADRs; HTTP chosen for simplicity; would require major refactoring |

---

## Detailed Analysis

### 1. HIGH: Zombie Slices (TWAP Slices Lost on Restart)

**Location:** `apps/execution_gateway/slice_scheduler.py:148`

**Current State:**
- APScheduler uses default in-memory job store: `self.scheduler = BackgroundScheduler(timezone="UTC")`
- ADR-0015 explicitly acknowledges: *"No Persistence: Scheduled jobs lost on service restart (mitigated by boot-time rescheduling in future iteration)"*
- The "future iteration" was never implemented

**Impact:**
- A 10-slice TWAP order could execute 3 slices, then if the service restarts, the remaining 7 slices are lost
- User has unintended partial position with no automatic recovery
- Database shows slices as `pending_new` but they'll never execute

**⚠️ CRITICAL RISK (Gemini Review):**
If service crashes AFTER submitting to Alpaca but BEFORE updating DB status to `submitted`, the slice remains `pending_new` in DB. Naive recovery would re-submit → **double fill**.

**Fix Required:**
1. **PREREQUISITE:** Run Reconciliation (T3) BEFORE Zombie Slice Recovery
   - Query Alpaca for all open orders (`status="open"`) and reconcile by `client_order_id`
   - Update DB status for any orders already submitted (avoid double-submit)
   - This prevents re-submitting orders that Alpaca already received
2. On startup (AFTER reconciliation), query database for orders where:
   - `parent_order_id IS NOT NULL` (child slices)
   - `status = 'pending_new'`
   - (Include ALL pending slices - both past-due and future - grace period logic handles timing)
3. **Parent Order & Safety Checks (Codex Review v2):**
   - Verify parent order status is in allowed set (`accepted`, `submitted`, `submitted_unconfirmed`)
   - If parent is `canceled`/`expired`/`failed`: Cancel pending slices, don't reschedule
   - Check circuit breaker state before rescheduling
   - If breaker is tripped: Mark slices as `blocked_circuit_breaker`, don't reschedule
4. Re-schedule eligible slices with APScheduler, **preserving original `client_order_id`**
   - The existing `client_order_id` is deterministic (hashed from inputs)
   - Alpaca's idempotency protects against duplicates if ID matches
5. **Grace Period Logic (Codex Review v1):**
   - If `scheduled_time < now()` but within `MISFIRE_GRACE_SECONDS` (default: 60):
     - If market is open AND parent valid AND breaker OK: Execute immediately (catch-up)
     - If market is closed: Reschedule to next open
   - If beyond grace period AND market closed: Fail the slice
   - If beyond grace period AND market open: Execute immediately with warning log

---

### 2. HIGH: Cold Start - Signal Service Startup Hydration

**Location:** `apps/signal_service/main.py:197-346`

**Current State:**
- Service loads model at startup (lines 237-269)
- Initializes Redis client and feature cache (lines 302-338)
- Does NOT precompute or hydrate feature data
- First `/signals` request computes features on-demand

**Impact:**
- Models using rolling windows (e.g., 20-day SMA) may produce invalid signals on first request
- Features requiring historical context start "cold"
- The `/api/v1/features/precompute` endpoint exists but is never called at startup

**Fix Required (Updated per Codex Review):**
1. After initializing SignalGenerator (line 346), start hydration in **background task**
   - Don't block service startup on hydration completion
   - Set `hydration_complete` flag when done
2. Add `FEATURE_HYDRATION_ENABLED` env var (default: true in production)
3. Add `FEATURE_HYDRATION_TIMEOUT_SECONDS` (default: 300)
4. **Conditional Skip:** If `feature_cache is None` (Redis disabled) or model not loaded (testing mode), skip hydration
5. **Readiness Gating:** Health check reports "degraded" until hydration completes
   - **NOTE:** Extend `HealthResponse.status` to include "degraded" (currently only "healthy"/"unhealthy")
   - **HTTP Semantics (Codex v13):** "degraded" returns HTTP 200 OK (service is running, just warming up)
     - Kubernetes liveness: Pass (200) - don't restart
     - Kubernetes readiness: Fail (503) - don't route traffic until "healthy"
     - Add separate `/ready` endpoint that returns 503 when degraded
   - **Test Updates (Codex v12):** Update tests that assert only "healthy"/"unhealthy" to handle "degraded"
6. Log hydration progress: "Hydrating feature cache with N days of history for M symbols..."
7. Handle per-symbol failures gracefully (don't crash service if one symbol fails to hydrate)

---

### 3. HIGH: Missing Alpaca Reconciliation Loop (Promoted per Review)

**Location:** `apps/execution_gateway/` (missing implementation)

**Current State:**
- The `apps/reconciler/` directory does NOT exist (was planned but never implemented)
- Execution gateway has position reservation sync and health check recovery
- NO periodic polling of Alpaca to detect state drift
- `submitted_unconfirmed` status exists but nothing actively reconciles these

**Impact:**
- If Alpaca fills/rejects an order but webhook fails to deliver, database remains stale
- Position tracker can diverge from actual broker positions
- No automatic detection of "split-brain" scenarios
- **CRITICAL:** Required as prerequisite for safe Zombie Slice recovery (T1)

**Fix Required (Updated per Gemini + Codex Reviews v2):**
1. Add background task to execution gateway (or new reconciler service)
2. **Startup Gating (Codex Review v2 + v3 + v13):**
   - Set `startup_reconciliation_complete = False` flag at startup
   - Block NEW order submissions until flag is True (return 503 Service Unavailable)
   - **Gated Endpoints (Codex v13):** ALL order-creating endpoints must check gating flag:
     - `POST /api/v1/orders` - Single orders
     - `POST /api/v1/orders/slice` - TWAP submissions
     - Scheduler recovery (internal) - Must wait for reconciliation before rescheduling
   - **Risk-Reducing Orders Only Mode (Codex v4 + v5 + v12):** Allow these operations during gating:
     - `DELETE /api/v1/orders/{parent_id}/slices` - Cancel pending TWAP slices (existing endpoint)
     - `POST /api/v1/orders/{client_order_id}/cancel` - Cancel individual order (NEW endpoint to add)
     - `POST /api/v1/orders` (Codex v14 - correct path) with **server-enforced reduce-only logic**:
       - **CRITICAL (Codex v12 + v14):** During gating, compute `effective_position` from LIVE Alpaca data:
         - Call `TradingClient.get_open_position(symbol)` for current position
           - **Note:** Alpaca returns 404 when flat; treat as `current_position = 0`
         - Call `TradingClient.get_orders(status="open")` and filter by symbol for open orders
         - If broker API unavailable: REJECT all orders (fail closed)
         - Do NOT trust DB state during reconciliation - it may be stale
       - **Available-to-Close Formula (Codex v6 + v8):**
         ```
         # Standard case: current_position != 0
         If current_position > 0 (long):
           open_reduce_qty = SUM(qty) of open SELL orders
           open_increase_qty = SUM(qty) of open BUY orders
         Else if current_position < 0 (short):
           open_reduce_qty = SUM(qty) of open BUY orders
           open_increase_qty = SUM(qty) of open SELL orders

         effective_position = current_position + open_increase_qty - open_reduce_qty
         available_to_close = abs(effective_position)

         # Flat case (Codex v8): current_position == 0 with open orders
         If current_position == 0:
           net_open = SUM(buy_qty) - SUM(sell_qty)  # Signed net of open orders
           effective_position = net_open
           If net_open > 0: reducing side = SELL
           Else if net_open < 0: reducing side = BUY
           Else (net_open == 0): Reject all orders (truly flat)
           available_to_close = abs(net_open)

         projected_position_after_all = effective_position + (new_qty * side_multiplier)
         ```
       - **Sign Consistency Check (Codex v5 + v6 + v7 + v8):** Prevent position reversal:
         - Base all checks on `effective_position` (not `current_position`)
         - If `effective_position > 0` (net long): Only allow `side=sell` with `qty <= available_to_close`
         - If `effective_position < 0` (net short): Only allow `side=buy` with `qty <= available_to_close`
         - If `effective_position == 0` (truly flat): Reject all orders (nothing to reduce)
       - **Projected Position Check:** `sign(projected_position_after_all) == sign(effective_position) OR projected_position_after_all == 0`
       - Permit any order that decreases `abs(effective_position)` without flipping sign
       - **Do NOT trust client-supplied `position_closing` flag** - verify server-side
   - **Timeout & Override (Codex v3 + v12 + v13 + v14):**
     - `RECONCILIATION_TIMEOUT_SECONDS` (default: 300 = 5 minutes)
     - If timeout exceeded: Log critical alert, remain in gated mode
     - Manual override: `POST /api/v1/reconciliation/force-complete`
       - **Permission (Codex v14):** Use `@require_permission(Permission.MANAGE_RECONCILIATION)` from `libs/web_console_auth`
         - Add `MANAGE_RECONCILIATION` to `Permission` enum (ADMIN already includes all)
       - Logs operator name from `request.state.user["user_id"]` (X-User-Id header), reason (from request body), timestamp
       - Flips readiness but logs warning for every subsequent order
   - Only flip readiness after reconciliation + recovery complete (or manual override)
3. **Startup Reconciliation** (runs BEFORE Zombie Slice recovery):
   - Query Alpaca for `status="open"` orders and reconcile by `client_order_id`
   - For DB orders still `pending_new` or `submitted_unconfirmed`, call
     `get_order_by_client_id()` individually to confirm broker state
   - Update DB status for any that were submitted but DB shows `pending_new`
4. **Periodic Reconciliation** (every 5 minutes):
   - Poll Alpaca for:
     - **All orders** via alpaca-py `TradingClient.get_orders()` (Codex v14):
       - **API Params:** `status="all"`, `limit=500`, `after=<last_check_time>` (ISO datetime)
       - **Pagination:** Alpaca uses cursor-based; if response count == limit, fetch next page
       - **Time Filter:** `after` param filters by `created_at`, NOT `updated_at`
       - **Implementation Note:** Alpaca does NOT support `client_order_id` pattern queries.
         Use a feasible fallback:
         - Always pull `status="open"` orders each run and reconcile with DB open/non-terminal rows.
         - For DB orders still non-terminal but older than the `after` window, call
           `get_order_by_client_id()` individually (bounded list).
       - **Overlap Buffer:** Query `after = last_check_time - 60s` to catch edge cases
       - **High-Water Mark:** Persist `last_check_time` to DB after successful reconciliation
     - All positions via `TradingClient.get_all_positions()`
   - Compare with database state:
     - Orders in DB but not in Alpaca: Query order by `client_order_id` to get final status
     - **Partial fills (Gemini):** If DB thinks 0 filled but Broker says 50, update DB
     - **Orphan Orders (Codex v2 + v3 + v4 + v5):** Orders in Alpaca but not in DB:
      - Create record in `orphan_orders` table (do NOT insert into `orders` unless operator adopts and maps a strategy)
      - **Scoped Quarantine with Fallback (Codex v4 + v5 + v12):** Block new orders with precise scoping:
        - **Single-Account Assumption (Codex v12):** Repo currently has no multi-account support
        - Quarantine key: `quarantine:{strategy_id}:{symbol}` (use `*` when strategy unknown; account_id omitted - single account)
        - **Strategy ID Resolution (CORRECTION):**
          - `client_order_id` is a **24-char SHA256 hash** (see `order_id_generator.py`) and **does NOT encode strategy_id**.
          - For true orphans (Alpaca order not in DB), **strategy is unknown** → set `strategy_id = "external"` sentinel.
          - **Quarantine must fail-closed when strategy is unknown** (see below).
          - Optional: In single-strategy deployments, allow operator override to map unknown → `STRATEGY_ID` after verification.
          - **Reserved Strategy ID (Optional):** "external" is a reserved name
            - **Note:** Orphans live in `orphan_orders` (no FK to `strategies`), so DB-level reservation is optional.
            - If you want DB enforcement, verify no existing `external` strategy first, then apply:
            - **DB-Level Constraint (Codex v7 + v8 + v9 + v10):**
              ```sql
              -- Step 1: Alter FK to ON UPDATE CASCADE (required before renaming parent)
              -- Constraint name is 'fk_strategy' per db/migrations/0006_create_rbac_tables.sql:32
              ALTER TABLE user_strategy_access DROP CONSTRAINT fk_strategy;
              ALTER TABLE user_strategy_access ADD CONSTRAINT fk_strategy
                FOREIGN KEY (strategy_id) REFERENCES strategies(strategy_id) ON UPDATE CASCADE ON DELETE CASCADE;

              -- Step 2: Rename any existing 'external' rows (CASCADE auto-updates FK rows)
              -- Use clock_timestamp() for per-row uniqueness (NOW() is stable per-statement)
              UPDATE strategies SET strategy_id = 'legacy_ext_' || LEFT(MD5(clock_timestamp()::text), 8)
                WHERE strategy_id = 'external';

              -- Step 3: Add CHECK constraint AFTER rename completes
              ALTER TABLE strategies ADD CONSTRAINT chk_strategy_id_not_reserved
                CHECK (strategy_id != 'external');
              ```
            - App-level validation: Reject `strategy_id = "external"` with error message
            - Startup check: Alert if reserved value found (defense in depth)
            - If somehow encountered in DB, log critical alert
          - **Quarantine Scope Policy for "external":**
            - **Default (fail-closed):** Quarantine `*:{symbol}` (blocks all strategies on that symbol).
            - Operator can **narrow** to `external:{symbol}` after manual verification.
          - Store resolved `strategy_id` with orphan order record for consistency
        - Order submission checks quarantine for matching strategy+symbol (or `*` scope)
        - Different strategies on same symbol remain unaffected unless `*` scope is set
      - **Explicit Exposure Accounting (Codex v4 + v13 + v14):**
         - Track orphan as "open_order_reserved_notional" (not position qty)
         - Do NOT add to position qty (avoids double-counting with actual fills)
         - **Storage (Codex v14):** Create new `orphan_orders` table (no existing position_reservations table):
           ```sql
           CREATE TABLE orphan_orders (
             id SERIAL PRIMARY KEY,
             broker_order_id TEXT NOT NULL UNIQUE,
             client_order_id TEXT,
             symbol TEXT NOT NULL,
             strategy_id TEXT NOT NULL DEFAULT 'external',
             side TEXT NOT NULL,
             qty INTEGER NOT NULL,
             estimated_notional DECIMAL(18,2) NOT NULL,
             status TEXT NOT NULL DEFAULT 'untracked',
             detected_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
             resolved_at TIMESTAMP WITH TIME ZONE
           );
           ```
        - **Risk Integration:** Add `get_orphan_exposure(symbol, strategy_id)` to `DatabaseClient`
          - Include both the requested strategy and `external` sentinel exposure
          - Called by risk check before order submission: `current_exposure + open_orders + orphan_exposure <= limit`
        - **Redis Key:** `orphan_exposure:{strategy}:{symbol}` for fast lookups (store `external` for unknown; synced from DB on write)
         - If orphan fills, position updates via normal webhook flow; mark orphan as `resolved`
       - **Resolution Workflow:** Define clear operator actions:
         - Auto-cancel policy option: Cancel orphan if `age > 1 hour` and not filled
         - Manual review: Operator can "adopt" order into system or cancel at broker
         - Clear quarantine only after orphan resolved
       - Log warning + persistent alert until cleared
     - Position qty mismatch: Log alert, update DB
5. **Conflict Resolution (Codex Review v2 + v3 + v4 + v5 + v6 + v7):**
   - Add columns: `last_updated_at`, `status_rank` (computed), `broker_event_id` (nullable), `is_terminal` (boolean NOT NULL DEFAULT FALSE), `source_priority` (integer NOT NULL DEFAULT 2)
   - **Migration Backfill (Codex v7):**
     ```sql
     -- Backfill existing rows based on current status
     UPDATE orders SET is_terminal = TRUE WHERE status IN ('filled', 'canceled', 'expired', 'failed');
     -- Add NOT NULL constraint after backfill
     ALTER TABLE orders ALTER COLUMN is_terminal SET NOT NULL;
     ALTER TABLE orders ALTER COLUMN is_terminal SET DEFAULT FALSE;
     ```
   - **Terminal State Lock (Codex v6):** Once order reaches terminal state, lock it:
     ```sql
     -- Additional WHERE clause for ALL updates:
     AND (is_terminal = FALSE OR (status = 'filled' AND :new_filled_qty > filled_qty))
     ```
     - Terminal states: `filled`, `canceled`, `expired`, `failed`
     - Only exception: `filled` can update `filled_qty`/`avg_price` for partial→full fill
   - **Unified CAS Approach (Codex v6):** Use Option B as primary (verified ordering):
     - **NOTE:** Alpaca's `event_id` is UUID (not integer sequence) - cannot use `<` comparison
     - Use `updated_at` from Alpaca response as timestamp source (ISO 8601, microsecond precision)
     ```sql
     UPDATE orders SET status = :new_status, last_updated_at = :broker_updated_at,
            status_rank = :new_status_rank, source_priority = :source_priority,
            filled_qty = GREATEST(filled_qty, :new_filled_qty),
            filled_avg_price = CASE WHEN :new_filled_qty > filled_qty THEN :new_filled_avg_price ELSE filled_avg_price END,
            is_terminal = :new_status IN ('filled', 'canceled', 'expired', 'failed')
     WHERE client_order_id = :id
       AND (is_terminal = FALSE OR (status = 'filled' AND :new_filled_qty > filled_qty))
       AND (last_updated_at IS NULL
            OR last_updated_at < :broker_updated_at
            OR (last_updated_at = :broker_updated_at AND status_rank < :new_status_rank)
            OR (last_updated_at = :broker_updated_at AND status_rank = :new_status_rank AND filled_qty < :new_filled_qty)
            OR (last_updated_at = :broker_updated_at AND status_rank = :new_status_rank AND filled_qty = :new_filled_qty AND source_priority < :source_priority))
     ```
   - **Status Rank Values (Codex v12 + v13 - complete OrderStatus coverage):**
     - Rank 1 (initial): `pending_new`, `dry_run`
     - Rank 2 (submitted): `submitted`, `submitted_unconfirmed`, `accepted`
     - Rank 3 (active): `pending_cancel`, `pending_replace`, `calculated`, `stopped`, `suspended`, `partially_filled`
   - Rank 4 (terminal-non-fill): `canceled`, `expired`, `failed`, `rejected`, `replaced`, `done_for_day`, `blocked_kill_switch`, `blocked_circuit_breaker`
     - Rank 5 (terminal-fill): `filled`
     - **NOTE (Codex v13):** Add `partially_filled` to OrderStatus TypeAlias if not present; map Alpaca `partial_fill` event to this status
     - **Fallback:** Unknown statuses default to rank 0 (lowest priority) with warning log
   - **Source Priority:** `manual(1), reconciliation(2), webhook(3)`
   - **Fill Protection:** Use `GREATEST(filled_qty, :new_filled_qty)` to never lose fill data
   - **ALL Update Paths:** Ensure webhook handler, reconciliation, and manual updates use same `update_order_status()` function
   - Log skipped updates for audit trail with reason (terminal locked, timestamp, status rank, or fill qty)
6. Handle `submitted_unconfirmed` orders specifically:
   - Look up by `client_order_id` in Alpaca
   - If found: Update to actual status (filled/cancelled/etc)
   - If not found: Mark as `failed` (submission never reached broker)
7. Add Prometheus metrics: `reconciliation_mismatches_total`, `reconciliation_last_run_timestamp`, `reconciliation_conflicts_skipped_total`

---

### 4. MEDIUM: Model Shadow Mode for Safe Hot-Swap

**Location:** `apps/signal_service/` (Codex: validation belongs here, not libs/models)

**Current State:**
- `reload_if_changed()` validates with test prediction using zeros (line ~410)
- New model goes live immediately upon reload
- No comparison with previous model outputs
- Corrupt model with valid format but bad weights → immediate garbage predictions

**Fix Required (Updated per Codex Review):**
1. Add `ShadowModeValidator` class in **`apps/signal_service/shadow_validator.py`**
   - NOT in `libs/models/` because it needs access to feature data
   - Run both old and new models on N recent feature samples
   - Compare outputs: correlation, range, sign changes
   - If correlation < 0.5 or outputs differ by >50% → reject new model
2. Integration: `ModelRegistry.reload_if_changed()` calls validator via callback
   - Pass validator function from signal_service layer
   - This maintains ModelRegistry's generic nature
3. Add `SHADOW_VALIDATION_ENABLED` config (default: true)
4. Add `SHADOW_SAMPLE_COUNT` config (default: 100)
5. **Async Validation (Gemini):** Ensure running two models doesn't block requests
   - Run validation in background task
   - Keep old model active until validation passes
6. Log shadow mode results to metrics for monitoring
7. Provide operator override: `SKIP_SHADOW_VALIDATION=true` for emergencies

---

### 5. MEDIUM: Liquidity-Aware TWAP Slicing

**Location:** `apps/execution_gateway/order_slicer.py`

**Current State:**
- `TWAPSlicer` divides quantity evenly across time intervals
- No integration with market data service for quote/depth
- Slice sizes are fixed regardless of market liquidity

**Impact:**
- In thin markets, a single slice could consume entire bid/ask depth
- Causes excessive slippage and adverse price impact
- TWAP's purpose (minimize market impact) is defeated

**⚠️ ARCHITECTURAL CONCERN (Gemini + Codex Reviews):**
- `TWAPSlicer` is currently stateless and pure (deterministic). Adding I/O makes it harder to test.
- Changing slice qty at execution time would break `client_order_id` determinism → duplicate orders

**Fix Required (Revised Architecture):**
1. **Keep TWAPSlicer Pure** - Do NOT add I/O inside the slicer
2. Fetch ADV/liquidity data in the **service layer** (`main.py` or new `LiquidityService`)
3. **ADV Data Source (Codex v13):**
   - Primary: Alpaca Market Data API `GET /v1beta1/stocks/{symbol}/bars?timeframe=1Day&limit=20`
   - Calculate ADV as 20-day average volume from bars
   - Cache TTL: 24 hours (refresh daily at market open)
   - **Failure Fallback:** If API unavailable, disable liquidity check for that symbol with WARNING log (don't block order)
4. Calculate `max_slice_qty` based on ADV before calling slicer:
   ```python
   adv = liquidity_service.get_adv(symbol)
   max_slice_qty = int(adv * MAX_SLICE_PCT_OF_ADV)  # e.g., 1% of ADV
   ```
5. Pass `max_slice_qty` as argument to `TWAPSlicer.plan()`:
   - If individual slice would exceed max, slicer creates more, smaller slices
   - This happens at PLAN time, not execution time
6. **Persist Liquidity Constraints** with the slicing plan in DB
   - When recovering orphaned slices, use persisted constraints (not fresh ADV)
   - This preserves `client_order_id` determinism
7. Add config: `MAX_SLICE_PCT_OF_ADV` (default: 0.01 = 1%)
8. Add config: `LIQUIDITY_CHECK_ENABLED` (default: true)
9. **Do NOT** adjust slice qty at execution time (breaks idempotency)

---

### 6. LOW: Redis Fallback Buffer for Market Data

**Location:** `apps/market_data_service/main.py`

**Current State:**
- Market data service streams quotes to Redis
- If Redis unavailable, quotes are simply lost
- Signal service gracefully degrades but goes "blind"

**Fix Required (Updated per Codex Review):**
1. Add in-memory ring buffer (last 100 quotes per symbol) in market data service
2. If Redis publish fails, store in buffer
3. Provide fallback endpoint: `GET /api/v1/quotes/{symbol}/latest` returning buffer data
4. **Wire Consumers (Codex):** Signal service must be updated to use fallback:
   - Try Redis first
   - If Redis fails, call market data service fallback endpoint
   - Add TTL/staleness policy (reject quotes older than 60 seconds)
5. **Multi-Instance Note:** Buffer is per-instance; document this limitation
6. Add health check: "degraded" when operating from buffer

---

### 7. LOW: Fat-Finger Size Warnings for Web Console

**Location:** `apps/web_console/app.py` (Codex: not `components/order_form.py`)

**Current State:**
- Backend has position limits and risk checks
- UI has existing two-step confirmation in `render_manual_order_entry` (Codex finding)
- **Missing:** Size-based warnings for unusually large orders
- Backend limits are last line of defense, not user-friendly

**Fix Required (Scope Clarified per Codex Review):**
1. Add **size-based warnings** to existing confirmation flow:
   - Max order value warning threshold: $5,000 (configurable)
   - Max order qty warning threshold: 1000 shares
2. If exceeded, enhance existing confirmation with warning text:
   - "⚠️ This order ($X / Y shares) exceeds normal limits. Are you sure?"
3. For orders >$10,000, require MFA step-up (already have infra for this)
4. Log all "large order" confirmations to audit log with order details

---

## Implementation Plan

### Prerequisites (Codex v12)

**ADRs Required:** Per repo policy, all architectural changes require ADRs before implementation.
ADRs written and available (0021-0025 were already taken):
- **ADR-0020: Reconciliation Service Architecture** - Startup gating, reduce-only mode, orphan handling, quarantine
- **ADR-0026: Shadow Mode Model Validation** - Validation flow in `signal_service`
- **ADR-0027: Liquidity-Aware Slicing** - ADV dependency + plan-time constraints
- **ADR-0028: Market Data Fallback Buffer** - Quote buffer + fallback client routing

These ADRs must be written and reviewed BEFORE implementing Phase 1 and Phase 2.

### Phase 1: Critical Fixes (HIGH Priority)

**⚠️ SEQUENCING (per Gemini Review):** T3 (Reconciliation) MUST run before T1 (Zombie Recovery)

**T3: Alpaca Reconciliation Loop** (10-12 hours) - **MUST BE FIRST**
1. Add `ReconciliationService` class to execution gateway
2. **Startup Gating with Cancel-Only Mode:**
   - Add `startup_reconciliation_complete` flag
   - Block new orders (return 503) but allow cancel/close-only operations
   - Add timeout + manual override endpoint with ADMIN permission
3. Implement **Startup Reconciliation** (syncs DB with Alpaca before Zombie recovery)
4. Implement **Periodic Reconciliation** (every 5 minutes)
5. Add endpoints: `GET /api/v1/reconciliation/status`, `POST /api/v1/reconciliation/run`, `POST /api/v1/reconciliation/force-complete`
6. Query `status=all` with time filter (not just open orders)
7. Handle partial fills, `submitted_unconfirmed` orders
8. **Orphan Order Handling:** Symbol quarantine, exposure sync, resolution workflow
9. **Atomic Conflict Resolution:** DB-level CAS updates, status progression rules, tie-breaker logic
10. Add Prometheus metrics: `reconciliation_mismatches_total`, `reconciliation_last_run_timestamp`, `reconciliation_conflicts_skipped_total`, `symbols_quarantined_total`
11. Add tests with mocked Alpaca responses including race condition scenarios

**T1: TWAP Slice Recovery on Startup** (5-7 hours) - **AFTER T3**
1. Add `recover_orphaned_slices()` to `SliceScheduler`
2. Call during execution gateway lifespan startup, **AFTER reconciliation completes**
3. **Parent Order & Safety Checks:** Verify parent status and circuit breaker before rescheduling
4. Preserve original `client_order_id` for idempotency
5. Implement grace period logic (catch-up vs fail based on market hours + safety state)
6. Add Prometheus counter: `twap_slices_recovered_total`, `twap_slices_skipped_parent_invalid`
7. Add tests for recovery scenarios including parent canceled/breaker tripped cases

**T2: Signal Service Startup Hydration** (3-4 hours)
1. Add `hydrate_feature_cache()` as **background task** (don't block startup)
2. Query last N days of features for configured universe
3. Skip hydration if Redis/feature_cache disabled or model not loaded
4. Add timeout (`FEATURE_HYDRATION_TIMEOUT_SECONDS`)
5. Report "degraded" in health check until hydration completes
6. Handle per-symbol failures gracefully
7. Add startup log messages for visibility

### Phase 2: Safety Improvements (MEDIUM Priority)

**T4: Model Shadow Mode** (4-5 hours)
1. Add `ShadowModeValidator` class to **`apps/signal_service/shadow_validator.py`**
2. Integrate with `ModelRegistry.reload_if_changed()` via callback
3. Run validation in background task (don't block requests)
4. Add config: `SHADOW_VALIDATION_ENABLED`, `SHADOW_SAMPLE_COUNT`
5. Add Prometheus metrics: `model_shadow_validation_passed`, `model_shadow_validation_rejected`

**T5: Liquidity-Aware Slicing** (4-5 hours)
1. Add `LiquidityService` to execution gateway (NOT in TWAPSlicer)
2. Fetch ADV in service layer, calculate `max_slice_qty`
3. Pass `max_slice_qty` to `TWAPSlicer.plan()` as argument
4. Persist liquidity constraints with slicing plan in DB
5. Add config: `MAX_SLICE_PCT_OF_ADV`, `LIQUIDITY_CHECK_ENABLED`
6. Add tests with mocked market data

### Phase 3: Minor Improvements (LOW Priority)

**T6: Redis Fallback Buffer** (3-4 hours)
1. Add in-memory ring buffer to market data service
2. Add fallback endpoint
3. Wire signal service to use fallback with staleness policy

**T7: Fat-Finger Size Warnings** (2-3 hours)
1. Add size-based warnings to existing confirmation in `apps/web_console/app.py`
2. MFA step-up for large orders

---

## Files to Modify

### Phase 1 (Corrected Sequencing)
- `apps/execution_gateway/reconciliation.py` (NEW) - Reconciliation service with startup gating
- `apps/execution_gateway/main.py` - Add reconciliation + recovery on startup, startup gating flag, new cancel endpoint
- `apps/execution_gateway/slice_scheduler.py` - Add recovery logic with parent/breaker checks
- `apps/execution_gateway/database.py` - Add `last_updated_at` column support, `get_orphan_exposure()` method
- `apps/execution_gateway/alpaca_client.py` (Codex v14) - Add `get_orders()`, `get_all_positions()`, `get_open_position()` methods
- `apps/execution_gateway/schemas.py` - Add `partially_filled` to OrderStatus TypeAlias
- `libs/web_console_auth/permissions.py` - Add `manage_reconciliation` permission
- `db/migrations/0009_add_conflict_resolution_columns.sql` (NEW) - Add columns:
  - `last_updated_at` (timestamp with time zone)
  - `is_terminal` (boolean NOT NULL DEFAULT FALSE)
  - `source_priority` (integer NOT NULL DEFAULT 2)
  - `status_rank` (integer, computed from status or stored)
  - `broker_event_id` (text, nullable)
  - `reconciliation_high_water_mark` table for pagination state
  - `orphan_orders` table (Codex v14) for orphan exposure tracking
- `apps/signal_service/main.py` - Add hydration background task
- `docs/ADRs/0020-reconciliation-service-architecture.md` (CREATED)
- `docs/ADRs/0026-shadow-mode-model-validation.md` (CREATED)
- `docs/ADRs/0027-liquidity-aware-slicing.md` (CREATED)
- `docs/ADRs/0028-market-data-fallback-buffer.md` (CREATED)

### Phase 2
- `apps/signal_service/shadow_validator.py` (NEW) - Shadow mode validation
- `apps/signal_service/model_registry.py` - Integrate shadow validation via callback
- `apps/execution_gateway/liquidity_service.py` (NEW) - ADV lookup service
- `apps/execution_gateway/order_slicer.py` - Accept max_slice_qty parameter
- `apps/execution_gateway/schemas.py` - Add `max_slice_qty`/`liquidity_constraints` to SlicingPlan (Gemini)
- `db/migrations/0010_add_liquidity_constraints_to_slicing_plans.sql` (NEW) - DB migration (Gemini)

### Phase 3
- `apps/market_data_service/quote_buffer.py` (NEW) - In-memory ring buffer for quotes
- `apps/market_data_service/main.py` - Add fallback endpoint `GET /api/v1/quotes/{symbol}/latest`
- `libs/redis_client/quote_client.py` (NEW, Codex v13) - Dedicated quote client with fallback logic:
  - Try Redis pub/sub first
  - If Redis unavailable, call market_data_service fallback endpoint
  - Add staleness policy (reject quotes > 60s old)
- `apps/signal_service/signal_generator.py` - Wire quote_client for price data if needed
- `apps/web_console/app.py` - Add size-based warnings

---

## Testing Strategy

1. **Unit Tests:** Each new class/method fully tested
2. **Integration Tests:** Recovery scenarios, reconciliation with mocked Alpaca
3. **E2E Tests:** Full TWAP order with restart, verify recovery
4. **Stress Tests:** Reconciliation under load

---

## Success Criteria

- [ ] TWAP slices survive service restarts (verified with test)
- [ ] Signal service produces valid signals immediately after cold start
- [ ] Reconciliation detects simulated broker/DB drift
- [ ] Model hot-swap rejects intentionally bad model (shadow mode)
- [ ] Large TWAP orders are subdivided in thin markets
- [ ] All Prometheus metrics visible in Grafana

---

## Related Documents

- [ADR-0015: TWAP Order Slicer](../ADRs/0015-twap-order-slicer.md) - Acknowledges "boot-time rescheduling" as future work
- [ADR-0014: Execution Gateway Architecture](../ADRs/0014-execution-gateway-architecture.md) - Mentions reconciliation as future enhancement
- [AI_GUIDE.md](../AI/AI_GUIDE.md) - Defines reconciliation requirements

---

**Last Updated:** 2025-12-17 (Revision 15)
**Author:** AI Assistant (Claude Code)
**Reviewers:** Gemini (APPROVED v1-v5), Codex (APPROVED v15)
