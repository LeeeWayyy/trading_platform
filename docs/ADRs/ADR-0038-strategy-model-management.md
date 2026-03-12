# ADR-0038: Strategy & Model Management with Fail-Closed Gating

**Status:** Accepted
**Date:** 2026-03-12
**Context:** P6T17

## Context

The platform lacked UI controls for strategy lifecycle (enable/disable) and model
registry browsing. Signal generation had no pre-flight check for strategy active status,
meaning disabled strategies could still generate signals.

## Decision

1. **Fail-closed strategy gating in signal service**: Before generating signals,
   `_check_strategy_active()` queries the `strategies.active` column. Returns a
   tri-state: `"active"` (proceed), `"inactive"` (HTTP 403), `"error"` (HTTP 503).
   On DB errors the check fails closed — no signals generated.

2. **Strategy Management page** (`/strategies`): Admin-only toggle with exposure
   check (open positions/orders warning). Uses `SELECT ... FOR UPDATE` to prevent
   concurrent toggle races.

3. **Model Registry Browser** (`/models`): Read-only view of model statuses from
   the `model_registry` table, with admin activate/deactivate controls. Model
   deactivation is **administrative only** — it sets DB status but does NOT stop a
   running signal service from using an already-loaded model. To stop signal
   generation, use the strategy toggle.

4. **Alert Configuration Enhancement**: Added PagerDuty channel, inline rule
   editing with PII masking (masked placeholders, never expose raw secrets),
   pagination, filtering, and bulk acknowledgment.

## Consequences

- Strategy toggle is the authoritative control for stopping signal generation.
- Model deactivation is for lifecycle tracking, not live trading control. Future
  work may integrate DB model status into the signal service reload loop.
- All admin actions are audit-logged via `AuditLogger`.
- RBAC: `MANAGE_STRATEGIES` + `VIEW_ALL_STRATEGIES` for strategy list,
  `is_admin()` for toggle. `VIEW_MODELS` for model browser, `is_admin()` for
  activate/deactivate.
