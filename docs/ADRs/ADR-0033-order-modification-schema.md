# ADR-0033: Order Modification Database Schema

- Status: Proposed
- Date: 2026-01-28

## Context
P6T6 requires order modification support for working orders via Alpaca's atomic
replace semantics. A replace creates a new order and cancels the original in a
single broker-side operation, which means we need to track lineage (original ->
replacement) and retain an auditable record of modifications. The current
`orders` table does not capture replacement relationships, and there is no
modification audit trail or idempotency mechanism for modify requests.

We also need deterministic, idempotent modify behavior under retries and
concurrency (e.g., network timeouts, double clicks) without double-replacing
orders.

## Decision
1. Add `replaced_order_id` column to `orders` to link replacement orders back to
   the original order. This enables simple lineage queries and UI grouping.

2. Create an `order_modifications` audit table to record each modification with:
   - Original and replacement order IDs
   - Modification sequence per original order
   - Client-supplied idempotency key
   - Status (pending/completed/failed)
   - Change set (JSONB) and metadata (who/when/why)

3. Store `order_modifications.original_client_order_id` with a foreign key to
   `orders.client_order_id` (ON DELETE RESTRICT). Do NOT enforce a foreign key
   for `new_client_order_id` because it is created before the replacement order
   row exists; referential integrity is ensured by reconciliation.

4. Implement two-phase modification flow:
   - Phase 1: acquire modification sequence with a short row lock and insert a
     pending modification record (idempotency key checked before lock).
   - Phase 2: call Alpaca replace without holding a DB lock.
   - Phase 3: finalize modification record, mark original order `replaced`, and
     insert the replacement order.

## Consequences
- Positive:
  - Full audit trail of modifications (regulatory and operational clarity).
  - Idempotent retries and concurrency safety.
  - Clear linkage between original and replacement orders for UI/reconciler.
- Negative:
  - Requires schema migration and additional table maintenance.
  - Adds minor write overhead for modification tracking.
- Follow-ups:
  - Add reconciler path to recover pending modifications if Alpaca succeeded but
    DB Phase 3 failed.
  - Update webhook handling to ignore status updates for orders marked
    `replaced`.
  - Implement UI to display modification history and replacement lineage.
