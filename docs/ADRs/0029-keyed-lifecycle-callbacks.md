# ADR-0029: Keyed Lifecycle Callbacks for ClientLifecycleManager

## Status
Accepted

## Context

`ClientLifecycleManager` manages per-client cleanup callbacks in the NiceGUI
web console. When a client disconnects, registered callbacks run to cancel
timers, release resources, etc.

**Problem:** Pages that register cleanup callbacks on every reload accumulate
duplicate callbacks over successive page navigations within the same client
session. Each reload appends a new callback referencing fresh timer instances,
while stale callbacks referencing already-garbage-collected timers remain.

## Decision

Add an optional `owner_key: str` parameter to `register_cleanup_callback()`.
When provided, the new callback **replaces** any existing callback with the
same `owner_key` for that `client_id`, ensuring at most one callback per
(client, owner_key) pair.

**Key design choices:**

- **Backward compatible:** Callbacks without `owner_key` append without dedup.
- **Atomic replacement:** Filter + append under `asyncio.Lock`.
- **Migration-tolerant dispatch:** `cleanup_client()` handles both tuple
  `(callback, owner_key)` and bare callable entries during migration.
- **Scope:** NiceGUI assigns unique `client_id` per browser tab. Multiple
  tabs = multiple clients; owner_key dedup is per-tab.

## Consequences

- Pages choose a stable `owner_key` per registration site.
- Legacy callers continue to work without changes.
- The bare-callable path should be removed once all callers adopt tuples.
- No database or configuration changes required.

## Alternatives Considered

1. **NiceGUI native page cleanup:** Destroys page contexts on navigation but
   callbacks fire on disconnect (tab close), not navigation.
2. **Weak references:** Auto-expire but don't prevent duplicate accumulation.
3. **Explicit unregister API:** More error-prone than automatic replacement.
