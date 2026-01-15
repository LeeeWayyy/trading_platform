# ADR-025: Monorepo Build Systems - Evaluation Deferred

**Status:** Accepted
**Date:** 2026-01-14
**Decision Makers:** Development Team
**Consulted:** DevOps, Platform Engineering

---

## Context

As the trading platform grows, we need to decide whether to adopt a monorepo build system (Bazel, Pants, Nx) or continue with our current script-based approach.

**Current state:**
- **~11 microservices** in `apps/`
- **~20 shared libraries** across 5 domains in `libs/`
- **3 production strategies** in `strategies/`
- **Build approach:** Makefile + Poetry + Docker multi-stage builds
- **Test execution:** pytest with selective markers (`@pytest.mark.integration`)
- **CI:** GitHub Actions with path-based selective triggering

**Pain points:**
- **Manual dependency tracking:** Changes to `libs/` require manually knowing which `apps/` are affected
- **No incremental builds:** Docker rebuilds entire images even for small changes
- **CI time:** ~15-20 minutes for full test suite (could be faster with smarter caching)
- **No build caching:** Local builds don't share cache with CI

**Growth projections:**
- **6 months:** +5 services, +10 libs (manageable with current approach)
- **12 months:** +10 services, +20 libs (approaching pain threshold)
- **18+ months:** +15-20 services, +30-40 libs (monorepo tooling likely beneficial)

---

## Decision

**We will defer adopting a monorepo build system for 12-18 months** and continue with our current script-based approach.

**Reasons:**
1. **Current scale manageable:** 11 services and 20 libs don't justify monorepo tool overhead
2. **CI is fast enough:** 15-20 minutes is acceptable for our team size (2-3 developers)
3. **Simple approach works:** Makefile + pytest markers + Docker are well-understood
4. **Focus on features:** We'd rather spend time on trading features than build infrastructure
5. **Tooling maturity:** Bazel/Pants ecosystems still evolving (especially for Python + Docker)

**Re-evaluation triggers:**
- **>50 services** OR **>100 libraries** - monorepo tooling becomes necessary
- **CI time >30 minutes** - build time becomes developer bottleneck
- **Team size >10 developers** - coordination overhead justifies tooling investment
- **Multi-language expansion** (e.g., adding Rust/Go services) - Bazel's strength

---

## Considered Options

### Option 1: Continue with Script-Based Approach (SELECTED)

**Tools:**
- Makefile for common commands
- Poetry for Python dependency management
- Docker multi-stage builds
- GitHub Actions with path-based triggers
- pytest markers for selective test execution

**Pros:**
- ✅ Simple, well-understood by team
- ✅ No learning curve or migration cost
- ✅ Works well at current scale
- ✅ Easy to debug and customize
- ✅ Standard Python tooling (Poetry, pytest)

**Cons:**
- ❌ Manual dependency tracking
- ❌ No incremental builds
- ❌ CI could be faster with smarter caching
- ❌ Doesn't scale to 100+ services

**When to revisit:** 12-18 months or when scale exceeds thresholds

---

### Option 2: Adopt Bazel

**Overview:** Google's build system, multi-language support, hermetic builds.

**Pros:**
- ✅ **Incremental builds:** Only rebuild changed targets
- ✅ **Remote caching:** Share build cache between CI and local
- ✅ **Multi-language:** Works with Python, Go, Rust, C++
- ✅ **Hermetic:** Reproducible builds (fixed versions, sandboxed)
- ✅ **Scalable:** Used by Google, Uber, Twitter

**Cons:**
- ❌ **Steep learning curve:** Complex BUILD files, custom rules
- ❌ **Python ecosystem gaps:** Poetry integration awkward, not all packages Bazel-friendly
- ❌ **Docker integration:** Bazel's container_image != Docker (different layer semantics)
- ❌ **Debugging difficulty:** Build failures harder to diagnose than Makefile
- ❌ **Migration cost:** ~2-4 weeks to migrate + ongoing maintenance

**Best for:** Very large monorepos (100+ services), multi-language, companies with dedicated build engineers

---

### Option 3: Adopt Pants

**Overview:** Python-first monorepo build system (used by Twitter, Toolchain).

**Pros:**
- ✅ **Python-first:** Better Poetry integration than Bazel
- ✅ **Incremental builds:** Dependency-aware caching
- ✅ **Remote caching:** Share cache like Bazel
- ✅ **Simpler than Bazel:** Less boilerplate, auto-infers dependencies
- ✅ **Good Docker support:** Native Docker build rules

**Cons:**
- ❌ **Less mature than Bazel:** Smaller community, fewer resources
- ❌ **Python-focused:** Multi-language support not as strong
- ❌ **Migration cost:** ~1-2 weeks + ongoing maintenance
- ❌ **Overkill at current scale:** Benefits don't justify costs yet

**Best for:** Python-heavy monorepos (50-200 services), teams comfortable with newer tools

---

### Option 4: Adopt Nx

**Overview:** JavaScript-first monorepo tool (React/Node.js focused).

**Pros:**
- ✅ **Good for web console:** If we expand NiceGUI with React/TypeScript
- ✅ **Incremental builds + caching:** Similar to Bazel/Pants
- ✅ **Easy to adopt:** Less boilerplate than Bazel

**Cons:**
- ❌ **JavaScript-focused:** Python support is secondary
- ❌ **Wrong fit:** Our backend is Python-heavy (90%+ of code)
- ❌ **Not recommended for Python monorepos**

**Best for:** Full-stack JavaScript/TypeScript applications

---

## Decision Criteria

| Criterion | Weight | Script-Based | Bazel | Pants | Nx |
|-----------|--------|--------------|-------|-------|----|
| **Simplicity** | 30% | ✅ 10/10 | ❌ 3/10 | 🟡 6/10 | 🟡 7/10 |
| **Python ecosystem fit** | 25% | ✅ 10/10 | 🟡 5/10 | ✅ 8/10 | ❌ 3/10 |
| **CI speed improvement** | 20% | ❌ 3/10 | ✅ 9/10 | ✅ 9/10 | 🟡 6/10 |
| **Scalability (>50 services)** | 15% | ❌ 3/10 | ✅ 10/10 | ✅ 9/10 | 🟡 7/10 |
| **Migration cost** | 10% | ✅ 10/10 | ❌ 2/10 | 🟡 5/10 | 🟡 6/10 |
| **Weighted Score** | - | **8.1** | **5.9** | **7.4** | **5.8** |

**Script-Based wins at current scale** due to simplicity and low migration cost.
**Pants would be best choice at 50+ services** (Python-first, good Docker support).

---

## Implementation

**Current approach improvements** (no tooling change):

### 1. Smarter CI Triggering (2-3 hours)
```yaml
# .github/workflows/ci-tests-coverage.yml
on:
  push:
    paths:
      - 'libs/core/**'        # Triggers all services
      - 'libs/trading/**'     # Triggers signal_service, execution_gateway
      - 'apps/signal_service/**'  # Triggers only signal_service tests
```

**Benefit:** Reduces CI time by 30-50% for focused changes

### 2. Test Parallelization (1-2 hours)
```bash
# Run tests in parallel (pytest-xdist)
pytest -n auto  # Uses all CPU cores
```

**Benefit:** Reduces test time by 40-60% (10min → 4-6min)

### 3. Docker Layer Caching (3-4 hours)
```dockerfile
# Pre-build base image with common dependencies
FROM python:3.11-slim as base
RUN pip install polars pandas numpy  # Heavy deps

FROM base
COPY requirements.txt .
RUN pip install -r requirements.txt  # Only service-specific deps
```

**Benefit:** Reduces Docker build time by 60-80% (8min → 2min)

### 4. Dependency Graph Documentation (2-3 hours)
```python
# scripts/dev/analyze_dependencies.py
# Generate dependency graph: which apps depend on which libs
```

**Benefit:** Makes impact analysis easier (know what to test when libs/ change)

**Total effort:** ~10 hours
**Total savings:** ~30-50% CI time reduction without monorepo tooling

---

## Consequences

### Positive
- ✅ **No migration disruption:** Team stays productive
- ✅ **Simple approach continues:** Easy onboarding for new developers
- ✅ **Focused improvements:** Targeted optimizations (test parallelization, caching)
- ✅ **Defer complexity:** Avoid build tool learning curve

### Negative
- ❌ **Manual dependency tracking:** Still need to know which apps depend on which libs
- ❌ **No incremental builds:** Small changes still rebuild entire Docker images
- ❌ **CI could be faster:** Monorepo tools would provide 2-3x speedup
- ❌ **Future migration cost:** If we do adopt Bazel/Pants later, migration will be more work

### Neutral
- 🟡 **Re-evaluation needed:** Must revisit decision in 12-18 months
- 🟡 **Scale monitoring:** Track service count, lib count, CI time

---

## Monitoring & Review

**Metrics to track:**
- Service count (current: 11)
- Library count (current: 20)
- CI time (current: 15-20 min, target: <30 min)
- Developer feedback on build pain points

**Review schedule:**
- **Q3 2026** (6 months): Check service/lib count growth
- **Q1 2027** (12 months): Full re-evaluation of this decision
- **Q3 2027** (18 months): Final deadline for monorepo tool adoption decision

**Hard triggers for immediate re-evaluation:**
- Service count >50
- Library count >100
- CI time >30 minutes
- Developer complaints about build slowness

---

## Related Documents

- [Python Dependencies](../STANDARDS/PYTHON_DEPENDENCIES.md) - Current dependency management
- [CI/CD Guide](../GETTING_STARTED/CI_CD_GUIDE.md) - Current CI architecture
- [Repository Map](../GETTING_STARTED/REPO_MAP.md) - Project structure

---

## References

- [Bazel](https://bazel.build/) - Google's build system
- [Pants](https://www.pantsbuild.org/) - Python-first monorepo tool
- [Nx](https://nx.dev/) - JavaScript-focused monorepo tool
- [Monorepo Tools Comparison](https://monorepo.tools/) - Feature matrix

---

**Status:** Accepted
**Review Date:** 2027-01-01 (12 months)
**Supersedes:** None
**Superseded By:** TBD (future ADR if we adopt Bazel/Pants)
