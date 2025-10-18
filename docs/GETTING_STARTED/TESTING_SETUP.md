# Testing Setup Guide

This guide provides step-by-step instructions for setting up the testing environment for the trading platform. Follow these instructions on any host to ensure tests run correctly.

---

## Prerequisites

### Required Software

1. **Python 3.11+**
   ```bash
   python --version  # Should be 3.11 or higher
   ```

2. **PostgreSQL 14+**
   ```bash
   psql --version  # Should be 14 or higher
   ```

3. **Git**
   ```bash
   git --version
   ```

### Required Python Packages

All packages are listed in project dependencies. Install via:

```bash
# Activate virtual environment
source .venv/bin/activate

# Install dependencies (if not already installed)
pip install -r requirements.txt

# Or for development
pip install -e ".[dev]"
```

**Key Testing Dependencies:**
- `pytest >= 8.0.0` - Test framework
- `pytest-asyncio >= 0.23.0` - Async test support
- `pytest-cov >= 4.1.0` - Coverage reporting
- `psycopg2-binary >= 2.9.0` - PostgreSQL adapter
- `lightgbm >= 4.1.0` - Model loading
- `pandas >= 2.0.0` - Data manipulation
- `numpy >= 1.24.0` - Numerical operations

---

## Database Setup

### Step 1: Start PostgreSQL

**Option A: Local PostgreSQL (Mac)**
```bash
# Start PostgreSQL service
brew services start postgresql@14

# Verify it's running
brew services list | grep postgresql
```

**Option B: Local PostgreSQL (Linux)**
```bash
# Start PostgreSQL service
sudo systemctl start postgresql
sudo systemctl status postgresql
```

**Option C: Docker PostgreSQL**
```bash
# Start PostgreSQL container
docker run -d \
  --name trading_platform_db \
  -p 5432:5432 \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_DB=trading_platform \
  postgres:14

# Verify it's running
docker ps | grep trading_platform_db
```

### Step 2: Create Databases

We need two databases:
- `trading_platform` - Production/development database
- `trading_platform_test` - Test database (isolated from production)

```bash
# Create production database
createdb -U postgres trading_platform

# Create test database
createdb -U postgres trading_platform_test

# Verify databases exist
psql -U postgres -l | grep trading_platform
```

**Expected output:**
```
 trading_platform      | postgres | UTF8     | ...
 trading_platform_test | postgres | UTF8     | ...
```

### Step 3: Run Database Migrations

```bash
# Navigate to project root
cd /path/to/trading_platform

# Run migration for production database
psql -U postgres -d trading_platform -f migrations/001_create_model_registry.sql

# Run migration for test database
psql -U postgres -d trading_platform_test -f migrations/001_create_model_registry.sql
```

**Expected output for each:**
```
CREATE TABLE
CREATE INDEX
CREATE INDEX
CREATE INDEX
CREATE FUNCTION
CREATE FUNCTION
CREATE FUNCTION
NOTICE: Migration 001 completed successfully: model_registry table created
```

### Step 4: Verify Database Schema

```bash
# Check production database
psql -U postgres -d trading_platform -c "\d model_registry"

# Check test database
psql -U postgres -d trading_platform_test -c "\d model_registry"
```

**Expected output:**
```
                                        Table "public.model_registry"
        Column         |            Type             | Collation | Nullable |                   Default
-----------------------+-----------------------------+-----------+----------+---------------------------------------------
 id                    | integer                     |           | not null | nextval('model_registry_id_seq'::regclass)
 strategy_name         | text                        |           | not null |
 version               | text                        |           | not null |
 ...
```

---

## Test Data Setup

### Step 1: Verify T1 Data Exists

Tests require T1 adjusted data to be present:

```bash
# Check data directory structure
ls -la data/adjusted/

# Check specific date (used in tests)
ls -la data/adjusted/2024-01-15/
```

**Expected structure:**
```
data/adjusted/
├── 2024-01-15/
│   ├── AAPL.parquet
│   ├── MSFT.parquet
│   ├── GOOGL.parquet
│   ├── AMZN.parquet
│   └── TSLA.parquet
└── ...
```

**If data doesn't exist**, generate test data:
```bash
# Run T1 data generation (if you have the pipeline setup)
python scripts/generate_test_data.py --symbols AAPL,MSFT,GOOGL,AMZN,TSLA --start 2024-01-01 --end 2024-01-31
```

### Step 2: Verify T2 Model Exists

Tests require the trained alpha_baseline model:

```bash
# Check model file
ls -lh artifacts/models/alpha_baseline.txt
```

**Expected output:**
```
-rw-r--r--  1 user  staff   1.2M Jan 17 14:30 artifacts/models/alpha_baseline.txt
```

**If model doesn't exist**, train it:
```bash
# Train baseline model (from T2)
python -m strategies.alpha_baseline.train

# Or use quick training script
python scripts/quick_train_test.py
```

### Step 3: Register Model in Database

Register the trained model in the database:

```bash
# Get absolute path to model
MODEL_PATH=$(pwd)/artifacts/models/alpha_baseline.txt

# Register model
psql -U postgres -d trading_platform <<EOF
INSERT INTO model_registry (
    strategy_name, version, model_path, status,
    performance_metrics, config, notes
) VALUES (
    'alpha_baseline',
    'v1.0.0',
    '$MODEL_PATH',
    'active',
    '{"ic": 0.082, "sharpe": 1.45, "max_drawdown": -0.12, "win_rate": 0.55}',
    '{"learning_rate": 0.05, "max_depth": 6, "num_boost_round": 100}',
    'Initial baseline model from T2 implementation'
) ON CONFLICT (strategy_name, version) DO NOTHING;
EOF

# Verify registration
psql -U postgres -d trading_platform -c "SELECT id, strategy_name, version, status FROM model_registry;"
```

**Expected output:**
```
 id | strategy_name  | version | status
----+----------------+---------+--------
  1 | alpha_baseline | v1.0.0  | active
(1 row)
```

---

## Environment Variables

Create a `.env` file in the project root:

```bash
# Create .env file
cat > .env <<'EOF'
# Database Configuration
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/trading_platform
TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/trading_platform_test

# Data Configuration
DATA_DIR=data/adjusted

# Strategy Configuration
DEFAULT_STRATEGY=alpha_baseline
TRADABLE_SYMBOLS=AAPL,MSFT,GOOGL,AMZN,TSLA

# Portfolio Configuration
TOP_N=3
BOTTOM_N=3

# Model Reload Configuration
MODEL_RELOAD_INTERVAL_SECONDS=300

# Logging Configuration
LOG_LEVEL=INFO
EOF
```

**Verify environment variables:**
```bash
# Source the .env file
source .env

# Check variables
echo $DATABASE_URL
echo $DATA_DIR
```

---

## Running Tests

### Quick Health Check

Run the automated health check script:

```bash
# Make script executable
chmod +x scripts/test_health_check.sh

# Run health check
./scripts/test_health_check.sh
```

**Expected output:**
```
=== Trading Platform Health Check ===

[1/6] Checking Python version...
✓ Python 3.11.9

[2/6] Checking PostgreSQL...
✓ PostgreSQL is running

[3/6] Checking database exists...
✓ Database: trading_platform

[4/6] Checking model_registry table...
✓ Table exists with 1 models

[5/6] Checking T1 data...
✓ Data directory: data/adjusted

[6/6] Checking model file...
✓ Model file: artifacts/models/alpha_baseline.txt (1.2M)

=== All checks passed! ===
```

### Unit Tests Only (No External Dependencies)

```bash
# Run unit tests (fast, no database required)
pytest apps/signal_service/tests/ -v -k "not integration"

# With coverage
pytest apps/signal_service/tests/ -v -k "not integration" --cov=apps.signal_service --cov-report=term-missing
```

**Expected output:**
```
======================== test session starts =========================
apps/signal_service/tests/test_model_registry.py::TestModelMetadata::test_model_metadata_creation PASSED
apps/signal_service/tests/test_model_registry.py::TestModelRegistryInitialization::test_initialization_with_valid_url PASSED
...
======================== 18 passed, 12 skipped in 2.35s =========================
```

### Integration Tests (Require Database and Data)

```bash
# Run all tests including integration tests
pytest apps/signal_service/tests/ -v -m integration

# Or run specific integration test file
pytest apps/signal_service/tests/test_model_registry.py -v -m integration
```

**Note:** Integration tests are marked with `@pytest.mark.integration` and skipped by default.

### Manual Integration Tests

Run the manual test scripts for P1-P3:

```bash
# Test P1-P2: Model Registry
python scripts/test_p1_p2_model_registry.py

# Test P3: Signal Generator
python scripts/test_p3_signal_generator.py

# Or run all manual tests
./scripts/run_manual_tests.sh
```

---

## Test Configuration

### pytest.ini

The project's `pytest.ini` configures test behavior:

```ini
[pytest]
# Test discovery patterns
python_files = test_*.py
python_classes = Test*
python_functions = test_*

# Markers for test categorization
markers =
    integration: marks tests as integration tests (require external services)
    slow: marks tests as slow running (> 1 second)
    unit: marks tests as unit tests (no external dependencies)

# Test paths
testpaths = tests apps/signal_service/tests

# Coverage configuration
addopts =
    --strict-markers
    --tb=short
    --disable-warnings

# Asyncio configuration
asyncio_mode = auto
```

### conftest.py Fixtures

Test fixtures are defined in `apps/signal_service/tests/conftest.py`:

**Available fixtures:**
- `temp_dir` - Temporary directory (auto-cleanup)
- `mock_model` - LightGBM model trained on synthetic data
- `test_db_url` - Test database connection string
- `db_connection` - Database connection (auto-close)
- `setup_model_registry_table` - Create table in test DB
- `mock_t1_data` - Mock T1 Parquet files
- `mock_alpha158_features` - Mock features DataFrame
- `sample_model_metadata` - Sample metadata dictionary

**Usage:**
```python
def test_something(temp_dir, mock_model):
    # Fixtures are automatically provided
    model_path = temp_dir / "model.txt"
    # ... test code ...
```

---

## Troubleshooting

### Issue: PostgreSQL Connection Failed

**Symptom:**
```
psycopg2.OperationalError: could not connect to server: Connection refused
```

**Solutions:**
1. Check PostgreSQL is running:
   ```bash
   # Mac
   brew services list | grep postgresql

   # Linux
   sudo systemctl status postgresql

   # Docker
   docker ps | grep postgres
   ```

2. Check connection string in `.env`:
   ```bash
   echo $DATABASE_URL
   # Should be: postgresql://postgres:postgres@localhost:5432/trading_platform
   ```

3. Verify PostgreSQL is listening on port 5432:
   ```bash
   lsof -i :5432
   ```

### Issue: Database Does Not Exist

**Symptom:**
```
psycopg2.OperationalError: FATAL: database "trading_platform" does not exist
```

**Solution:**
```bash
# Create database
createdb -U postgres trading_platform

# Verify
psql -U postgres -l | grep trading_platform
```

### Issue: Model File Not Found

**Symptom:**
```
FileNotFoundError: Model file not found: artifacts/models/alpha_baseline.txt
```

**Solution:**
```bash
# Check if model exists
ls -lh artifacts/models/alpha_baseline.txt

# If not, train model
python -m strategies.alpha_baseline.train

# Or use quick training
python scripts/quick_train_test.py
```

### Issue: T1 Data Not Found

**Symptom:**
```
ValueError: No features available for 2024-01-15
```

**Solution:**
```bash
# Check data exists
ls -la data/adjusted/2024-01-15/

# If not, generate test data
python scripts/generate_test_data.py --symbols AAPL,MSFT,GOOGL --start 2024-01-01 --end 2024-01-31
```

### Issue: Import Errors

**Symptom:**
```
ModuleNotFoundError: No module named 'apps.signal_service'
```

**Solution:**
```bash
# Ensure virtual environment is activated
source .venv/bin/activate

# Ensure you're in project root
pwd  # Should be /path/to/trading_platform

# Add project root to PYTHONPATH
export PYTHONPATH=$(pwd):$PYTHONPATH

# Or install in editable mode
pip install -e .
```

---

## Test Execution Checklist

Before running tests, verify:

- [ ] PostgreSQL is running
- [ ] Database `trading_platform` exists
- [ ] Database `trading_platform_test` exists
- [ ] Migrations have been run (both databases)
- [ ] Model file exists: `artifacts/models/alpha_baseline.txt`
- [ ] T1 data exists: `data/adjusted/2024-01-15/*.parquet`
- [ ] Model registered in database
- [ ] `.env` file created with correct values
- [ ] Virtual environment activated
- [ ] In project root directory

**Quick verification:**
```bash
./scripts/test_health_check.sh
```

---

## Continuous Integration (Future)

For CI/CD pipelines (GitHub Actions, etc.):

```yaml
# .github/workflows/test.yml (example)
name: Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest

    services:
      postgres:
        image: postgres:14
        env:
          POSTGRES_USER: postgres
          POSTGRES_PASSWORD: postgres
          POSTGRES_DB: trading_platform_test
        ports:
          - 5432:5432
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5

    steps:
      - uses: actions/checkout@v3

      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: |
          pip install -r requirements.txt

      - name: Run migrations
        run: |
          psql -h localhost -U postgres -d trading_platform_test -f migrations/001_create_model_registry.sql

      - name: Run tests
        run: |
          pytest apps/signal_service/tests/ -v -k "not integration"
```

---

## Additional Resources

- **pytest documentation:** https://docs.pytest.org/
- **PostgreSQL documentation:** https://www.postgresql.org/docs/
- **psycopg2 documentation:** https://www.psycopg.org/docs/

For project-specific documentation:
- `/docs/IMPLEMENTATION_GUIDES/t3-signal-service.md` - Implementation guide
- `/docs/ADRs/0004-signal-service-architecture.md` - Architecture decisions
- `/docs/CONCEPTS/model-registry.md` - Model registry concept

---

## Summary

**Setup time:** ~10-15 minutes

**Steps:**
1. Install PostgreSQL
2. Create databases (production + test)
3. Run migrations
4. Verify T1 data and T2 model exist
5. Register model in database
6. Create `.env` file
7. Run health check
8. Run tests

**Quick start:**
```bash
# 1. Setup databases
createdb -U postgres trading_platform
createdb -U postgres trading_platform_test
psql -U postgres -d trading_platform -f migrations/001_create_model_registry.sql
psql -U postgres -d trading_platform_test -f migrations/001_create_model_registry.sql

# 2. Register model
./scripts/register_model.sh

# 3. Run health check
./scripts/test_health_check.sh

# 4. Run tests
pytest apps/signal_service/tests/ -v
```

Done! Your testing environment is ready. 🎉
