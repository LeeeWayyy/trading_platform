# Tests Directory Context

Test suite for the trading platform. Tests are organized to mirror the source structure.

## Running Tests

```bash
source .venv/bin/activate          # REQUIRED: activate virtual environment first
make test                          # Preferred: runs with correct PYTHONPATH
PYTHONPATH=. python3 -m pytest tests/           # Run all tests
PYTHONPATH=. python3 -m pytest tests/apps/      # Run service-specific tests only
PYTHONPATH=. python3 -m pytest tests/libs/      # Run library tests only
PYTHONPATH=. python3 -m pytest -x -q tests/     # Stop on first failure, quiet output
PYTHONPATH=. python3 -m pytest -k "test_name"   # Run specific test by name
```

## Directory Structure

- `apps/` — Per-service unit and integration tests
- `libs/` — Library unit tests
- `strategies/` — Strategy and model tests
- `integration/` — Cross-service integration tests
- `e2e/` — End-to-end tests (full paper run, backtest replay)
- `fixtures/` — Shared test data and fixtures
- `conftest.py` — Root-level fixtures (DB sessions, Redis mocks, API clients)

## Key Testing Rules

- **Fixtures live in `conftest.py`** files at appropriate directory levels
- **Mock external APIs** (Alpaca, Redis) — never hit real services in tests
- **Use `freezegun`** or equivalent for time-dependent tests (all timestamps must be UTC)
- **Coverage target:** >80% for new code
- **Quarantined tests** listed in `quarantine.txt` — investigate before adding more
