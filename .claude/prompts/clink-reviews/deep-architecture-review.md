# Deep Architecture Review (Pre-PR)

**Tool:** clink + gemini codereviewer → codex codereviewer (multi-phase)
**Duration:** 3-5 minutes
**When:** Before creating PR (Tier 2 review)

---

## Review Prompt (Phase 1: Gemini Codereviewer)

Please perform a comprehensive architecture review of all changes in this branch for the trading platform.

**Focus Areas:**

1. **Architecture & Design:**
   - Microservice boundaries clean?
   - Data flows correct (Redis streams, Postgres persistence)?
   - Event-driven patterns appropriate?
   - Reconciliation logic sound?
   - ADR required for architectural changes?

2. **Trading Safety (Comprehensive):**
   - Circuit breaker integration complete?
   - Idempotency preserved across all order paths?
   - Position limit enforcement consistent?
   - Risk checks comprehensive?
   - Reconciliation handles edge cases?
   - Kill-switch behavior correct?

3. **Scalability & Performance:**
   - Database queries efficient (no N+1)?
   - Redis usage appropriate (ephemeral data)?
   - Async patterns correct (FastAPI + httpx)?
   - Backpressure handling?
   - Memory leaks possible?

4. **Maintainability:**
   - Feature parity maintained (research vs production)?
   - Code duplication avoided?
   - Test coverage comprehensive (unit, integration, E2E)?
   - Documentation adequate?
   - Error messages actionable?

5. **Security:**
   - SQL injection prevented (parameterized queries)?
   - API keys/secrets not hardcoded?
   - Input validation present?
   - Authentication/authorization correct?

**Output Format:**

```
**Critical Findings**
- [Issue]: [Description with file:line references]
  [Architectural impact and trading risk]

**High Priority**
- [Issue]: [Description with file:line references]
  [Impact and recommendation]

**Medium Priority**
- [Issue]: [Description]

**Low Priority**
- [Issue]: [Description]

**Positives**
- [Strengths in design, safety, or implementation]

**Architecture Assessment**
[Overall evaluation of design decisions, scalability, maintainability]

<SUMMARY>[Comprehensive verdict with key recommendations]</SUMMARY>
```

**Save continuation_id for Phase 2!**

---

## Phase 2 Prompt (Codex Codereviewer (model: gpt-5-codex) - Reuse continuation_id)

Given the architecture review findings above, please synthesize:

1. **Priority Recommendations:**
   - What MUST be fixed before merge?
   - What should be follow-up tasks?
   - Any architectural debt to track?

2. **Testing Strategy:**
   - Are current tests sufficient?
   - What additional test scenarios needed?
   - E2E scenarios covered?

3. **Documentation Needs:**
   - ADR required?
   - README updates needed?
   - API documentation current?

4. **Next Steps:**
   - Ordered action items for developer
   - Estimated effort for fixes
   - Follow-up task creation needed?

**Output format: Actionable plan with clear priorities**

---

## 🔔 Workflow Reminder

**After addressing findings, remind the developer:**

1. **Deep Review Complete:**
   - ✅ Architecture analysis done (gemini)
   - ✅ Recommendations synthesized (codex)
   - ❌ DO NOT merge yet - address findings first

2. **Fix Priority:**
   - **CRITICAL:** Block merge, fix immediately
   - **HIGH:** Must fix before merge
   - **MEDIUM:** Fix before merge or create follow-up task
   - **LOW:** Create follow-up task

3. **After Fixes:**
   - Re-request deep review with continuation_id
   - Verify all CRITICAL/HIGH addressed
   - Verify no other issue exist
   - Only proceed to PR after explicit approval

4. **PR Creation:**
   - (MANDATORY) STRICTLY Follow `.claude/workflows/02-git-pr.md`
   - Include continuation_id in PR description
   - Link to deep review findings
   - Note any follow-up tasks created

5. **Progressive Workflow Maintained:**
   - All commits followed 4-step pattern ✓
   - Each component reviewed individually ✓
   - Deep review before PR ✓
   - Follow-up tasks tracked ✓

**Do NOT skip fixes or create PR prematurely!**

---

## Trading Platform Context

**Architecture Overview:**
- Microservices: signal_service, execution_gateway, market_data_service, orchestrator
- Future services: reconciler, risk_manager, cli (planned)
- Communication: Redis Streams/pub-sub + Postgres persistence
- Safety: Circuit breakers in Redis, position limits enforced
- Parity: Research and production share feature code

**Critical Patterns:**
- **Idempotency:** `client_order_id = hash(f"{symbol}|{side}|{qty}|{price}|{strategy}|{date}")[:24]`
- **Circuit Breaker:** Check Redis state before EVERY order
- **Reconciliation:** Boot-time + periodic, heal broker vs DB discrepancies
- **Feature Parity:** `strategies/*/features.py` shared by research and production

**Never approve:**
- Architectural changes without ADR
- Broken circuit breaker integration
- Lost idempotency guarantees
- Research/production feature divergence
- Missing reconciliation for state changes
- Untested critical paths (order submission, position tracking)

**Success criteria:**
- All order paths idempotent
- Circuit breakers never bypassable
- Backtest replay produces same signals as paper trading
- Reconciliation heals all discrepancies
- Test coverage >80% for critical paths
