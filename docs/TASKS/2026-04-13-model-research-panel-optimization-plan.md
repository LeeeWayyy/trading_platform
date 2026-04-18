# Model + Research Panel Optimization Plan (Gemini-Informed)

Date: 2026-04-13  
Scope: `apps/web_console_ng` (`/models`, `/alpha-explorer`, `/backtest`)

## Inputs Used

- Direct Gemini CLI discussion (`gemini -p`) over current code.
- Local code inspection of:
  - `apps/web_console_ng/pages/models.py`
  - `apps/web_console_ng/pages/alpha_explorer.py`
  - `apps/web_console_ng/pages/backtest.py`
  - `libs/web_console_services/model_registry_browser_service.py`
  - `libs/web_console_services/alpha_explorer_service.py`
  - `libs/models/models/registry.py`

## Current Backend Reality (Must Preserve)

1. Model surfaces are split:
   - `/models`: Postgres `model_registry` (`active/inactive/testing/failed`) via `ModelRegistryBrowserService`.
   - `/alpha-explorer`: file-based/DuckDB `ModelRegistry` (`staged/production/archived/failed`) via `AlphaExplorerService`.
2. `alpha_explorer` deep-links to `/backtest?signal=...`, but `/backtest` currently does not consume query params.
3. `AlphaExplorerService` already carries `backtest_job_id` in `SignalSummary` (from `ModelMetadata.parameters` when present).
4. Backtest page already has dense form + results + compare primitives; extending prefill and handoff is low-risk.
5. RBAC and safety must remain unchanged (auth decorators, permission checks, admin-only activation/deactivation).

## Gemini Suggestions (Filtered to Code-Backed Work)

### Can Ship Now (No New Backend Contract)

1. Deep-link prefill from Alpha Explorer to Backtest form.
2. Model version diff UX using existing `config`/`performance_metrics` payloads in `/models`.
3. Promotion-readiness indicators in Alpha Explorer using already-available metrics (`ic`, `sharpe`, `paper_trade_hours` when present).
4. Better lifecycle clarity between research artifacts and operations registry rows (UI lineage labels, not backend merge yet).

### Thin Backend Extension

1. Add summary/count fields from model service (`list_strategies_with_models` currently returns strategy name only).
2. Add read-only registry bridge endpoint/service method to expose staged/production research artifacts alongside Postgres operational models.
3. Add explicit backtest-to-model handoff helper API (typed payload from backtest result metadata).

### Major Extension

1. True unified model lifecycle across DuckDB and Postgres with explicit ownership and sync policy.
2. Live-vs-backtest drift monitoring with authoritative live performance feed and permission-scoped exposure.

## IA Direction (Next UI Iteration)

### `/models`

- Keep dense strategy-grouped surface, add:
  - quick filters (strategy/status/search)
  - optional compare drawer for version config/metrics diff
  - lineage chips: source backtest id / registry source type (ops vs research)
- Preserve typed confirmation flows for state-changing actions.

### `/alpha-explorer`

- Keep current list + detail tabs, add:
  - sortable/rankable signal table mode (IC/ICIR/hit-rate)
  - promotion-readiness column
  - stronger CTA path to backtest with prefilled configuration context

### `/backtest`

- Consume query prefill from explorer.
- Add explicit “research handoff” panel in results with immutable provenance details.
- Keep compare mode as primary research validation surface.

## Implementation Plan

## Phase 1 (1-2 PRs, safe/fast)

### PR A: Alpha -> Backtest Prefill Path

Files:
- `apps/web_console_ng/pages/alpha_explorer.py`
- `apps/web_console_ng/pages/backtest.py`
- `tests/apps/web_console_ng/pages/test_alpha_explorer.py`
- `tests/apps/web_console_ng/pages/test_backtest.py`

Changes:
- Encode selected signal context in navigation query.
- Parse query params in backtest page and prefill form controls (`alpha`, optional dates/weights/provider when present).
- Add non-blocking warning when prefill metadata is partial or stale.

### PR B: Model Grid UX Hardening

Files:
- `apps/web_console_ng/pages/models.py`
- `tests/apps/web_console_ng/pages/test_models.py`

Changes:
- Add client-side filter bar (strategy/status/search).
- Add config/metrics diff view for selected versions in same strategy.
- Add explicit source/lineage labels where data is already present.

## Phase 2 (2-3 PRs, thin service additions)

### PR C: Strategy Summary Enrichment

Files:
- `libs/web_console_services/model_registry_browser_service.py`
- `apps/web_console_ng/pages/models.py`
- `tests/apps/web_console_ng/pages/test_models.py`

Changes:
- Extend strategy listing to return counts/status summary for top-row density and quick triage.
- Keep existing RBAC scoping semantics unchanged.

### PR D: Alpha Readiness Indicators

Files:
- `libs/web_console_services/alpha_explorer_service.py`
- `apps/web_console_ng/pages/alpha_explorer.py`
- `tests/apps/web_console_ng/pages/test_alpha_explorer.py`

Changes:
- Compute readiness flags from existing metrics payload (with clear “unknown” state when missing).
- Surface readiness badges and sorter options.

## Phase 3 (major architecture)

### PR E: Unified Registry Read Model

Files:
- `libs/web_console_services/model_registry_browser_service.py`
- `libs/web_console_services/alpha_explorer_service.py`
- `apps/web_console_ng/pages/models.py`
- New service module for merged read model (to be defined)
- Dedicated tests across service + page layers

Changes:
- Add explicit merged read model for operator UI (research artifact + operational status).
- Keep write paths separated initially; merge read-only first, then introduce controlled transitions.

## Risks and Mitigations

1. Dual-registry confusion  
Mitigation: always display source-of-truth label per row; avoid silent status mapping.

2. RBAC regressions  
Mitigation: keep service-level permission checks as enforcement boundary; add negative tests.

3. Prefill creating invalid submissions  
Mitigation: prefill only; never bypass existing submit validation.

4. UI density reducing clarity  
Mitigation: keep compact but explicit labels and deterministic status badges.

5. Drift of status vocabularies  
Mitigation: normalize display vocabulary at UI adapter layer with raw-status hover/details.

6. Overcoupling explorer/backtest/model flows  
Mitigation: start with link/prefill contract, then iterate to stronger handoff service contract.
