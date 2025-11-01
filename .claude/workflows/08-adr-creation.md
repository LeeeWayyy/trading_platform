# ADR Creation Workflow

**Purpose:** Document architectural decisions using Architecture Decision Records
**When:** Before ANY architectural change
**Prerequisites:** Architectural change proposed or needed
**Expected Outcome:** ADR created, reviewed, and committed BEFORE implementation

---

## Quick Reference

**Standards:** See [/docs/STANDARDS/ADR_GUIDE.md](../../docs/STANDARDS/ADR_GUIDE.md)
**Template:** See [/docs/ADRs/0000-template.md](../../docs/ADRs/0000-template.md)

---

## When to Create ADR

**MANDATORY for:**
- Adding/removing/modifying services or components
- Changing API contracts or database schemas
- Adding third-party dependencies
- Changing deployment architecture
- Modifying data flow patterns
- Changing security mechanisms
- Altering error handling strategies

**Rule of thumb:** If unsure, create an ADR.

---

## Step-by-Step Process

### 1. Identify Next ADR Number

```bash
ls docs/ADRs/ | grep -E '^[0-9]' | sort -n | tail -1
# If last is 0012, create 0013
```

### 2. Copy Template

```bash
cp docs/ADRs/0000-template.md docs/ADRs/0013-descriptive-title.md
```

### 3. Fill Out Required Sections

**A. Status**
```markdown
## Status
Proposed (2025-10-21)
```

**B. Context**
- What problem are you solving?
- Why is this decision needed now?
- What constraints exist?

**C. Decision**
- State what you decided (be specific)
- Include implementation details

**D. Consequences**
- Positive: Benefits and capabilities
- Negative: Downsides and complexity
- Risks: What could go wrong

**E. Alternatives Considered (Minimum 2)**
- Describe each alternative
- Explain why not chosen

**F. Implementation Notes**
- Key steps
- Migration path
- Testing approach
- Rollback plan

### 4. Self-Review Checklist

- [ ] Context clearly explains problem
- [ ] Decision is specific and actionable
- [ ] At least 2 alternatives considered
- [ ] Consequences include pros AND cons
- [ ] Implementation notes are concrete
- [ ] All required sections completed

### 5. Update Status to Accepted

```markdown
## Status
Accepted (2025-10-21)
```

### 6. Commit ADR BEFORE Implementation

```bash
# Commit ADR first
git add docs/ADRs/0013-new-decision.md
git commit -m "ADR-0013: Use Redis for circuit breaker state"

# THEN implement, referencing ADR
git commit -m "Implement circuit breaker in Redis (ADR-0013)"
```

### 7. Update Related Documentation

**Complete this checklist after creating ADR:**

- [ ] **README.md updated** (if new component/service/capability)
- [ ] **CONCEPTS/ documentation created** (if new architectural concept)
- [ ] **Workflow updates** (if ADR changes development process)
- [ ] **API documentation** (if ADR affects API contracts)
- [ ] **Database documentation** (if ADR affects schema)
- [ ] **LESSONS_LEARNED/ retrospective planned** (post-implementation review)
- [ ] **Related ADRs linked** (cross-references updated)

**Time estimate:** 15-30 minutes for documentation updates

### 8. Reference ADR in Code

```python
def check_circuit_breaker():
    """
    Check circuit breaker state in Redis.

    See ADR-0013 for design rationale.
    """
```

---

## Decision Points

### Should I Create an ADR?

**Create ADR when:**
- Affects >1 file/module
- Adds dependencies
- Changes API contracts
- Affects multiple teams

**Skip (use comments) when:**
- Simple bug fixes
- Refactoring with no API changes
- Configuration tweaks
- Documentation updates
- Test additions

### Before or After Implementation?

**ALWAYS before implementation:**
- Forces clear thinking
- Enables team review
- Documents rationale while fresh
- Prevents "lock-in" to hasty choices

**Exception:** POC/spike work (but document findings after)

### How Many Alternatives?

**Minimum: 2 alternatives** (not counting chosen approach)

Good rule:
- 2-3 for most decisions
- 4-5 for critical architectural choices
- Include "do nothing" if relevant

---

## ADR Template Example

```markdown
# ADR-0013: Use Redis for Circuit Breaker State

## Status
Accepted (2025-10-21)

## Context
We need distributed circuit breaker state shared across instances:
- Multiple execution gateway instances
- State must be consistent
- Sub-second latency required
- Atomic state transitions

Current: No circuit breaker implementation

## Decision
Use Redis for circuit breaker state storage.

**Implementation:**
- Single key: `cb:state` (values: `OPEN` or `TRIPPED`)
- Use Redis WATCH/MULTI/EXEC for atomicity
- TTL on TRIPPED state for auto-recovery
- Fallback to TRIPPED if Redis unavailable

## Consequences

**Positive:**
- Atomic operations prevent races
- Sub-millisecond latency
- Automatic expiry for recovery
- Shared across instances

**Negative:**
- Additional dependency (Redis)
- Single point of failure (mitigated by fallback)
- State lost on restart (acceptable for circuit breaker)

**Risks:**
- Redis unavailable = all trading stops (mitigated by safe-mode fallback)

## Alternatives Considered

### PostgreSQL
- **Pros:** Already using, ACID guarantees
- **Cons:** Too slow (10-50ms vs <1ms)
- **Why not:** Latency unacceptable

### In-memory (process local)
- **Pros:** Fastest, no dependency
- **Cons:** Not shared across instances
- **Why not:** Need distributed state

### Consul/etcd
- **Pros:** Designed for distributed config
- **Cons:** Overkill, ops burden
- **Why not:** Redis simpler

## Implementation Notes

```bash
# Add to docker-compose.yml
redis:
  image: redis:7-alpine
  ports:
    - "6379:6379"
```

```python
import redis
r = redis.Redis(host='localhost', port=6379)

def is_tripped() -> bool:
    return r.get("cb:state") == b"TRIPPED"

def trip():
    r.setex("cb:state", 3600, "TRIPPED")  # 1hr TTL
```

### Testing
- Unit tests with fakeredis
- Integration tests with real Redis
- Failure tests (Redis down)

## Related ADRs
- ADR-0009: Redis Integration (general)
```

---

## Common Issues

### Can't Think of Alternatives

**Solution:**
1. Time-box 30 min research
2. Common alternatives:
   - Build vs buy
   - Sync vs async
   - Database choices (SQL vs NoSQL)
   - Cloud vs self-hosted
   - Monolith vs microservice
3. Include "do nothing"
4. Ask team for input

### Too Vague or Too Detailed

**Right level:**
- ✅ "Use PostgreSQL 16 with JSONB columns"
- ✅ "Use Redis with TTL for circuit breaker state"
- ❌ Too vague: "Use a database"
- ❌ Too detailed: Full implementation code

**Include:**
- Technology choice + version
- Key configuration decisions
- Integration points
- Migration approach
- Short code examples

**Exclude:**
- Full implementation
- Line-by-line code walkthrough
- Detailed test cases

---

## Validation

**How to verify ADR is complete:**
- [ ] All required sections filled
- [ ] At least 2 alternatives documented
- [ ] Consequences include pros AND cons
- [ ] Decision is specific and actionable
- [ ] Implementation notes include code examples
- [ ] Status is "Accepted" before implementation
- [ ] ADR committed BEFORE implementation
- [ ] Documentation update checklist completed

---

## Related Workflows

- [01-git-commit.md](./01-git-commit.md) - Commit ADR before implementation
- [07-documentation.md](./07-documentation.md) - Reference ADR in code docs

---

## References

- [/docs/STANDARDS/ADR_GUIDE.md](../../docs/STANDARDS/ADR_GUIDE.md) - Complete ADR guide
- [/docs/ADRs/0000-template.md](../../docs/ADRs/0000-template.md) - ADR template
- ADR concept: https://adr.github.io/
