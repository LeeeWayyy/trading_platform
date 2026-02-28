# Pre-Implementation Analysis Checklist

**MANDATORY:** Complete this checklist BEFORE writing ANY code.

**Purpose:** Prevent reactive fixing by identifying ALL impacted areas upfront.

**Time Required:** 30-60 minutes

**When to Use:**
- Before implementing ANY feature or fix
- Before changing function signatures
- Before modifying safety-critical code
- Before making architectural changes

---

## Phase 1: Comprehensive Analysis (30-60 min)

### 1. Understand the Requirement (5 min)

- [ ] Read the full requirement/issue/ticket
- [ ] Identify primary objective
- [ ] List acceptance criteria
- [ ] Understand "why" not just "what"
- [ ] Flag any unclear requirements for user clarification

**Output:** One-sentence summary of the requirement and its purpose

---

### 2. Identify ALL Impacted Components (15 min)

**CRITICAL:** Find EVERY component that needs changes. Missing even one component causes reactive fix commits.

#### Code Impact Analysis

- [ ] **Search for ALL call sites:**

  **Option A: Direct search (simple cases, <5 expected results):**
  ```bash
  # If changing a function/method
  grep -rn "function_name(" apps/ libs/ tests/

  # If changing a class
  grep -rn "ClassName" apps/ libs/ tests/
  ```

  **Option B: Delegate to subagent (complex cases, >10 expected results or uncertain scope):**
  ```python
  # See ./16-subagent-delegation.md
  Task(
      description="Find all call sites for function_name",
      prompt="""Search apps/, libs/, tests/ for function_name() usage.

      Deliverable: JSON with categorized file:line references:
      {"calls": [...], "imports": [...], "tests": [...]}

      Constraints: <5000 tokens
      """,
      subagent_type="Explore"
  )
  # Benefits: 20-30k token savings, isolated context
  ```

- [ ] **List ALL files that import the module**
- [ ] **Identify similar patterns elsewhere**
- [ ] **Check database schema impact** (migrations? backfill data?)
- [ ] **Check API contract impact** (request/response schemas? breaking changes?)

**Output:** Complete list of ALL files/modules/components that need changes

**💡 Context Optimization:** If context usage >50% (100k tokens), prefer Option B (delegation) for all searches. See [16-subagent-delegation.md](./16-subagent-delegation.md) for how-to patterns and [delegation-decision-tree.md](../Research/delegation-decision-tree.md) for when-to criteria.

---

### 3. Identify ALL Tests That Need Updating (10 min)

- [ ] **Find existing tests:**

  **Option A: Direct search (simple cases):**
  ```bash
  find tests/ -name "*test_component*"
  grep -r "from module import" tests/
  ```

  **Option B: Delegate (if context >50% used):**
  ```python
  # Delegate test discovery to subagent
  Task(
      description="Find tests for component",
      prompt="""Find all test files related to <component>.

      Deliverable: Categorized test references:
      {"unit": [...], "integration": [...], "e2e": [...]}
      """,
      subagent_type="Explore"
  )
  ```

- [ ] **Categorize tests:**
  - Unit tests that need updating: _______
  - Integration tests that need updating: _______
  - E2E tests that need updating: _______

- [ ] **Identify missing test scenarios:**
  - Success path tests needed: _______
  - Failure path tests needed: _______
  - Edge case tests needed: _______

**Output:** List of ALL tests that need changes + NEW tests needed

---

### 4. Verify Pattern Parity (10 min)

**Ensure new code follows established patterns:**

- [ ] **Check error handling patterns:**
  - Are exceptions logged with proper context?
  - Are exceptions raised with meaningful messages?
  - Are try-except blocks used appropriately?
  - Are proper exception types used (not bare `except:`)?
  - Is error context preserved (chaining with `from`)?

- [ ] **Check retry patterns:**
  - Do ALL Redis methods have `@retry` decorator?
  - Do HTTP calls have proper timeout/retry logic?
  - Are retry attempts logged?

- [ ] **Check logging patterns:**
  - Structured logging (JSON)?
  - Required fields (`strategy_id`, `client_order_id`)?

- [ ] **Check decorator patterns:**
  - Required decorators present?
  - Decorators in correct order?

**Output:** List of patterns to follow; confirmation new code will match

---

### 5. Verify Language/Library Assumptions (10 min)

- [ ] **Python language rules:**
  - If using `global`: Check function doesn't already have global declaration
  - If using async/await: Understand event loop implications

- [ ] **Library behavior:**
  - Check library documentation for edge cases
  - Verify version compatibility

- [ ] **Framework patterns:**
  - FastAPI: Understand exception handling in endpoints
  - pytest: Understand marker inheritance and fixture scope

**Output:** List of verified assumptions; any uncertainties flagged

---

### 6. Call Site Analysis (If Changing Function Signature)

**MANDATORY if:**
- Adding new exceptions a function can raise
- Changing function parameters
- Changing return type

- [ ] **Find ALL call sites:**
  ```bash
  grep -rn "function_name(" apps/ libs/ tests/
  ```

- [ ] **For EACH call site, document:**
  - File path and line number: _______
  - Current error handling: _______
  - New error handling needed: _______
  - Impact on tests: _______

- [ ] **Create todos for EVERY call site** (use 6-step pattern per site)

**Output:** Complete table of ALL call sites with change requirements

---

### 7. Process Compliance Verification (5 min)

**Ensure required quality gates will be enforced:**

- [ ] **Review gate confirmation:**
  - Will code be reviewed BEFORE commit? (MANDATORY: YES)
  - When will review happen? (After implementation, before commit)

- [ ] **CI gate confirmation:**
  - Will `make ci-local` run before commit? (MANDATORY: YES)
  - If tests fail, will commit be blocked? (MANDATORY: YES)

- [ ] **Approval gate confirmation:**
  - Does this require user approval? (architectural changes, breaking changes)

**Output:** Confirmation that ALL quality gates will be enforced

---

## Phase 2: Design Complete Solution (15-30 min)

### 1. Document EVERY Change Needed

- [ ] **Create comprehensive todo list** using 6-step pattern for EACH component:
  ```markdown
  - [ ] Plan [component] approach
  - [ ] Request plan review for [component]
  - [ ] Implement [component] logic
  - [ ] Create test cases for [component] (success + failure)
  - [ ] Request code review for [component]
  - [ ] Commit [component] after review approval
  ```

- [ ] **Break down into logical components:**
  - Component 1: _______
  - Component 2: _______
  - Component 3: _______

- [ ] **Document change strategy:**
  - What changes in what order?
  - Dependencies between changes?
  - Rollback strategy if issues arise?

**Output:** Complete todo list with 6-step pattern for EACH component

---

### 2. Identify ALL Edge Cases

- [ ] **Normal operation edge cases:**
  - Empty inputs, Null/None values
  - Very large/small inputs, Boundary conditions

- [ ] **Failure scenarios:**
  - Redis/Database unavailable
  - API timeout, Invalid data
  - State missing/corrupted

- [ ] **Concurrency edge cases:**
  - Race conditions, Deadlocks, Stale data

- [ ] **Security edge cases:**
  - SQL injection, XSS, Auth failures

**Output:** List of ALL edge cases; confirmation each has test coverage

---

### 3. Plan Error Handling for EVERY Call Site

- [ ] **For each call site, plan:**
  - What exceptions can be raised?
  - How should each exception be handled?
  - Should errors propagate or be caught?
  - What should be logged? What should users see?

**Output:** Error handling strategy documented for EVERY call site

---

## Phase 3: Final Pre-Implementation Checks (5 min)

- [ ] **Review checklist completion:**
  - ALL sections complete?
  - NO gaps in analysis?
  - NO uncertain assumptions?

- [ ] **Stakeholder approval (if needed):**
  - User approved design?
  - Architectural changes approved?
  - Breaking changes approved?

- [ ] **Ready to implement:**
  - Todo list created with 6-step pattern?
  - All impacted areas identified?
  - Review gates confirmed?

**Output:** ✅ APPROVED to proceed with implementation

---

## Red Flags: STOP and Get Help

**STOP implementation if:**

- ❌ Cannot find all call sites
- ❌ Uncertain about language/library behavior
- ❌ Missing information from requirements
- ❌ Architectural implications unclear
- ❌ Breaking changes without user approval
- ❌ Pattern parity violations cannot be resolved

**Action:** Use AskUserQuestion tool to clarify uncertainties BEFORE coding.

---

## Success Criteria

This analysis is complete when:

1. ✅ ALL impacted components identified
2. ✅ ALL tests identified (existing + new)
3. ✅ ALL edge cases documented
4. ✅ ALL patterns verified
5. ✅ ALL assumptions validated
6. ✅ Comprehensive todo list created (6-step pattern per component)
7. ✅ Review gates confirmed
8. ✅ NO uncertainties remaining

**Time saved by thorough analysis:** 3-11 hours (vs. reactive fixing)

**Next Step:** Proceed to implementation using the 6-step pattern per component.
