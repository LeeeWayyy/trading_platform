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
2. **Read implementation guide**: `docs/IMPLEMENTATION_GUIDES/t1-data-etl.md`
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