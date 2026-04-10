# Unified Execution Workspace UI Plan (Gemini-led)

Date: 2026-04-09
Scope: `apps/web_console_ng`
Design lead method: direct `gemini -p` CLI session (no clink)

## Requirement Summary

Redesign the trading UI into a high-density professional "Unified Execution Workspace" where market context and execution are in the same focal plane.

- Zone A (top 5%): condensed command/status strip with muted default safety indicators.
- Zone B (left 65%): price chart over a compact microstructure strip (Order Flow + half-height DOM ladder).
- Zone C (right 35%): order ticket + analytics tabs + integrated logs.
- Order ticket:
  - large split Buy/Sell actions (emerald/rose glow variants)
  - horizontal smart presets `1, 5, 10, CLOSE`
  - buying power impact gauge with warning threshold
- DOM ladder:
  - liquidity heatmap by row volume density
  - compact footprint (half prior height)
  - click price -> update ticket price with side-color pulse confirmation
- New Zone B microstructure block:
  - Order Flow / Time & Sales block above DOM (uses reclaimed space)
  - fast scan of recent prints and aggressor flow
- Activity/history:
  - compact dense data grids for scan speed
  - integrated terminal-style log area in right column bottom
- Theme:
  - base `#0f172a`
  - subtle slate borders
  - restrained emerald/rose P&L colors
  - Inter for UI text, JetBrains Mono for numeric/data text

## Visual Thesis

Information density without noise: use structure and spacing for hierarchy, reserve saturated color for risk/action/P&L states.

## Interaction Thesis

Zero-latency context-action loop: DOM click updates ticket price, preset updates size, close-position action is one-click deterministic.

## Zone Blueprint (Implementation Target)

Top-level workspace container:
- `h-screen flex flex-col overflow-hidden bg-[var(--surface-0)]`

Zone A (command strip):
- Height: `h-[5vh] min-h-[44px]`
- Classes: `flex items-center gap-3 px-4 border-b border-slate-800 bg-slate-900/90`
- Content: latency/connection, kill switch, circuit breaker, account summary, session clock

Main body:
- `flex-1 flex min-h-0`

Zone B (left observation plane):
- Width: `w-[65%] min-w-0`
- Classes: `flex flex-col border-r border-slate-800`
- Chart section: `flex-1 min-h-0 p-2 border-b border-slate-800`
- Lower microstructure strip: `h-[340px] min-h-[280px] p-2 grid grid-rows-2 gap-2`
  - Order Flow / Time & Sales: `min-h-[130px]`
  - DOM section (compact): `min-h-[130px]` (half prior visual height)

Zone C (right execution plane):
- Width: `w-[35%] min-w-[360px]`
- Classes: `flex flex-col min-h-0 bg-slate-900/25`
- Ticket section: `p-3 border-b border-slate-800 shrink-0`
- Analytics + logs section: `flex-1 min-h-0 flex flex-col`
  - tabs/grids region: `flex-1 min-h-0 overflow-auto`
  - log tail region: `h-[150px] border-t border-slate-800 bg-black/35`

## Component Redesign Decisions

### 1) Header / Safety Strip (`ui/layout.py`)
- Convert current large badges/buttons into compact status pills.
- Normal states use slate palette (`text-slate-300`, muted border).
- Dangerous states:
  - Kill switch engaged: rose tint + strong text
  - Circuit tripped: rose tint + optional subtle glow ring
- Keep engage/disengage actions, but de-emphasize default visual weight.

### 2) Dashboard Composition (`pages/dashboard.py`)
- Replace current multi-grid/stack section with tri-pane workspace container.
- Keep existing `OrderEntryContext` wiring and lifecycle logic.
- Place PriceChart and lower microstructure strip (Order Flow + compact DOM) in left pane.
- Place OrderTicket and TabbedPanel in right pane.
- Add integrated log tail card at right-bottom using notification history stream.

### 3) Order Ticket (`components/order_ticket.py`)
- Reframe card as dense execution module:
  - prominent symbol/side/qty row
  - large dual action controls (Buy/Sell style treatment)
- Add buying power gauge under impact text:
  - ratio = notional / buying_power
  - color: normal (<50%), warning (>=50%), danger (>=80%)
  - future hook: replace hard thresholds with risk settings value when exposed
- Add pulse hook method for DOM click confirmation (class toggle on price input).

### 4) Quantity Presets (`components/quantity_presets.py`)
- Presets become `1`, `5`, `10`, `CLOSE`.
- `CLOSE` logic:
  - if current position > 0, preset qty = abs(position), side should be sell
  - if current position < 0, preset qty = abs(position), side should be buy
  - if position == 0, notify "No open position to close"
- Keep MAX logic available for risk-aware sizing, but visually secondary.

### 5) DOM Ladder (`components/dom_ladder.py`, `static/js/dom_ladder.js`)
- Liquid heatmap row background intensity from `level.ratio`.
- Add CSS variables per row (e.g. `--liquidity-alpha`) and side-specific tint.
- Keep ladder in compact panel (half prior height) and optimize row density for scan speed.
- Keep click-to-prefill logic; add ticket-side pulse event dispatch after click routed.

### 6) Order Flow / Time & Sales (`components/*` new compact block)
- Add a compact panel above DOM in Zone B lower strip.
- Show recent prints, side aggressor cue, and short rolling flow summary.
- Keep this block read-focused (no order-entry actions).

### 7) Tabs + Activity/Logs (`components/tabbed_panel.py`, `components/activity_feed.py`)
- Keep dense grid behavior from `density.css`.
- Improve tab strip contrast and compact spacing for scan speed.
- Add integrated log pane at bottom-right:
  - mono font
  - fixed small row height
  - auto-scroll newest at top or bottom (decide and keep consistent)
  - surface fill/cancel/connectivity events immediately

### 8) Theme Tokens (`static/css/custom.css`, `static/css/density.css`)
- Set deep navy baseline and slate layer tokens.
- Add restrained glow classes for:
  - positive (`emerald`)
  - negative (`rose`)
- Add pulse animation utilities for price confirm.
- Enforce typography split:
  - Inter for UI labels/controls
  - JetBrains Mono for numeric + market data

## Accessibility + Responsive Fallback

Desktop-first behavior:
- Keep tri-pane full-screen with keyboard-reachable controls.
- Minimum contrast target WCAG AA for text on navy/slate surfaces.

Mobile/tablet fallback:
- `< lg`: collapse to single-column stack in order:
  1. command strip
  2. chart
  3. Order Flow / Time & Sales
  4. compact DOM
  5. watchlist/symbol selector
  6. order ticket
  7. tabs
  8. log tail
- Ensure touch targets >= 40px on trade action controls.
- Avoid horizontal overflow in grid-heavy panes.

## Ordered Implementation Checklist

1. Update theme tokens and utility classes in CSS (`custom.css`, `density.css`).
2. Refactor dashboard page into tri-pane layout scaffold.
3. Refactor order ticket structure and buy/sell visual treatment.
4. Implement buying power gauge and threshold state styles.
5. Update quantity preset logic and UI (`1,5,10,CLOSE`, retain MAX support if needed).
6. Add Zone B Order Flow / Time & Sales compact block.
7. Upgrade DOM ladder renderer to compact liquidity heatmap rows.
8. Add DOM-click price pulse feedback path into ticket UI.
9. Integrate right-column log tail region and wire feed updates.
10. Tighten tab/grid density visuals for right-column analytics.
11. Adjust header strip for compact muted safety indicators.
12. Validate desktop + mobile behavior.
13. Run formatting/lint/tests relevant to changed modules.

## Notes

- This document records the agreed plan and UI direction before branch creation.
- Next step: sync latest `master`, create a dedicated implementation branch, then execute checklist.

## Addendum: Model + Strategy Interface Design (Gemini Discussion)

Date: 2026-04-10

### Scope Decision

- Dashboard (`/`): read-focused strategy/model context + limited high-safety quick actions.
- Dedicated pages (`/strategies`, `/models`): full lifecycle controls, detailed edits, deeper audits.

### Dashboard Integration (Zone C)

Add a compact `StrategyContextWidget` directly above the order ticket.

Widget content:
- Active strategy name + status badge (`active`, `inactive`, `idle`).
- Active model version (mono text) + status ring (`active`, `testing`, `failed`, `stale`).
- Tiny active-model performance sparkline (short horizon).
- Safety banner when strategy inactive or model failed.

Allowed quick actions in dashboard:
- Strategy emergency deactivation.
- Promote pre-validated testing model to active (with explicit typed confirmation).

Do not include heavy management actions in dashboard (editing configs, bulk archive, full history).

### Dedicated Page Redesign Direction

`/strategies` should shift to dense grid-first layout:
- Columns: status, name, strategy_id, exposure summary, recent activity, updated_at/by, actions.
- Row detail: side panel / expansion for deeper info.

`/models` should shift to dense grid-first layout:
- Columns: status, version, strategy, deployed_at, short metrics, path, actions.
- Row detail: existing JSON/config/perf detail remains expandable.

### Safety UX Guardrails

Before strategy deactivation:
- Must run open-exposure check inline; if open positions/orders exist, show strong warning and require explicit confirmation.

Before model activation/promote:
- Type-to-confirm flow (`ACTIVATE`) and include impact summary (target strategy, current active model, new model).

Execution gating:
- Disable order submission when strategy inactive or model failed for selected symbol context.
- Show inline reason near action buttons instead of only modal errors.

### Workflow Alignment

Recommended operator flow:
1. Select symbol.
2. Confirm strategy context in widget.
3. Confirm active model/version and health.
4. Execute order from ticket.
5. Observe fills/logs and context status in same pane.

### Implementation Follow-Up (File Mapping)

- `apps/web_console_ng/pages/dashboard.py`
  - Mount `StrategyContextWidget` in Zone C above ticket.
  - Bind symbol selection + realtime state to widget.
- `apps/web_console_ng/pages/strategies.py`
  - Move from card list to dense ag-grid style presentation.
- `apps/web_console_ng/pages/models.py`
  - Move from card list to dense ag-grid style presentation.
- `apps/web_console_ng/components/strategy_context.py` (new)
  - Build compact strategy/model status + quick safe actions.
- `apps/web_console_ng/components/order_ticket.py`
  - Gate execution when strategy/model context unsafe; show inline disabled reason.

## Addendum: Global Page Placement IA (Gemini Discussion)

Date: 2026-04-10

### Proposed Top-Level Nav Groups

- Execute
- Monitor
- Research
- Analysis
- Data
- Governance
- Auth (not in main trader nav)

### Placement Summary (Workspace vs Page)

Move into workspace quick-access components:
- `/manual-order` -> Execute drawer/panel
- `/position-management` -> workspace panel/bottom sheet
- `/circuit-breaker` -> global header control + detail drawer
- `/alerts` -> notification drawer
- `/journal` -> contextual panel
- `/compare` -> research overlay (instead of standalone first-class page)
- `/data/inspector` -> floating inspector overlay

Remain dedicated pages:
- `/risk`, `/risk/exposure`, `/execution-quality`, `/attribution`, `/tax-lots`
- `/backtest`, `/alpha-explorer`, `/research/universes`, `/strategies`, `/models`, `/notebooks`
- `/performance`, `/reports`
- `/data`, `/data/coverage`, `/data/sources`, `/data/features`, `/data/sql-explorer`
- `/health`, `/admin`

Admin/ops hidden from trader-default nav:
- `/data/shadow`

Auth flow routes remain separate from app navigation:
- `/login`, `/mfa-verify`, `/forgot-password`

### Suggested Merges / Simplifications

- Consider merging `/risk` + `/risk/exposure` into one Risk page with tabs.
- Consider folding `/compare` into `/backtest` multi-run compare mode.
- Consider consolidating `/data/coverage`, `/data/sources`, `/data/features` as tabs under `/data`.
- Keep `/data/shadow` as ops/admin-only functionality.

### Rollout Sequence

1. Phase 1: Navigation grouping only (no route removals).
2. Phase 2: Workspace overlays/panels for execute-critical workflows.
3. Phase 3: Route consolidation/merges after usage validation.

## Addendum: Claude Review Resolutions (All Issues Addressed)

Date: 2026-04-10

This section resolves all issues raised in the `claude` plan review and supersedes any conflicting language above.

### 1) Safety Invariants (Hard Requirements)

- `CLOSE` is a prefill action only. It must never submit an order.
- `CLOSE` must still follow existing two-phase flow:
  1. Prefill qty + side
  2. `Preview Order`
  3. `Confirm Order`
- `CLOSE` must fail-closed on stale position data:
  - if position timestamp exceeds `POSITION_STALE_THRESHOLD_S`, show warning and refuse prefill.
- No new fast path may bypass `OrderTicketComponent._should_disable_submission()` checks.

### 2) Strategy/Model Execution Gating: Required Data Path

Execution gating for strategy/model safety requires a new explicit data contract.

Required implementation before enabling gating:
- Backend source of truth for symbol->strategy mapping and strategy/model runtime health.
- Transport:
  - Preferred: realtime Redis pub/sub channel for strategy/model state changes.
  - Fallback: short-interval polling endpoint.
- `OrderEntryContext` becomes subscription owner for this data (single-owner pattern).
- Unknown/unavailable strategy/model status is fail-closed for risk-increasing orders.

Status states for UI and gating:
- Strategy: `active | inactive | idle | unknown`
- Model: `active | testing | failed | stale | unknown`

### 3) Layout Ownership Strategy (`main_layout` Compatibility)

To avoid global breakage:
- Keep `main_layout` as the shared shell for all pages.
- Introduce dashboard-only workspace mode inside page content area (not replacing global shell).
- Avoid raw full-viewport assumptions that conflict with existing header/drawer height.
- Effective dashboard content container should use available-height calculation under header.

### 4) Notification/Log Tail Ownership

`NotificationRouter` currently uses single callback ownership via `LogDrawer`.

Resolution:
- Do not add a second direct callback consumer without refactor.
- Phase 1 approach:
  - keep current `LogDrawer`
  - inline log tail uses a separate feed source (activity/trade/system events already routed in dashboard scope)
- Phase 2 optional refactor:
  - upgrade `NotificationRouter` to multi-subscriber event bus if unified source is required.

### 5) Buying Power Gauge Threshold Policy

- Remove static 50/80 assumptions as primary authority.
- Gauge thresholds must derive from loaded risk context:
  - effective limit baseline: `min(buying_power, max_notional_per_order if present)`
  - exposure policy aware when max total exposure is configured.
- If limits are unavailable/stale, gauge state shows `unavailable` and avoids safe-looking visuals.

### 6) Quantity Preset Semantics

`1/5/10` are ambiguous for this platform.

Resolution:
- Presets become configurable by mode:
  - Equity default: `100, 500, 1000, CLOSE`
  - Optional lot-mode UI may expose `1, 5, 10 lots` with clear label.
- Document unit explicitly in UI label (`shares` or `lots`).

### 7) Theme Token Migration Safety

To avoid global regressions:
- New workspace palette is scoped (e.g., `.workspace-v2`) instead of immediate global token replacement.
- Existing token system remains for non-redesigned pages during transition.
- Global token migration happens only after page-by-page adoption audit.

### 8) StrategyContext Sparkline Dependency

Per-model sparkline is deferred until data exists.

Phase 1:
- Show model status + basic metadata only (no sparkline).

Phase 2:
- Add sparkline only after backend endpoint/channel is available and documented.

### 9) `OrderEntryContext` Lifecycle Ownership Expansion

New realtime/safety-aware dashboard components (e.g., `StrategyContextWidget`) must have explicit ownership.

Resolution:
- Add creation/wiring/cleanup hooks in `OrderEntryContext` for strategy/model context component.
- All timers/subscriptions must be registered in lifecycle cleanup callbacks.

### 10) Mobile Safety Order

Mobile stack order is revised to preserve market context before execution:

1. command strip
2. chart
3. Order Flow / Time & Sales
4. compact DOM
5. watchlist/symbol selector
6. order ticket
7. tabs
8. log tail

### 11) Watchlist Placement (Explicit)

Watchlist remains required and must not be dropped.

Placement:
- Desktop: compact watchlist/symbol selector in Zone C top area (above strategy context + ticket), or left-pane collapsible strip.
- Mobile: watchlist appears before order ticket.
- Symbol selection path remains explicit and primary.

### 12) Confirmation UX for Model Promotion

Static `ACTIVATE` confirmation is insufficient.

Resolution:
- Require contextual confirmation text (model version or `strategy_id:model_version`).
- Include timeout and validation failure behavior.
- Show impact summary before confirmation (current active model -> target model).

### 13) DOM Click Update Stabilization

Add click-to-price stabilization:
- last-click-wins semantics with short debounce/settling window before action buttons are considered ready.
- visible settle cue on price input pulse.

### 14) Typography Loading Strategy

Implement explicit font loading for required families:
- Inter for UI text
- JetBrains Mono for numeric/data text

Loading approach:
- Self-hosted preferred in `static/fonts` with fallback stack.
- Head injection and CSS declarations must be documented and deterministic.

### 15) Feature Flag + Rollback Plan

Dashboard redesign must be reversible:
- Introduce feature-flagged workspace mode (or `/dashboard-v2` route).
- Keep legacy dashboard path available during validation window.
- Define rollback switch and acceptance metrics before default cutover.

### 16) Revised Implementation Checklist (Supersedes Previous Order)

1. Define feature flag / v2 route and rollback toggle for dashboard.
2. Refactor header/content height integration strategy under `main_layout` (dashboard-safe).
3. Add scoped workspace theme tokens and font-loading implementation.
4. Preserve and place watchlist/symbol selector explicitly in new layout.
5. Build tri-pane workspace scaffold in `dashboard.py` (without removing legacy path).
6. Refactor order ticket visuals while preserving existing submission safety flow.
7. Implement `CLOSE` as prefill-only with stale-position guard.
8. Implement risk-derived buying power gauge with unavailable/fail-closed states.
9. Implement Zone B Order Flow / Time & Sales compact block (read-focused).
10. Implement compact DOM heatmap + price pulse + debounce/settling semantics.
11. Add strategy/model data contract and `OrderEntryContext` wiring (subscription owner model).
12. Add `StrategyContextWidget` status-only in phase 1; defer sparkline until data exists.
13. Gate execution by strategy/model safety only after data path is production-ready.
14. Implement dashboard inline log tail with non-conflicting source (or router refactor).
15. Apply navigation grouping changes (Execute/Monitor/Research/Analysis/Data/Governance).
16. Redesign `/strategies` and `/models` into dense grid-first pages.
17. Validate desktop/mobile safety flows and run lint/tests/perf checks.

## Addendum: Gemini Detailed UI Review (High-Fidelity Spec)

Date: 2026-04-10

### Remaining Gaps (Post-Resolution)

1. **High**: Strategy/model health data contract still needs explicit backend interface.
   - **Fix**: define endpoint + realtime channel and wire through `OrderEntryContext` with fail-closed unknown handling.
2. **Medium**: Reconnect/stale-state masking behavior not fully specified.
   - **Fix**: add dashboard overlay/mask and interaction lock while state is stale/disconnected.
3. **Low**: Quantity step precision not symbol-aware.
   - **Fix**: support symbol-driven qty step/lot size metadata.
4. **Low**: DOM click settling window should be explicit.
   - **Fix**: enforce 150ms settle/last-click-wins before actions re-enable.

### Dashboard High-Fidelity Composition

Root composition:
- `h-screen flex flex-col overflow-hidden bg-[#020617]` (slate-950 backdrop)
- Zone A: `h-11 shrink-0 bg-[#0f172a] border-b border-slate-800`
- Workspace body: `flex-1 flex min-h-0`
- Zone B: `flex-1 min-w-0 flex flex-col border-r border-slate-800`
- Zone C: `w-[380px] shrink-0 bg-[#0f172a]/50 flex flex-col`
- Zone B lower strip: split into two compact panels (Order Flow / Time & Sales + compact DOM), 50/50 within fixed lower band.

Spacing/motion defaults:
- Spacing base: 4px rhythm (4/8/12/16)
- Radius scale: 4, 6, 8
- Motion: `duration-200 ease-out` default interaction transitions
- Price-confirm pulse: short custom pulse around 300-400ms

Typography:
- UI label: Inter, uppercase micro labels for status surfaces
- Numeric/data: JetBrains Mono + tabular nums

### Component Detail Baseline

1) Header safety strip
- Compact status pills only; low-noise default, high-contrast critical.
- Keep kill-switch/circuit-breaker controls accessible but visually subordinate during normal state.

2) Watchlist/symbol selector
- Explicitly present in workspace (desktop and mobile).
- Symbol selection remains first-order path for chart/DOM/ticket synchronization.

3) StrategyContextWidget
- Place above order ticket in Zone C.
- Show strategy status + model version/status.
- Phase 1: status-only (no sparkline dependency).
- Fail-closed visual state should disable risk-increasing ticket actions.

4) Order ticket
- Large BUY/SELL actions with restrained glow.
- Presets: equity default `100, 500, 1000, CLOSE`.
- `CLOSE` prefill-only and stale-position guarded.

5) Risk gauge
- Use risk-derived effective thresholds from loaded limit context.
- Render explicit unavailable state when risk limits are stale/missing.

6) Price chart panel
- Dominant context panel in Zone B top.
- Must remain visible before execution controls in mobile order.

7) Order Flow / Time & Sales panel
- Located in Zone B lower strip above DOM.
- Shows recent prints and aggressor-side flow cues in a compact, read-only layout.

8) DOM ladder heatmap + click pulse
- Volume-density heatmap row treatment.
- DOM panel remains compact (half prior height).
- DOM click updates ticket price with settle/debounce + visible pulse.

9) Tabs/grids
- High-density compact grid profile aligned to existing `density.css` behavior.

10) Inline log tail
- Terminal-style compact mono surface.
- Use non-conflicting event source unless notification router is refactored to multi-subscriber.

### `/strategies` and `/models` Detail Direction

Both pages move to dense grid-first control surfaces:
- `/strategies` columns: status, name, strategy_id, exposure, activity, updated_at/by, actions.
- `/models` columns: status, version, strategy, deploy/build metadata, short performance rank/metrics, actions.
- Row drill-in via side panel/expansion; avoid modal-heavy navigation.
- Promotion/toggle flows require contextual typed confirmation and explicit impact summary.

### Navigation Implementation Detail

Target grouped navigation:
- Execute
- Monitor
- Research
- Analysis
- Data
- Governance

Migration note:
- group-first nav refactor may precede route consolidation;
- keep old routes active while adding grouped IA and overlay entry points.

### PR-Sized Packaging Recommendation

1. Theme + typography package (scoped tokens and font loading).
2. Dashboard scaffold package (tri-pane with legacy-safe shell compatibility).
3. Execution controls package (ticket visual refactor + CLOSE safety + risk gauge).
4. Market context package (Order Flow block + compact DOM heatmap/pulse/settling behavior).
5. Strategy/model surfaces package (`StrategyContextWidget`, `/strategies`, `/models` grid modernization).
6. Navigation grouping package and rollout toggles.

### Additional Safety Validation Checklist

- Verify `CLOSE` never submits directly.
- Verify disconnect/stale state masks execution interactions.
- Verify strategy/model unknown state fails closed for risk-increasing actions.
- Verify idempotency path remains intact in preview/confirm flow.
- Verify circuit-breaker and kill-switch blocks still supersede all ticket actions.

## Implementation Progress (2026-04-10)

Completed in current branch (`codex/unified-execution-workspace-ui`):

1. Dashboard workspace scaffold (phase-1)
- Added feature flag `FEATURE_UNIFIED_EXECUTION_WORKSPACE` (default `true`).
- Implemented tri-pane dashboard composition in `pages/dashboard.py`:
  - command metric strip
  - Zone B chart + Order Flow + compact DOM
  - Zone C watchlist + strategy context + ticket + tabs + inline log tail
- Preserved legacy dashboard layout path when feature flag is disabled.

2. Order ticket execution module redesign
- Reworked `OrderTicketComponent` visual structure to dense execution layout.
- Added large split action buttons (`BUY` / `SELL`) that keep existing preview/confirm flow.
- Added risk-derived impact gauge with `NORMAL/WARNING/DANGER/UNAVAILABLE` states.
- Added `CLOSE` preset integration (prefill-only, no auto-submit) with stale-position guard.
- Added DOM-click price pulse animation and 150ms settle window on action buttons.

3. New supporting components
- Added `components/strategy_context.py` (`StrategyContextWidget`) for status-aware context placement above ticket.
- Added `components/log_tail_panel.py` (`LogTailPanel`) for terminal-style inline right-column logs.
- Added `components/order_flow_panel.py` and wired live trade updates to it.

4. DOM ladder compact heatmap
- Updated `dom_ladder.js` and CSS for liquidity heatmap row intensity by volume ratio.
- Kept compact ladder footprint (`levels=5`) and click-to-prefill behavior.

5. Theme and density updates
- Added scoped `.workspace-v2` palette, typography split (Inter + JetBrains Mono), panel/action/risk styles.
- Added compact grid adjustments for workspace mode.
- Condensed header safety/status badge sizing via CSS.

6. Verification
- `ruff check` passes for all touched Python files.
- Targeted tests pass:
  - `tests/apps/web_console_ng/test_quantity_presets.py`
  - `tests/apps/web_console_ng/test_order_ticket.py`
  - `tests/apps/web_console_ng/pages/test_dashboard.py`
  - `tests/apps/web_console_ng/pages/test_dashboard_dispatch.py`
  - `tests/apps/web_console_ng/test_order_entry_context.py`

7. `/strategies` + `/models` dense-grid modernization (phase-1 of page alignment)
- `pages/strategies.py` now renders a compact dense row surface:
  - status/name/strategy_id/activity/exposure placeholder/updated metadata/actions
  - expandable detail row per strategy
  - existing admin toggle flow and exposure safety confirmation preserved
- `pages/models.py` now renders strategy-grouped dense rows:
  - status/version/strategy/deployed-at/metrics/path/actions
  - compact status summary pills
  - expandable detail row with metrics/config metadata
- Model activation/deactivation dialog now requires contextual typed token:
  - `strategy_name:version`
  - includes explicit impact summary before confirmation

8. Navigation IA grouping (phase-1)
- Updated sidebar rendering in `ui/layout.py` to grouped sections:
  - `Execute`, `Monitor`, `Analysis`, `Research`, `Governance`
- Preserved existing `nav_items` permissions and feature-flag logic.
- Added grouped-section rendering with fallback for unmapped future routes.
