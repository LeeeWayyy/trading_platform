# Environment Bootstrap Workflow

**Purpose:** Set up development environment from scratch
**Prerequisites:** macOS/Linux system, terminal access, GitHub account
**Expected Outcome:** Fully functional development environment, all tests passing
**Owner:** @devops-team + @onboarding
**Last Reviewed:** 2025-10-21

---

## When to Use This Workflow

**Use this workflow when:**
- First time setting up project
- New developer onboarding
- Setting up new development machine
- Reinstalling after system wipe
- Creating fresh VM/container for development
- Environment corrupted and needs rebuild

**Time estimate:** 30-60 minutes (depending on download speeds)

---

## Step-by-Step Process

### 1. Install Prerequisites

**Required software:**

**A. Install Homebrew** (macOS package manager)
```bash
# macOS only
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Linux: Use system package manager (apt, yum, etc.)
```

**B. Install system dependencies**
```bash
# macOS
brew install python@3.11 postgresql@15 redis git gh docker

# Ubuntu/Debian
sudo apt update
sudo apt install python3.11 python3.11-venv postgresql-15 redis-server git docker.io docker-compose

# Verify installations
python3.11 --version  # Should show 3.11.x
git --version
docker --version
```

**C. Install Poetry** (Python dependency manager)
```bash
curl -sSL https://install.python-poetry.org | python3.11 -

# Add to PATH (add to ~/.zshrc or ~/.bashrc)
export PATH="$HOME/.local/bin:$PATH"

# Verify
poetry --version  # Should show 1.5.0 or higher
```

**D. Configure Git**
```bash
git config --global user.name "Your Name"
git config --global user.email "your.email@example.com"

# Optional: Set default editor
git config --global core.editor "code --wait"  # VS Code
# or
git config --global core.editor "vim"
```

### 2. Clone Repository

```bash
# Via SSH (recommended, requires SSH key setup)
gh auth login  # Authenticate GitHub CLI
git clone git@github.com:username/trading_platform.git

# Or via HTTPS
git clone https://github.com/username/trading_platform.git

# Navigate to project
cd trading_platform

# Verify clone
git status
git remote -v
```

### 3. Install Python Dependencies

```bash
# Create virtual environment and install dependencies
poetry install

# Verify installation
poetry env info

# Activate virtual environment
poetry shell

# Or use poetry run for individual commands
poetry run python --version
```

**If poetry install fails:**
```bash
# Check Poetry config
poetry config --list

# Clear cache
poetry cache clear . --all

# Retry
poetry install --no-cache
```

### 4. Set Up Infrastructure Services

**A. Start Docker services**
```bash
# Start PostgreSQL, Redis, Grafana, Prometheus
make up

# Or manually:
docker-compose up -d

# Verify services running
docker-compose ps

# Expected output:
# NAME                STATUS
# postgres            Up
# redis               Up
# grafana             Up
# prometheus          Up
```

**B. Verify service connectivity**
```bash
# Test PostgreSQL
docker-compose exec postgres psql -U postgres -c "SELECT version();"

# Test Redis
docker-compose exec redis redis-cli PING
# Should return: PONG

# Access Grafana (in browser)
open http://localhost:3000
# Default credentials: admin/admin
```

### 5. Initialize Database

```bash
# Create database
make db-create

# Or manually:
docker-compose exec postgres psql -U postgres -c "CREATE DATABASE trading_db;"

# Run migrations
make db-migrate

# Or manually:
poetry run alembic upgrade head

# Verify tables created
docker-compose exec postgres psql -U postgres -d trading_db -c "\dt"

# Expected: tables like orders, positions, symbols, etc.
```

**Seed test data (optional):**
```bash
# Load sample data for development
poetry run python scripts/seed_data.py

# Or use test fixtures
poetry run pytest tests/fixtures/ --create-db
```

### 6. Configure Environment Variables

```bash
# Copy example env file
cp .env.example .env

# Edit .env with your values
# Required variables:
# - DATABASE_URL
# - REDIS_URL
# - ALPACA_API_KEY (for paper trading)
# - ALPACA_SECRET_KEY (for paper trading)

# Example .env:
cat > .env << EOF
# Database
DATABASE_URL=postgresql://postgres:password@localhost:5432/trading_db

# Redis
REDIS_URL=redis://localhost:6379/0

# Alpaca (Paper Trading)
ALPACA_API_KEY=PK_YOUR_PAPER_KEY
ALPACA_SECRET_KEY=your_paper_secret
ALPACA_BASE_URL=https://paper-api.alpaca.markets

# Application
DRY_RUN=true
LOG_LEVEL=INFO
ENVIRONMENT=development
EOF

# Verify .env loaded
poetry run python -c "from dotenv import load_dotenv; load_dotenv(); import os; print(os.getenv('DATABASE_URL'))"
```

### 7. Run Tests

```bash
# Run full test suite
make test

# Or manually:
poetry run pytest tests/ -v

# Expected output:
# ===================== XXX passed in X.XXs ======================

# Run linters
make lint

# Expected: No errors

# Check test coverage
make coverage
open htmlcov/index.html
```

**If tests fail:**
- See [06-debugging.md](./06-debugging.md) for debugging workflow
- Common issues: database not running, missing env vars, stale migrations

### 8. Verify Services Run

```bash
# Start signal service (in one terminal)
poetry run python -m apps.signal_service.main

# Start execution gateway (in another terminal)
poetry run python -m apps.execution_gateway.main

# Or use docker-compose (recommended for local dev)
docker-compose -f docker-compose.dev.yml up

# Verify services healthy
curl http://localhost:8000/health  # Execution gateway
curl http://localhost:8001/health  # Signal service

# Expected: {"status": "healthy"}
```

### 9. Run Paper Trading End-to-End

```bash
# Run full paper trading workflow
make paper-run

# This will:
# 1. Fetch latest market data
# 2. Generate signals from model
# 3. Calculate target positions
# 4. Place orders (DRY_RUN=true = logged only)
# 5. Update position tracker

# Check output
cat logs/paper_run_$(date +%Y%m%d).log

# Verify no errors
grep -i error logs/paper_run_$(date +%Y%m%d).log
# Should return empty (no errors)
```

### 10. Install Development Tools (Optional)

**A. Install IDE/Editor**
```bash
# VS Code (recommended)
brew install --cask visual-studio-code

# Install extensions
code --install-extension ms-python.python
code --install-extension ms-python.vscode-pylance
code --install-extension ms-python.black-formatter
code --install-extension charliermarsh.ruff
```

**B. Install Claude Code CLI**
```bash
# Install Claude Code
npm install -g @anthropics/claude-code

# Or download from:
# https://claude.com/code

# Verify installation
claude --version
```

**C. Configure zen-mcp (code review tool)**
```bash
# Already configured in .claude/commands/zen-review.md
# Test zen-mcp integration
# (See 03-zen-review-quick.md for usage)
```

---

## Decision Points

### Should I use Docker or local services?

**Use Docker (recommended):**
- Easier setup (one command: `make up`)
- Consistent across team (same versions)
- Isolated from system (no conflicts)
- Easy cleanup (`make down`)
- Production-like environment

**Use local services when:**
- Need to debug PostgreSQL/Redis internals
- Performance constraints (Docker overhead)
- Frequent restarts needed
- Already have local services running

**Hybrid approach:**
```bash
# Use Docker for most services
docker-compose up -d postgres redis grafana prometheus

# Run Python services locally for development
poetry run python -m apps.signal_service.main
```

### Should I use poetry or pip?

**Use Poetry (recommended):**
- Lockfile ensures deterministic installs
- Dependency resolution automatic
- Virtual environment management built-in
- Project standard

**Use pip when:**
- Quick one-off script
- CI environment (can use requirements.txt from poetry)

**Generate requirements.txt from poetry:**
```bash
poetry export -f requirements.txt --output requirements.txt
```

### How much test data should I seed?

**Minimal (fast, good for most development):**
```bash
# Just enough to run tests
poetry run python scripts/seed_data.py --minimal

# ~100 records, <1 second
```

**Full (realistic, good for testing):**
```bash
# Realistic dataset
poetry run python scripts/seed_data.py --full

# ~10,000 records, ~30 seconds
```

**Production-like (for stress testing):**
```bash
# Large dataset
poetry run python scripts/seed_data.py --large

# ~1,000,000 records, ~5 minutes
```

---

## Common Issues & Solutions

### Issue: `poetry install` Fails

**Symptom:**
```
ERROR: Failed to build wheels for package
```

**Solutions:**
```bash
# 1. Update poetry
poetry self update

# 2. Clear cache
poetry cache clear . --all

# 3. Check Python version
python --version  # Must be 3.11

# 4. Install system dependencies (macOS)
brew install postgresql@15  # For psycopg2

# 5. Retry with verbose output
poetry install -vvv
```

### Issue: Docker Services Won't Start

**Symptom:**
```
ERROR: Port 5432 is already in use
```

**Solutions:**
```bash
# 1. Check what's using the port
lsof -i :5432

# 2. Kill existing process
kill -9 <PID>

# 3. Or change port in docker-compose.yml
# Change: "5432:5432" to "5433:5432"

# 4. Restart services
make down && make up
```

### Issue: Database Migrations Fail

**Symptom:**
```
ERROR: relation "orders" already exists
```

**Solutions:**
```bash
# 1. Check migration status
poetry run alembic current

# 2. Reset database (CAUTION: deletes all data)
make db-reset

# 3. Or drop and recreate
docker-compose exec postgres psql -U postgres -c "DROP DATABASE trading_db;"
docker-compose exec postgres psql -U postgres -c "CREATE DATABASE trading_db;"

# 4. Rerun migrations
make db-migrate

# 5. Verify
docker-compose exec postgres psql -U postgres -d trading_db -c "\dt"
```

### Issue: Tests Fail Due to Missing Environment Variables

**Symptom:**
```
KeyError: 'DATABASE_URL'
```

**Solutions:**
```bash
# 1. Verify .env file exists
ls -la .env

# 2. Check .env content
cat .env | grep DATABASE_URL

# 3. Load .env in test environment
# Add to conftest.py:
from dotenv import load_dotenv
load_dotenv()

# 4. Or export directly
export DATABASE_URL=postgresql://postgres:password@localhost:5432/trading_db

# 5. Run tests again
make test
```

### Issue: `make` Commands Not Found

**Symptom:**
```
make: command not found
```

**Solutions:**
```bash
# 1. Install make (macOS)
xcode-select --install

# 2. Or use direct commands (see Makefile)
cat Makefile  # View available targets

# 3. Run commands directly
docker-compose up -d
poetry run pytest tests/ -v
```

---

## Examples

### Example 1: Fresh macOS Setup

```bash
# Scenario: New MacBook Pro, empty system

# Step 1: Install Homebrew
$ /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Step 2: Install prerequisites
$ brew install python@3.11 postgresql@15 redis git gh docker
$ brew install --cask docker  # Docker Desktop

# Step 3: Install Poetry
$ curl -sSL https://install.python-poetry.org | python3.11 -
$ export PATH="$HOME/.local/bin:$PATH"
$ poetry --version
Poetry version 1.7.0 ✅

# Step 4: Clone repo
$ gh auth login
$ git clone git@github.com:user/trading_platform.git
$ cd trading_platform

# Step 5: Install dependencies
$ poetry install
# ... installing dependencies ... ✅

# Step 6: Start infrastructure
$ make up
# Creating network ...
# Creating postgres ...
# Creating redis ...
✅

# Step 7: Initialize database
$ make db-create
$ make db-migrate
✅

# Step 8: Configure .env
$ cp .env.example .env
$ vim .env  # Add Alpaca API keys

# Step 9: Run tests
$ make test
===================== 296 passed in 2.14s ======================
✅

# Step 10: Verify services
$ curl http://localhost:8000/health
{"status": "healthy"}
✅

# Success! Environment ready for development.
```

### Example 2: Troubleshooting Failed Setup

```bash
# Scenario: Tests failing after setup

# Step 1: Run tests
$ make test
FAILED tests/test_order_placer.py::test_create_order - KeyError: 'DATABASE_URL'
❌

# Step 2: Check .env
$ cat .env
# File is empty! ❌

# Step 3: Copy example
$ cp .env.example .env

# Step 4: Edit .env
$ cat > .env << EOF
DATABASE_URL=postgresql://postgres:password@localhost:5432/trading_db
REDIS_URL=redis://localhost:6379/0
ALPACA_API_KEY=PK_test
ALPACA_SECRET_KEY=test_secret
ALPACA_BASE_URL=https://paper-api.alpaca.markets
DRY_RUN=true
EOF

# Step 5: Verify database running
$ docker-compose ps
NAME                STATUS
postgres            Up ✅
redis               Up ✅

# Step 6: Run migrations
$ make db-migrate
INFO  [alembic.runtime.migration] Running upgrade -> abc123
✅

# Step 7: Run tests again
$ make test
===================== 296 passed in 2.14s ======================
✅

# Success! Tests passing.
```

---

## Validation

**How to verify environment is set up correctly:**
- [ ] All dependencies installed (`poetry install` succeeds)
- [ ] Docker services running (`docker-compose ps` shows all Up)
- [ ] Database initialized (`psql` shows tables exist)
- [ ] .env file configured with valid values
- [ ] All tests passing (`make test` shows 0 failures)
- [ ] Linters passing (`make lint` shows no errors)
- [ ] Services start successfully (`curl /health` returns 200)
- [ ] Paper trading run completes (`make paper-run` succeeds)

**What to check if something seems broken:**
- Check service logs: `docker-compose logs`
- Verify environment variables: `cat .env`
- Test database connection: `psql -U postgres -h localhost`
- Check disk space: `df -h`
- Verify Python version: `python --version` (must be 3.11)
- Check Poetry environment: `poetry env info`

---

## Related Workflows

- [05-testing.md](./05-testing.md) - Running tests after setup
- [06-debugging.md](./06-debugging.md) - Debugging setup issues
- [01-git-commit.md](./01-git-commit.md) - Making first commit

---

## References

**Setup Documentation:**
- [/docs/GETTING_STARTED/SETUP.md](../../docs/GETTING_STARTED/SETUP.md) - Detailed setup guide
- [/docs/GETTING_STARTED/TESTING_SETUP.md](../../docs/GETTING_STARTED/TESTING_SETUP.md) - Test environment

**Tools:**
- Poetry: https://python-poetry.org/docs/
- Docker: https://docs.docker.com/
- PostgreSQL: https://www.postgresql.org/docs/
- Redis: https://redis.io/documentation

**Troubleshooting:**
- Docker troubleshooting: https://docs.docker.com/config/daemon/
- Poetry troubleshooting: https://python-poetry.org/docs/faq/

---

**Maintenance Notes:**
- Update when new dependencies added
- Review when system requirements change
- Update version numbers as tools upgrade
- Add new common issues as discovered
