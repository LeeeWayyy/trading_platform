---
id: P5T5
title: "NiceGUI Migration - Manual Trading Controls"
phase: P5
task: T5
priority: P0
owner: "@development-team"
state: DONE
created: 2025-12-31
dependencies: [P5T1, P5T2, P5T3, P5T4]
estimated_effort: "5-7 days"
related_adrs: [ADR-0031-nicegui-migration]
related_docs: [P5_PLANNING.md, P5T1_DONE.md, P5T2_DONE.md, P5T3_DONE.md, P5T4_DONE.md]
features: [T5.1, T5.2, T5.3]
---

# P5T5: NiceGUI Migration - Manual Trading Controls

**Phase:** P5 (Web Console Modernization)
**Status:** DONE
**Priority:** P0 (Critical Safety)
**Owner:** @development-team
**Created:** 2025-12-31
**Estimated Effort:** 5-7 days
**Track:** Phase 4 from P5_PLANNING.md
**Dependency:** P5T4 (Real-Time Dashboard) must be complete with Phase 3.5 security gate passed

---

## Objective

Port manual order entry, kill switch management, and position management controls to NiceGUI with safety confirmations. These are the most critical trading actions in the system.

**Success looks like:**
- Manual orders submitted with idempotent client_order_id
- Kill switch engage/disengage with two-factor confirmation
- Position close and flatten operations with safety dialogs
- All destructive actions require confirmation
- Real-time kill switch status check before any order submission
- Full audit logging for all trading actions
- Permission-based UI controls (viewer vs trader vs admin)

**Critical Safety Requirements:**
- NEVER submit orders without kill switch check
- Idempotent client_order_id prevents duplicate orders
- Two-factor confirmation for destructive actions
- Rate limiting on repeated actions
- Audit trail for all trading operations

---

## Acceptance Criteria

> **⚠️ IMPORTANT: Code Snippets Are Illustrative Only**
>
> The code snippets in this document are **conceptual illustrations** showing the UI flow and safety patterns.
> Actual implementation MUST follow the **Backend API Contracts** specified in the Implementation Notes section:
>
> - Use manual controls endpoints (`/positions/{symbol}/close`, `/positions/flatten-all`, `/orders/cancel-all`)
> - Backend generates `client_order_id` - do NOT generate client-side
> - Cancel-all requires `symbol` parameter (per-symbol, not global)
> - Flatten-all requires `id_token` (MFA) from auth session
> - Use specific exception types (`httpx.HTTPStatusError`, `httpx.RequestError`), NOT general `except Exception`
> - All requests require: `reason`, `requested_by`, `requested_at`
>
> See **Implementation Notes** at the end of this document for complete API specifications.

### T5.1 Manual Order Entry

**Deliverables:**
- [ ] Order form with symbol, qty, side, order type inputs
- [ ] Limit price input (visible only for limit orders)
- [ ] Reason field (required, min 10 characters)
- [ ] Preview dialog with order summary
- [ ] Kill switch check BEFORE showing preview dialog
- [ ] FRESH kill switch check at confirmation time
- [ ] Backend generates idempotent client_order_id (frontend receives in response)
- [ ] Form validation with error messages
- [ ] Submit button disabled during API call
- [ ] Success/error notification
- [ ] Form reset after successful submission
- [ ] Backend handles audit logging
- [ ] Backend enforces rate limiting (10 orders per minute per user)

**NOTE:** Backend generates `client_order_id` deterministically. Frontend does NOT generate order IDs.

**Implementation Flow (pseudo-code):**
```python
# apps/web_console_ng/pages/manual_order.py
# NOTE: This is conceptual pseudo-code. See Implementation Notes for API contracts.

async def preview_order():
    # 1. Validate form fields
    # 2. Check kill switch BEFORE showing preview
    try:
        ks_status = await trading_client.fetch_kill_switch_status()
        if ks_status.get("state") == "ENGAGED":
            ui.notify("Cannot submit: Kill Switch is ENGAGED")
            return
    except httpx.HTTPStatusError as e:
        ui.notify(f"Cannot verify kill switch: {e}")
        return
    # 3. Show preview dialog

async def confirm_order():
    # 1. FRESH kill switch check at confirmation time
    # 2. Submit order - backend generates client_order_id
    try:
        result = await trading_client.submit_order(
            symbol=symbol,
            side=side,
            qty=qty,
            order_type=order_type,
            limit_price=limit_price,  # if limit order
            time_in_force=time_in_force,
        )
        # Backend returns client_order_id in response
        order_id = result.get("client_order_id")
        ui.notify(f"Order submitted: {order_id[:12]}...")
    except httpx.HTTPStatusError as e:
        ui.notify(f"Order failed: {e}")
```

**Testing:**
- [ ] Order form validation (symbol, qty, reason length)
- [ ] Limit price visibility toggle
- [ ] Kill switch blocks order (engaged state)
- [ ] Preview dialog shows correct values
- [ ] Confirm submits order to API
- [ ] Form resets after success
- [ ] Error notification on failure
- [ ] Audit log recorded
- [ ] Rate limiting blocks rapid submissions (10/min)

---

### T5.2 Kill Switch Management

**Deliverables:**
- [ ] Kill switch status display (large, prominent)
- [ ] Status indicator with color (ENGAGED=red, DISENGAGED=green)
- [ ] Engage button with single confirmation
- [ ] Disengage button with two-factor confirmation
- [ ] Reason input required for both actions
- [ ] Backend enforces rate limiting (1 action per minute per user)
- [ ] Real-time status updates via Redis Pub/Sub
- [ ] Backend handles audit logging
- [ ] Permission check (admin only for disengage)

**Two-Factor Confirmation Pattern:**
```
# For high-risk actions (kill switch disengage, flatten all):
# 1. First dialog: "Are you sure?" + reason input
# 2. Second dialog: "Type CONFIRM to proceed"
# Both must pass before action executes.
```

**Implementation Flow (pseudo-code):**
```python
# apps/web_console_ng/pages/kill_switch.py
# NOTE: This is conceptual pseudo-code. See Implementation Notes for API contracts.

async def engage_kill_switch(reason: str):
    # Single confirmation dialog with reason
    try:
        await trading_client.engage_kill_switch(
            operator=user_id,
            reason=reason,
            details={},
        )
        ui.notify("Kill Switch ENGAGED")
    except httpx.HTTPStatusError as e:
        ui.notify(f"Failed: {e}")

async def disengage_kill_switch(reason: str):
    # Two-factor: first dialog + second "type CONFIRM"
    # Admin permission required
    try:
        await trading_client.disengage_kill_switch(
            operator=user_id,
            notes=reason,
        )
        ui.notify("Kill Switch DISENGAGED")
    except httpx.HTTPStatusError as e:
        ui.notify(f"Failed: {e}")
```

**Testing:**
- [ ] Status display shows correct state and color
- [ ] Engage button shows single confirmation
- [ ] Engage requires reason
- [ ] Disengage requires admin permission
- [ ] Disengage shows two-factor confirmation
- [ ] Real-time updates reflect changes

---

### T5.3 Position Management

**Deliverables:**
- [ ] Close single position button (from dashboard grid)
- [ ] Flatten all positions button with two-factor confirmation + MFA
- [ ] Cancel all open orders button (per-symbol)
- [ ] Kill switch check before position close/flatten (order submissions)
- [ ] Cancel-all orders BYPASSES kill switch (risk-reducing action)
- [ ] Backend enforces rate limiting (close: 10/min, flatten-all: 1 per 5 minutes)
- [ ] Backend handles audit logging

**CRITICAL API Notes:**
- **Close Position**: Use `POST /api/v1/positions/{symbol}/close` (NOT generic submit_order)
- **Flatten All**: Use `POST /api/v1/positions/flatten-all` with `id_token` (MFA required)
- **Cancel All**: Per-symbol - `POST /api/v1/orders/cancel-all` requires `symbol` parameter
- Backend generates order IDs - frontend does not

**Implementation Flow (pseudo-code):**
```python
# apps/web_console_ng/pages/position_management.py
# NOTE: This is conceptual pseudo-code. See Implementation Notes for API contracts.

async def close_position(symbol: str, reason: str):
    # 1. Check kill switch
    # 2. Use dedicated close endpoint - backend handles order ID generation
    try:
        result = await trading_client.close_position(
            symbol=symbol,
            reason=reason,
            requested_by=user_id,
            requested_at=datetime.now(UTC),
        )
        ui.notify(f"Closing {symbol}: order {result['order_id'][:12]}...")
    except httpx.HTTPStatusError as e:
        ui.notify(f"Failed: {e}")

async def flatten_all_positions(reason: str):
    # Two-factor: first dialog + second "type FLATTEN"
    # CRITICAL: Requires MFA id_token from session
    # Uses BULK endpoint - NOT client-side loop
    id_token = app.storage.user.get("id_token")
    if not id_token:
        ui.notify("MFA required - please re-authenticate")
        return
    try:
        result = await trading_client.flatten_all_positions(
            reason=reason,
            requested_by=user_id,
            requested_at=datetime.now(UTC),
            id_token=id_token,  # MFA proof
        )
        ui.notify(f"Flattened {result['positions_closed']} positions")
    except httpx.HTTPStatusError as e:
        ui.notify(f"Failed: {e}")

async def cancel_all_orders_for_symbol(symbol: str, reason: str):
    # Cancel-all is PER-SYMBOL (not global)
    # Bypasses kill switch (risk-reducing)
    try:
        result = await trading_client.cancel_all_orders(
            symbol=symbol,
            reason=reason,
            requested_by=user_id,
            requested_at=datetime.now(UTC),
        )
        ui.notify(f"Cancelled {result['cancelled_count']} orders for {symbol}")
    except httpx.HTTPStatusError as e:
        ui.notify(f"Failed: {e}")
```

**Testing:**
- [ ] Close position shows confirmation dialog
- [ ] Close position submits correct order (buy to close short, sell to close long)
- [ ] Flatten all requires two-factor confirmation
- [ ] Flatten all processes all positions
- [ ] Flatten all shows progress
- [ ] Cancel all orders shows order count
- [ ] Cancel all orders cancels correctly
- [ ] Kill switch blocks close/flatten when engaged
- [ ] Cancel-all BYPASSES kill switch (allowed even when engaged)
- [ ] Rate limiting blocks rapid actions (close 10/min, cancel-all 5/min, flatten-all 1/5min)
- [ ] Audit logs recorded for all actions

---

## Prerequisites Checklist

**Must verify before starting implementation:**

- [ ] **P5T4 complete:** Real-Time Dashboard with security gate passed
- [ ] **Kill switch API available:**
  - [ ] `GET /api/v1/kill-switch/status` - Current state (NOTE: hyphen, not underscore)
  - [ ] `POST /api/v1/kill-switch/engage` - Engage with reason
  - [ ] `POST /api/v1/kill-switch/disengage` - Disengage with reason
- [ ] **Manual Controls API available:**
  - [ ] `POST /api/v1/orders/{order_id}/cancel` - Cancel single order
  - [ ] `POST /api/v1/orders/cancel-all` - Cancel all for symbol (requires `symbol`)
  - [ ] `POST /api/v1/positions/{symbol}/close` - Close position
  - [ ] `POST /api/v1/positions/flatten-all` - Flatten all (requires `id_token`)
- [ ] **Position API available:**
  - [ ] `GET /api/v1/positions` - Current positions
- [ ] **Audit logging infrastructure ready** (backend already logs via `AuditLogger`)
- [ ] **Rate limiting infrastructure ready** (backend enforces via `RateLimiter`)
- [ ] **MFA token available** in auth session for flatten-all operations

---

## Approach

### High-Level Plan

1. **C0: Manual Order Entry** (3-4 days)
   - Order form with validation
   - Preview dialog
   - Kill switch checks
   - Idempotent submission

2. **C1: Kill Switch Management** (2-3 days)
   - Status display with real-time updates
   - Engage with single confirmation
   - Disengage with two-factor confirmation
   - Audit trail display

3. **C2: Position Management** (2 days)
   - Close single position
   - Flatten all with two-factor
   - Cancel all orders
   - Progress indicators

---

## Component Breakdown

### C0: Manual Order Entry

**Files to Create:**
```
apps/web_console_ng/pages/
├── manual_order.py             # Order entry page
apps/web_console_ng/components/
├── order_form.py               # Reusable order form
├── order_confirmation.py       # Confirmation dialog
apps/web_console_ng/core/
├── audit.py                    # Trading audit logger (wraps backend API)
tests/apps/web_console_ng/
└── test_manual_order.py
```

**NOTE:** Backend generates `client_order_id` - no client-side order_utils.py needed.

---

### C1: Kill Switch Management

**Files to Create:**
```
apps/web_console_ng/pages/
├── kill_switch.py              # Kill switch page
apps/web_console_ng/components/
├── kill_switch_panel.py        # Status panel component
├── two_factor_dialog.py        # Reusable two-factor confirmation
apps/web_console_ng/core/
├── rate_limiter.py             # Rate limiting utility
tests/apps/web_console_ng/
└── test_kill_switch.py
```

---

### C2: Position Management

**Files to Create:**
```
apps/web_console_ng/pages/
├── position_management.py      # Position management page
apps/web_console_ng/components/
├── position_actions.py         # Action buttons component
├── bulk_operation_progress.py  # Progress indicator
tests/apps/web_console_ng/
└── test_position_management.py
```

---

## Testing Strategy

### Unit Tests (CI - Automated)
- `test_manual_order.py`: Form validation, kill switch check, API integration
- `test_kill_switch.py`: Status display, engage/disengage flow, rate limiting
- `test_position_management.py`: Close position, flatten all, cancel all
- `test_rate_limiter.py`: Rate limiting behavior
- NOTE: Order ID generation is tested in backend (`apps/execution_gateway/`) - frontend does not generate IDs

### Integration Tests (CI - Docker)
- `test_order_submission_integration.py`: Full order flow with mocked backend
- `test_kill_switch_integration.py`: Kill switch state changes

### E2E Tests (CI - Playwright)
- `test_manual_order_e2e.py`: Full order entry flow
- `test_kill_switch_e2e.py`: Engage/disengage with confirmations
- `test_flatten_all_e2e.py`: Two-factor flatten confirmation

---

## Dependencies

### External
- `nicegui>=2.0`: UI framework
- `httpx>=0.25`: Async HTTP client

### Internal
- `apps/web_console_ng/core/client.py`: Async trading client (P5T1)
- `apps/web_console_ng/auth/`: Auth middleware (P5T2)
- `apps/web_console_ng/core/realtime.py`: Real-time updates (P5T4)
- `apps/web_console_ng/core/audit.py`: Audit logging

---

## Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Kill switch check race condition | Medium | Critical | Double-check: before preview AND at confirm |
| Duplicate order submission | Low | High | Deterministic client_order_id, same pattern as existing |
| Rate limit bypass | Low | Medium | Server-side rate limiting in addition to client |
| Two-factor dialog bypass | Low | High | Server validates both steps, not just client |
| Bulk operation timeout | Medium | Medium | Progress indicator, async processing |

---

## Implementation Notes

**Address during development:**

1. **Kill Switch Double-Check Pattern:**
   - Check BEFORE showing preview dialog
   - Check AGAIN at confirmation time
   - This prevents TOCTOU (time-of-check-time-of-use) race conditions

2. **Backend API Contract - CRITICAL:** ✅ ADDRESSED IN DOCUMENT (Rev 4, Rev 6)
   - **T5.1 Manual Order Entry**: Use generic `POST /api/v1/orders` for new orders
   - **T5.3 Position Management**: Use manual controls endpoints:
     - Close position: `POST /api/v1/positions/{symbol}/close`
     - Cancel order: `POST /api/v1/orders/{order_id}/cancel`
     - Cancel all orders: `POST /api/v1/orders/cancel-all` (PER-SYMBOL, requires `symbol`)
     - Flatten all: `POST /api/v1/positions/flatten-all`
   - All requests require: `reason`, `requested_by`, `requested_at`
   - Backend generates `client_order_id` deterministically - DO NOT generate client-side
   - Kill switch endpoints: `/api/v1/kill-switch/status` (hyphen, not underscore)

3. **Idempotent Order ID:** ✅ ADDRESSED IN DOCUMENT (Rev 2)
   - Backend generates deterministic IDs via `_generate_manual_order_id()`
   - Format: `hash(action:symbol:side:qty:user_id:date)[:24]`
   - Frontend does NOT need to generate order IDs - backend handles idempotency
   - For position closes: backend uses `strategy_id = "manual_controls_close_position"`

4. **Two-Factor Confirmation + MFA:** ✅ ADDRESSED IN DOCUMENT (Rev 4)
   - Used for: kill switch disengage, flatten all positions
   - First dialog: "Are you sure?" + reason
   - Second dialog: "Type CONFIRM/FLATTEN to proceed"
   - **CRITICAL for Flatten-All**: Backend requires `id_token` (MFA proof)
   - UI two-factor alone is NOT sufficient - must pass MFA token from auth session
   - `FlattenAllRequest(reason, requested_by, requested_at, id_token)`

5. **Rate Limiting:**
   - Kill switch actions: 1 per minute per user
   - Order submissions: 10 per minute per user
   - Position close: 10 per minute per user
   - Cancel-all: 5 per minute per user
   - Flatten-all: 1 per 5 minutes per user
   - Backend enforces server-side rate limits

6. **Cancel-All is PER-SYMBOL:** ✅ ADDRESSED IN DOCUMENT (Rev 4)
   - Backend `CancelAllOrdersRequest` requires `symbol` field
   - To cancel ALL orders globally: fetch positions, iterate over symbols, call cancel-all per symbol
   - Or implement symbol selection UI before cancel-all

7. **Exception Handling - AVOID general `except Exception`:** ✅ ADDRESSED IN DOCUMENT (Rev 4)
   - Use specific exception types: `httpx.HTTPStatusError`, `httpx.RequestError`
   - Backend uses: `AlpacaClientError`, `TimeoutError`, `HTTPException`
   - Never use bare `except Exception as e:` - always catch specific exceptions

8. **Audit Logging:**
   - ALL trading actions must be logged
   - Include: user_id, action, timestamp, details
   - Store in database for compliance
   - **CREATE `apps/web_console_ng/core/audit.py`** - wraps backend audit API

9. **Permission Checks:**
   - `trade:submit_order` - Required for order entry
   - `trade:manage_positions` - Required for close/cancel
   - `admin:flatten_all` - Required for flatten all (ADMIN ONLY)
   - `admin:kill_switch_disengage` - Required for disengaging kill switch

10. **Error Handling:**
    - Never leave user without feedback
    - Show specific error messages
    - Use specific exception types (see note 7 above)
    - Allow retry after failure

11. **Kill Switch API Schema:** ✅ ADDRESSED IN DOCUMENT (Rev 2)
    - Engage: `KillSwitchEngageRequest(operator, reason, details)`
    - Disengage: `KillSwitchDisengageRequest(operator, notes)`
    - Maps `user_id` → `operator`, `reason` → `notes` for disengage

12. **Time-In-Force Field:** ✅ ADDRESSED IN DOCUMENT (Rev 2)
    - Added to manual order form (day, gtc, ioc, fok)
    - Included in order ID generation
    - Included in order request

13. **Position Close TOCTOU Prevention:** ✅ ADDRESSED IN DOCUMENT (Rev 2)
    - Pre-dialog kill switch check
    - FRESH kill switch check before submit_order()
    - Reason required (min 10 chars) for all close operations

14. **Flatten All Improvements:** ✅ ADDRESSED IN DOCUMENT (Rev 2, Rev 4)
    - ADMIN permission required (`admin:flatten_all`)
    - Re-fetch positions at execution time (prevents stale data)
    - Kill switch check before execution
    - Kill switch check every 5 orders during batch
    - Individual failure audit logging
    - **REQUIRES MFA id_token** (Rev 4)

15. **Failure Audit Logging:** ✅ ADDRESSED IN DOCUMENT (Rev 2)
    - All actions log both success and failure
    - Includes error details in failure logs
    - Actions: position_close_failed, flatten_order_failed, flatten_all_failed, cancel_all_orders_failed

16. **Rate Limiting Implementation:** ✅ ADDRESSED IN DOCUMENT (Rev 3, Rev 6)
    - Manual orders: 10/min per user
    - Position close: 10/min per user
    - Flatten-all: 1 per 5 minutes per user
    - Cancel-all: 5/min per user
    - Kill switch actions: 1/min per user
    - Backend enforces rate limits server-side

17. **Cancel-All Kill Switch Policy:** ✅ ADDRESSED IN DOCUMENT (Rev 3)
    - Cancel-all orders BYPASSES kill switch
    - Rationale: Risk-reducing action (cancels pending, doesn't submit new)
    - **PER-SYMBOL**: Backend requires symbol in request (Rev 4)

18. **Backend Generates Order IDs:** ✅ ADDRESSED IN DOCUMENT (Rev 4)
    - Removed client-side order ID generation from code snippets
    - Backend `_generate_manual_order_id()` handles idempotency
    - Frontend receives order_id in response for display

19. **MFA Token Acquisition:** ✅ ADDRESSED IN DOCUMENT (Rev 4)
    - `id_token` for flatten-all is obtained from OAuth2 session
    - Auth middleware stores token in session during login flow
    - Access via `app.storage.user.get("id_token")` in NiceGUI
    - If token expired, prompt user to re-authenticate before flatten-all
    - Token is passed to `FlattenAllRequest(id_token=...)`

20. **core/audit.py Interface:** ✅ ADDRESSED IN DOCUMENT (Rev 4)
    ```python
    # apps/web_console_ng/core/audit.py
    """Trading action audit logger wrapper.

    NOTE: Backend already logs actions via AuditLogger in manual_controls.py.
    This wrapper is for CLIENT-SIDE audit logging to complement backend logs.
    For most actions, backend logging is sufficient.
    """
    import logging
    from datetime import UTC, datetime

    logger = logging.getLogger(__name__)

    async def audit_log(
        action: str,
        user_id: str,
        details: dict[str, object],
    ) -> None:
        """Log trading action for audit trail.

        Args:
            action: Action name (e.g., "order_submitted", "position_closed")
            user_id: User performing the action
            details: Action-specific details
        """
        logger.info(
            "trading_audit",
            extra={
                "action": action,
                "user_id": user_id,
                "details": details,
                "timestamp": datetime.now(UTC).isoformat(),
            },
        )
    ```

21. **Cancel-All UI Flow:** ✅ ADDRESSED IN DOCUMENT (Rev 4)
    - Backend requires `symbol` in `CancelAllOrdersRequest`
    - UI options:
      a) Per-symbol cancel button in orders table (recommended)
      b) "Cancel All" with symbol dropdown selection
      c) Iterate: fetch open orders, get unique symbols, call cancel-all per symbol
    - Recommended: Option (a) - cancel-all per symbol from orders table

---

## Definition of Done

- [ ] All acceptance criteria met
- [ ] Unit tests pass with >90% coverage
- [ ] Integration tests pass
- [ ] E2E tests pass (Playwright)
- [ ] Kill switch double-check verified
- [ ] Idempotent order ID matches existing pattern
- [ ] Two-factor confirmation working for destructive actions
- [ ] Rate limiting functional
- [ ] Audit logging complete
- [ ] Permission checks enforced
- [ ] No regressions in P5T1-P5T4 tests
- [ ] Code reviewed and approved
- [ ] Merged to feature branch

---

**Last Updated:** 2026-01-03 (Rev 6)
**Status:** DONE

**Rev 6 Changes (Third Planning Review Iteration):**
- Removed all outdated detailed code snippets that contradicted Implementation Notes
- Replaced with concise pseudo-code aligned with backend API contracts
- Clarified endpoint usage: T5.1 uses generic `/api/v1/orders`, T5.3 uses manual controls endpoints
- Fixed rate limit for flatten-all: 1 per 5 minutes (not 5 per minute)
- Code snippets now correctly show:
  - Backend generates order IDs (frontend receives in response)
  - Specific exception handling (httpx.HTTPStatusError, not general Exception)
  - MFA id_token for flatten-all
  - Per-symbol cancel-all

**Rev 5 Changes (Second Planning Review Iteration):**
- Added prominent notice that code snippets are illustrative only
- Updated Prerequisites with correct endpoint paths
- Added MFA token acquisition mechanism (OAuth2 session)
- Added core/audit.py interface specification
- Clarified Cancel-All UI flow options (per-symbol recommended)

**Rev 4 Changes (First Planning Review Feedback):**
- Backend generates order IDs - removed client-side generation
- Use manual controls endpoints, not generic /api/v1/orders
- Cancel-all is per-symbol, not global
- Flatten-all requires MFA id_token
- Avoid general `except Exception` - use specific types
- Added core/audit.py to Files to Create
- Fixed endpoint paths (kill-switch vs kill_switch)
