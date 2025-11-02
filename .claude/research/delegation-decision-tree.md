# Delegation Decision Tree
**Phase:** P1T13-F3 Component 1
**Date:** 2025-11-01
**Duration:** 1 hour
**Status:** COMPLETED

---

## Purpose

Provide clear, actionable criteria for when to delegate tasks to subagents vs. keeping in main context during AI coding workflows.

---

## Quick Decision Flowchart

```
┌──────────────────────────────────┐
│ Task needs to be performed       │
└───────────┬──────────────────────┘
            │
            ▼
   ┌────────────────────┐
   │ Is this a CORE     │───YES──→ ┌─────────────────────┐
   │ task?              │          │ KEEP IN MAIN CONTEXT│
   └────────┬───────────┘          └─────────────────────┘
            │                              ▲
           NO                              │
            │                              │
            ▼                              │
   ┌────────────────────┐                 │
   │ Is this a known    │───YES───────────┘
   │ specific target?   │        (Use Read/Glob/Grep)
   └────────┬───────────┘
            │
           NO
            │
            ▼
   ┌────────────────────┐
   │ Is this SEARCH or  │───YES──→ ┌───────────────────────┐
   │ ANALYSIS heavy?    │          │ Understanding WHAT     │
   └────────┬───────────┘          │ exists or HOW to impl? │
            │                      └───────┬───────────────┘
           NO                              │
            │                      ┌───────┴────────┐
            │                      │                │
            ▼                     WHAT             HOW
   ┌────────────────────┐          │                │
   │ Does this need     │          ▼                ▼
   │ MULTI-TURN context?│   ┌──────────┐    ┌──────────┐
   └────────┬───────────┘   │ DELEGATE │    │   KEEP   │
            │               │   Task   │    │   MAIN   │
           NO               └──────────┘    └──────────┘
            │                      ▲
            │                      │
            ▼                      │
   ┌────────────────────┐         │
   │ Does this need     │───YES───┘
   │ MULTI-TURN context?│        (Use Zen-MCP Clink)
   └────────┬───────────┘
            │
           NO
            │
            ▼
   ┌────────────────────┐
   │ KEEP IN MAIN       │
   │ (Default: err on   │
   │  side of keeping)  │
   └────────────────────┘
```

**Key Decision: WHAT vs HOW?**
- **WHAT exists** → DELEGATE: "Find all call sites", "List test files", "Search for patterns"
- **HOW to implement** → KEEP IN MAIN: "Break into components", "Plan error handling", "Identify edge cases"

---

## Decision Categories

### Category 1: ALWAYS Keep in Main Context (Core Tasks)

These tasks are **NEVER delegated** because they require full context continuity, user interaction, or critical decision-making:

| Task Type | Keep in Main? | Reason | Example |
|-----------|---------------|--------|---------|
| **Task planning** | ✅ YES | Core decision-making, requires requirements analysis | Breaking P1T13-F3 into components |
| **Requirements analysis** | ✅ YES | Understanding "why", not just "what" | Analyzing task acceptance criteria |
| **Architecture decisions** | ✅ YES | Strategic choices with long-term impact | Choosing hybrid delegation approach |
| **Core implementation** | ✅ YES | Primary deliverable, needs continuity | Writing `multi_alpha_allocator.py` |
| **Component breakdown** | ✅ YES | Understanding "how" to implement, strategic decomposition | Breaking feature into 5-step checklist components |
| **Edge case generation** | ✅ YES | Requires deep understanding of implementation strategy | Identifying failure scenarios for new feature |
| **Commit creation** | ✅ YES | Critical operation, needs full history | Creating feature commit with zen-review ID |
| **PR creation** | ✅ YES | Critical operation, full branch context needed | Creating PR with summary |
| **User interaction** | ✅ YES | Direct communication, clarification | Answering user questions, AskUserQuestion |
| **Review coordination** | ✅ YES | Orchestrating quality gates | Requesting zen-mcp review, checking status |

**Rule:** If task failure would corrupt session state or lose critical context, **KEEP IN MAIN**.

**CRITICAL DISTINCTION - "WHAT" vs "HOW":**
- **Delegate "WHAT" tasks** (discovering what exists): Finding call sites, searching for patterns, listing test files
- **Keep "HOW" tasks** (understanding how to implement): Designing components, planning error handling, identifying edge cases

---

### Category 2: ALWAYS Delegate (Non-Core, High-Token Tasks)

These tasks are **ALWAYS delegated** because they consume disproportionate context with tangential information:

| Task Type | Delegate to | Reason | Example | Token Savings |
|-----------|-------------|--------|---------|---------------|
| **Open-ended file search** | `Task` (Explore) | May require multiple search rounds, isolated context | "Find all circuit breaker call sites" | 15-25k → 2-3k |
| **"Where is X?" questions** | `Task` (Explore) | Codebase exploration specialist | "Where are errors from client handled?" | 20-30k → 3-4k |
| **Multi-file pattern search** | `Task` (Explore) | Search-heavy, pattern extraction | "Find all uses of @retry decorator" | 18-25k → 2-3k |
| **CI log analysis** | `Task` (general-purpose) | Large logs, extract failure patterns | Parsing 5000-line pytest output | 25-35k → 4-5k |
| **PR comment extraction** | `Task` (general-purpose) | Structured data extraction | Fetching inline+review+issue comments | 10-15k → 2-3k |
| **Large doc lookups** | `Task` (Explore) | Reference docs, non-critical | "What does Alpaca API support for TWAP?" | 12-18k → 2-3k |

**Rule:** If task consumes >10k tokens but only needs 2-5k summary, **DELEGATE**.

---

### Category 3: CONDITIONAL Delegation (Context-Dependent)

These tasks require judgment based on current context utilization and criticality:

| Task Type | Delegate? | Decision Criteria | Example (Delegate) | Example (Keep) |
|-----------|-----------|-------------------|-------------------|----------------|
| **Test execution** | ⚠️ MAYBE | If logs >15k tokens AND test results can be summarized | Delegating 80-test suite run | Keep 3-test focused run |
| **Doc lookup** | ⚠️ MAYBE | If reference docs (non-critical) | Delegating Grafana dashboard docs | Keep trading_platform_realization_plan.md |
| **Code analysis** | ⚠️ MAYBE | If understanding context, not implementing | Delegating "How does reconciler work?" | Keep implementation analysis |
| **Specific file read** | ❌ NO (use Read) | Known path, direct access | Read CLAUDE.md | N/A |
| **Test log analysis** | ⚠️ MAYBE | If logs >10k AND failures <5 | Delegating 2000-line log | Keep 50-line focused log |

**Decision Rule:**
```python
if token_cost > 10000 and output_summary_fits_in < 5000:
    DELEGATE
elif task_is_critical_for_implementation:
    KEEP_IN_MAIN
else:
    # Default: err on side of keeping for simplicity
    KEEP_IN_MAIN
```

---

### Category 4: NEVER Delegate (Use Direct Tools)

These tasks have specialized tools that are faster than delegation:

| Task Type | Use Instead | Reason | Example |
|-----------|-------------|--------|---------|
| **Specific file read** | `Read` tool | Direct access faster | Reading `CLAUDE.md` |
| **Specific class search** | `Glob` tool | Pattern match faster | Finding `class MultiAlphaAllocator` |
| **Search within 2-3 files** | `Read` tool | Direct read faster | Searching within known file |
| **Simple grep** | `Grep` tool | Direct search faster | Finding `def allocate(` |

**Rule:** If you know the **exact path** or **exact pattern**, use direct tools (Read/Glob/Grep).

---

## Delegation Patterns by Use Case

### Use Case 1: Pre-Implementation Analysis

**Context:** Following `.claude/workflows/00-analysis-checklist.md` to find ALL impacted components.

**Without Delegation (Current):**
```python
# Step 2: Identify ALL impacted components (15 min)
grep -rn "function_name(" apps/ libs/ tests/  # 20k tokens of results
grep -rn "from module import" apps/ libs/     # 15k tokens of results
# ... manual filtering ...
# Total: 35k tokens for analysis phase
```

**With Delegation (Optimized):**
```python
# Delegate search to Explore subagent
Task(
    description="Find all call sites for function_name",
    prompt="""Search apps/, libs/, tests/ for function_name() usage.

    Deliverable: Return list of file:line references grouped by:
    - Direct call sites
    - Import statements
    - Test files

    Format: {"calls": [...], "imports": [...], "tests": [...]}
    """,
    subagent_type="Explore"
)
# Returns: 3-4k token summary
# Savings: 35k → 4k = 31k tokens (89% reduction)
```

**Decision:** ✅ DELEGATE (open-ended search, high token cost)

---

### Use Case 2: Debugging Workflow

**Context:** Following `.claude/workflows/06-debugging.md` to trace error source.

**Without Delegation:**
```python
# Read 10 potentially related files (200 lines each)
# = 10 × 2k = 20k tokens
# Extract stack trace patterns manually
# = 5k tokens
# Total: 25k tokens
```

**With Delegation:**
```python
# Delegate codebase analysis
Task(
    description="Trace error source from stack trace",
    prompt="""Given stack trace:

    File "apps/risk_manager/monitor.py", line 142, in check_limits
        position = redis.get(f"pos:{symbol}")
    TypeError: unsupported operand type(s) for +: 'NoneType' and 'int'

    Trace the call chain backwards to find where position is set to None.

    Deliverable:
    - Call chain (file:line references)
    - Likely root cause
    - Related code patterns
    """,
    subagent_type="Explore"
)
# Returns: 4-5k token summary with call chain
# Savings: 25k → 5k = 20k tokens (80% reduction)
```

**Decision:** ✅ DELEGATE (multi-file analysis, pattern extraction)

---

### Use Case 3: Zen-MCP Review Coordination

**Context:** Following `.claude/workflows/03-zen-review-quick.md` for pre-commit review.

**Current (Correct):**
```python
# DO NOT delegate review execution to Task tool
# Use zen-mcp clink (already implemented)

mcp__zen-mcp__clink(
    cli_name="codex",
    role="codereviewer",
    prompt="""Review these staged changes for:
    - Trading safety (circuit breaker checks, idempotency)
    - Test coverage gaps
    - Pattern parity violations

    Files: libs/allocation/multi_alpha.py, tests/libs/allocation/test_multi_alpha.py
    """,
    absolute_file_paths=[
        "/path/to/libs/allocation/multi_alpha.py",
        "/path/to/tests/libs/allocation/test_multi_alpha.py"
    ],
    working_directory_absolute_path="/path/to/trading_platform"
)
# Returns: review results with continuation_id
# Main context: 5k tokens (review coordination)
```

**Decision:** ✅ Use **Zen-MCP Clink** (NOT Task tool) for reviews. Zen-mcp supports multi-turn conversations and continuation_id for context preservation.

---

### Use Case 4: PR Comment Processing

**Context:** Phase 5 automation - reading and addressing PR review comments.

**Without Delegation:**
```python
# Fetch ALL PR comments (inline + review + issue)
gh api repos/owner/repo/pulls/123/comments  # 15k tokens
gh pr view 123 --json reviews                # 12k tokens
# Parse each comment manually
# = 8k tokens processing
# Total: 35k tokens
```

**With Delegation:**
```python
# Delegate comment extraction + parsing
Task(
    description="Extract actionable PR comments",
    prompt="""Fetch all comments for PR #123 and categorize:

    Use:
    - gh api repos/owner/repo/pulls/123/comments (inline)
    - gh pr view 123 --json reviews (review comments)
    - gh api repos/owner/repo/issues/123/comments (issue comments)

    Deliverable: JSON structure:
    {
      "actionable": [
        {"file": "...", "line": 42, "comment": "...", "type": "fix_required"},
        ...
      ],
      "questions": [...],
      "approvals": [...]
    }

    Filter out: non-actionable comments, thumbs-up reactions, "LGTM" only.
    """,
    subagent_type="general-purpose"
)
# Returns: 4-5k token structured summary
# Savings: 35k → 5k = 30k tokens (86% reduction)
```

**Decision:** ✅ DELEGATE (structured data extraction, high token cost)

---

## Context Slice Format (What to Pass to Subagent)

When delegating, provide **minimal but sufficient** context:

### Template for Task (Explore)

```python
Task(
    description="<5-word summary>",  # e.g., "Find circuit breaker implementations"
    prompt="""<Detailed autonomous task description>

    Context (minimal):
    - Repository: /path/to/trading_platform
    - Focus areas: apps/, libs/, tests/
    - Exclude: data/, artifacts/, docs/

    Task:
    <Specific instructions>

    Deliverable:
    <Exact format for return value>
    - Use JSON if structured
    - Use file:line if references
    - NO full code blocks (summaries only)

    Constraints:
    - Token budget: <5000 tokens for return
    - Timeout: <2 min
    """,
    subagent_type="Explore"  # or "general-purpose"
)
```

### Template for Zen-MCP Clink

```python
mcp__zen-mcp__clink(
    cli_name="codex",  # or "gemini"
    role="codereviewer",  # or "planner" or "default"
    prompt="""<Review request with specific focus areas>

    Files under review:
    <List files>

    Review focus:
    - <Specific concern 1>
    - <Specific concern 2>
    """,
    absolute_file_paths=[...],  # Full paths
    continuation_id="<previous-id>"  # If multi-turn
)
```

**Key Principles:**
1. **Minimal context**: Only what's needed for the task
2. **Explicit deliverable format**: JSON structure, file:line list, summary length
3. **Constraints**: Token budget, timeout, exclusions
4. **No full code**: Summaries and references only

---

## Anti-Patterns (What NOT to Delegate)

### ❌ Anti-Pattern 1: Delegating Core Planning

**Wrong:**
```python
Task(
    description="Plan P1T13-F3 implementation",
    prompt="Read P1T13_F3_AUTOMATION.md and create component breakdown",
    subagent_type="general-purpose"
)
```

**Why Wrong:** Planning is CORE decision-making requiring full context and user interaction.

**Correct:** Keep planning in main context, use existing workflows (`.claude/workflows/00-analysis-checklist.md`).

---

### ❌ Anti-Pattern 2: Delegating Known File Reads

**Wrong:**
```python
Task(
    description="Read CLAUDE.md",
    prompt="Read /path/to/CLAUDE.md and return contents",
    subagent_type="Explore"
)
```

**Why Wrong:** Known path, direct `Read` tool is faster and more efficient.

**Correct:**
```python
Read(file_path="/path/to/CLAUDE.md")
```

---

### ❌ Anti-Pattern 3: Delegating Review Execution to Task Tool

**Wrong:**
```python
Task(
    description="Review my code",
    prompt="Review libs/allocation/multi_alpha.py for safety issues",
    subagent_type="general-purpose"
)
```

**Why Wrong:**
- Zen-MCP clink provides multi-turn conversation, continuation_id
- Task tool is stateless (single message return)
- Review quality inferior without multi-turn context

**Correct:** Use `mcp__zen-mcp__clink` with appropriate CLI and role.

---

## Measurement & Validation

### How to Measure Context Optimization

**Baseline (Without Delegation):**
1. Perform sample task (e.g., "implement position limit validation")
2. Track token usage at each step:
   - File searches: ___ tokens
   - Doc lookups: ___ tokens
   - Implementation: ___ tokens
   - Test logs: ___ tokens
   - Total: ___ tokens

**Optimized (With Delegation):**
1. Perform same task with delegation
2. Track token usage:
   - File search summaries: ___ tokens (delegated)
   - Doc lookup summaries: ___ tokens (delegated)
   - Implementation: ___ tokens (same)
   - Test result summaries: ___ tokens (delegated)
   - Total: ___ tokens

**Calculate:**
```
Optimization % = (Baseline - Optimized) / Baseline × 100
Target: ≥30%
Projected: ~38% (based on research analysis)
```

---

## Decision Tree Summary

**Quick Reference Card:**

| If task is... | Then... | Tool |
|---------------|---------|------|
| 🎯 **Core planning, architecture, implementation** | KEEP IN MAIN | (main context) |
| 🔍 **Open-ended search ("where is X?")** | DELEGATE | `Task` (Explore) |
| 📊 **CI log analysis, PR comments** | DELEGATE | `Task` (general-purpose) |
| 🔬 **Code review** | DELEGATE | `mcp__zen-mcp__clink` |
| 📖 **Known file path** | USE DIRECT TOOL | `Read` |
| 🔎 **Known pattern** | USE DIRECT TOOL | `Glob` or `Grep` |
| ❓ **Unsure** | DEFAULT: KEEP IN MAIN | (main context) |

**Golden Rule:** When in doubt, **keep in main context**. Only delegate when clear token savings (>10k → <5k) and non-critical.

---

**Next:** Implement delegation pattern (Task 3)
