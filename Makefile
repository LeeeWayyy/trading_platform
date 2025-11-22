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

install-hooks: ## Install git hooks (workflow gate enforcement + commit marker automation)
	@echo "Installing workflow gate hooks..."
	@chmod +x scripts/workflow_gate.py
	@chmod +x scripts/pre-commit-hook.sh
	@chmod +x scripts/prepare-commit-msg-hook.sh
	@chmod +x scripts/post-commit-hook.sh
	@ln -sf ../../scripts/pre-commit-hook.sh .git/hooks/pre-commit
	@ln -sf ../../scripts/prepare-commit-msg-hook.sh .git/hooks/prepare-commit-msg
	@ln -sf ../../scripts/post-commit-hook.sh .git/hooks/post-commit
	@echo "✓ Pre-commit hook installed successfully!"
	@echo "✓ Prepare-commit-msg hook installed successfully!"
	@echo "✓ Post-commit hook installed successfully!"
	@echo ""
	@echo "The hooks enforce the 4-step workflow pattern:"
	@echo "  implement → test → review → commit"
	@echo ""
	@echo "Installed hooks:"
	@echo "  • pre-commit: Validates workflow gates (review approval + CI passing)"
	@echo "  • prepare-commit-msg: Automatically adds zen-mcp review markers"
	@echo "  • post-commit: Resets workflow state for next component"
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
	@if [ ! -f .git/hooks/pre-commit ] || [ ! -f .git/hooks/prepare-commit-msg ] || [ ! -f .git/hooks/post-commit ]; then \
		echo "❌ One or more git hooks are not installed. Run: make install-hooks"; \
		exit 1; \
	fi
	@echo "✅ All git hooks installed (pre-commit, prepare-commit-msg, post-commit)"

ci-local: ## Run CI checks locally (mirrors GitHub Actions exactly)
	@echo "🔍 Running CI checks locally..."
	@echo ""
	@echo "This mirrors the GitHub Actions CI workflow (docs, mypy, ruff, pytest, workflow gates)."
	@echo "Note: CI also runs DB migrations - run those separately if needed."
	@echo "If this passes, CI should pass too."
	@echo ""
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo "Step 1/6: Validating documentation index"
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
	@echo "Step 2/6: Checking markdown links"
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@command -v markdown-link-check >/dev/null 2>&1 || { \
		echo "❌ markdown-link-check not found. Installing..."; \
		npm install -g markdown-link-check; \
	}
	@find . -type f -name "*.md" ! -path "./CLAUDE.md" ! -path "./AGENTS.md" ! -path "./.venv/*" ! -path "./node_modules/*" -print0 | \
		xargs -0 markdown-link-check --config .github/markdown-link-check-config.json || { \
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
		exit 1; \
	}
	@echo ""
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo "Step 3/6: Type checking with mypy --strict"
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	poetry run mypy libs/ apps/ strategies/ --strict
	@echo ""
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo "Step 4/6: Linting with ruff"
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	poetry run ruff check libs/ apps/ strategies/
	@echo ""
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo "Step 5/6: Running tests (integration and e2e tests skipped)"
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	PYTHONPATH=. poetry run pytest -m "not integration and not e2e" --cov=libs --cov=apps --cov-report=term --cov-fail-under=80
	@echo ""
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo "Step 6/6: Verifying workflow gate compliance (Review-Hash validation)"
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@CI=true PYTHONPATH=. python3 scripts/verify_gate_compliance.py || { \
		echo ""; \
		echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"; \
		echo "❌ Workflow gate compliance failed!"; \
		echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"; \
		echo ""; \
		echo "This check validates that all commits have:"; \
		echo "  • Valid Review-Hash trailers (cryptographic proof of review)"; \
		echo "  • Zen-MCP review approval markers"; \
		echo ""; \
		echo "To fix missing Review-Hash trailers:"; \
		echo "  1. Compute hash: python3 libs/common/hash_utils.py COMMIT_SHA"; \
		echo "  2. Amend commit: git commit --amend --no-verify --trailer \"Review-Hash: <hash>\""; \
		echo ""; \
		echo "See Component A2.1 (P1T13-F5) for details"; \
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
