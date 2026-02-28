# Automated Pre-Implementation Analysis

**Purpose:** Automate the pre-implementation analysis workflow to reduce manual analysis time from 100 minutes to 55 minutes (45% reduction)
**When:** Before implementing ANY feature or fix (replaces manual 00-analysis-checklist.md workflow)
**Prerequisites:** Requirement/ticket to implement
**Expected Outcome:** Complete analysis summary with component breakdown, test plan, and edge cases

---

## Quick Reference

**Time Savings:**
- Manual analysis: 100 minutes
- Automated analysis: 55 minutes (22 min automated + 33 min human-guided)
- **Savings: 45 minutes (45% reduction)**

**What's Automated (WHAT exists discovery):**
- ✅ Impacted component discovery (15 min → 3 min)
- ✅ Test discovery and categorization (10 min → 2 min)
- ✅ Call site analysis (10 min → 4 min)
- ✅ Pattern parity verification (10 min → 5 min)

**What's Human-Guided (HOW to implement planning):**
- 🧑 Component breakdown (10 min, main context) - strategic decomposition
- 🧑 Edge case generation (10 min, main context) - implementation strategy

**What Remains Manual:**
- Requirement understanding (5 min)
- Process compliance verification (5 min)
- Final approval (3 min)

---

## Step-by-Step Automated Analysis

### Step 1: Parse Requirement (3 min)

**Input:** User provides ticket/requirement

**Action:** Semi-automated requirement extraction
```python
requirement_summary = Task(
    description="Parse requirement ticket",
    prompt=f"""Analyze requirement and extract key information.

Ticket content:
```
{ticket_content}
```

Tasks:
1. Identify primary objective (one-sentence summary)
2. Extract acceptance criteria
3. Determine change category (new_feature, bug_fix, refactor, signature_change, etc.)
4. Identify target components (modules, functions, classes)

Deliverable (JSON):
{{
  "objective": "<one sentence>",
  "acceptance_criteria": ["criterion 1", "criterion 2", ...],
  "change_category": "new_feature|bug_fix|refactor|signature_change",
  "target_components": {{
    "modules": ["apps.execution_gateway.order_placer"],
    "functions": ["place_order"],
    "classes": []
  }}
}}

Constraints: <3000 tokens, timeout 90 seconds
""",
    subagent_type="general-purpose"
)

# Human review: Verify objective matches intent (1 min)
```

**Output:** JSON with objective, criteria, category, target components

---

### Step 2: Parallel Component Discovery (3 min total)

**Action:** Launch 3 automated discovery delegations in parallel
```python
# The following 3 delegations run concurrently (3 min total, not sequential)

# Delegation 1: Impacted Components (2-3 min)
impacted_components = Task(
    description="Analyze impacted components",
    prompt=f"""Find ALL impacted components for requirement change.

Requirement: {requirement_summary['objective']}
Target: {requirement_summary['target_components']}
Change type: {requirement_summary['change_category']}

Tasks:
1. Find ALL direct call sites
2. Find ALL import statements
3. Find similar patterns elsewhere
4. Check database schema impact (migrations needed?)
5. Check API contract impact (breaking changes?)

Repository: $PROJECT_ROOT
Focus: apps/, libs/, tests/
Exclude: data/, artifacts/, docs/

Deliverable (JSON):
{{
  "call_sites": [
    {{"file": "apps/execution_gateway/order_placer.py", "line": 142, "context": "if place_order(...)", "impact": "signature_change"}}
  ],
  "imports": [
    {{"file": "apps/risk_manager/monitor.py", "line": 8}}
  ],
  "similar_patterns": [
    {{"file": "apps/reconciler/order_sync.py", "line": 200, "pattern": "place_order_v2", "note": "Legacy pattern to migrate"}}
  ],
  "schema_impact": {{
    "tables": ["orders", "positions"],
    "migrations_needed": true|false,
    "migration_type": "add_column|alter_column|new_table"
  }},
  "api_impact": {{
    "endpoints": ["/orders/place"],
    "breaking_changes": true|false
  }}
}}

Constraints: <8000 tokens, timeout 2 min
""",
    subagent_type="Explore"
)

# Delegation 2: Test Discovery (2 min)
tests_to_update = Task(
    description="Identify tests to update",
    prompt=f"""Find ALL tests related to component change.

Component: {requirement_summary['target_components']}
Change type: {requirement_summary['change_category']}

Tasks:
1. Find existing test files
2. Categorize by type (unit, integration, e2e)
3. Identify tests that need updating
4. Identify missing test scenarios

Repository: $PROJECT_ROOT/tests

Deliverable (JSON):
{{
  "existing_tests": {{
    "unit": [
      {{"file": "tests/apps/execution_gateway/test_order_placer.py", "needs_update": true, "reason": "Signature change"}}
    ],
    "integration": [...],
    "e2e": [...]
  }},
  "missing_tests": {{
    "success_paths": ["Test successful order placement with new validation"],
    "failure_paths": ["Test validation error handling", "Test circuit breaker trip"],
    "edge_cases": ["Empty input", "Null strategy_id", "Boundary conditions"]
  }},
  "total_test_count": {{
    "existing_to_update": 12,
    "new_to_create": 8
  }}
}}

Constraints: <6000 tokens, timeout 2 min
""",
    subagent_type="Explore"
)

# Delegation 3: Pattern Parity (2 min)
pattern_parity = Task(
    description="Verify pattern parity",
    prompt=f"""Verify code follows established patterns.

Target components: {requirement_summary['target_components']}
Change type: {requirement_summary['change_category']}

Pattern categories to check:
1. Error handling (try-except, exception chaining, logging)
2. Retry patterns (@retry decorator on Redis/HTTP calls)
3. Logging patterns (structured JSON, required fields)
4. Decorator patterns (order, required decorators)

Reference examples from codebase:
- Error handling: apps/execution_gateway/order_placer.py:50-60
- Retry patterns: libs/common/redis_client.py:20-30
- Logging: apps/signal_service/signal_generator.py:100-110

Tasks:
1. Extract existing pattern examples
2. Compare target code against patterns
3. Identify violations or missing patterns

Deliverable (JSON):
{{
  "pattern_compliance": {{
    "error_handling": {{
      "compliant": true|false,
      "violations": ["Missing exception chaining in apps/execution_gateway/order_placer.py:142"]
    }},
    "retries": {{
      "compliant": true|false,
      "missing": ["Redis call in libs/allocation/multi_alpha.py:80 needs @retry"]
    }},
    "logging": {{
      "compliant": true|false,
      "missing_fields": ["strategy_id missing in log call at apps/risk_manager/monitor.py:100"]
    }},
    "decorators": {{
      "compliant": true|false,
      "order_issues": []
    }}
  }},
  "patterns_to_follow": [
    "Add @retry decorator to all Redis calls (see libs/common/redis_client.py:20)",
    "Chain exceptions with 'from' clause (see apps/execution_gateway/order_placer.py:55)"
  ]
}}

Constraints: <7000 tokens, timeout 2 min
""",
    subagent_type="general-purpose"
)

# Wait for all 3 delegations to complete (3 min total in parallel)
```

**Output:** 3 analysis results (impacted components, tests, patterns)

---

### Step 3: Manual Language Assumption Review (5 min)

**Action:** Based on the discovered impacted components, a human should review the code for language-specific issues.

**⚠️ CRITICAL:** This step requires human judgment with concrete code context.

**Checks to perform:**
- Python gotchas (e.g., mutable default arguments, `global` statement conflicts, async/await misuse)
- Library version compatibility issues
- Framework-specific patterns (e.g., FastAPI dependency injection, pytest fixtures)
- Review specific dependencies identified in the `impacted_components` analysis

**Why manual review is required:**
- Generic language checks miss component-specific issues
- Requires understanding of actual code patterns and dependencies
- Needs context from Step 2 results to identify relevant files

**Output:** A list of potential language-related issues to consider during implementation.

---

### Step 4: Call Site Analysis (4 min, conditional)

**Only if signature change detected:**
```python
if requirement_summary.get("change_category") == "signature_change":
    # Note: The old and new function signatures must be determined before this step.
    # This might involve reading the source file or having it in the requirement.
    # Normalize target_components to prevent AttributeError on None
    target_components = requirement_summary.get("target_components") or {}
    functions = target_components.get("functions", [])
    modules = target_components.get("modules", [])

    # Guard against empty lists before indexing
    function_name = functions[0] if functions else "<undefined>"
    module_path = modules[0] if modules else "<undefined>"
    old_signature = "<old_signature_to_be_determined>"
    new_signature = "<new_signature_to_be_determined>"

    call_sites = Task(
        description="Analyze all call sites",
        prompt=f"""Analyze ALL call sites for function signature change.

Function: {function_name}
Module: {module_path}
Signature change:
  OLD: {old_signature}
  NEW: {new_signature}

Tasks:
1. Find ALL call sites (apps/, libs/, tests/)
2. For EACH call site:
   - Document file:line
   - Identify current error handling
   - Determine new error handling needed
   - Assess test impact

Repository: $PROJECT_ROOT

Deliverable (JSON):
{{
  "call_sites": [
    {{
      "file": "apps/execution_gateway/order_placer.py",
      "line": 142,
      "current_usage": "result = func(arg1, arg2)",
      "current_error_handling": "try-except ValueError",
      "new_error_handling_needed": "Add except TypeError",
      "test_impact": "Need new test for TypeError case"
    }}
  ],
  "summary": {{
    "total_call_sites": 15,
    "require_changes": 12,
    "no_changes_needed": 3
  }}
}}

Constraints: <10000 tokens, timeout 3 min
""",
        subagent_type="Explore"
    )
```

**Output:** Call site analysis with change requirements

---

### Step 5: Generate Component Breakdown (10 min) - HUMAN-GUIDED

**Action:** Aggregate analysis + manually create component todos **IN MAIN CONTEXT**

**⚠️ CRITICAL:** This is a "HOW to implement" task requiring strategic decomposition - **NEVER delegate** (see delegation-decision-tree.md Category 1).

```python
# Orchestrator aggregates all automated results IN MAIN CONTEXT
aggregated_analysis = {
    "requirement": requirement_summary,
    "impacted_components": impacted_components,
    "tests_to_update": tests_to_update,
    "pattern_parity": pattern_parity,
    "language_review_notes": [],  # Populated from Step 3 manual review
    "call_sites": call_sites if applicable else None
}

# HUMAN performs component breakdown in main context (10 min)
# Questions to guide breakdown:
# 1. What are the logical units of work? (database, API, validation, etc.)
# 2. What's the natural order? (schema → model → endpoint → tests)
# 3. What can be independently tested and committed?
# 4. What dependencies exist between components?

# For EACH component, create 6-step implementation checklist:
#   - Plan component approach
#   - Request plan review
#   - Implement logic
#   - Create test cases (success + failure)
#   - Request code review (clink + codex)
#   - Commit after approval + CI pass

# Template for each component:
"""
## Component N: {component_name}

- [ ] Implement {component_name} logic
- [ ] Create test cases for {component_name} (success + failure + edge cases)
- [ ] Request quick review (clink + codex) for {component_name}
- [ ] Run make ci-local for {component_name}
- [ ] Commit {component_name} (after review + CI pass)
"""

# Human review: Validate decomposition strategy (2 min)
```

**Output:** Markdown checklist with 6-step checklist per logical component (created in main context)

---

### Step 6: Generate Edge Cases (10 min) - HUMAN-GUIDED

**Action:** Identify comprehensive edge cases **IN MAIN CONTEXT**

**⚠️ CRITICAL:** This is a "HOW to implement" task requiring deep understanding of implementation strategy - **NEVER delegate** (see delegation-decision-tree.md Category 1).

```python
# HUMAN identifies edge cases in main context (10 min)
# Drawing from:
# - Component breakdown (what needs testing?)
# - Pattern parity requirements (what error handling patterns apply?)
# - Trading platform safety requirements (what failures are critical?)
# - Implementation strategy (what concurrency risks exist?)

# Categories to consider:
# 1. Normal operation edge cases (empty inputs, null, boundaries)
# 2. Failure scenarios (Redis down, API timeout, invalid data, state corruption)
# 3. Concurrency edge cases (race conditions, deadlocks, stale data)
# 4. Security edge cases (SQL injection, XSS, auth failures)

# Template:
edge_cases = {
  "normal_operation": [
    {"case": "Empty input list", "test_scenario": "allocate([])"},
    {"case": "Null strategy_id", "test_scenario": "allocate(strategy_id=None)"}
  ],
  "failure_scenarios": [
    {"case": "Redis unavailable", "expected_behavior": "Log error, raise RedisConnectionError"},
    {"case": "API timeout", "expected_behavior": "Retry 3 times with exponential backoff, then fail"}
  ],
  "concurrency": [
    {"case": "Simultaneous order submissions", "mitigation": "Use Redis locks"},
    {"case": "Stale position data", "mitigation": "Check timestamp, reject if >30s old"}
  ],
  "security": [
    {"case": "SQL injection in symbol parameter", "mitigation": "Use parameterized queries"},
    {"case": "Unauthorized access", "mitigation": "Check API key before processing"}
  ]
}

# Human review: Validate completeness + add domain-specific edge cases (2 min)
```

**Output:** Comprehensive edge case list categorized by type (created in main context)

---

### Step 7: Final Review & Approval (5 min)

**Action:** Present analysis summary to human for approval

**Summary Format:**
```markdown
# Pre-Implementation Analysis Summary

**Requirement:** {objective}
**Change Category:** {change_category}
**Time:** {analysis_duration} minutes

---

## Impacted Components ({total_count})

**Call Sites:** {call_site_count} files
- apps/execution_gateway/order_placer.py:142 (signature change)
- libs/risk_manager/limits.py:80 (new validation logic)
- ...

**Imports:** {import_count} files

**Schema Impact:** {migrations_needed ? "YES" : "NO"}
{if migrations_needed: "- Migration type: {migration_type}"}

**API Impact:** {breaking_changes ? "BREAKING CHANGES" : "No breaking changes"}

---

## Tests to Update ({total_test_count})

**Existing tests to update:** {existing_to_update}
- Unit: tests/apps/execution_gateway/test_order_placer.py
- Integration: tests/integration/test_order_flow.py

**New tests to create:** {new_to_create}
- Success: Test position limit validation passes for valid input
- Failure: Test validation error when limit exceeded
- Edge: Test null strategy_id handling

---

## Pattern Compliance

**Status:** {compliant ? "COMPLIANT" : "VIOLATIONS FOUND"}

{if violations:}
**Violations:**
- Missing @retry on Redis call in libs/allocation/multi_alpha.py:80
- Missing exception chaining in apps/execution_gateway/order_placer.py:142

**Patterns to follow:**
- Add @retry decorator (see libs/common/redis_client.py:20)
- Chain exceptions with 'from' (see apps/execution_gateway/order_placer.py:55)

---

## Edge Cases Identified ({edge_case_count} cases)

**Normal operation:** {normal_count} cases
**Failure scenarios:** {failure_count} cases
**Concurrency:** {concurrency_count} cases
**Security:** {security_count} cases

---

## Component Breakdown ({component_count} components)

{component_breakdown_markdown}

---

## Proceed with implementation?

[ ] YES - Start implementation with 6-step checklist
[ ] NO - Adjust analysis first

**Review checklist:**
- [ ] ALL impacted components identified
- [ ] ALL tests identified (existing + new)
- [ ] ALL edge cases documented
- [ ] ALL patterns verified
- [ ] Component breakdown complete
- [ ] NO uncertainties remaining
```

**Human Decision:** Approve or request adjustments (2 min)

---

### Step 8: Process Compliance Verification (5 min)

**Action:** Confirm ALL quality gates will be enforced during implementation

**MANDATORY Checklist:**

```markdown
## Process Compliance Verification

- [ ] **Review gate confirmation:**
  - Will code be reviewed BEFORE commit? (MANDATORY: YES)
  - Using zen-mcp clink with codex codereviewer? (MANDATORY: YES)
  - Review workflow: `./03-reviews.md`

- [ ] **CI gate confirmation:**
  - Will `make ci-local` run BEFORE commit? (MANDATORY: YES)
  - If tests fail, will commit be blocked? (MANDATORY: YES)
  - CI workflow: See 6-step checklist (step 4)

- [ ] **Approval gate confirmation:**
  - Does this require architectural approval? (YES/NO/N/A)
    - If YES: Create ADR before implementation (see `./05-operations.md`)
  - Does this introduce breaking changes? (YES/NO/N/A)
    - If YES: Requires user approval before implementation
  - Does this change API contracts? (YES/NO/N/A)
    - If YES: Requires ADR + user approval

- [ ] **Implementation discipline:**
  - Will 6-step checklist be followed for EACH component? (MANDATORY: YES)
  - Will components be committed separately? (MANDATORY: YES)
  - Will commits be blocked if review/CI fails? (MANDATORY: YES)
```

**Why This Matters:**

From `/tmp/ci-failure-root-cause-analysis.md`:
- **Skipping review gates** = PRIMARY root cause of 7 fix commits (10-15 hours wasted)
- **Skipping local CI** = 2-4x slower than running locally first
- **Incremental fixing** = Multiple fix commits instead of comprehensive upfront analysis

**Output:** ✅ Process compliance verified - ALL quality gates will be enforced

---

## Usage Example

### Example 1: Add Position Limit Validation

**Input:**
```markdown
# Requirement: Add position limit validation to order placement

Acceptance criteria:
- Reject orders exceeding max position per symbol (10,000 shares)
- Reject orders exceeding total portfolio notional ($1M)
- Log validation failures with order details
- Return clear error message to client
```

**Step 1: Parse Requirement (automated, 2 min)**
```json
{
  "objective": "Add position limit validation to order placement",
  "acceptance_criteria": [
    "Reject orders exceeding max position per symbol",
    "Reject orders exceeding total portfolio notional",
    "Log validation failures",
    "Return clear error message"
  ],
  "change_category": "new_feature",
  "target_components": {
    "modules": ["apps.execution_gateway.order_placer"],
    "functions": ["place_order"],
    "classes": ["OrderPlacer"]
  }
}
```

**Step 2: Parallel Discovery (automated, 3 min)**
- Impacted components: 15 files
- Tests to update: 12 existing, 8 new
- Call sites: 42 locations

**Step 3: Manual Language Review (human, 5 min)**
- Python gotchas identified
- Library compatibility checks
- Framework pattern verification

**Steps 4-6: Analysis & Planning (varies)**
- Pattern compliance: 3 violations (missing @retry, logging fields)
- Component breakdown: 4 components
- Edge cases: 24 cases identified

**Steps 7-8: Final Review & Compliance (8 min total)**
```markdown
# Analysis Summary

**Requirement:** Add position limit validation to order placement

## Impacted Components (15 files)
- apps/execution_gateway/order_placer.py (add validation)
- libs/risk_manager/limits.py (position limit logic)
- apps/risk_manager/monitor.py (circuit breaker integration)
- ... (12 more files)

## Component Breakdown (4 components)

### Component 1: Position Limit Validator
- [ ] Implement position limit validation logic
- [ ] Create test cases (success + failure + edge)
- [ ] Request quick review (clink + codex)
- [ ] Run make ci-local
- [ ] Commit after approval

### Component 2-4: ... (similar pattern)

**Proceed?** YES ✅
```

**Example Total Time:** 15 minutes (simple case)

**Note:** This is a simple example with 4 components and 15 impacted files. Complex cases with more components, deeper analysis, or architectural changes may take up to 55 minutes automated time (vs. 100 minutes manual baseline). See "Time Savings" in Quick Reference for typical case breakdown.

---

## Integration with Existing Workflows

This automated workflow **replaces** `./00-analysis-checklist.md` for most cases.

**When to use automated analysis:**
- New features (90% of cases)
- Bug fixes requiring impact analysis
- Refactoring with known scope
- Signature changes

**When to use manual 00-analysis-checklist.md:**
- Architectural changes requiring deep thought
- Security-critical changes needing manual verification
- First-time implementation (learning the checklist)

---

## Troubleshooting

### Delegation Timeout
**Symptom:** Task delegation exceeds 2-3 min timeout
**Fix:** Reduce scope, break into smaller delegations

### Incomplete Analysis
**Symptom:** Missing impacted components or tests
**Fix:** Re-run specific delegation with refined prompt

### Pattern Violations Not Detected
**Symptom:** Code review finds violations missed by automation
**Fix:** Update pattern parity delegation prompt with new patterns

---

## Success Criteria

Automated analysis succeeds when:

- [ ] Time reduction ≥40% (100 min → 60 min or less) [Achieved: 45% = 100 min → 55 min]
- [ ] ALL impacted components identified (0% miss rate)
- [ ] ALL tests identified (existing + new)
- [ ] Component breakdown complete with 6-step checklist
- [ ] Edge cases comprehensive (normal, failure, concurrency, security)
- [ ] Human approval obtained before implementation

---

## Related Workflows

- [00-analysis-checklist.md](./00-analysis-checklist.md) - Manual analysis (fallback)
- [16-subagent-delegation.md](./16-subagent-delegation.md) - Delegation patterns
- [01-git.md](./01-git.md) - Commit workflow
- [12-component-cycle.md](./12-component-cycle.md) - 6-step pattern
- [03-reviews.md](./03-reviews.md) - Review workflow

---

**Next Step:** Use component breakdown to start implementation with 6-step checklist
