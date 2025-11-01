# Task Creation Review Workflow (Clink + Gemini → Codex)

**Purpose:** Validate task documents before starting work to prevent scope creep (RECOMMENDED quality gate)
**Tool:** clink + gemini planner → codex planner (Tier 3 review)
**Prerequisites:** Task document created in `/docs/TASKS/*.md`
**Expected Outcome:** Task validated for scope clarity, requirement completeness
**Owner:** @development-team
**Last Reviewed:** 2025-10-21

---

## Quick Reference

**Clink Usage:** See [Zen-MCP Review Process](./_common/zen-review-process.md)
**Git:** See [Git Commands Reference](./_common/git-commands.md)

---

## When to Use This Workflow

**RECOMMENDED for:**
- Complex tasks (>4 hours estimated)
- Tasks with architectural changes
- Unclear requirements
- New feature development

**Can skip for:**
- Trivial tasks (<2 hours, well-defined)
- Simple bug fixes
- Documentation-only updates
- Routine maintenance

**Benefits:** 2-3 minutes → Saves hours of rework!

---

## Step-by-Step Process

### 1. Create Task Document

```bash
cp docs/TASKS/00-TEMPLATE_TASK.md docs/TASKS/P1T15_TASK.md
# Fill in: Objective, Success criteria, Requirements, Implementation approach
git add docs/TASKS/P1T15_TASK.md
```

### 2. Request Task Review (Two-Phase)

**Phase 1: Gemini Planner**
```
"Review docs/TASKS/P1T15_TASK.md using clink + gemini planner.
Validate scope clarity, requirement completeness, and readiness."
```

**Phase 2: Codex Planner** (receives continuation_id from Gemini)
```
"Use clink + codex planner with continuation_id to synthesize readiness assessment."
```

See [Zen-MCP Review Process](./_common/zen-review-process.md) for clink usage details.

### 3. Review Findings

**Gemini assesses:**
- Scope Clarity: Objective clear? Boundaries defined?
- Requirements Completeness: Functional + non-functional specified?
- Implementation Readiness: Component breakdown logical? Time estimates reasonable?
- Trading Safety: Circuit breakers, idempotency requirements clear?

**Expected output:**
```
**Findings**
- HIGH – Missing acceptance criteria (lines 164-186)
- MEDIUM – Unclear scope boundary

**Strengths**
- Component breakdown follows 4-step pattern ✓

**Recommendations**
1. Make success criteria measurable
2. Clarify reconciliation scope
3. Add test strategy

<SUMMARY>NEEDS REVISION</SUMMARY>

continuation_id: abc123-def456
```

### 4. Handle Review Results

**If APPROVED:**
```
✅ Task ready to implement
→ Follow 4-step pattern per component
→ Request quick reviews (clink + codex) per commit
```

**If NEEDS REVISION:**
```
⚠️ Fix HIGH/CRITICAL findings

Steps:
1. Update task document
2. Re-request review: "I've addressed findings. Verify using continuation_id: abc123"
3. Wait for APPROVED
```

**If BLOCKED:**
```
❌ Cannot proceed
→ Gather missing information
→ Consult team/user
→ Update comprehensively
→ Re-request full review
```

---

## Decision Points

### Should I skip task review?

**✅ Skip if:**
- Trivial (<2 hours, very clear)
- Simple bug fix
- Documentation-only

**❌ Never skip for:**
- Complex features (>4 hours)
- Architectural changes
- Unclear requirements
- Trading platform safety features

**Rule of thumb:** If debating → DON'T SKIP!

### Task review vs Deep review confusion?

**Task Creation Review (13):**
- BEFORE implementation
- Validates task document
- Gemini planner
- 2-3 minutes
- Prevents bad plans

**Quick Review (03):**
- DURING implementation
- Per commit (every 30-60 min)
- Codex codereviewer
- ~30 seconds
- Prevents bad code

**Deep Review (04):**
- AFTER implementation
- Before PR
- Gemini + Codex
- 3-5 minutes
- Prevents bad architecture

---

## Common Issues

### Gemini Says Scope Too Large

**Solution:**
- Break into multiple tasks (P1T15a, P1T15b, each <8 hours)
- OR use component breakdown (tightly coupled work)

### Unclear If ADR Required

**ADR triggers:**
- New service creation
- Database schema changes
- Communication pattern changes
- Circuit breaker modifications
- External API integrations

### Time Estimate Way Off

```
"Break down time estimate by component?"

Gemini provides:
- Component 1: 2 hours (implementation + tests + review + commit)
- Component 2: 1.5 hours
- Buffer: 15%
- Total: 8 hours (rounded up)
```

---

## Validation

**How to verify:**
- [ ] Task reviewed by gemini planner
- [ ] APPROVED verdict received
- [ ] All HIGH/CRITICAL issues resolved
- [ ] Scope clearly defined
- [ ] continuation_id captured

---

## Related Workflows

- [01-git-commit.md](./01-git-commit.md) - 4-step pattern from task
- [03-zen-review-quick.md](./03-zen-review-quick.md) - Quick review per commit
- [04-zen-review-deep.md](./04-zen-review-deep.md) - Deep review before PR
- [08-adr-creation.md](./08-adr-creation.md) - Creating ADRs

---

## References

- [/docs/STANDARDS/GIT_WORKFLOW.md](../../docs/STANDARDS/GIT_WORKFLOW.md) - Progressive commits
- [/docs/STANDARDS/ADR_GUIDE.md](../../docs/STANDARDS/ADR_GUIDE.md) - When to create ADRs
- [/docs/TASKS/00-TEMPLATE_TASK.md](../../docs/TASKS/00-TEMPLATE_TASK.md) - Task template
