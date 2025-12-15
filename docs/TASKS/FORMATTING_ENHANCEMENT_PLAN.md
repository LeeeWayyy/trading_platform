# Formatting Enhancement Plan (Revised)

**Branch:** `feature/formatting-enhancement`
**Date:** 2024-12-14
**Status:** Planning - Round 2 Review
**Revision:** Based on Codex and Gemini feedback

---

## Problem Statement

The current `make fmt` command has issues:
1. **Exits with error code** even when formatting succeeds (ruff returns non-zero on unfixable lint issues)
2. **176 lint issues remain** after auto-fix, mostly in test files
3. **No per-file-ignores** configured for test files that intentionally violate E402 (module-level imports after sys.path manipulation)
4. **Ruff config schema mismatch** - mixing old `[tool.ruff]` and new `[tool.ruff.lint.*]` schemas

---

## Current State Analysis

### Ruff Configuration Issue
Current pyproject.toml mixes schemas:
```toml
# OLD schema (ruff 0.1.x)
[tool.ruff]
select = [...]
ignore = [...]

# NEW schema (ruff 0.1.x+)
[tool.ruff.lint.flake8-pytest-style]
mark-parentheses = true
```

**Fix:** Standardize to new `[tool.ruff.lint]` schema for consistency.

### Issue Breakdown (176 remaining after auto-fix)
| Rule | Count | Description | Fix Strategy |
|------|-------|-------------|--------------|
| E402 | 66 | Module import not at top | Per-file-ignore for tests/ |
| F841 | 25 | Unused local variable | **Manual fix** (not blanket ignore) |
| PT011 | 17 | pytest.raises too broad | Manual fix or targeted ignore |
| UP007 | 13 | Use X \| Y for type annotations | --unsafe-fixes |
| B007 | 10 | Loop variable not used | --unsafe-fixes |
| Others | 45 | Various style issues | Case-by-case |

---

## Proposed Changes (Revised)

### 1. Standardize pyproject.toml Ruff Config

Migrate from mixed schema to consistent `[tool.ruff.lint]` schema:

```toml
[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = [
    "E",   # pycodestyle errors
    "W",   # pycodestyle warnings
    "F",   # pyflakes
    "I",   # isort
    "B",   # flake8-bugbear
    "C4",  # flake8-comprehensions
    "UP",  # pyupgrade
    "PT",  # flake8-pytest-style
]
ignore = [
    "E501",  # line too long (handled by black)
    "B008",  # do not perform function calls in argument defaults
]

[tool.ruff.lint.per-file-ignores]
# Test files need sys.path manipulation before imports
"tests/**/*.py" = [
    "E402",   # Module level import not at top (intentional for stubbing)
    "PT004",  # Fixture doesn't return (valid pattern)
]
# Scripts may have path manipulation
"scripts/**/*.py" = ["E402"]

[tool.ruff.lint.flake8-pytest-style]
mark-parentheses = true
fixture-parentheses = true
parametrize-names-type = "tuple"
parametrize-values-type = "list"
parametrize-values-row-type = "tuple"
```

**Note:** F841 (unused variable) is NOT blanket-ignored - will be fixed manually or with targeted `# noqa` comments to avoid masking real issues.

### 2. Fix Makefile - Separate Formatting from Linting

```makefile
fmt: ## Format code with black and ruff (auto-fix, won't fail on unfixable)
	poetry run black .
	poetry run ruff check --fix --unsafe-fixes --exit-zero .
	@echo ""
	@echo "Formatting complete. Run 'make lint' to check for remaining issues."

fmt-check: ## Check if code is formatted (for CI, fails on issues)
	poetry run black --check .
	poetry run ruff format --check .
```

**Rationale:**
- `--exit-zero` prevents failure on unfixable issues (formatting succeeded)
- `--unsafe-fixes` applies safe automated fixes for UP007, B007, etc.
- Echo reminder to run `make lint` for full validation
- `fmt-check` uses `ruff format --check` for pure formatting check

### 3. Update ci-local to Check All Files

Update Step 4 in Makefile ci-local target:

```makefile
@echo "Step 4/6: Linting with ruff"
poetry run ruff check .  # Changed from: libs/ apps/ strategies/
```

This ensures tests/ and scripts/ are also linted in CI, now that per-file-ignores are configured.

### 4. NO scripts/format.py

**Dropped** per reviewer feedback - Makefile targets are sufficient. Avoids duplication and drift.

---

## Implementation Steps

### Step 1: Update pyproject.toml
- Migrate to standardized `[tool.ruff.lint]` schema
- Add `[tool.ruff.lint.per-file-ignores]` section
- Keep F841 enforced (no blanket ignore)

### Step 2: Update Makefile
- Fix `fmt` target: add `--unsafe-fixes --exit-zero`, add reminder echo
- Add `fmt-check` target for CI
- Update `ci-local` to check all files (`.` instead of specific dirs)

### Step 3: Run Full Formatting
- Execute `make fmt` to format all code
- Manually fix remaining F841 issues (unused variables)

### Step 4: Verify and Test
- Run `make lint` to verify remaining issues are acceptable
- Run `make ci-local` to ensure CI passes

---

## Risk Assessment

| Risk | Mitigation |
|------|------------|
| Ruff config migration breaks | Test with `poetry run ruff check --version` first |
| --unsafe-fixes causes issues | Review diff before commit |
| CI now checks more files | Per-file-ignores handle intentional violations |
| F841 manual fixes tedious | One-time effort, prevents future issues |

---

## Success Criteria

1. `make fmt` completes successfully (exit code 0)
2. `make lint` passes on libs/, apps/, strategies/
3. `make ci-local` passes
4. Ruff config uses consistent schema
5. No blanket F841 ignore (enforced in non-test code)

---

## Files to Modify

1. `pyproject.toml` - Standardize ruff config, add per-file-ignores
2. `Makefile` - Fix fmt target, add fmt-check, update ci-local
3. Multiple source files - Auto-formatted by black/ruff
4. Some test files - Manual F841 fixes where needed

---

## Review Checklist

- [ ] Is the ruff config schema standardized correctly?
- [ ] Are per-file-ignores minimal and appropriate?
- [ ] Is F841 properly enforced (not blanket ignored)?
- [ ] Is Makefile backward compatible?
- [ ] Is ci-local updated to check all files?
- [ ] Is scripts/format.py dropped as recommended?
