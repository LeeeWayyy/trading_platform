# Architecture Decision Records (ADR) Guide

## Purpose

ADRs document significant architectural decisions, their context, and consequences. They serve as:
- Historical record of why decisions were made
- Learning tool for understanding system design
- Reference for future changes
- Communication tool for design decisions

## When to Create an ADR (MANDATORY)

Create an ADR for ANY of the following changes:

### Service & Component Changes
- Adding a new service or microservice
- Removing or deprecating a service
- Changing service boundaries or responsibilities
- Modifying inter-service communication patterns

### API & Interface Changes
- Changing API contracts (endpoints, request/response schemas)
- Adding or removing API endpoints
- Modifying authentication/authorization mechanisms
- Changing API versioning strategy

### Data & Storage Changes
- Modifying database schema
- Adding new tables or collections
- Changing data models
- Altering data retention policies
- Switching storage technologies

### Architecture & Deployment
- Changing deployment architecture
- Modifying scaling strategies
- Altering infrastructure components
- Changing container orchestration approach

### Dependencies & Integration
- Introducing new third-party dependencies
- Changing external service integrations (Alpaca, data providers, etc.)
- Modifying dependency management strategy
- Adding new runtime dependencies

### Patterns & Practices
- Modifying data flow patterns
- Changing error handling strategies
- Altering logging or monitoring approaches
- Modifying security mechanisms
- Changing concurrency patterns

### Testing & Quality
- Modifying testing strategies
- Changing test framework or tools
- Altering quality gates
- Modifying CI/CD pipeline significantly

### General Rule
**If you're unsure whether a change needs an ADR, create one.** It's better to have too much documentation than too little, especially when learning.

## ADR Template Location

Use the template at: `/docs/ADRs/0000-template.md`

## ADR Naming Convention

Format: `NNNN-short-descriptive-title.md`

Examples:
- `0001-use-postgres-for-state-management.md`
- `0002-idempotent-order-ids.md`
- `0003-circuit-breaker-in-redis.md`
- `0004-feature-parity-strategy.md`
- `0005-use-pydantic-for-config.md`

**Numbering:**
- Start at 0001
- Increment sequentially
- Never reuse numbers
- Pad with zeros (0001, not 1)

## Required ADR Sections

### 1. Status (required)
Current state of the decision:
- **Proposed** — Under consideration, seeking feedback
- **Accepted** — Approved and ready to implement
- **Deprecated** — No longer recommended, but may still be in use
- **Superseded** — Replaced by another ADR (link to it)

### 2. Context (required)
Explain the situation:
- What problem are we solving?
- Why does this decision need to be made now?
- What constraints exist (technical, business, time)?
- What is the current state?

**Tips:**
- Include relevant background information
- Explain why status quo is insufficient
- Reference related tickets or requirements
- Keep it factual and objective

### 3. Decision (required)
State what you decided:
- Be specific and concrete
- Explain the chosen approach clearly
- Include key implementation details
- Define success criteria

**Tips:**
- Use clear, unambiguous language
- Include diagrams if helpful
- Specify concrete technologies/patterns
- Make it actionable

### 4. Consequences (required)
Analyze the trade-offs:

**Positive:**
- What benefits does this provide?
- What problems does it solve?
- What capabilities does it enable?

**Negative:**
- What downsides exist?
- What complexity does it add?
- What maintenance burden?

**Risks:**
- What could go wrong?
- What assumptions are we making?
- What might change in the future?

**Tips:**
- Be honest about downsides
- Include both short-term and long-term impacts
- Consider performance, cost, maintainability
- Think about reversibility

### 5. Alternatives Considered (required)
What else did you evaluate?

For each alternative:
- Describe the approach
- Explain why it wasn't chosen
- Note if it was a close decision

**Tips:**
- Include "do nothing" as an option
- Show you considered multiple approaches
- Explain trade-offs between options
- Note if alternatives might be reconsidered later

### 6. Implementation Notes (required)
Technical details for implementers:
- Key steps to implement
- Migration path (if applicable)
- Testing approach
- Rollback plan
- Timeline estimates

**Tips:**
- Include code snippets if helpful
- Reference related documentation
- Note dependencies on other work
- Identify potential blockers

### 7. Related ADRs (optional but encouraged)
Links to related decisions:
- ADRs this builds upon
- ADRs this supersedes
- ADRs that depend on this
- ADRs that conflict with this

## ADR Workflow

### 1. Create ADR in Proposed Status
```bash
# Find next available number
ls docs/ADRs/ | grep -E '^[0-9]' | sort -n | tail -1
# If last is 0003, create 0004

# Copy template
cp docs/ADRs/0000-template.md docs/ADRs/0004-idempotent-order-submission.md

# Edit with your decision details
vim docs/ADRs/0004-idempotent-order-submission.md
```

### 2. Fill Out All Sections

Work through each required section:
- Write context based on ticket or problem statement
- Research alternatives thoroughly
- Document decision clearly
- Analyze consequences honestly
- Add implementation notes

### 3. Review (if working with team) or Self-Review

**Self-review checklist:**
- [ ] Context clearly explains the problem
- [ ] Decision is specific and actionable
- [ ] At least 2 alternatives considered
- [ ] Consequences include both pros and cons
- [ ] Implementation notes are concrete
- [ ] All required sections completed

**If working with others:**
- Share ADR for feedback
- Update based on comments
- Reach consensus before accepting

### 4. Update Status to Accepted

```markdown
# Status
Accepted (2024-01-15)
```

### 5. Commit ADR BEFORE Implementing

```bash
# Commit ADR first
git add docs/ADRs/0004-idempotent-order-submission.md
git commit -m "ADR-0004: Use deterministic client_order_id for idempotency"

# Then implement, referencing ADR
git commit -m "Implement idempotent order submission (ADR-0004)"
```

### 6. Reference ADR in Related Work

In code comments:
```python
def deterministic_id(order: OrderIn) -> str:
    """
    Generate deterministic order ID for idempotency.

    See ADR-0004 for design rationale.
    """
```

In PRs:
```markdown
## Summary
Implements idempotent order submission to prevent duplicate orders.

## Related ADRs
- ADR-0004: Idempotent Order Submission

## Changes
...
```

## ADR Examples

### Example 1: Simple Decision

```markdown
# ADR-0001: Use PostgreSQL for State Management

## Status
Accepted (2024-01-10)

## Context
We need a database to store:
- Order state (pending, filled, cancelled)
- Position snapshots
- Risk limits configuration
- Model registry metadata

Requirements:
- ACID transactions (critical for order/position consistency)
- JSON support (for flexible metadata)
- Strong typing
- Mature ecosystem
- Free/open source

Current state: No database selected.

## Decision
Use PostgreSQL 16 as the primary database.

Implementation:
- Run in Docker for local dev
- Use managed service (RDS/Cloud SQL) for production
- Connect via SQLAlchemy ORM for Python
- Use Alembic for migrations

## Consequences

**Positive:**
- ACID guarantees prevent position/order inconsistencies
- JSONB columns provide schema flexibility
- Mature, well-documented, stable
- Great Python support (psycopg3, SQLAlchemy)
- Free and open source

**Negative:**
- Single point of failure (mitigated by managed service HA)
- Potential bottleneck at scale (not a concern for MVP)
- More complex than SQLite

**Risks:**
- Learning curve if unfamiliar with PostgreSQL
- Need backup/recovery strategy
- Connection pool management in async context

## Alternatives Considered

### SQLite
- **Pros:** Simple, file-based, no server
- **Cons:** No concurrent writes, limited production use, no built-in HA
- **Why not:** Cannot handle multiple services writing concurrently

### MongoDB
- **Pros:** Flexible schema, horizontal scaling
- **Cons:** No ACID across documents (in older versions), eventual consistency
- **Why not:** ACID is critical for order/position consistency

### Redis + Persistence
- **Pros:** Very fast, already using for cache/breakers
- **Cons:** Not designed as primary DB, limited query capabilities
- **Why not:** Better suited for cache/ephemeral state

## Implementation Notes

### Setup
```bash
# Docker compose (already in place)
docker compose up -d postgres

# Create database
createdb -h localhost -U trader trader
```

### Migrations
```bash
# Install alembic
poetry add alembic

# Initialize
alembic init db/migrations

# Create migration
alembic revision --autogenerate -m "Initial schema"

# Apply
alembic upgrade head
```

### Connection
```python
from sqlalchemy.ext.asyncio import create_async_engine
from config import settings

engine = create_async_engine(
    settings.database_url,
    pool_size=10,
    max_overflow=20
)
```

### Testing
- Use separate test database
- Fixtures to create/drop tables
- Transaction rollback for test isolation

## Related ADRs
None (first ADR)
```

### Example 2: Complex Decision

```markdown
# ADR-0004: Idempotent Order Submission Using Deterministic IDs

## Status
Accepted (2024-01-15)

## Context
Order submission to Alpaca may fail due to:
- Network timeouts (request sent but no response)
- Server errors (500, 503)
- Rate limiting (429)

Current behavior:
- Retry on failure → may create duplicate orders
- No retry → may lose intended orders
- No way to know if order was accepted

Requirements:
- Must prevent duplicate orders (critical safety requirement)
- Must support safe retries
- Must handle network failures gracefully
- Must work with Alpaca API constraints

Alpaca API provides:
- `client_order_id` field (optional, max 48 chars)
- 409 Conflict response if duplicate `client_order_id`
- No server-side retry guarantees

## Decision
Use deterministic `client_order_id` generation for idempotent order submission.

**Approach:**
1. Generate ID from order parameters + date: `hash(symbol, side, qty, price, strategy, date)[:24]`
2. Use SHA256 for collision resistance
3. Include date to allow same order on different days
4. Check database before submitting to broker
5. Handle 409 from Alpaca as success (already submitted)

**ID Format:**
- First 24 chars of SHA256 hex (96 bits)
- Collision probability: ~1 in 10^28 for birthday attack
- Safe for our scale (thousands of orders/day)

## Consequences

**Positive:**
- Safe retries without duplicate orders
- Works even if database write fails (Alpaca returns 409)
- No manual intervention needed for retries
- Deterministic = testable and debuggable
- Works with Alpaca's built-in duplicate detection

**Negative:**
- Same order on same day gets same ID (by design, but must understand)
- 24-char limit means truncating hash (acceptable collision risk)
- Must include all relevant parameters in hash (easy to forget one)
- Cannot submit truly identical orders on same day (edge case)

**Risks:**
- Hash collision (mitigated by SHA256 + 96 bits)
- Clock skew causing different dates (mitigated by using UTC)
- Floating point precision causing different hashes (mitigated by rounding)
- Forgetting to include a parameter (mitigated by tests + code review)

## Alternatives Considered

### UUID per Order
- **Approach:** Generate random UUID for each submission
- **Pros:** No collisions, simple
- **Cons:** Retry creates different ID → duplicate orders
- **Why not:** Doesn't solve retry problem

### Server-Side Deduplication
- **Approach:** Store pending orders in DB, check before submit
- **Pros:** More control
- **Cons:** Doesn't handle DB write failures; still need broker-level idempotency
- **Why not:** Incomplete solution, adds complexity

### Sequence Numbers per Symbol
- **Approach:** Auto-incrementing counter per symbol
- **Pros:** Simple, sequential
- **Cons:** Requires distributed coordination; doesn't work across restarts
- **Why not:** Too complex for distributed system

### Include Timestamp in ID
- **Approach:** Use timestamp + random suffix
- **Pros:** Unique IDs
- **Cons:** Retry gets different timestamp → duplicate orders
- **Why not:** Same problem as UUID

## Implementation Notes

### ID Generation
```python
import hashlib
from datetime import date

def deterministic_id(order: OrderIn) -> str:
    today = date.today().isoformat()
    raw = f"{order.symbol}|{order.side}|{order.qty}|{order.limit_price}|{order.strategy_id}|{today}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]
```

### Handling Duplicates
```python
@app.post("/orders")
async def place_order(o: OrderIn):
    client_order_id = deterministic_id(o)

    # Check DB first
    existing = await get_order(client_order_id)
    if existing:
        return {"status": "duplicate", "order": existing}

    # Submit to Alpaca
    try:
        result = await alpaca.submit_order(o, client_order_id)
        await save_order(client_order_id, result)
        return result
    except AlpacaConflict:  # 409
        # Order exists in Alpaca but not in our DB (DB write failed before)
        return {"status": "duplicate_recovered", "client_order_id": client_order_id}
```

### Testing Strategy
```python
def test_same_order_same_id():
    """Retry generates same ID."""
    order = OrderIn(symbol="AAPL", side="buy", qty=10)
    assert deterministic_id(order) == deterministic_id(order)

def test_different_qty_different_id():
    """Different parameters generate different IDs."""
    order1 = OrderIn(symbol="AAPL", side="buy", qty=10)
    order2 = OrderIn(symbol="AAPL", side="buy", qty=11)
    assert deterministic_id(order1) != deterministic_id(order2)

async def test_retry_doesnt_duplicate():
    """Retrying order returns existing, doesn't submit twice."""
    order = OrderIn(symbol="AAPL", side="buy", qty=10)

    result1 = await place_order(order)
    assert result1["status"] == "accepted"

    result2 = await place_order(order)
    assert result2["status"] == "duplicate"
```

### Migration Path
1. Add `client_order_id` column to orders table (nullable initially)
2. Deploy ID generation code
3. Test in DRY_RUN mode for 1 week
4. Enable for paper trading
5. Verify no duplicates in logs/DB
6. Enable for live trading
7. Make `client_order_id` non-nullable after 1 month

### Rollback Plan
If issues discovered:
1. Set feature flag to disable deterministic IDs
2. Fall back to UUID generation (accepts duplicates as known issue)
3. Fix ID generation bug
4. Re-enable with new hash function
5. No data migration needed (client_order_id remains in DB)

## Related ADRs
- ADR-0001: Use PostgreSQL for State Management (orders table)
- ADR-0006: Retry Strategy with Exponential Backoff (uses this for idempotency)
```

## Tips for Writing Good ADRs

### Be Specific
**Bad:** "We'll use a database."
**Good:** "We'll use PostgreSQL 16 with JSONB columns for metadata."

### Explain Why
**Bad:** "Use Redis for circuit breakers."
**Good:** "Use Redis for circuit breakers because we need atomic operations, sub-second latency, and state shared across multiple service instances."

### Document Trade-offs Honestly
**Bad:** "PostgreSQL is the best database."
**Good:** "PostgreSQL provides ACID and good Python support, but adds operational complexity compared to SQLite."

### Make it Actionable
**Bad:** "We should consider improving error handling."
**Good:** "Implement custom exception hierarchy in `libs/common/exceptions.py` with context-rich error messages."

### Link to Resources
- Reference related tickets
- Link to external documentation
- Cite benchmarks or research
- Point to example code

### Update ADRs When Superseded
If you make a new decision that contradicts an old one:

1. Update old ADR status to `Superseded by ADR-NNNN`
2. Create new ADR referencing the old one
3. Explain what changed and why

Example:
```markdown
# ADR-0003: Circuit Breaker in Redis

## Status
Superseded by ADR-0012: Circuit Breaker in PostgreSQL (2024-03-15)

Reason: Redis proved too volatile for critical safety state. See ADR-0012.
```

## Common Mistakes to Avoid

### Mistake: ADR After Implementation
**Wrong:** Implement feature, then write ADR documenting what you did.
**Right:** Write ADR first, get feedback, then implement.

### Mistake: ADR for Every Small Change
**Wrong:** ADR for renaming a variable.
**Right:** ADR for architectural decisions (see "When to Create" section).

### Mistake: Vague Decisions
**Wrong:** "Use a good database."
**Right:** "Use PostgreSQL 16 with connection pooling via SQLAlchemy."

### Mistake: No Alternatives
**Wrong:** Only document chosen approach.
**Right:** Show you considered multiple options and explain why you chose this one.

### Mistake: Hiding Downsides
**Wrong:** Only list benefits.
**Right:** Honestly document trade-offs, risks, and downsides.

### Mistake: No Implementation Details
**Wrong:** "We'll use Docker."
**Right:** "We'll use Docker Compose for local dev with this docker-compose.yml structure..."

## ADR Review Checklist

Before accepting an ADR:

- [ ] Status is set appropriately (Proposed → Accepted)
- [ ] Context clearly explains the problem and requirements
- [ ] Decision is specific and actionable
- [ ] At least 2 alternatives were considered and compared
- [ ] Consequences include both positive and negative impacts
- [ ] Risks are identified and mitigation noted
- [ ] Implementation notes are concrete and helpful
- [ ] Related ADRs are linked (if any)
- [ ] Examples or diagrams included (if complex)
- [ ] ADR filename follows naming convention
- [ ] ADR committed before implementation begins
