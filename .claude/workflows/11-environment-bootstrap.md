# Environment Bootstrap Workflow

**Purpose:** Set up development environment from scratch
**Prerequisites:** macOS/Linux system, terminal access, GitHub account
**Expected Outcome:** Fully functional development environment, all tests passing
**Owner:** @devops-team + @onboarding
**Last Reviewed:** 2025-10-21

---

## Quick Reference

**Testing:** See [Test Commands Reference](./_common/test-commands.md)
**Git:** See [Git Commands Reference](./_common/git-commands.md)

---

## When to Use This Workflow

**Use this workflow when:**
- First time setting up project
- New developer onboarding
- Setting up new development machine
- Reinstalling after system wipe
- Environment corrupted and needs rebuild

**Time estimate:** 30-60 minutes

---

## Step-by-Step Process

### 1. Install Prerequisites

```bash
# macOS
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
brew install python@3.11 postgresql@15 redis git gh docker

# Ubuntu/Debian
sudo apt update && sudo apt install python3.11 python3.11-venv postgresql-15 redis-server git docker.io docker-compose

# Install Poetry
curl -sSL https://install.python-poetry.org | python3.11 -
export PATH="$HOME/.local/bin:$PATH"

# Configure Git (see Git Commands Reference for details)
git config --global user.name "Your Name"
git config --global user.email "your.email@example.com"
```

See [Git Commands Reference](./_common/git-commands.md) for git configuration details.

### 2. Clone Repository

```bash
gh auth login
git clone git@github.com:username/trading_platform.git
cd trading_platform
```

See [Git Commands Reference](./_common/git-commands.md) for git setup details.

### 3. Install Dependencies and Start Infrastructure

```bash
# Install Python dependencies
poetry install

# Start infrastructure services
make up

# Verify services running
docker-compose ps  # Should show postgres, redis, grafana, prometheus
```

### 4. Initialize Database

```bash
make db-create
make db-migrate

# Verify tables exist
docker-compose exec postgres psql -U postgres -d trading_db -c "\dt"
```

### 5. Configure Environment

```bash
cp .env.example .env

# Edit .env with required values:
# - DATABASE_URL, REDIS_URL
# - ALPACA_API_KEY, ALPACA_SECRET_KEY (paper trading)
# - DRY_RUN=true
```

### 6. Run Tests

```bash
make test
make lint
```

See [Test Commands Reference](./_common/test-commands.md) for all testing options.

**If tests fail:** See [06-debugging.md](./06-debugging.md)

### 7. Verify End-to-End

```bash
# Start services
docker-compose -f docker-compose.dev.yml up

# Check health endpoints
curl http://localhost:8000/health  # Execution gateway
curl http://localhost:8001/health  # Signal service

# Run paper trading workflow
make paper-run
```

---

## Common Issues

### Poetry Install Fails

```bash
# Update poetry and clear cache
poetry self update
poetry cache clear . --all

# Verify Python 3.11
python --version

# Install system dependencies (macOS)
brew install postgresql@15

# Retry
poetry install -vvv
```

### Docker Services Won't Start

```bash
# Check port conflicts
lsof -i :5432

# Kill existing process or change port in docker-compose.yml
kill -9 <PID>

# Restart
make down && make up
```

### Database Migrations Fail

```bash
# Check status
poetry run alembic current

# Reset database (CAUTION: deletes all data)
make db-reset
make db-migrate
```

### Missing Environment Variables

```bash
# Verify .env exists and has required values
cat .env | grep DATABASE_URL

# Copy example if missing
cp .env.example .env
```

---

## Validation

**How to verify setup succeeded:**
- [ ] `poetry install` succeeds
- [ ] `docker-compose ps` shows all services Up
- [ ] `make test` shows 0 failures
- [ ] `make lint` shows no errors
- [ ] `curl http://localhost:8000/health` returns 200
- [ ] `make paper-run` completes successfully

---

## Related Workflows

- [05-testing.md](./05-testing.md) - Running tests
- [06-debugging.md](./06-debugging.md) - Debugging issues
- [01-git.md](./01-git.md) - Git workflow

---

## References

- [/docs/GETTING_STARTED/SETUP.md](../../docs/GETTING_STARTED/SETUP.md) - Detailed setup
- Poetry: https://python-poetry.org/docs/
- Docker: https://docs.docker.com/
