.PHONY: help up down logs fmt lint validate-docs test test-cov test-watch clean install install-hooks ci-local pre-push

help: ## Show this help message
	@echo 'Usage: make [target]'
	@echo ''
	@echo 'Available targets:'
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  %-15s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: ## Install dependencies with Poetry
	poetry install

up: ## Start infrastructure (Postgres, Redis, Prometheus, Grafana)
	docker compose up -d
	@echo "Waiting for services to be healthy..."
	@sleep 5
	@docker compose ps

down: ## Stop infrastructure
	docker compose down

down-v: ## Stop infrastructure and remove volumes
	docker compose down -v

logs: ## Show logs from all services
	docker compose logs -f

fmt: ## Format code with black and ruff
	poetry run black .
	poetry run ruff check --fix .

lint: ## Run linters (black, ruff, mypy --strict)
	poetry run black --check .
	poetry run ruff check .
	poetry run mypy libs/ apps/ strategies/ --strict

validate-docs: ## Validate that all markdown files are indexed in docs/INDEX.md
	@./scripts/validate_doc_index.sh

test: ## Run tests
	PYTHONPATH=. poetry run pytest

test-cov: ## Run tests with coverage report
	PYTHONPATH=. poetry run pytest --cov=libs --cov=apps --cov-report=html --cov-report=term

test-watch: ## Run tests in watch mode
	poetry run pytest-watch

install-hooks: ## Install git pre-commit hooks (workflow gate enforcement)
	@echo "Installing workflow gate pre-commit hooks..."
	@chmod +x scripts/pre-commit-hook.sh
	@ln -sf ../../scripts/pre-commit-hook.sh .git/hooks/pre-commit
	@echo "✓ Pre-commit hook installed successfully!"
	@echo ""
	@echo "The hook enforces the 4-step workflow pattern:"
	@echo "  implement → test → review → commit"
	@echo ""
	@echo "Prerequisites for commit:"
	@echo "  1. Zen-MCP review approved (clink + gemini → codex)"
	@echo "  2. CI passing (make ci-local)"
	@echo "  3. Current step is 'review'"
	@echo ""
	@echo "⚠️  WARNING: DO NOT use 'git commit --no-verify'"
	@echo "   Bypassing gates defeats quality system and will be detected by CI"
	@echo ""
	@echo "To test the hook: make ci-local"

check-hooks: ## Verify git hooks are installed
	@if [ ! -f .git/hooks/pre-commit ]; then \
		echo "❌ Pre-commit hook not installed. Run: make install-hooks"; \
		exit 1; \
	fi
	@echo "✅ Pre-commit hook installed"

ci-local: ## Run CI checks locally (mirrors GitHub Actions exactly)
	@echo "🔍 Running CI checks locally..."
	@echo ""
	@echo "This mirrors the GitHub Actions CI workflow (mypy, ruff, pytest)."
	@echo "Note: CI also runs DB migrations - run those separately if needed."
	@echo "If this passes, CI should pass too."
	@echo ""
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo "Step 1/3: Type checking with mypy --strict"
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	poetry run mypy libs/ apps/ strategies/ --strict
	@echo ""
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo "Step 2/3: Linting with ruff"
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	poetry run ruff check libs/ apps/ strategies/
	@echo ""
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo "Step 3/3: Running tests (integration and e2e tests skipped)"
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	PYTHONPATH=. poetry run pytest -m "not integration and not e2e" --cov=libs --cov=apps --cov-report=term --cov-fail-under=80
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
	rm -rf .coverage
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
