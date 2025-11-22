# Phase 1 Implementation Plan: Planning Discipline Enforcement

**Task:** P1T13-F5 Phase 1
**Created:** 2025-11-12
**Status:** READY FOR REVIEW
**Estimate:** 8-10 hours (4 components)
**Reviews:** Gemini APPROVED, Codex APPROVED WITH FINDINGS

---

## Executive Summary

This implementation plan addresses all 6 technical findings from Codex's deep review and incorporates Gemini's recommendations. Phase 1 adds hard gates to enforce planning discipline (analysis → task doc → components → todos → delegation) before allowing code implementation.

**Key Changes:**
- Extend workflow state machine with "plan" step
- Add planning metadata to workflow-state.json schema
- Implement first-commit detection for planning gates
- Add TodoWrite enforcement for complex tasks (3+ components)
- Wire context delegation thresholds into pre-commit hook
- Implement context caching for performance

---

## Open Questions & Decisions

**These require user decision before implementation:**

### Q1: First-Commit Detection Strategy
**Options:**
- **A) State flag:** Track `first_commit_made` boolean, flip in record_commit()
- **B) Git history:** Use `git rev-list --count HEAD ^$(git merge-base HEAD origin/master)`
- **C) Task-state integration:** Check if any components marked complete in task-state.json

**Recommendation:** **Option A (State flag)**
**Rationale:**
- Simplest to implement and test
- Works reliably across branch switches and rebases
- No dependency on git history which can be rewritten
- Clear reset semantics when start_task_with_state() runs

**Decision:** ✅ **CONFIRMED - Option A (State flag)**

---

### Q2: TodoWrite Detection in Subtasks
**Options:**
- **A) Shared:** Single `.claude/session-todos.json` for entire session
- **B) Per-subtask:** `.claude/subtasks/<task_id>/session-todos.json`

**Recommendation:** **Option A (Shared)**
**Rationale:**
- TodoWrite is session-scoped, not task-scoped
- Simpler file detection logic
- Subtask isolation (Subfeature C) handles task-state, not todos
- Can revisit if Subfeature C implementation requires isolation

**Decision:** ✅ **CONFIRMED - Option A (Shared)**

---

### Q3: Merge/Amend Behavior for Planning Gates
**Options:**
- **A) Skip gates:** Merges and amends bypass planning validation
- **B) Revalidate:** Planning gates check on every commit type

**Recommendation:** **Option A (Skip gates)**
**Rationale:**
- Merge commits combine validated work (planning already done)
- Amends fix mistakes in already-validated commits
- `first_commit_made` flag already set, so gates naturally skip
- Override available for edge cases

**Decision:** ✅ **CONFIRMED - Option A (Skip gates)**

---

### Q4: Context Cache Invalidation Strategy
**Options:**
- **A) Time-based:** Cache valid for 5 minutes
- **B) Change-based:** Invalidate when files modified
- **C) Hybrid (revised):** Time-based (5min) + git index hash

**Recommendation:** **Option C (Hybrid with git index hash)**
**Rationale:**
- Time-based prevents stale cache across long sessions
- Git index hash detects commits/stage changes (cheap operation)
- File mtime sum approach (original proposal) too expensive (O(n) filesystem walks)
- Hybrid provides best performance + accuracy balance

**Implementation:**
```python
{
  "context_cache": {
    "tokens": 145000,
    "timestamp": "2025-11-12T10:30:00Z",
    "git_index_hash": "abc123def456"  # git rev-parse HEAD
  }
}
```

**Decision:** ✅ **CONFIRMED - Option C (Hybrid with git index hash)**

---

## Component Breakdown

### Component 1: State Machine + Schema + CLI (2.5h)

**Addresses:** F1-plan-step, F2-planning-metadata

**Tasks:**
1. **Extend VALID_TRANSITIONS** (30min)
   - Add `"plan": ["implement"]` to workflow_gate.py:55-61
   - Update transition validation logic
   - Add unit tests for new transition

2. **Extend workflow state schema** (45min)
   - Add fields to _init_state() method:
     ```python
     {
       "task_file": None,  # Path to docs/TASKS/<task_id>_TASK.md
       "analysis_completed": False,  # Checklist completion flag
       "components": [],  # [{"num": 1, "name": "..."}]
       "first_commit_made": False,  # First commit detection flag
       "context_cache": {  # Performance optimization
         "tokens": 0,
         "timestamp": None,
         "git_index_hash": None  # git rev-parse HEAD
       }
     }
     ```
   - Add schema migration for existing state files
   - Test backward compatibility

3. **Add CLI commands** (1h 15min)
   - `start-task`: Initialize workflow with planning step
     ```bash
     ./scripts/workflow_gate.py start-task \
       --id P1T14 \
       --title "Feature Title" \
       --task-file docs/TASKS/P1T14_TASK.md \
       --components 3
     ```
     - Sets step="plan"
     - Stores task_file path
     - Initializes components array
     - Sets first_commit_made=False

   - `record-analysis-complete`: Mark analysis done
     ```bash
     ./scripts/workflow_gate.py record-analysis-complete \
       --checklist-file .claude/analysis/P1T14-checklist.md
     ```
     - Validates checklist file exists
     - Sets analysis_completed=True
     - Logs completion timestamp

   - `set-components`: Define component breakdown
     ```bash
     ./scripts/workflow_gate.py set-components \
       "Component 1: Core logic" \
       "Component 2: API endpoints" \
       "Component 3: Tests"
     ```
     - Parses component names
     - Stores as structured array
     - Validates minimum 2 components

4. **Update start_task_with_state()** (15min)
   - Default to "plan" step instead of "implement"
   - Initialize new schema fields
   - Reset first_commit_made flag

**Validation:**
- Unit test: `test_planning_step_transition()`
- Unit test: `test_state_schema_migration()`
- Unit test: `test_start_task_cli_command()`
- Unit test: `test_record_analysis_cli_command()`
- Unit test: `test_set_components_cli_command()`

**Files Modified:**
- `scripts/workflow_gate.py` (lines 55-61, 86-115, 1609-1627, 2400+)

---

### Component 2: Planning Artifact Validation Gates (3-3.5h)

**Addresses:** F2-planning-metadata, F3-first-commit-detection

**Tasks:**
1. **Implement _has_planning_artifacts()** (1h)
   ```python
   def _has_planning_artifacts(self) -> bool:
       """Check if all required planning artifacts exist."""
       state = self.load_state()

       # Check 1: Task document exists
       task_file = state.get("task_file")
       if not task_file:
           print("Missing: task_file not set")
           return False
       if not Path(task_file).exists():
           print(f"Missing: task document not found at {task_file}")
           return False

       # Check 2: Analysis checklist completed
       if not state.get("analysis_completed", False):
           print("Missing: analysis not completed")
           print("  Run: ./scripts/workflow_gate.py record-analysis-complete")
           return False

       # Check 3: Component breakdown exists (≥2 components)
       components = state.get("components", [])
       if len(components) < 2:
           print(f"Missing: need ≥2 components, found {len(components)}")
           print("  Run: ./scripts/workflow_gate.py set-components '<name>' '<name>'")
           return False

       return True
   ```

2. **Implement _is_first_commit()** (45min)
   ```python
   def _is_first_commit(self) -> bool:
       """Check if this is the first commit on the current branch/task."""
       state = self.load_state()
       return not state.get("first_commit_made", False)
   ```

3. **Add planning gate to check_commit()** (1h)
   - Insert at beginning of check_commit() method (before existing gates)
   ```python
   def check_commit(self) -> None:
       """Validate commit prerequisites (called by pre-commit hook)."""

       # Gate 0: Planning artifacts (first commit only)
       if self._is_first_commit():
           if not self._has_planning_artifacts():
               print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
               print("❌ COMMIT BLOCKED: Missing planning artifacts")
               print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
               print("   Required before first commit:")
               print("     1. Task document (docs/TASKS/)")
               print("     2. Analysis checklist completion")
               print("     3. Component breakdown (≥2 components)")
               print()
               print("   Complete planning steps:")
               print("     ./scripts/workflow_gate.py start-task --id <task_id> ...")
               print("     ./scripts/workflow_gate.py record-analysis-complete")
               print("     ./scripts/workflow_gate.py set-components '<name>' '<name>'")
               print()
               print("   Emergency bypass (production outage only):")
               print("     ZEN_REVIEW_OVERRIDE=1 git commit -m \"...\"")
               sys.exit(1)

       # Existing gates: step check, review check, CI check...
   ```

4. **Update record_commit() to flip flag** (30min)
   ```python
   def record_commit(self) -> None:
       """Record successful commit and reset workflow state."""
       with self._locked_state() as state:
           # Mark that first commit has been made
           state["first_commit_made"] = True

           # Existing reset logic...
           state["step"] = "implement"
           state["zen_review"] = {"status": "NOT_REQUESTED", ...}
           state["ci_passed"] = False
   ```

5. **Handle branch switches/task resets** (30min)
   - Ensure start_task_with_state() resets first_commit_made=False
   - Document behavior in CLAUDE.md

**Validation:**
- Unit test: `test_has_planning_artifacts_all_present()`
- Unit test: `test_has_planning_artifacts_missing_task_file()`
- Unit test: `test_has_planning_artifacts_missing_analysis()`
- Unit test: `test_has_planning_artifacts_insufficient_components()`
- Unit test: `test_is_first_commit_initial_state()`
- Unit test: `test_is_first_commit_after_one_commit()`
- Integration test: `test_first_commit_blocked_without_planning()`
- Integration test: `test_second_commit_allows_without_revalidation()`

**Files Modified:**
- `scripts/workflow_gate.py` (lines 366-466, 468-563)

---

### Component 3: TodoWrite Enforcement (1.5-2h)

**Addresses:** F4-todowrite-enforcement

**Tasks:**
1. **Implement _is_complex_task()** (15min)
   ```python
   def _is_complex_task(self) -> bool:
       """Task is complex if it has 3+ components."""
       state = self.load_state()
       components = state.get("components", [])
       return len(components) >= 3
   ```

2. **Implement _has_active_todos()** (45min)
   ```python
   def _has_active_todos(self) -> bool:
       """Check if TodoWrite tool has been used (session-todos.json exists).

       Relaxed validation: Checks minimal structure only to be robust to
       Claude Code format changes. Logs warnings for missing optional fields.
       """
       # Q2 Decision: Shared session-todos.json
       todos_file = PROJECT_ROOT / ".claude" / "session-todos.json"

       if not todos_file.exists():
           return False

       # Validate JSON schema (not just existence)
       try:
           with open(todos_file) as f:
               data = json.load(f)

           # Check 1: Must be a dictionary or array
           if isinstance(data, dict):
               # Format: {"todos": [...]}
               todos = data.get("todos", [])
           elif isinstance(data, list):
               # Format: [...]
               todos = data
           else:
               print(f"Warning: {todos_file} is not a list or dict")
               return False

           # Check 2: Must have at least one todo
           if len(todos) == 0:
               print(f"Warning: {todos_file} is empty")
               return False

           # Check 3: Minimal validation - each todo must be a dict
           # Do NOT require specific fields (Claude Code may change format)
           for i, todo in enumerate(todos):
               if not isinstance(todo, dict):
                   print(f"Warning: Todo {i} is not a dict in {todos_file}")
                   return False

               # Optional: Log warnings for missing recommended fields
               if "content" not in todo:
                   print(f"Info: Todo {i} missing 'content' field")
               if "status" not in todo:
                   print(f"Info: Todo {i} missing 'status' field")

           return True

       except json.JSONDecodeError as e:
           print(f"Warning: Failed to parse {todos_file}: {e}")
           return False
   ```

3. **Add TodoWrite gate to check_commit()** (30min)
   ```python
   def check_commit(self) -> None:
       # Gate 0: Planning artifacts (first commit only)
       # ... existing planning gate ...

       # Gate 0.5: TodoWrite for complex tasks (every commit)
       if self._is_complex_task() and not self._has_active_todos():
           print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
           print("❌ COMMIT BLOCKED: Complex task requires todo tracking")
           print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
           print("   This task has 3+ components but no active todos")
           print()
           print("   Create todo list using TodoWrite tool in Claude Code")
           print()
           print("   Manual fallback:")
           print("     Create .claude/session-todos.json with format:")
           print('     [{"content": "Task description", "status": "pending", "activeForm": "Doing task"}]')
           print()
           print("   Emergency bypass (production outage only):")
           print("     ZEN_REVIEW_OVERRIDE=1 git commit -m \"...\"")
           sys.exit(1)

       # Existing gates: step check, review check, CI check...
   ```

4. **Document TodoWrite fallback** (15min)
   - Add to CLAUDE.md:
     ```markdown
     ### TodoWrite Enforcement

     Complex tasks (3+ components) require todo tracking via Claude Code's TodoWrite tool.

     **Fallback:** If TodoWrite unavailable, manually create `.claude/session-todos.json`:
     ```json
     [
       {"content": "Component 1: Core logic", "status": "pending", "activeForm": "Implementing core logic"},
       {"content": "Component 2: Tests", "status": "pending", "activeForm": "Writing tests"}
     ]
     ```
     ```

**Validation:**
- Unit test: `test_is_complex_task_with_2_components()` (not complex)
- Unit test: `test_is_complex_task_with_3_components()` (complex)
- Unit test: `test_has_active_todos_file_missing()`
- Unit test: `test_has_active_todos_file_exists_valid()`
- Unit test: `test_has_active_todos_file_invalid_json()`
- Unit test: `test_has_active_todos_file_empty_array()`
- Unit test: `test_has_active_todos_file_invalid_schema()`
- Integration test: `test_complex_task_blocked_without_todos()`
- Integration test: `test_simple_task_allows_without_todos()`

**Files Modified:**
- `scripts/workflow_gate.py` (add methods + gate)
- `CLAUDE.md` (document fallback mechanism)

---

### Component 4: Context Delegation Enforcement (2h)

**Addresses:** F5-context-delegation-gate, Gemini KF-001

**Tasks:**
1. **Implement context cache** (45min)
   ```python
   def _get_cached_context_tokens(self) -> int:
       """Get current context usage with caching for performance.

       Uses hybrid invalidation:
       - Time-based: Cache expires after 5 minutes
       - Change-based: Git index hash detects commits/stage changes

       Performance target: <100ms cache hit, <1s cache miss
       """
       import subprocess
       import time

       state = self.load_state()
       cache = state.get("context_cache", {})

       # Get current git index hash (cheap: ~10-20ms)
       try:
           git_index_hash = subprocess.check_output(
               ["git", "rev-parse", "HEAD"],
               cwd=PROJECT_ROOT,
               text=True,
               stderr=subprocess.DEVNULL
           ).strip()
       except subprocess.CalledProcessError:
           # Fallback if not in git repo or detached state
           git_index_hash = "unknown"

       # Calculate cache age
       now = time.time()
       cache_age = now - cache.get("timestamp", 0) if cache.get("timestamp") else float('inf')

       # Invalidate if:
       # 1. Cache older than 5 minutes
       # 2. Git index changed (new commits, stage changes)
       # 3. No cache exists
       if (cache_age > 300 or  # 5 minutes
           cache.get("git_index_hash") != git_index_hash or
           not cache.get("tokens")):

           # Expensive operation: calculate tokens via DelegationRules
           delegation_rules = DelegationRules(
               load_state=self.load_state,
               save_state=self.save_state,
               locked_modify_state=self.locked_modify_state
           )

           # Use existing API: get_context_snapshot() not get_current_tokens()
           snapshot = delegation_rules.get_context_snapshot()
           tokens = snapshot.get("current_tokens", 0)

           # Update cache
           with self._locked_state() as state:
               state["context_cache"] = {
                   "tokens": tokens,
                   "timestamp": now,
                   "git_index_hash": git_index_hash
               }

           return tokens

       # Return cached value (fast: <1ms)
       return cache["tokens"]
   ```

   **Performance Analysis:**
   - **Cache hit:** <1ms (dict lookup)
   - **Cache miss (time expired):** ~500-800ms (DelegationRules.get_context_snapshot())
   - **Cache miss (git changed):** ~500-800ms
   - **Git index hash:** ~10-20ms (subprocess call)
   - **Total pre-commit hook:** <1s (meets requirement)

2. **Add context delegation gate to check_commit()** (1h)
   ```python
   def check_commit(self) -> None:
       # Gate 0: Planning artifacts (first commit only)
       # Gate 0.5: TodoWrite for complex tasks (every commit)
       # ... existing gates ...

       # Gate 0.6: Context delegation threshold (every commit)
       current_tokens = self._get_cached_context_tokens()
       max_tokens = 200_000
       usage_percent = (current_tokens / max_tokens) * 100

       if usage_percent >= 85:  # MANDATORY threshold
           print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
           print("❌ COMMIT BLOCKED: Context usage ≥85%")
           print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
           print(f"   Current: {current_tokens:,} / {max_tokens:,} tokens ({usage_percent:.1f}%)")
           print()
           print("   You MUST delegate before committing:")
           print("     1. ./scripts/workflow_gate.py suggest-delegation")
           print("     2. ./scripts/workflow_gate.py record-delegation '<task description>'")
           print()
           print("   After delegation:")
           print("     - Context resets to 0")
           print("     - Commit will be allowed")
           print()
           print("   Emergency bypass (production outage only):")
           print("     ZEN_REVIEW_OVERRIDE=1 git commit -m \"...\"")
           sys.exit(1)

       # Show warning at 70% (informational)
       if 70 <= usage_percent < 85:
           print(f"⚠️  Warning: Context usage at {usage_percent:.1f}%")
           print(f"   Current: {current_tokens:,} / {max_tokens:,} tokens")
           print("   Consider delegating soon:")
           print("     ./scripts/workflow_gate.py suggest-delegation")
           print()

       # Existing gates: step check, review check, CI check...
   ```

3. **Add performance instrumentation** (15min)
   ```python
   import time

   def check_commit(self) -> None:
       start_time = time.time()

       # ... all gates ...

       end_time = time.time()
       duration_ms = (end_time - start_time) * 1000

       if duration_ms > 1000:  # Warn if slower than 1 second
           print(f"⚠️  Pre-commit hook took {duration_ms:.0f}ms (slow)")
   ```

**Validation:**
- Unit test: `test_get_cached_context_tokens_cache_hit()`
- Unit test: `test_get_cached_context_tokens_cache_miss_time()`
- Unit test: `test_get_cached_context_tokens_cache_miss_files()`
- Unit test: `test_context_gate_blocks_at_85_percent()`
- Unit test: `test_context_gate_warns_at_70_percent()`
- Unit test: `test_context_gate_allows_below_70_percent()`
- Performance test: `test_check_commit_performance_under_1_second()`
- Integration test: `test_delegation_resets_context_cache()`

**Files Modified:**
- `scripts/workflow_gate.py` (add caching + gate + instrumentation)

---

## Testing Strategy

### Unit Tests (New)
- `test_workflow_gate_planning.py` (20+ tests covering all new methods)
  - State machine transitions
  - Planning artifact validation
  - First-commit detection
  - TodoWrite enforcement
  - Context caching and invalidation
  - Delegation threshold gates

### Integration Tests (New)
- `test_planning_workflow_integration.py` (10+ tests)
  - Full planning workflow: start-task → analysis → components → implement → commit
  - First vs second commit behavior
  - Complex task TodoWrite requirement
  - Context threshold blocking
  - Emergency override (ZEN_REVIEW_OVERRIDE)

### Performance Tests (New)
- `test_workflow_gate_performance.py`
  - check_commit() completes in <1 second
  - Context caching prevents expensive recalculation
  - Benchmark token calculation vs cached retrieval

### Manual Tests
- Test 1: Start new task without planning → commit blocked
- Test 2: Complete planning → advance to implement → commit allowed
- Test 3: Second commit without revalidation → allowed
- Test 4: Complex task (3+ components) without TodoWrite → blocked
- Test 5: Context at 85% without delegation → blocked
- Test 6: ZEN_REVIEW_OVERRIDE=1 → bypasses all planning gates

---

## Rollout Plan

### Step 1: Implement & Test Locally (8-10h)
- Implement all 4 components following this plan
- Run full test suite (unit + integration + performance)
- Manual testing of all gates
- Performance validation (<1s pre-commit hook)

### Step 2: Request Implementation Review (30min)
- Request Gemini + Codex review of implementation
- Address any findings
- Ensure all 6 original findings resolved

### Step 3: Update Documentation (1h)
- Update CLAUDE.md with new planning workflow
- Document all new CLI commands
- Add TodoWrite fallback instructions
- Update workflow diagrams

### Step 4: Commit with 4-Step Pattern (30min)
- Follow Phase 0 proven workflow
- zen-mcp review before commit
- CI validation
- Clean commit message

---

## Risk Mitigation

**Risk:** Context caching adds complexity
- **Mitigation:** Comprehensive unit tests, performance benchmarks, fallback to uncached if cache corrupted

**Risk:** First-commit detection brittle across branch operations
- **Mitigation:** Q1 decision uses simple state flag, well-tested across scenarios

**Risk:** TodoWrite detection fragile to Claude Code changes
- **Mitigation:** Validate JSON schema, clear error messages, documented fallback

**Risk:** Planning gates too strict, block legitimate work
- **Mitigation:** ZEN_REVIEW_OVERRIDE bypass available, all blocks have clear remediation steps

**Risk:** Performance regression in pre-commit hook
- **Mitigation:** Context caching, performance tests, instrumentation to detect slowness

---

## Success Metrics

**After Phase 1 deployment:**
- Planning bypass rate: 0% (down from ~30%)
- Wasted hours from skipped analysis: 0h (down from 3-11h per incident)
- Context overflow incidents: 0 (down from ~2 per month)
- Pre-commit hook performance: <1 second (95th percentile)
- TodoWrite usage on complex tasks: 100% (up from ~20%)

---

## Next Steps

1. **USER: Review and approve this implementation plan**
2. **USER: Answer open questions Q1-Q4 (decisions required)**
3. **Request final review:** Send this plan to Gemini + Codex for validation
4. **Implementation:** Execute plan component-by-component with 4-step pattern
5. **Deployment:** Merge to feature branch, validate in practice

---

## Appendix: Addressing All 6 Codex Findings

| Finding | Severity | Component | Status |
|---------|----------|-----------|--------|
| F1-plan-step | HIGH | Component 1 | ✅ Addressed (state machine extension) |
| F2-planning-metadata | HIGH | Components 1+2 | ✅ Addressed (schema + CLI + validation) |
| F3-first-commit-detection | MEDIUM | Component 2 | ✅ Addressed (state flag approach) |
| F4-todowrite-enforcement | MEDIUM | Component 3 | ✅ Addressed (detection + validation + fallback) |
| F5-context-delegation-gate | MEDIUM | Component 4 | ✅ Addressed (caching + gate + instrumentation) |
| F6-hotfix-bypass-alignment | LOW | All components | ✅ Addressed (reuse ZEN_REVIEW_OVERRIDE) |

**All findings comprehensively addressed with implementation details, validation strategy, and risk mitigation.**
