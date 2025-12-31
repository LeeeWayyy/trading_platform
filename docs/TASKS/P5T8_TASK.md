---
id: P5T8
title: "NiceGUI Migration - Documentation & Knowledge Base"
phase: P5
task: T8
priority: P1
owner: "@development-team"
state: PLANNING
created: 2025-12-31
dependencies: [P5T1, P5T2, P5T3, P5T4, P5T5, P5T6, P5T7]
estimated_effort: "5-7 days"
related_adrs: [ADR-0031-nicegui-migration]
related_docs: [P5_PLANNING.md, P5T1_TASK.md, P5T2_TASK.md, P5T3_TASK.md, P5T4_TASK.md, P5T5_TASK.md, P5T6_TASK.md, P5T7_TASK.md]
features: [T8.1, T8.2, T8.3, T8.4]
---

# P5T8: NiceGUI Migration - Documentation & Knowledge Base

**Phase:** P5 (Web Console Modernization)
**Status:** PLANNING
**Priority:** P1 (Project Completion)
**Owner:** @development-team
**Created:** 2025-12-31
**Estimated Effort:** 5-7 days
**Track:** Phase 8 from P5_PLANNING.md
**Dependency:** All implementation tasks (P5T1-P5T7) should be substantially complete

---

## Objective

Create comprehensive documentation for the NiceGUI migration, ensuring maintainability and knowledge transfer.

**Success looks like:**
- ADR-0031 documenting migration decision rationale
- Concept documentation covering architecture, auth, real-time, and component patterns
- Operational runbooks for deployment, troubleshooting, performance, and rollback
- Updated project documentation (REPO_MAP, PROJECT_STATUS, INDEX.md)
- AI agent guidance updated for NiceGUI patterns
- Streamlit-specific documentation archived/deprecated

**Documentation Standards:**
- Follow `docs/STANDARDS/DOCUMENTATION_STANDARDS.md`
- Include diagrams where architecture is non-obvious
- Code examples must be copy-paste runnable
- All runbooks must have verification steps
- Cross-reference related documents per matrix below

**Cross-Reference Matrix (required links):**

| Document | Must Link To |
|----------|--------------|
| ADR-0031 | P5_PLANNING.md, All concept docs, Rollback runbook |
| nicegui-architecture.md | ADR-0031, nicegui-auth.md, nicegui-realtime.md |
| nicegui-auth.md | ADR-0031, nicegui-architecture.md, P5T2_TASK.md |
| nicegui-realtime.md | ADR-0031, nicegui-architecture.md, nicegui-components.md |
| nicegui-components.md | ADR-0031, nicegui-architecture.md, P5T4-P5T7 tasks |
| nicegui-deployment.md | ADR-0031, nicegui-troubleshooting.md, nicegui-rollback.md |
| nicegui-troubleshooting.md | nicegui-deployment.md, nicegui-performance.md |
| nicegui-performance.md | nicegui-deployment.md, nicegui-troubleshooting.md |
| nicegui-rollback.md | ADR-0031, nicegui-deployment.md, nicegui-troubleshooting.md |
| REPO_MAP.md | nicegui-architecture.md |
| CLAUDE.md | All concept docs |

---

## Acceptance Criteria

### T8.1 Architecture Decision Record (ADR) (2 days)

**Deliverable:** `docs/ADRs/ADR-0031-nicegui-migration.md`

**PR:** `docs(P5): ADR-0031 NiceGUI migration`

**Required Sections:**

1. **Title and Metadata**
   - ADR number: 0031
   - Status: Accepted
   - Date: Implementation completion date
   - Decision makers: Development team

2. **Context**
   - [ ] Streamlit execution model limitations (script-rerun, UI flicker)
   - [ ] Synchronous request blocking
   - [ ] `st.session_state` coupling issues
   - [ ] `st.stop()` non-standard flow control
   - [ ] Static data tables limitations
   - [ ] Polling inefficiency (`streamlit_autorefresh`)
   - [ ] Limited layout control

3. **Decision**
   - [ ] Migrate to NiceGUI framework
   - [ ] Event-driven AsyncIO architecture
   - [ ] FastAPI middleware for auth
   - [ ] AG Grid for interactive tables
   - [ ] WebSocket push for real-time updates

4. **Alternatives Considered**
   - [ ] React/Next.js - Rejected (separate frontend repo, skill gap)
   - [ ] Vue.js - Rejected (same reasons as React)
   - [ ] Dash/Plotly - Rejected (callback complexity, limited async)
   - [ ] Panel/Holoviz - Rejected (less mature, smaller community)
   - [ ] Streamlit improvements - Rejected (fundamental model limitations)

5. **Consequences**
   - [ ] Positive: Real-time updates, async operations, responsive UI
   - [ ] Positive: FastAPI integration, same backend patterns
   - [ ] Negative: Learning curve for team
   - [ ] Negative: Migration effort (~70-96 days)
   - [ ] Trade-off: NiceGUI less popular than React ecosystem

6. **Security Considerations**
   - [ ] Session architecture changes
   - [ ] Auth flow migration details
   - [ ] CSRF protection approach
   - [ ] Cookie security flags

7. **Performance Requirements**
   - [ ] Target latencies (50-100ms vs 500-2000ms)
   - [ ] Validation approach
   - [ ] Benchmark results (if available)

8. **Rollback Plan**
   - [ ] Parallel run architecture during migration
   - [ ] Rollback triggers (error rate, latency thresholds)
   - [ ] Rollback procedure (nginx route switch)

9. **Implementation Notes** (required per ADR_GUIDE.md)
   - [ ] Migration path (phased approach, parallel run)
   - [ ] Testing approach (unit, integration, E2E, load)
   - [ ] Timeline and milestones
   - [ ] Team assignments

10. **Risks** (under Consequences)
    - [ ] Risk: Team unfamiliarity with NiceGUI
    - [ ] Risk: WebSocket scalability under load
    - [ ] Risk: Session migration complexity
    - [ ] Mitigations for each risk

**Implementation Template:**
```markdown
# ADR-0031: NiceGUI Migration for Web Console

## Status
Accepted

## Date
YYYY-MM-DD

## Context

### Problem Statement
The trading platform web console uses Streamlit, which has fundamental
architectural limitations for real-time trading applications:

1. **Execution Model**: Every user interaction triggers a full script re-run,
   causing 500-2000ms response times and UI flicker.

2. **Synchronous Blocking**: API calls block the UI for all users on the
   same server process.

3. **State Coupling**: `st.session_state` creates tight coupling between
   auth, UI, and business logic.

[Continue with full ADR content...]

## Decision

We will migrate the web console from Streamlit to NiceGUI.

### Key Changes
| Component | Streamlit | NiceGUI |
|-----------|-----------|---------|
| Execution | Script re-run | Event-driven |
| HTTP | Sync `requests` | Async `httpx` |
| State | `st.session_state` | `app.storage` |
| Tables | `st.dataframe` | AG Grid |
| Real-time | Polling | WebSocket push |
| Auth | Custom helpers | FastAPI middleware |

## Alternatives Considered

### React/Next.js
**Rejected** - Would require separate frontend repository, TypeScript
expertise not present on team, and doubled deployment complexity.

[Continue with other alternatives...]

## Consequences

### Positive
- Response times reduced from 500-2000ms to 50-100ms
- Real-time updates via WebSocket (no polling)
- Native AsyncIO prevents UI blocking
- FastAPI integration matches backend patterns

### Negative
- 70-96 day migration effort
- Team learning curve for NiceGUI patterns
- Smaller community than React ecosystem

### Trade-offs
- NiceGUI is less popular but better fits our Python-centric team

### Risks
| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Team unfamiliarity with NiceGUI | Medium | Medium | Training sessions, pair programming |
| WebSocket scalability | Low | High | Load testing, Redis Pub/Sub fan-out |
| Session migration complexity | Medium | Medium | Parallel run, gradual cutover |
| Breaking changes in NiceGUI | Low | Medium | Pin versions, test before upgrade |

## Security Considerations

### Session Architecture
- Sessions stored in Redis with `ng_session:` prefix
- Cookie flags: HttpOnly, Secure, SameSite=Strict
- 8-hour sliding expiration with 24-hour absolute maximum

### Auth Flows
- JWT validation via FastAPI middleware
- OAuth callbacks on separate `/ng/auth/callback` path
- CSRF protection via double-submit cookie pattern

## Performance Requirements

| Metric | Streamlit | NiceGUI Target |
|--------|-----------|----------------|
| Button click response | 500-2000ms | <100ms |
| Data refresh | 2-5s polling | <500ms push |
| Initial page load | 3-5s | <2s |

## Rollback Plan

### Triggers
- Error rate >5% for 15 minutes
- P99 latency >2s for 15 minutes
- Critical security vulnerability

### Procedure
1. Update nginx to route all traffic to Streamlit
2. Notify users via status page
3. Investigate and fix NiceGUI issues
4. Gradual re-rollout after fixes verified

## Implementation Notes

### Migration Path
1. Phase 1-2: Foundation and auth (parallel run begins)
2. Phase 3-5: Dashboard, controls, charts
3. Phase 6: Remaining pages
4. Phase 7: Infrastructure and cutover
5. Phase 8: Documentation

### Testing Approach
- Unit tests: Component logic, service mocks
- Integration tests: Auth flows, API calls
- E2E tests: Playwright browser automation
- Load tests: Locust for WebSocket scalability

### Timeline
See P5_PLANNING.md for detailed timeline (70-96 days total)

### Team Assignments
- Lead developer: Architecture, auth, core components
- Developer 2: Dashboard, charts, pages
- DevOps: Infrastructure, deployment, monitoring

## References
- NiceGUI Documentation: https://nicegui.io/documentation
- P5_PLANNING.md - Migration planning document
- P5T1-P5T7 - Implementation task documents
```

**Testing:**
- [ ] ADR follows repository ADR format
- [ ] All required sections present
- [ ] Decision rationale is clear and justified
- [ ] Security considerations are comprehensive
- [ ] Rollback plan is actionable

---

### T8.2 Concept Documentation (2-3 days)

**PR:** `docs(P5): NiceGUI concepts and patterns`

**Deliverables:**

#### T8.2a NiceGUI Architecture (`docs/CONCEPTS/nicegui-architecture.md`)

**Content:**
- [ ] Event-driven execution model explanation
- [ ] AsyncIO patterns and best practices
- [ ] Comparison diagram: Streamlit vs NiceGUI execution flow
- [ ] Component lifecycle and state management
- [ ] `@ui.refreshable` pattern for reactive updates
- [ ] `ui.timer` vs polling patterns
- [ ] Service integration (reusing existing services)
- [ ] Error handling patterns

**Example Sections:**
```markdown
# NiceGUI Architecture

## Execution Model

### Streamlit (Script Re-run)
```
User clicks button
    ↓
Re-run entire script (500-2000ms)
    ↓
Rebuild all widgets
    ↓
Re-fetch all data
    ↓
Re-render page
```

### NiceGUI (Event-Driven)
```
User clicks button
    ↓
Event handler fires
    ↓
Update specific DOM element (50-100ms)
    ↓
Async API call (non-blocking)
    ↓
Patch UI element
```

## State Management

### Pattern: `@ui.refreshable`
Use `@ui.refreshable` for sections that need to update independently:

```python
@ui.refreshable
def position_table() -> None:
    """Refreshable section - call .refresh() to update."""
    positions = app.storage.user.get("positions", [])
    if not positions:
        ui.label("No positions")
        return

    ui.table(columns=[...], rows=positions)

# To update:
position_table.refresh()
```

### Pattern: Avoid recreating components
BAD - Recreates select on every change:
```python
@ui.refreshable
def selection_section() -> None:
    ui.select(options, on_change=on_change)  # Recreated!
```

GOOD - Separate data refresh from component creation:
```python
select = ui.select(options)

@ui.refreshable
def results_section() -> None:
    # Only refresh results, not the select
    render_results(selected_value)
```

[Continue with more patterns...]
```

#### T8.2b NiceGUI Auth (`docs/CONCEPTS/nicegui-auth.md`)

**Content:**
- [ ] Session store architecture (Redis-backed)
- [ ] Auth middleware implementation (`@requires_auth`)
- [ ] JWT validation flow
- [ ] OAuth callback handling
- [ ] CSRF protection (double-submit cookie)
- [ ] Cookie security flags
- [ ] Permission checks (RBAC patterns)
- [ ] Session expiration and refresh
- [ ] Parallel run session isolation

**Example Sections:**
```markdown
# NiceGUI Authentication

## Session Architecture

```
┌─────────────────┐     ┌─────────────────┐
│   User Browser  │────▶│  NiceGUI App    │
│   (Cookie)      │     │  (FastAPI)      │
└─────────────────┘     └────────┬────────┘
                                 │
                                 ▼
                        ┌─────────────────┐
                        │  Redis Cluster  │
                        │  ng_session:*   │
                        └─────────────────┘
```

## Auth Middleware

```python
from functools import wraps
from nicegui import app
from apps.web_console_ng.auth import get_current_user

def requires_auth(func):
    """Decorator ensuring user is authenticated."""
    @wraps(func)
    async def wrapper(*args, **kwargs):
        user = get_current_user()
        if not user:
            ui.notify("Please log in", type="warning")
            ui.navigate.to("/login")
            return
        return await func(*args, **kwargs)
    return wrapper

@ui.page("/dashboard")
@requires_auth
async def dashboard():
    user = get_current_user()
    ui.label(f"Welcome, {user['username']}")
```

## Permission Checks

```python
from apps.web_console_ng.auth.permissions import Permission, has_permission

if has_permission(user, Permission.TRIP_CIRCUIT):
    ui.button("Trip Circuit", on_click=handle_trip)
else:
    ui.label("TRIP_CIRCUIT permission required").classes("text-gray-500")
```

[Continue with more patterns...]
```

#### T8.2c NiceGUI Real-time (`docs/CONCEPTS/nicegui-realtime.md`)

**Content:**
- [ ] WebSocket push architecture
- [ ] `ui.timer` for periodic updates
- [ ] Redis Pub/Sub for cross-instance updates
- [ ] Progressive polling patterns (2s -> 5s -> 10s -> 30s)
- [ ] Connection recovery and state rehydration
- [ ] Real-time vs polling trade-offs
- [ ] Example: Position updates via WebSocket

**Example Sections:**
```markdown
# NiceGUI Real-time Updates

## Push vs Polling

| Approach | Use Case | Latency | Complexity |
|----------|----------|---------|------------|
| `ui.timer` polling | General updates | 100ms-30s | Low |
| WebSocket push | Critical data | <100ms | Medium |
| Redis Pub/Sub | Multi-instance | <500ms | High |

## Timer-Based Updates

```python
@ui.refreshable
def status_section() -> None:
    status = fetch_status()
    render_status(status)

# Auto-refresh every 5 seconds
ui.timer(5.0, status_section.refresh)
```

## Progressive Polling

For long-running jobs, use progressive backoff:

```python
POLL_INTERVALS = {
    30: 2.0,    # < 30s: poll every 2s
    60: 5.0,    # < 60s: poll every 5s
    300: 10.0,  # < 5min: poll every 10s
    None: 30.0, # > 5min: poll every 30s
}

poll_elapsed = 0.0

def get_interval() -> float:
    for threshold, interval in sorted(POLL_INTERVALS.items(), key=lambda x: (x[0] or float("inf"))):
        if threshold is None or poll_elapsed < threshold:
            return interval
    return 30.0

async def poll() -> None:
    global poll_elapsed
    await fetch_data()
    poll_elapsed += get_interval()
    timer.interval = get_interval()  # Dynamic interval update

timer = ui.timer(get_interval(), poll)
```

[Continue with WebSocket patterns...]
```

#### T8.2d NiceGUI Components (`docs/CONCEPTS/nicegui-components.md`)

**Content:**
- [ ] Component structure and organization
- [ ] AG Grid usage for interactive tables
- [ ] Form patterns (validation, submission)
- [ ] Dialog and confirmation patterns
- [ ] Tab and expansion patterns
- [ ] Chart integration (`ui.plotly`)
- [ ] Download functionality
- [ ] Component naming conventions (`_ng` suffix)
- [ ] Service dependency injection

**Example Sections:**
```markdown
# NiceGUI Component Patterns

## Component Organization

```
apps/web_console_ng/components/
├── backtest_form.py          # Form component
├── backtest_results.py       # Results display
├── position_grid.py          # AG Grid wrapper
├── metric_card.py            # Reusable card
└── confirmation_dialog.py    # Dialog pattern
```

## Naming Convention

Ported components use `_ng` suffix to avoid import conflicts:

```python
# Original Streamlit
from apps.web_console.components.backtest_form import render_backtest_form

# NiceGUI port
from apps.web_console_ng.components.backtest_form import render_backtest_form_ng
```

## AG Grid Tables

```python
from nicegui import ui

def render_position_grid(positions: list[dict]) -> None:
    columns = [
        {"field": "symbol", "headerName": "Symbol", "sortable": True},
        {"field": "quantity", "headerName": "Qty", "type": "numericColumn"},
        {"field": "market_value", "headerName": "Value", "type": "numericColumn"},
        {"field": "pnl", "headerName": "P&L", "cellClass": "pnl-cell"},
    ]

    ui.aggrid({
        "columnDefs": columns,
        "rowData": positions,
        "defaultColDef": {"resizable": True, "filter": True},
    }).classes("w-full h-96")
```

## Form Patterns

```python
async def render_order_form() -> None:
    with ui.card().classes("p-4"):
        symbol = ui.input("Symbol", validation={"required": True})
        quantity = ui.number("Quantity", min=1, max=10000)
        side = ui.select(["buy", "sell"], value="buy", label="Side")

        async def submit():
            if not symbol.value:
                ui.notify("Symbol required", type="warning")
                return
            # Show confirmation dialog
            with ui.dialog() as dialog, ui.card():
                ui.label(f"Confirm {side.value} {quantity.value} {symbol.value}?")
                with ui.row():
                    ui.button("Cancel", on_click=dialog.close)
                    ui.button("Confirm", on_click=lambda: execute_order(dialog))
            dialog.open()

        ui.button("Submit Order", on_click=submit)
```

[Continue with more patterns...]
```

**Testing:**
- [ ] All 4 concept documents created
- [ ] Architecture diagrams included
- [ ] Code examples are runnable
- [ ] Cross-references to related docs
- [ ] Follows documentation standards

---

### T8.3 Operational Runbooks (1-2 days)

**PR:** `docs(P5): NiceGUI operational runbooks`

**Deliverables:**

#### T8.3a Deployment Runbook (`docs/RUNBOOKS/nicegui-deployment.md`)

**Content:**
- [ ] Prerequisites (Redis, PostgreSQL, environment vars)
- [ ] Docker build procedure
- [ ] Kubernetes/compose deployment steps
- [ ] Health check verification
- [ ] nginx configuration for routing
- [ ] Environment variable reference
- [ ] Scaling procedures
- [ ] Zero-downtime deployment

**Example Sections:**
```markdown
# NiceGUI Deployment Runbook

## Prerequisites

- Redis cluster running (Sentinel recommended for HA)
- PostgreSQL accessible
- Environment variables configured
- Docker registry access

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `REDIS_URL` | Yes | - | Redis connection URL |
| `DATABASE_URL` | Yes | - | PostgreSQL connection |
| `SECRET_KEY` | Yes | - | Session encryption key |
| `NICEGUI_PORT` | No | 8080 | Server port |
| `LOG_LEVEL` | No | INFO | Logging level |

## Deployment Steps

### 1. Build Docker Image

```bash
docker build -t trading-platform/web-console-ng:latest \
  -f apps/web_console_ng/Dockerfile .
```

### 2. Push to Registry

```bash
docker push trading-platform/web-console-ng:latest
```

### 3. Deploy (Kubernetes)

```bash
kubectl apply -f infra/k8s/web-console-ng.yaml
kubectl rollout status deployment/web-console-ng
```

### 4. Verify Health

```bash
# Check health endpoint
curl -f http://web-console-ng:8080/health

# Expected response:
# {"status": "healthy", "version": "1.0.0"}
```

### 5. Update nginx Routing

```nginx
# Enable NiceGUI traffic
location / {
    proxy_pass http://web-console-ng:8080;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
}

# Legacy Streamlit (remove after cutover)
location /legacy {
    proxy_pass http://web-console:8501;
}
```

## Verification Checklist

- [ ] Health endpoint returns 200
- [ ] Login flow works
- [ ] Dashboard loads positions
- [ ] Real-time updates working
- [ ] No error spikes in logs
```

#### T8.3b Troubleshooting Runbook (`docs/RUNBOOKS/nicegui-troubleshooting.md`)

**Content:**
- [ ] Common errors and solutions
- [ ] Debug logging configuration
- [ ] WebSocket debugging
- [ ] Session issues diagnosis
- [ ] Performance profiling
- [ ] Log analysis commands
- [ ] Database connectivity issues
- [ ] Redis connectivity issues

**Example Sections:**
```markdown
# NiceGUI Troubleshooting Runbook

## Common Issues

### Issue: WebSocket Connection Fails

**Symptoms:**
- Real-time updates stop
- Browser console shows WebSocket errors
- Pages load but don't update

**Diagnosis:**
```bash
# Check WebSocket endpoint
curl -i -N -H "Connection: Upgrade" \
  -H "Upgrade: websocket" \
  http://web-console-ng:8080/_nicegui_ws

# Check nginx WebSocket config
nginx -T | grep -A5 "Upgrade"
```

**Solution:**
Ensure nginx passes WebSocket headers:
```nginx
proxy_http_version 1.1;
proxy_set_header Upgrade $http_upgrade;
proxy_set_header Connection "upgrade";
```

### Issue: Session Lost After Restart

**Symptoms:**
- Users logged out after deployment
- "Session expired" errors spike

**Diagnosis:**
```bash
# Check Redis connectivity
redis-cli -u $REDIS_URL ping

# Check session keys
redis-cli -u $REDIS_URL keys "ng_session:*" | wc -l
```

**Solution:**
- Verify Redis cluster is healthy
- Check `SECRET_KEY` consistency across replicas
- Ensure sticky sessions for multi-instance

[Continue with more issues...]
```

#### T8.3c Performance Runbook (`docs/RUNBOOKS/nicegui-performance.md`)

**Content:**
- [ ] Key metrics to monitor
- [ ] Grafana dashboard setup
- [ ] Performance targets (SLOs)
- [ ] Bottleneck identification
- [ ] Optimization techniques
- [ ] Load testing procedures
- [ ] Resource sizing guidelines

**Example Sections:**
```markdown
# NiceGUI Performance Runbook

## Key Metrics

| Metric | Target | Alert Threshold |
|--------|--------|-----------------|
| Request latency P50 | <100ms | >200ms |
| Request latency P99 | <500ms | >1000ms |
| WebSocket message latency | <50ms | >200ms |
| Error rate | <0.1% | >1% |
| Memory per instance | <512MB | >768MB |
| CPU per instance | <50% avg | >80% |

## Prerequisites

Before running performance procedures, ensure access to monitoring services:

```bash
# Set up port-forwarding (if outside cluster)
kubectl port-forward svc/grafana 3000:3000 &
kubectl port-forward svc/prometheus 9090:9090 &

# Or set environment variables for direct access
export GRAFANA_URL="${GRAFANA_URL:-http://localhost:3000}"
export PROM_URL="${PROM_URL:-http://localhost:9090}"
export GRAFANA_API_KEY="${GRAFANA_API_KEY}"  # Required for API operations
```

## Grafana Dashboard Setup

### Step 1: Import Dashboard

**Option A: Via UI (recommended)**
1. Open Grafana UI at `$GRAFANA_URL`
2. Navigate to Dashboards > Import
3. Upload `infra/grafana/dashboards/nicegui.json`
4. Click "Import"

**Option B: Via API (requires API key)**
```bash
# Create API key in Grafana UI: Configuration > API Keys > Add
curl -X POST "$GRAFANA_URL/api/dashboards/db" \
  -H "Authorization: Bearer $GRAFANA_API_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"dashboard\": $(cat infra/grafana/dashboards/nicegui.json), \"overwrite\": true}"
```

### Step 2: Verify Metrics Flow
```bash
# Check Prometheus targets
curl -s "$PROM_URL/api/v1/targets" | jq '.data.activeTargets[] | select(.labels.job=="nicegui")'

# Verify metrics are scraped
curl -s "$PROM_URL/api/v1/query?query=nicegui_request_latency_seconds_count"
```

### Step 3: Configure Alert Rules
```bash
# Apply alerting rules
kubectl apply -f infra/prometheus/rules/nicegui-alerts.yaml

# Verify rules loaded
curl -s "$PROM_URL/api/v1/rules" | jq '.data.groups[] | select(.name=="nicegui")'
```

## SLO Validation

### Procedure: Validate SLOs
1. **Query current performance:**
   ```bash
   # P50 latency over last hour
   curl -s "$PROM_URL/api/v1/query" \
     --data-urlencode 'query=histogram_quantile(0.5, rate(nicegui_request_latency_seconds_bucket[1h]))' \
     | jq '.data.result[0].value[1]'

   # P99 latency over last hour
   curl -s "$PROM_URL/api/v1/query" \
     --data-urlencode 'query=histogram_quantile(0.99, rate(nicegui_request_latency_seconds_bucket[1h]))' \
     | jq '.data.result[0].value[1]'
   ```

2. **Verify against targets:**
   - P50 should be <0.1 (100ms)
   - P99 should be <0.5 (500ms)

3. **Check error rate:**
   ```bash
   curl -s "$PROM_URL/api/v1/query" \
     --data-urlencode 'query=sum(rate(nicegui_request_errors_total[1h])) / sum(rate(nicegui_request_total[1h]))' \
     | jq '.data.result[0].value[1]'
   # Should be <0.001 (0.1%)
   ```

## Load Testing

### Procedure: Run Load Test
1. **Prerequisites:**
   ```bash
   # Activate virtual environment (REQUIRED per repo policy)
   source .venv/bin/activate

   # Install locust in venv
   python3 -m pip install locust
   ```

2. **Run baseline test:**
   ```bash
   locust -f tests/load/locustfile.py \
     --host http://web-console-ng:8080 \
     --users 100 \
     --spawn-rate 10 \
     --run-time 10m \
     --headless \
     --csv=load_test_results
   ```

3. **Verify results:**
   ```bash
   # Check P99 latency
   tail -1 load_test_results_stats.csv | cut -d',' -f10
   # Should be <500ms

   # Check failure rate
   tail -1 load_test_results_stats.csv | cut -d',' -f6
   # Should be <1%
   ```

4. **WebSocket load test:**
   ```bash
   locust -f tests/load/ws_locustfile.py \
     --host ws://web-console-ng:8080 \
     --users 500 \
     --spawn-rate 50 \
     --run-time 5m \
     --headless
   ```

## Bottleneck Identification

### Step 1: Check resource utilization
```bash
kubectl top pods -l app=web-console-ng
# Look for pods >80% CPU or >768MB memory
```

### Step 2: Profile slow requests
```bash
# Enable debug logging temporarily
kubectl set env deployment/web-console-ng LOG_LEVEL=DEBUG

# Check logs for slow requests
kubectl logs -l app=web-console-ng --since=5m | grep "slow_request"
```

### Step 3: Database query analysis
```bash
# Check for slow queries
psql $DATABASE_URL -c "SELECT query, mean_time, calls FROM pg_stat_statements ORDER BY mean_time DESC LIMIT 10;"
```

## Resource Sizing Guidelines

| Users | Replicas | CPU Request | Memory Request |
|-------|----------|-------------|----------------|
| <100 | 2 | 250m | 256Mi |
| 100-500 | 3 | 500m | 512Mi |
| 500-1000 | 5 | 1000m | 1Gi |
| >1000 | HPA | 1000m | 1Gi |
```

#### T8.3d Rollback Runbook (`docs/RUNBOOKS/nicegui-rollback.md`)

**Content:**
- [ ] Rollback triggers (error rate, latency)
- [ ] Step-by-step rollback procedure
- [ ] nginx route switching
- [ ] Verification after rollback
- [ ] Post-rollback investigation
- [ ] Re-rollout procedure

**Example Sections:**
```markdown
# NiceGUI Rollback Runbook

## Rollback Triggers

Initiate rollback if ANY of these conditions persist for 15+ minutes:

| Metric | Threshold |
|--------|-----------|
| Error rate | >5% |
| P99 latency | >2000ms |
| WebSocket failures | >10% |
| Login failures | >5% |

## Rollback Procedure

### 1. Switch nginx to Streamlit (Immediate)

```bash
# Update nginx config
cat > /etc/nginx/conf.d/web-console.conf << 'EOF'
# Rollback: Route all traffic to Streamlit
location / {
    proxy_pass http://web-console:8501;
}
EOF

# Reload nginx
nginx -s reload
```

### 2. Verify Streamlit is Healthy

```bash
curl -f http://web-console:8501/healthz
# Expected: 200 OK
```

### 3. Notify Users

Update status page:
```
Web Console: Degraded - Rolled back to previous version
ETA for resolution: TBD
```

### 4. Preserve Evidence

```bash
# Capture NiceGUI logs
kubectl logs deployment/web-console-ng --since=1h > nicegui_rollback_$(date +%s).log

# Capture metrics snapshot
curl "$PROM_URL/api/v1/query_range" \
  --data-urlencode 'query=nicegui_request_latency_seconds' \
  --data-urlencode "start=$(date -d '1 hour ago' +%s)" \
  --data-urlencode "end=$(date +%s)" \
  --data-urlencode 'step=60s' > metrics_snapshot.json
```

### 5. Investigation

After rollback is stable:
1. Analyze logs for error patterns
2. Review metrics around trigger time
3. Check for recent deployments/changes
4. Create incident report

## Re-rollout Procedure

After fixing issues:

1. Deploy fix to staging
2. Run load test (minimum 30 minutes)
3. Verify metrics are healthy
4. Gradual rollout (10% -> 50% -> 100%)
5. Monitor for 24 hours before declaring stable
```

**Testing:**
- [ ] All 4 runbooks created
- [ ] Procedures are step-by-step
- [ ] Commands are copy-paste runnable
- [ ] Verification steps included
- [ ] Escalation paths defined

---

### T8.4 Migration Guide Updates (1 day)

**PR:** `docs(P5): Update getting started and repo map`

**Deliverables:**

#### T8.4a Update REPO_MAP

**File:** `docs/GETTING_STARTED/REPO_MAP.md`

**Changes:**
- [ ] Add `apps/web_console_ng/` section with directory structure
- [ ] Add NiceGUI-specific files to inventory
- [ ] Update file counts

#### T8.4b Update PROJECT_STATUS

**File:** `docs/GETTING_STARTED/PROJECT_STATUS.md`

**Changes:**
- [ ] Mark P5 as complete
- [ ] Add P5 task completion dates
- [ ] Update web console technology stack

#### T8.4c Update INDEX.md

**File:** `docs/INDEX.md`

**Changes:**
- [ ] Add links to new concept documents
- [ ] Add links to new runbooks
- [ ] Add link to ADR-0031

#### T8.4d Update CLAUDE.md

**File:** `CLAUDE.md`

**Changes:**
- [ ] Add NiceGUI patterns section
- [ ] Update web console guidance for AI agents
- [ ] Add common NiceGUI code patterns
- [ ] Reference new concept documents

#### T8.4e Archive Streamlit Documentation

**Actions:**
- [ ] Move Streamlit-specific docs to `docs/ARCHIVE/streamlit/`
- [ ] Add deprecation notice to moved docs
- [ ] Update any cross-references

**Streamlit Documentation Inventory (to archive):**

| Current Path | Archive Path | Description |
|--------------|--------------|-------------|
| `apps/web_console/README.md` | `docs/ARCHIVE/streamlit/web_console_readme.md` | Original Streamlit console docs |
| `docs/CONCEPTS/streamlit-*` (if any) | `docs/ARCHIVE/streamlit/concepts/` | Streamlit concept docs |
| `tests/integration/test_streamlit_csp.py` | Keep but mark deprecated | CSP tests (may need for rollback) |

**Deprecation Notice Template:**
```markdown
---
**DEPRECATED:** This document describes the legacy Streamlit web console.

**Archived:** YYYY-MM-DD
**Reason:** Replaced by NiceGUI implementation (see ADR-0031)
**Replacement:** See `docs/CONCEPTS/nicegui-*.md` for current documentation

For rollback procedures, see `docs/RUNBOOKS/nicegui-rollback.md`

---

[Original document content below]
```

**Link Update Mapping:**

| Old Link | New Link |
|----------|----------|
| `apps/web_console/README.md` | `apps/web_console_ng/README.md` |
| Any `st.session_state` references | `app.storage` references |
| `streamlit_helpers.py` references | `auth/middleware.py` references |

**Testing:**
- [ ] All documentation links work
- [ ] No broken cross-references
- [ ] PROJECT_STATUS reflects completion
- [ ] CLAUDE.md has NiceGUI guidance

---

## Prerequisites Checklist

**Must verify before starting documentation:**

- [ ] **P5T1 complete:** Foundation patterns to document
- [ ] **P5T2 complete:** Auth patterns to document
- [ ] **P5T3 complete:** HA/scaling patterns to document
- [ ] **P5T4 complete:** Dashboard patterns to document
- [ ] **P5T5 complete:** Manual controls patterns to document
- [ ] **P5T6 complete:** Chart patterns to document
- [ ] **P5T7 complete:** Page patterns to document
- [ ] **ADR template available:** `docs/STANDARDS/ADR_GUIDE.md`
- [ ] **Documentation standards available:** `docs/STANDARDS/DOCUMENTATION_STANDARDS.md`

---

## Approach

### High-Level Plan

1. **C0: ADR-0031** (2 days)
   - Draft decision record
   - Review with team
   - Finalize and merge

2. **C1: Concept Documentation** (2-3 days)
   - Architecture concepts
   - Auth concepts
   - Real-time concepts
   - Component concepts

3. **C2: Operational Runbooks** (1-2 days)
   - Deployment runbook
   - Troubleshooting runbook
   - Performance runbook
   - Rollback runbook

4. **C3: Migration Guide Updates** (1 day)
   - REPO_MAP updates
   - PROJECT_STATUS updates
   - INDEX.md updates
   - CLAUDE.md updates
   - Archive Streamlit docs

---

## Component Breakdown

### C0: ADR-0031

**Files to Create:**
```
docs/ADRs/
└── ADR-0031-nicegui-migration.md
```

### C1: Concept Documentation

**Files to Create:**
```
docs/CONCEPTS/
├── nicegui-architecture.md
├── nicegui-auth.md
├── nicegui-realtime.md
└── nicegui-components.md
```

### C2: Operational Runbooks

**Files to Create:**
```
docs/RUNBOOKS/
├── nicegui-deployment.md
├── nicegui-troubleshooting.md
├── nicegui-performance.md
└── nicegui-rollback.md
```

### C3: Migration Guide Updates

**Files to Modify:**
```
docs/GETTING_STARTED/REPO_MAP.md
docs/GETTING_STARTED/PROJECT_STATUS.md
docs/INDEX.md
CLAUDE.md
```

**Files to Create/Move:**
```
docs/ARCHIVE/streamlit/
└── (moved Streamlit-specific docs)
```

---

## Testing Strategy

### Documentation Review

For each document:
- [ ] Technical accuracy verified against implementation
- [ ] Code examples tested (copy-paste runnable)
- [ ] Cross-references checked
- [ ] Diagrams render correctly
- [ ] Follows repository documentation standards

### Link Verification

- [ ] Run link checker on all modified docs
- [ ] Verify INDEX.md links work
- [ ] Verify CLAUDE.md links work
- [ ] Verify cross-reference matrix compliance (all required links present)

**Link Verification Procedure:**
```bash
# Install markdown-link-check
npm install -g markdown-link-check

# Check all new docs
markdown-link-check docs/ADRs/ADR-0031-nicegui-migration.md
markdown-link-check docs/CONCEPTS/nicegui-*.md
markdown-link-check docs/RUNBOOKS/nicegui-*.md

# Check updated docs
markdown-link-check docs/INDEX.md
markdown-link-check CLAUDE.md
markdown-link-check docs/GETTING_STARTED/REPO_MAP.md
```

### Stakeholder Review

- [ ] ADR reviewed by tech lead
- [ ] Runbooks reviewed by ops team
- [ ] Concept docs reviewed by developers

---

## Dependencies

### Internal
- All P5T1-P5T7 implementation complete
- Existing documentation standards
- ADR template

### External
- None (documentation only)

---

## Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Implementation changes after doc | Medium | Low | Update docs in same PR as code |
| Stale code examples | Medium | Medium | Test all examples before merge |
| Incomplete coverage | Low | Medium | Checklist-driven approach |
| Broken links | Low | Low | Automated link checking |

---

## Implementation Notes

**Address during documentation:**

1. **ADR Structure:**
   - Follow existing ADR format in repository
   - Include diagrams for architecture decisions
   - Reference all P5T* task documents

2. **Code Examples:**
   - All examples must be copy-paste runnable
   - Include necessary imports
   - Show both good and bad patterns

3. **Runbook Format:**
   - Prerequisites section required
   - Step-by-step procedures
   - Verification commands after each step
   - Rollback steps where applicable

4. **Cross-References:**
   - Link related concept docs
   - Reference ADR from concept docs
   - Link runbooks from deployment docs

5. **Archive Strategy:**
   - Don't delete Streamlit docs immediately
   - Move to `docs/ARCHIVE/streamlit/`
   - Add deprecation notice with date

---

## Definition of Done

- [ ] ADR-0031 created and approved
- [ ] All 4 concept documents created
- [ ] All 4 runbooks created
- [ ] REPO_MAP updated with `apps/web_console_ng/`
- [ ] PROJECT_STATUS updated with P5 completion
- [ ] INDEX.md updated with new doc links
- [ ] CLAUDE.md updated with NiceGUI guidance
- [ ] Streamlit-specific docs archived
- [ ] All links verified
- [ ] Code examples tested
- [ ] Documentation review completed
- [ ] Merged to feature branch

---

**Last Updated:** 2025-12-31 (Rev 4)
**Status:** PLANNING
