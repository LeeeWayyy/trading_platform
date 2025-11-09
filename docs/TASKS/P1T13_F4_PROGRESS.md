---
id: P1T13-F4
title: "AI Coding Automation: Workflow Intelligence & Context Efficiency"
phase: P1
task: T13-F4
priority: P1
owner: "@development-team"
state: PLANNING
created: 2025-11-07
updated: 2025-11-07
dependencies: ["P1T13-F3"]
estimated_effort: "12-16 hours"
related_adrs: []
related_docs: ["CLAUDE.md", ".claude/workflows/", "scripts/workflow_gate.py"]
features: ["smart_testing", "auto_delegation", "unified_reviews", "debug_rescue", "workflow_simplification"]
branch: "feature/P1T13-F4-workflow-intelligence"
---

# P1T13-F4: AI Coding Automation - Workflow Intelligence & Context Efficiency

**Phase:** P1 (Hardening)
**Status:** APPROVED (Both gemini and codex planners approved)
**Priority:** P1 (HIGH)
**Owner:** @development-team
**Created:** 2025-11-07
**Estimated Effort:** 12-16 hours
**Dependencies:** P1T13-F3 (Context Optimization & Checkpointing)
**Continuation ID:** 3bfc744f-3719-4994-b352-3b8488ba03b1 (45 turns remaining)

---

## Objective

Address workflow inefficiencies identified during P1T13-F3 implementation by adding intelligence to workflow_gate.py: smart testing, automated delegation rules, unified reviews, debug rescue, and workflow simplification.

**Core Goals:**
1. **Smart Testing**: Reduce CI time/context by running targeted tests for commits, full tests for PRs
2. **Auto-Delegation Rules**: Codify when to use subagents (CI runs, PR reviews, analysis tasks)
3. **Integrated Planning**: Unify task creation/breakdown workflows into workflow_gate.py
4. **Unified Review System**: Merge quick/deep reviews with multi-iteration pre-PR validation
5. **Debug Rescue**: Add clink codex intervention when AI loops on issues
6. **Workflow Simplification**: Replace verbose docs with workflow_gate commands

---

## Problem Statement

### Observed Issues from P1T13-F3 Implementation

**1. CI Testing Bottleneck (Time + Context Waste)**

**Current:** Every commit runs full test suite via `make ci-local`
- **Time cost:** 2-5 minutes per commit × 10-15 commits = 20-75 minutes wasted
- **Context cost:** 10-20k tokens per full test run × 15 runs = 150-300k tokens
- **Impact:** Context exhaustion mid-task, slow commit cycles

**Example:**
```
Commit 1: Add position_limit_validation.py
  → Runs ALL 1,500 tests (only 3 relevant)
  → 3 minutes, 15k tokens consumed

Commit 2: Add test_position_limit_validation.py
  → Runs ALL 1,503 tests (only 3 new)
  → 3 minutes, 15k tokens consumed

Total waste: 6 minutes, 30k tokens for 6 relevant tests
```

**Root Cause:** No intelligence about what changed

**2. Subagent Delegation Gaps (No Clear Rules)**

**Current:** Phase 1 delegation pattern exists but no automated decision logic
- AI must manually decide when to delegate
- No codified rules for high-context operations
- Results in inconsistent delegation usage

**Example of missed delegation opportunities:**
```python
# HIGH-CONTEXT operations that SHOULD delegate but don't:

# 1. Full CI test run (100k+ token output)
make ci-local  # Should delegate to Task(general-purpose)

# 2. PR review comment analysis (50k+ tokens)
gh pr view 123 --comments  # Should delegate to Task(Explore)

# 3. Multi-file codebase search (30k+ tokens)
grep -r "circuit_breaker" apps/ libs/  # Should delegate to Task(Explore)

# 4. Large file analysis (20k+ tokens)
analyzing 500-line file  # Should delegate to Task(general-purpose)
```

**Impact:** Context pollution from operations that should be delegated

**3. Planning Workflow Fragmentation**

**Current:** Task creation and breakdown workflows are separate manual processes
- `.claude/workflows/00-task-breakdown.md` - Manual subfeature planning
- `.claude/workflows/12-phase-management.md` - Manual phase planning
- `.claude/workflows/13-task-creation-review.md` - Manual review request
- No integration with workflow_gate.py state tracking

**User Experience:**
```bash
# User must manually:
1. Create task document
2. Request task creation review (clink + gemini)
3. Address review findings
4. Re-request review if needed
5. Start task (separate command)
6. No connection to workflow_gate.py tracking
```

**Impact:** High cognitive load, context switching, lost state

**4. Review System Redundancy**

**Current:** Separate quick (Tier 1) and deep (Tier 2) review workflows
- **Quick review:** 03-reviews.md (per-commit, gemini → codex, 2-3 min)
- **Deep review:** 03-reviews.md (pre-PR, gemini → codex, 3-5 min)
- Both use same two-phase pattern
- Both check similar concerns (safety, quality, architecture)
- Both require gemini AND codex approval

**Overlap:**
```
Quick Review Checks:          Deep Review Checks:
✓ Trading safety             ✓ Trading safety (again)
✓ Circuit breakers           ✓ Circuit breakers (again)
✓ Idempotency               ✓ Idempotency (again)
✓ Code quality              ✓ Code quality (again)
✓ Error handling            ✓ Error handling (again)
                            ✓ Architecture (new)
                            ✓ Test coverage (new)
                            ✓ Integration concerns (new)
```

**Issues:**
- Redundant checks waste review time
- Pre-PR review finds issues already caught in quick reviews
- No multi-iteration loop for pre-PR (single pass, may miss issues)

**5. Debug Loop Traps (AI Gets Stuck)**

**Observed Pattern:**
```
AI debugging cycle:
1. Test fails with error
2. AI attempts fix
3. Test still fails (different error)
4. AI attempts another fix
5. Test still fails (original error back)
6. AI loops between attempts 2-5 for 30+ minutes
7. Context exhausted, no progress

Example: P1T13-F3 Phase 4 stuck on test mocking for 45 minutes
```

**Root Cause:** No escalation mechanism when stuck

**6. Workflow Context Bloat**

**Current State:**
- `CLAUDE.md`: 750 lines of workflow guidance
- `.claude/workflows/`: 23 workflow files, ~8,500 total lines
- Repeated instructions across files
- High context cost to load workflows

**Example Redundancy:**
```markdown
# Repeated in 5+ workflow files:
"MANDATORY: Request zen-mcp review before commit"
"NEVER use git commit --no-verify"
"Follow 4-step pattern: implement → test → review → commit"
```

**Impact:** 20-30k tokens just to understand workflow rules

---

## Proposed Solutions

### **Component 1: Smart Testing System (3-4 hours)**

**Goal:** Run targeted tests for commits, full tests for PRs

**Design:**

```python
# workflow_gate.py enhancement

class SmartTestRunner:
    """Intelligent test selection based on code changes."""

    def detect_changed_modules(self, staged_files: list[str]) -> set[str]:
        """
        Analyze staged files to determine impacted modules.

        Returns:
            Set of module paths (e.g., {"libs/allocation", "apps/execution_gateway"})
        """
        modules = set()
        for file in staged_files:
            if file.startswith(("libs/", "apps/", "strategies/")):
                # Extract module path (e.g., "libs/allocation/multi_alpha.py" → "libs/allocation")
                module = "/".join(file.split("/")[:2])
                modules.add(module)
        return modules

    def get_relevant_tests(self, modules: set[str]) -> list[str]:
        """
        Find test files for changed modules.

        Strategy:
        1. Direct tests: tests/<module>/test_*.py
        2. Integration tests: If app changed, include integration tests
        3. New test files: If staged file is test_*.py, include it
        """
        test_files = []

        for module in modules:
            # Direct module tests
            module_tests = glob.glob(f"tests/{module}/**/*.py", recursive=True)
            test_files.extend(module_tests)

            # If app changed, include integration tests
            if module.startswith("apps/"):
                integration_tests = glob.glob(f"tests/integration/{module.split('/')[1]}/*.py")
                test_files.extend(integration_tests)

        # Include any new test files being committed
        staged_tests = [f for f in staged_files if f.startswith("tests/") and "test_" in f]
        test_files.extend(staged_tests)

        return sorted(set(test_files))

    def run_smart_ci(self, context: str = "commit") -> tuple[bool, str]:
        """
        Run appropriate test suite based on context.

        Args:
            context: "commit" (targeted) or "pr" (full)

        Returns:
            (passed: bool, summary: str)
        """
        if context == "pr":
            # Full test suite for PR
            return self._run_full_ci()

        # Targeted testing for commits
        staged = self._get_staged_files()

        # Always run: linting + type checking (fast)
        lint_result = self._run_lint()
        if not lint_result:
            return (False, "Linting failed")

        # Smart test selection
        modules = self.detect_changed_modules(staged)

        if not modules:
            # No code changes (docs only)
            return (True, "No code changes - skipping tests")

        test_files = self.get_relevant_tests(modules)

        if not test_files:
            # Code changed but no tests found - WARNING
            return (False, f"Code changed in {modules} but no tests found! Add tests first.")

        # Run targeted tests
        result = self._run_tests(test_files)

        summary = f"""
        Smart CI Results (commit):
        - Linting: PASSED
        - Modules changed: {len(modules)}
        - Test files: {len(test_files)}
        - Tests run: {result.total_tests}
        - Duration: {result.duration}s
        """

        return (result.passed, summary)
```

**Workflow Integration:**

```bash
# Enhanced workflow_gate.py commands

# For commits (targeted tests)
./scripts/workflow_gate.py record-ci true  # Replaced with:
./scripts/workflow_gate.py run-ci commit   # Smart targeted testing

# For PRs (full tests)
./scripts/workflow_gate.py run-ci pr       # Full test suite
```

**Benefits:**
- **Time saved:** 2-5 min → 15-30 sec per commit (10-20x faster)
- **Context saved:** 15k tokens → 2k tokens per commit (7-8x reduction)
- **Quality maintained:** All relevant tests still run

**Edge Cases:**
- If >5 modules changed → fall back to full CI (likely refactor)
- If core infrastructure changed (libs/common) → full CI
- If no tests found for changed code → BLOCK commit

---

### **Component 2: Automated Subagent Delegation Rules (2-3 hours)**

**Goal:** Codify when to automatically delegate to subagents

**Design:**

```python
# workflow_gate.py enhancement

class DelegationRules:
    """Automated rules for when to delegate to subagents."""

    # Thresholds (from Phase 3)
    CONTEXT_WARN_PCT = 70
    CONTEXT_CRITICAL_PCT = 85

    # Operation context costs (empirical measurements)
    OPERATION_COSTS = {
        "full_ci": 100_000,           # Full test suite output
        "pr_comments": 50_000,        # PR review thread analysis
        "multi_file_search": 30_000,  # grep across many files
        "large_file_analysis": 20_000,  # Analyzing 500+ line files
        "test_failure_analysis": 15_000,  # Analyzing test output
        "git_diff_large": 25_000,     # git diff with >1000 lines
    }

    def should_delegate_operation(
        self,
        operation: str,
        current_context: int,
        max_context: int = 200_000
    ) -> tuple[bool, str]:
        """
        Determine if operation should be delegated to subagent.

        Args:
            operation: Operation key from OPERATION_COSTS
            current_context: Current token usage
            max_context: Max tokens available

        Returns:
            (should_delegate: bool, reason: str)
        """
        # Get estimated cost
        est_cost = self.OPERATION_COSTS.get(operation, 0)

        # Calculate usage after operation
        usage_after = current_context + est_cost
        usage_pct_after = (usage_after / max_context) * 100

        # MANDATORY delegation if would exceed CRITICAL threshold
        if usage_pct_after >= self.CONTEXT_CRITICAL_PCT:
            return (
                True,
                f"MANDATORY: Operation '{operation}' would push context to {usage_pct_after:.1f}% (≥{self.CONTEXT_CRITICAL_PCT}%)"
            )

        # RECOMMENDED delegation if would exceed WARN threshold
        if usage_pct_after >= self.CONTEXT_WARN_PCT:
            return (
                True,
                f"RECOMMENDED: Operation '{operation}' would push context to {usage_pct_after:.1f}% (≥{self.CONTEXT_WARN_PCT}%)"
            )

        # High-cost operations ALWAYS delegate (even if context OK)
        if est_cost >= 50_000:
            return (
                True,
                f"AUTO-DELEGATE: Operation '{operation}' is high-cost ({est_cost:,} tokens)"
            )

        return (False, f"OK: Operation '{operation}' safe in main context")

    def get_delegation_command(self, operation: str) -> str:
        """
        Get recommended Task tool delegation command for operation.

        Returns:
            Suggested Task tool invocation
        """
        delegation_map = {
            "full_ci": """
                Task(
                    description="Run full CI test suite",
                    prompt="Run 'make ci-local' and summarize results: pass/fail counts, error messages, duration. Return ONLY summary, not full output.",
                    subagent_type="general-purpose"
                )
            """,
            "pr_comments": """
                Task(
                    description="Analyze PR review comments",
                    prompt="Run 'gh pr view <pr_num> --comments' and extract: (1) Unresolved review comments, (2) Requested changes, (3) Blocking issues. Return structured summary only.",
                    subagent_type="Explore"
                )
            """,
            "multi_file_search": """
                Task(
                    description="Search codebase for pattern",
                    prompt="Find all occurrences of '<pattern>' in <directories>. Return file:line references ONLY, no code snippets.",
                    subagent_type="Explore"
                )
            """,
            "large_file_analysis": """
                Task(
                    description="Analyze large file structure",
                    prompt="Read <file_path> and summarize: (1) Key functions/classes, (2) Dependencies, (3) Complexity hotspots. Return structural summary, not full code.",
                    subagent_type="general-purpose"
                )
            """,
        }

        return delegation_map.get(operation, "# No delegation template for this operation")
```

**Workflow Integration:**

```bash
# Enhanced workflow_gate.py checks before operations

# Before running CI
./scripts/workflow_gate.py check-delegation full_ci
# Output: "MANDATORY: Delegate to subagent (would use 100k tokens)"
# → AI automatically uses Task tool instead of direct `make ci-local`

# Before analyzing PR comments
./scripts/workflow_gate.py check-delegation pr_comments
# Output: "AUTO-DELEGATE: High-cost operation (50k tokens)"
# → AI uses Task(Explore) instead of direct `gh pr view`
```

**Benefits:**
- **Context saved:** 100-150k tokens per task (50-75% reduction)
- **Automation:** No manual delegation decisions needed
- **Consistency:** Same delegation rules always applied

---

### **Component 3: Integrated Planning Workflow (2-3 hours)**

**Goal:** Unify task creation/breakdown workflows into workflow_gate.py

**Design:**

```python
# workflow_gate.py enhancement

class PlanningWorkflow:
    """Integrated task planning and creation workflow."""

    def create_task_with_review(
        self,
        task_id: str,
        title: str,
        description: str,
        estimated_hours: float
    ) -> str:
        """
        Create task document and automatically request planning review.

        Flow:
        1. Generate task document from template
        2. Auto-request gemini planner review (Tier 3)
        3. Display review findings
        4. Guide user through fixes if needed
        5. Re-request review after fixes
        6. Mark task as APPROVED when ready

        Returns:
            Task file path
        """
        # Generate task document
        task_file = self._generate_task_doc(task_id, title, description, estimated_hours)

        print(f"✅ Task document created: {task_file}")
        print()
        print("📋 Requesting task creation review (gemini planner → codex planner)...")
        print("   This will validate scope, requirements, and feasibility.")
        print()

        # Workflow will guide through review process
        return task_file

    def plan_subfeatures(
        self,
        task_id: str,
        components: list[dict]
    ) -> list[str]:
        """
        Generate subfeature breakdown and branches.

        Follows 00-task-breakdown.md rules:
        - Task >8h → MUST split into subfeatures
        - Task 4-8h → CONSIDER splitting
        - Task <4h → DON'T split

        Args:
            task_id: Parent task ID (e.g., "P1T13")
            components: List of component dicts with {name, description, hours}

        Returns:
            List of subfeature IDs (e.g., ["P1T13-F1", "P1T13-F2"])
        """
        total_hours = sum(c["hours"] for c in components)

        if total_hours < 4:
            print(f"ℹ️  Task is simple (<4h), no subfeature split needed")
            return []

        if total_hours >= 8 or len(components) >= 3:
            print(f"✅ Task is complex (≥8h or ≥3 components), splitting into subfeatures...")
        else:
            print(f"⚠️  Task is moderate (4-8h), splitting recommended...")

        # Generate subfeature IDs
        subfeatures = []
        for idx, component in enumerate(components, start=1):
            subfeature_id = f"{task_id}-F{idx}"
            subfeatures.append(subfeature_id)

            print(f"  {subfeature_id}: {component['name']} ({component['hours']}h)")

        return subfeatures

    def start_task_with_state(
        self,
        task_id: str,
        branch_name: str
    ) -> None:
        """
        Initialize task tracking with workflow state integration.

        Combines:
        - .claude/task-state.json (Phase 0 auto-resume)
        - .claude/workflow-state.json (Phase 3 workflow gates)

        Sets up:
        1. Git branch
        2. Task state tracking
        3. Workflow state initialization
        4. Component list from task document
        """
        # Create branch
        subprocess.run(["git", "checkout", "-b", branch_name], check=True)

        # Initialize task state (update_task_state.py integration)
        task_doc = self._load_task_doc(task_id)
        components = self._extract_components(task_doc)

        subprocess.run([
            "scripts/update_task_state.py", "start",
            "--task", task_id,
            "--branch", branch_name,
            "--components", str(len(components))
        ], check=True)

        # Initialize workflow state
        self.reset()  # Clean slate

        # Set first component
        if components:
            self.set_component(components[0]["name"])

        print(f"✅ Task {task_id} started")
        print(f"   Branch: {branch_name}")
        print(f"   Components: {len(components)}")
        print(f"   Current: {components[0]['name'] if components else 'N/A'}")
```

**Workflow Integration:**

```bash
# Unified planning commands

# 1. Create task with auto-review
./scripts/workflow_gate.py create-task \
    --id P1T14 \
    --title "Add position limit monitoring" \
    --description "Monitor and alert on position limit violations" \
    --hours 6
# → Creates task doc
# → Auto-requests gemini + codex planning review
# → Guides through review findings

# 2. Plan subfeatures (if needed)
./scripts/workflow_gate.py plan-subfeatures P1T14 \
    --component "Position monitor service:3h" \
    --component "Alert integration:2h" \
    --component "Dashboard UI:3h"
# → Detects 8h total → recommends split
# → Generates P1T14-F1, P1T14-F2, P1T14-F3

# 3. Start task (integrated with state tracking)
./scripts/workflow_gate.py start-task P1T14-F1
# → Creates branch
# → Initializes task-state.json
# → Initializes workflow-state.json
# → Sets first component
```

**Benefits:**
- **Unified interface:** All planning via workflow_gate.py
- **State integration:** task-state.json + workflow-state.json connected
- **Auto-review:** Planning review automatic, not manual
- **Context saved:** No need to load separate planning workflow docs

---

### **Component 4: Unified Review System (3-4 hours)**

**Goal:** Merge quick/deep reviews with multi-iteration pre-PR validation

**Design:**

```python
# workflow_gate.py enhancement

class UnifiedReviewSystem:
    """Consolidated review system with context-aware rigor."""

    def request_review(
        self,
        scope: str = "commit",  # "commit" or "pr"
        iteration: int = 1
    ) -> dict:
        """
        Request unified review (gemini codereviewer → codex codereviewer).

        Args:
            scope: "commit" (lightweight) or "pr" (comprehensive + multi-iteration)
            iteration: Iteration number for PR reviews (1, 2, 3...)

        Returns:
            Review result dict with continuation_id and status
        """
        if scope == "commit":
            return self._commit_review()
        else:
            return self._pr_review(iteration)

    def _commit_review(self) -> dict:
        """
        Lightweight commit review (replaces quick review).

        Focus:
        - Trading safety (circuit breakers, idempotency)
        - Critical bugs
        - Code quality (type safety, error handling)

        Speed: 2-3 minutes (gemini 1-2min, codex 30-60sec)
        """
        print("🔍 Requesting commit review (gemini → codex)...")
        print("   Focus: Trading safety, critical bugs, code quality")
        print()

        # Phase 1: Gemini codereviewer (lightweight mode)
        # Phase 2: Codex codereviewer (synthesis)
        # Return: {continuation_id, status: APPROVED/NEEDS_REVISION, issues: [...]}

        return {
            "scope": "commit",
            "continuation_id": "<generated>",
            "status": "APPROVED",
            "issues": []
        }

    def _pr_review(self, iteration: int) -> dict:
        """
        Comprehensive PR review with multi-iteration loop.

        Iteration 1:
        - Architecture analysis
        - Integration concerns
        - Test coverage
        - All commit-level checks (safety, quality)

        Iteration 2+ (if issues found):
        - INDEPENDENT review (fresh context, no memory of iteration 1)
        - Verify fixes from previous iteration
        - Look for NEW issues introduced by fixes
        - Continue until BOTH reviewers find NO issues

        Speed: 3-5 minutes per iteration
        Max iterations: 3 (escalate to user if still failing)
        """
        print(f"🔍 Requesting PR review - Iteration {iteration} (gemini → codex)...")
        print("   Focus: Architecture, integration, coverage, safety, quality")
        print()

        if iteration > 1:
            print(f"   ⚠️  INDEPENDENT REVIEW (no memory of iteration {iteration-1})")
            print("      Looking for: (1) Verified fixes, (2) New issues from fixes")
            print()

        # Phase 1: Gemini codereviewer (comprehensive mode)
        # Phase 2: Codex codereviewer (synthesis + actionable recommendations)

        # Return result
        result = {
            "scope": "pr",
            "iteration": iteration,
            "continuation_id": "<generated>",
            "status": "APPROVED",  # or "NEEDS_REVISION"
            "issues": []
        }

        if result["status"] == "NEEDS_REVISION" and iteration < 3:
            print()
            print(f"⚠️  Found {len(result['issues'])} issues")
            print("   Fix issues and re-request review:")
            print(f"     ./scripts/workflow_gate.py request-review pr --iteration {iteration + 1}")

        elif result["status"] == "NEEDS_REVISION" and iteration >= 3:
            print()
            print("⚠️  Max iterations reached (3)")
            print("   Escalating to user for guidance")

        return result
```

**Workflow Integration:**

```bash
# For commits (lightweight)
./scripts/workflow_gate.py advance review
./scripts/workflow_gate.py request-review commit
# → Gemini + Codex review (2-3 min)
# → APPROVED → record and commit
# → NEEDS_REVISION → fix and re-request

# For PRs (comprehensive + multi-iteration)
./scripts/workflow_gate.py request-review pr
# → Iteration 1: Comprehensive review (3-5 min)
# → If issues found, fix and run:
./scripts/workflow_gate.py request-review pr --iteration 2
# → Iteration 2: INDEPENDENT review (fresh, no memory)
# → Repeat until BOTH reviewers find NO issues (max 3 iterations)
```

**Benefits:**
- **Simplified:** One review command instead of two workflows
- **Stronger pre-PR:** Multi-iteration loop catches issues early
- **Independent iterations:** Fresh review perspective prevents bias
- **Consistent:** Same two-phase pattern (gemini → codex) for both
- **Context saved:** No separate workflow docs to load

---

### **Component 5: Debug Rescue Workflow (2-3 hours)**

**Goal:** Escalate to clink codex when AI stuck in debug loop

**Design:**

```python
# workflow_gate.py enhancement

class DebugRescue:
    """Automated detection and escalation of stuck debug loops."""

    def __init__(self):
        self.attempt_history: list[dict] = []
        self.max_attempts = 3
        self.loop_detection_window = 10  # Check last 10 attempts

    def record_test_attempt(
        self,
        test_file: str,
        status: str,  # "passed" or "failed"
        error_signature: str  # Hash of error message
    ) -> None:
        """
        Record test execution attempt for loop detection.

        Args:
            test_file: Test file path
            status: Test outcome
            error_signature: Hash of error message (for detecting repeats)
        """
        self.attempt_history.append({
            "timestamp": datetime.utcnow().isoformat(),
            "test_file": test_file,
            "status": status,
            "error_signature": error_signature
        })

        # Prune old history
        if len(self.attempt_history) > 50:
            self.attempt_history = self.attempt_history[-50:]

    def is_stuck_in_loop(self) -> tuple[bool, str]:
        """
        Detect if AI is stuck in debug loop.

        Indicators:
        1. Same test failing 3+ times in last 10 attempts
        2. Error signature cycling (A → B → A pattern)
        3. >30 minutes spent on same test

        Returns:
            (is_stuck: bool, reason: str)
        """
        if len(self.attempt_history) < self.max_attempts:
            return (False, "Not enough attempts to detect loop")

        recent = self.attempt_history[-self.loop_detection_window:]

        # Check 1: Same test failing repeatedly
        test_files = [a["test_file"] for a in recent if a["status"] == "failed"]
        if len(test_files) >= self.max_attempts:
            most_common = max(set(test_files), key=test_files.count)
            if test_files.count(most_common) >= self.max_attempts:
                return (
                    True,
                    f"Test '{most_common}' failed {test_files.count(most_common)} times in last {len(recent)} attempts"
                )

        # Check 2: Error signature cycling
        signatures = [a["error_signature"] for a in recent]
        if len(set(signatures)) <= 3 and len(signatures) >= 6:
            # Limited unique errors cycling
            return (
                True,
                f"Cycling between {len(set(signatures))} error patterns: {set(signatures)}"
            )

        # Check 3: Time spent (if timestamps available)
        if len(recent) >= 5:
            first_ts = datetime.fromisoformat(recent[0]["timestamp"])
            last_ts = datetime.fromisoformat(recent[-1]["timestamp"])
            duration = (last_ts - first_ts).total_seconds() / 60

            if duration > 30:
                return (
                    True,
                    f"Spent {duration:.1f} minutes on same test without progress"
                )

        return (False, "No loop detected")

    def request_debug_rescue(self, test_file: str, recent_errors: list[str]) -> str:
        """
        Request clink codex debugging assistance.

        Provides codex with:
        1. Test file and recent failure history
        2. Recent fix attempts (from git log)
        3. Request systematic debugging approach

        Returns:
            Continuation ID for rescue session
        """
        print("🆘 DEBUG RESCUE TRIGGERED")
        print(f"   Test: {test_file}")
        print(f"   Recent attempts: {len(self.attempt_history[-10:])}")
        print()
        print("📞 Requesting clink codex debugging assistance...")
        print()

        # Build rescue prompt
        rescue_prompt = f"""
DEBUG RESCUE REQUEST

I'm stuck in a debug loop on this test:
- Test file: {test_file}
- Failed attempts: {len(self.attempt_history[-10:])}
- Recent errors: {recent_errors[:3]}

Recent fix attempts (git log):
{self._get_recent_commits()}

Please help with systematic debugging:
1. Analyze the error pattern (is it cycling?)
2. Identify root cause (not just symptoms)
3. Suggest focused debugging approach
4. Recommend specific diagnostic steps

I need a fresh perspective to break out of this loop.
"""

        # Delegate to clink codex
        print("   Using: mcp__zen__clink(cli_name='codex', role='default')")
        print("   → Codex will provide systematic debugging guidance")
        print()

        # Return continuation_id for tracking
        return "<continuation_id>"
```

**Workflow Integration:**

```bash
# Automatic detection during test runs
./scripts/workflow_gate.py run-ci commit
# → If test fails, records attempt
# → If loop detected (3+ failures same test):
#    🆘 DEBUG RESCUE TRIGGERED
#    📞 Requesting clink codex assistance
#    → Codex provides systematic debugging approach

# Manual rescue trigger
./scripts/workflow_gate.py debug-rescue \
    tests/libs/secrets/test_vault_backend.py
# → Analyzes recent failure history
# → Requests clink codex debugging assistance
# → Returns systematic debugging plan
```

**Benefits:**
- **Auto-detection:** No manual intervention needed
- **Context efficient:** Delegates debugging to subagent
- **Fresh perspective:** Codex sees patterns AI in loop misses
- **Time saved:** 30-45 min debug loops → 5-10 min rescue

---

### **Component 6: Workflow Simplification (1-2 hours)**

**Goal:** Replace verbose workflow docs with workflow_gate.py commands

**Current State:**
```
CLAUDE.md: 750 lines
.claude/workflows/: 23 files, 8,500 total lines
Context cost: 20-30k tokens
```

**Target State:**
```
CLAUDE.md: 400 lines (46% reduction)
  - Remove redundant process instructions
  - Replace with workflow_gate.py command examples
  - Keep only architecture/domain knowledge

.claude/workflows/: Consolidate to 12 essential files, 4,000 lines (53% reduction)
  - Merge 03-reviews.md + 03-reviews.md → 03-reviews.md
  - Merge 00-task-breakdown.md + 12-phase-management.md + 13-task-creation-review.md → 03-planning.md
  - Remove redundant "MANDATORY" reminders (workflow_gate enforces)
  - Keep domain-specific workflows (05-testing.md, 06-debugging.md, 08-adr-creation.md)

Context cost: 8-12k tokens (60% reduction)
```

**Example Simplification:**

**Before (CLAUDE.md):**
```markdown
## Development Process

**Workflow Index:** [`.claude/workflows/README.md`](./.claude/workflows/README.md)

### 🔍 PHASE 0: Pre-Implementation Analysis (MANDATORY - 30-60 min)

**⚠️ CRITICAL:** Complete comprehensive analysis BEFORE writing ANY code.

**Requirements:**
- Follow [`.claude/workflows/00-analysis-checklist.md`](./.claude/workflows/00-analysis-checklist.md)
- Identify ALL impacted components, call sites, tests
- Verify pattern parity (retries, error handling, logging)
- Verify process compliance (review gates, CI gates)
- Create comprehensive todo list with 4-step pattern for EACH component

**DO NOT write code before completing this analysis.**

### ⚠️ MANDATORY: 4-Step Pattern for Each Logical Component

**CRITICAL:** After completing Phase 0 analysis, implement EVERY logical component:

1. **Implement** the logic component
2. **Create test cases** for comprehensive coverage (TDD)
3. **🔒 MANDATORY: Request zen-mcp review** (NEVER skip)
4. **🔒 MANDATORY: Run `make ci-local`** (NEVER skip)
5. **Commit** ONLY after BOTH reviewers approve + CI passes

[... 200 more lines of process instructions ...]
```

**After (CLAUDE.md):**
```markdown
## Development Process

Use `workflow_gate.py` for all development workflow operations.

### Planning & Setup

```bash
# Create and review task
./scripts/workflow_gate.py create-task --id P1T14 --title "..." --hours 6

# Plan subfeatures (if >8h)
./scripts/workflow_gate.py plan-subfeatures P1T14 --component "Name:3h" ...

# Start task
./scripts/workflow_gate.py start-task P1T14
```

### Component Development (4-Step Pattern)

```bash
# 1. Set component
./scripts/workflow_gate.py set-component "Position Limit Monitor"

# 2. Implement + test (your code here)

# 3. Advance to review
./scripts/workflow_gate.py advance test
./scripts/workflow_gate.py advance review

# 4. Request review + CI
./scripts/workflow_gate.py request-review commit  # Auto-delegates if needed
./scripts/workflow_gate.py run-ci commit          # Smart targeted tests

# 5. Commit (workflow_gate enforces prerequisites)
git commit -m "Add position limit monitor"
```

### Pre-PR Review

```bash
# Comprehensive multi-iteration review
./scripts/workflow_gate.py request-review pr
# → Iteration 1: Finds issues
# → Fix issues
./scripts/workflow_gate.py request-review pr --iteration 2
# → Iteration 2: Verifies fixes (independent review)
# → Repeat until clean
```

See `.claude/workflows/README.md` for detailed workflow docs.
```

**Workflow Files to Consolidate:**

| Before | After | Reduction |
|--------|-------|-----------|
| 03-reviews.md (390 lines) + 03-reviews.md (270 lines) | 03-reviews.md (200 lines) | 70% |
| 00-task-breakdown.md (245 lines) + 12-phase-management.md (142 lines) + 13-task-creation-review.md (229 lines) | 03-planning.md (180 lines) | 71% |
| component-cycle.md (remove redundant enforcement reminders) | component-cycle.md (simplified) | 40% |

**Benefits:**
- **Context saved:** 20-30k → 8-12k tokens (60% reduction)
- **Maintainability:** Single source of truth (workflow_gate.py)
- **Clarity:** Commands replace prose instructions
- **Enforcement:** workflow_gate.py ensures compliance, not docs

---

## Review Findings & Implementation Guidance

**Review Status:** ✅ APPROVED by both gemini and codex planners (2025-11-07)
**Continuation ID:** 3bfc744f-3719-4994-b352-3b8488ba03b1 (45 turns remaining)

### Phase 1: Gemini Planner Review (Scope & Requirements)

**Overall Assessment:** "This is a high-quality task plan that I would approve for implementation. It is a model of how to build upon prior work, addressing real-world friction points with targeted, intelligent solutions."

**Ratings:**
- ✅ Scope Appropriateness: **EXCELLENT** - All 6 components well-defined with clear problem statements
- ✅ Requirements Completeness: **VERY GOOD** - Detailed designs with minor edge cases to address
- ✅ Trading Platform Fit: **EXCELLENT** - Strong safety focus, no direct trading logic impact
- ✅ Implementation Feasibility: **EXCELLENT** - 12-16h estimate realistic, extends existing workflow_gate.py
- ✅ Risk Assessment: **VERY GOOD** - Critical risks identified with appropriate mitigations
- ✅ Dependencies & Sequencing: **EXCELLENT** - Logical order, no circular dependencies

**Edge Cases to Address During Implementation:**

1. **Component 1 (Smart Testing):**
   - **Cross-component dependencies:** Current design detects changed modules but may not identify tests in OTHER modules that depend on the changed code
   - **Mitigation:** Make fallback to full CI for `libs/common` changes explicit; add test data fixture handling (`tests/fixtures/`)
   - **Action:** Flag core packages list (`libs/common`, shared infrastructure) that always trigger full CI

2. **Component 3 (Integrated Planning):**
   - **Task doc updates:** Workflow for updating an existing task document after initial review isn't specified
   - **Question:** Does updating a task doc trigger a new review?
   - **Action:** Define update workflow: emit warnings, require `--force` to overwrite, add checksum to ensure we only mutate files we created

3. **Component 4 (Unified Review):**
   - **User override:** Should user be able to manually approve a PR review stuck in `NEEDS_REVISION` loop after 3 iterations?
   - **Action:** Define available actions after max iterations - allow manual override with explicit justification

4. **Component 5 (Debug Rescue):**
   - **State after rescue:** What is expected state after debug rescue? Does AI automatically apply the `clink codex` suggestion, or present to user for approval?
   - **Action:** Define hand-off protocol: present suggestion to user, require explicit approval before applying

### Phase 2: Codex Planner Review (Technical Implementation)

**Overall Assessment:** "Implementation is feasible if we first extract shared git-diff helpers and keep workflow_gate extensible via internal helper classes."

**Key Technical Findings:**

1. **Modularization Required:**
   - workflow_gate.py is already 600+ lines
   - Layering 6 new subsystems requires lightweight internal modularization
   - **Solution:** Use helper classes (SmartTestRunner, DelegationRules, etc.) or separate modules
   - **Critical:** Avoid monolithic growth - maintain CLI usability

2. **Shared Utilities Foundation:**
   - Smart Testing and Delegation both need git diff/staged file introspection
   - **Action:** Create `scripts/git_utils.py` module during Component 1 (Smart Testing)
   - **Reuse:** Debug Rescue and other components will use same git utilities
   - **Benefit:** Avoid duplicate code, consistent file detection

3. **State Schema Migration:**
   - F4 components depend on F3's state schema (`.claude/workflow-state.json`)
   - New workflow_gate state (smart-test metadata, delegation thresholds, review iteration counters) must migrate old files safely
   - **Action:**
     - Version state schema: `state.get('version', 1)`
     - Gate new behavior behind version checks
     - Provide rollback instructions
   - **Critical:** Users on older branches may pull updated workflow_gate.py but keep stale state files

4. **Coordination with task-state.json:**
   - Must integrate with `.claude/task-state.json` tooling to avoid split truth
   - **Action:** Ensure unified state management between task-state.json (Phase 0) and workflow-state.json (Phase 3)
   - **Risk:** Desynchronization between state files

5. **Testing Strategy:**
   - **Unit tests:** New pytest modules under `tests/scripts/` for smart testing, delegation, planning/review orchestration
   - **Integration tests:** Invoke workflow_gate.py via subprocess
   - **Smoke tests:** Dry-run of workflow_gate.py on repo with pre-F4 state
   - **Migration tests:** Verify backward-compatible state migration

**Implementation Sequence (from Codex):**

**Step 1: Sequence & Dependency Validation**
- Confirm proposed order by mapping shared helpers
- Change-detection utilities must land before both Smart Testing AND Debug Rescue
- Delegation cost tables should be ready before unified reviews that auto-suggest delegation

**Step 2: Core workflow_gate Extensions (Smart Testing + Delegation)**
- Design internal classes (SmartTestRunner, DelegationRules)
- Extract configuration (module→tests map, operation cost table) into data blocks for easier tuning
- Integrate with existing CLI: `run-ci` and `check-delegation` commands
- **Critical:** Flag 'core packages' list (`libs/common`, shared infrastructure) that always trigger full CI
- **Critical:** Add `--override` flag for run-ci to acknowledge manual full runs
- **Critical:** Ensure record-context defaults to safe values when unset

**Step 3: Higher-Level Workflows (Planning, Unified Review, Debug Rescue)**
- Extend workflow_gate CLI with subcommands: `create-task`, `plan-subfeatures`, `request-review`, `debug-rescue`
- Define persistent review metadata (scope, iteration, continuation_id)
- Store rescue attempt history under workflow_state
- **Critical:** Cap stored review iterations (last 3) and archive older entries
- **Critical:** Emit warnings and require `--force` to overwrite existing task docs

**Step 4: Testing, Rollout, and Backward Compatibility**
- Author targeted pytest modules under `tests/scripts/`
- Add integration tests invoking workflow_gate via subprocess
- Craft migration routine that injects defaults when legacy workflow-state.json lacks new fields
- **Critical:** Version state schema and gate new behavior behind `state.get('version', 1)`
- **Critical:** Keep domain knowledge sections in docs (only remove process redundancy)

### Summary of Required Changes

**Before Implementation Starts:**

1. **Create `scripts/git_utils.py`** (Component 1 prerequisite):
   ```python
   def get_staged_files() -> list[str]:
       """Get list of staged files from git."""

   def detect_changed_modules(files: list[str]) -> set[str]:
       """Analyze files to determine impacted modules."""

   def is_core_package(module: str) -> bool:
       """Check if module is core infrastructure (always requires full CI)."""
   ```

2. **Add state schema versioning** (all components):
   ```python
   # In workflow_gate.py
   WORKFLOW_STATE_VERSION = 2  # Increment from F3's version 1

   def migrate_state(old_state: dict) -> dict:
       """Migrate old state to new schema."""
       version = old_state.get('version', 1)
       if version < 2:
           # Add F4 fields with safe defaults
           old_state['version'] = 2
           old_state.setdefault('smart_test', {})
           old_state.setdefault('delegation', {})
           old_state.setdefault('review', {})
       return old_state
   ```

3. **Define core packages list** (Component 1):
   ```python
   CORE_PACKAGES = {
       "libs/common",
       "libs/feature_store",
       "scripts/",  # Infrastructure scripts
       "infra/",    # Docker, configs
   }
   ```

4. **Add `--override` flags** (Components 1, 2):
   ```bash
   # Allow manual overrides for safety
   ./scripts/workflow_gate.py run-ci commit --override  # Force full CI
   ./scripts/workflow_gate.py check-delegation <op> --override  # Skip delegation
   ```

**Recommendation:** Implement Components in order (1→2→3→4→5→6) with shared utilities first.

---

## Edge Case Resolutions & Final Clarifications

**Review Phase:** Follow-up Q&A with gemini planner + codex codereviewer (2025-11-07)
**Continuation ID:** 3bfc744f-3719-4994-b352-3b8488ba03b1 (35 turns remaining)

### Q1: Smart Testing (Component 1) - Cross-Component Dependencies

**Question:** How to handle cross-component dependencies? Build full dependency graph vs simple heuristics?

**Gemini's Recommendation (PRIORITY 1 - MOST CRITICAL):**
- Use expanded **CORE_PACKAGES** heuristic (libs/, config/, infra/, tests/fixtures/, scripts/)
- Any file in these dirs triggers full CI
- Simple, safe, maintainable approach
- Avoid complex dependency graph parsing

**Codex's Technical Implementation:**
```python
# Normalize staged paths before comparison
CORE_PACKAGES = {
    "libs/",
    "config/",
    "infra/",
    "tests/fixtures/",
    "scripts/",
}

def requires_full_ci(staged_files: list[str]) -> bool:
    """Check if any staged file is in CORE_PACKAGES."""
    for file in staged_files:
        # Convert to POSIX path and check prefix
        posix_path = Path(file).as_posix()
        if posix_path.startswith(tuple(CORE_PACKAGES)):
            return True
    return False
```

**Key technical points:**
- Use `Path(file).as_posix()` for normalization
- Ensure trailing slash in CORE_PACKAGES to avoid false matches (libs_special/ shouldn't match libs/)
- Cache staged files once per CLI invocation
- Provide `--force-targeted` escape hatch for power users
- Log override in state for auditing

**DECISION:** Use CORE_PACKAGES heuristic as specified above.

---

### Q2: Unified Review (Component 6) - User Override Protocol

**Question:** After 3 stuck iterations, should users be able to override review? Require justification? Log for audit? Severity-based restrictions?

**Gemini's Recommendation (PRIORITY 2):**
- **Yes to all** - require justification, log to PR, severity-based restrictions
- Block CRITICAL/HIGH overrides entirely
- Allow MEDIUM/LOW with justification
- Log via `gh pr comment` for audit trail

**USER OVERRIDE (2025-11-07):**
- **More conservative approach:** Block CRITICAL/HIGH/MEDIUM entirely
- Allow LOW only with justification
- Fix LOW issues if necessary before override
- This provides stronger quality gates for trading system safety

**Codex's Technical Implementation (Updated):**
```python
# Extend workflow_state.json structure
state["zen_review"] = {
    "status": "APPROVED",
    "continuation_id": "...",
    "issues": [
        {"id": 1, "severity": "CRITICAL", "summary": "..."},
        {"id": 2, "severity": "MEDIUM", "summary": "..."},
        {"id": 3, "severity": "LOW", "summary": "..."},
    ]
}

def allow_override(state: dict, justification: str) -> bool:
    """Check if override is allowed based on severity (conservative policy)."""
    issues = state.get("zen_review", {}).get("issues", [])

    # Block CRITICAL/HIGH/MEDIUM (stricter than original recommendation)
    blocked_issues = []
    low_issues = []

    for issue in issues:
        if issue["severity"] in {"CRITICAL", "HIGH", "MEDIUM"}:
            blocked_issues.append(issue)
        elif issue["severity"] == "LOW":
            low_issues.append(issue)

    # Cannot override if any CRITICAL/HIGH/MEDIUM issues exist
    if blocked_issues:
        print(f"❌ Cannot override {len(blocked_issues)} CRITICAL/HIGH/MEDIUM issue(s):")
        for issue in blocked_issues:
            print(f"   - [{issue['severity']}] {issue['summary']}")
        print("\n💡 FIX these issues before proceeding. Override only allowed for LOW severity.")
        return False

    # Allow LOW only with justification
    if low_issues:
        if not justification:
            print(f"❌ --justification required to override {len(low_issues)} LOW issue(s)")
            return False

        print(f"⚠️ Overriding {len(low_issues)} LOW severity issue(s):")
        for issue in low_issues:
            print(f"   - {issue['summary']}")
        print(f"\n💡 RECOMMENDED: Fix LOW issues if straightforward before override")

    # Log to PR via gh pr comment
    if low_issues:
        subprocess.run([
            "gh", "pr", "comment", "--body",
            f"⚠️ REVIEW OVERRIDE (LOW severity only):\n{justification}\n\nDeferred LOW issues: {len(low_issues)}"
        ], check=False)  # Degrade gracefully if gh unavailable

    # Persist override in state
    state["zen_review"]["override"] = {
        "justification": justification,
        "timestamp": datetime.now().isoformat(),
        "low_issues_count": len(low_issues),
        "policy": "block_critical_high_medium_allow_low",
    }

    return True
```

**CLI usage:**
```bash
# Override LOW issues only (CRITICAL/HIGH/MEDIUM must be fixed)
./scripts/workflow_gate.py request-review pr --override --justification "Minor style issues, will fix in follow-up cleanup PR #123"

# If CRITICAL/HIGH/MEDIUM exist, command fails with error
```

**DECISION:** Implement **conservative** severity-based override (block CRITICAL/HIGH/MEDIUM, allow LOW only) with mandatory justification and PR logging.

---

### Q3: Debug Rescue (Component 5) - Handoff Protocol

**Question:** Should debug rescue auto-apply recommendations or require approval?

**Gemini's Recommendation (PRIORITY 3):**
- **Tiered handoff protocol:** categorize as diagnostic (auto-apply) vs fix (require approval)
- **Safe operations (auto-apply):** grep, ls, print statements (read-only, no mutations)
- **Unsafe operations (require approval):** file modifications, package installs

**Codex's Technical Implementation:**
```python
# Request structured JSON from clink prompt
RESCUE_PROMPT = """
Analyze this stuck debugging loop and provide recommendations as JSON:
{
  "diagnostics": [
    {"command": "grep -r 'pattern' tests/", "reason": "Check for similar test patterns"},
    {"command": "pytest tests/foo.py -xvs", "reason": "Re-run with verbose output"}
  ],
  "fixes": [
    {"file": "tests/foo.py", "change": "...", "reason": "Fix assertion logic"}
  ]
}
"""

DIAGNOSTIC_WHITELIST = {
    "grep", "ls", "cat", "pytest", "python -m pdb",
    "git diff", "git log", "git show",
}

def apply_rescue(rescue_json: dict, state: dict) -> None:
    """Apply rescue recommendations with safety checks."""
    # Auto-run diagnostics
    for diagnostic in rescue_json.get("diagnostics", []):
        cmd = diagnostic["command"]
        # Enforce whitelist
        if not any(cmd.startswith(safe) for safe in DIAGNOSTIC_WHITELIST):
            print(f"⚠️ Skipping unsafe diagnostic: {cmd}")
            continue

        print(f"🔍 Running diagnostic: {cmd}")
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)

        # Log output snippet in state
        state["debug_rescue"]["diagnostics"].append({
            "command": cmd,
            "output": result.stdout[:500],
            "timestamp": datetime.now().isoformat(),
        })

    # Require approval for fixes
    fixes = rescue_json.get("fixes", [])
    if fixes:
        print(f"\n⚠️ {len(fixes)} fix(es) recommended - REQUIRES APPROVAL:")
        for fix in fixes:
            print(f"  - {fix['file']}: {fix['reason']}")
        print("\nApply fixes? (y/N): ", end="")
        # Wait for user approval
```

**DECISION:** Use tiered handoff with whitelisted auto-diagnostics and user-approved fixes.

---

### Q4: Integrated Planning (Component 2) - Task Doc Update + Review Tier

**Question:** When `plan-component` updates task doc, what review tier should trigger?

**Codex's Recommendation:**
- **Heuristic-based:** deep review for ≥4h tasks OR ≥3 components, quick review otherwise
- Parse task doc front-matter (YAML header) for `estimated_effort` field
- Allow user override via `--review-tier quick|deep` flag (always enforce minimum of quick review)

**Implementation:**
```python
def determine_review_tier(task_doc_path: str, user_tier: str | None) -> str:
    """Determine review tier based on task complexity."""
    # Parse YAML front matter
    with open(task_doc_path) as f:
        content = f.read()
        if content.startswith("---"):
            yaml_match = re.match(r"---\n(.*?)\n---", content, re.DOTALL)
            if yaml_match:
                metadata = yaml.safe_load(yaml_match.group(1))
                effort_hours = metadata.get("estimated_effort", 0)
                num_components = len(metadata.get("components", []))

                # Trigger deep review for complex tasks
                if effort_hours >= 4 or num_components >= 3:
                    default_tier = "deep"
                else:
                    default_tier = "quick"

                # User override takes precedence
                return user_tier if user_tier else default_tier

    return "quick"  # Fallback

# Re-planning protocol
def handle_replanning(task_doc_path: str, force_amend: bool) -> None:
    """Handle re-planning of APPROVED task docs."""
    with open(task_doc_path) as f:
        content = f.read()
        metadata = parse_yaml_frontmatter(content)

    if metadata.get("state") == "APPROVED":
        if not force_amend:
            raise ValueError(
                "Task already APPROVED. Use --force-amend to create amendment."
            )

        # Mark as amendment
        metadata["amendment"] = True
        metadata["amendment_id"] = str(uuid.uuid4())
        metadata["amendment_timestamp"] = datetime.now().isoformat()

        # Require at least quick review for amendments
        print("⚠️ Task APPROVED - creating amendment (requires quick review)")
```

**CLI usage:**
```bash
# Trigger planning with auto-review tier selection
./scripts/workflow_gate.py plan-component P1T13F4.1

# Force deep review even for small tasks
./scripts/workflow_gate.py plan-component P1T13F4.1 --review-tier deep

# Re-plan approved task (creates amendment)
./scripts/workflow_gate.py plan-component P1T13F4 --force-amend
```

**DECISION:** Use heuristic-based review tier selection with user override support.

---

### Q5: Subagent Delegation (Component 4) - Cost Threshold Configuration

**Question:** Are 50k/100k token thresholds empirical or estimates? Should they be configurable?

**Codex's Recommendation:**
- **Both:** constants with config override capability
- Hard-code defaults for predictability: `DELEGATION_WARN_TOKENS=50_000`, `DELEGATION_CRITICAL_TOKENS=100_000`
- Allow overrides via `.claude/workflow-config.json` for tuning
- Add `--cost-override` CLI flag for ad-hoc experiments
- Capture telemetry to refine empirically over time

**Implementation:**
```python
# Default constants (baked into workflow_gate.py)
DELEGATION_WARN_TOKENS = 50_000    # Quick operations (tests, linting)
DELEGATION_CRITICAL_TOKENS = 100_000  # Research operations (grep, reads)

def load_delegation_config() -> dict:
    """Load delegation thresholds from config file."""
    config_path = Path(".claude/workflow-config.json")
    if config_path.exists():
        with open(config_path) as f:
            config = json.load(f)
            return config.get("delegation", {})
    return {}

def get_delegation_thresholds(cli_override: dict | None = None) -> dict:
    """Get effective delegation thresholds with precedence."""
    # Load from config file
    config = load_delegation_config()

    # Merge with defaults
    thresholds = {
        "warn": config.get("warn", DELEGATION_WARN_TOKENS),
        "critical": config.get("critical", DELEGATION_CRITICAL_TOKENS),
    }

    # CLI override takes precedence
    if cli_override:
        thresholds.update(cli_override)
        # Persist override in workflow_state for audit
        state = load_workflow_state()
        state["delegation"]["override"] = {
            "thresholds": cli_override,
            "timestamp": datetime.now().isoformat(),
        }
        save_workflow_state(state)

    return thresholds

# Telemetry tracking
def record_delegation_event(operation: str, token_count: int, delegated: bool, user_override: bool):
    """Capture telemetry for threshold refinement."""
    state = load_workflow_state()
    state.setdefault("delegation", {}).setdefault("events", []).append({
        "operation": operation,
        "token_count": token_count,
        "delegated": delegated,
        "user_override": user_override,
        "timestamp": datetime.now().isoformat(),
    })
    save_workflow_state(state)
```

**CLI usage:**
```bash
# Use default thresholds
./scripts/workflow_gate.py check-delegation "run full test suite"

# Ad-hoc override
./scripts/workflow_gate.py check-delegation "run tests" --cost-override warn=60000 critical=120000

# Config file (.claude/workflow-config.json)
{
  "delegation": {
    "warn": 60000,
    "critical": 120000
  }
}
```

**DECISION:** Use constants with config + CLI override capability, capture telemetry for refinement.

---

### Q6: Debug Rescue (Component 5) - Loop Detection Threshold

**Question:** How many failed iterations constitute a "stuck loop"?

**Codex's Recommendation:**
- **5 consecutive failures** of the same test within **30 minutes** (balanced approach)
- Track via circular buffer keyed by `test_id = f"{file}::{nodeid}"`
- Reset counter when different test becomes primary failure
- Reset after 30 min elapsed since first failure in streak
- Extract test IDs from pytest output via regex on `::`
- Store `error_signature = hash(stderr[:2000])` to detect alternating error types

**Implementation:**
```python
import hashlib
from collections import deque
from datetime import datetime, timedelta

DEBUG_LOOP_THRESHOLD = 5  # Consecutive failures
DEBUG_LOOP_WINDOW = timedelta(minutes=30)

class DebugLoopDetector:
    def __init__(self):
        self.attempt_history = deque(maxlen=10)
        self.current_test_id = None
        self.failure_count = 0
        self.first_failure_time = None

    def record_failure(self, pytest_output: str) -> bool:
        """Record test failure and check if rescue needed. Returns True if stuck."""
        # Parse test ID from pytest output
        match = re.search(r"(tests/[^:]+)::(Test\w+)::(test_\w+) FAILED", pytest_output)
        if not match:
            return False  # Can't parse test ID

        test_id = f"{match.group(1)}::{match.group(2)}::{match.group(3)}"
        error_sig = hashlib.md5(pytest_output[-2000:].encode()).hexdigest()

        now = datetime.now()

        # Check if same test
        if test_id != self.current_test_id:
            # Different test - reset counter
            self.current_test_id = test_id
            self.failure_count = 1
            self.first_failure_time = now
            self.attempt_history.clear()
        else:
            # Same test - increment counter
            self.failure_count += 1

            # Check 30-min window
            if (now - self.first_failure_time) > DEBUG_LOOP_WINDOW:
                # Window expired - reset
                self.failure_count = 1
                self.first_failure_time = now
                self.attempt_history.clear()

        # Record attempt
        self.attempt_history.append({
            "test_id": test_id,
            "error_sig": error_sig,
            "timestamp": now.isoformat(),
        })

        # Check for stuck loop
        if self.failure_count >= DEBUG_LOOP_THRESHOLD:
            print(f"🚨 Stuck debugging loop detected: {test_id} failed {self.failure_count} times")
            return True

        return False
```

**CLI integration:**
```bash
# Automatic detection during test runs
./scripts/workflow_gate.py run-ci commit  # Auto-detects loops, offers rescue
```

**DECISION:** Use 5 consecutive failures in 30-min window with test-ID-based tracking.

---

### Q7: Unified Review (Component 6) - Independent Fresh Reviews

**Question:** How to ensure PR review iterations are truly independent?

**Codex's Recommendation:**
- **DO NOT reuse continuation_id** across iterations
- Provide **FULL PR diff** (branch vs base) every time
- Add short annotation listing files changed since previous iteration
- Track findings per iteration in `workflow_state["review_history"]`
- If iteration N finds NEW critical issues absent in N-1, **continue counting** (iteration 3 remains cap)
- Flag condition and recommend manual architectural review
- Only reset to iteration 1 if user explicitly requests via `--reset-iterations`

**Implementation:**
```python
def request_pr_review(iteration: int, max_iterations: int = 3) -> dict:
    """Request independent PR review iteration."""
    state = load_workflow_state()
    review_history = state.setdefault("review", {}).setdefault("history", [])

    # Get full PR diff (always fresh, no continuation_id)
    pr_diff = subprocess.run(
        ["git", "diff", "origin/master...HEAD"],
        capture_output=True, text=True
    ).stdout

    # Annotation: what changed since last iteration
    if iteration > 1 and review_history:
        last_review = review_history[-1]
        last_files = set(last_review.get("files_reviewed", []))
        current_files = set(parse_changed_files(pr_diff))
        new_files = current_files - last_files

        annotation = f"\n**NOTE:** Since last review, {len(new_files)} file(s) changed: {', '.join(new_files)}"
    else:
        annotation = ""

    # Request review (NO continuation_id - fresh session)
    prompt = f"""
    Review this PR (iteration {iteration}/{max_iterations}):

    {pr_diff}
    {annotation}

    Provide severity-tagged findings.
    """

    review_result = call_clink_codex(prompt, continuation_id=None)  # Fresh session

    # Parse findings
    issues = parse_review_issues(review_result)

    # Check for NEW critical issues in later iterations
    if iteration > 1:
        prev_critical = {
            issue["summary"]
            for prev in review_history
            for issue in prev.get("issues", [])
            if issue["severity"] in {"CRITICAL", "HIGH"}
        }
        new_critical = {
            issue["summary"]
            for issue in issues
            if issue["severity"] in {"CRITICAL", "HIGH"}
            and issue["summary"] not in prev_critical
        }

        if new_critical:
            print(f"\n⚠️ WARNING: Iteration {iteration} found {len(new_critical)} NEW critical issue(s):")
            for summary in new_critical:
                print(f"  - {summary}")
            print("\n💡 RECOMMENDATION: Consider manual architectural review before final iteration")

    # Record iteration findings
    review_history.append({
        "iteration": iteration,
        "timestamp": datetime.now().isoformat(),
        "files_reviewed": parse_changed_files(pr_diff),
        "issues": issues,
        "continuation_id": None,  # Explicitly track as independent
    })

    save_workflow_state(state)

    return {
        "issues": issues,
        "iteration": iteration,
        "max_iterations": max_iterations,
    }
```

**CLI usage:**
```bash
# Iteration 1 (fresh)
./scripts/workflow_gate.py request-review pr

# Iteration 2 (independent, no continuation)
./scripts/workflow_gate.py request-review pr

# Iteration 3 (final, independent)
./scripts/workflow_gate.py request-review pr

# Reset counter (user explicitly requests fresh start)
./scripts/workflow_gate.py request-review pr --reset-iterations
```

**DECISION:** Independent reviews with full context, no continuation_id reuse, track findings per iteration.

---

**Summary of Final Decisions:**

| Component | Edge Case | Resolution |
|-----------|-----------|------------|
| 1. Smart Testing | Cross-component deps | CORE_PACKAGES heuristic (libs/, config/, infra/, tests/fixtures/, scripts/) |
| 2. Integrated Planning | Review tier selection | Heuristic-based (≥4h OR ≥3 components → deep, else quick) + user override |
| 2. Integrated Planning | Re-planning APPROVED tasks | Amendment protocol with `--force-amend`, requires quick review |
| 3. Unified Review | User override protocol | **CONSERVATIVE:** Block CRITICAL/HIGH/MEDIUM, allow LOW only + justification + PR logging |
| 4. Subagent Delegation | Cost thresholds | Constants (50k/100k) with config + CLI override, telemetry for refinement |
| 5. Debug Rescue | Handoff protocol | Tiered (auto-apply diagnostics, require approval for fixes) with whitelist |
| 5. Debug Rescue | Loop detection threshold | 5 consecutive failures of same test in 30-min window |
| 6. Unified Review | Independent reviews | Full context each time, no continuation_id reuse, track findings per iteration |

**Next Steps:**
1. Update component specifications with edge case resolutions
2. Proceed with implementation following Phase 1→2→3→4→5→6 order
3. Test each edge case scenario during validation

---

## Implementation Plan

### Phase 1: Smart Testing System (3-4 hours)

**Status:** NOT STARTED

**Tasks:**
1. Implement `SmartTestRunner` class in workflow_gate.py
2. Add `run-ci` command with "commit" and "pr" modes
3. Integrate with existing `record-ci` workflow
4. Test with real commit scenarios
5. Document usage in CLAUDE.md

**Deliverables:**
1. Enhanced `scripts/workflow_gate.py` with smart testing
2. Updated `CLAUDE.md` with `run-ci` examples
3. Validation: 10-20x faster commit CI, 7-8x context reduction

---

### Phase 2: Automated Subagent Delegation Rules (2-3 hours)

**Status:** NOT STARTED

**Tasks:**
1. Implement `DelegationRules` class in workflow_gate.py
2. Add `check-delegation` command
3. Define OPERATION_COSTS empirically
4. Create delegation command templates
5. Integrate with context monitoring (Phase 3)

**Deliverables:**
1. Enhanced `scripts/workflow_gate.py` with delegation rules
2. Updated `.claude/workflows/16-subagent-delegation.md` with auto-rules
3. Validation: 50-75% context reduction on high-cost operations

---

### Phase 3: Integrated Planning Workflow (2-3 hours)

**Status:** NOT STARTED

**Tasks:**
1. Implement `PlanningWorkflow` class in workflow_gate.py
2. Add `create-task`, `plan-subfeatures`, `start-task` commands
3. Integrate with task-state.json and workflow-state.json
4. Connect to clink gemini planner for task review
5. Consolidate 00-task-breakdown.md + 12-phase-management.md + 13-task-creation-review.md

**Deliverables:**
1. Enhanced `scripts/workflow_gate.py` with planning workflow
2. New `.claude/workflows/03-planning.md` (consolidated)
3. Removed old planning workflows
4. Validation: Single-command task creation with auto-review

---

### Phase 4: Unified Review System (3-4 hours)

**Status:** NOT STARTED

**Tasks:**
1. Implement `UnifiedReviewSystem` class in workflow_gate.py
2. Add `request-review` command with "commit" and "pr" scopes
3. Implement multi-iteration PR review loop
4. Consolidate 03-reviews.md + 03-reviews.md
5. Update `advance review` to use unified system

**Deliverables:**
1. Enhanced `scripts/workflow_gate.py` with unified reviews
2. New `.claude/workflows/03-reviews.md` (consolidated)
3. Removed old review workflows
4. Validation: Multi-iteration PR review catches all issues

---

### Phase 5: Debug Rescue Workflow (2-3 hours)

**Status:** NOT STARTED

**Tasks:**
1. Implement `DebugRescue` class in workflow_gate.py
2. Add `debug-rescue` command
3. Integrate loop detection with `run-ci` command
4. Create clink codex rescue prompts
5. Document debug rescue workflow

**Deliverables:**
1. Enhanced `scripts/workflow_gate.py` with debug rescue
2. Updated `.claude/workflows/06-debugging.md` with rescue workflow
3. Validation: 30-45 min debug loops → 5-10 min rescue

---

### Phase 6: Workflow Simplification (1-2 hours)

**Status:** NOT STARTED

**Tasks:**
1. Simplify CLAUDE.md (750 → 400 lines)
2. Consolidate workflow files (8,500 → 4,000 lines)
3. Update all workflow docs with workflow_gate.py commands
4. Remove redundant process enforcement text
5. Validate context reduction

**Deliverables:**
1. Simplified `CLAUDE.md` (46% reduction)
2. Consolidated `.claude/workflows/` (53% reduction)
3. Validation: 20-30k → 8-12k token context cost

---

## Success Criteria

**Overall Success:**

1. **Smart Testing:**
   - [ ] Commit CI time: 2-5 min → 15-30 sec (10-20x faster)
   - [ ] Commit CI context: 15k → 2k tokens (7-8x reduction)
   - [ ] PR CI still runs full suite (unchanged)

2. **Auto-Delegation:**
   - [ ] High-cost operations (>50k tokens) auto-delegate
   - [ ] Context-aware delegation at 70% and 85% thresholds
   - [ ] Context reduction: 50-75% on delegated operations

3. **Integrated Planning:**
   - [ ] Single command creates task + requests review
   - [ ] Auto-detects subfeature split needs (>8h)
   - [ ] State tracking unified (task-state + workflow-state)

4. **Unified Reviews:**
   - [ ] One command for commit and PR reviews
   - [ ] Multi-iteration PR review (max 3 iterations)
   - [ ] Independent iterations (fresh context each time)
   - [ ] BOTH reviewers must find NO issues to approve PR

5. **Debug Rescue:**
   - [ ] Auto-detects stuck loops (3+ failures same test)
   - [ ] Escalates to clink codex for systematic debugging
   - [ ] Time saved: 30-45 min loops → 5-10 min rescue

6. **Workflow Simplification:**
   - [ ] CLAUDE.md reduced 46% (750 → 400 lines)
   - [ ] Workflow docs reduced 53% (8,500 → 4,000 lines)
   - [ ] Context cost reduced 60% (20-30k → 8-12k tokens)

---

## Out of Scope

**Not Included in F4:**

- **Full test parallelization** → Smart selection sufficient for MVP
- **Automatic PR creation** → Manual trigger maintained for safety
- **Automatic merge** → User approval still required
- **Cross-repo testing** → Single-repo context only
- **Custom subagent types** → Use existing Task tool agents only
- **Review caching** → Fresh review each time (quality over speed)
- **Workflow UI/dashboard** → CLI-only interface
- **Metrics collection** → Manual validation sufficient

---

## Risk Assessment

**Risks:**

1. **Smart Testing False Negatives**
   - **Impact:** HIGH (might miss relevant tests)
   - **Mitigation:**
     - Conservative module detection (include integration tests)
     - Fall back to full CI if >5 modules changed
     - Mandatory full CI before PR (safety net)
     - Monitor false negative rate in practice

2. **Delegation Rules Too Aggressive**
   - **Impact:** MEDIUM (might over-delegate simple operations)
   - **Mitigation:**
     - Empirical OPERATION_COSTS tuning
     - User can override delegation recommendations
     - Monitor delegation patterns and adjust thresholds

3. **Multi-Iteration Review Loops**
   - **Impact:** MEDIUM (could extend PR review time)
   - **Mitigation:**
     - Max 3 iterations before escalation to user
     - Each iteration is independent (fresh perspective)
     - User can approve with known issues if justified

4. **Debug Rescue False Positives**
   - **Impact:** LOW (might trigger rescue too early)
   - **Mitigation:**
     - Conservative thresholds (3+ failures, 30+ min)
     - Manual trigger available if auto-detection too sensitive
     - Rescue is guidance, not blocking

5. **Workflow Simplification Loses Context**
   - **Impact:** MEDIUM (might remove useful guidance)
   - **Mitigation:**
     - Keep domain knowledge in docs (only remove process redundancy)
     - Preserve detailed workflows for complex operations
     - User can still read full workflows if needed

---

## Dependencies

**Requires:**
- P1T13-F3 (Context Optimization & Checkpointing) - COMPLETED
  - Context monitoring infrastructure (workflow_gate.py)
  - Subagent delegation patterns (Task tool)
  - Context thresholds (70% WARN, 85% CRITICAL)

**Builds On:**
- workflow_gate.py state machine (Component 2 from F3)
- task-state.json tracking (Phase 0 from earlier work)
- clink + gemini/codex review infrastructure (Tier 1/2/3)

---

## Notes

- This is initial proposal (revision 1) awaiting gemini + codex planner feedback
- Focus on MVP: practical improvements from real implementation experience
- All enhancements integrate with existing workflow_gate.py
- Preserve quality: No shortcuts on review rigor or test coverage
- User control maintained: Auto-delegation is recommendations, not forced

---

## Review History

**Round 1 (2025-11-07):**
- Status: ✅ APPROVED
- Gemini planner: ✅ APPROVED - "High-quality task plan, model of how to build upon prior work"
- Codex planner: ✅ APPROVED - "Implementation is feasible with shared utilities and proper modularization"
- Continuation ID: 3bfc744f-3719-4994-b352-3b8488ba03b1 (45 turns remaining)
- Review Duration: ~80 seconds total (gemini: 33s, codex: 47s)
- Key Findings: 4 edge cases from gemini, 5 technical guidance items from codex
- Recommendation: Proceed to implementation following codex's 4-step sequence

---

## References

**Related Tasks:**
- P1T13-F3: Context Optimization & Checkpointing (COMPLETED)

**Workflows:**
- `.claude/workflows/00-task-breakdown.md` - Task decomposition (to be consolidated)
- `.claude/workflows/12-phase-management.md` - Phase planning (to be consolidated)
- `.claude/workflows/13-task-creation-review.md` - Task review (to be consolidated)
- `.claude/workflows/03-reviews.md` - Quick review (to be consolidated)
- `.claude/workflows/03-reviews.md` - Deep review (to be consolidated)
- `.claude/workflows/16-subagent-delegation.md` - Delegation patterns (to be enhanced)

**Implementation:**
- `scripts/workflow_gate.py` - Workflow enforcement (to be enhanced)
- `scripts/update_task_state.py` - Task state tracking (to be integrated)
- `CLAUDE.md` - Primary guidance (to be simplified)
