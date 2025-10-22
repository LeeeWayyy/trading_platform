# Security Audit

**Tool:** clink + codex codereviewer
**Duration:** 2-4 minutes
**When:** Before merging security-sensitive changes or periodic audits

---

## Review Prompt

Please perform a comprehensive security audit of the changes for this trading platform.

**Focus Areas:**

1. **OWASP Top 10:**
   - **Injection:** SQL queries parameterized? User input sanitized?
   - **Broken Auth:** API key handling secure? Session management correct?
   - **Sensitive Data Exposure:** Secrets in code? Logs leaking data?
   - **XML External Entities:** (Less relevant, but check parsers)
   - **Broken Access Control:** Authorization checks present?
   - **Security Misconfiguration:** Defaults secure? Debug mode off?
   - **XSS:** (Less relevant for API-only service, but check)
   - **Insecure Deserialization:** Pickle/eval usage?
   - **Components with Known Vulnerabilities:** Dependencies current?
   - **Insufficient Logging:** Security events logged?

2. **Trading Platform Specific:**
   - **API Keys:** Alpaca keys never hardcoded?
   - **Order Manipulation:** Input validation prevents malicious orders?
   - **Position Limits:** Cannot be bypassed?
   - **Circuit Breakers:** Cannot be disabled via API?
   - **Race Conditions:** Concurrent order submission safe?
   - **Replay Attacks:** client_order_id prevents order replay?
   - **Data Integrity:** Database constraints prevent invalid states?

3. **Infrastructure Security:**
   - **Redis Security:** Authentication enabled? Public exposure?
   - **Postgres Security:** Credentials management? Connection encryption?
   - **API Endpoints:** Rate limiting? Input validation?
   - **Webhooks:** Signature verification for Alpaca webhooks?
   - **Environment Variables:** Secrets in .env, not code?

4. **Code Security:**
   - **Dynamic Execution:** No eval(), exec(), pickle?
   - **File Operations:** Path traversal prevented?
   - **Cryptography:** Secure random for sensitive operations?
   - **Dependencies:** requirements.txt pinned versions?
   - **Error Messages:** Don't leak internal details?

**Output Format:**

```
**Critical Security Issues**
- [CVE/CWE if applicable] [Issue]: [Description with file:line]
  [Attack vector and impact]
  [Recommendation]

**High Priority**
- [Issue]: [Description with file:line]
  [Security risk]
  [Recommendation]

**Medium Priority**
- [Issue]: [Description]
  [Recommendation]

**Low Priority**
- [Issue]: [Description]
  [Recommendation]

**Security Positives**
- [Good security practices observed]

**Overall Security Posture**
[Assessment of security maturity and remaining gaps]

<SUMMARY>[Security verdict: approve, conditional, or block]</SUMMARY>
```

**Severity Levels:**
- **CRITICAL:** Immediate security risk (RCE, SQL injection, exposed secrets)
- **HIGH:** Exploitable vulnerability (broken auth, missing input validation)
- **MEDIUM:** Security weakness (poor error handling, missing rate limits)
- **LOW:** Security improvement (better logging, dependency updates)

---

## 🔔 Workflow Reminder

**After addressing security findings:**

1. **Security Fixes Priority:**
   - **CRITICAL:** Fix immediately, do NOT merge
   - **HIGH:** Must fix before merge
   - **MEDIUM:** Fix or create follow-up security task
   - **LOW:** Track in security backlog

2. **Verification:**
   - Re-request security audit with continuation_id
   - All CRITICAL/HIGH must be resolved
   - Test security fixes don't break functionality

3. **Documentation:**
   - If fix changes security model, update docs
   - If new threat discovered, document mitigation
   - Update security runbooks if needed

4. **After Security Approval:**
   - Continue with standard workflow (commit/PR)
   - Note security review in commit message
   - Include continuation_id

**Security is never optional - do NOT bypass security review!**

---

## Trading Platform Security Context

**Critical Security Requirements:**

1. **API Keys Protection:**
   - Alpaca API keys in environment variables only
   - Never logged, never in responses
   - Separate keys for paper vs live trading

2. **Order Integrity:**
   - All order parameters validated
   - Position limits enforced (cannot bypass)
   - Circuit breakers cannot be disabled via API
   - client_order_id prevents replay attacks

3. **Data Integrity:**
   - Database constraints prevent invalid positions
   - Reconciliation detects and heals tampering
   - Audit trail for all order operations
   - Timestamps UTC, timezone-aware

4. **Input Validation:**
   - Pydantic models validate all inputs
   - SQL queries parameterized (no string concatenation)
   - API endpoints validate request bodies
   - Webhooks verify Alpaca signatures

5. **Access Control:**
   - Kill-switch requires authentication
   - Circuit breaker state protected
   - Admin operations logged
   - No unauthenticated order submission

**Never approve:**
- Hardcoded API keys or secrets
- SQL string concatenation
- Missing input validation on order endpoints
- Circuit breaker bypass mechanisms
- Unauthenticated admin operations
- Exposed Redis/Postgres to public internet
- eval(), exec(), or pickle usage
- Sensitive data in logs (API keys, account details)

**Common Trading Platform Vulnerabilities:**
- **Front-running:** Order info leaked before submission
- **Order replay:** Missing nonce/idempotency
- **Race conditions:** Concurrent position updates
- **Position manipulation:** Bypassing limits via race
- **Data leakage:** Logs containing sensitive trading data
