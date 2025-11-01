# Zen-MCP Review Process

**Purpose:** Three-tier review system using zen-mcp with clink for AI-assisted code quality assurance.

## Critical: Clink-Only Policy

**⚠️ MANDATORY: ALL zen-mcp interactions MUST use `mcp__zen-mcp__clink` exclusively.**

See [Clink-Only Tool Usage Policy](./clink-policy.md) for complete details.

## Three-Tier Review System

### Tier 1: Quick Review (Pre-Commit) - MANDATORY

**When:** Before EVERY commit (~30 seconds)
**Tool:** clink + codex codereviewer
**Model:** `gpt-5-codex` (400K context, code-specialized)

**Purpose:**
- Safety checks for trading logic
- Idempotency verification
- Test coverage validation
- Pattern compliance

**Usage:**
```python
mcp__zen-mcp__clink(
    prompt="""Review staged changes for:
    - Trading safety (idempotency, circuit breakers, risk checks)
    - Test coverage completeness
    - Pattern parity with existing code
    - No regressions introduced

    Files changed:
    [List files]

    Changes summary:
    [Describe changes]
    """,
    cli_name="codex",
    role="codereviewer"
)
```

**See:** [`.claude/workflows/03-zen-review-quick.md`](../03-zen-review-quick.md)

### Tier 2: Deep Review (Pre-PR) - MANDATORY

**When:** Before creating ANY pull request (3-5 minutes)
**Tool:** clink + gemini codereviewer → codex codereviewer
**Models:** `gemini-2.5-pro` (1M context) + `gpt-5-codex` (synthesis)

**Purpose:**
- Comprehensive architecture review
- Cross-file impact analysis
- Security and performance assessment
- Documentation completeness
- Multi-perspective validation

**Phase 1 - Gemini Analysis:**
```python
mcp__zen-mcp__clink(
    prompt="""Perform deep review of feature branch:

    Branch: [branch-name]
    Commits: [commit-count] commits
    Files changed: [file-list]

    Review focus:
    - Architecture and design patterns
    - Trading safety across all components
    - Test coverage and edge cases
    - Documentation quality
    - Performance implications
    - Security considerations

    Provide comprehensive analysis with continuation_id for follow-up.
    """,
    cli_name="gemini",
    role="codereviewer"
)
```

**Phase 2 - Codex Synthesis (reuse continuation_id):**
```python
mcp__zen-mcp__clink(
    prompt="""Synthesize recommendations from gemini review:

    continuation_id: [from-gemini-response]

    Provide:
    - Prioritized action items
    - Critical vs nice-to-have fixes
    - Next steps for PR readiness
    """,
    cli_name="codex",
    role="codereviewer",
    continuation_id="[from-gemini-response]"
)
```

**See:** [`.claude/workflows/04-zen-review-deep.md`](../04-zen-review-deep.md)

### Tier 3: Task Creation Review (Pre-Work)

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
mcp__zen-mcp__clink(
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

**See:** [`.claude/workflows/13-task-creation-review.md`](../13-task-creation-review.md)

## Model Selection Strategy

### Codex CLI (gpt-5-codex)

**Use for:**
- Quick safety reviews (Tier 1)
- Pre-commit validation
- Implementation quality checks
- Synthesis of multi-model reviews

**Characteristics:**
- 400K context window
- Code-specialized
- Fast (~30 seconds)
- Good for pattern matching and safety checks

### Gemini CLI (gemini-2.5-pro/flash)

**Use for:**
- Deep architecture reviews (Tier 2)
- Task creation planning (Tier 3)
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

## Continuation ID Preservation

**What:** Unique identifier preserving conversation context across multiple turns.

**Why:** Enables multi-turn conversations (up to 49 exchanges) without losing context.

**Usage:**
1. First call: Returns `continuation_id` in response
2. Subsequent calls: Pass same `continuation_id` to preserve context
3. Works across different CLIs (gemini → codex handoff)

**Example:**
```python
# Phase 1: Gemini analysis
response1 = mcp__zen-mcp__clink(
    prompt="Analyze this feature...",
    cli_name="gemini",
    role="codereviewer"
)
# Extract continuation_id from response1

# Phase 2: Codex synthesis (reuses context)
response2 = mcp__zen-mcp__clink(
    prompt="Synthesize recommendations...",
    cli_name="codex",
    role="codereviewer",
    continuation_id="[from-response1]"
)
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
- ~100-200 quick reviews/month (Tier 1)
- ~20-40 deep reviews/month (Tier 2)
- ~10-20 task reviews/month (Tier 3)
- All within fixed subscription cost

## Review Gate Enforcement

**MANDATORY Review Gates:**
1. **Pre-Commit (Tier 1):** NEVER skip quick review before commit
2. **Pre-PR (Tier 2):** NEVER skip deep review before PR
3. **Pre-Work (Tier 3):** RECOMMENDED for task documents

**Why Enforce:**
- Skipping Tier 1 caused 7 fix commits (10-15 hours wasted)
- Skipping Tier 2 causes architectural issues requiring major rework
- Skipping Tier 3 causes scope creep and unclear requirements

**Process Violation Warning:**
Committing without review gates is the PRIMARY root cause of multiple fix commits and wasted time. NEVER skip reviews regardless of urgency.

## Available Roles

**codereviewer:**
- Safety and quality validation
- Pattern compliance checking
- Test coverage assessment
- Used in Tier 1 and Tier 2 reviews

**planner:**
- Scope validation
- Requirements analysis
- Strategic planning
- Used in Tier 3 task reviews

**default:**
- General-purpose interactions
- Not typically used in formal reviews

## See Also

- [Clink-Only Tool Usage Policy](./clink-policy.md) - Tool restriction details
- [Quick Review Workflow](../03-zen-review-quick.md) - Tier 1 step-by-step
- [Deep Review Workflow](../04-zen-review-deep.md) - Tier 2 step-by-step
- [Task Creation Review](../13-task-creation-review.md) - Tier 3 step-by-step
- [CLAUDE.md Zen-MCP Integration](/CLAUDE.md#zen-mcp--clink-integration) - Complete policy
- [Troubleshooting Guide](/.claude/TROUBLESHOOTING.md) - Error resolution
