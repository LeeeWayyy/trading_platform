# Circuit Breaker Trade Control

## Overview

Circuit breaker status is global, but manual trip/reset actions now live in the Trade workspace order-entry area. The former standalone `/circuit-breaker` page has been removed to keep emergency execution controls inside the trading cockpit.

The global header remains the monitoring surface: it shows the current circuit state beside the other safety indicators. The Order Ticket header is the action surface: it exposes a compact circuit breaker icon that opens a confirmation dialog.

## Placement

| Surface | Purpose | Interaction |
|---------|---------|-------------|
| Global header safety strip | Monitor current circuit state from any page | Read-only status badge |
| Trade workspace Order Ticket header | Trip or reset the circuit breaker during execution workflow | Icon button plus confirmation dialog |

This separates the circuit breaker from the kill switch. The kill switch is a broader emergency stop, while the circuit breaker tactically blocks new entries and may still allow risk-reducing exits. Keeping their primary actions apart reduces accidental activation risk.

## Status Indicators

| State | Icon | Tone | Description |
|-------|------|------|-------------|
| `OPEN` | `lock` | Emerald | Trading is allowed; clicking can halt new entries after confirmation |
| `TRIPPED` | `lock_open` | Rose | New entries are blocked; clicking can request reset after confirmation |
| `UNKNOWN` | `help_outline` | Slate | State cannot be verified; control is disabled fail-closed |

## RBAC Requirements

Circuit breaker actions are still enforced by the service layer.

| Action | Required Permission |
|--------|---------------------|
| Trip breaker | `TRIP_CIRCUIT` |
| Reset breaker | `RESET_CIRCUIT` |

The UI only enables the corresponding action when the current user has permission and the circuit state is actionable. Backend RBAC, validation, rate limiting, and audit logging remain authoritative.

## Manual Trip Workflow

1. Open the Trade workspace.
2. Locate the circuit breaker icon in the Order Ticket header.
3. Click the icon while the state is `OPEN`.
4. Confirm **Halt Trading** in the modal.

No reason input is required. The UI submits the deterministic audit reason `MANUAL`.

**Impact:** When tripped, all new risk-increasing order entries are blocked. Risk-reducing exits may remain available under the existing safety-gate semantics.

## Manual Reset Workflow

1. Open the Trade workspace.
2. Locate the circuit breaker icon in the Order Ticket header.
3. Click the icon while the state is `TRIPPED`.
4. Confirm **Resume Trading** in the modal after verifying recovery conditions.

No reason input is required. The UI submits a deterministic audit reason indicating trade-workspace manual reset confirmation.

**Rate limit:** Reset operations remain globally rate-limited by the circuit breaker service.

## Safety Guarantees

- Unknown Redis/service state disables the action control fail-closed.
- The global header status remains visible for situational awareness.
- The order ticket submission pipeline still performs its existing preview, confirm, and fresh safety checks.
- Trip/reset operations use `run.io_bound` because the circuit breaker service is synchronous.
- Service-layer RBAC, validation, audit logging, and rate limiting are unchanged.

## Redis Key Schema

Circuit breaker state is stored in Redis:

| Key | Type | Description |
|-----|------|-------------|
| `circuit_breaker:state` | JSON | Current state and metadata |
| `circuit_breaker:history` | ZSET | Trip/reset history with timestamps |
| `circuit_breaker:reset_rate_limit` | String | Reset rate-limit counter with TTL |

Example state payload:

```json
{
  "state": "OPEN",
  "tripped_at": null,
  "trip_reason": null,
  "trip_details": null,
  "reset_at": null,
  "reset_by": null
}
```

## Related Docs

- [Circuit Breaker Operations Runbook](../RUNBOOKS/circuit-breaker-ops.md)
- [Risk Management ADR](../ADRs/0011-risk-management-system.md)
