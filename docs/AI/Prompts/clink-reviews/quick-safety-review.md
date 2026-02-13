# Quick Safety Review (Pre-Commit)

**Tool:** clink + codex codereviewer
**Duration:** ~30 seconds
**When:** Before EVERY commit (Tier 1 review)

---

## Review Prompt

Please perform a quick safety review of the staged changes for this trading platform commit.

**Focus Areas:**

1. **Trading Safety:**
   - Circuit breaker checks present before orders?
   - Idempotency: client_order_id prevents duplicates?
   - Position limits enforced?
   - Risk checks present?
   - No silent failures (proper logging + exceptions)?

2. **Code Quality:**
   - Type hints present?
   - Pydantic models for configs/data?
   - SQL queries parameterized (no injection)?
   - UTC timezone-aware timestamps?
   - Test coverage adequate?

3. **Anti-Patterns:**
   - Duplicate feature logic (research vs production)?
   - In-memory state for critical data?
   - Missing error handling?
   - Hardcoded configs (not in Settings)?

**Output Format:**

```
**Findings**
- [Severity] – [Issue]: [Description with file:line reference]
  [Explanation of risk and trading impact]

**Positives**
- [What was done well]

<SUMMARY>[One-sentence verdict: approve, conditional, or block]</SUMMARY>
```

**Severity Levels:**
- **CRITICAL:** Blocks commit (data loss, duplicate orders, circuit breaker bypass)
- **HIGH:** Must fix before commit (safety violation, missing idempotency)
- **MEDIUM:** Should fix soon (test gaps, unclear error handling)
- **LOW:** Consider for follow-up (style, minor improvements)

---

## 🔔 Workflow Reminder

**After addressing findings, remind the developer to follow the 6-step pattern:**

1. **6-Step Pattern (MANDATORY):**
   - ✅ Plan approach
   - ✅ Request plan review
   - ✅ Implement logic
   - ✅ Create test cases (TDD)
   - ✅ Request code review (you are here!)
   - ❌ Commit changes (NOT YET - wait for approval)

2. **Progressive Commits:**
   - Commit every 30-60 minutes per logical component
   - Never combine multiple components in one commit
   - Each commit requires quick review approval

3. **After This Review:**
   - Fix CRITICAL/HIGH issues immediately
   - Re-request verification with continuation_id
   - Only commit when explicitly approved
   - Include continuation_id in commit message

4. **Before PR:**
   - Deep review MANDATORY (use clink + gemini codereviewer)
   - See `.claude/workflows/03-reviews.md`

**Do NOT skip these steps after completing fixes!**

---

## Trading Platform Context

This is a **Qlib + Alpaca trading platform** with these critical requirements:

- **Idempotency first:** Every order path must be retry-safe
- **Circuit breakers:** MANDATORY check before every order
- **Feature parity:** Research and production must share feature code
- **No duplicate orders:** client_order_id prevents double submission
- **Position limits:** Enforce per-symbol and total notional limits
- **Test coverage:** Backtest replay must match paper trading

**Never approve:**
- Orders without circuit breaker checks
- Missing idempotency (client_order_id)
- Duplicate feature logic across research/production
- Untested order paths
- Silent failures (missing logs/exceptions)
