# ADR Creation Workflow

**Purpose:** Document architectural decisions using Architecture Decision Records
**Prerequisites:** Architectural change proposed or needed
**Expected Outcome:** ADR created, reviewed, and committed before implementation
**Owner:** @architecture-team + @development-team
**Last Reviewed:** 2025-10-21

---

## When to Use This Workflow

**Create ADR for ANY of these changes:**
- Adding/removing/modifying services
- Changing API contracts
- Modifying database schema
- Adding third-party dependencies
- Changing deployment architecture
- Modifying data flow patterns
- Changing security mechanisms
- Altering error handling strategies

**Rule of thumb:** If unsure, create an ADR. Better too much documentation than too little.

---

## Step-by-Step Process

### 1. Identify Next ADR Number

```bash
# List existing ADRs
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
- What is current state?

**C. Decision**
- State what you decided
- Be specific and concrete
- Include implementation details

**D. Consequences**
- Positive: Benefits and capabilities
- Negative: Downsides and complexity
- Risks: What could go wrong

**E. Alternatives Considered**
- Describe each alternative
- Explain why not chosen

**F. Implementation Notes**
- Key steps
- Migration path
- Testing approach
- Rollback plan

### 4. Review ADR

**Self-review checklist:**
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

### 7. Complete ADR Documentation Update Checklist

**MANDATORY: After creating ADR, update all related documentation to maintain project coherence.**

**Review and complete this checklist:**

```markdown
## ADR Documentation Update Checklist

### Core ADR
- [ ] ADR created in `docs/ADRs/NNNN-title.md` with all required sections
- [ ] ADR status updated to "Accepted" before implementation
- [ ] ADR committed to git BEFORE implementation code

### Project Documentation Updates
- [ ] **README.md updated** (if introducing new component/service/capability)
  - Add to "Key Achievements" or relevant feature section
  - Update architecture overview if structural change
  - Add links to new documentation

- [ ] **CONCEPTS/ documentation created** (if introducing new architectural concept)
  - Create `docs/CONCEPTS/concept-name.md` explaining:
    - What problem does this solve?
    - How does it work? (with examples)
    - Why did we choose this approach?
    - Common patterns and best practices
  - Link from README.md "Concept Documentation" section

- [ ] **Workflow updates** (if ADR changes development process)
  - Update affected workflows in `.claude/workflows/`
  - Add new workflow if introducing new process
  - Update workflow index in `.claude/workflows/README.md`

- [ ] **API documentation** (if ADR affects API contracts)
  - Update OpenAPI specs in `docs/API/*.openapi.yaml`
  - Document breaking changes clearly
  - Update API migration guides if needed

- [ ] **Database documentation** (if ADR affects schema)
  - Update schema docs in `docs/DB/*.sql`
  - Create migration guide if schema changes
  - Document data migration strategy

### Implementation Tracking
- [ ] **LESSONS_LEARNED/ retrospective planned** (schedule post-implementation review)
  - Create placeholder: `docs/LESSONS_LEARNED/NNNN-title-retrospective.md`
  - Schedule review: 1-2 weeks after implementation complete
  - Document: What worked? What didn't? What would we do differently?

- [ ] **Task tracking updated** (if part of larger task/phase)
  - Link ADR in relevant `docs/TASKS/*.md`
  - Update implementation approach section
  - Note ADR number in acceptance criteria

### Cross-References
- [ ] **Related ADRs linked**
  - Add "Related ADRs" section if this builds on/modifies previous decisions
  - Update superseded ADRs with "Status: Superseded by ADR-NNNN"

- [ ] **Code references prepared** (for implementation phase)
  - Plan where to add "See ADR-NNNN" comments
  - Identify key modules/functions that implement this decision
```

**Enforcement:** This checklist is reviewed during:
- Pre-commit review (quick zen-mcp check)
- Deep zen-mcp review before PR
- PR review by automated reviewers

**Time estimate:** 15-30 minutes for thorough documentation updates

### 8. Reference ADR in Related Work

**In code:**
```python
def check_circuit_breaker():
    """
    Check circuit breaker state in Redis.

    See ADR-0013 for design rationale.
    """
```

**In PRs:**
```markdown
## Related ADRs
- ADR-0013: Redis Circuit Breaker
```

---

## Decision Points

### Should I create an ADR for this change?

**Create ADR when:**
- Adding/removing/modifying services or components
- Changing API contracts or data schemas
- Adding third-party dependencies (libraries, services)
- Modifying deployment architecture
- Changing security mechanisms or auth flows
- Altering core error handling or retry strategies
- Making decisions that affect multiple teams

**Skip ADR (use comments/docs instead) when:**
- Simple bug fixes with no architectural impact
- Refactoring with no API changes
- Configuration tweaks (env vars, feature flags)
- Documentation updates
- Test additions

### Should I write ADR before or after implementation?

**ALWAYS before implementation:**
- Forces clear thinking before coding
- Enables team review of approach
- Documents decision rationale while fresh
- Prevents "lock-in" to hasty choices

**Exception:** POC/spike work (but document findings in ADR after)

### How many alternatives should I consider?

**Minimum: 2 alternatives** (not counting the chosen approach)

**Good rule:**
- 2-3 alternatives for most decisions
- 4-5 for critical architectural choices
- Include "do nothing" if relevant

**For each alternative:**
- Describe the approach
- List pros and cons
- Explain why not chosen

---

## ADR Template Example

```markdown
# ADR-0013: Use Redis for Circuit Breaker State

## Status
Accepted (2025-10-21)

## Context
We need distributed circuit breaker state shared across multiple instances:
- Multiple execution gateway instances
- State must be consistent across instances
- Sub-second read/write latency required
- State changes must be atomic

Current: No circuit breaker implementation

## Decision
Use Redis for circuit breaker state storage.

**Implementation:**
- Single Redis key: `cb:state` with values `OPEN` or `TRIPPED`
- Use Redis WATCH/MULTI/EXEC for atomic state transitions
- TTL on TRIPPED state for automatic recovery
- Fallback to TRIPPED if Redis unavailable (safe mode)

## Consequences

**Positive:**
- Atomic operations prevent race conditions
- Sub-millisecond read latency
- Automatic expiry for recovery
- Shared across all instances
- Well-understood technology

**Negative:**
- Additional dependency (Redis)
- Single point of failure (mitigated by fallback)
- State lost on Redis restart (acceptable for circuit breaker)

**Risks:**
- Redis unavailable = all trading stops (mitigated by fallback to safe mode)
- Network partition could cause split-brain (mitigated by TTL)

## Alternatives Considered

### PostgreSQL
- **Pros:** Already using, ACID guarantees
- **Cons:** Too slow (10-50ms vs <1ms), not designed for high-frequency reads
- **Why not:** Latency unacceptable for pre-trade check

### In-memory (process local)
- **Pros:** Fastest, no dependency
- **Cons:** Not shared across instances
- **Why not:** Need distributed state

### Consul/etcd
- **Pros:** Designed for distributed config
- **Cons:** Overkill, additional ops burden
- **Why not:** Redis simpler for this use case

## Implementation Notes

### Setup
```bash
# Add to docker-compose.yml
redis:
  image: redis:7-alpine
  ports:
    - "6379:6379"
```

### Code
```python
import redis

r = redis.Redis(host='localhost', port=6379)

# Check breaker
def is_tripped() -> bool:
    state = r.get("cb:state")
    return state == b"TRIPPED"

# Trip breaker
def trip():
    r.setex("cb:state", 3600, "TRIPPED")  # 1 hour TTL
```

### Testing
- Unit tests with fakeredis
- Integration tests with real Redis
- Failure tests (Redis down)

## Related ADRs
- ADR-0009: Redis Integration (general)
```

---

## Common Issues & Solutions

### Issue: Don't Know What Qualifies as "Architectural"

**Symptom:** Unsure if change needs ADR or just code comments

**Solution:**
- **If it affects >1 file/module** → Likely needs ADR
- **If it adds dependencies** → Needs ADR
- **If it changes API contracts** → Needs ADR
- **If it's reversible in <1 hour** → Probably just comments
- **When in doubt** → Create ADR (better too much than too little)

### Issue: Can't Think of Alternatives

**Symptom:** Only one approach seems obvious

**Solution:**
1. **Time-box research:** Spend 30 min searching for alternatives
2. **Common alternatives:**
   - Build vs buy (library)
   - Sync vs async
   - Database choices (SQL vs NoSQL, Postgres vs Redis)
   - Cloud vs self-hosted
   - Monolith vs microservice
3. **Include "do nothing":** What if we don't solve this problem?
4. **Ask team:** Get input from experienced developers

### Issue: Too Vague or Too Detailed

**Symptom:** ADR either says "use a database" or includes 500 lines of code

**Solution:**
**Right level of detail:**
- ✅ "Use PostgreSQL 16 with JSONB columns for flexible schema"
- ✅ "Use Redis with TTL for circuit breaker state"
- ❌ Too vague: "Use a database"
- ❌ Too detailed: Full implementation code in ADR

**Include:**
- Technology choice + version
- Key configuration decisions
- Integration points
- Migration approach
- Code examples (short, illustrative)

**Exclude:**
- Full implementation
- Line-by-line code walkthrough
- Detailed test cases (those go in implementation guides)

---

## Examples

### Example 1: Full ADR Workflow

```bash
# Scenario: Need to add distributed circuit breaker

# Step 1: Identify next number
$ ls docs/ADRs/ | grep -E '^[0-9]' | sort -n | tail -1
0012-model-registry.md

# Step 2: Create from template
$ cp docs/ADRs/0000-template.md docs/ADRs/0013-redis-circuit-breaker.md

# Step 3: Fill out all sections
# (See "ADR Template Example" section above for full content)

# Step 4: Self-review
✅ Context explains problem (distributed state needed)
✅ Decision is specific (Redis with TTL)
✅ 3 alternatives considered (PostgreSQL, in-memory, Consul)
✅ Consequences include pros AND cons
✅ Implementation notes with code examples

# Step 5: Update status to Accepted
# (Edit Status section in ADR)

# Step 6: Commit ADR FIRST
$ git add docs/ADRs/0013-redis-circuit-breaker.md
$ git commit -m "ADR-0013: Use Redis for circuit breaker state"
$ git push

# Step 7: Complete documentation update checklist
$ # Review checklist and update related docs:

# 7a. Update README.md (new capability)
$ vim README.md
# Added: Circuit breaker section to "Key Features"
# Added: Link to circuit breaker concept doc

# 7b. Create CONCEPTS/ doc (new architectural concept)
$ vim docs/CONCEPTS/circuit-breaker-design.md
# Explained: What circuit breakers are, why needed, how implemented

# 7c. Plan retrospective
$ vim docs/LESSONS_LEARNED/0013-circuit-breaker-retrospective.md
# Created placeholder for post-implementation review

# 7d. Commit documentation updates
$ git add README.md docs/CONCEPTS/circuit-breaker-design.md docs/LESSONS_LEARNED/
$ git commit -m "Add circuit breaker documentation (ADR-0013)

- Updated README.md with circuit breaker capabilities
- Created circuit-breaker-design.md concept doc
- Planned retrospective for post-implementation review

Related: ADR-0013"
$ git push

# Step 8: Implement and reference
$ # ... implement code ...
$ git commit -m "Implement Redis circuit breaker (ADR-0013)"

# Step 9: Reference in code
# See code example in step-by-step section
```

### Example 2: Quick ADR for Dependency Addition

```markdown
# ADR-0014: Use Pydantic for Config Validation

## Status
Accepted (2025-10-21)

## Context
Config loaded from .env has no validation. Typos cause runtime errors.
Need: type safety, env var parsing, clear error messages.

## Decision
Use Pydantic BaseSettings for all config classes.

## Consequences
**Positive:** Type safety, validation, IDE autocomplete, env parsing
**Negative:** New dependency, learning curve
**Risks:** None significant (well-maintained library)

## Alternatives Considered
### dataclasses + manual validation
- **Pros:** No dependency
- **Cons:** Manual parsing, no validation
- **Why not:** Reinventing the wheel

### attrs
- **Pros:** Lightweight
- **Cons:** No env parsing built-in
- **Why not:** Pydantic is standard for FastAPI projects

## Implementation Notes
```python
from pydantic_settings import BaseSettings

class AppConfig(BaseSettings):
    database_url: str
    redis_host: str = "localhost"

    class Config:
        env_file = ".env"
```
```

---

## Validation

**How to verify ADR is complete:**
- [ ] All required sections filled (Status, Context, Decision, Consequences, Alternatives, Implementation)
- [ ] At least 2 alternatives documented
- [ ] Consequences include both pros AND cons
- [ ] Decision is specific and actionable
- [ ] Implementation notes include code examples
- [ ] Status is "Accepted" before implementation starts
- [ ] ADR committed to git before implementation commit
- [ ] **Documentation update checklist completed** (Step 7):
  - [ ] README.md updated if new component/service
  - [ ] CONCEPTS/ doc created if new architectural pattern
  - [ ] Related workflows updated if process changes
  - [ ] LESSONS_LEARNED/ retrospective planned
  - [ ] Related ADRs cross-referenced

**What to check if ADR seems incomplete:**
- Read it as if you're new to the decision
- Can someone implement from ADR alone?
- Are trade-offs honest (not hiding downsides)?
- Is the "why" explained, not just "what"?
- Would this help in 6 months when debugging?
- Is the broader documentation ecosystem updated?

---

## Related Workflows

- [01-git-commit.md](./01-git-commit.md) - Commit ADR before implementation
- [07-documentation.md](./07-documentation.md) - Reference ADR in code docs

---

## References

- [/docs/STANDARDS/ADR_GUIDE.md](../../docs/STANDARDS/ADR_GUIDE.md) - Complete ADR guide with examples
- [/docs/ADRs/0000-template.md](../../docs/ADRs/0000-template.md) - ADR template
- ADR concept: https://adr.github.io/

---

**Maintenance Notes:**
- Update when ADR template changes
- Review quarterly for deprecated ADRs
- Link new ADRs to related existing ADRs
