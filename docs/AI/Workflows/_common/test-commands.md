# Test Commands Reference

**Purpose:** Common testing commands and patterns used across all workflows.

## Basic Test Commands

### Run All Tests
```bash
make test
```

### Run Specific Test Module
```bash
make test ARGS="tests/libs/allocation/test_multi_alpha.py"
```

### Run Specific Test Class
```bash
make test ARGS="tests/libs/allocation/test_multi_alpha.py::TestMultiAlphaAllocator"
```

### Run Specific Test Function
```bash
make test ARGS="tests/libs/allocation/test_multi_alpha.py::TestMultiAlphaAllocator::test_equal_weight -v"
```

### Run Tests with Markers
```bash
# Skip integration and e2e tests (unit only)
make test ARGS="-m 'not integration and not e2e'"

# Run only integration tests
make test ARGS="-m integration"

# Run only e2e tests
make test ARGS="-m e2e"
```

### Verbose and Debug Options
```bash
# Verbose output
make test ARGS="-v"

# Stop on first failure
make test ARGS="-x"

# Show print statements
make test ARGS="-s"

# Combine options
make test ARGS="-xvs"
```

## CI/Local Commands

### Full CI Suite (MANDATORY before commit)
```bash
make ci-local
```

This runs:
1. `make fmt` - Format code (black + ruff)
2. `make lint` - Type checking and linting (mypy --strict + ruff)
3. `make test` - Run test suite

### Individual CI Steps
```bash
# Format code
make fmt

# Lint code
make lint

# Run tests
make test
```

## Coverage Commands

### Run Tests with Coverage
```bash
make test ARGS="--cov=libs --cov=apps --cov=strategies --cov-report=term-missing"
```

### Coverage Report
```bash
# Terminal report
poetry run pytest --cov=libs --cov-report=term-missing

# HTML report
poetry run pytest --cov=libs --cov-report=html
```

## Common Test Patterns

### Test Discovery
```bash
# List all tests
poetry run pytest --collect-only

# List tests in specific file
poetry run pytest tests/libs/allocation/test_multi_alpha.py --collect-only
```

### Debugging Failures
```bash
# Show full diff on assertion failures
make test ARGS="--tb=long"

# Show local variables on failure
make test ARGS="--showlocals"

# Drop into debugger on failure
make test ARGS="--pdb"
```

## See Also

- [Testing Standards](/docs/STANDARDS/TESTING.md) - Test pyramid and requirements
- [Development Workflow](../04-development.md) - Step-by-step testing guide
- [Development Workflow](../04-development.md) - Debug test failures
