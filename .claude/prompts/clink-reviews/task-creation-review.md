# Task Creation Review (Pre-Work)

**Tool:** clink + gemini planner → codex planner
**Duration:** 2-3 minutes
**When:** Before starting work on task (Tier 3 review)

---

## Review Prompt (Phase 1: Gemini Planner)

Please review this task document to ensure it's well-scoped and ready for implementation.

**Focus Areas:**

1. **Scope Clarity:**
   - Objective clearly stated?
   - Boundaries defined (what's in/out of scope)?
   - Success criteria measurable?
   - Deliverables explicit?
   - Scope creep risks identified?

2. **Requirements Completeness:**
   - Functional requirements comprehensive?
   - Non-functional requirements (performance, security) specified?
   - Dependencies identified?
   - Blockers listed?
   - Acceptance criteria testable?

3. **Trading Platform Fit:**
   - Trading safety requirements clear (circuit breakers, idempotency)?
   - Feature parity considerations addressed?
   - Reconciliation impact assessed?
   - Testing strategy includes backtest replay?
   - ADR required for architectural changes?

4. **Implementation Approach:**
   - Component breakdown logical?
   - 4-step pattern applied per component?
   - Time estimates reasonable?
   - File structure clear?
   - Review checkpoints identified?

5. **Risk Management:**
   - Risks identified with mitigations?
   - Complexity estimated correctly?
   - Breaking changes noted?
   - Rollback plan present (if needed)?

6. **Documentation & Testing:**
   - Test strategy comprehensive (unit, integration, E2E)?
   - Edge cases identified?
   - Documentation requirements clear?
   - Trading concepts documented (if needed)?

**Output Format:**

```
**Findings**
- [Severity] – [Category]: [Issue with specific reference]
  [Impact on implementation]
  [Recommendation]

**Strengths**
- [Well-defined aspects of the task]

**Scope Assessment**
[Overall evaluation: clear, needs refinement, or underspecified]

**Recommendations**
1. [Specific improvements needed before starting work]
2. [Additional considerations]

<SUMMARY>[Verdict: APPROVED, NEEDS REVISION, or BLOCKED with reasoning]</SUMMARY>
```

**Severity Levels:**
- **CRITICAL:** Task cannot proceed (missing requirements, unclear objective)
- **HIGH:** Major gaps (missing acceptance criteria, unclear scope)
- **MEDIUM:** Improvements needed (missing edge cases, weak time estimates)
- **LOW:** Minor enhancements (documentation polish, additional context)

**Save continuation_id for Phase 2!**

---

## Phase 2 Prompt (Codex Planner - Reuse continuation_id)

Given the task validation findings above, please synthesize:

1. **Readiness Assessment:**
   - Is task ready to implement (APPROVED)?
   - What MUST be fixed before starting (CRITICAL/HIGH)?
   - What should be clarified (MEDIUM)?

2. **Scope Refinement:**
   - Are boundaries clear enough?
   - Is component breakdown logical?
   - Are time estimates reasonable?

3. **Risk Mitigation:**
   - What could go wrong during implementation?
   - Are dependencies manageable?
   - Are there hidden complexities?

4. **Next Steps:**
   - Ordered action items for developer
   - Estimated effort to address findings
   - When to re-request validation

**Output format: Actionable recommendations with clear verdict (APPROVED/NEEDS REVISION/BLOCKED)**

---

## 🔔 Workflow Reminder

**After task review is APPROVED:**

1. **Start Implementation:**
   - ✅ Task document validated
   - ✅ Scope clear and achievable
   - ✅ Ready to begin work

2. **Follow 4-Step Pattern for EACH Component:**
   - **Step 1:** Implement logic component
   - **Step 2:** Create test cases (TDD)
   - **Step 3:** Request quick review (clink + codex codereviewer)
   - **Step 4:** Commit after approval

3. **Progressive Commits:**
   - Commit every 30-60 minutes per logical component
   - Never combine multiple components in one commit
   - Each commit requires quick review (Tier 1)

4. **After All Components Complete:**
   - Request deep review (clink + gemini codereviewer)
   - Address findings from deep review
   - Create PR following `.claude/workflows/02-git-pr.md`

5. **Do NOT Forget:**
   - Run `make test && make lint` before every commit
   - Include continuation_id in commit messages
   - Update documentation as you go
   - Create follow-up tasks for deferred work

**When task review finds issues (NEEDS REVISION):**

1. **Address Findings First:**
   - Fix CRITICAL/HIGH issues before starting work
   - Clarify scope ambiguities
   - Add missing acceptance criteria
   - Identify overlooked dependencies

2. **Re-Request Task Review:**
   - Use continuation_id to preserve context
   - Show how findings were addressed
   - Only start work after APPROVED

**This reminder prevents you from forgetting workflow steps during long implementation sessions!**

---

## Trading Platform Task Validation Context

**Standard Task Requirements:**

1. **Trading Safety:**
   - Circuit breaker integration points identified?
   - Idempotency strategy clear?
   - Position limit impacts assessed?
   - Risk check requirements specified?

2. **Feature Parity:**
   - Shared code path between research/production?
   - Feature computation reusable?
   - No duplicate logic allowed?

3. **Testing Requirements:**
   - Unit tests for pure functions
   - Integration tests for APIs/database
   - E2E test for paper trading scenario
   - Backtest replay validation

4. **Documentation Requirements:**
   - ADR for architectural changes (MANDATORY)
   - Trading concepts in `/docs/CONCEPTS/` (if new domain knowledge)
   - API documentation updated (if endpoints change)
   - README updates (if user-facing changes)

5. **Standard Component Structure:**
   - Each component uses 4-step pattern
   - Components independent and testable
   - Progressive commits (30-60 min cadence)
   - Review checkpoints explicit

**Common Task Anti-Patterns to Flag:**

- ❌ **Scope too large:** >8 hours without component breakdown
- ❌ **Missing acceptance criteria:** Cannot verify completion
- ❌ **No trading safety requirements:** Circuit breakers, idempotency missing
- ❌ **Unclear dependencies:** Blockers not identified
- ❌ **No test strategy:** Missing unit/integration/E2E breakdown
- ❌ **Missing ADR requirement:** Architectural change without ADR
- ❌ **No risk assessment:** Complexity or breaking changes not considered
- ❌ **Weak time estimates:** No component-level breakdown
- ❌ **Feature parity ignored:** Research/production divergence not addressed
- ❌ **No reconciliation impact:** State changes without reconciliation plan

**Approval Criteria:**

✅ **Objective:** Clear, measurable, achievable
✅ **Scope:** Well-bounded, <8 hours or broken into components
✅ **Requirements:** FR and NFR comprehensive
✅ **Acceptance Criteria:** Testable and specific
✅ **Trading Safety:** Circuit breakers, idempotency, limits addressed
✅ **Test Strategy:** Unit, integration, E2E planned
✅ **Dependencies:** Identified and verified available
✅ **Time Estimates:** Reasonable with component breakdown
✅ **Documentation:** ADR/concepts/API updates identified
✅ **Implementation Approach:** 4-step pattern per component

**Never approve tasks that:**
- Lack clear success criteria
- Ignore trading safety requirements
- Have unclear scope or boundaries
- Missing test strategy
- Require architectural changes without ADR mention
- Exceed 8 hours without component breakdown
- Skip feature parity considerations
