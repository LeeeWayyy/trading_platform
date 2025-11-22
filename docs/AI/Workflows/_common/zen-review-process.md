# Zen-MCP Review Process

**Purpose:** Comprehensive code review system using zen-mcp with clink for AI-assisted code quality assurance.

## Critical: Clink-Only Policy

**⚠️ MANDATORY: ALL zen-mcp interactions MUST use `mcp__zen__clink` exclusively.**

See [Clink-Only Tool Usage Policy](./clink-policy.md) for complete details.

## Review System

### Code Review (Pre-Commit & Pre-PR) - MANDATORY

**When:** Before EVERY commit and before creating pull requests
**Tool:** clink + gemini codereviewer → codex codereviewer
**Models:** `gemini-2.5-pro` (1M context) + `gpt-5-codex` (synthesis)
**Duration:** ~3-5 minutes

**Purpose:**
- Comprehensive architecture review
- Trading safety validation (idempotency, circuit breakers, risk checks)
- Cross-file impact analysis
- Security and performance assessment
- Test coverage completeness
- Pattern compliance
- Documentation completeness
- Multi-perspective validation

**Phase 1 - Gemini Analysis:**
```python
mcp__zen__clink(
    prompt="""Perform comprehensive review of changes:

    Files changed: [file-list]
    Changes summary: [describe changes]

    Review focus:
    - Architecture and design patterns
    - Trading safety (idempotency, circuit breakers, risk checks, position limits)
    - Concurrency & data safety (race conditions, transactions, atomic operations)
    - Error handling (exception handling, logging, error propagation)
    - Code quality (type hints, data validation, resource cleanup, None handling)
    - Security (secrets handling, SQL injection, input validation)
    - Configuration & environment (DRY_RUN mode, no hardcoding, env variables)
    - Standards compliance (docstrings, coding standards, test coverage)
    - Domain-specific (feature parity, timezone handling, API contracts)
    - Test coverage and edge cases
    - Documentation quality
    - Performance implications

    Provide comprehensive analysis with continuation_id for follow-up.
    """,
    cli_name="gemini",
    role="codereviewer"
)
```

**Phase 2 - Codex Independent Review (fresh perspective):**
```python
mcp__zen__clink(
    prompt="""Perform fresh independent review of the same changes:

    Files changed: [file-list]
    Changes summary: [describe changes]

    Review with same comprehensive criteria:
    - Architecture and design patterns
    - Trading safety (idempotency, circuit breakers, risk checks, position limits)
    - Concurrency & data safety
    - Error handling
    - Code quality
    - Security
    - Configuration & environment
    - Standards compliance
    - Domain-specific requirements
    - Test coverage
    - Documentation
    - Performance

    Provide independent analysis with all issues categorized by severity.
    DO NOT reference or build upon previous reviews - provide fresh perspective.
    """,
    cli_name="codex",
    role="codereviewer"
)
```

**See:** [`../03-reviews.md`](../03-reviews.md)

### Task Creation Review (Pre-Work)

**When:** Before starting work on task documents (2-3 minutes)
**Tool:** clink + gemini planner
**Model:** `gemini-2.5-pro` or `gemini-2.5-flash` (planning-optimized)

**Purpose:**
- Validate task scope and requirements
- Identify missing acceptance criteria
- Prevent scope creep
- Ensure implementation clarity

**Usage:**
```python
mcp__zen__clink(
    prompt="""Review task document for implementation readiness:

    Task: [task-file-path]

    Validate:
    - Requirements completeness
    - Clear acceptance criteria
    - Scope appropriateness (not too large/small)
    - Dependencies identified
    - Edge cases considered
    - Test strategy defined

    Identify gaps or ambiguities before implementation starts.
    """,
    cli_name="gemini",
    role="planner"
)
```

**See:** [`../02-planning.md`](../02-planning.md)

## Model Selection Strategy

### Codex CLI (gpt-5-codex)

**Use for:**
- Synthesis of gemini reviews
- Implementation quality checks validation
- Final approval confirmation

**Characteristics:**
- 400K context window
- Code-specialized
- Fast (~30 seconds)
- Good for pattern matching and safety checks

### Gemini CLI (gemini-2.5-pro/flash)

**Use for:**
- Comprehensive code reviews
- Task creation planning
- Comprehensive analysis
- Multi-file impact assessment

**Characteristics:**
- 1M context window (pro)
- Planning-optimized
- Slower (2-5 minutes)
- Better for strategic thinking and cross-cutting concerns

### Model Selection is CLI-Configured

**IMPORTANT:** Model selection happens in the CLI configuration, NOT in clink parameters.

- Codex CLI automatically uses `gpt-5-codex`
- Gemini CLI uses `gemini-2.5-pro` or `gemini-2.5-flash`
- You select CLI via `cli_name` parameter ("codex" or "gemini")
- You select role via `role` parameter ("codereviewer", "planner", "default")

## Continuation ID Recording

**What:** Unique identifier returned from each independent review for audit trail purposes.

**Why:** Provides traceability and accountability - links commit to specific AI review sessions.

**Usage:**
1. Each review (Gemini and Codex) returns its own `continuation_id`
2. **DO NOT reuse continuation_ids across reviewers or iterations** - each review is independent
3. **ONLY record the final approved continuation_ids** (when both reviewers approve with zero issues)
4. Include both final IDs in commit message for audit trail

**Workflow:**
```python
# === Iteration 1 ===
# Gemini review (fresh, independent)
gemini_response_1 = mcp__zen__clink(
    prompt="""Review these changes comprehensively...""",
    cli_name="gemini",
    role="codereviewer"
)
# Returns gemini_continuation_id_1 (discard if issues found)

# Codex review (fresh, independent, NO reference to Gemini)
codex_response_1 = mcp__zen__clink(
    prompt="""Review these same changes comprehensively...""",
    cli_name="codex",
    role="codereviewer"
)
# Returns codex_continuation_id_1 (discard if issues found)

# If ANY issues found → fix and restart with fresh reviews

# === Iteration N (final) ===
# Gemini fresh review
gemini_response_final = mcp__zen__clink(...)
# Returns gemini_continuation_id_final

# Codex fresh review
codex_response_final = mcp__zen__clink(...)
# Returns codex_continuation_id_final

# If BOTH approve with ZERO issues:
# → RECORD gemini_continuation_id_final + codex_continuation_id_final
# → Include in commit message
```

## Cost Model (Subscription-Based)

### Monthly Costs
- Codex CLI subscription: $20-50/month (fixed)
- Gemini CLI tier: Free or $20/month (fixed)
- Maintenance hours: ~$300 (3 hours × $100/hr)
- **Total: $320-370/month** (predictable)

### Benefits
- No per-token charges
- Unlimited reviews within subscription
- 691% ROI vs pay-per-use API ($468/month)
- Predictable budgeting

### What This Buys
- ~100-200 code reviews/month
- ~10-20 task reviews/month
- All within fixed subscription cost

## Review Gate Enforcement

**MANDATORY Review Gates:**
1. **Pre-Commit:** NEVER skip comprehensive review before commit
2. **Pre-PR:** NEVER skip comprehensive review before PR
3. **Pre-Work:** RECOMMENDED for task documents

**Why Enforce:**
- Skipping reviews caused 7 fix commits (10-15 hours wasted)
- Skipping reviews causes architectural issues requiring major rework
- Skipping task review causes scope creep and unclear requirements

**Process Violation Warning:**
Committing without review gates is the PRIMARY root cause of multiple fix commits and wasted time. NEVER skip reviews regardless of urgency.

## Available Roles

**codereviewer:**
- Comprehensive safety and quality validation
- Pattern compliance checking
- Test coverage assessment
- Architecture review
- Used for code reviews

**planner:**
- Scope validation
- Requirements analysis
- Strategic planning
- Used for task reviews

**default:**
- General-purpose interactions
- Not typically used in formal reviews

## See Also

- [Clink-Only Tool Usage Policy](./clink-policy.md) - Tool restriction details
- [Review Workflow](../03-reviews.md) - Complete step-by-step process
- [Task Creation Review](../02-planning.md) - Task review workflow
- [CLAUDE.md Zen-MCP Integration](/CLAUDE.md#zen-mcp--clink-integration) - Complete policy
- [Troubleshooting Guide](../troubleshooting.md) - Error resolution
