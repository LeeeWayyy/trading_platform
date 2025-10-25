# ADR-0013: Workflow Automation Gates

- Status: Accepted
- Date: 2025-10-25

## Context

The trading platform's workflow processes (task reviews, ADR documentation, testing, documentation updates) rely on manual compliance. This creates risk:

**Current State:**
- Workflows document best practices but lack enforcement
- Quality depends on developer discipline and reviewer vigilance
- Easy to skip critical steps (task review, ADR documentation)
- No automated reminders for documentation maintenance
- Pre-existing pre-commit hook enforces CI checks only (mypy, ruff, tests)

**Problems:**
- **Task review skipping:** Developers may skip workflow 13 (task creation review) leading to unclear requirements and scope creep
- **Incomplete ADR documentation:** ADRs created without updating README, CONCEPTS/, or related docs
- **Test gaps:** New code committed without corresponding tests
- **Stale documentation:** Function signatures change without docstring updates
- **No #docs-only support:** Current pre-commit hook runs full CI even for documentation-only commits (experienced during P1T12)

**Why Now:**
- P1T12 (Workflow Review & Pre-commit Automation) identified these gaps
- Workflow audit revealed 17 workflows with varying compliance expectations
- Recent experience: #docs-only commits blocked by full CI checks (inefficient)
- Growing codebase requires systematic enforcement
- Educational project principle requires maintaining comprehensive documentation

**Non-Functional Requirement:**
**Simplicity and Maintainability** - Gates must be:
- Easy to understand and debug
- Simple implementation (prefer bash over complex frameworks)
- Clear error messages for developers
- Low maintenance burden
- Composable and testable

## Decision

Implement 4 pre-commit automation gates with progressive enforcement levels.

### Gate 1: Task Review Reminder (Non-blocking)

**Trigger:** Files matching `docs/TASKS/*_{TASK,PROGRESS}.md` staged

**Behavior:**
- Detect if task file is being created or updated
- Print reminder message:
```
ℹ️  WORKFLOW REMINDER: Task Review (Workflow 13)
────────────────────────────────────────────────
You're working on a task file. Consider validating it:
• Use workflow 13: Task Creation Review (clink + gemini planner)
• Validates scope clarity, requirement completeness
• 2-3 minutes now saves hours of rework

Skip for trivial tasks (<2 hours, well-defined).
See: .claude/workflows/13-task-creation-review.md
```

**Exit Code:** Always 0 (warning only, never blocks)

**Rationale:**
- Hard gate can be spoofed with fake validation markers
- Reminder reinforces process without friction
- Respects developer judgment (task complexity varies)
- Aligns with Gemini/Codex strategic recommendation
- Non-blocking preserves workflow flexibility

**Deduplication:** Print once per commit (track via temp file)

### Gate 2: ADR Documentation Completeness Check

**Trigger:** Files matching `docs/ADRs/*.md` created or modified (excluding 0000-template.md)

**Checks:**
1. **Core ADR validity:**
   - Status field present and valid (Proposed|Accepted|Superseded)
   - Required sections exist (Context, Decision, Consequences)
   - At least 200 words in Context section (ensures thought)

2. **Documentation ecosystem updates** (heuristics):
   - If ADR introduces "service" or "component" → Check README.md staged
   - If ADR introduces new architectural concept → Check `docs/CONCEPTS/` files staged
   - If ADR modifies "workflow" or "process" → Check `.claude/workflows/` staged

**Exit Codes:**
- `0` - All checks pass
- `1` - Warning: Heuristic suggests missing docs (lists what to check manually)
- `2` - Error: Core ADR invalid (missing sections, invalid status)

**Error Messages:**
```
⚠️  ADR Documentation Check (exit 1)
────────────────────────────────────────────────
docs/ADRs/0014-new-service.md mentions "service" but README.md not staged.

Consider if you need to:
• Update README.md with new service overview
• Create docs/CONCEPTS/ explaining the pattern

This is a heuristic - override if not applicable.
See: .claude/workflows/08-adr-creation.md Step 7
```

**Rationale:**
- Enforces ADR documentation update checklist (created in Phase 2c)
- Heuristic-based (not strict) to allow edge cases
- Exit 1 (warning) allows override for false positives
- Exit 2 (error) blocks objectively invalid ADRs

### Gate 3: Test Coverage Enforcement

**Trigger:** Files matching `apps/**/*.py` or `libs/**/*.py` modified (excluding `__init__.py`, `conftest.py`)

**Checks:**
1. **Test file exists:** For each modified source file, check corresponding test file exists
   - `apps/service/module.py` → `apps/service/tests/test_module.py`
   - `libs/package/file.py` → `libs/package/tests/test_file.py`

2. **Test file updated:** If source modified, check test file also staged
   - Exception: If test file recently updated (within 1 hour) or has 90%+ coverage for this module

3. **#skip-test-check marker:** Allow explicit skip with commit message marker
   - Supported markers: `#refactor-only`, `#docs-only`, `#test-update-deferred`
   - If `#test-update-deferred` used → Require TODO comment in code or follow-up ticket reference

**Exit Codes:**
- `0` - Test file exists and updated (or valid skip marker)
- `1` - Warning: New file without test (allows first commit without test if TODO present)
- `2` - Error: Existing file modified without test update AND no skip marker

**Error Messages:**
```
❌ Test Coverage Check (exit 2)
────────────────────────────────────────────────
Modified: apps/execution_gateway/order_placer.py
Missing:  apps/execution_gateway/tests/test_order_placer.py (not staged)

Action required:
• Update test file and stage it, OR
• Add commit message marker: #test-update-deferred + reference follow-up ticket

See: .claude/workflows/05-testing.md
```

**Rationale:**
- Enforces TDD/test-first principle
- Prevents "tests as afterthought"
- Allows pragmatic skips with explicit markers
- Exit 2 (error) blocks obvious violations
- Exit 1 (warning) for new files (allows initial implementation commit)

### Gate 4: Documentation Update Check (Docstrings)

**Trigger:** Files matching `apps/**/*.py` or `libs/**/*.py` modified (excluding tests)

**Checks:**
1. **Function signature changes detected:**
   - Parse staged diff for `def function_name(` changes
   - Detect: new functions, parameter additions/removals, type hint changes

2. **Docstring presence:**
   - For each changed function, verify docstring exists (triple-quoted string after def)
   - Check docstring mentions new parameters (simple keyword search)

3. **#docs-only support:**
   - If commit message contains `#docs-only` → Skip gate entirely (exit 0)
   - Allows pure documentation commits without running full CI

**Exit Codes:**
- `0` - Docstrings present and updated, OR `#docs-only` marker present
- `1` - Warning: New function without docstring (allows if TODO comment present)
- `2` - Error: Existing function signature changed without docstring update

**Error Messages:**
```
⚠️  Documentation Update Check (exit 1)
────────────────────────────────────────────────
New function: apps/risk_management/checker.py:check_volatility_limit()
Missing: Docstring

Action required:
• Add docstring following /docs/STANDARDS/DOCUMENTATION_STANDARDS.md, OR
• Add TODO comment if docstring deferred

See: .claude/workflows/07-documentation.md
```

**Rationale:**
- Enforces documentation standards (educational project principle)
- Supports `#docs-only` for pure documentation commits (addresses P1T12 finding)
- Pragmatic: warnings for new code, errors for modified code
- Simple implementation: diff parsing + keyword search (no AST analysis needed)

## Implementation Plan

**Scope:** Design only (this ADR). Implementation explicitly deferred to future tasks.

**Implementation Structure:**
```
tests/scripts/hooks/
├── pre-commit                       # Main hook (existing, minimal changes)
├── gates/
│   ├── 00-task-review-reminder.sh  # Gate 1 (non-blocking)
│   ├── 01-adr-documentation.sh     # Gate 2
│   ├── 02-test-coverage.sh         # Gate 3
│   └── 03-documentation-update.sh  # Gate 4
├── lib/
│   ├── common.sh                   # Shared: colors, logging, temp files
│   ├── git-utils.sh                # Shared: staged files, diff parsing
│   └── markers.sh                  # Shared: #docs-only, #test-update-deferred detection
└── tests/
    ├── test_gate_task_review.sh
    ├── test_gate_adr_docs.sh
    ├── test_gate_test_coverage.sh
    └── test_gate_doc_update.sh
```

**Integration Points:**
1. **Existing pre-commit hook** (`.git/hooks/pre-commit`):
   - Insert gate calls before CI checks
   - Order: Gate 1 → 2 → 3 → 4 → CI checks
   - Accumulate exit codes: max(all gate exits)
   - If any gate exits 2 → Block commit
   - If any gate exits 1 → Print warnings, allow commit
   - If all gates exit 0 → Proceed silently

2. **Commit message markers:**
   - `#docs-only` - Skips CI checks and Gate 4
   - `#test-update-deferred` - Skips Gate 3 (requires TODO or ticket reference)
   - `#refactor-only` - Skips Gate 3 (for pure refactors)

3. **Workflow updates:**
   - Mark enforced steps with "🔒 ENFORCED (planned):" prefix
   - Link to this ADR in workflow docs
   - Update 03-zen-review-quick.md to document #docs-only behavior

**Effort Estimate:**
- Gate 1 (Task Review Reminder): 2 hours (simple echo + temp file dedup)
- Gate 2 (ADR Documentation): 6 hours (heuristics, multi-file checks)
- Gate 3 (Test Coverage): 8 hours (path mapping, coverage integration)
- Gate 4 (Documentation Update): 6 hours (diff parsing, docstring detection)
- Integration + testing: 4 hours
- **Total: 26 hours (~3-4 days)**

**Follow-up Tickets:**
- **P1.1T1:** Implement Gate 1 (Task Review Reminder) + Gate 4 (#docs-only support) [Priority: HIGH, 8 hours]
- **P1.1T2:** Implement Gate 2 (ADR Documentation) [Priority: MEDIUM, 6 hours]
- **P1.1T3:** Implement Gate 3 (Test Coverage) [Priority: MEDIUM, 8 hours]
- **P1.1T4:** Integration testing + documentation [Priority: LOW, 4 hours]

**Testing Strategy:**
- Unit tests for each gate (bash test framework)
- Integration test: simulate commits with various scenarios
- Test matrix: all combinations of markers × file types × gate triggers
- Validate: false positive rate <5%, false negative rate <1%

## Alternatives Considered

### Alternative 1: GitHub Actions Pre-merge Checks Only

**Description:** Skip pre-commit hooks, enforce everything in CI/CD

**Pros:**
- Centralized enforcement (no local hook installation)
- Easier to update (no `make install-hooks`)
- Can't be bypassed with `--no-verify`

**Cons:**
- Slower feedback (wait for CI run)
- Wastes CI minutes on obvious violations
- Breaks fast local iteration cycle
- Still requires pre-commit for local testing to match CI

**Why Not Chosen:** Local pre-commit hooks provide faster feedback and preserve developer flow.

### Alternative 2: Git Hook Framework (Husky, Pre-commit.com)

**Description:** Use existing hook management framework

**Pros:**
- Standard tool, well-documented
- Config-driven (YAML)
- Built-in hook orchestration
- Multi-language support

**Cons:**
- Additional dependency (Python or Node)
- Overkill for 4 simple gates
- Harder to debug (abstraction layer)
- Violates **Simplicity and Maintainability** NFR

**Why Not Chosen:** Bash scripts are simpler, easier to debug, and sufficient for our needs.

### Alternative 3: Strict Enforcement (All Gates Exit 2)

**Description:** Make all gates blocking (exit 2 on any violation)

**Pros:**
- Strongest enforcement
- No ambiguity (all rules mandatory)
- Highest quality bar

**Cons:**
- Inflexible for edge cases
- False positives block legitimate work
- Encourages `--no-verify` overuse
- Heuristics (Gate 2) will have false positives

**Why Not Chosen:** Progressive enforcement (exit 0/1/2) balances quality with pragmatism.

### Alternative 4: AST-based Analysis (pylint, mypy plugins)

**Description:** Use Python AST parsing for Gate 3 & 4 instead of diff parsing

**Pros:**
- More accurate (understands Python semantics)
- Can detect complex refactors
- Integrates with existing tooling

**Cons:**
- Complex implementation (AST traversal)
- Slower execution (parse all files)
- Harder to debug and maintain
- Violates **Simplicity and Maintainability** NFR

**Why Not Chosen:** Diff parsing + heuristics are "good enough" and much simpler.

## Consequences

### Positive

1. **Reduced workflow violations**
   - Task reviews recommended at point of need
   - ADR documentation kept in sync
   - Test coverage enforced systematically
   - Documentation kept up-to-date

2. **Faster feedback cycles**
   - Violations caught locally (not in PR review)
   - #docs-only commits skip unnecessary CI
   - Clear error messages guide fixes

3. **Educational value maintained**
   - Documentation standards enforced automatically
   - Aligns with project's learning-focused mission
   - Comprehensive docs for future contributors

4. **Progressive enforcement**
   - Warnings for new code (exit 1)
   - Errors for violations (exit 2)
   - Reminders for best practices (exit 0)
   - Respects developer judgment

5. **Simplicity achieved**
   - Bash scripts (easy to debug)
   - No external frameworks
   - Composable gates (testable independently)
   - Clear integration points

### Negative

1. **Implementation effort**
   - 26 hours across 4 follow-up tickets
   - Testing matrix is non-trivial
   - Maintenance burden (new gates, updates)

2. **False positives inevitable**
   - Heuristics (Gate 2) will miss context
   - Diff parsing (Gate 4) may miss refactors
   - Developers need override mechanisms

3. **Developer friction**
   - Additional steps before commit
   - Learning curve for markers (#docs-only, etc.)
   - May slow initial commits

4. **Hook bypass risk**
   - `--no-verify` still works
   - Can't prevent determined bypass
   - Relies on culture + PR review as backstop

### Risks

1. **High false positive rate**
   - **Mitigation:** Exit 1 (warning) for heuristics, allow override
   - **Monitoring:** Track false positive rate, iterate on heuristics

2. **Developers bypass hooks routinely**
   - **Mitigation:** Make error messages helpful, not punitive
   - **Monitoring:** PR reviewers check for --no-verify in commit history

3. **Gates break due to Git changes**
   - **Mitigation:** Comprehensive test suite, integration tests
   - **Monitoring:** CI tests for gate functionality

4. **Maintenance burden grows**
   - **Mitigation:** Keep gates simple (bash only), document thoroughly
   - **Monitoring:** Review quarterly, deprecate unused gates

### Follow-ups

1. **Immediate (P1T12 completion):**
   - Mark enforced steps in workflows with "🔒 ENFORCED (planned):"
   - Update 03-zen-review-quick.md with #docs-only documentation
   - Create follow-up tickets (P1.1T1-4)

2. **After Gate Implementation (P1.1T1-4 completion):**
   - Monitor false positive rates (target: <5%)
   - Gather developer feedback (survey after 2 weeks)
   - Iterate on heuristics if needed

3. **Quarterly Review:**
   - Assess gate effectiveness (violations before/after)
   - Review maintenance burden
   - Consider new gates or deprecate unused ones

## Migration Plan

**Phase 1: Design & Documentation (This ADR)**
- Status: COMPLETE
- Deliverables: This ADR, updated workflows with markers

**Phase 2: High-Priority Gates (P1.1T1)**
- Implement Gate 1 (Task Review Reminder) - Addresses workflow adoption
- Implement Gate 4 (#docs-only support) - Addresses P1T12 finding
- Timeline: 8 hours
- Risk: LOW (non-blocking reminder + simple marker check)

**Phase 3: ADR Documentation Gate (P1.1T2)**
- Implement Gate 2 (ADR Documentation Completeness)
- Timeline: 6 hours
- Risk: MEDIUM (heuristics may have false positives)

**Phase 4: Test Coverage Gate (P1.1T3)**
- Implement Gate 3 (Test Coverage Enforcement)
- Timeline: 8 hours
- Risk: MEDIUM (path mapping edge cases)

**Phase 5: Integration & Rollout (P1.1T4)**
- Integration testing
- Update all workflow documentation
- Developer communication (announce gates, provide examples)
- Timeline: 4 hours
- Risk: LOW

**Rollback Plan:**
- Gates implemented as separate scripts (can disable individually)
- Rollback: Comment out gate call in pre-commit hook
- Preserve all commit history (gates don't modify files)

## Related ADRs

- **ADR-0008:** Git Workflow and Progressive Commits (this enhances enforcement)
- **ADR-0012:** Prometheus/Grafana Monitoring (example of comprehensive ADR documentation this gate will enforce)

## Implementation Notes

**Key Design Principles:**

1. **Fail-safe:** If gate script crashes → Exit 0 (don't block commit)
2. **Fast:** Each gate <1 second execution (total <4 seconds)
3. **Clear errors:** Error messages include:
   - What failed
   - Why it matters
   - How to fix (command or workflow link)
   - How to override (if applicable)
4. **Testable:** Each gate has unit tests + integration tests
5. **Debuggable:** `DEBUG=1 git commit` enables verbose output

**Example Gate Implementation (Gate 1):**

```bash
#!/bin/bash
# tests/scripts/hooks/gates/00-task-review-reminder.sh

set -euo pipefail

# Source shared utilities
source "$(dirname "$0")/../lib/common.sh"
source "$(dirname "$0")/../lib/git-utils.sh"

# Gate 1: Task Review Reminder (Non-blocking)
main() {
    # Check if task files staged
    local task_files
    task_files=$(get_staged_files | grep -E 'docs/TASKS/.*_(TASK|PROGRESS)\.md$' || true)

    if [[ -z "$task_files" ]]; then
        exit 0  # No task files, skip
    fi

    # Check if already reminded this commit (dedup)
    local lock_file="/tmp/claude-task-reminder-$$.lock"
    if [[ -f "$lock_file" ]]; then
        exit 0  # Already reminded
    fi
    touch "$lock_file"
    trap "rm -f $lock_file" EXIT

    # Print reminder
    print_info "WORKFLOW REMINDER: Task Review (Workflow 13)"
    echo "────────────────────────────────────────────────"
    echo "You're working on a task file. Consider validating it:"
    echo "• Use workflow 13: Task Creation Review (clink + gemini planner)"
    echo "• Validates scope clarity, requirement completeness"
    echo "• 2-3 minutes now saves hours of rework"
    echo ""
    echo "Skip for trivial tasks (<2 hours, well-defined)."
    echo "See: .claude/workflows/13-task-creation-review.md"
    echo ""

    exit 0  # Always non-blocking
}

main "$@"
```

**Example Integration (pre-commit hook update):**

```bash
# .git/hooks/pre-commit (updated)

# ... existing code ...

# ==============================================================================
# QUALITY GATES: Workflow Automation (ADR-0013)
# ==============================================================================
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}🔒 QUALITY GATES: Workflow Automation${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

GATE_EXIT=0

# Gate 1: Task Review Reminder (non-blocking)
if tests/scripts/hooks/gates/00-task-review-reminder.sh; then
    : # Success, continue
else
    GATE_EXIT=$((GATE_EXIT > $? ? GATE_EXIT : $?))  # Max exit code
fi

# Gate 2: ADR Documentation
if tests/scripts/hooks/gates/01-adr-documentation.sh; then
    print_status 0 "ADR documentation check passed"
else
    exit_code=$?
    GATE_EXIT=$((GATE_EXIT > exit_code ? GATE_EXIT : exit_code))
    if [[ $exit_code -eq 1 ]]; then
        print_status 1 "ADR documentation check warning (see above)"
    else
        print_status 1 "ADR documentation check failed"
        FAILED=1
    fi
fi

# Gate 3: Test Coverage
# ... similar pattern ...

# Gate 4: Documentation Update
# ... similar pattern ...

# Continue to CI checks if gates passed...
```

**Error Message Template:**

All gates follow this template for consistency:

```
[EMOJI] [GATE NAME] (exit [CODE])
────────────────────────────────────────────────
[SPECIFIC VIOLATION]

[WHY IT MATTERS]

Action required:
• [FIX OPTION 1]
• [FIX OPTION 2 (if applicable)]

See: [WORKFLOW LINK]
```

Examples:
- Gate 1: ℹ️ (info, blue)
- Gate 2: ⚠️ (warning, yellow) or ❌ (error, red)
- Gate 3: ⚠️ or ❌
- Gate 4: ⚠️ or ❌

---

**This ADR documents the design. Implementation tracking:**
- [ ] P1.1T1: Gates 1 & 4 (HIGH priority)
- [ ] P1.1T2: Gate 2 (MEDIUM priority)
- [ ] P1.1T3: Gate 3 (MEDIUM priority)
- [ ] P1.1T4: Integration & rollout (LOW priority)
