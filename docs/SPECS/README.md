# SPECS README

## Overview
This directory contains technical specifications for services, libraries, strategies, and infrastructure
components in this repository. The specs are intended to provide an accurate, code-adjacent reference
for behavior, interfaces, and dependencies without requiring readers to open source files.

## Directory Structure
```
docs/SPECS/
├── README.md
├── services/         # Specs for apps/ services
├── libs/             # Specs for libs/ packages
├── strategies/       # Specs for strategies/ implementations
└── infrastructure/   # Specs for infra/ and backing services
```

## apps/ -> services/ Mapping
The codebase uses `apps/` for microservices. In this documentation, those map to `docs/SPECS/services/`
per architecture terminology ("service catalog"). Example:
- [`apps/signal_service/`](../../apps/signal_service/) -> [`docs/SPECS/services/signal_service.md`](./services/signal_service.md)

## Spec Template (Reference)
Use the template below as the baseline. Include only relevant sections for the component type and
mark non-applicable sections as "N/A" or omit them entirely.

```markdown
# [Component Name]

## Identity
- **Type:** Service | Library | Strategy | Infrastructure
- **Port:** [if Service]
- **Container:** [Docker container name, if applicable]

## Interface
### For Services: Public API Endpoints
| Endpoint | Method | Parameters | Returns |
|----------|--------|------------|---------|

### For Libraries: Public Interface (Exported Classes & Functions)
| Class/Function | Parameters | Returns | Description |
|----------------|------------|---------|-------------|

### For Strategies: Signal Generation Interface
| Function | Input | Output | Description |
|----------|-------|--------|-------------|
- **Model Type:** [LightGBM, XGBoost, etc.]
- **Feature Set:** [Alpha158, custom, etc.]
- **Retraining Frequency:** [daily, weekly, etc.]

### For Infrastructure: Service Configuration
| Setting | Value | Description |
|---------|-------|-------------|
- **Version:** [version number]
- **Persistence:** [yes/no]

## Behavioral Contracts
> **Purpose:** Enable AI coders to understand WHAT the code does without reading source.

### Key Functions (detailed behavior)
For each important public function, document:
```
### function_name(param1: Type, param2: Type) -> ReturnType
**Purpose:** [One sentence describing what this function accomplishes]

**Preconditions:**
- [What must be true before calling]
- [Required state, valid inputs]

**Postconditions:**
- [What will be true after successful execution]
- [State changes, side effects]

**Behavior:**
1. [Step-by-step description of what happens]
2. [Key decision points and branches]
3. [How edge cases are handled]

**Example:**
```python
# Concrete usage example
result = function_name(value1, value2)
assert result.status == "success"
```

**Raises:**
- `ExceptionType`: When [condition]
```

### Invariants
- [What must ALWAYS be true for this component]
- Example: "client_order_id is always unique per (symbol, side, date)"
- Example: "Circuit breaker state is checked before every order submission"

### State Machine (if stateful)
```
[Initial] --> [State1] --> [State2] --> [Final]
           |            ^
           +------------+  (on error)
```
- **States:** [List valid states]
- **Transitions:** [What triggers each transition]

## Data Flow
> How data transforms through this component

```
Input --> [Transform 1] --> [Transform 2] --> Output
             |
             v
         [Side Effect: DB write, Redis cache, etc.]
```
- **Input format:** [Describe expected input structure]
- **Output format:** [Describe output structure]
- **Side effects:** [External state changes]

## Usage Examples
> Concrete code examples for common use cases

### Example 1: [Common Use Case]
```python
# Setup
client = ComponentClient(config)

# Usage
result = client.do_something(params)

# Verification
assert result.success
```

### Example 2: [Error Handling]
```python
try:
    result = client.risky_operation()
except SpecificException as e:
    # Expected handling
    logger.error(f"Failed: {e}")
```

## Edge Cases & Boundaries
| Scenario | Input | Expected Behavior |
|----------|-------|-------------------|
| Empty input | `[]` | Returns empty result, no error |
| Max limit | `limit=10000` | Truncates to MAX_LIMIT (1000) |
| Invalid state | Called before init | Raises `NotInitializedError` |

## Dependencies
- **Internal:** libs/xxx, apps/yyy
- **External:** Redis, Postgres, Alpaca API

## Configuration
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|

## Error Handling
- [Exception types and handling patterns]

## Observability (Services only)
### Health Check
- **Endpoint:** `/health` or `/healthz`
- **Checks:** [What the health check validates]

### Metrics
| Metric Name | Type | Labels | Description |
|-------------|------|--------|-------------|

## Security
- **Auth Required:** Yes/No
- **Auth Method:** [JWT, API Key, mTLS, None]
- **Data Sensitivity:** [Public, Internal, Confidential, Restricted]
- **RBAC Roles:** [Required roles if applicable]

## Testing
- **Test Files:** `tests/apps/<name>/` or `tests/libs/<name>/`
- **Run Tests:** `pytest tests/<path> -v`
- **Coverage:** [Current coverage % if known]

## Related Specs
- [Link to related specs for navigation]
- Example: `../libs/redis_client.md`, `../services/execution_gateway.md`

## Known Issues & TODO
| Issue | Severity | Description | Tracking |
|-------|----------|-------------|----------|
| [Short ID] | Low/Medium/High | [Description of known limitation or future work] | [GitHub issue # or "Backlog"] |

## Metadata
- **Last Updated:** YYYY-MM-DD
- **Source Files:** [List of key source files]
- **ADRs:** [Related ADR numbers if any]
```

## Spec Coverage Index
### Services (apps/)
- [alert_worker](./services/alert_worker.md)
- [auth_service](./services/auth_service.md)
- [backtest_worker](./services/backtest_worker.md)
- [execution_gateway](./services/execution_gateway.md)
- [market_data_service](./services/market_data_service.md)
- [model_registry](./services/model_registry.md)
- [orchestrator](./services/orchestrator.md)
- [signal_service](./services/signal_service.md)
- [web_console_ng](./services/web_console_ng.md)

### Libraries (libs/)
- [admin](./libs/admin.md)
- [alerts](./libs/alerts.md)
- [allocation](./libs/allocation.md)
- [alpha](./libs/alpha.md)
- [analytics](./libs/analytics.md)
- [backtest](./libs/backtest.md)
- [common](./libs/common.md)
- [data_pipeline](./libs/data_pipeline.md)
- [data_providers](./libs/data_providers.md)
- [data_quality](./libs/data_quality.md)
- [duckdb_catalog](./libs/duckdb_catalog.md)
- [factors](./libs/factors.md)
- [health](./libs/health.md)
- [market_data](./libs/market_data.md)
- [models](./libs/models.md)
- [redis_client](./libs/redis_client.md)
- [risk](./libs/risk.md)
- [risk_management](./libs/risk_management.md)
- [secrets](./libs/secrets.md)
- [tax](./libs/tax.md)
- [web_console_auth](./libs/web_console_auth.md)
- [web_console_data](./libs/web_console_data.md)
- [web_console_services](./libs/web_console_services.md)

### Strategies
**Production (strategies/):**
- [alpha_baseline](./strategies/alpha_baseline.md)
- [backtest](./strategies/backtest.md)
- [ensemble](./strategies/ensemble.md)

**Experimental (research/strategies/):**
- [mean_reversion](./strategies/mean_reversion.md) 🧪
- [momentum](./strategies/momentum.md) 🧪

### Infrastructure
- [alertmanager](./infrastructure/alertmanager.md)
- [docker-compose](./infrastructure/docker-compose.md)
- [grafana](./infrastructure/grafana.md)
- [loki](./infrastructure/loki.md)
- [nginx](./infrastructure/nginx.md)
- [postgres](./infrastructure/postgres.md)
- [prometheus](./infrastructure/prometheus.md)
- [promtail](./infrastructure/promtail.md)
- [redis](./infrastructure/redis.md)

## Notes
- Specs should be updated whenever their corresponding source directories change.
- The specs are designed to be precise and technical. Avoid generic summaries.
