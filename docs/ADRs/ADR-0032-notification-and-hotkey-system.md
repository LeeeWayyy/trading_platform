# ADR-0032: Notification and Hotkey System

**Status:** Accepted
**Date:** 2026-01-20
**Decision Makers:** Development Team
**Related ADRs:** ADR-0031-nicegui-migration
**Related Tasks:** P6T3

## Context

The NiceGUI-based trading terminal needs a notification and hotkey system that supports high-frequency updates without overwhelming users or bypassing trading safeguards. Current direct `ui.notify()` usage causes toast spam during high activity, lacks persistent user preferences (quiet mode), and is not safe for background-thread emission. Hotkeys also require context awareness (global vs order form) and conflict avoidance with browser and input focus behavior. Additionally, multi-phase order flows (submit → confirm → fill) need clear UI feedback that does not double-submit or race against async events.

## Decision

We will implement four coordinated patterns:

### 1) Notification priority routing with quiet mode persistence
- Introduce a per-client `NotificationRouter` that routes notifications by priority: HIGH, MEDIUM, LOW.
- Toast policy:
  - Quiet mode OFF: HIGH and MEDIUM show toasts; LOW goes to the drawer only.
  - Quiet mode ON: only HIGH shows toasts; MEDIUM/LOW go to drawer only.
- All priorities add to a bounded notification history (max 100 items) and update badge counts for HIGH/MEDIUM.
- Quiet mode preference is persisted via `UserStateManager` and restored on layout initialization using a preferences key (e.g., `notification_quiet_mode`).

### 2) Hotkey context management with dynamic scope detection
- Define hotkeys with explicit scopes (`GLOBAL` vs `ORDER_FORM`) and exact modifier matching to avoid conflicts.
- Implement browser-side detection using `_isInOrderForm()` that checks `document.activeElement.closest('[data-order-form]')` at keypress time.
- Do not rely on focusin/focusout toggles to track scope (prevents scope “latching” and flicker).
- Global hotkeys with modifiers are allowed even when an input is focused; otherwise global hotkeys are suppressed while typing.

### 3) Client-side event handling with NiceGUI client context capture
- `NotificationRouter` captures the NiceGUI client context (`nicegui.context.client`) on initialization.
- Toast emission uses the captured client context (`with self._client:`) to ensure thread-safe, per-session notifications from background tasks (e.g., Redis listeners).
- If no client context exists (e.g., initialization outside a request), toasts are skipped and routed to history only.

### 4) ActionButton state machine with manual lifecycle support
- Introduce an `ActionButton` component with state feedback (`DEFAULT`, `SENDING`, `CONFIRMING`, `SUCCESS`, `FAILED`, `TIMEOUT`).
- Default mode: button return value controls success/failure.
- `manual_lifecycle=True` enables multi-phase order flows where external events drive the state via `set_external_state()`.
- Timeouts and state-based disabling prevent rapid double-submit; hotkeys trigger the button via its public `trigger()` to reuse the same guarded submission path.

## Consequences

### Positive
- Prevents toast spam while preserving critical alerts through priority routing.
- Quiet mode preference persists across sessions, improving operator focus during algo runs.
- Thread-safe toasts eliminate cross-session leakage from background tasks.
- Hotkey scope logic prevents accidental order submissions while typing.
- ActionButton lifecycle makes multi-phase order flows explicit and reduces duplicate submissions.

### Negative / Trade-offs
- Additional client-side JS logic requires browser testing across layouts and keyboards.
- If client context is unavailable, toasts are suppressed; operators must check the drawer for low/medium events in those cases.
- Manual lifecycle increases component complexity and requires disciplined state transitions in order flows.

### Follow-ups
- Document recommended hotkey bindings and conflict resolution in the UI help/command palette.
- Add regression tests for hotkey suppression when typing in inputs and for quiet mode persistence.
- Consider international keyboard layout handling if user feedback indicates issues.

## Alternatives Considered

1) **Direct `ui.notify()` everywhere**
   - Rejected: causes toast spam, no quiet mode, unsafe for background task emission.

2) **Server-side focus tracking for hotkey scope**
   - Rejected: focusin/focusout approach can latch and flicker; dynamic DOM detection is simpler and more reliable.

3) **Single global hotkey scope only**
   - Rejected: risks unintended order submission while typing or interacting with grids.

4) **No manual lifecycle on ActionButton**
   - Rejected: multi-phase order lifecycle (submit → confirm → fill) would have confusing UI feedback and be harder to coordinate safely.

---
**Last Updated:** 2026-01-20
**Author:** Development Team
