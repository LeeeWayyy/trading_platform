# Subagent Delegation Workflow

**Purpose:** Guide AI assistant on when and how to delegate non-core tasks to isolated subagents for context optimization
**When:** During any workflow when context pollution risk is high (>100k tokens used)
**Prerequisites:** Understanding of task delegation decision tree
**Expected Outcome:** 30-40% context usage reduction, longer uninterrupted coding sessions

---

## Quick Reference

**When to use this workflow:**
- Context usage >50% (100k+ of 200k tokens)
- About to perform file search across large codebase
- Processing CI logs >10k tokens
- Extracting PR comments
- Analyzing test failures
- Non-critical doc lookups

**Three Delegation Tools:**
1. **Task (Explore)** — File searches, codebase exploration ("where is X?")
2. **Task (general-purpose)** — CI analysis, PR comments, structured extraction
3. **Zen-MCP Clink** — Code reviews (already implemented, multi-turn)

---

## Delegation Decision Quick Check

```bash
# Before delegating, ask:

1. Is this a CORE task?
   YES → KEEP IN MAIN (planning, implementation, commits)
   NO  → Continue...

2. Is this a KNOWN target?
   YES → USE DIRECT TOOL (Read/Glob/Grep)
   NO  → Continue...

3. Is this SEARCH/ANALYSIS heavy (>10k tokens)?
   YES → DELEGATE to Task
   NO  → KEEP IN MAIN (default)
```

---

## Context Checkpoint Before Delegation

**⚠️ IMPORTANT:** Before delegating to subagents, create a checkpoint to preserve current context state. This enables restoration if delegation introduces issues or context corruption.

**When to create checkpoints:**
- Before any Task (Explore) delegation
- Before any Task (general-purpose) delegation
- Before zen-mcp clink multi-turn conversations
- When context usage >50% (100k+ tokens)

**How to create checkpoint:**
```bash
# Create delegation checkpoint before using Task tool
./scripts/context_checkpoint.py create --type delegation

# Output example:
# ✓ Created delegation checkpoint: a1b2c3d4-e5f6-7890-abcd-ef1234567890
# File: .claude/checkpoints/a1b2c3d4-e5f6-7890-abcd-ef1234567890.json
# Symlink: .claude/checkpoints/latest_delegation.json
```

**Checkpoint captures:**
- Current task state (.claude/task-state.json)
- Workflow state (.claude/workflow-state.json)
- Git state (branch, commit, staged files)
- Token usage estimate (if available)
- Critical findings and pending decisions

**Restoration (if needed):**
```bash
# Restore from specific checkpoint
./scripts/context_checkpoint.py restore --id a1b2c3d4-...

# Or restore from latest delegation checkpoint
LATEST_ID=$(basename $(readlink .claude/checkpoints/latest_delegation.json) .json)
./scripts/context_checkpoint.py restore --id $LATEST_ID
```

---

## Pattern 1: File Search Delegation (Task Explore)

**Use When:**
- Open-ended file search ("where is X implemented?")
- Multi-file pattern search
- Codebase exploration requiring multiple search rounds

**Example: Finding Circuit Breaker Call Sites**

**Before (No Delegation):**
```python
# Main context gets polluted with full search results
grep_result = Grep(
    pattern="check_circuit_breaker",
    path="$PROJECT_ROOT",
    output_mode="content",
    -n=True
)
# Returns: 20k-30k tokens of file contents with line numbers
# Context pollution: HIGH
```

**After (With Delegation + Checkpoint):**
```bash
# Step 1: Create checkpoint before delegation
./scripts/context_checkpoint.py create --type delegation
```

```python
# Step 2: Delegate to Explore subagent with isolated 200k context
search_results = Task(
    description="Find circuit breaker call sites",
    prompt="""Search the trading platform codebase for all occurrences of check_circuit_breaker().

Repository: $PROJECT_ROOT
Focus areas: apps/, libs/, tests/
Exclude: data/, artifacts/, docs/

Task: Find all direct call sites of check_circuit_breaker() function.

Deliverable (JSON format):
{
  "call_sites": [
    {"file": "apps/execution_gateway/order_placer.py", "line": 42, "context": "if check_circuit_breaker():"},
    ...
  ],
  "import_sites": [
    {"file": "apps/risk_manager/monitor.py", "line": 8"},
    ...
  ],
  "test_sites": [...]
}

Constraints:
- Return file:line references ONLY (no full code blocks)
- Token budget: <5000 tokens
- Timeout: 2 minutes
""",
    subagent_type="Explore"
)
# Returns: 3-5k token JSON summary
# Context savings: 20k → 4k = 16k tokens (80% reduction)
```

**Key Points:**
- ✅ Provide minimal context (repo path, focus areas, exclusions)
- ✅ Specify exact deliverable format (JSON structure)
- ✅ Set constraints (token budget, timeout)
- ✅ Request summaries/references, NOT full code

---

## Pattern 2: CI Log Analysis (Task general-purpose)

**Use When:**
- Test failures with >10k token logs
- CI failures requiring pattern extraction
- Large output needing summarization

**Example: Analyzing pytest Failure Logs**

**Before (No Delegation):**
```python
# Run tests and capture full output
bash_result = Bash(
    command="make test ARGS='tests/libs/allocation/ -v'",
    description="Run allocation tests"
)
# Returns: 25k-40k tokens of test output
# Manual parsing required
# Context pollution: VERY HIGH
```

**After (With Delegation + Checkpoint):**
```bash
# Step 1: Create checkpoint before delegation
./scripts/context_checkpoint.py create --type delegation
```

```python
# Step 2: Delegate log analysis to subagent
test_analysis = Task(
    description="Analyze pytest failure logs",
    prompt="""Run tests and analyze failures:

Command: make test ARGS='tests/libs/allocation/ -v'
Working directory: $PROJECT_ROOT

Task: Execute tests and extract failure information.

Deliverable (JSON format):
{
  "summary": {
    "total_tests": 80,
    "passed": 75,
    "failed": 5,
    "errors": 0
  },
  "failures": [
    {
      "test": "test_inverse_vol_weight_calculation",
      "file": "tests/libs/allocation/test_multi_alpha.py:142",
      "error_type": "AssertionError",
      "message": "Expected 0.6, got 0.4",
      "likely_cause": "Inverse volatility calculation incorrect"
    },
    ...
  ],
  "recommendations": [
    "Check inverse volatility weighting formula",
    "Verify strategy stats are populated correctly"
  ]
}

Constraints:
- DO NOT include full stack traces (error message only)
- Token budget: <6000 tokens
- Timeout: 3 minutes
""",
    subagent_type="general-purpose"
)
# Returns: 5-6k token structured analysis
# Context savings: 35k → 6k = 29k tokens (83% reduction)
```

---

## Pattern 3: PR Comment Extraction (Task general-purpose)

**Use When:**
- Automating PR comment addressing (Phase 5)
- Extracting actionable feedback from reviews
- Filtering comments (inline + review + issue)

**Example: Extracting Actionable PR Comments**

**Without Delegation:**
```python
# Fetch all comment types manually
inline = Bash(command="gh api repos/owner/repo/pulls/123/comments")  # 12k tokens
reviews = Bash(command="gh pr view 123 --json reviews")  # 15k tokens
issues = Bash(command="gh api repos/owner/repo/issues/123/comments")  # 8k tokens
# Manual parsing and filtering required
# Total: 35k tokens
```

**With Delegation + Checkpoint:**
```bash
# Step 1: Create checkpoint before delegation
./scripts/context_checkpoint.py create --type delegation
```

```python
# Step 2: Delegate comment extraction + parsing
pr_comments = Task(
    description="Extract PR review comments",
    prompt="""Fetch and categorize all comments for PR #123.

Repository: $PROJECT_ROOT
PR number: 123

Task: Use gh CLI to fetch all comment types and categorize:

Commands to run:
1. gh api repos/owner/repo/pulls/123/comments (inline comments)
2. gh pr view 123 --json reviews (review comments)
3. gh api repos/owner/repo/issues/123/comments (issue comments)

Deliverable (JSON format):
{
  "actionable": [
    {
      "type": "inline",
      "file": "libs/allocation/multi_alpha.py",
      "line": 142,
      "reviewer": "@gemini-code-assist",
      "comment": "Missing circuit breaker check before order submission",
      "severity": "high"
    },
    ...
  ],
  "questions": [
    {"file": "...", "line": ..., "comment": "Why use inverse volatility instead of equal weight?"},
    ...
  ],
  "approvals": [
    {"reviewer": "@codex", "comment": "LGTM - all safety checks in place"},
    ...
  ],
  "stats": {
    "total_comments": 15,
    "actionable": 8,
    "questions": 4,
    "approvals": 3
  }
}

Filtering rules:
- Exclude: non-actionable ("👍", "LGTM" only, "Thanks")
- Include: specific code change requests, bug reports, security concerns
- Categorize by severity: high (safety/security), medium (quality), low (style)

Constraints:
- Token budget: <5000 tokens
- Timeout: 2 minutes
""",
    subagent_type="general-purpose"
)
# Returns: 4-5k token structured summary
# Context savings: 35k → 5k = 30k tokens (86% reduction)
```

---

## Pattern 4: Code Review Delegation (Zen-MCP Clink)

**Use When:**
- Pre-commit quick review (Tier 1)
- Pre-PR deep review (Tier 2)
- Task creation review (Tier 3)

**Example: Quick Pre-Commit Review**

**Implementation:**
```python
# Use zen-mcp clink (NOT Task tool)
review_result = mcp__zen__clink(
    cli_name="codex",
    role="codereviewer",
    prompt="""Review these staged changes for P1T13-F3 Phase 1:

Files:
- .claude/research/subagent-capabilities-research.md (NEW, 350 lines)
- .claude/research/delegation-decision-tree.md (NEW, 480 lines)
- .claude/workflows/16-subagent-delegation.md (NEW, 420 lines)

Review focus:
1. **Accuracy:** Are delegation patterns correct? Do they match Task tool capabilities?
2. **Completeness:** Are all decision categories covered? Missing edge cases?
3. **Clarity:** Are examples clear? Decision tree actionable?
4. **Integration:** Do patterns align with existing workflows (00-analysis, 06-debugging)?
5. **Safety:** Any delegation patterns that could corrupt main context?

Project context:
- P1T13-F3 Component 1: Context Optimization via Subagent Delegation
- Goal: 30%+ context usage reduction
- Hybrid approach: Task (Explore/general-purpose) + Zen-MCP Clink

Deliverable:
- Approval status: APPROVED / NEEDS REVISION
- Blocking issues (if any)
- Suggestions for improvement
- Continuation ID for follow-up
""",
    absolute_file_paths=[
        "$PROJECT_ROOT/.claude/research/subagent-capabilities-research.md",
        "$PROJECT_ROOT/.claude/research/delegation-decision-tree.md",
        "$PROJECT_ROOT/.claude/workflows/16-subagent-delegation.md"
    ],
    working_directory_absolute_path="$PROJECT_ROOT"
)
# Returns: Review results with continuation_id
# Main context: 5-6k tokens (coordination only)
```

**Why Zen-MCP Clink (NOT Task tool) for reviews:**
- ✅ Multi-turn conversation support (continuation_id)
- ✅ Context preservation across review cycles
- ✅ Higher quality reviews (CLI tools optimized for code review)
- ❌ Task tool is stateless (single message return, no follow-up)

---

## Pattern 5: Documentation Lookup (Conditional Delegation)

**Use When:**
- Reference docs (non-critical)
- Large docs (>10k tokens)
- Multiple doc sources

**Don't Delegate:**
- Critical task docs (CLAUDE.md, P1TXX_TASK.md)
- Small docs (<5k tokens)

**Example: Alpaca API TWAP Support Lookup**

**With Delegation (Reference Doc):**
```python
# Delegate non-critical API doc lookup
api_support = Task(
    description="Check Alpaca TWAP support",
    prompt="""Research Alpaca Trading API documentation for TWAP order support.

Sources to check:
1. Alpaca Docs: https://alpaca.markets/docs/trading/orders
2. GitHub: https://github.com/alpacahq/alpaca-trade-api-python

Task: Determine if Alpaca supports native TWAP orders or if we need custom slicing.

Deliverable (JSON format):
{
  "native_support": true/false,
  "twap_parameters": ["start_time", "end_time", "..."] or null,
  "custom_slicing_required": true/false,
  "reference_urls": ["https://...", "..."],
  "summary": "<2 sentence summary>"
}

Constraints:
- Token budget: <3000 tokens
- Timeout: 2 minutes
""",
    subagent_type="Explore"
)
# Returns: 2-3k token summary
```

**Without Delegation (Critical Doc):**
```python
# Keep critical project docs in main context
Read(file_path="$PROJECT_ROOT/CLAUDE.md")
# Reason: Core workflow guidance, high reference frequency, critical for decisions
```

---

## Integration with Existing Workflows

### 00-analysis-checklist.md Integration

**Step 2: Identify ALL Impacted Components (15 min)**

**Before:**
```bash
# Manual searches pollute context
grep -rn "function_name(" apps/ libs/ tests/  # 20k tokens
grep -rn "from module import" apps/ libs/     # 15k tokens
```

**After (With Delegation):**
```python
# Delegate component search
components = Task(
    description="Find impacted components",
    prompt="""Find all impacted components for function signature change.

Function: allocate() in libs/allocation/multi_alpha.py
Change: Adding new parameter `correlation_cap: float`

Task: Find ALL:
1. Direct call sites of allocate()
2. Import statements
3. Test files using allocate()
4. Similar patterns (other allocator classes)

Deliverable: Grouped file:line references with categorization.

Constraints: <5000 tokens
""",
    subagent_type="Explore"
)
# Savings: 35k → 5k = 30k tokens
```

---

### 06-debugging.md Integration

**Step: Trace Error Source**

**Before:**
```python
# Read 10 potentially related files
for file in potential_files:
    Read(file_path=file)  # 2k × 10 = 20k tokens
```

**After (With Delegation):**
```python
# Delegate error tracing
error_trace = Task(
    description="Trace TypeError source",
    prompt="""Trace error source from stack trace:

Stack trace:
File "apps/risk_manager/monitor.py", line 142, in check_limits
    position = redis.get(f"pos:{symbol}")
TypeError: unsupported operand type(s) for +: 'NoneType' and 'int'

Task: Trace call chain backwards to find where position is set to None.

Deliverable:
- Call chain (file:line references)
- Likely root cause
- Related code patterns showing similar bugs

Constraints: <6000 tokens
""",
    subagent_type="Explore"
)
# Savings: 20k → 6k = 14k tokens
```

---

## Anti-Patterns (What NOT to Do)

### ❌ Anti-Pattern 1: Delegating Core Planning

**Wrong:**
```python
Task(
    description="Plan P1T13-F3 implementation",
    prompt="Create component breakdown for F3 task",
    subagent_type="general-purpose"
)
```

**Why Wrong:** Planning is CORE, requires full context and user interaction.

**Correct:** Keep planning in main context, use `.claude/workflows/00-analysis-checklist.md`.

---

### ❌ Anti-Pattern 2: Delegating Known File Reads

**Wrong:**
```python
Task(
    description="Read CLAUDE.md",
    prompt="Read and summarize CLAUDE.md",
    subagent_type="Explore"
)
```

**Why Wrong:** Known path, `Read` tool is faster.

**Correct:**
```python
Read(file_path="$PROJECT_ROOT/CLAUDE.md")
```

---

### ❌ Anti-Pattern 3: Using Task for Code Reviews

**Wrong:**
```python
Task(
    description="Review my code",
    prompt="Review libs/allocation/multi_alpha.py for safety",
    subagent_type="general-purpose"
)
```

**Why Wrong:** Task tool is stateless, no multi-turn conversation.

**Correct:** Use `mcp__zen__clink` with `role="codereviewer"`.

---

## Context Monitoring & When to Delegate

**Check context usage periodically:**

```python
# Estimate: Each tool call consumes ~2-5k tokens baseline
# File reads: 2-10k depending on size
# Search results: 10-40k depending on breadth

# Rule of thumb:
# - At 100k tokens (50% utilization): START delegating non-core tasks
# - At 150k tokens (75% utilization): AGGRESSIVELY delegate everything possible
# - At 180k tokens (90% utilization): STOP, request user session continuation
```

**Proactive Delegation Triggers:**

| Context Usage | Action |
|---------------|--------|
| <100k (50%) | No delegation needed, proceed normally |
| 100-150k (50-75%) | Delegate file searches, CI logs, PR comments |
| 150-180k (75-90%) | Delegate ALL non-core tasks, minimize file reads |
| >180k (>90%) | CRITICAL: Request user to continue session (context near exhaustion) |

---

## Measurement (Phase 1 Task 5)

**Baseline Measurement (Without Delegation):**
1. Select sample task (e.g., "implement position limit validation")
2. Track token usage per step:
   - File searches: ___ tokens
   - Doc lookups: ___ tokens
   - Implementation: ___ tokens
   - Test logs: ___ tokens
   - **Total:** ___ tokens

**Optimized Measurement (With Delegation):**
1. Perform same task with delegation
2. Track token usage:
   - File search summaries: ___ tokens (delegated)
   - Doc summaries: ___ tokens (delegated)
   - Implementation: ___ tokens (same)
   - Test summaries: ___ tokens (delegated)
   - **Total:** ___ tokens

**Calculate:**
```
Optimization % = (Baseline - Optimized) / Baseline × 100
Target: ≥30%
Projected: ~38%
```

---

## Success Criteria

Phase 1 delegation implementation succeeds when:

- [  ] Delegation pattern documented (this file)
- [  ] Task (Explore) examples provided
- [  ] Task (general-purpose) examples provided
- [  ] Zen-MCP clink pattern documented (review delegation)
- [  ] Integration examples for 00-analysis, 06-debugging included
- [  ] Anti-patterns documented
- [  ] Context monitoring guidelines provided
- [  ] Measurement approach defined

---

## Related Workflows

- [00-analysis-checklist.md](./00-analysis-checklist.md) — Impacted component search → delegate
- [06-debugging.md](./06-debugging.md) — Error tracing → delegate
- [03-reviews.md](./03-reviews.md) — Use clink for reviews (NOT Task)
- [03-reviews.md](./03-reviews.md) — Deep review delegation

---

## References

- `.claude/research/subagent-capabilities-research.md` — Research findings
- `.claude/research/delegation-decision-tree.md` — Decision criteria
- `CLAUDE.md` — Zen-MCP clink integration policy

---

**Next Step:** Update workflows with delegation examples (Phase 1 Task 4)
