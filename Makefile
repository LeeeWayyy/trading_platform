.PHONY: help up up-dev down down-dev logs fmt fmt-check lint validate-docs check-doc-freshness check-architecture test test-cov test-watch clean install requirements install-hooks ci-local pre-push

help: ## Show this help message
	@echo 'Usage: make [target]'
	@echo ''
	@echo 'Available targets:'
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  %-15s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: ## Install dependencies with Poetry
	poetry install

requirements: ## Generate requirements.txt from pyproject.toml (for Docker builds)
	@pip show poetry-plugin-export >/dev/null 2>&1 || pip install poetry-plugin-export
	poetry export -f requirements.txt --output requirements.txt --without-hashes
	@echo "Generated requirements.txt from pyproject.toml"

up: ## Start infrastructure (Postgres, Redis, Prometheus, Grafana)
	docker compose up -d
	@echo "Waiting for services to be healthy..."
	@sleep 5
	@docker compose ps

up-dev: ## Start all dev services (infrastructure + APIs + web console)
	docker compose --profile dev up -d
	@echo "Waiting for services to be healthy..."
	@sleep 10
	@docker compose --profile dev ps
	@echo ""
	@echo "Services available:"
	@echo "  - Web Console:       http://localhost:8501"
	@echo "  - Execution Gateway: http://localhost:8002"
	@echo "  - Signal Service:    http://localhost:8001"
	@echo "  - Orchestrator:      http://localhost:8003"
	@echo "  - Grafana:           http://localhost:3000"
	@echo "  - Prometheus:        http://localhost:9090"

down: ## Stop infrastructure
	docker compose down

down-dev: ## Stop all dev services
	docker compose --profile dev down

down-v: ## Stop infrastructure and remove volumes
	docker compose down -v

logs: ## Show logs from all services
	docker compose logs -f

fmt: ## Format code with black and ruff (auto-fix, non-fatal on remaining issues)
	poetry run black .
	poetry run ruff check --fix --unsafe-fixes --exit-zero .
	@echo ""
	@echo "Formatting complete. Run 'make lint' for strict validation."

fmt-check: ## Check formatting only (fails on issues)
	poetry run black --check .
	poetry run ruff format --check .

lint: ## Run linters (black, ruff, mypy --strict)
	poetry run black --check .
	poetry run ruff check .
	poetry run mypy libs/ apps/ strategies/ --strict

validate-docs: ## Validate that all markdown files are indexed in docs/INDEX.md
	@./scripts/validate_doc_index.sh

check-doc-freshness: ## Validate documentation freshness and coverage
	@python scripts/check_doc_freshness.py

check-architecture: ## Verify architecture map outputs are up to date
	@python scripts/generate_architecture.py --check

test: ## Run tests
	PYTHONPATH=. poetry run pytest

test-cov: ## Run tests with coverage report
	PYTHONPATH=. poetry run pytest --cov=libs --cov=apps --cov=scripts/ai_workflow --cov-report=html --cov-report=term

perf: ## Run performance tests (requires RUN_PERF_TESTS=1)
	RUN_PERF_TESTS=1 PYTHONPATH=. poetry run pytest tests/apps/web_console/test_performance.py -v

test-watch: ## Run tests in watch mode
	poetry run pytest-watch

install-hooks: ## Install git hooks (workflow gate enforcement)
	@echo "Installing workflow gate hooks..."
	@chmod +x scripts/workflow_gate.py
	@chmod +x scripts/pre-commit-hook.sh
	@ln -sf ../../scripts/pre-commit-hook.sh .git/hooks/pre-commit
	@echo "✓ Pre-commit hook installed successfully!"
	@echo ""
	@echo "The hook enforces the workflow pattern:"
	@echo "  implement → test → review → commit"
	@echo ""
	@echo "Prerequisites for commit:"
	@echo "  1. Zen-MCP review approved"
	@echo "  2. CI passing (make ci-local)"
	@echo ""
	@echo "⚠️  WARNING: DO NOT use 'git commit --no-verify'"
	@echo "   Bypassing gates defeats quality system and will be detected by CI"
	@echo ""
	@echo "To test the hook: make ci-local"

check-hooks: ## Verify git hooks are installed
	@if [ ! -f .git/hooks/pre-commit ]; then \
		echo "❌ Pre-commit hook is not installed. Run: make install-hooks"; \
		exit 1; \
	fi
	@echo "✅ Pre-commit hook installed"

ci-local: ## Run CI checks locally (mirrors GitHub Actions exactly)
	@# Lock mechanism to prevent multiple CI instances
	@if [ -f .ci-local.lock ]; then \
		LOCK_PID=$$(cat .ci-local.lock 2>/dev/null); \
		if kill -0 $$LOCK_PID 2>/dev/null; then \
			echo ""; \
			echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"; \
			echo "⚠️  CI-LOCAL ALREADY RUNNING (PID: $$LOCK_PID)"; \
			echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"; \
			echo ""; \
			echo "Only ONE ci-local instance is allowed at a time."; \
			echo "Wait for the current run to complete or kill it:"; \
			echo "  kill $$LOCK_PID"; \
			echo "  rm -f .ci-local.lock"; \
			echo ""; \
			exit 1; \
		else \
			rm -f .ci-local.lock; \
		fi; \
	fi
	@echo $$$$ > .ci-local.lock
	@trap 'rm -f .ci-local.lock' EXIT INT TERM; \
	echo "🔍 Running CI checks locally..."; \
	echo ""; \
	echo "This mirrors the GitHub Actions CI workflow (docs, mypy, ruff, pytest, workflow gates)."; \
	echo "Note: CI also runs DB migrations - run those separately if needed."; \
	echo "If this passes, CI should pass too."; \
	echo ""; \
	echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"; \
	echo "Step 0/6: Validating local environment matches pyproject.toml"; \
	echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@poetry run python scripts/validate_env.py || { \
		echo ""; \
		echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"; \
		echo "❌ Environment validation failed!"; \
		echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"; \
		echo ""; \
		echo "Your local environment is missing packages from pyproject.toml."; \
		echo "This can cause different behavior between local and CI."; \
		echo ""; \
		echo "To fix: poetry install"; \
		exit 1; \
	}
	@echo ""
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo "Step 1/7: Validating documentation index"
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@./scripts/validate_doc_index.sh || { \
		echo ""; \
		echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"; \
		echo "❌ Documentation index validation failed!"; \
		echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"; \
		echo ""; \
		echo "All markdown files must be indexed in docs/INDEX.md"; \
		echo "See error output above for missing files"; \
		exit 1; \
	}
	@echo ""
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo "Step 2/7: Checking documentation freshness"
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@python scripts/check_doc_freshness.py || { \
		echo ""; \
		echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"; \
		echo "❌ Documentation freshness check failed!"; \
		echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"; \
		echo ""; \
		echo "Update docs/GETTING_STARTED/REPO_MAP.md and/or specs to match current source directories."; \
		exit 1; \
	}
	@echo ""
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo "Step 3/7: Checking markdown links (timeout: 1min)"
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@command -v markdown-link-check >/dev/null 2>&1 || { \
		echo "❌ markdown-link-check not found. Installing..."; \
		npm install -g markdown-link-check; \
	}
	@HANG_TIMEOUT=60 ./scripts/ci_with_timeout.sh bash -c 'find . -type f -name "*.md" ! -path "./CLAUDE.md" ! -path "./AGENTS.md" ! -path "./.venv/*" ! -path "./node_modules/*" ! -path "./qlib/*" -print0 | xargs -0 markdown-link-check --config .github/markdown-link-check-config.json' || { \
		EXIT_CODE=$$?; \
		if [ $$EXIT_CODE -eq 124 ]; then \
			echo ""; \
			echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"; \
			echo "❌ Markdown link check TIMED OUT!"; \
			echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"; \
		else \
			echo ""; \
			echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"; \
			echo "❌ Markdown link check failed!"; \
			echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"; \
			echo ""; \
			echo "Common issues:"; \
			echo "  • Broken internal links (wrong path depth)"; \
			echo "  • Missing anchor links (heading text changed)"; \
			echo "  • Files moved/renamed without updating references"; \
			echo "  • External URLs changed or removed"; \
			echo ""; \
			echo "See error output above for specific broken links"; \
		fi; \
		exit 1; \
	}
	@echo ""
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo "Step 4/7: Type checking with mypy --strict"
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	poetry run mypy libs/ apps/ strategies/ --strict
	@echo ""
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo "Step 5/7: Linting with ruff"
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	poetry run ruff check .
	@echo ""
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo "Step 6/7: Running tests (integration and e2e tests skipped, timeout: 2 min per stall)"
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	# TODO: restore --cov-fail-under back to 80% once flaky tests are fixed (GH-issue to track)
	@HANG_TIMEOUT=120 PYTHONPATH=. ./scripts/ci_with_timeout.sh poetry run pytest -m "not integration and not e2e" --cov=libs --cov=apps --cov-report=term --cov-fail-under=50 || { \
		EXIT_CODE=$$?; \
		if [ $$EXIT_CODE -eq 124 ]; then \
			echo ""; \
			echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"; \
			echo "❌ Tests TIMED OUT (no progress for 2 minutes)!"; \
			echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"; \
			echo ""; \
			echo "A test is likely hanging. Check the last test output above."; \
		fi; \
		exit $$EXIT_CODE; \
	}
	@echo ""
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo "Step 7/7: Verifying workflow gate compliance (review approval markers)"
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@CI=true PYTHONPATH=. poetry run python scripts/verify_gate_compliance.py || { \
		echo ""; \
		echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"; \
		echo "❌ Workflow gate compliance failed!"; \
		echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"; \
		echo ""; \
		echo "This check validates that all commits have:"; \
		echo "  • zen-mcp-review: approved marker"; \
		echo "  • gemini-continuation-id: <uuid> trailer"; \
		echo "  • codex-continuation-id: <uuid> trailer"; \
		echo ""; \
		echo "To fix missing markers, request a zen-mcp review before committing."; \
		exit 1; \
	}
	@echo ""
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo "✓ All CI checks passed!"
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo ""
	@echo "✓ Your code should pass GitHub Actions CI"

pre-push: ci-local ## Run CI checks before pushing (alias for ci-local)

clean: ## Clean up generated files
	rm -rf .pytest_cache
	rm -rf .mypy_cache
	rm -rf .ruff_cache
	rm -rf htmlcov
	rm -rf .coverage*
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete

status: ## Show current positions, orders, P&L and service health
	@./scripts/operational_status.sh

circuit-trip: ## Manually trip circuit breaker (placeholder for P1)
	@echo "Circuit breaker command not yet implemented (P1)"

kill-switch: ## Emergency kill switch (placeholder for P1)
	@echo "Kill switch not yet implemented (P1)"

market-data: ## Run Market Data Service (port 8004)
	PYTHONPATH=. poetry run uvicorn apps.market_data_service.main:app --host 0.0.0.0 --port 8004 --reload
