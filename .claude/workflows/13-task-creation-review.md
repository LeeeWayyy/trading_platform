# Task Creation Review Workflow (Clink + Gemini → Codex)

**Purpose:** Validate task documents before starting work to prevent scope creep and unclear requirements (RECOMMENDED quality gate)
**Tool:** clink + gemini planner → codex planner (Tier 3 review, two-phase)
**Prerequisites:** Task document created in `/docs/TASKS/*.md`
**Expected Outcome:** Task validated for scope clarity, requirement completeness, ready to implement
**Owner:** @development-team
**Last Reviewed:** 2025-10-21

---

{{include:../snippets/clink-only-warning.md}}

---

## When to Use This Workflow

**RECOMMENDED for:**
- ✅ Complex tasks (>4 hours estimated)
- ✅ Tasks with architectural changes
- ✅ Tasks with unclear requirements
- ✅ New feature development
- ✅ When unsure about task scope

**Can skip for:**
- 📝 Trivial tasks (<2 hours, well-defined)
- 🐛 Simple bug fixes with clear reproduction
- 📚 Documentation-only updates
- 🔧 Routine maintenance

**Benefits:**
- Prevents mid-work scope changes
- Identifies missing requirements early
- Validates time estimates
- Ensures ADR requirements clear
- Reduces implementation rework

**Time investment:** 2-3 minutes → Saves hours of rework!

---

## Step-by-Step Process

### 1. Create Task Document

**Use task template:**
```bash
cp docs/TASKS/00-TEMPLATE_TASK.md docs/TASKS/P1T15_TASK.md
```

**Fill in sections:**
- Objective (clear, measurable)
- Success criteria (testable)
- Functional requirements (comprehensive)
- Non-functional requirements (performance, security)
- Implementation approach (component breakdown)
- Time estimates (per component)
- Acceptance criteria (specific)

**Save and stage:**
```bash
git add docs/TASKS/P1T15_TASK.md
```

### 2. Request Task Review (Two-Phase: Gemini → Codex)

**Phase 1: Task Validation (Gemini Planner)**
```
"Please review docs/TASKS/P1T15_TASK.md using clink + gemini planner.
Validate scope clarity, requirement completeness, and readiness to implement."
```

**Alternative (use review prompt):**
```
"Use the task-creation-review prompt from .claude/prompts/clink-reviews/
to review docs/TASKS/P1T15_TASK.md"
```

**What happens in Phase 1:**
- Claude uses clink with gemini CLI planner role (gemini-2.5-pro)
- Gemini validates task scope, requirements, acceptance criteria
- Checks for trading safety requirements
- Identifies missing dependencies or blockers
- Verifies time estimates and component breakdown
- Review takes ~1-2 minutes
- Returns findings WITH continuation_id

**Phase 2: Readiness Assessment (Codex Planner)**
```
"Now use clink + codex planner with the same continuation_id
to synthesize readiness assessment and action plan"
```

**What happens in Phase 2:**
- Claude uses clink with codex CLI planner role (preserves continuation_id)
- Codex synthesizes gemini's findings into actionable verdict
- Provides clear APPROVED/NEEDS REVISION/BLOCKED decision
- Lists ordered action items if revisions needed
- Takes ~30-60 seconds
- Total review time: ~2-3 minutes across both phases

### 3. Review Comprehensive Findings

**Gemini will assess:**

**Scope Clarity:**
- Objective clearly stated?
- Boundaries defined (what's in/out)?
- Success criteria measurable?
- Scope creep risks identified?

**Requirements Completeness:**
- Functional requirements comprehensive?
- Non-functional requirements specified?
- Trading safety requirements clear?
- Dependencies identified?
- Blockers listed?

**Implementation Readiness:**
- Component breakdown logical?
- 4-step pattern per component?
- Time estimates reasonable?
- ADR required for arch changes?

**Expected output format:**
```
**Findings**

- HIGH – Missing acceptance criteria (lines 164-186):
  Success criteria too vague. "Improve performance" isn't testable.
  Recommend: "Reduce order placement latency from 150ms to <50ms (p95)"

- MEDIUM – Unclear scope boundary:
  Task mentions "reconciliation updates" but doesn't specify if this
  includes database schema changes. Clarify scope.

**Strengths**
- Component breakdown follows 4-step pattern ✓
- Trading safety requirements comprehensive ✓
- Time estimates include buffer ✓

**Scope Assessment**
Scope is mostly clear but needs refinement on acceptance criteria
and reconciliation scope boundary.

**Recommendations**
1. Make success criteria measurable (add metrics)
2. Clarify reconciliation scope (schema changes in/out?)
3. Add test strategy for edge cases
4. Estimate looks reasonable at 6-8 hours

<SUMMARY>NEEDS REVISION - Address HIGH and MEDIUM findings before starting work.</SUMMARY>

continuation_id: abc123-def456
```

### 4. Handle Review Results

**If APPROVED:**
```
✅ Task is ready to implement!

Next steps:
1. Follow 4-step pattern for each component
2. Request quick reviews (clink + codex) per commit
3. Track progress with todo list
4. Request deep review before PR
```

**If NEEDS REVISION:**
```
⚠️ Fix issues before starting work

Steps:
1. Address HIGH/CRITICAL findings immediately
2. Fix MEDIUM issues or clarify scope
3. Update task document
4. Re-request review with continuation_id:
   "I've updated the task document to address findings.
   Please verify using continuation_id: abc123-def456"
5. Wait for APPROVED before starting implementation
```

**If BLOCKED:**
```
❌ Cannot proceed - critical gaps

Steps:
1. Review blocking issues (missing requirements, dependencies)
2. Gather needed information
3. Consult with team/user if needed
4. Update task document comprehensively
5. Re-request full review (may not preserve continuation_id)
```

### 5. Update Task Document

**Fix HIGH/CRITICAL issues immediately:**
```bash
# Edit task document
vim docs/TASKS/P1T15_TASK.md

# Make success criteria measurable
# Clarify scope boundaries
# Add missing requirements

# Stage changes
git add docs/TASKS/P1T15_TASK.md
```

**Request verification:**
```
"I've addressed the findings:
- Made success criteria measurable (latency target: <50ms p95)
- Clarified reconciliation scope (no schema changes, in-memory only)
- Added edge case testing strategy

Please verify using continuation_id: abc123-def456"
```

### 6. Get Final Approval

**Gemini will verify fixes:**
```
**Verification Results**

✅ All HIGH findings addressed
✅ MEDIUM issues resolved
✅ Task document now complete

Updated sections verified:
- Success criteria now measurable with metrics
- Scope boundaries explicit
- Test strategy comprehensive

<SUMMARY>APPROVED - Task is ready for implementation.</SUMMARY>

continuation_id: abc123-def456
```

**After approval:**
- Start implementation with confidence!
- Follow 4-step pattern per component
- Reference task document during work
- Update task if scope changes during implementation

---

## Decision Points

### Should I skip task review?

**✅ Skip if:**
- Task is trivial (<2 hours, very clear)
- Simple bug fix with obvious solution
- Documentation-only update
- Routine maintenance (dependency updates)

**❌ Never skip for:**
- Complex features (>4 hours)
- Architectural changes
- Tasks with unclear requirements
- Trading platform safety features
- When you're uncertain about scope

**Rule of thumb:** If you're debating whether to skip → DON'T SKIP!

### Task review found many issues - should I revise?

**Always revise if:**
- HIGH/CRITICAL findings (blocking issues)
- Scope ambiguity that could cause rework
- Missing trading safety requirements

**Consider team discussion if:**
- Fundamental approach questioned
- Time estimate way off
- Dependencies unclear
- Requires architectural decision

**Don't over-revise:**
- LOW issues can be noted and addressed during implementation
- Perfect is enemy of good
- Some details emerge during implementation

---

## Common Issues & Solutions

### Issue: Gemini Says Scope Too Large

**Symptom:** Task estimate >8 hours, complex, many requirements

**Solution:**
```
Option 1: Break into multiple tasks
- Create P1T15a, P1T15b, P1T15c
- Each task <8 hours
- Clear dependencies between tasks

Option 2: Use component breakdown
- Keep single task
- Break into 4-6 logical components
- Each component uses 4-step pattern
- Progressive commits per component
```

**When to use each:**
- Multiple tasks: Independent features, could be assigned separately
- Component breakdown: Tightly coupled, must be done together

### Issue: Unclear If ADR Required

**Symptom:** Task involves design decisions, not sure if architectural

**Solution:**
```
"Does this task require an ADR? It involves [describe decision].

Gemini will assess:
- Is this a significant architectural decision?
- Does it affect multiple services?
- Does it create precedent for future work?
- Is it reversible easily?

If yes → Add ADR creation to task
If no → Proceed without ADR
If maybe → Err on side of creating ADR
```

**ADR triggers:**
- New service creation
- Database schema changes
- Communication pattern changes
- Circuit breaker modifications
- External API integrations

### Issue: Time Estimate Way Off

**Symptom:** Gemini says estimate too low/high

**Solution:**
```
"Can you break down time estimate by component?

Gemini provides:
- Component 1: 2 hours (implementation + tests + review + commit)
- Component 2: 1.5 hours
- Component 3: 3 hours
- Testing integration: 1 hour
- Total: 7.5 hours → Round to 8 hours

Compare to your estimate, adjust if needed"
```

**Estimation tips:**
- Include testing time (30-50% of implementation)
- Include review time (~10% for quick reviews)
- Include buffer (15% for unexpected)
- Round up, not down

### Issue: Task Review vs Deep Review Confusion

**Symptom:** Unsure which workflow to use

**Clarification:**
```
Task Creation Review (13):
- BEFORE implementation starts
- Validates task document
- Uses gemini planner
- 2-3 minutes
- Prevents bad plans

Quick Review (03):
- DURING implementation
- Per commit (every 30-60 min)
- Uses codex codereviewer
- ~30 seconds
- Prevents bad code

Deep Review (04):
- AFTER implementation complete
- Before PR creation
- Uses gemini → codex
- 3-5 minutes
- Prevents bad architecture
```

---

## Examples

### Example 1: Task Approved

```bash
$ "Review docs/TASKS/P1T15_TASK.md using clink + gemini planner"

Claude: [Uses clink with gemini CLI]

Gemini: "**Findings**
(none - task document is comprehensive)

**Strengths**
- Objective clear and measurable ✓
- Success criteria testable with specific metrics ✓
- Functional and non-functional requirements complete ✓
- Trading safety requirements explicit (circuit breakers, idempotency) ✓
- Component breakdown logical (4 components, 4-step pattern each) ✓
- Time estimate reasonable (6 hours with 15% buffer) ✓
- ADR requirement noted for service communication change ✓

**Scope Assessment**
Well-scoped task, ready for implementation.

**Recommendations**
(none needed - proceed with implementation)

<SUMMARY>APPROVED - Task document is comprehensive and ready for implementation.</SUMMARY>

continuation_id: abc123-def456"

$ "Now use clink + codex planner with continuation_id: abc123-def456 to synthesize readiness assessment"

Claude: [Uses clink with codex CLI planner - Phase 2]

Codex: "**Readiness Assessment**
Task is APPROVED and ready for implementation.

**Scope Refinement**
- Boundaries clear ✓
- Component breakdown logical ✓
- Time estimates reasonable ✓

**Risk Mitigation**
- No hidden complexities identified
- Dependencies manageable
- Low implementation risk

**Next Steps**
1. Start implementation using 4-step pattern
2. Request quick review (clink + codex) per commit
3. Track progress with todo list

<SUMMARY>APPROVED - Begin implementation immediately.</SUMMARY>

continuation_id: abc123-def456"

$ # Start implementation with confidence!
$ # Follow 4-step pattern per component
```

### Example 2: Task Needs Revision

```bash
$ "Review docs/TASKS/P1T16_TASK.md using clink + gemini planner"

Claude: [Uses clink with gemini CLI]

Gemini: "**Findings**

- HIGH – Missing trading safety requirements:
  Task involves order placement but doesn't mention circuit breaker
  integration or idempotency strategy. These are MANDATORY.
  Add explicit requirements for both.

- MEDIUM – Vague success criteria (line 16):
  \"Improve order placement performance\" is not testable.
  Recommend: \"Reduce p95 latency from 150ms to <75ms\"

- LOW – Missing edge case testing:
  Consider adding test scenarios for network failures, race conditions

**Strengths**
- Component breakdown clear ✓
- Time estimate includes buffer ✓

**Scope Assessment**
Core scope clear but missing critical trading safety requirements.

**Recommendations**
1. Add circuit breaker integration requirement
2. Add idempotency strategy (client_order_id)
3. Make success criteria measurable with metrics
4. Add edge case test scenarios

<SUMMARY>NEEDS REVISION - Must add trading safety requirements before starting.</SUMMARY>

continuation_id: xyz789-abc123"

$ "Now use clink + codex planner with continuation_id: xyz789-abc123 to synthesize action plan"

Claude: [Uses clink with codex CLI planner - Phase 2]

Codex: "**Readiness Assessment**
Task is BLOCKED - MUST fix HIGH findings before starting.

**Scope Refinement**
- Core scope clear but safety requirements missing
- Add: Circuit breaker integration
- Add: Idempotency strategy

**Risk Mitigation**
HIGH RISK: Starting without trading safety requirements will cause rework

**Next Steps (Priority Order)**
1. Add circuit breaker integration requirement (15 min)
2. Add idempotency strategy (client_order_id) (10 min)
3. Make success criteria measurable with metrics (5 min)
4. Add edge case test scenarios (10 min)
Total: ~40 minutes to address all findings

<SUMMARY>NEEDS REVISION - Fix HIGH findings, then re-request validation.</SUMMARY>

continuation_id: xyz789-abc123"

$ # Fix the issues
$ vim docs/TASKS/P1T16_TASK.md
# Add circuit breaker and idempotency requirements
# Make success criteria measurable
# Add edge case tests

$ git add docs/TASKS/P1T16_TASK.md

$ "I've updated the task to address findings:
- Added circuit breaker integration requirement (line 42)
- Added idempotency strategy (client_order_id, line 48)
- Made success criteria measurable (p95 <75ms, line 16)
- Added edge case test scenarios (line 203)

Please verify using continuation_id: xyz789-abc123"

Claude: [Uses clink with codex planner, preserves context]

Codex: "**Verification Results**

✅ All HIGH findings addressed:
   - Circuit breaker integration specified ✓
   - Idempotency strategy (client_order_id) added ✓

✅ MEDIUM findings resolved:
   - Success criteria now measurable (p95 <75ms) ✓

✅ LOW findings addressed:
   - Edge case tests comprehensive ✓

<SUMMARY>APPROVED - All findings resolved. Task ready for implementation.</SUMMARY>

continuation_id: xyz789-abc123"

$ # Now start implementation!
```

---

## Validation

**How to verify this workflow succeeded:**
- [ ] Task document reviewed by gemini planner
- [ ] APPROVED verdict received
- [ ] All HIGH/CRITICAL issues resolved
- [ ] Scope clearly defined
- [ ] Requirements comprehensive
- [ ] continuation_id captured for reference

**What to check if something seems wrong:**
- Verify task document file exists and is readable
- Check gemini planner returned structured verdict
- Look for explicit "APPROVED" or "NEEDS REVISION"
- Verify continuation_id provided for follow-up

---

## Related Workflows

- [01-git-commit.md](./01-git-commit.md) - Progressive commit workflow (uses 4-step pattern from task)
- [03-zen-review-quick.md](./03-zen-review-quick.md) - Quick review per commit during implementation
- [04-zen-review-deep.md](./04-zen-review-deep.md) - Deep review before PR
- [08-adr-creation.md](./08-adr-creation.md) - Creating ADRs for architectural changes

---

## References

**Standards & Policies:**
- [/docs/STANDARDS/GIT_WORKFLOW.md](../../docs/STANDARDS/GIT_WORKFLOW.md) - Progressive commit workflow
- [/docs/STANDARDS/ADR_GUIDE.md](../../docs/STANDARDS/ADR_GUIDE.md) - When to create ADRs

**Task Templates:**
- [/docs/TASKS/00-TEMPLATE_TASK.md](../../docs/TASKS/00-TEMPLATE_TASK.md) - Task document template
- [/docs/TASKS/00-TEMPLATE_PHASE_PLANNING.md](../../docs/TASKS/00-TEMPLATE_PHASE_PLANNING.md) - Phase planning template

**Implementation Details:**
- [/CLAUDE.md](../../CLAUDE.md#🤖-zen-mcp--clink-integration) - Clink + zen-mcp integration overview
- [/docs/CONCEPTS/zen-mcp-clink-optimization-proposal.md](../../docs/CONCEPTS/zen-mcp-clink-optimization-proposal.md) - Task review workflow design
- [.claude/prompts/clink-reviews/task-creation-review.md](../prompts/clink-reviews/task-creation-review.md) - Task review prompt template

**Task Validation Focus:**
- Scope clarity and boundaries
- Success criteria measurability
- Requirement completeness (FR and NFR)
- Trading safety requirements (circuit breakers, idempotency, limits)
- Component breakdown quality
- Time estimate reasonableness
- ADR requirements for architectural changes
- Test strategy completeness

---

**Maintenance Notes:**
- Update when task template changes
- Review when new mandatory requirements added
- Adjust if false positive rate > 10%
- Notify @development-team for substantial changes
