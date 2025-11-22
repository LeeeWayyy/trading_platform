# Development Workflows (Testing, Debugging, Documentation, ADRs)

**Purpose:** Complete development cycle - testing, debugging, writing docs, and architectural decisions
**When:** Throughout development lifecycle
**Tools:** pytest, mypy, ruff, grep, markdown editors

---

## Quick Reference

| Workflow | When | Duration | See Section |
|----------|------|----------|-------------|
| **Testing** | After implementation, before review | 5-15 min | §1 |
| **Debugging** | When tests fail or bugs found | Varies | §2 |
| **Documentation** | During/after implementation | 10-20 min | §3 |
| **ADR Creation** | Before architectural changes | 20-30 min | §4 |

---

## §1: Testing Workflow

**MANDATORY before zen-mcp review**

### Test Pyramid

| Level | Scope | Speed | Coverage Target |
|-------|-------|-------|-----------------|
| Unit | Pure functions, features | Fast (<1s) | 80%+ |
| Integration | API endpoints, DB workflows | Medium (1-5s) | Key paths |
| E2E | Full paper run, backtest | Slow (>30s) | Critical flows |

### Running Tests

```bash
# Quick unit tests (run before review)
pytest -m "not integration and not e2e" --maxfail=3

# Full local CI (matches GitHub Actions)
make ci-local

# Specific test file/function
pytest tests/libs/allocation/test_multi_alpha.py::TestRankAggregation

# With coverage
pytest --cov=libs --cov=apps --cov-report=term --cov-fail-under=80

# Watch mode (TDD)
pytest-watch tests/libs/feature_store/
```

### Test Markers

```python
@pytest.mark.unit          # Fast, no external deps
@pytest.mark.integration   # DB, Redis, HTTP
@pytest.mark.e2e           # Full system test
@pytest.mark.slow          # >5 seconds
```

### Writing Tests (TDD)

```python
def test_position_limit_validation():
    """Test that position limits are enforced correctly."""
    # Arrange
    allocator = Allocator(max_position=100)

    # Act
    result = allocator.allocate(symbol="AAPL", weight=0.15)

    # Assert
    assert result.qty <= 100
    assert result.reason == "capped_at_limit"
```

**Coverage requirements:** 80% for `libs/` and `apps/`, key paths for integration

---

## §2: Debugging Workflow

### When Tests Fail

```bash
# 1. Run failing test with verbose output
pytest tests/path/to/test.py::test_name -vv

# 2. Add print debugging (temporary)
import pdb; pdb.set_trace()  # Breakpoint

# 3. Check logs
tail -f logs/app.log | grep ERROR

# 4. Verify assumptions
pytest tests/path/to/test.py::test_name -vv --capture=no
```

### Common Failure Patterns

**Import errors:**
```bash
# Check PYTHONPATH
echo $PYTHONPATH

# Run from project root
cd /path/to/trading_platform
pytest tests/...
```

**Async issues:**
```python
# Missing @pytest.mark.asyncio
@pytest.mark.asyncio
async def test_async_function():
    result = await some_async_call()
```

**Mock issues:**
```python
# Wrong patch path - patch where it's USED, not defined
@patch('apps.orchestrator.main.TradingOrchestrator')  # ✓ Correct
@patch('apps.orchestrator.orchestrator.TradingOrchestrator')  # ✗ Wrong
```

### Debug Rescue System (Component 5)

**Auto-detection of debug loops:**
```bash
# Check if stuck in debug loop
./scripts/workflow_gate.py check-debug-loop

# Request systematic debugging help
./scripts/workflow_gate.py request-debug-rescue --test tests/path/to/test.py
```

**Triggers rescue when:**
- Same test fails 3+ times
- Error signature cycling (A → B → A pattern)
- >30 minutes spent without progress

**Rescue provides:**
- Systematic debugging steps via zen-mcp codex
- Recent commit analysis
- Common root causes for error patterns

---

## §3: Documentation Workflow

**When to document:** During implementation (not after!)

### Docstring Standards

**Module docstrings:**
```python
"""Position limit validation for trading system.

This module enforces per-symbol and total notional limits to prevent
over-concentration and comply with risk management policies.

Key concepts:
- Position limit: Max shares per symbol (e.g., 1000 shares of AAPL)
- Notional limit: Max dollar exposure (e.g., $100k total)
- Gross exposure: Sum of abs(position_value) across all symbols

See: /docs/CONCEPTS/position_limits.md for detailed explanation
"""
```

**Function docstrings:**
```python
def check_position_limits(
    symbol: str,
    proposed_qty: int,
    current_positions: dict[str, Position],
    limits: PositionLimits
) -> tuple[bool, str]:
    """Check if proposed order would violate position limits.

    Args:
        symbol: Ticker symbol (e.g., "AAPL")
        proposed_qty: Shares to trade (positive = buy, negative = sell)
        current_positions: Current positions by symbol
        limits: Position limit configuration

    Returns:
        (allowed, reason) where allowed is True if within limits,
        and reason explains why if rejected

    Raises:
        ValueError: If proposed_qty is zero

    Example:
        >>> limits = PositionLimits(max_position_per_symbol=1000)
        >>> allowed, reason = check_position_limits("AAPL", 500, {}, limits)
        >>> assert allowed is True
    """
```

### Concept Documentation

**Create `/docs/CONCEPTS/{topic}.md` for trading concepts:**

```markdown
# Position Limits

**What:** Maximum number of shares we can hold per symbol

**Why:** Prevents over-concentration in single stocks (risk management)

**How:** Before every order, check:
1. Current position + proposed order <= max_position_per_symbol
2. Total notional exposure <= max_total_exposure

**Example:**
- Limit: 1000 shares per symbol
- Current AAPL position: 800 shares
- Proposed order: +300 shares
- Result: REJECTED (would be 1100 shares, exceeds limit)

**Code:** `libs/allocation/limits.py:check_position_limits()`
```

### README Updates

**Update README when adding:**
- New commands (`make xyz`)
- New environment variables
- New services or dependencies
- Breaking changes to setup

---

## §4: ADR Creation Workflow

**MANDATORY for architectural changes**

### When to Create ADR

**✅ Required for:**
- New microservices or modules
- Schema changes (DB migrations)
- API contract changes (breaking)
- New infrastructure (Redis, queues)
- External service integrations
- Authentication/authorization changes
- Circuit breaker policy changes

**❌ Not required for:**
- Bug fixes (unless design change)
- Refactoring (same interface)
- Test additions
- Documentation updates

### ADR Template

```markdown
# ADR-XXXX: Title in Imperative Mood

**Status:** Proposed | Accepted | Deprecated | Superseded by ADR-YYYY

**Context:**
What problem are we solving? Why now?

**Decision:**
What are we doing? (1-2 sentences)

**Alternatives Considered:**
1. Option A: Pros/cons
2. Option B: Pros/cons
3. Chosen Option C: Why this wins

**Consequences:**
- **Positive:** What improves
- **Negative:** What trade-offs
- **Neutral:** What changes

**Implementation Notes:**
- Migration path (if breaking change)
- Rollback plan
- Timeline

**References:**
- Related ADRs
- External docs
- Prior art
```

### ADR Process

```bash
# 1. Create ADR
cp docs/ADRs/0000-template.md docs/ADRs/0042-new-decision.md

# 2. Fill in template
vim docs/ADRs/0042-new-decision.md

# 3. Request review (zen-mcp)
"Review my ADR for architectural soundness and completeness"

# 4. Update ADR index
# Add to docs/ADRs/README.md

# 5. Commit ADR
git add docs/ADRs/0042-new-decision.md docs/ADRs/README.md
git commit -m "docs: Add ADR-0042 for circuit breaker policy"
```

### ADR Examples

**Good ADR titles:**
- "Use Redis for circuit breaker state"
- "Separate read/write DB connections for scalability"
- "Add idempotency keys to all order submissions"

**Bad ADR titles:**
- "Database stuff" (vague)
- "Fix the bug" (not architectural)
- "Update code" (not a decision)

---

## Common Scenarios

### Scenario: Test Failures After Implementation

```bash
# 1. Run tests
pytest -m "not integration"

# 2. Fix failures one by one
pytest tests/specific/test.py::test_func -vv

# 3. If stuck >30 min, request debug rescue
./scripts/workflow_gate.py request-debug-rescue

# 4. Re-run full suite
make ci-local
```

### Scenario: Adding New Feature

```bash
# 1. Check if architectural change
# If yes: Create ADR first (§4)

# 2. Write tests (TDD)
vim tests/libs/new_feature/test_feature.py

# 3. Implement
vim libs/new_feature/feature.py

# 4. Document (inline + concept doc)
vim docs/CONCEPTS/new_feature.md

# 5. Run tests
pytest tests/libs/new_feature/

# 6. Update README if needed
vim README.md
```

### Scenario: Debugging Production Issue

```bash
# 1. Reproduce locally
DRY_RUN=true python -m apps.signal_service.main

# 2. Check logs
tail -f logs/signal_service.log | grep ERROR

# 3. Add debug logging (temporary)
logger.info(f"DEBUG: variable={variable}")

# 4. Write regression test
vim tests/apps/signal_service/test_regression_issue_123.py

# 5. Fix + verify test passes
pytest tests/apps/signal_service/test_regression_issue_123.py
```

---

## Validation Checklists

**Testing complete:**
- [ ] All tests passing (`make ci-local`)
- [ ] Coverage ≥80% for new code
- [ ] Integration tests for API endpoints
- [ ] Edge cases covered

**Documentation complete:**
- [ ] Docstrings for public functions
- [ ] Concept doc if new trading concept
- [ ] README updated if new commands
- [ ] Code comments for complex logic

**ADR complete (if architectural change):**
- [ ] ADR created from template
- [ ] Alternatives considered section filled
- [ ] Consequences documented
- [ ] ADR index updated
- [ ] Review requested and approved

---

## See Also

- [12-component-cycle.md](./12-component-cycle.md) - 4-step pattern (includes testing)
- [03-reviews.md](./03-reviews.md) - Zen-mcp review workflow
- [01-git.md](./01-git.md) - Progressive commits
- [/docs/STANDARDS/TESTING.md](../STANDARDS/TESTING.md) - Test requirements
- [/docs/STANDARDS/DOCUMENTATION_STANDARDS.md](../STANDARDS/DOCUMENTATION_STANDARDS.md) - Docstring standards
- [/docs/STANDARDS/CODING_STANDARDS.md](../STANDARDS/CODING_STANDARDS.md) - Code patterns
