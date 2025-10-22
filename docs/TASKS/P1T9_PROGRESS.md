---
id: P1T9
title: "Centralized Logging"
phase: P1
task: T9
priority: P1
owner: "@development-team"
state: PROGRESS
created: 2025-10-20
dependencies: []
estimated_effort: "3-5 days"
related_adrs: []
related_docs: []
features: []
started: 2025-10-21
---

# P1T9: Centralized Logging

**Phase:** P1 (Hardening & Automation, 46-90 days)
**Status:** TASK (Not Started)
**Priority:** P1 (MVP)
**Owner:** @development-team
**Created:** 2025-10-20
**Estimated Effort:** 3-5 days

---

## Naming Convention

**This task:** `P1T9_TASK.md` → `P1T9_PROGRESS.md` → `P1T9_DONE.md`

**If this task has multiple features/sub-components:**
- Feature 0: `P1T9-F0_PROGRESS.md` (separate tracking for complex features)
- Feature 1: `P1T9-F1_PROGRESS.md`

**Where:**
- **Px** = Phase (P1 = MVP/0-45 days, P1 = Hardening/46-90 days, P2 = Advanced/91-120 days)
- **Ty** = Task number within phase (T9, T1, T2, ...)
- **Fz** = Feature/sub-component within task (F0, F1, F2, ...)

---

## Objective

Implement centralized structured logging with aggregation and correlation to enable production debugging and observability.

**Current State (P0):**
- Scattered print statements and basic logging
- No log aggregation or centralized storage
- Difficult to correlate events across services
- No retention policies

**Success looks like:**
- All services emit structured JSON logs
- Logs aggregated in Elasticsearch or Loki
- Trace IDs correlate events across services
- Retention policies enforce 30-day storage
- Query interface for debugging and analysis

---

## Acceptance Criteria

- [ ] **AC1:** All services emit structured JSON logs with consistent schema
- [ ] **AC2:** Logs include trace IDs for request correlation across services
- [ ] **AC3:** Log aggregation stack (ELK or Loki) deployed and ingesting logs
- [ ] **AC4:** Query interface supports filtering by service, level, trace ID, timestamp
- [ ] **AC5:** Retention policy auto-deletes logs older than 30 days
- [ ] **AC6:** Unit tests verify log structure and trace ID propagation

---

## Approach

### High-Level Plan

1. **Design log schema** - Define JSON structure, required fields, trace ID format
2. **Implement structured logging** - Add JSON formatter to all services
3. **Deploy log aggregation stack** - Choose and deploy ELK or Loki
4. **Add trace ID propagation** - Generate and pass trace IDs across service calls
5. **Configure retention** - Set up 30-day auto-delete policies
6. **Testing** - Verify log structure, correlation, and retention

### Logical Components

**Component 1: Structured Logging Library**
- Create shared logging configuration with JSON formatter
- Define standard log schema (timestamp, level, service, message, trace_id, context)
- Add utility functions for trace ID generation and propagation
- Add unit tests for log formatting and schema validation
- Request zen-mcp review & commit

**Component 2: Service Integration**
- Update all services to use structured logging library
- Replace print statements and basic logging
- Add trace ID middleware for FastAPI endpoints
- Propagate trace IDs in inter-service HTTP calls (via headers)
- Add integration tests for trace ID correlation
- Request zen-mcp review & commit

**Component 3: Log Aggregation Stack**
- Choose stack (Loki + Grafana recommended for simplicity)
- Add docker-compose configuration for log stack
- Configure log shipping (Promtail or Filebeat)
- Set up retention policies (30 days)
- Document query examples in runbook
- Request zen-mcp review & commit

**Component 4: Query Interface & Documentation**
- Create example queries for common debugging scenarios
- Add Grafana Explore dashboards for log browsing
- Update runbooks with troubleshooting workflows
- Add E2E test verifying logs appear in aggregation stack
- Request zen-mcp review & commit

---

## Technical Details

### Files to Modify/Create
- `libs/common/logging/` - NEW: Shared structured logging library
  - `formatter.py` - JSON log formatter with standard schema
  - `context.py` - Trace ID generation and context propagation
  - `config.py` - Logging configuration for all services
- `apps/signal_service/main.py` - Update to use structured logging
- `apps/execution_gateway/main.py` - Update to use structured logging
- `apps/orchestrator/main.py` - Update to use structured logging
- `infra/docker-compose.logging.yml` - NEW: Log aggregation stack
  - Loki for log storage
  - Promtail for log shipping
  - Grafana for querying (reuse existing)
- `tests/libs/common/test_logging.py` - NEW: Logging library tests
- `tests/integration/test_trace_correlation.py` - NEW: Trace ID propagation tests

### APIs/Contracts
- No API changes required
- HTTP headers: Add `X-Trace-ID` header for request correlation
- Log schema (JSON):
  ```json
  {
    "timestamp": "2025-10-21T10:30:00.000Z",
    "level": "INFO",
    "service": "signal_service",
    "trace_id": "abc123-def456",
    "message": "Generated signals for 10 symbols",
    "context": {
      "strategy": "alpha_baseline",
      "symbol_count": 10
    }
  }
  ```

### Database Changes
- No database changes required

---

## Dependencies

**Blockers (must complete before starting):**
- None (can start independently)

**Nice-to-have (can start without):**
- P1T8: Monitoring & Alerting - Would enable alerting on log error rates

**Blocks (other tasks waiting on this):**
- None (improves observability but not blocking other tasks)

---

## Risks & Mitigations

| Risk | Impact | Probability | Mitigation |
|------|--------|-------------|------------|
| High log volume overwhelms storage | Medium | Medium | Configure sampling for high-frequency logs, retention policies, volume monitoring |
| Trace ID propagation breaks across services | Medium | Medium | Comprehensive integration tests, fallback to generating new trace ID if missing |
| Log stack (ELK/Loki) adds operational complexity | Medium | Low | Start with Loki (simpler than ELK), document runbooks, add health checks |
| Performance impact from JSON serialization | Low | Low | Async logging, measure overhead, disable verbose logging in production if needed |

---

## Testing Strategy

### Test Coverage Needed
- **Unit tests:**
  - JSON log formatter produces valid JSON with required fields
  - Trace ID generation creates unique IDs
  - Log schema validation catches missing fields
- **Integration tests:**
  - Trace ID propagates across HTTP service calls
  - Logs appear in aggregation stack within 10 seconds
  - Query interface filters by service, level, trace ID
- **E2E tests:**
  - Full request flow (orchestrator → signal → execution) uses same trace ID
  - Retention policy deletes logs older than 30 days

### Manual Testing
- [ ] Generate logs from each service and verify they appear in Grafana Explore
- [ ] Search by trace ID and verify all service logs for a request are correlated
- [ ] Verify log retention policy deletes old logs after 30 days
- [ ] Check performance impact (latency and CPU) of JSON logging vs basic logging

---

## Documentation Requirements

### Must Create/Update
- [ ] ADR for centralized logging architecture (ELK vs Loki decision, log schema design)
- [ ] Runbook in `/docs/RUNBOOKS/` for log querying and troubleshooting
- [ ] Update `/docs/GETTING_STARTED/SETUP.md` with log stack setup instructions

### Must Update
- [ ] `/docs/GETTING_STARTED/REPO_MAP.md` for new `libs/common/logging/` structure
- [ ] `/docs/GETTING_STARTED/PROJECT_STATUS.md` when complete
- [ ] `infra/README.md` for new logging services

---

## Related

**ADRs:**
- ADR (to be created): Centralized Logging Architecture (ELK vs Loki decision)

**Documentation:**
- [P1_PLANNING.md](./P1_PLANNING.md#t9-centralized-logging) - Source planning document

**Tasks:**
- Nice-to-have: [P1T8_DONE.md](./P1T8_DONE.md) - Monitoring & Alerting (enables log-based alerts)

---

## Notes

**Stack Recommendation:** Use **Loki + Grafana** instead of ELK for simplicity:
- Loki: Log aggregation and storage (simpler than Elasticsearch)
- Promtail: Log shipping agent (lightweight)
- Grafana: Already deployed for metrics, can query logs via Explore

**Why not ELK:**
- Elasticsearch is heavyweight (high memory requirements)
- Logstash adds complexity vs Promtail
- Kibana would be redundant with Grafana

**Log Schema Fields:**
- `timestamp` (ISO 8601, UTC)
- `level` (DEBUG/INFO/WARNING/ERROR/CRITICAL)
- `service` (signal_service, execution_gateway, orchestrator)
- `trace_id` (UUID v4, propagated via X-Trace-ID header)
- `message` (human-readable description)
- `context` (dict of request-specific data: symbol, strategy, order_id, etc.)

**Reference:** See [P1_PLANNING.md](./P1_PLANNING.md#t9-centralized-logging) for original requirements.

---

## State Transition Instructions

**When starting this task:**

```bash
# 1. Rename file
git mv docs/TASKS/P1T9_TASK.md docs/TASKS/P1T9_PROGRESS.md

# 2. Update front matter in P1T9_PROGRESS.md:
#    state: PROGRESS
#    started: 2025-10-20

# 3. Commit
git add docs/TASKS/P1T9_PROGRESS.md
git commit -m "Start P1T9: Task Title"
```

**Or use automation:**
```bash
./scripts/tasks.py start P1T9
```
