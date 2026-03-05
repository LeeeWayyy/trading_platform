# Libs Directory Context

Shared libraries used by all microservices. Changes here have high blast radius.

## Before Modifying Shared Code

1. **Grep all call sites** before changing any function signature or class interface
2. **Run full test suite** (`make test`) — changes here affect multiple services
3. **Check for feature parity** — research and production must share feature code

## Key Libraries

- `common/` — Logging, configuration, shared utilities
- `core/` — Core domain models and interfaces
- `trading/` — Order types, position management, risk calculations
- `data/` — Data pipeline, market data access, feature computation
- `analytics/` — Performance metrics, P&L calculation, reporting
- `models/` — Pydantic models shared across services
- `platform/` — Platform-level utilities (health checks, lifecycle)

## Package Boundaries

- Libraries MUST NOT import from `apps/` (dependency flows: apps -> libs, never reverse)
- Cross-library imports are allowed but should be minimized
- New shared utilities go in `common/`; domain-specific code goes in the relevant library
