# Development Environment Setup

## Python Version Requirement

This project requires **Python 3.11 or higher** (specified in `pyproject.toml`).

## Installing Python 3.11+

### Option 1: pyenv (Recommended - Best Version Management)

This is the recommended approach as it properly manages Python versions and ensures your virtual environment uses the correct Python version.

```bash
# Install pyenv (if not already installed)
brew install pyenv

# Add pyenv to your shell configuration
echo 'export PYENV_ROOT="$HOME/.pyenv"' >> ~/.zshrc
echo 'export PATH="$PYENV_ROOT/bin:$PATH"' >> ~/.zshrc
echo 'eval "$(pyenv init --path)"' >> ~/.zshrc
echo 'eval "$(pyenv init -)"' >> ~/.zshrc

# Reload your shell configuration
source ~/.zshrc

# Install Python 3.11.9
pyenv install 3.11.9

# Navigate to project directory
cd /path/to/trading_platform

# Set Python 3.11.9 as the local version for this project
pyenv local 3.11.9

# Verify - this should now show Python 3.11.9
python --version
# Expected: Python 3.11.9
```

**Note**: If you're using bash instead of zsh, replace `~/.zshrc` with `~/.bashrc` or `~/.bash_profile`.

### Option 2: Homebrew (Alternative for macOS)

```bash
# Install Python 3.11
brew install python@3.11

# Verify installation
python3.11 --version
# Expected: Python 3.11.x
```

**Important**: With this option, you'll need to explicitly use `python3.11` when creating virtual environments (see Step 1 below).

### Option 3: Official Python Installer

Download from [python.org](https://www.python.org/downloads/)

## Setting Up Virtual Environment

### Step 1: Create Virtual Environment

**If you used pyenv (Option 1 - Recommended)**:

```bash
# Navigate to project root
cd /path/to/trading_platform

# Verify pyenv is configured correctly
python --version
# Must show: Python 3.11.9

# Create venv using pyenv's Python
python -m venv .venv

# Activate venv
source .venv/bin/activate

# Verify Python version in venv
python --version
# Expected: Python 3.11.9
```

**If you used Homebrew (Option 2)**:

```bash
# Navigate to project root
cd /path/to/trading_platform

# Create venv explicitly with Python 3.11
python3.11 -m venv .venv

# Activate venv
source .venv/bin/activate

# Verify Python version in venv
python --version
# Expected: Python 3.11.x

# If this shows the wrong version, delete and recreate:
# deactivate
# rm -rf .venv
# python3.11 -m venv .venv
# source .venv/bin/activate
```

### Step 2: Install Dependencies

#### Using requirements.txt (Simpler)

```bash
# Ensure venv is activated (you should see (.venv) in your prompt)
source .venv/bin/activate

# Upgrade pip
pip install --upgrade pip setuptools wheel

# Install dependencies
pip install -r requirements.txt

# Verify installation
pip list | grep polars
# Expected: polars 0.20.x or higher
```

#### Using Poetry (Recommended for production)

```bash
# Install Poetry (if not already installed)
curl -sSL https://install.python-poetry.org | python3 -

# Install dependencies
poetry install

# Activate Poetry shell
poetry shell

# Verify
poetry run python --version
# Expected: Python 3.11.x
```

## Environment Configuration

### Step 1: Copy Environment Template

```bash
cp .env.example .env
```

### Step 2: Configure Settings

Edit `.env` file with your settings:

```bash
# Trading Platform Configuration

# Data Pipeline
DATA_FRESHNESS_MINUTES=30
OUTLIER_THRESHOLD=0.30

# Database (for future use)
DATABASE_URL=postgresql+psycopg://trader:trader@localhost:5432/trader

# Alpaca API (for T2+, not needed for T1)
ALPACA_API_KEY_ID=your_key_here
ALPACA_SECRET_KEY=your_secret_here
ALPACA_BASE_URL=https://paper-api.alpaca.markets

# Logging
LOG_LEVEL=INFO
```

## Secrets Management Setup

### Overview

This project uses a **pluggable secrets management system** to securely handle sensitive credentials (Alpaca API keys, database passwords). Secrets are kept separate from configuration and never committed to git.

**Backends supported:**
- **EnvSecretManager** (local development) - Reads from `.env` file
- **VaultSecretManager** (production) - HashiCorp Vault integration
- **AWSSecretsManager** (production) - AWS Secrets Manager integration

### Local Development (EnvSecretManager)

For local development, use `EnvSecretManager` which reads secrets from your `.env` file (gitignored).

#### Step 1: Copy Environment Template

```bash
# Create .env file from template
cp .env.template .env
```

**Note:** `.env.template` contains placeholders for secrets (commented out) and real configuration values. Only `.env` is gitignored.

#### Step 2: Populate Secrets

Edit `.env` and uncomment/populate secret values:

```bash
vim .env
```

**Secrets to populate (uncomment and fill in):**

```bash
# Secrets Management Configuration
SECRET_BACKEND=env  # Use EnvSecretManager for local dev

# Alpaca API Credentials (SECRETS - populate with your paper trading keys)
ALPACA_API_KEY_ID=PK...                # Get from https://app.alpaca.markets/paper/dashboard/overview
ALPACA_API_SECRET_KEY=...              # Your paper trading secret key

# Database Credentials (SECRETS - populate with local DB password)
DATABASE_PASSWORD=trader               # Default for local dev (docker-compose)

# Configuration (ENV VARS - keep these values as-is)
ALPACA_BASE_URL=https://paper-api.alpaca.markets/v2
DATABASE_HOST=localhost
DATABASE_PORT=5432
DATABASE_NAME=trader
DATABASE_USER=trader
REDIS_URL=redis://localhost:6379/0
DRY_RUN=true
LOG_LEVEL=INFO
STRATEGY_ID=alpha_baseline
```

**Important distinctions:**
- **Secrets** (via SecretManager): `ALPACA_API_KEY_ID`, `ALPACA_API_SECRET_KEY`, `DATABASE_PASSWORD`
- **Configuration** (via env vars): `ALPACA_BASE_URL`, `DATABASE_HOST`, `DRY_RUN`, `LOG_LEVEL`

#### Step 3: Verify Secret Loading

```bash
# Ensure venv is activated
source .venv/bin/activate

# Start a service to verify secrets load correctly
python -m apps.signal_service.main

# Expected logs:
# INFO: Initialized EnvSecretManager
# INFO: Loaded secret: database/password
# INFO: Database connection established
```

**If you see errors:**
- `SecretNotFoundError: Secret 'database/password' not found` → Check `.env` file has `DATABASE_PASSWORD` uncommented
- `FileNotFoundError: .env file not found` → Run `cp .env.template .env` and populate secrets

### Staging/Production (Vault or AWS)

For staging and production environments, use Vault or AWS Secrets Manager for encrypted secrets at rest.

**Migration process:**
1. Provision Vault or AWS Secrets Manager
2. Populate secrets in backend (encrypted at rest)
3. Update service environment variables:
   ```bash
   export SECRET_BACKEND=vault  # or 'aws'
   export VAULT_ADDR=https://vault.prod.example.com:8200
   export VAULT_TOKEN=<prod-token>
   export SECRET_NAMESPACE=prod
   ```
4. Services will automatically load secrets from backend on startup

**See detailed migration guide:**
- `docs/RUNBOOKS/secrets-migration.md` - Step-by-step migration from `.env` to Vault/AWS
- `docs/RUNBOOKS/secret-rotation.md` - 90-day secret rotation procedure (compliance)
- `docs/ADRs/0017-secrets-management.md` - Architecture decisions and rationale

### Security Best Practices

**DO:**
- ✅ Use `.env.template` as reference (commit to git)
- ✅ Keep real secrets in `.env` (gitignored)
- ✅ Use `EnvSecretManager` for local development
- ✅ Use Vault/AWS for staging and production
- ✅ Rotate secrets every 90 days (compliance requirement)

**DON'T:**
- ❌ Never commit `.env` file to git
- ❌ Never put secrets in `pyproject.toml`, `docker-compose.yml`, or code
- ❌ Never share `.env` file via email or Slack
- ❌ Never use production secrets in local development

### Troubleshooting Secrets

**Issue: `SecretNotFoundError: Secret 'alpaca/api_key_id' not found in EnvSecretManager`**

**Cause:** Missing or commented secret in `.env` file

**Solution:**
```bash
# Check .env file exists
ls -la .env

# Verify secret is present and uncommented
grep ALPACA_API_KEY_ID .env
# Should show: ALPACA_API_KEY_ID=PK...

# If missing, copy from template and populate
cp .env.template .env
vim .env  # Uncomment and fill in ALPACA_API_KEY_ID
```

**Issue: Service starts but Alpaca API returns 401 Unauthorized**

**Cause:** Invalid API credentials or wrong API key type (live vs paper)

**Solution:**
```bash
# Verify credentials in Alpaca dashboard:
# https://app.alpaca.markets/paper/dashboard/overview → API Keys

# Test credentials manually
curl -X GET https://paper-api.alpaca.markets/v2/account \
  -H "APCA-API-KEY-ID: <your_key_id>" \
  -H "APCA-API-SECRET-KEY: <your_secret_key>"

# Should return account details (not 401 Unauthorized)
# If 401: regenerate keys in dashboard and update .env
```

## Running Tests

### Run All Tests

```bash
# Ensure venv is activated
source .venv/bin/activate

# Run all tests with verbose output
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=libs --cov-report=term-missing

# Run specific test file
pytest tests/test_freshness.py -v
```

### Run Integration Test Only

```bash
pytest tests/test_integration_pipeline.py -v
```

### Expected Output

```
================================ test session starts =================================
platform darwin -- Python 3.11.x, pytest-8.0.x, pluggy-1.x.x
collected 48 items

tests/test_corporate_actions.py::TestAdjustForSplits::test_simple_split_adjustment PASSED
tests/test_corporate_actions.py::TestAdjustForSplits::test_no_split_returns_unchanged PASSED
...
tests/test_integration_pipeline.py::TestCompleteDataPipeline::test_end_to_end_pipeline_realistic_scenario PASSED

================================ 48 passed in 2.5s ==================================
```

## Development Workflow

### Activate Environment

```bash
# Always activate venv before working
source .venv/bin/activate

# Verify you're in the correct environment
which python
# Should show: /path/to/trading_platform/.venv/bin/python

python --version
# Should show: Python 3.11.x
```

### Run Linting

```bash
# Format code
black libs/ tests/

# Lint
ruff check libs/ tests/

# Type check
mypy libs/
```

### Run Infrastructure (Docker)

```bash
# Start services (Postgres, Redis, etc.)
make up

# Stop services
make down

# View logs
docker-compose logs -f
```

## IDE Configuration

### VSCode

Create `.vscode/settings.json`:

```json
{
  "python.defaultInterpreterPath": "${workspaceFolder}/.venv/bin/python",
  "python.testing.pytestEnabled": true,
  "python.testing.pytestArgs": ["tests"],
  "python.formatting.provider": "black",
  "python.linting.enabled": true,
  "python.linting.ruffEnabled": true,
  "editor.formatOnSave": true,
  "editor.rulers": [88]
}
```

### PyCharm

1. **Settings → Project → Python Interpreter**
2. Click gear icon → Add
3. Select "Existing environment"
4. Browse to `.venv/bin/python`
5. Apply

**Configure pytest**:
1. **Settings → Tools → Python Integrated Tools**
2. Default test runner: pytest
3. Apply

## Troubleshooting

### Issue: Virtual environment has wrong Python version

**Symptom**: After creating venv with `python3.11 -m venv .venv`, running `python --version` shows Python 3.9 or another version instead of 3.11.

**Root Cause**: The system's default Python is interfering with venv creation, or pyenv is not properly configured.

**Solution 1 - Use pyenv (Recommended)**:

```bash
# Deactivate current venv
deactivate

# Remove incorrect venv
rm -rf .venv

# Install and configure pyenv (if not done already)
brew install pyenv
echo 'export PYENV_ROOT="$HOME/.pyenv"' >> ~/.zshrc
echo 'export PATH="$PYENV_ROOT/bin:$PATH"' >> ~/.zshrc
echo 'eval "$(pyenv init --path)"' >> ~/.zshrc
echo 'eval "$(pyenv init -)"' >> ~/.zshrc

# Reload shell
source ~/.zshrc

# Install Python 3.11.9 and set as local version
pyenv install 3.11.9
cd /path/to/trading_platform
pyenv local 3.11.9

# Verify before creating venv
python --version
# Must show: Python 3.11.9

# Now create venv
python -m venv .venv

# Activate and verify
source .venv/bin/activate
python --version
# Should show: Python 3.11.9
```

**Solution 2 - Use full path to Python 3.11**:

```bash
# Deactivate and remove incorrect venv
deactivate
rm -rf .venv

# Find the full path to Python 3.11
which python3.11
# Example output: /opt/homebrew/bin/python3.11

# Create venv using full path
/opt/homebrew/bin/python3.11 -m venv .venv

# Activate and verify
source .venv/bin/activate
python --version
# Should show: Python 3.11.x
```

### Issue: `ModuleNotFoundError: No module named 'libs'`

**Solution**: Ensure you're running from project root with venv activated

```bash
# Check current directory
pwd
# Should be: /path/to/trading_platform

# Check Python path
python -c "import sys; print(sys.path)"
# Should include project root
```

### Issue: `pyenv: command not found` after installation

**Solution**: Shell configuration not loaded

```bash
# Reload your shell configuration
source ~/.zshrc  # or source ~/.bashrc for bash

# Verify pyenv is available
pyenv --version

# If still not working, check if pyenv is in PATH
echo $PATH | grep pyenv
```

### Issue: Import errors in tests

**Solution**: Install in editable mode

```bash
pip install -e .
```

Or add project root to PYTHONPATH:

```bash
export PYTHONPATH="${PYTHONPATH}:$(pwd)"
```

## Verification Checklist

Before starting development, verify each step:

- [ ] Python 3.11+ installed: `python --version` shows 3.11.x
- [ ] Virtual environment activated: Terminal prompt shows `(.venv)`
- [ ] Correct Python in venv: `which python` shows `.venv/bin/python`
- [ ] Python version in venv correct: `python --version` shows 3.11.x
- [ ] Dependencies installed: `pip list | grep polars` shows polars
- [ ] Tests pass: `pytest tests/ -v` shows all tests passing
- [ ] Environment configured: `.env` file exists with proper settings
- [ ] Docker running (optional for T1): `docker ps` shows containers (if needed)

**If any step fails, refer to the Troubleshooting section above.**

## Next Steps

After setup is complete:

1. **Run tests**: `pytest tests/ -v`
2. **Read implementation guide**: `docs/IMPLEMENTATION_GUIDES/p0t1-data-etl.md`
3. **Review ADRs**: `docs/ADRs/`
4. **Start T2**: Real data integration (see `P0_TICKETS.md`)

## Quick Reference

```bash
# Common commands (with venv activated)
make test          # Run all tests
make lint          # Run linters
make fmt           # Format code
make up            # Start Docker services
make down          # Stop Docker services

# Manual commands
pytest tests/ -v                           # Run tests
black libs/ tests/                         # Format
ruff check libs/ tests/                    # Lint
mypy libs/                                 # Type check
pytest --cov=libs --cov-report=html        # Coverage report
```

## Resources

- **Python 3.11**: https://www.python.org/downloads/
- **pyenv**: https://github.com/pyenv/pyenv
- **Poetry**: https://python-poetry.org/docs/
- **Pytest**: https://docs.pytest.org/
- **Polars**: https://pola-rs.github.io/polars-book/
- **Pydantic**: https://docs.pydantic.dev/