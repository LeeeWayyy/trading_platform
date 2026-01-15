# Python Dependency Management

**Last Updated:** 2026-01-14
**Status:** Active
**Owner:** Development Team

---

## Overview

This document describes how to manage Python dependencies in this monorepo trading platform. We use **Poetry** for dependency management with a hybrid approach combining a shared root `pyproject.toml` with service-specific requirements files for Docker builds.

**Key Principles:**
- **Single source of truth:** `pyproject.toml` defines all dependencies
- **Path-based imports:** Internal libs/apps/strategies use PYTHONPATH, not pip install
- **Service-specific subsets:** Docker images install only needed dependencies
- **Version consistency:** All services use same package versions from root

---

## Architecture

### Monorepo Structure

```
trading_platform/
├── pyproject.toml           # Root dependency definitions (Poetry)
├── requirements.txt          # Auto-generated from pyproject.toml (for CI/Docker)
├── apps/                     # Microservices (FastAPI)
│   ├── signal_service/
│   │   ├── requirements.txt              # Service-specific subset
│   │   └── requirements-docker.txt       # Docker-optimized subset
│   └── execution_gateway/
│       ├── requirements.txt
│       └── requirements-docker.txt
├── libs/                     # Shared libraries (imported via PYTHONPATH)
│   ├── core/
│   ├── data/
│   ├── trading/
│   ├── models/
│   └── platform/
└── strategies/               # Production strategies (imported via PYTHONPATH)
```

**Internal Dependencies:** All `libs/`, `apps/`, and `strategies/` use **path-based imports** via PYTHONPATH, not pip packages.

---

## Dependency Types

### 1. External Dependencies (Third-Party Packages)

Defined in `pyproject.toml`, installed via Poetry/pip.

**Categories:**
- **Data processing:** polars, pandas, numpy, scipy
- **ML & Qlib:** pyqlib, lightgbm, scikit-learn, mlflow
- **Services:** fastapi, uvicorn, httpx
- **Database:** psycopg, redis, duckdb, alembic
- **Market data:** alpaca-py, pandas-datareader
- **Utilities:** pydantic, python-dotenv, tenacity
- **Monitoring:** prometheus-client, structlog
- **Web console:** nicegui, plotly, jinja2

### 2. Internal Dependencies (Monorepo Code)

**NOT installed as packages** - accessed via PYTHONPATH.

**Import patterns:**
```python
# Shared libraries (PYTHONPATH includes project root)
from libs.core.common.logging import get_logger
from libs.trading.risk_management.circuit_breaker import CircuitBreaker
from libs.data.market_data.provider import MarketDataProvider

# Strategies (signal service imports strategies)
from strategies.alpha_baseline.features import Alpha158Features

# Cross-service imports (orchestrator imports apps)
from apps.signal_service.schemas import SignalRequest
```

**PYTHONPATH setup:**
- **Development:** Set in IDE, shell rc, or Makefile
- **Docker:** `ENV PYTHONPATH=/app:$PYTHONPATH` in Dockerfile
- **Tests:** `pytest` automatically adds project root

### 3. Development Dependencies

Test tools, linters, formatters - NOT needed in production.

**Defined in:** `[tool.poetry.group.dev.dependencies]`

**Includes:**
- Testing: pytest, pytest-asyncio, pytest-cov, playwright
- Linting: black, ruff, mypy
- Type stubs: pandas-stubs, types-requests
- Pre-commit hooks: pre-commit

---

## How to Add/Update Dependencies

### Scenario 1: Add External Package (All Services)

**When:** New library needed across multiple services (e.g., new data provider, utility)

**Steps:**
```bash
# 1. Add to pyproject.toml
poetry add <package>

# 2. Update lock file
poetry lock

# 3. Regenerate requirements.txt for CI/Docker
poetry export -f requirements.txt --output requirements.txt --without-hashes

# 4. Update service-specific requirements if needed
# (see Scenario 3)

# 5. Commit changes
git add pyproject.toml poetry.lock requirements.txt
git commit -m "deps: Add <package> for <reason>"
```

**Example:**
```bash
poetry add yfinance  # Add Yahoo Finance data provider
poetry export -f requirements.txt --output requirements.txt --without-hashes
```

### Scenario 2: Add Development-Only Package

**When:** Adding test tool, linter, or dev utility

**Steps:**
```bash
# Add to dev group
poetry add --group dev <package>

# Lock and export
poetry lock
poetry export -f requirements.txt --output requirements.txt --without-hashes

# No need to update service requirements (dev deps excluded from Docker)
```

**Example:**
```bash
poetry add --group dev pytest-timeout  # Add test timeout support
```

### Scenario 3: Create Service-Specific Requirements

**When:** Docker image should only install subset of dependencies

**Pattern:**
```bash
# apps/signal_service/requirements-docker.txt
# Generated from root pyproject.toml - include only needed packages
# Excludes: Streamlit, web console deps, dev tools, other service deps

# Core dependencies (REQUIRED for all services)
polars>=1.0.0
pydantic>=2.5.0
pydantic-settings>=2.1.0
fastapi>=0.109.0
uvicorn[standard]>=0.27.0
httpx>=0.26.0
psycopg[binary]>=3.1.0
redis>=5.0.0
python-dotenv>=1.0.0
structlog>=24.0.0
prometheus-client>=0.19.0

# Service-specific dependencies
pyqlib>=0.9.5       # Signal service needs Qlib
lightgbm>=4.1.0     # Signal service needs LightGBM
pandas>=2.2.0       # Signal service needs pandas for Qlib
numpy>=1.26.0       # Required by Qlib
scikit-learn>=1.4.0 # Required by Qlib
```

**Steps to create:**
```bash
# 1. Copy relevant sections from root pyproject.toml
# 2. Remove unneeded packages (other services, dev tools, etc.)
# 3. Keep version pins consistent with root
# 4. Document rationale in comments
```

**Rationale:** Smaller Docker images, faster builds, security (fewer attack surfaces).

### Scenario 4: Add Internal Library Dependency

**When:** Service needs to import from a new shared library

**NO poetry commands needed** - just add import and ensure PYTHONPATH is set.

**Steps:**
```python
# In apps/execution_gateway/order_manager.py
from libs.trading.allocation.optimizer import optimize_portfolio  # New import

# No package installation - PYTHONPATH includes libs/
```

**Verify PYTHONPATH:**
```bash
# In Dockerfile
ENV PYTHONPATH=/app:$PYTHONPATH

# In development
export PYTHONPATH=$PYTHONPATH:/path/to/trading_platform
```

### Scenario 5: Update Package Version

**When:** Security patch, bug fix, or new features needed

**Steps:**
```bash
# Update version in pyproject.toml
poetry add <package>@^<new-version>

# Or manually edit pyproject.toml then:
poetry lock

# Regenerate requirements.txt
poetry export -f requirements.txt --output requirements.txt --without-hashes

# Update service-specific requirements.txt to match
# (Keep version pins consistent!)

git commit -am "deps: Update <package> to <version> - <reason>"
```

**Example:**
```bash
poetry add fastapi@^0.110.0  # Update to new version
poetry export -f requirements.txt --output requirements.txt --without-hashes
# Manually update apps/*/requirements-docker.txt to use fastapi>=0.110.0
```

---

## Versioning Conventions

### Version Pins

**Production dependencies:** Use caret (`^`) for minor version flexibility
```toml
[tool.poetry.dependencies]
fastapi = "^0.109.0"  # Allows 0.109.x, 0.110.x, etc. (not 1.x)
```

**Rationale:** Balance stability (no major breaks) with security patches.

**Dev dependencies:** Can be more flexible
```toml
[tool.poetry.group.dev.dependencies]
pytest = "^8.0.0"  # Latest pytest 8.x is fine
```

### Consistency Rules

1. **Root pyproject.toml is authoritative** - all version pins come from here
2. **Service requirements.txt must match** - use same version ranges
3. **Never pin different versions** across service requirements files
4. **Lock file (poetry.lock) is committed** - ensures reproducible installs

**Example - CORRECT:**
```
# pyproject.toml
pydantic = "^2.5.0"

# apps/signal_service/requirements-docker.txt
pydantic>=2.5.0

# apps/execution_gateway/requirements-docker.txt
pydantic>=2.5.0
```

**Example - WRONG:**
```
# ❌ BAD - Different versions across services
# apps/signal_service/requirements-docker.txt
pydantic>=2.5.0

# apps/execution_gateway/requirements-docker.txt
pydantic>=2.4.0  # INCONSISTENT!
```

---

## Publishing Strategy

**Current approach:** **NOT publishing** internal packages to PyPI or private registry.

**Rationale:**
- Monorepo simplifies development (no publish/versioning overhead)
- All code deployed together (no version skew between libs/apps)
- Path-based imports work well for our scale

**If we publish in future** (>50 services, multi-team development):
1. Extract `libs/` into separate packages
2. Publish to private PyPI (e.g., AWS CodeArtifact, JFrog Artifactory)
3. Version libs independently with semantic versioning
4. Update apps to install libs via pip instead of PYTHONPATH

**Not needed now** - revisit when monorepo pain exceeds publish overhead.

---

## Common Scenarios

### Scenario: New Microservice

**Steps:**
1. Create service directory: `apps/new_service/`
2. Create minimal requirements file:
   ```bash
   # apps/new_service/requirements-docker.txt
   # Core service dependencies (copy from similar service)
   fastapi>=0.109.0
   uvicorn[standard]>=0.27.0
   pydantic>=2.5.0
   # Add service-specific deps as needed
   ```
3. Update Dockerfile to copy and install requirements
4. No changes to root pyproject.toml unless adding NEW external package

### Scenario: New Shared Library

**Steps:**
1. Create library directory: `libs/<domain>/<library>/`
2. Add `__init__.py` to make it a package
3. **NO poetry/pip installation needed** - imports work via PYTHONPATH
4. If library needs NEW external package, add to root pyproject.toml

**Example:**
```bash
# Create new library
mkdir -p libs/platform/notifications
touch libs/platform/notifications/__init__.py

# Use it immediately (no install)
# In apps/orchestrator/main.py:
from libs.platform.notifications.email import send_email

# If notifications needs sendgrid (new external dep):
poetry add sendgrid
poetry export -f requirements.txt --output requirements.txt --without-hashes
```

### Scenario: Research Code Needs New Package

**Approach 1 (Recommended):** Add to root if widely useful
```bash
poetry add <package>
```

**Approach 2:** Install locally in research/ (not tracked)
```bash
cd research
pip install <package>  # Local install, not committed
```

**Rationale:** Research has lenient standards - experimental packages OK locally.

### Scenario: Security Vulnerability Patch

**Steps:**
```bash
# Update affected package
poetry add <package>@^<patched-version>
poetry lock

# Regenerate requirements
poetry export -f requirements.txt --output requirements.txt --without-hashes

# Update service requirements to match
# Run tests
make test

# Commit
git commit -am "security: Update <package> to <version> - CVE-XXXX"
```

---

## Dependency Drift Prevention

### Problem

Service requirements drift from root pyproject.toml over time.

### Solution

**Automated check** in CI:
```python
# scripts/check_dependency_drift.py (future task)
# Parses pyproject.toml and all apps/*/requirements*.txt
# Fails CI if versions don't match
```

**Manual verification:**
```bash
# Check for inconsistencies
grep -h "fastapi" pyproject.toml apps/*/requirements*.txt | sort | uniq

# Should show same version across all files
```

**Best practice:** Regenerate service requirements from root periodically.

---

## IDE Configuration

### VS Code

**`.vscode/settings.json`:**
```json
{
  "python.defaultInterpreterPath": "${workspaceFolder}/.venv/bin/python",
  "python.analysis.extraPaths": [
    "${workspaceFolder}/libs",
    "${workspaceFolder}/apps",
    "${workspaceFolder}/strategies"
  ],
  "python.analysis.ignore": [
    "${workspaceFolder}/research"
  ]
}
```

### PyCharm

**Mark directories as sources:**
1. Right-click project root → Mark Directory As → Sources Root
2. Libs, apps, strategies are automatically available for imports

---

## Docker Best Practices

### Multi-Stage Builds

**Pattern:** Build dependencies in stage 1, copy only runtime in stage 2
```dockerfile
# Stage 1: Builder
FROM python:3.11-slim as builder
WORKDIR /build
COPY apps/signal_service/requirements-docker.txt .
RUN pip install --no-cache-dir -r requirements-docker.txt -t /install

# Stage 2: Runtime
FROM python:3.11-slim
COPY --from=builder /install /usr/local/lib/python3.11/site-packages
COPY libs/ ./libs/
COPY strategies/ ./strategies/
COPY apps/signal_service/ ./apps/signal_service/
ENV PYTHONPATH=/app:$PYTHONPATH
CMD ["uvicorn", "apps.signal_service.main:app"]
```

### Layer Caching

**Optimize order:** (least changing → most changing)
1. Install dependencies (rarely changes)
2. Copy libs/ (stable)
3. Copy strategies/ (occasional)
4. Copy apps/ (frequent)

---

## Troubleshooting

### Issue: Import Error in Docker

**Symptom:** `ModuleNotFoundError: No module named 'libs'`

**Fix:** Check PYTHONPATH in Dockerfile
```dockerfile
ENV PYTHONPATH=/app:$PYTHONPATH
```

### Issue: Different Package Version in Docker vs Local

**Symptom:** Tests pass locally, fail in Docker

**Fix:** Ensure service requirements.txt matches pyproject.toml
```bash
poetry export -f requirements.txt --output requirements.txt --without-hashes
# Manually sync apps/*/requirements-docker.txt
```

### Issue: Poetry Lock File Conflicts

**Symptom:** Merge conflict in poetry.lock

**Fix:**
```bash
# Accept either version, then regenerate
git checkout --theirs poetry.lock  # or --ours
poetry lock --no-update  # Regenerate without updating versions
```

---

## Related Documents

- [CODING_STANDARDS.md](./CODING_STANDARDS.md) - Python code standards
- [TESTING.md](./TESTING.md) - Test requirements and patterns
- [../GETTING_STARTED/SETUP.md](../GETTING_STARTED/SETUP.md) - Development environment setup
- [../GETTING_STARTED/REPO_MAP.md](../GETTING_STARTED/REPO_MAP.md) - Repository structure

---

**Last Updated:** 2026-01-14
**Author:** Claude Code
**Status:** Active - Update when dependency patterns change
