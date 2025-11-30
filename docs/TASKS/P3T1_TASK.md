# P3T1: Workflow Modernization - Task Document

**Task ID:** P3T1
**Phase:** P3 (Review, Remediation & Modernization)
**Track:** Track 1 - Workflow Modernization
**Priority:** FIRST PRIORITY (improves velocity for all subsequent fixes)
**Estimated Effort:** 14-20 hours (5 subtasks) - REVISED per Gemini/Codex review
**Status:** APPROVED (Ready for Implementation)
**Created:** 2025-11-29
**Last Updated:** 2025-11-29 (v2.0 - Gemini/Codex review feedback incorporated)

---

## Executive Summary

Replace the monolithic `workflow_gate.py` (4,369 lines) with a modular workflow package from `general_ai_guide`. This modernization improves development velocity, enables better testing, and provides a cleaner architecture for the 47+ fixes in subsequent tracks.

**Goal:** Modular, well-tested workflow management with reduced complexity

**Key Deliverables:**
1. Backup current workflow_gate.py with integrity verification
2. Copy ai_workflow/ package from general_ai_guide
3. Adapt configuration for trading_platform (master branch, paths)
4. Validate all CLI commands (including failure paths)
5. Update documentation and hooks
6. **[NEW - Reviewer Feedback]** Legacy test cleanup & CI integration

---

## Context & Dependencies

### Source Package Location
```
/Users/leeewayyy/Documents/SourceCode/general_ai_guide/scripts/
├── workflow_gate.py          # 952 lines (new CLI entry point)
└── ai_workflow/              # ~85KB modular package
    ├── __init__.py           (2,029 bytes)
    ├── config.py             (3,429 bytes) - Configuration management
    ├── constants.py          (1,826 bytes) - Paths and shared constants
    ├── core.py               (22,186 bytes) - Main WorkflowGate class
    ├── delegation.py         (11,589 bytes) - Agent delegation
    ├── git_utils.py          (5,645 bytes) - Git operations
    ├── hash_utils.py         (4,477 bytes) - Code fingerprinting
    ├── pr_workflow.py        (14,574 bytes) - PR handling
    ├── reviewers.py          (7,098 bytes) - Review orchestration
    ├── subtasks.py           (12,134 bytes) - Subtask management
    └── tests/                # ~144KB comprehensive tests
        ├── conftest.py       (5,249 bytes)
        ├── test_cli.py       (18,721 bytes)
        ├── test_config.py    (11,003 bytes)
        ├── test_constants.py (6,859 bytes)
        ├── test_core.py      (20,132 bytes)
        ├── test_delegation.py (12,184 bytes)
        ├── test_git_utils.py  (9,699 bytes)
        ├── test_hash_utils.py (8,724 bytes)
        ├── test_pr_workflow.py (15,633 bytes)
        ├── test_reviewers.py  (16,953 bytes)
        └── test_subtasks.py   (19,186 bytes)
```

### Target Location
```
/Users/leeewayyy/Documents/SourceCode/trading_platform/scripts/
├── workflow_gate.py          # New 952-line CLI entry point
├── workflow_gate.py.bak      # Backup of original 4,369-line file
└── ai_workflow/              # Copied and adapted package
```

### Current State vs Target

| Metric | Current | Target |
|--------|---------|--------|
| **workflow_gate.py lines** | 4,369 | 952 |
| **Modular structure** | No (monolith) | Yes (10 modules) |
| **Test coverage** | Limited | Comprehensive (~144KB) |
| **File locking** | Unknown | Yes (atomic operations) |
| **State migration** | N/A | Built-in (.claude → .ai_workflow) |

### Dependencies Verification (Gemini Review Item)

**CONFIRMED:** The ai_workflow package uses **only standard library** modules:
- `copy`, `fcntl`, `hashlib`, `json`, `os`, `re`, `subprocess`, `sys`, `tempfile`, `time`, `uuid`
- `contextlib`, `collections.abc`, `dataclasses`, `datetime`, `enum`, `pathlib`, `typing`

**No new PyPI dependencies required.**

### Legacy Tests to Archive (Gemini/Codex Review Finding)

The following tests depend on the old monolithic workflow_gate.py:
```
tests/scripts/test_workflow_gate_delegation.py
tests/scripts/test_workflow_gate_smoke.py
tests/scripts/test_workflow_gate_cli.py
tests/scripts/test_workflow_gate_plan_review.py
```
**Action:** Archive to `tests/scripts/_archived_legacy/` in T1.5

---

## Pre-Implementation Analysis

### 1. Required Adaptations

**A. `constants.py` adaptations:**
```python
# Current (general_ai_guide):
PROJECT_ROOT = Path(__file__).parent.parent.parent  # Auto-calculated ✓
WORKFLOW_DIR = Path(".ai_workflow")                  # Tool-agnostic ✓

# No changes needed - paths are relative and auto-calculated
```

**B. `config.py` adaptations:**
```python
# Current default:
"git": {
    "default_base_branch": "main"
}

# Required for trading_platform:
"git": {
    "default_base_branch": "master"  # Trading platform uses master
}
```

**C. Legacy migration support:**
```python
# Already built-in:
LEGACY_CLAUDE_DIR = Path(".claude")
LEGACY_STATE_FILE = LEGACY_CLAUDE_DIR / "workflow-state.json"
# Automatic migration from .claude/ → .ai_workflow/
```

### 2. Files to Create/Modify

**CREATE:**
```
scripts/ai_workflow/              # Full package copy
scripts/ai_workflow/tests/        # Test suite copy
.ai_workflow/config.json          # Auto-created on first run
```

**MODIFY:**
```
scripts/workflow_gate.py          # Replace with new CLI
scripts/ai_workflow/config.py     # Update default_base_branch → "master"
CLAUDE.md                         # Update workflow command examples
docs/AI/Workflows/README.md       # Update workflow documentation
```

**BACKUP:**
```
scripts/workflow_gate.py.bak      # Preserve original for rollback
```

### 3. Feature Comparison

| Feature | Old (monolith) | New (modular) |
|---------|----------------|---------------|
| State management | In main file | `core.py` |
| File locking | Unknown | Yes (fcntl) |
| Review tracking | Partial | Full (gemini + codex) |
| Code fingerprinting | No | Yes (hash_utils) |
| PR workflow | No | Yes (pr_workflow.py) |
| Subtask management | Partial | Full (subtasks.py) |
| Delegation | Partial | Full (delegation.py) |
| Configuration | Hardcoded | JSON config file |
| State migration | No | Yes (.claude → .ai_workflow) |
| Comprehensive tests | No | Yes (~144KB) |

### 4. Risk Analysis

**Risk 1: Breaking existing workflows**
- **Likelihood:** MEDIUM
- **Impact:** HIGH
- **Mitigation:** Keep backup, test all commands before removing backup

**Risk 2: State file incompatibility**
- **Likelihood:** LOW
- **Impact:** MEDIUM
- **Mitigation:** Built-in migration from .claude/ to .ai_workflow/

**Risk 3: Import path issues**
- **Likelihood:** MEDIUM
- **Impact:** MEDIUM
- **Mitigation:** Verify sys.path in CLI entry point matches trading_platform structure

**Risk 4: CI failure due to broken legacy tests** [NEW - Gemini Review]
- **Likelihood:** HIGH
- **Impact:** HIGH
- **Mitigation:** Archive legacy tests before replacing workflow_gate.py (T1.5)

**Risk 5: Tests expecting "main" branch** [NEW - Codex Review]
- **Likelihood:** MEDIUM
- **Impact:** MEDIUM
- **Mitigation:** Update test fixtures/docs to expect "master" in T1.2

**Risk 6: Executable permission loss on copy** [NEW - Codex Review]
- **Likelihood:** MEDIUM
- **Impact:** HIGH
- **Mitigation:** Verify and restore `chmod +x` after copy in T1.1

---

## Implementation Plan

### Subtask T1.1: Backup & Copy Workflow Package (2-4 hours)

**Steps:**
1. Backup current workflow_gate.py with integrity verification
2. Copy ai_workflow/ package
3. Copy new workflow_gate.py CLI
4. Verify file structure and permissions
5. **[NEW - Codex]** Verify executable permissions restored

**Commands:**
```bash
# 1. Backup with integrity check
cp scripts/workflow_gate.py scripts/workflow_gate.py.bak
md5sum scripts/workflow_gate.py.bak scripts/workflow_gate.py  # Should match

# 2. Copy package
cp -r /Users/leeewayyy/Documents/SourceCode/general_ai_guide/scripts/ai_workflow scripts/

# 3. Copy CLI entry point
cp /Users/leeewayyy/Documents/SourceCode/general_ai_guide/scripts/workflow_gate.py scripts/

# 4. [NEW - Codex] Restore executable permission
chmod +x scripts/workflow_gate.py

# 5. Verify file count matches source
ls scripts/ai_workflow/*.py | wc -l  # Should be 10
ls /Users/leeewayyy/Documents/SourceCode/general_ai_guide/scripts/ai_workflow/*.py | wc -l  # Compare
```

**Verification:**
```bash
# Verify structure
ls -la scripts/ai_workflow/

# Verify imports work
python3 -c "from scripts.ai_workflow import core; print('Import OK')"

# [NEW - Codex] Verify executable
test -x scripts/workflow_gate.py && echo "Executable OK"

# [NEW - Codex] Verify backup integrity
diff -q scripts/workflow_gate.py.bak <(cat scripts/workflow_gate.py.bak) && echo "Backup integrity OK"
```

**Test Cases:**
- [ ] All files copied successfully (file count: source=10, target=10)
- [ ] No import errors when loading package
- [ ] `python3 -c "from scripts.ai_workflow import core"` succeeds
- [ ] **[NEW - Codex]** Backup checksum matches original
- [ ] **[NEW - Codex]** workflow_gate.py has executable permission
- [ ] **[NEW - Codex]** Shebang line intact (`#!/usr/bin/env python3`)

---

### Subtask T1.2: Adapt Configuration & Paths (4-6 hours)

**Steps:**
1. Update `default_base_branch` from "main" to "master"
2. Verify PROJECT_ROOT resolves correctly
3. Test config.json auto-creation
4. **[NEW - Codex]** Update test fixtures expecting "main" to use "master"
5. **[NEW - Codex]** Create explicit migration test for .claude → .ai_workflow

**Configuration Changes:**

**`scripts/ai_workflow/config.py`:**
```python
# Line ~30: Change default_base_branch
DEFAULT_CONFIG = {
    # ...
    "git": {
        "push_retry_count": 3,
        "default_base_branch": "master"  # Changed from "main"
    },
    # ...
}
```

**Test Fixtures Update (Codex Finding):**
```bash
# Find all occurrences of "main" in test fixtures
grep -rn '"main"' scripts/ai_workflow/tests/ | grep -i branch

# Update each to "master"
# Example: test_config.py fixture
```

**Verification:**
```bash
# Remove any existing config to test auto-creation
rm -f .ai_workflow/config.json

# Run status to trigger config creation
./scripts/workflow_gate.py status

# Verify config
cat .ai_workflow/config.json | grep default_base_branch
```

**[NEW - Codex] Migration Test:**
```bash
# 1. Seed legacy state
mkdir -p .claude
echo '{"step": "implement", "current_component": "TestComp"}' > .claude/workflow-state.json

# 2. Run status (triggers migration)
./scripts/workflow_gate.py status

# 3. Verify data migrated to .ai_workflow
cat .ai_workflow/workflow-state.json | grep TestComp  # Should find it

# 4. Cleanup
rm -rf .claude
```

**Test Cases:**
- [ ] `test_config_paths.py` - All paths resolve correctly
- [ ] `test_config_loading.py` - config.json loads without errors
- [ ] `test_project_root.py` - PROJECT_ROOT matches trading_platform
- [ ] `test_default_branch.py` - default_base_branch is "master"
- [ ] **[NEW - Codex]** Test fixtures use "master" not "main"
- [ ] **[NEW - Codex]** Migration test: .claude state → .ai_workflow without data loss

---

### Subtask T1.3: Validate CLI Commands (4-6 hours)

**Commands to Test:**

| Command | Purpose | Expected Behavior |
|---------|---------|-------------------|
| `status` | Show current state | JSON output with step, component, reviews |
| `start-task` | Initialize new task | Creates state, sets task_file |
| `set-components` | Set component list | Updates state.components |
| `advance` | Transition step | Validates transition, updates step |
| `request-review` | Trigger review | Outputs review instructions |
| `check-commit` | Pre-commit validation | Returns 0 if ready, 1 if blocked |
| `record-analysis-complete` | Mark analysis done | Sets analysis_completed=true |
| `set-component` | Set current component | Updates current_component |

**Test Sequence:**
```bash
# 1. Status (should work even with no state)
./scripts/workflow_gate.py status

# 2. Start task
./scripts/workflow_gate.py start-task P3T1 feature/P3T1-workflow-modernization

# 3. Set components
./scripts/workflow_gate.py set-components "Backup & Copy" "Adapt Config" "Validate CLI" "Documentation"

# 4. Record analysis complete
./scripts/workflow_gate.py record-analysis-complete

# 5. Set component
./scripts/workflow_gate.py set-component "Backup & Copy"

# 6. Advance through steps
./scripts/workflow_gate.py advance plan-review
./scripts/workflow_gate.py advance implement
./scripts/workflow_gate.py advance test
./scripts/workflow_gate.py advance review

# 7. Request review
./scripts/workflow_gate.py request-review commit

# 8. Check commit readiness
./scripts/workflow_gate.py check-commit
```

**Test Cases:**
- [ ] `test_cli_status.py` - Status returns valid JSON
- [ ] `test_cli_start_task.py` - Task creation works
- [ ] `test_cli_set_components.py` - Components stored correctly
- [ ] `test_cli_advance.py` - All state transitions valid
- [ ] `test_cli_request_review.py` - Review request outputs instructions
- [ ] `test_cli_check_commit.py` - Pre-commit validation works
- [ ] Integration test: Full 6-step workflow cycle

**[NEW - Codex] Negative-Path Test Cases:**
- [ ] `test_check_commit_blocked.py` - Returns non-zero when gates unmet
- [ ] `test_advance_invalid_transition.py` - Rejects invalid step transitions
- [ ] `test_pre_commit_hook_fail.py` - Hook blocks commit on invalid state
- [ ] `test_lock_contention.py` - Two parallel status calls don't deadlock

**[NEW - Codex] Pre-commit Hook Simulation:**
```bash
# Simulate pre-commit hook behavior
cd $(mktemp -d)
git init
mkdir -p scripts
cp /path/to/trading_platform/scripts/workflow_gate.py scripts/
chmod +x scripts/workflow_gate.py

# Test hook returns non-zero when gates unmet
./scripts/workflow_gate.py check-commit
echo "Exit code: $?"  # Should be non-zero
```

---

### Subtask T1.4: Update Documentation & Hooks (2-4 hours)

**Files to Update:**

**1. CLAUDE.md** - Update workflow command examples:
```markdown
# Development Process section
./scripts/workflow_gate.py start-task P3T1 feature/P3T1-workflow
./scripts/workflow_gate.py set-components "Component 1" "Component 2"
./scripts/workflow_gate.py record-analysis-complete
./scripts/workflow_gate.py set-component "Component 1"
./scripts/workflow_gate.py advance plan-review
# ... etc
```

**2. docs/AI/Workflows/README.md** - Update workflow documentation:
- Document new .ai_workflow/ directory
- Document config.json options
- Update examples to match new CLI

**3. Pre-commit hook verification:**
- Verify pre-commit hook still calls `./scripts/workflow_gate.py check-commit`
- Test hook enforcement works

**[NEW - Codex] 4. Additional docs to sweep:**
- `docs/AI/AGENTS.md` - Update any workflow references
- `docs/AI/Workflows/*.md` - All workflow-related docs
- Any onboarding scripts referencing old workflow

**Test Cases:**
- [ ] Pre-commit hook runs without errors
- [ ] CLAUDE.md examples execute successfully
- [ ] `make ci-local` passes with new workflow
- [ ] **[NEW - Codex]** All docs use "master" not "main"
- [ ] **[NEW - Codex]** Command examples in docs are executable

---

### Subtask T1.5: Legacy Test Cleanup & CI Integration (2-4 hours) [NEW - Gemini/Codex]

**Purpose:** Address Gemini/Codex finding that legacy tests will break CI and new tests won't run.

**Steps:**
1. Archive legacy workflow tests
2. Update pytest.ini to include scripts/ in testpaths
3. Update Makefile for coverage of ai_workflow
4. Verify all tests pass

**Legacy Tests to Archive:**
```bash
# Create archive directory
mkdir -p tests/scripts/_archived_legacy/

# Move legacy tests
mv tests/scripts/test_workflow_gate_delegation.py tests/scripts/_archived_legacy/
mv tests/scripts/test_workflow_gate_smoke.py tests/scripts/_archived_legacy/
mv tests/scripts/test_workflow_gate_cli.py tests/scripts/_archived_legacy/
mv tests/scripts/test_workflow_gate_plan_review.py tests/scripts/_archived_legacy/
```

**pytest.ini Updates:**
```ini
# Before:
testpaths = tests apps libs

# After:
testpaths = tests apps libs scripts
```

**Makefile Updates:**
```makefile
# Update coverage to include scripts
--cov=libs --cov=apps --cov=scripts/ai_workflow
```

**Test Cases:**
- [ ] Legacy tests archived (not deleted - for reference)
- [ ] `make ci-local` runs without failures
- [ ] New ai_workflow tests are executed by CI
- [ ] Coverage includes scripts/ai_workflow
- [ ] No import errors from legacy test removal

**Acceptance Criteria (Full T1):**
- [ ] All workflow commands work correctly
- [ ] All workflow tests pass (run from scripts/ai_workflow/tests/)
- [ ] Pre-commit hook enforces gates
- [ ] Documentation updated with new paths and commands
- [ ] Old workflow_gate.py.bak preserved for rollback
- [ ] CI passes with new test structure
- [ ] Legacy tests archived (not breaking CI)
- [ ] Coverage includes ai_workflow package

---

## Testing Strategy

### Test Location
```
scripts/ai_workflow/tests/
├── conftest.py           # Shared fixtures
├── test_cli.py           # CLI command tests
├── test_config.py        # Configuration tests
├── test_constants.py     # Constants validation
├── test_core.py          # Core workflow logic
├── test_delegation.py    # Delegation tests
├── test_git_utils.py     # Git operation tests
├── test_hash_utils.py    # Hashing tests
├── test_pr_workflow.py   # PR workflow tests
├── test_reviewers.py     # Reviewer tests
└── test_subtasks.py      # Subtask tests
```

### Running Tests
```bash
# Run all workflow tests
python3 -m pytest scripts/ai_workflow/tests/ -v

# Run with coverage
python3 -m pytest scripts/ai_workflow/tests/ -v --cov=scripts/ai_workflow --cov-report=term-missing
```

### Integration Tests (Manual)
1. Full 6-step workflow cycle with new CLI
2. Pre-commit hook enforcement
3. Review request → approval flow
4. State persistence across sessions

---

## Rollback Plan

If the new workflow fails in production:

```bash
# 1. Restore CLI backup
cp scripts/workflow_gate.py.bak scripts/workflow_gate.py
chmod +x scripts/workflow_gate.py

# 2. Remove new package
rm -rf scripts/ai_workflow/

# 3. Remove new state directory (preserve old .claude/ if exists)
rm -rf .ai_workflow/

# 4. Verify old workflow works
./scripts/workflow_gate.py status

# 5. [NEW - Codex] Restore legacy tests
mv tests/scripts/_archived_legacy/*.py tests/scripts/

# 6. [NEW - Codex] Revert pytest.ini changes
# Restore: testpaths = tests apps libs
# Remove: scripts from testpaths

# 7. [NEW - Codex] Revert documentation if changed
git checkout -- CLAUDE.md docs/AI/Workflows/
```

**[NEW - Codex] Important Rollback Notes:**
- Keep .claude/ state if it exists (don't delete during rollback)
- Verify pre-commit hook works with restored CLI
- Run `make ci-local` after rollback to confirm tests pass

---

## Success Metrics

| Metric | Target | Verification |
|--------|--------|--------------|
| **Lines of code** | <1,000 (CLI) | `wc -l scripts/workflow_gate.py` |
| **Modules** | 10 | `ls scripts/ai_workflow/*.py \| wc -l` |
| **Test coverage** | >90% | pytest --cov report |
| **All commands work** | 100% | Manual CLI validation |
| **CI passes** | Yes | `make ci-local` |

---

## Open Questions

### Q1: Should we keep .claude/ or migrate to .ai_workflow/?
**Decision:** Migrate to .ai_workflow/ - the new package has built-in migration support
**Rationale:** Tool-agnostic naming is better for future flexibility

### Q2: Should we archive or delete the backup after successful validation?
**Decision:** Keep backup for 1 week, then archive to docs/archive/ or delete
**Rationale:** Safety first, but avoid permanent clutter

---

## References

- [P3_PLANNING.md](./P3_PLANNING.md) - Track 1 specification
- [general_ai_guide/scripts/](../../general_ai_guide/scripts/) - Source package
- [docs/AI/Workflows/](../AI/Workflows/) - Workflow documentation

---

## Review Log

### Review 1: Gemini + Codex (2025-11-29)

**Gemini Review:**
- Status: CHANGES_REQUESTED
- Key findings:
  1. Legacy tests in `tests/scripts/` will break CI → Added T1.5
  2. New tests won't run (pytest.ini missing scripts/) → Added T1.5
  3. Confirm no new PyPI dependencies → CONFIRMED: all stdlib
  4. Add "CI failure due to broken legacy tests" risk → Added Risk 4

**Codex Review:**
- Status: CHANGES_REQUESTED
- Key findings:
  1. Add permission/exec-bit verification after copy → Added to T1.1
  2. Add checksum integrity check for backup → Added to T1.1
  3. Tests expecting "main" will fail → Added to T1.2
  4. Need explicit migration test .claude → .ai_workflow → Added to T1.2
  5. Missing failure-path tests for check-commit → Added to T1.3
  6. Missing pre-commit hook simulation → Added to T1.3
  7. Missing lock contention test → Added to T1.3
  8. Expand doc sweep to AGENTS.md → Added to T1.4
  9. Rollback missing docs/hooks restoration → Added to Rollback Plan

**Timeline Adjustment:** 12-16h → 14-20h (per reviewer consensus)

### Review 2: Gemini + Codex (2025-11-29) - APPROVED

**Gemini Review:**
- Status: **APPROVED**
- Verified source directory exists
- T1.5 CI integration is "crucial and well-defined"
- Ready to proceed with T1.1

**Codex Review:**
- Status: **APPROVED**
- All 5 subtasks have clear validation gates
- Risks have explicit mitigations
- Ready to proceed sequentially

### Implementation Progress (2025-11-29)

**T1.1-T1.5: All subtasks COMPLETED**

Additional refinements based on post-implementation review:

1. **Dual Review Enforcement (Codex CRITICAL):**
   - Fixed `check-commit` to require BOTH gemini AND codex approval
   - Added placeholder ID detection (`test-*`, `placeholder-*`, etc.)
   - Added `ZEN_REVIEW_OVERRIDE` support for emergencies

2. **File Locking Integration (Gemini Recommendation):**
   - Refactored CLI to use `WorkflowGate` class from `core.py`
   - `state_transaction()` now uses `_gate._acquire_lock/release_lock` for fcntl-based locking
   - Ensures atomic state operations across concurrent processes

3. **State Schema Migration:**
   - Fixed migration to handle legacy `commit_history` with string entries
   - Changed `default_base_branch` to "master" in config and migration

4. **Test Updates:**
   - Updated test fixtures to use dual reviews (gemini + codex both APPROVED)
   - Changed test base branch from "main" to "master"
   - All 262 ai_workflow tests passing

5. **Documentation Updates:**
   - Updated `docs/AI/Workflows/README.md` with new architecture details
   - Added common commands reference

### Review 3: Gemini + Codex Fresh Review (2025-11-29)

**Issues Identified and Fixed:**

1. **CRITICAL - Schema Divergence (Both Reviewers):**
   - Updated `core.py` to use V2 nested schema matching CLI
   - V2 schema: `state["component"]["step"]`, `state["reviews"]["gemini"]`, etc.
   - Added `_migrate_v1_to_v2()` for automatic migration
   - CLI and core.py now share the same V2 schema

2. **HIGH - Continuation ID Persistence (Codex):**
   - CLI `record-review` now requires and persists `--continuation-id` for component reviews
   - IDs are logged to audit trail for verification
   - `check-commit` validates continuation IDs are not placeholders or empty

3. **HIGH - Override Bypass Auditing (Codex):**
   - `ZEN_REVIEW_OVERRIDE` now logs to audit trail
   - Emits loud warnings to stderr with user attribution
   - Action is traceable for security audits

4. **MEDIUM - Config Alignment (Codex):**
   - Updated `config.py` defaults: `enabled: ["gemini", "codex"]`, `min_required: 2`
   - `check-commit` respects `WorkflowConfig.get_enabled_reviewers()` and `get_min_required_approvals()`
   - Fresh installs now default to dual-review requirement

5. **LOW - Placeholder Detection (Codex):**
   - Case-insensitive matching (`TEST-123` now detected)
   - Rejects empty/blank strings (`""`, `"   "`)

6. **HIGH - Test Updates (Gemini):**
   - All tests updated for V2 schema
   - 59 tests passing (44 core + 15 CLI)

---

**Last Updated:** 2025-11-29
**Author:** Claude Code
**Version:** 2.3 (All review issues addressed)
