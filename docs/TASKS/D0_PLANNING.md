# D0 Planning: Documentation Infrastructure Overhaul

**Phase:** D0 (Documentation Foundation)
**Status:** Planning
**Current Task:** Not started
**Previous Phase:** None (Independent documentation initiative)
**Last Updated:** 2026-01-02

---

## Progress Summary

**Overall:** 0% (0/6 tasks complete)

| Track | Progress | Status |
|-------|----------|--------|
| **Track 1: Inventory & Specs** | 0% (0/3) | Planning |
| **Track 2: Visualization & Cleanup** | 0% (0/3) | Planning |

**Completed:**
- None yet

**Next:** D0T1 - Update Repository Map

**See individual D0Tx_TASK.md files for detailed tracking**

---

## Executive Summary

Create a comprehensive "Shadow Codebase" documentation system that enables AI assistants and developers to understand the codebase structure without reading thousands of lines of code. The system produces machine-readable specs, visual architecture maps, and ensures documentation stays synchronized with code.

**Key D0 Goals:**
1. Accurate, up-to-date repository map reflecting current structure
2. Technical specs for all services and libraries (the "Shadow Codebase")
3. Visual architecture documentation (Obsidian Canvas + Mermaid diagrams)
4. Cleaned-up task archive with proper indexing

**Development Workflow:**

All tasks in this phase follow the standard development workflow with **clink-based zen-mcp reviews**:

1. **Task Creation Review** (RECOMMENDED)
   - Use workflow: `../AI/Workflows/02-planning.md`
   - Tool: clink + gemini planner -> codex planner
   - Validates: scope clarity, completeness
   - Duration: ~2-3 minutes

2. **Progressive Implementation** (MANDATORY 6-step pattern per component)
   - Plan -> Plan Review -> Implement -> Test -> Code Review -> Commit
   - See: `../AI/Workflows/03-reviews.md`

3. **Deep Review** (MANDATORY before PR)
   - Tool: clink + gemini codereviewer -> codex codereviewer
   - Duration: ~3-5 minutes

---

## Current State Analysis

### REPO_MAP.md Status: OUTDATED

**Last Updated:** 2025-11-21 (42+ days stale)

**Missing Apps (4 new services):**
| Service | Purpose | Status |
|---------|---------|--------|
| `alert_worker` | Alert processing worker | Not documented |
| `auth_service` | Authentication service | Not documented |
| `backtest_worker` | Backtesting worker | Not documented |
| `model_registry` | ML model registry service | Not documented |

**Missing Libs (13 new libraries):**
| Library | Purpose | Status |
|---------|---------|--------|
| `admin` | Admin utilities | Not documented |
| `alerts` | Alert system | Not documented |
| `alpha` | Alpha signal generation | Not documented |
| `analytics` | Analytics and metrics | Not documented |
| `backtest` | Backtesting framework | Not documented |
| `data_providers` | Data provider integrations | Not documented |
| `data_quality` | Data quality checks | Not documented |
| `factors` | Factor calculations | Not documented |
| `health` | Health check utilities | Not documented |
| `models` | ML model utilities | Not documented |
| `risk` | Risk calculations | Not documented |
| `tax` | Tax calculations | Not documented |
| `web_console_auth` | Web console authentication | Not documented |

**docs/SPECS/ Status:** Does not exist (to be created)

---

## D0 Tasks Breakdown

### Track 1: Inventory & Specs (The "Shadow Codebase")

#### D0T1: Update Repository Map - HIGH PRIORITY

**Goal:** Synchronize REPO_MAP.md with current directory structure

**Current State:**
- REPO_MAP.md last updated 2025-11-21
- Missing 4 apps and 13 libs
- Some documented components may be deprecated/renamed

**Implementation Steps:**
1. **Audit current structure**
   - Scan ALL top-level directories: `apps/`, `libs/`, `scripts/`, `tests/`, `config/`, `infra/`, `db/`, `docs/`, `data/`, `artifacts/`, `notebooks/`, `strategies/`, `migrations/`
   - Include hidden/metadata directories: `.ai_workflow/`, `.github/`
   - Identify new, deprecated, and renamed components
   - Compare against current REPO_MAP.md

2. **Update REPO_MAP.md**
   - Add all new services with 1-sentence descriptions
   - Add all new libraries with 1-sentence descriptions
   - Mark deprecated components as [DEPRECATED]
   - Update "Last Updated" timestamp

3. **Verify accuracy**
   - Cross-reference with actual file structure
   - Ensure all active folders have descriptions

4. **Create freshness check script** (REQUIRED DELIVERABLE - prevents doc rot)
   - Create `scripts/check_doc_freshness.py`
   - See **Script Specifications** section below for detailed function definitions
   - Compare REPO_MAP.md entries against actual directories
   - Report missing/orphaned entries
   - Exit non-zero if discrepancies found (for CI integration)
   - **IMPORTANT:** Script must treat missing `docs/SPECS/` as "not applicable" until D0T2 completes
     (prevents CI failure before specs exist)

5. **Wire script into CI** (REQUIRED - ensures enforcement)
   - Add `check_doc_freshness.py` to `.github/workflows/` or `ci_with_timeout.sh`
   - Document in README or CONTRIBUTING.md
   - **Sequencing:** D0T1 CI wiring checks REPO_MAP only; D0T2 extends to check specs

**Acceptance Criteria:**
- [ ] All apps/ directories documented with descriptions
- [ ] All libs/ directories documented with descriptions
- [ ] Deprecated components marked
- [ ] Last Updated timestamp reflects current date
- [ ] No orphaned references to non-existent paths
- [ ] Freshness check script created and working
- [ ] Freshness check wired into CI pipeline
- [ ] Unit tests for freshness script in `tests/scripts/test_check_doc_freshness.py`

**Files to Modify:**
- `docs/GETTING_STARTED/REPO_MAP.md`

**Estimated Effort:** 2-3 days

**Files to Create:**
- `scripts/check_doc_freshness.py`
- `tests/scripts/test_check_doc_freshness.py`
- `.github/workflows/docs-check.yml` (or extend existing CI config)

---

#### D0T2: Generate Service & Library Specs - HIGH PRIORITY

**Goal:** Create technical specifications for every service and library

**Current State:**
- No docs/SPECS/ folder exists
- AI assistants must read source code to understand components
- No standardized spec format

**Implementation Steps:**
1. **Create directory structure**
   ```
   docs/SPECS/
   ├── README.md              # Index and spec format guide
   ├── services/              # App specs (maps to apps/)
   │   ├── signal_service.md
   │   ├── execution_gateway.md
   │   └── ...
   ├── libs/                  # Library specs (maps to libs/)
   │   ├── common.md
   │   ├── risk_management.md
   │   └── ...
   ├── strategies/            # Strategy specs (maps to strategies/)
   │   ├── alpha_baseline.md
   │   └── ...
   └── infrastructure/        # Infrastructure specs (covers both infra/ and docker-compose services)
       ├── redis.md           # Redis backing service (from docker-compose.yml)
       ├── postgres.md        # Postgres database (from docker-compose.yml)
       ├── docker-compose.md  # Service orchestration overview (base compose only)
       ├── prometheus.md      # Observability (from infra/prometheus/)
       ├── grafana.md         # Dashboards (from infra/grafana/)
       └── loki.md            # Logging (from infra/loki/)
   ```

   > **Infrastructure scope:** Includes backing services (Redis, Postgres) from `docker-compose.yml`
   > AND observability tools (Prometheus, Grafana, Loki) from `infra/` directory.
   > **Note:** Only base `docker-compose.yml` is documented. Environment-specific files
   > (`docker-compose.ci.yml`, `docker-compose.staging.yml`) are cross-linked but not fully spec'd.

   > **Note on naming:** `apps/` maps to `docs/SPECS/services/` (architecture terminology).
   > **Rationale:** "Services" reflects the microservices architecture pattern and aligns with
   > industry-standard documentation (e.g., "Service Catalog"). The mapping is explicitly
   > documented in SPECS/README.md with a cross-reference table.

2. **Define spec template**
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
   > This is the most critical section for AI coding assistance.

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
   - [Prometheus metrics exposed by this component]

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

   > **Template usage:** Include only relevant sections for the component type.
   > Mark non-applicable sections with "N/A" or omit entirely.
   >
   > | Section | Service | Library | Strategy | Infrastructure |
   > |---------|---------|---------|----------|----------------|
   > | Identity | ✅ | ✅ | ✅ | ✅ |
   > | Interface (API Endpoints) | ✅ | ❌ | ❌ | ❌ |
   > | Interface (Public Classes) | ❌ | ✅ | ❌ | ❌ |
   > | Interface (Signal Gen) | ❌ | ❌ | ✅ | ❌ |
   > | Interface (Config) | ❌ | ❌ | ❌ | ✅ |
   > | **Behavioral Contracts** | ✅ | ✅ | ✅ | ❌ |
   > | **Data Flow** | ✅ | ✅ | ✅ | ❌ |
   > | **Usage Examples** | ✅ | ✅ | ✅ | ❌ |
   > | **Edge Cases** | ✅ | ✅ | ✅ | ❌ |
   > | Dependencies | ✅ | ✅ | ✅ | ✅ |
   > | Configuration | ✅ | ✅ | ✅ | ✅ |
   > | Error Handling | ✅ | ✅ | ✅ | ❌ |
   > | Observability | ✅ | ❌ | ❌ | ✅ |
   > | Security | ✅ | Optional | ✅ | ✅ |
   > | Testing | ✅ | ✅ | ✅ | ❌ |
   > | Related Specs | ✅ | ✅ | ✅ | ✅ |
   > | Known Issues | ✅ | ✅ | ✅ | ✅ |
   > | Metadata | ✅ | ✅ | ✅ | ✅ |
   >
   > **AI Coding Priority:** The sections in **bold** are CRITICAL for AI coders.
   > These enable understanding code functionality without reading source files.

3. **Generate specs for all components**
   - Process each app and lib
   - Extract interfaces, dependencies, config
   - Do NOT write generic summaries - raw technical specs only

4. **Extract behavioral contracts (AI-critical sections)**
   - Parse docstrings for pre/post conditions, behaviors
   - Extract assert statements for invariants
   - Identify state transitions from class methods
   - Generate data flow diagrams from function signatures
   - Create usage examples from test files

**Acceptance Criteria:**
- [ ] docs/SPECS/ directory created
- [ ] Spec file for each app (one per app/ directory)
- [ ] Spec file for each lib (one per libs/ directory - all libs, not just "major")
- [ ] Spec file for each strategy (one per strategies/ directory)
- [ ] Infrastructure specs for Redis, Postgres, Docker
- [ ] README.md with index and format guide (includes apps/→services/ mapping note)
- [ ] Update docs/INDEX.md with SPECS section links
- [ ] **Each spec includes Behavioral Contracts section (AI-critical)**
- [ ] **Each spec includes Data Flow section (AI-critical)**
- [ ] **Each spec includes Usage Examples from tests (AI-critical)**
- [ ] **Each spec includes Edge Cases table (AI-critical)**

**Estimated Effort:** 5-6 days (increased for behavioral extraction)

**Files to Create:**
- `docs/SPECS/README.md`
- `docs/SPECS/services/*.md` (one per app)
- `docs/SPECS/libs/*.md` (one per lib)
- `docs/SPECS/strategies/*.md` (one per strategy)
- `docs/SPECS/infrastructure/*.md` (6 files: redis, postgres, docker-compose, prometheus, grafana, loki)

---

#### D0T3: Generate Data Dictionary - MEDIUM PRIORITY

**Goal:** Centralized catalog of all data models in the codebase

**Current State:**
- Pydantic models scattered across codebase
- DB tables defined in migrations
- No unified data dictionary

**Implementation Steps:**
1. **Scan for data models**
   - Find all Pydantic models (`class ... (BaseModel)`)
   - Find all dataclasses (`@dataclass`)
   - Find all TypedDicts
   - Scan `libs/duckdb_catalog.py` for DuckDB/OLAP schemas
   - **Primary source (SQL migrations):** Parse `db/migrations/*.sql` and `migrations/*.sql` for CREATE TABLE statements
   - **Secondary source (if Alembic exists):** `db/versions/*.py` for SQLAlchemy table definitions
   - **Authoritative paths:** `db/migrations/*.sql`, `migrations/*.sql` (primary), `db/versions/*.py` (if present)

2. **Create DATA_MODELS.md**
   - Group by domain (Orders, Signals, Positions, Users, etc.)
   - List fields and types for each model
   - Note relationships and foreign keys

3. **Create EVENTS.md** (Addition to original plan)
   - Document all Redis pub/sub channels AND Redis Streams
   - Document event/message formats and payloads
   - **Link payload schemas:** Reference Pydantic models in DATA_MODELS.md or define JSON schema inline
   - Document producers and consumers (including consumer groups for Streams)
   - Include queue/stream semantics (at-least-once, at-most-once, etc.)
   - **Note:** Manual maintenance required; consider future automation via static analysis of `redis_client` calls

4. **Create SCHEMAS.md** (Addition to original plan)
   - Extract OpenAPI schemas from FastAPI services
   - **Pre-requisite check:** Verify each service in `apps/` can emit OpenAPI JSON:
     - If not available, add a `dump-schema` CLI command or build step to generate it
     - Test with: `source .venv/bin/activate && TESTING=1 python -c "from apps.<name>.main import app; print(app.openapi())"`
   - **Deterministic extraction method (preferred):** Commit generated OpenAPI JSON files in repo
     (e.g., `docs/SPECS/openapi/<service>.json`) and update SCHEMAS.md from those.
     Services generate these during build/test with `app.openapi()`.
   - **Safe-import contract (REQUIRED per service):**
     Each service's `main.py` MUST support safe import for schema extraction:
     ```python
     # At top of main.py - guard all side-effect code
     if not os.getenv("TESTING") and not os.getenv("SCHEMA_ONLY"):
         # Initialize DB connections, Redis, external APIs
         ...
     ```
     - `TESTING=1`: Skip all external connections (for tests)
     - `SCHEMA_ONLY=1`: Skip ALL initialization, only define app and routes
     - **Verification:** During D0T3, audit each service for this pattern
     - **Fallback:** If a service lacks the guard, document it in SCHEMAS.md as "manual extraction required"
   - **Alternative with isolation:** Import `app` from each service's `main.py` in a script that:
     - Mocks `sys.modules` dependencies (DB, Redis, external APIs) to prevent side effects
     - Uses `TESTING=1` or `SCHEMA_ONLY=1` environment flags to skip startup hooks
     - Never connects to live services
   - Document request/response contracts

**Acceptance Criteria:**
- [ ] DATA_MODELS.md with all Pydantic models cataloged
- [ ] Models grouped by domain
- [ ] EVENTS.md with Redis channel documentation (including payload schemas)
- [ ] SCHEMAS.md with API contract documentation
- [ ] DuckDB schema documented
- [ ] Legacy SQL migrations tables cataloged
- [ ] OpenAPI JSON files committed for each FastAPI service

**Estimated Effort:** 3-4 days

**Files to Create:**
- `docs/SPECS/DATA_MODELS.md`
- `docs/SPECS/EVENTS.md`
- `docs/SPECS/SCHEMAS.md`
- `docs/SPECS/openapi/*.json` (one per FastAPI service)

---

### Track 2: Visualization & Cleanup

#### D0T4: Document System Mechanisms - MEDIUM PRIORITY

**Goal:** Step-by-step technical walkthroughs of critical business processes

**Current State:**
- Business logic spread across multiple services
- No end-to-end process documentation
- AI assistants must trace code paths manually

**Implementation Steps:**
1. **Identify top 6 critical processes**
   - Order Execution Lifecycle
   - Signal Generation Pipeline
   - Data Ingestion Pipeline
   - Risk Check Flow
   - Circuit Breaker Activation
   - Reconciliation/Position Sync (critical trading safety mechanism)

2. **Create SYSTEM_MECHANISMS.md**
   - Step-by-step technical walkthrough for each process
   - Reference specific services, APIs, and data flows
   - Include Mermaid sequence diagrams

3. **Format example**
   ~~~markdown
   ## Order Execution Lifecycle

   ### Sequence Diagram
   ```mermaid
   sequenceDiagram
       participant O as Orchestrator
       participant S as SignalService
       participant E as ExecutionGateway
       participant A as Alpaca API
   ```

   ### Steps
   1. **Orchestrator** triggers signal generation
   2. **SignalService** loads model from registry...
   ~~~

**Acceptance Criteria:**
- [ ] 6 critical processes documented (including Reconciliation)
- [ ] Each process has step-by-step walkthrough
- [ ] Mermaid sequence diagrams for visual flow
- [ ] Specific service/API references (not generic descriptions)

**Estimated Effort:** 2-3 days

**Files to Create:**
- `docs/SPECS/SYSTEM_MECHANISMS.md`

---

#### D0T5: Generate Architecture Visualization - MEDIUM PRIORITY

**Goal:** Visual architecture map with Obsidian Canvas + Mermaid fallback

**Current State:**
- No visual architecture documentation
- System relationships must be inferred from code

**Implementation Steps:**
1. **Create Python generator script**
   ```python
   # scripts/generate_architecture.py
   # - Use Python `ast` module (stdlib only - no external dependencies)
   # - Scan apps/, libs/, AND strategies/ for Python imports
   # - Filter to internal modules only (apps/, libs/, strategies/) - exclude third-party
   # - External dependencies aggregated or omitted to keep diagrams readable
   # - Parse imports/dependencies from code (avoid custom regex)
   # - Generate Obsidian Canvas JSON
   # - Generate Mermaid diagram as fallback
   ```

2. **Generate Obsidian Canvas**
   - Create file nodes for each spec in docs/SPECS/
   - Group nodes: Services, Libraries, Infrastructure
   - Draw edges based on import analysis
   - Output: `docs/ARCHITECTURE/system_map.canvas`

3. **Generate Mermaid fallback** (Addition to original plan)
   - Create `docs/ARCHITECTURE/system_map.md`
   - Embed Mermaid diagram for portability
   - Works without Obsidian

4. **Create ARCHITECTURE README**
   - Explain how to view/edit the architecture map
   - Document the generation script usage

5. **Wire script into CI** (REQUIRED - keeps diagrams live)
   - Add `generate_architecture.py` to CI pipeline
   - Fail build if generated output differs from committed version
   - Ensures architecture diagrams stay synchronized with code

**Acceptance Criteria:**
- [ ] Python script generates both formats
- [ ] Obsidian Canvas with grouped nodes
- [ ] Mermaid diagram as markdown fallback
- [ ] Edges reflect actual import relationships
- [ ] README explains usage
- [ ] Script wired into CI (fail on drift)
- [ ] Unit tests for architecture script in `tests/scripts/test_generate_architecture.py`
- [ ] Script handles parse errors gracefully (skip with warning, not fail)

**Estimated Effort:** 3-4 days

**Files to Create:**
- `scripts/generate_architecture.py`
- `tests/scripts/test_generate_architecture.py`
- `docs/ARCHITECTURE/README.md`
- `docs/ARCHITECTURE/system_map.canvas`
- `docs/ARCHITECTURE/system_map.md`

**Files to Modify:**
- `.github/workflows/docs-check.yml` (add architecture drift check)

---

#### D0T6: Archive & Cleanup - LOW PRIORITY

**Goal:** Clean up stale documentation and organize task archive

**Current State:**
- 60+ task files in docs/TASKS/
- Many completed tasks (_DONE.md) cluttering directory
- docs/INDEX.md may be outdated

**Implementation Steps:**
1. **Archive completed tasks**
   - Move `*_DONE.md` files to `docs/ARCHIVE/TASKS_HISTORY/`
   - Preserve git history (use git mv)
   - Keep only active/future tasks in docs/TASKS/

2. **Update docs/INDEX.md**
   - Add docs/SPECS/ section
   - Add docs/ARCHITECTURE/ section
   - Update docs/TASKS/ references

3. **Clean duplicate documentation**
   - Identify duplicate/outdated docs
   - Consolidate or mark as deprecated

4. **Scan and fix broken links** (REQUIRED - prevents dead documentation)
   - Run existing `scripts/check_links.py` on all markdown files in `docs/`
   - Use `scripts/fix_links.py` to update relative links broken by archive moves
   - Existing CI: `.github/workflows/markdown-link-check.yml` already configured

**Acceptance Criteria:**
- [ ] Completed tasks moved to archive
- [ ] docs/TASKS/ contains only active tasks
- [ ] docs/INDEX.md updated with new sections
- [ ] No broken internal links (verified by existing `scripts/check_links.py`)

**Estimated Effort:** 1-2 days

**Files to Create:**
- `docs/ARCHIVE/TASKS_HISTORY/` (directory)

**Existing Tooling (no new scripts needed):**
- `scripts/check_links.py` - Already exists
- `scripts/fix_links.py` - Already exists
- `.github/workflows/markdown-link-check.yml` - Already configured

**Files to Modify:**
- `docs/INDEX.md`
- `docs/SPECS/README.md` (update index links after archiving)
- Various task files (move operation)

---

## D0 Roadmap & Priorities

### Priority Order

1. **D0T1: Update Repository Map** - Foundation for all other tasks
2. **D0T2: Generate Service & Library Specs** - Core "Shadow Codebase"
3. **D0T3: Generate Data Dictionary** - Completes the spec coverage
4. **D0T4: Document System Mechanisms** - Adds process understanding
5. **D0T5: Generate Architecture Visualization** - Depends on specs existing
6. **D0T6: Archive & Cleanup** - Can run in parallel with T4-T5

### Dependency Graph

```
D0T1 (Repo Map)
    ↓
D0T2 (Specs) ─────────┬───────────→ D0T5 (Visualization)
    ↓                 │
D0T3 (Data Models)    │
    ↓                 │
D0T4 (Mechanisms) ────┘

D0T6 (Cleanup) ─── [Independent, can run in parallel]
```

---

## Success Metrics

### D0 Success Criteria
- [ ] REPO_MAP.md accurately reflects current structure
- [ ] Every app has a technical spec
- [ ] Every lib has a technical spec (100% libs/ coverage)
- [ ] All Pydantic models cataloged in DATA_MODELS.md
- [ ] Architecture visualization generates successfully
- [ ] Completed tasks archived

### Quality Metrics
- [ ] Zero orphaned file references in REPO_MAP.md
- [ ] Spec coverage: 100% of apps, 100% of libs, 100% of strategies
- [ ] All Mermaid diagrams render correctly
- [ ] docs/INDEX.md links all verify

---

## Testing Strategy

### Validation Tests
- Verify all file paths in REPO_MAP.md exist
- Verify all spec files have required sections
- Verify Mermaid diagrams render (GitHub preview)
- Verify Obsidian Canvas JSON is valid

### Automation (Delivered in D0T1 and D0T5)
- `scripts/check_doc_freshness.py` - CI check for REPO_MAP.md freshness AND spec existence (D0T1 deliverable, extended in D0T2)
  - Checks: REPO_MAP.md entries vs actual directories
  - Checks: `docs/SPECS/services/<name>.md` exists for each `apps/<name>/`
  - Checks: `docs/SPECS/libs/<name>.md` exists for each `libs/<name>/`
  - Checks: `docs/SPECS/strategies/<name>.md` exists for each `strategies/<name>/`
  - Exit codes: 0=fresh, 1=missing, 2=orphaned, 4=missing specs, 8=stale REPO_MAP (bitmask)
- `scripts/check_links.py` - **ALREADY EXISTS** - CI check for broken links
- `scripts/generate_architecture.py` - Architecture diagram generation (D0T5 deliverable)
- markdownlint for documentation quality

### Script Unit Tests
- **Requirement:** Each NEW automation script must have corresponding unit tests in `tests/scripts/`
- **Tests to include:**
  - `test_check_doc_freshness.py` - Test parsing, freshness detection, exit code logic
  - `test_generate_architecture.py` - Test AST parsing of complex imports, graph building
- **Existing scripts:** `scripts/check_links.py` already has tests or CI coverage
- **Rationale:** Scripts are part of CI; bugs cause false passes/failures affecting all PRs

---

## Documentation Requirements

### For Each Task
- [ ] Updated docs/INDEX.md (only if task creates new docs directories/major sections)
- [ ] Self-documenting README in new directories
- [ ] Commit message following standard format

**INDEX.md Update Matrix:**
| Task | INDEX.md Update Required? | What to Add |
|------|--------------------------|-------------|
| D0T1 | No (existing section) | N/A |
| D0T2 | Yes | docs/SPECS/ section |
| D0T3 | No (part of SPECS) | N/A |
| D0T4 | No (part of SPECS) | N/A |
| D0T5 | Yes | docs/ARCHITECTURE/ section |
| D0T6 | Yes | docs/ARCHIVE/ section, update TASKS refs |

### New Files Created
- `docs/SPECS/` directory and all contents
- `docs/ARCHITECTURE/` directory and contents
- `docs/ARCHIVE/TASKS_HISTORY/` directory
- `scripts/generate_architecture.py`

---

## Risk & Mitigation

### Risk 1: Scope Creep on Specs
**Impact:** Medium
**Probability:** High
**Mitigation:** Define strict spec template; avoid prose descriptions; focus on machine-readable data

### Risk 2: Obsidian Canvas Format Changes
**Impact:** Low
**Probability:** Medium
**Mitigation:** Generate Mermaid fallback; keep Canvas as optional enhancement

### Risk 3: Specs Become Stale
**Impact:** High
**Probability:** Medium
**Mitigation:** Add CI freshness checks; include "Last Updated" in each spec; consider auto-generation from code

---

## Future Considerations (Out of Scope for D0)

1. **Auto-generation from Code**
   - Parse AST to generate specs automatically
   - Keep specs synchronized with code changes

2. **Documentation Linting**
   - markdownlint integration
   - Link checking in CI

3. **API Documentation**
   - Auto-generate from OpenAPI specs
   - Integrate with Swagger/Redoc

---

## Script Specifications

Detailed function definitions for automation scripts to ensure file changes trigger documentation updates.

### scripts/check_doc_freshness.py

**Purpose:** Detect when source directories change but documentation hasn't been updated.

```python
# Path Normalization Rules
#
# CRITICAL: Define canonical path formats to prevent false positives in CI.
#
# Scopes:
#   1. Top-level directory existence: "apps/", "libs/", etc.
#   2. Component subdirectory coverage: "apps/signal_service/", "libs/redis_client/"
#
# Normalization:
#   - All paths are root-relative (from repo root)
#   - Trailing slash indicates directory (e.g., "apps/" not "apps")
#   - Component paths include parent: "apps/signal_service/" not "signal_service"
#   - normalize_path(path) -> strip leading "./", ensure trailing "/"
#
# Matching Rules:
#   - REPO_MAP.md documents TOP-LEVEL directories: ["apps/", "libs/"]
#   - SPECS documents COMPONENT directories: ["apps/signal_service/", "libs/redis_client/"]
#   - parse_documented_entries() must return FULL paths matching source structure
#
# Example:
#   Source: apps/signal_service/ exists
#   SPECS: docs/SPECS/services/signal_service.md exists
#   Match: normalize("apps/signal_service/") == "apps/signal_service/"

def normalize_path(path: str) -> str:
    """
    Normalize path to canonical format for comparison.

    Rules:
        - Strip leading "./"
        - Ensure trailing "/" for directories
        - Root-relative (no leading "/")

    Examples:
        normalize_path("./apps") -> "apps/"
        normalize_path("apps/signal_service") -> "apps/signal_service/"
        normalize_path("/libs/redis_client/") -> "libs/redis_client/"
    """

# Function Definitions

def get_source_directories() -> dict[str, list[str]]:
    """
    Returns mapping of documentation files to source directories they document.

    Returns:
        {
            "docs/GETTING_STARTED/REPO_MAP.md": [
                "apps/", "libs/", "scripts/", "tests/", "config/",
                "infra/", "db/", "docs/", "data/", "artifacts/",
                "notebooks/", "strategies/", "migrations/",
                ".ai_workflow/", ".github/"
            ],
            "docs/SPECS/services/*.md": ["apps/*/"],
            "docs/SPECS/libs/*.md": ["libs/*/"],
            "docs/SPECS/strategies/*.md": ["strategies/*/"],
            # Infrastructure and D0T3 docs are EXEMPT from freshness checks
            # (manually maintained, no direct source-to-doc mapping)
        }

    Special behavior:
        - If `docs/SPECS/` does not exist, SKIP all spec checks (return exit 0 for specs)
        - This allows D0T1 to complete before D0T2 creates the SPECS directory
        - Only validate REPO_MAP entries when SPECS directory is missing
    """

class DirectoryState(TypedDict):
    """Typed structure for directory state."""
    subdirs: list[str]           # Immediate subdirectory names
    last_modified: str           # ISO 8601 timestamp of most recent change

class FreshnessReport(TypedDict):
    """Report from freshness check."""
    doc_path: str                # Path to documentation file checked
    missing: list[str]           # Dirs in source but not in docs
    orphaned: list[str]          # Dirs in docs but not in source (excluding [DEPRECATED])
    deprecated: list[str]        # Dirs marked [DEPRECATED] (informational, not an error)
    stale: bool                  # True if doc older than source changes
    last_doc_update: str         # ISO 8601 timestamp of doc last modified
    last_source_change: str      # ISO 8601 timestamp of most recent source change
    missing_specs: list[str]     # Source dirs with no corresponding spec file

def get_directory_state(path: str) -> DirectoryState:
    """
    Get current state of directory using GIT COMMIT TIMESTAMPS (not filesystem mtimes).

    CRITICAL: Use git timestamps for CI stability.
    Filesystem mtimes are unreliable in CI due to checkout time skew.

    Implementation:
        # Get last commit time touching any file in directory
        result = subprocess.run(
            ["git", "log", "-1", "--format=%cI", "--", path],
            capture_output=True, text=True
        )
        last_modified = result.stdout.strip() or "1970-01-01T00:00:00Z"

    Returns:
        DirectoryState with subdirs and last_modified (git commit timestamp)
        Example: {"subdirs": ["auth_service", "execution_gateway"], "last_modified": "2026-01-02T10:30:00Z"}
    """

def parse_documented_entries(doc_path: str) -> tuple[set[str], set[str]]:
    """
    Parse documentation file to extract documented directory names.
    Uses regex to find directory references in markdown.

    Returns:
        Tuple of (active_entries, deprecated_entries)
        - active_entries: Directories that should exist
        - deprecated_entries: Directories marked with [DEPRECATED] (excluded from orphan checks)

    Deprecated detection:
        - Entries containing "[DEPRECATED]" in the same line are classified as deprecated
        - Example: "- `old_service/` [DEPRECATED] - Legacy service, removed in v2.0"
        - Deprecated entries are NOT flagged as orphaned even if directory is missing

    Example: From REPO_MAP.md extracts (FULL NORMALIZED PATHS per normalization rules):
        active=["apps/signal_service/", "apps/execution_gateway/", "libs/redis_client/"]
        deprecated=["apps/legacy_api/"]

    Note: Returns FULL paths matching normalize_path() output, NOT bare component names.
    """

def check_freshness(doc_path: str, source_dirs: list[str]) -> FreshnessReport:
    """
    Compare documented entries against actual directory state.

    Logic:
        1. Parse doc to get (active_entries, deprecated_entries)
        2. Get actual directories from source_dirs
        3. missing = actual - active (dirs exist but not documented)
        4. orphaned = active - actual (documented but missing, excluding deprecated)
        5. Deprecated entries are logged but NOT counted as errors

    Returns:
        FreshnessReport(
            doc_path="docs/GETTING_STARTED/REPO_MAP.md",
            missing=[],      # Dirs in source but not in docs
            orphaned=[],     # Dirs in docs but not in source (excluding deprecated)
            deprecated=["legacy_api"],  # Marked deprecated, missing is OK
            stale=False,     # True if doc older than source changes
            last_doc_update="2025-11-21",
            last_source_change="2025-12-31"
        )
    """

def main() -> int:
    """
    Main entry point. Returns exit code.

    Exit codes (bitmask for combining):
        0: All docs fresh
        1: Missing entries found (dirs exist but not documented in REPO_MAP)
        2: Orphaned entries found (documented dirs don't exist, excluding [DEPRECATED])
        4: Missing spec files (source dirs without corresponding spec file)
        8: Stale REPO_MAP (REPO_MAP.md older than 7 days since source change)

    Staleness policy:
        - REPO_MAP.md: BLOCKING if stale >7 days (exit code 8) - prevents critical doc rot
        - Spec files: WARNING only (logged to stderr, does not fail CI) - allows reasonable lag
        - Rationale: REPO_MAP is the navigation index; specs may lag source during active development

    Deprecated entries:
        - Entries marked [DEPRECATED] are NOT counted as orphaned
        - They are logged as informational output

    Example: status=9 (missing + stale REPO_MAP) → exit code = 9 (both fail CI)
    Example: status=8 (stale REPO_MAP only) → exit code = 8 (CI fails, update REPO_MAP)
    """
```

**File-to-Documentation Mapping:**

| Source Change | Documentation to Update | Detection Method |
|---------------|------------------------|------------------|
| New dir in `apps/` | `REPO_MAP.md`, create `docs/SPECS/services/<name>.md` | Dir exists, no spec file |
| New dir in `libs/` | `REPO_MAP.md`, create `docs/SPECS/libs/<name>.md` | Dir exists, no spec file |
| New dir in `strategies/` | `REPO_MAP.md`, create `docs/SPECS/strategies/<name>.md` | Dir exists, no spec file |
| Deleted dir | `REPO_MAP.md` (mark [DEPRECATED]) | Spec exists, dir missing |
| Renamed dir | Treated as delete + add (no special rename detection) | Orphaned spec + missing spec |
| New file in `apps/*/` | Update corresponding spec's Interface section | mtime comparison |

---

### scripts/check_links.py - ALREADY EXISTS

**Note:** This script already exists in the repository. No new implementation needed.

**Existing files:**
- `scripts/check_links.py` - Link checking script
- `scripts/fix_links.py` - Link fixing script
- `.github/workflows/markdown-link-check.yml` - CI workflow
- `.github/markdown-link-check-config.json` - Configuration

**Usage for D0T6:**
```bash
# Check for broken links
python scripts/check_links.py

# Fix broken links after archive moves
python scripts/fix_links.py
```

**Trigger Conditions:**

| Action | Links to Check |
|--------|---------------|
| `git mv` any `.md` file | All files that reference moved file |
| Delete `.md` file | All files that reference deleted file |
| Archive task files | `docs/INDEX.md`, `docs/TASKS/INDEX.md` |

---

### scripts/generate_architecture.py

**Purpose:** Generate architecture diagrams from code structure, fail CI if diagrams drift.

```python
# Function Definitions

def scan_imports(file_path: Path) -> list[Import]:
    """
    Use Python `ast` module to extract imports from a Python file.

    Handles:
        - Absolute imports: `from libs.common import x` -> "libs.common"
        - Relative imports: `from . import x` or `from ..pkg import y`
          - Normalize to package root using file_path context
          - Example: in `apps/signal_service/handlers.py`, `from . import utils`
            becomes "apps.signal_service.utils"

    Limitations (logged as warnings, not errors):
        - Dynamic imports: `importlib.import_module("...")` cannot be statically analyzed
        - Runtime-generated imports: `__import__(name)` patterns
        - These are logged to stderr with the source file path for manual review

    Error handling:
        - SyntaxError: Log warning with file path, skip file, continue processing
        - UnicodeDecodeError: Log warning, skip file, continue processing
        - Any parse failure skips the file rather than failing the entire script

    Returns:
        [
            Import(module="libs.common.logging", alias=None),
            Import(module="libs.redis_client", alias="redis"),
        ]
    """

def build_dependency_graph(root_dirs: list[str]) -> DependencyGraph:
    """
    Build complete dependency graph for apps/, libs/, and strategies/.

    Args:
        root_dirs: ["apps/", "libs/", "strategies/"]

    Returns:
        DependencyGraph with nodes (components) and edges (imports)
        Filters to internal modules only; third-party dependencies omitted.

    Missing spec handling:
        - If `docs/SPECS/` does not exist: Log warning, generate nodes from source dirs only
          (nodes will use placeholder paths like "docs/SPECS/services/<name>.md [MISSING]")
        - If individual spec file is missing: Include node with "[MISSING]" suffix in label
        - This allows D0T5 to run before D0T2 completes, showing gaps visually
        - The --check mode will NOT fail for missing specs; it only checks diagram drift
    """

def generate_obsidian_canvas(graph: DependencyGraph) -> dict:
    """
    Generate Obsidian Canvas JSON structure.

    Output structure:
        {
            "nodes": [
                {"id": "1", "type": "file", "file": "docs/SPECS/services/signal_service.md", "x": 0, "y": 0},
                ...
            ],
            "edges": [
                {"id": "e1", "fromNode": "1", "toNode": "5", "label": "imports"},
                ...
            ]
        }
    """

def generate_mermaid_diagram(graph: DependencyGraph) -> str:
    """
    Generate Mermaid flowchart from dependency graph.

    Output:
        ```mermaid
        flowchart TD
            subgraph Services
                signal_service --> libs_common
                execution_gateway --> libs_redis_client
            end
            subgraph Libraries
                libs_common
                libs_redis_client
            end
        ```
    """

def check_drift(generated: str, committed: Path) -> bool:
    """
    Compare generated output with committed file.
    Returns True if they differ (drift detected).
    """

def main() -> int:
    """
    Main entry point.

    Modes:
        --generate: Write new files
        --check: Compare against committed (for CI)

    Exit codes:
        0: No drift (or files generated successfully)
        1: Drift detected (generated != committed)
    """
```

**Source-to-Diagram Mapping:**

| Source Change | Diagram Impact | Detection |
|---------------|---------------|-----------|
| New `import` statement | Add edge in graph | AST parsing |
| New app/lib directory | Add node + edges | Directory scan |
| Removed import | Remove edge | AST parsing |
| Renamed module | Treated as delete + add (no special handling) | Directory scan |

**CI Integration:**

```yaml
# .github/workflows/docs-check.yml
jobs:
  check-docs:
    steps:
      - run: python scripts/check_doc_freshness.py
      - run: python scripts/check_links.py
      - run: python scripts/generate_architecture.py --check
```

---

### Spec File Update Triggers

Define when each spec file needs updating based on source changes:

| Spec File | Trigger Source Files | Update Required When |
|-----------|---------------------|---------------------|
| `docs/SPECS/services/<name>.md` | `apps/<name>/*.py` | Any `.py` file modified |
| `docs/SPECS/libs/<name>.md` | `libs/<name>/*.py` | Any `.py` file modified |
| `docs/SPECS/strategies/<name>.md` | `strategies/<name>/*.py` | Any `.py` file modified |
| `docs/SPECS/DATA_MODELS.md` | `**/schemas.py`, `**/models.py`, `db/versions/*.py`, `migrations/*.sql` | Pydantic/SQLAlchemy/SQL changes |
| `docs/SPECS/EVENTS.md` | `**/events.py`, `**/pubsub.py`, Redis publish calls | Event definitions change |
| `docs/SPECS/SYSTEM_MECHANISMS.md` | Core workflow files | Major flow changes |
| `docs/ARCHITECTURE/system_map.*` | Any import statement change | Dependency graph changes |

**Staleness Detection:**

```python
def is_spec_stale(spec_path: Path, source_dir: Path) -> bool:
    """
    Check if spec is older than any source file it documents.

    CRITICAL: Use git commit timestamps for CI stability (not filesystem mtimes).

    Logic:
        # Get last commit time for spec file
        spec_commit = subprocess.run(
            ["git", "log", "-1", "--format=%ct", "--", str(spec_path)],
            capture_output=True, text=True
        ).stdout.strip()
        spec_time = int(spec_commit) if spec_commit else 0

        # Get last commit time for any file in source directory
        source_commit = subprocess.run(
            ["git", "log", "-1", "--format=%ct", "--", str(source_dir)],
            capture_output=True, text=True
        ).stdout.strip()
        source_time = int(source_commit) if source_commit else 0

        return source_time > spec_time
    """
```

---

## Related Documents

- [docs/GETTING_STARTED/REPO_MAP.md](../GETTING_STARTED/REPO_MAP.md) - Current repo map (to be updated)
- [docs/INDEX.md](../INDEX.md) - Documentation index (to be updated)
- [docs/AI/Workflows/03-reviews.md](../AI/Workflows/03-reviews.md) - Review workflow

---

**Last Updated:** 2026-01-02
**Status:** Planning (0% complete, 0/6 tasks)
**Next Review:** After D0T1 completion
