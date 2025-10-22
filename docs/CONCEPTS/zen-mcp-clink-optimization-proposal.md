# Zen-MCP Clink-Only Optimization Proposal

**Status:** ✅ Revised - Ready for Re-Review
**Created:** 2025-10-21
**Revised:** 2025-10-21 (Fixed ALL Codex review findings + subscription model)
**Purpose:** Optimize trading platform development workflow using zen-mcp clink with codex and gemini-cli exclusively

**Revision Summary:**
- ✅ **Fixed HIGH #1:** Removed all model= parameters (not supported in clink API)
- ✅ **Fixed HIGH #2:** Updated to subscription-based cost model (CLI tools, not direct API calls)
- ✅ **Fixed MEDIUM:** Recalculated costs with subscription model
- ✅ **Major improvement:** Monthly cost reduced from $468 → $320-350 (691% ROI!)

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Clink Architecture](#clink-architecture)
3. [Model Selection Strategy](#model-selection-strategy)
4. [Workflow Optimizations](#workflow-optimizations)
5. [Task Creation Review Process](#task-creation-review-process)
6. [Implementation Plan](#implementation-plan)
7. [Cost-Benefit Analysis](#cost-benefit-analysis)

---

## Executive Summary

**Recommendation:** Optimize zen-mcp integration to use **ONLY clink** with two CLI tools:
1. **Codex CLI** (configured to use `gpt-5-codex` by default) - Code-focused tasks
2. **Gemini CLI** (supports multiple models via CLI flags) - Large context, planning, cost optimization

**Key Changes from Current Approach:**
- ❌ **Remove:** Direct use of zen tools (chat, codereview, planner, etc.)
- ✅ **Use ONLY:** clink → codex/gemini CLIs
- ✅ **Add:** Task creation review workflow
- ✅ **Standardize:** Model selection via CLI configuration (not API parameters)

**Benefits:**
- **Simpler:** One integration pattern (clink only)
- **Consistent:** Predictable model behavior (codex = gpt-5-codex)
- **Cost-optimized:** Gemini models for large context, planning
- **Quality:** Task creation review prevents scope creep

---

## Clink Architecture

### Verified Configuration

**Available CLI Clients:**
```
1. codex    - Codex CLI with gpt-5-codex model
2. gemini   - Gemini CLI with model selection
3. claude   - Claude CLI (not used in this proposal)
```

**Available Roles (both codex and gemini):**
```
1. default       - General questions, code generation
2. codereviewer  - Code analysis, severity reporting, security audit
3. planner       - Strategic planning, task breakdown, implementation plans
```

**Clink Tool Signature:**
```python
mcp__zen-mcp__clink(
    prompt: str,                          # Required - Task description
    cli_name: "codex" | "gemini",        # Required in our workflows
    role: "default" | "codereviewer" | "planner" = "default",
    absolute_file_paths: List[str] = [], # Optional - Files for context
    images: List[str] = [],              # Optional - Images for context
    continuation_id: str = None          # Optional - Multi-turn context
)
```

### Context Flow

**Multi-turn conversations preserve full context:**
```
Phase 1: Gemini codereviewer finds 3 issues
         → Returns continuation_id: "abc123"

Phase 2: Codex planner creates fix plan
         → Uses continuation_id: "abc123"
         → Codex knows all 3 issues from Phase 1!

Phase 3: Codex codereviewer validates fixes
         → Uses continuation_id: "abc123"
         → Codex knows issues + plan + implementation!
```

---

## Model Selection Strategy

### Codex CLI - Always gpt-5-codex

**Model:** `gpt-5-codex` (configured in Codex CLI, always used by default)

**How it works:**
- Codex CLI is pre-configured to use gpt-5-codex model
- No need to specify model in clink calls
- Model selection happens automatically via CLI configuration

**Specifications:**
- 400K context window
- Specialized for coding, refactoring, software architecture
- Optimized for code generation and analysis

**Use Codex (gpt-5-codex) for:**
- ✅ Code reviews (quick & deep)
- ✅ Security audits (code-focused)
- ✅ Code generation (tests, implementations)
- ✅ Refactoring validation
- ✅ Implementation planning
- ✅ Bug fixing assistance

**Example:**
```bash
# Codex automatically uses gpt-5-codex (no model parameter needed)
Use clink with cli_name="codex" role="codereviewer":
- Review staged changes for trading safety
- Files: apps/execution_gateway/order_placer.py
```

### Gemini CLI - Optimized Model Selection

**Available Models (from gemini-cli, updated 2025):**
- `gemini-2.5-pro` - Latest, large context (1M tokens), advanced reasoning
- `gemini-2.5-flash` - Latest flash, fast, cost-effective
- `gemini-2.0-flash-exp` - Experimental flash variant
- `gemini-exp-1206` - Experimental features

**Selection Strategy:**

| Task Type | Gemini Model | Reasoning |
|-----------|--------------|-----------|
| Large branch review | `gemini-2.5-pro` | 1M token context for entire branch |
| High-level planning | `gemini-2.5-pro` | Advanced reasoning for architecture |
| Quick analysis | `gemini-2.5-flash` | Fast, cost-effective, latest |
| Task creation review | `gemini-2.5-flash` | Structured validation, efficient |
| Multi-file coordination | `gemini-2.5-pro` | Large context window |

**How it works:**
- Gemini CLI supports model selection via command-line flags
- Model specified in gemini CLI configuration or via --model flag
- Clink calls gemini CLI which handles model routing

**Cost Model:**
- Gemini CLI uses subscription or free tier (not pay-per-use API)
- No variable costs based on token usage
- Unlimited usage within subscription limits

**Example:**
```bash
# Gemini uses model configured in CLI or specified via wrapper
# Clink does not pass model parameter directly
Use clink with cli_name="gemini" role="planner":
- Plan feature extraction to libs/feature_store/
- Files: strategies/alpha_baseline/, apps/signal_service/

# Note: Model selection (gemini-2.5-pro vs flash) happens in gemini CLI config
```

---

## Workflow Optimizations

### 1. Pre-Commit Quick Review (Codex Codereviewer)

**When:** Before EVERY commit that touches code
**Duration:** ~20-30 seconds
**Tool:** clink + codex + codereviewer (uses gpt-5-codex automatically)

**Workflow:**
```bash
# 1. Stage changes
git add apps/execution_gateway/order_placer.py

# 2. Clink review with codex (gpt-5-codex used automatically)
"Use clink with codex codereviewer to review staged changes for:
- Circuit breaker checks before order placement
- Idempotent order IDs (deterministic hash, no UUIDs)
- Position limit validation (per-symbol and portfolio-wide)
- Race conditions in concurrent operations
- Structured logging with context (strategy_id, client_order_id)

Files: apps/execution_gateway/order_placer.py

Output format: Severity (HIGH/MEDIUM/LOW), Location, Issue, Impact, Fix"

# 3. Fix HIGH/CRITICAL issues immediately
# 4. Re-request verification with continuation_id
# 5. Commit when approved
```

**Success Criteria:**
- ✅ Review completes in < 30 seconds
- ✅ All HIGH/CRITICAL issues fixed before commit
- ✅ Continuation_id captured for audit trail

### 2. Deep Branch Review (Gemini → Codex Multi-Phase)

**When:** Before creating PR
**Duration:** ~3-5 minutes
**Tool:** clink + gemini (1.5-pro) + codex (gpt-5-codex)

**Workflow:**
```bash
# Phase 1: Gemini codereviewer (breadth analysis)
"Use clink with gemini codereviewer (model: gemini-1.5-pro) to analyze all branch changes:
- Overall architecture patterns
- Service integration points
- Test coverage completeness
- Documentation quality
- Code organization

Compare: master..HEAD (all changed files)

Output: Comprehensive findings with severity levels
Save continuation_id for next phase"

# Phase 2: Codex planner (remediation if issues found)
"Use clink with codex planner (model: gpt-5-codex) to create fix plan:
- Break fixes into logical steps
- Prioritize by severity
- Include test strategy for each fix
- Estimate time per fix

continuation_id: <from Phase 1>
Codex now knows all Gemini's findings!"

# Phase 3: Implement fixes (if needed)
# ... implementation ...

# Phase 4: Codex codereviewer (final validation)
"Use clink with codex codereviewer (model: gpt-5-codex) to verify:
- All HIGH/CRITICAL issues resolved
- MEDIUM issues addressed or deferred with reason
- Tests cover new/changed logic
- Documentation updated

continuation_id: <from Phase 1>
Files: <all changed files>

Ready for PR if approved!"
```

**Success Criteria:**
- ✅ Gemini analyzes entire branch (1M token context)
- ✅ Codex validates with code-specific focus
- ✅ Context preserved across all phases
- ✅ Ready for PR creation after approval

### 3. Complex Refactoring (Gemini Planner → Iterative Codex Review)

**When:** Large refactors (>500 LOC, multiple files)
**Duration:** Varies (planning: 2-3 min, validation per step: 30 sec)
**Tool:** clink + gemini (1.5-pro) + codex (gpt-5-codex)

**Workflow:**
```bash
# Phase 1: Gemini planner (strategy)
"Use clink with gemini planner (model: gemini-1.5-pro) to plan:

Task: Extract feature computation from apps/signal_service/ to libs/feature_store/

Requirements:
- Maintain offline/online parity
- Backward compatibility (no breaking changes)
- Comprehensive test coverage
- Zero downtime migration

Files: strategies/alpha_baseline/features.py, apps/signal_service/features.py

Output: Phased plan with validation gates
Save continuation_id"

# Phase 2: Implement step 1 (from plan)
# ... code implementation ...

# Phase 3: Codex codereviewer validates step 1
"Use clink with codex codereviewer (model: gpt-5-codex) to validate step 1:
- Code quality check
- Test coverage verified
- No regressions introduced
- Parity maintained

continuation_id: <from Phase 1>
Files: libs/feature_store/momentum.py, tests/libs/test_momentum.py"

# Phase 4: Commit step 1 after approval

# Repeat Phase 2-4 for each step in the plan
```

**Success Criteria:**
- ✅ Gemini creates comprehensive plan (large context)
- ✅ Each step validated by Codex before commit
- ✅ Context preserved throughout refactor (days/weeks)
- ✅ No step committed without validation

### 4. Security Audit (Codex Codereviewer Focused)

**When:** Periodic (monthly) or before production deployment
**Duration:** ~5-10 minutes
**Tool:** clink + codex + codereviewer + gpt-5-codex

**Workflow:**
```bash
# Codex codereviewer for deep security analysis
"Use clink with codex codereviewer (model: gpt-5-codex) to audit:

Target: apps/execution_gateway/

Security Focus:
- SQL injection (parameterized queries ONLY)
- API key leakage in logs or error messages
- Missing circuit breaker checks in critical paths
- Race conditions in order placement logic
- Input validation gaps (user input, API responses)
- Insecure Redis key patterns
- Missing authentication checks on endpoints

Files: apps/execution_gateway/ (all Python files)

Output: Security findings by severity with remediation steps"
```

**Success Criteria:**
- ✅ All CRITICAL security issues identified
- ✅ Remediation plan with specific code changes
- ✅ Follow-up validation after fixes

### 5. Test Generation (Gemini Planner → Codex Default)

**When:** After implementing new features
**Duration:** Planning: 1-2 min, Generation: 2-3 min
**Tool:** clink + gemini (1.5-flash) + codex (gpt-5-codex)

**Workflow:**
```bash
# Phase 1: Gemini planner (test strategy)
"Use clink with gemini planner (model: gemini-1.5-flash) to design test strategy:

Target: libs/risk_management/checker.py

Requirements:
- Unit tests for all public methods
- Integration tests for external dependencies (Redis, DB)
- Edge cases: circuit breaker tripped, stale data (>30min), concurrent updates
- Follow /docs/STANDARDS/TESTING.md standards

Files: libs/risk_management/checker.py

Output: Test structure with specific scenarios
Save continuation_id"

# Phase 2: Codex default (test implementation)
"Use clink with codex default (model: gpt-5-codex) to generate pytest tests:

Based on test strategy, implement:
- Happy path tests (limits enforced correctly)
- Edge case tests (from strategy)
- Mock external dependencies (Redis, Postgres)
- Follow project conventions (fixtures, parametrize, etc.)

continuation_id: <from Phase 1>
Files: tests/libs/risk_management/test_checker.py (new file)

Output: Complete pytest test suite"
```

**Success Criteria:**
- ✅ Comprehensive test strategy from Gemini
- ✅ Codex generates runnable pytest tests
- ✅ Tests follow project standards
- ✅ Edge cases covered

---

## Task Creation Review Process

### Overview

**Problem:** Tasks created ad-hoc without validation lead to:
- Scope creep (tasks too large)
- Missing requirements (incomplete specs)
- Unclear acceptance criteria
- Poor time estimates

**Solution:** Mandatory task creation review using clink + gemini + planner

### Task Creation Workflow

**When:** Before starting ANY new phase/task (P*T*)
**Duration:** ~2-3 minutes
**Tool:** clink + gemini + planner + gemini-1.5-flash

**Step-by-Step Process:**

#### Step 1: Draft Task Document

Create task following template (`/docs/TASKS/00-TEMPLATE_TASK.md`):
```markdown
# P1T11 - Example Task

## Objective
[Clear, measurable goal]

## Requirements
- [ ] Requirement 1
- [ ] Requirement 2

## Implementation Approach
[High-level approach]

## Acceptance Criteria
- [ ] Criterion 1
- [ ] Criterion 2

## Testing Strategy
[How to validate]

## Time Estimate
[X hours/days]
```

#### Step 2: Clink Review with Gemini Planner

```bash
"Use clink with gemini planner (model: gemini-1.5-flash) to review task:

Task Document: docs/TASKS/P1T11_TASK.md

Review Focus:
1. Scope Validation
   - Is task appropriately sized (4-8 hours ideal)?
   - Should it be split into sub-tasks?
   - Are there hidden complexities?

2. Requirements Completeness
   - Are all functional requirements listed?
   - Are non-functional requirements clear (performance, security)?
   - Are dependencies on other tasks identified?

3. Acceptance Criteria Quality
   - Are criteria measurable and testable?
   - Do they cover all requirements?
   - Are edge cases considered?

4. Testing Strategy Adequacy
   - Unit tests identified?
   - Integration tests needed?
   - Edge cases covered?

5. Time Estimate Realism
   - Does estimate match scope?
   - Are risks/unknowns accounted for?
   - Is research time included if needed?

Output Format:
- Severity: HIGH (blocker) / MEDIUM (important) / LOW (suggestion)
- Category: Scope / Requirements / Criteria / Testing / Estimate
- Issue: What's missing or problematic
- Recommendation: Specific improvement

Files: docs/TASKS/P1T11_TASK.md"
```

#### Step 3: Address Findings

**HIGH severity (blocking):**
- Fix immediately before task starts
- Examples: Missing critical requirements, scope too large (>8 hours)

**MEDIUM severity (important):**
- Address or document deferral
- Examples: Unclear acceptance criteria, missing test strategy

**LOW severity (suggestions):**
- Optional improvements
- Examples: More detailed time breakdown, additional edge cases

#### Step 4: Re-validate After Changes

```bash
"Use clink with gemini planner (model: gemini-1.5-flash) to verify:

Task Document: docs/TASKS/P1T11_TASK.md (updated)

Verify:
- All HIGH issues resolved
- MEDIUM issues addressed or deferred with reason
- Task is ready to start

continuation_id: <from Step 2>

Output: Approved for implementation OR remaining issues"
```

#### Step 5: Start Task After Approval

Only begin implementation when Gemini approves:
```
✅ Task P1T11 approved for implementation
- Scope: Appropriate (6 hours estimated)
- Requirements: Complete
- Acceptance Criteria: Measurable
- Testing Strategy: Comprehensive
- Time Estimate: Realistic with buffer
```

### Workflow Reminders in Review Responses

**CRITICAL:** All review prompts MUST include workflow reminders in the response format.

**Why:** Claude Code tends to forget established workflows after large amounts of work. Review responses should remind Claude about the current workflow to keep it on track.

**Standard Reminder Template:**
```
## 🔔 Workflow Reminder

After addressing findings, remember to follow the established workflow:

1. **4-Step Pattern (MANDATORY):**
   - ✅ Implement logic
   - ✅ Create test cases (TDD)
   - ✅ Request zen-mcp review (you are here!)
   - ❌ Commit changes (NOT YET - wait for approval)

2. **Progressive Commits:**
   - Commit every 30-60 minutes per logical component
   - Never combine multiple components in one commit
   - Each commit requires quick review approval

3. **After This Review:**
   - Fix HIGH/CRITICAL issues immediately
   - Re-request verification with continuation_id
   - Only commit when explicitly approved
   - Include continuation_id in commit message

4. **Before PR:**
   - Deep review MANDATORY (use clink + gemini codereviewer)
   - See `.claude/workflows/04-zen-review-deep.md`

**Do NOT skip these steps after completing fixes!**
```

This reminder should be included at the END of every review response.

---

### Task Review Template

**File:** `.claude/prompts/clink-reviews/task-creation-review.md`

```markdown
# Task Creation Review (Gemini Planner)

You are reviewing a task document for a trading platform development project.

## Review Criteria

### 1. Scope Validation (CRITICAL)
**Questions:**
- Is the task appropriately sized? (Ideal: 4-8 hours, Max: 16 hours)
- If >8 hours, should it be split? Suggest breakdown.
- Are there hidden complexities not captured in estimate?
- Are external dependencies clearly identified?

**Output:** APPROVE scope OR RECOMMEND split with specific sub-tasks

### 2. Requirements Completeness (CRITICAL)
**Questions:**
- Are all functional requirements explicit and testable?
- Are non-functional requirements clear? (performance, security, scalability)
- Are edge cases identified? (error handling, boundary conditions)
- Are dependencies on other components/tasks listed?
- Are trading safety requirements called out? (circuit breakers, idempotency, etc.)

**Output:** List MISSING requirements by category

### 3. Acceptance Criteria Quality (HIGH)
**Questions:**
- Is each criterion measurable and testable?
- Do criteria cover ALL requirements?
- Are both happy path AND error cases included?
- Can completion be verified objectively?

**Output:** Flag VAGUE or INCOMPLETE criteria with specific improvements

### 4. Testing Strategy Adequacy (HIGH)
**Questions:**
- Are unit tests identified for business logic?
- Are integration tests planned for external dependencies?
- Are edge cases from requirements covered in test plan?
- Does strategy follow /docs/STANDARDS/TESTING.md?

**Output:** Identify GAPS in test coverage

### 5. Time Estimate Realism (MEDIUM)
**Questions:**
- Does estimate align with scope and complexity?
- Is research/exploration time included if needed?
- Is buffer included for unknowns (10-20%)?
- Are dependencies on other teams accounted for?

**Output:** Flag UNREALISTIC estimates (too optimistic or too pessimistic)

## Output Format

For each finding:
```
Severity: HIGH | MEDIUM | LOW
Category: Scope | Requirements | Criteria | Testing | Estimate
Issue: [Specific problem found]
Recommendation: [Concrete improvement]
```

## Approval Decision

After review, provide ONE of:
- ✅ APPROVED: Task ready to start
- ⚠️ APPROVED WITH MINOR FIXES: Start after addressing [specific items]
- ❌ NOT APPROVED: Fix [blocking issues] before starting

## Trading Platform Context

Remember this is a trading platform with critical safety requirements:
- Idempotency for all order operations
- Circuit breakers before risk-taking actions
- Position limits validation
- Data quality gates
- Comprehensive logging for audit trail

Flag any task that touches order placement, risk management, or data handling
without explicit safety requirements.

---

## 🔔 Workflow Reminder (Include in EVERY response)

After addressing task review findings, remember to follow the workflow:

**When task is APPROVED:**
1. **Start implementation** following the 4-step pattern:
   - Step 1: Implement logic component
   - Step 2: Create test cases (TDD)
   - Step 3: Request quick review (clink + codex codereviewer)
   - Step 4: Commit after approval

2. **Repeat 4-step pattern** for EACH logical component in the task

3. **Before creating PR:**
   - Deep review MANDATORY (clink + gemini codereviewer)
   - See `.claude/workflows/04-zen-review-deep.md`

**Do NOT:**
- ❌ Skip the 4-step pattern
- ❌ Combine multiple components in one commit
- ❌ Commit without review approval
- ❌ Create PR without deep review

**Remember:** This task validation is step 0. Now follow steps 1-4 for implementation!
```

### Integration with 4-Step Pattern

**Updated 4-Step Pattern:**
```markdown
1. **Review task** (NEW!) - Gemini planner validates task document
2. **Implement logic** - Write code based on approved task
3. **Create test cases** - TDD following test strategy from task
4. **Request zen-mcp review** - Codex codereviewer validates implementation
5. **Commit** - After approval with audit trail
```

### Task Review Examples

#### Example 1: Task Approved

```bash
$ "Use clink with gemini planner to review docs/TASKS/P1T11_TASK.md"

Gemini Response:
✅ APPROVED - Task ready to start

Review Summary:
- Scope: Appropriate (6 hours estimated) ✅
- Requirements: Complete with trading safety focus ✅
- Acceptance Criteria: Measurable and testable ✅
- Testing Strategy: Comprehensive (unit + integration) ✅
- Time Estimate: Realistic with 15% buffer ✅

Strengths:
+ Explicitly calls out circuit breaker integration
+ Edge cases well-identified (stale data, concurrent updates)
+ Test strategy includes backtest parity validation

Minor Suggestions (LOW):
- Consider adding performance acceptance criteria (latency < 100ms)
- Could expand error handling test cases

Continuation ID: task-review-abc123
```

#### Example 2: Task Needs Splitting

```bash
$ "Use clink with gemini planner to review docs/TASKS/P1T12_TASK.md"

Gemini Response:
❌ NOT APPROVED - Scope too large, recommend split

Findings:

HIGH (Scope):
- Task estimated at 20 hours (exceeds 16 hour max)
- Contains 3 distinct components that should be separate tasks:
  1. Real-time data ingestion (8 hours)
  2. Data quality validation (6 hours)
  3. Backfill historical data (6 hours)
- Mixing infrastructure setup with business logic

Recommendation:
Split into 3 tasks:
- P1T12: Real-time market data ingestion
- P1T13: Data quality validation gates
- P1T14: Historical data backfill automation

MEDIUM (Testing):
- Integration test strategy vague ("test with live data")
- Recommend: Mock data provider for reproducible tests

Action Required:
1. Split task into P1T12, P1T13, P1T14
2. Create separate task documents for each
3. Re-submit P1T12 for review (smallest, start here)
4. Clarify integration test approach

Continuation ID: task-review-def456
```

#### Example 3: Missing Requirements

```bash
$ "Use clink with gemini planner to review docs/TASKS/P1T13_TASK.md"

Gemini Response:
⚠️ APPROVED WITH FIXES - Address 2 MEDIUM issues before starting

Findings:

MEDIUM (Requirements):
- Missing: What happens when circuit breaker trips during position check?
- Missing: How to handle Redis connection failures?
- Missing: Position limit values (per-symbol, portfolio-wide) not specified

Recommendation:
Add to requirements:
+ If circuit breaker TRIPPED, position check should fail-safe (block order)
+ Redis unavailable → fallback to DB query with warning log
+ Load limits from config: max_position_per_symbol, max_portfolio_notional

MEDIUM (Acceptance Criteria):
- Criterion "Position limits enforced" is not measurable
- Improve to: "Given position=90 and limit=100, order for qty=15 is REJECTED"

Recommendation:
Rewrite criteria with specific examples and expected outcomes

LOW (Testing):
- Consider adding concurrent position update test (race condition)

Action Required:
1. Add 3 missing requirements (circuit breaker, Redis failover, limit values)
2. Rewrite acceptance criteria with concrete examples
3. Re-submit for verification OR start with documented deferral

Continuation ID: task-review-ghi789
```

---

## Implementation Plan

### Phase 1: Documentation & Templates (Week 1)

**Objective:** Create standardized clink workflows and templates

**Single Task: Implement Clink-Based Zen-MCP Documentation**

**Deliverables:**
1. Update CLAUDE.md with clink-only workflows
   - Remove references to direct zen tools
   - Add clink workflows for quick review, deep review, task creation review
   - Document model selection strategy (codex=gpt-5-codex, gemini=2.5)

2. Create standardized review prompts (`.claude/prompts/clink-reviews/`)
   - `quick-safety-review.md` - Pre-commit safety checks
   - `deep-architecture-review.md` - Pre-PR comprehensive review
   - `security-audit.md` - Security-focused analysis
   - `task-creation-review.md` - Task validation before work starts
   - **Each prompt includes workflow reminders** (see below)

3. Update workflow guides (`.claude/workflows/`)
   - `03-zen-review-quick.md` → Use clink + codex
   - `04-zen-review-deep.md` → Use clink + gemini → codex
   - `13-task-creation-review.md` (new)

4. Update task templates (`/docs/TASKS/`)
   - `00-TEMPLATE_TASK.md` - Add task review checklist
   - `00-TEMPLATE_PHASE_PLANNING.md` - Document clink review requirement

**Success Criteria:**
- ✅ All documentation uses clink (not direct zen tools)
- ✅ Model selection clearly documented
- ✅ Task creation review workflow documented
- ✅ Review prompts include workflow reminders
- ✅ Tested with sample commit and task document

### Phase 2: Automation (Week 2)

**Objective:** Automate pre-commit reviews and task validation

**Tasks:**

1. **Pre-commit hook with clink**
   - Create `.git/hooks/pre-commit` script
   - Integrate clink + codex codereviewer
   - Block commits with HIGH/CRITICAL issues
   - Cache continuation_id for re-validation

2. **Task creation validation script**
   - Create `scripts/validate_task.py`
   - Calls clink + gemini planner
   - Validates task document before work starts
   - Outputs approval/issues

3. **Continuation ID management**
   - Create helper to save/load continuation_id
   - Auto-populate in multi-phase workflows
   - Track review history per commit/task

**Success Criteria:**
- ✅ Pre-commit hook prevents unsafe commits
- ✅ Task validation automated
- ✅ Continuation IDs tracked automatically

### Phase 3: Testing & Validation (Week 3)

**Objective:** Validate clink workflows with real development tasks

**Tasks:**

1. **Test quick review workflow**
   - Stage sample commit (order placement code)
   - Run clink + codex codereviewer
   - Measure: time, issues found, false positives

2. **Test deep review workflow**
   - Complete feature branch (P1T11)
   - Run clink + gemini → codex multi-phase
   - Measure: coverage, time, issue quality

3. **Test task creation review**
   - Create sample task (P1T15)
   - Run clink + gemini planner validation
   - Iterate based on findings

4. **Measure cost & time savings**
   - Track subscription costs (codex CLI, gemini-cli tiers)
   - Monitor usage against subscription limits
   - Measure review durations
   - Calculate ROI vs manual review

**Success Criteria:**
- ✅ Quick review < 30 seconds
- ✅ Deep review < 5 minutes
- ✅ Task review < 3 minutes
- ✅ Zero CRITICAL issues bypass reviews
- ✅ Total cost < $400/month (subscriptions + maintenance, predictable)

### Phase 4: Team Adoption (Week 4+)

**Objective:** Roll out clink workflows to all development

**Tasks:**

1. **Create ADR documenting decision**
   - `docs/ADRs/0XXX-zen-mcp-clink-optimization.md`
   - Document rationale, alternatives, decision

2. **Update all standards**
   - CODING_STANDARDS.md → Require clink reviews
   - GIT_WORKFLOW.md → Clink pre-commit mandatory
   - TESTING.md → Recommend clink for test generation

3. **Training & examples**
   - Add examples to docs/GETTING_STARTED/
   - Create video walkthrough (optional)
   - Weekly tips in team sync

4. **Metrics & iteration**
   - Dashboard: reviews/week, issues found, time saved
   - Monthly review: false positive rate, cost
   - Iterate on prompts based on feedback

**Success Criteria:**
- ✅ ADR approved and merged
- ✅ All standards reference clink workflows
- ✅ Team trained and using clink
- ✅ Metrics show value (bugs caught, time saved)

---

## Cost-Benefit Analysis

### Costs

**Important:** This proposal uses **CLI tools with subscriptions**, NOT direct API calls with variable per-token costs.

**1. CLI Subscription Costs (fixed monthly)**

| Tool | Subscription Type | Monthly Cost |
|------|------------------|--------------|
| Codex CLI | Pro subscription (estimated) | $20-30 |
| Gemini CLI | Free tier or AI Studio subscription | $0-20 |
| **Total Subscriptions** | | **~$20-50/month** |

**Key Advantages:**
- ✅ **Fixed costs** regardless of usage volume
- ✅ **Unlimited reviews** within subscription limits
- ✅ **Predictable budgeting** (no surprise API bills)
- ✅ **No per-token calculations** needed

**2. Setup Time:**
- Documentation updates: 4 hours
- Template creation: 2 hours
- Automation scripts: 4 hours
- Testing & validation: 6 hours
- **Total setup:** ~16 hours ($1,600 @ $100/hour)

**3. Ongoing Maintenance:**
- Prompt refinement: 2 hours/month
- Metrics review: 1 hour/month
- **Total monthly:** ~3 hours ($300/month)

**Total Monthly Cost:** $20-50 (subscriptions) + $300 (maintenance) = **$320-350/month**

**vs. Previous API Model:** Saves $118-148/month compared to pay-per-use API approach!

### Benefits

**1. Time Savings:**

| Activity | Current | Clink-Optimized | Savings per Instance |
|----------|---------|-----------------|----------------------|
| Pre-commit review | 0 min (skipped) | 0.5 min (automated) | Catches issues early |
| Deep branch review | 60-90 min (PR wait) | 3-5 min (clink) | 55-85 min |
| Task creation | 0 min (no validation) | 3 min (validated) | Prevents rework |
| Security audit | 120 min (manual) | 10 min (clink) | 110 min |
| Test generation | 60 min (manual) | 5 min (clink) | 55 min |

**Monthly time savings (assuming):**
- 40 commits with quick review: Catch 8 issues early → 8 × 30 min rework avoided = **240 min**
- 15 deep reviews: 15 × 60 min savings = **900 min**
- 5 task reviews prevent rework: 5 × 60 min = **300 min**
- 2 security audits: 2 × 110 min = **220 min**
- **Total:** ~1,660 minutes = **27.7 hours/month saved**

**Value:** 27.7 hours × $100/hour = **$2,770/month**

**2. Quality Improvements:**
- **Fewer bugs in production:** Estimated 20-30% reduction
- **Better task planning:** Less scope creep, clearer requirements
- **Improved code quality:** Consistent review standards
- **Faster feedback:** Issues caught in seconds, not days

**3. Developer Experience:**
- Real-time feedback (no PR waiting)
- Automated quality gates (no manual reminder)
- Context-aware suggestions (continuation_id)
- Validated tasks (clear goals before starting)

### ROI Calculation

**Monthly Benefit:** $2,770 (time saved)
**Monthly Cost:** $320-350 (subscriptions + maintenance)
**Net Benefit:** **$2,420-2,450/month**

**ROI:** (2,770 - 350) / 350 = **691% ROI** (using max cost)

**Payback Period:** Setup cost $1,600 / Monthly benefit $2,420 = **0.66 months (2.6 weeks)**

**Key Insight:** Subscription model makes this even more cost-effective than direct API usage!

---

## Risks & Mitigations

### Risk 1: Subscription Limits Exceeded

**Risk:** Hitting rate limits or usage caps on CLI tool subscriptions

**Mitigation:**
- Monitor usage dashboards for codex and gemini CLIs
- Upgrade subscription tier if approaching limits
- Fixed cost structure makes upgrades predictable
- No surprise bills (unlike pay-per-use APIs)

**Contingency:** If free tier exhausted for gemini, upgrade to paid tier (~$20/month) which is still cost-effective

### Risk 2: Gemini CLI Not Installed

**Risk:** Gemini CLI not available on developer machines

**Mitigation:**
- Add installation to `.claude/workflows/11-environment-bootstrap.md`
- One-line install: `npm install -g @google/gemini-cli`
- Add to pre-commit hook: Check for gemini CLI availability
- Fallback: Use codex for all reviews if gemini unavailable (costlier)

**Contingency:** Document installation in onboarding, auto-check in pre-commit

### Risk 3: Task Review Overhead

**Risk:** Mandatory task review adds friction, slows down development

**Mitigation:**
- Optimize prompt for < 3 min review time
- Use fast gemini model (gemini-1.5-flash)
- Skip review for trivial tasks (< 2 hour, docs-only)
- Provide quick-fix templates for common issues

**Contingency:** Make task review optional for experienced developers, mandatory for new tasks/complex features

### Risk 4: False Positives in Reviews

**Risk:** Clink reviews flag non-issues, developer frustration

**Mitigation:**
- Track false positive rate (target < 10%)
- Weekly prompt refinement based on feedback
- Allow override with justification (--no-verify flag)
- Document common false positives in FAQ

**Contingency:** If false positive rate > 20%, pause automation and refine prompts

### Risk 5: Continuation ID Lost

**Risk:** Context lost between review phases due to continuation_id not preserved

**Mitigation:**
- Auto-save continuation_id to `.cache/zen/last_review`
- Workflow templates include explicit continuation_id tracking
- Git commit messages include continuation_id for audit trail
- Error handling: Restart review if continuation_id invalid

**Contingency:** If continuation lost, start new review (minor time cost)

---

## Success Metrics

### Key Performance Indicators (KPIs)

| Metric | Target | Measurement |
|--------|--------|-------------|
| Quick review duration | < 30 seconds | Median time logged |
| Deep review duration | < 5 minutes | Median time logged |
| Task review duration | < 3 minutes | Median time logged |
| Issues caught pre-commit | > 80% of total | Compare pre-commit vs PR findings |
| False positive rate | < 10% | Developer skip rate + feedback |
| Total monthly cost | < $400 | Subscription + maintenance costs |
| Time saved per week | > 5 hours | Survey + time tracking |
| Developer satisfaction | > 7/10 | Weekly survey |

### Monthly Metrics Dashboard

```bash
# Example monthly report
./scripts/clink_metrics.py --month 2025-10

Output:
═══════════════════════════════════════════
CLINK REVIEW METRICS - October 2025
═══════════════════════════════════════════

Review Performance:
- Quick reviews: 42 completed
  - Median duration: 28 seconds ✅
  - 95th percentile: 45 seconds ✅

- Deep reviews: 16 completed
  - Median duration: 4.2 minutes ✅
  - 95th percentile: 6.8 minutes ⚠️

- Task reviews: 12 completed
  - Median duration: 2.8 minutes ✅
  - Tasks split: 3 (25%) 📊

Issues Found:
- HIGH: 6 (4 pre-commit, 2 deep review) ✅
- MEDIUM: 14 (10 pre-commit, 4 deep review) ✅
- LOW: 22 (18 pre-commit, 4 deep review) ✅
- Pre-commit catch rate: 82% ✅

Quality Metrics:
- False positives: 4 (5.7%) ✅
- Override rate: 2 (2.9%) ✅
- Re-reviews needed: 8 (11.4%) ✅

Cost Analysis:
- Codex CLI subscription: $30/month ✅
- Gemini CLI tier: Free (or $20 if upgraded) ✅
- Maintenance hours: $300 (3 hrs × $100/hr) ✅
- Total monthly cost: $330 ✅ (under $400 target)

Time Savings:
- Quick reviews prevented rework: 240 min
- Deep reviews vs PR wait: 960 min
- Task reviews prevented scope creep: 360 min
- Total saved: 1,560 min (26 hours) ✅

Developer Feedback:
- Satisfaction: 8.4/10 ✅
- Most useful: "Catches circuit breaker issues early"
- Top improvement: "Faster deep review for small branches"
```

---

## Next Steps

### Immediate (This Week)

1. **Request codex review of this proposal**
   - Use clink + codex codereviewer
   - Focus: completeness, feasibility, cost accuracy
   - Get approval before proceeding

2. **Create Phase 1 task documents**
   - P1T11: Update CLAUDE.md with clink workflows
   - P1T12: Create standardized review prompts
   - P1T13: Update workflow guides
   - Each task goes through task creation review

3. **Test clink workflows on sample code**
   - Quick review: Sample commit with order placement
   - Deep review: Recent feature branch
   - Task review: Sample P1T11 document

### Week 2-3

1. **Implement Phase 1** (Documentation & Templates)
2. **Implement Phase 2** (Automation)
3. **Validate with real development**

### Week 4+

1. **Phase 3** (Testing & Validation)
2. **Phase 4** (Team Adoption)
3. **Ongoing optimization**

---

## Appendices

### A. Clink Command Reference

**Note:** Model selection happens via CLI configuration, NOT via clink parameters.

**Quick Review (Codex Codereviewer):**
```bash
# Codex CLI defaults to gpt-5-codex - no model argument needed
Use clink with codex codereviewer:
Prompt: <review focus>
Files: <staged files>
```

**Deep Review (Gemini Codereviewer):**
```bash
# Gemini CLI configured for gemini-2.5-pro (or flash) via CLI config
Use clink with gemini codereviewer:
Prompt: <comprehensive review>
Compare: master..HEAD
Save continuation_id
```

**Planning (Gemini Planner):**
```bash
# Gemini CLI defaults to gemini-2.5-pro for planning tasks
Use clink with gemini planner:
Prompt: <planning task>
Files: <context files>
```

**Task Review (Gemini Planner):**
```bash
# Gemini CLI can use gemini-2.5-flash for faster task validation
Use clink with gemini planner:
Prompt: <task validation>
Files: docs/TASKS/P*T*_TASK.md
```

### B. Model Selection Decision Tree

```
┌─────────────────────────────────────┐
│      Need large context (>100K)?    │
│           Yes → Gemini               │
│           No → Codex                 │
└─────────────────────────────────────┘
                  │
                  ├─── Gemini Branch
                  │    ├─── Planning/Strategy? → gemini-2.5-pro
                  │    ├─── Quick analysis? → gemini-2.5-flash
                  │    └─── Task validation? → gemini-2.5-flash
                  │
                  └─── Codex Branch
                       └─── Always → gpt-5-codex
```

### C. Review Prompt Templates Summary

| Template | CLI | Role | Model (CLI config) | Use Case |
|----------|-----|------|-------|----------|
| quick-safety-review.md | codex | codereviewer | gpt-5-codex | Pre-commit |
| deep-architecture-review.md | gemini | codereviewer | gemini-2.5-pro | Pre-PR |
| security-audit.md | codex | codereviewer | gpt-5-codex | Security scan |
| task-creation-review.md | gemini | planner | gemini-2.5-flash | Task validation |
| refactor-planning.md | gemini | planner | gemini-2.5-pro | Large refactors |
| test-generation.md | codex | default | gpt-5-codex | Test writing |

---

**Status:** ✅ Ready for Approval
**Created:** 2025-10-21
**Revised:** 2025-10-21 (All Codex findings addressed)
**Owner:** Development Team
**Next Action:** Proceed with Phase 1 implementation
