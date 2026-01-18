# Orchestrator Service

<!-- Last reviewed: 2026-01-18 - Orchestrator DB pool lazy-open (open=False) -->

## Identity
- **Type:** Service
- **Port:** 8003
- **Container:** N/A

## Interface
### Public API Endpoints
| Endpoint | Method | Parameters | Returns |
|----------|--------|------------|---------|
| `/` | GET | None | Service metadata JSON |
| `/health` | GET | None | `HealthResponse` |
| `/api/v1/config` | GET | None | `ConfigResponse` |
| `/api/v1/kill-switch/engage` | POST | `KillSwitchEngageRequest` | Kill-switch status JSON |
| `/api/v1/kill-switch/disengage` | POST | `KillSwitchDisengageRequest` | Kill-switch status JSON |
| `/api/v1/kill-switch/status` | GET | None | Kill-switch status JSON |
| `/api/v1/orchestration/run` | POST | `OrchestrationRequest` | `OrchestrationResult` |
| `/api/v1/orchestration/runs` | GET | Query: limit, offset, strategy_id?, status? | `OrchestrationRunsResponse` |
| `/api/v1/orchestration/runs/{run_id}` | GET | Path `run_id` | `OrchestrationResult` |
| `/metrics` | GET | None | Prometheus metrics |

## Behavioral Contracts
### Key Functions
#### run_orchestration(request: OrchestrationRequest) -> OrchestrationResult
**Purpose:** Execute full signal-to-order workflow across services.

**Preconditions:**
- Kill-switch is available and not engaged (fail-closed if unavailable).
- Signal Service and Execution Gateway are reachable.

**Postconditions:**
- Results persisted in orchestration DB.
- Metrics recorded for run duration and outcomes.

**Behavior:**
1. Validate kill-switch availability and state.
2. Parse `as_of_date` (ISO `YYYY-MM-DD`) if provided.
3. Instantiate `TradingOrchestrator` with capital and max position size.
4. Fetch signals, map to orders, submit to Execution Gateway.
5. Persist run summary and mappings to DB.

**Raises:**
- `HTTPException 400` for invalid inputs.
- `HTTPException 503` if dependent services unavailable or kill-switch unavailable/engaged.
- `HTTPException 500` for unexpected failures.

### Invariants
- If kill-switch is unavailable, orchestration is blocked (fail-closed).
- Orchestration uses Signal Service + Execution Gateway URLs from config.

## Data Flow
```
OrchestrationRequest -> Kill-Switch Check -> Signal Service -> Execution Gateway -> DB Persist
```
- **Input format:** JSON with symbols, optional date/capital/max position size.
- **Output format:** Orchestration result with signals, orders, mappings.
- **Side effects:** Database writes, downstream service calls, metrics updates.

## Dependencies
- **Internal:** `apps/orchestrator/orchestrator.py`, `apps/orchestrator/database.py`, `apps/orchestrator/schemas.py`, `libs.redis_client`, `libs.risk_management`.
- **External:** Signal Service API, Execution Gateway API, Postgres, Redis (kill-switch), Prometheus.

### Database Pooling
- Orchestration DB pool initializes with `open=False` to avoid eager connections during startup/tests.

## Configuration
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `SIGNAL_SERVICE_URL` | No | `http://localhost:8001` | Signal Service base URL |
| `EXECUTION_GATEWAY_URL` | No | `http://localhost:8002` | Execution Gateway base URL |
| `DATABASE_URL` | Yes | `postgresql://trader:trader@localhost:5433/trader` | Postgres DB |
| `CAPITAL` | No | `100000` | Total capital |
| `MAX_POSITION_SIZE` | No | `20000` | Per-symbol cap |
| `STRATEGY_ID` | No | `alpha_baseline` | Strategy identifier |
| `DRY_RUN` | No | `true` | Propagated safety flag |
| `ALPACA_PAPER` | No | `true` | Paper flag |
| `CIRCUIT_BREAKER_ENABLED` | No | `true` | Circuit breaker flag |
| `REDIS_HOST` | No | `localhost` | Redis host |
| `REDIS_PORT` | No | `6379` | Redis port |
| `REDIS_DB` | No | `0` | Redis DB |
| `REDIS_PASSWORD` | No | `None` | Redis password |
| `LOG_LEVEL` | No | `INFO` | Log level |

## Observability (Services only)
### Health Check
- **Endpoint:** `/health`
- **Checks:** DB connectivity; dependent service health; kill-switch availability.

### Metrics
- `orchestrator_runs_total{status}`
- `orchestrator_orchestration_duration_seconds`
- `orchestrator_signals_received_total`
- `orchestrator_orders_submitted_total{status}`
- `orchestrator_positions_adjusted_total`
- `orchestrator_database_connection_status`
- `orchestrator_signal_service_available`
- `orchestrator_execution_gateway_available`

## Security
- **Auth Required:** No (no auth dependencies defined)
- **Auth Method:** N/A
- **Data Sensitivity:** Internal
- **RBAC Roles:** N/A

## Testing
- **Test Files:** `tests/apps/orchestrator/`
- **Run Tests:** `pytest tests/apps/orchestrator -v`
- **Coverage:** N/A

## Usage Examples
### Example 1: Health check
```bash
curl -s http://localhost:8003/health
```

### Example 2: Run orchestration
```bash
curl -s -X POST http://localhost:8003/api/v1/orchestration/run   -H 'Content-Type: application/json'   -d '{"symbols":["AAPL","MSFT"],"as_of_date":"2025-01-02"}'
```

## Edge Cases & Boundaries
| Scenario | Input | Expected Behavior |
|----------|-------|-------------------|
| Kill-switch engaged | `POST /orchestration/run` | 503 with safety failure. |
| Downstream unavailable | Signal/Execution API down | 503 response. |
| Invalid date | `as_of_date="bad"` | 400 validation error. |

## Known Issues & TODO
| Issue | Severity | Description | Tracking |
|-------|----------|-------------|----------|
| None | - | No known issues | - |

## Related Specs
- `signal_service.md`
- `execution_gateway.md`
- `../libs/redis_client.md`
- `../libs/risk_management.md`

## Metadata
- **Last Updated:** 2026-01-16 (Test consolidation: tests moved from apps/orchestrator/tests/ to tests/apps/orchestrator/)
- **Source Files:** `apps/orchestrator/main.py`, `apps/orchestrator/orchestrator.py`, `apps/orchestrator/database.py`, `apps/orchestrator/schemas.py`
- **ADRs:** `docs/ADRs/0006-orchestrator-architecture.md`
