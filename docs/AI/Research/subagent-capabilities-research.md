# Subagent Capabilities Research
**Phase:** P1T13-F3 Component 1
**Date:** 2025-11-01
**Duration:** 30 minutes
**Status:** COMPLETED

---

## Objective

Research existing Claude Code subagent capabilities to inform delegation pattern design for context optimization.

---

## Findings

### 1. Native Task Tool Capabilities

Claude Code provides native `Task` tool with **4 specialized subagent types**:

| Subagent Type | Purpose | Context Window | Best For |
|---------------|---------|----------------|----------|
| **general-purpose** | Research complex questions, code search, multi-step tasks | 200k (isolated) | Complex analysis, multi-step workflows |
| **Explore** | Fast codebase exploration | 200k (isolated) | File search, pattern finding, "where is X?" questions |
| **statusline-setup** | Configure status line | 200k (isolated) | UI configuration |
| **output-style-setup** | Create output styles | 200k (isolated) | UI configuration |

**Key Properties:**
- ✅ Each invocation creates **isolated 200k context window** (no pollution to main context)
- ✅ Stateless: Returns single message (cannot multi-turn conversation)
- ✅ Concurrent execution: Can launch multiple agents in parallel
- ✅ Output trusted: Agent results should be trusted
- ⚠️  Must provide detailed autonomous task description
- ⚠️  Must specify what information to return (summary format)

**Usage Pattern:**

```python
Task(
    description="Find circuit breaker implementations",
    prompt="""Search codebase for check_circuit_breaker() call sites.

    Deliverable: Return file:line references ONLY (no full code).
    Format: ["path/to/file.py:42", "path/to/other.py:103"]
    """,
    subagent_type="Explore"
)
# Returns: List of file:line references
# Main context saved: ~15-20k tokens (no full file contents)
```

**When NOT to Use Task Tool:**
- ❌ Reading specific known file path → Use `Read` instead
- ❌ Searching for specific class "class Foo" → Use `Glob` instead
- ❌ Searching within specific 2-3 files → Use `Read` instead
- ❌ Tasks not matching agent descriptions above

---

### 2. Existing Integration Points

**Zen-MCP Clink (Already Implemented):**
- ✅ Used for code review delegation
- ✅ Uses `mcp__zen__clink` with CLI authentication
- ✅ Supports `continuation_id` for multi-turn conversations
- ✅ Three-tier review system:
  - Tier 1: Quick review (clink + codex, ~30 sec)
  - Tier 2: Deep review (clink + gemini → codex, 3-5 min)
  - Tier 3: Task creation review (clink + gemini planner, 2-3 min)

**Current State:**
- ❌ No workflows currently use Task tool for delegation
- ❌ No script-based hooks exist
- ✅ This is a **greenfield implementation**

---

### 3. Delegation Decision Criteria

Based on research, **delegate to subagent** when:

| Task Type | Delegate? | Tool | Reason |
|-----------|-----------|------|--------|
| **File search (open-ended)** | ✅ YES | `Task` (Explore) | May require multiple search rounds, isolated context |
| **"Where is X?" questions** | ✅ YES | `Task` (Explore) | Open-ended codebase exploration |
| **Multi-file pattern search** | ✅ YES | `Task` (Explore) | Search-heavy, tangential to main task |
| **Code review** | ✅ YES | `mcp__zen__clink` | Already implemented, multi-turn needed |
| **Test execution** | ⚠️  MAYBE | Script hook | Logs can pollute context, but test results needed |
| **CI log analysis** | ✅ YES | `Task` (general-purpose) | Large logs, pattern extraction |
| **PR comment extraction** | ✅ YES | `Task` (general-purpose) | Structured data extraction |
| **Doc lookup (reference)** | ⚠️  MAYBE | `Task` (Explore) | If non-critical, delegate; if critical, keep in main |
| **Specific file read** | ❌ NO | `Read` tool | Known path, direct access faster |
| **Specific class search** | ❌ NO | `Glob` tool | Known pattern, direct search faster |
| **Planning** | ❌ NO | Main context | Core decision-making, requires full context |
| **Architecture decisions** | ❌ NO | Main context | Core decision-making, user interaction |
| **Implementation** | ❌ NO | Main context | Core task, requires continuity |
| **Commit creation** | ❌ NO | Main context | Critical operation, needs full history |
| **User interaction** | ❌ NO | Main context | Direct communication required |

---

### 4. Context Optimization Analysis

Delegating non-core tasks to subagents is projected to yield significant context savings. A detailed analysis is available in [`context-optimization-measurement.md`](./context-optimization-measurement.md), which projects the following benefits.

**Projected Benefits:**
- ✅ **43.5-60.9% context usage reduction** (exceeds 30% target)
- ✅ **Longer sessions** before context exhaustion
- ✅ **Better continuity** across implementation
- ✅ **Isolated failures** (subagent failure doesn't corrupt main context)

---

### 5. Recommended Approach: **Hybrid (Option C)**

Combine native Task tool + zen-mcp clink + reserve script hooks for future:

**Architecture:**

```
┌─────────────────────────────────────────────┐
│   Claude CLI Orchestrator (Main Context)   │
│   - Task planning                           │
│   - Core implementation                     │
│   - Architecture decisions                  │
│   - User interaction                        │
└─────────────┬───────────────────────────────┘
              │
              │ Delegates non-core tasks
              │
    ┌─────────┼──────────┬──────────────┐
    │         │          │              │
    ▼         ▼          ▼              ▼
┌─────────┐ ┌────────┐ ┌──────────┐ ┌──────────┐
│ Task    │ │ Task   │ │ Zen-MCP  │ │ Script   │
│ Explore │ │ general│ │ Clink    │ │ Hooks    │
│         │ │        │ │          │ │ (Future) │
└─────────┘ └────────┘ └──────────┘ └──────────┘
    │           │          │              │
    │           │          │              │
    └───────────┴──────────┴──────────────┘
              │
        Returns summaries only
      (15k vs. 90k tokens)
```

**Implementation Strategy:**

1. **Use Task (Explore)** for:
   - File searches across codebase
   - "Where is X implemented?" questions
   - Multi-file pattern searches
   - Open-ended exploration

2. **Use Zen-MCP Clink** for:
   - Code review delegation (quick/deep/task reviews)
   - Architecture analysis requiring multi-turn dialogue
   - Conversations needing `continuation_id`

3. **Reserve Script Hooks** for:
   - Future custom delegation needs
   - Specialized parsers (if needed)
   - External tool integrations

---

### 6. Implementation Next Steps

**Phase 1 (Current):**
1. ✅ Research complete (this document)
2. ⏳ **NEXT:** Design delegation decision tree
3. ⏳ Implement delegation pattern (Option C - Hybrid)
4. ⏳ Update workflows with delegation examples
5. ⏳ Measure context optimization (baseline vs. optimized)
6. ⏳ Create delegation guide (`.claude/workflows/16-subagent-delegation.md`)

**Success Criteria:**
- [✅] Native Task tool capabilities documented
- [✅] Zen-MCP integration points identified
- [✅] Delegation decision criteria defined
- [✅] Hybrid approach (Option C) selected
- [✅] 38% context optimization projected (exceeds 30% target)

---

## Key Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| **Delegation approach** | **Hybrid (Option C)** | Leverages native Task tool + existing zen-mcp + reserves script hooks for future |
| **Primary subagent** | **Explore** | Best fit for file search and codebase exploration (most common delegatable task) |
| **Review delegation** | **Zen-MCP Clink** | Already implemented, multi-turn support, continuation_id |
| **Target optimization** | **≥30%** | Projected 38% based on analysis (exceeds target) |

---

## References

- Claude Code `Task` tool system documentation
- `.claude/workflows/03-reviews.md` (clink usage patterns and multi-tier review)
- `CLAUDE.md` (zen-mcp + clink integration policy)

---

**Next:** Design delegation decision tree (Task 2)
