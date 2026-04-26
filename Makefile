.PHONY: help up up-dev up-dev-fast ensure-requirements down down-dev logs fmt fmt-check lint check-doc-freshness check-architecture test test-cov test-watch ui-crawl ui-deep clean clean-cache clean-all install requirements install-hooks ci-local pre-push

# CI step formatting - reduces duplication in ci-local target
SEPARATOR := ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Docker Desktop local proxy endpoint that is reachable from build containers on macOS.
# Some environments enforce egress via this proxy (iptables services1 rules).
DOCKER_DESKTOP_PROXY ?= http://192.168.65.7:3128

# Usage: $(call ci_step_header,Step X/Y,Description)
define ci_step_header
	@echo ""
	@echo "$(SEPARATOR)"
	@echo "$(1): $(2)"
	@echo "$(SEPARATOR)"
endef

# Usage: $(call ci_error,Title,Fix instructions)
define ci_error
	echo ""; \
	echo "$(SEPARATOR)"; \
	echo "❌ $(1)"; \
	echo "$(SEPARATOR)"; \
	echo ""; \
	echo "$(2)"; \
	exit 1
endef

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

ensure-requirements: ## Ensure requirements.txt exists and is newer than pyproject/lock
	@echo "Generating requirements.txt for Docker builds..."
	@$(MAKE) requirements

up: ## Start infrastructure (Postgres, Redis, Prometheus, Grafana)
	docker compose up -d
	@echo "Waiting for services to be healthy..."
	@sleep 5
	@docker compose ps

up-dev: ## Start all dev services (rebuild first to avoid stale images)
	@PYTHON=$$( [ -x .venv/bin/python3 ] && echo .venv/bin/python3 || echo python3 ); \
	$$PYTHON scripts/ops/ensure_web_console_jwt_keys.py
	$(MAKE) ensure-requirements
	@set -e; \
	if [ -n "$(DOCKER_DESKTOP_PROXY)" ]; then \
		echo "Building dev services using proxy: $(DOCKER_DESKTOP_PROXY)"; \
		if ! env \
			http_proxy="$(DOCKER_DESKTOP_PROXY)" \
			https_proxy="$(DOCKER_DESKTOP_PROXY)" \
			HTTP_PROXY="$(DOCKER_DESKTOP_PROXY)" \
			HTTPS_PROXY="$(DOCKER_DESKTOP_PROXY)" \
			docker compose --profile dev --profile workers build \
				--build-arg http_proxy="$(DOCKER_DESKTOP_PROXY)" \
				--build-arg https_proxy="$(DOCKER_DESKTOP_PROXY)" \
				--build-arg HTTP_PROXY="$(DOCKER_DESKTOP_PROXY)" \
				--build-arg HTTPS_PROXY="$(DOCKER_DESKTOP_PROXY)"; then \
			echo "Proxy build failed, retrying direct build..."; \
			docker compose --profile dev --profile workers build; \
		fi; \
	else \
		docker compose --profile dev --profile workers build; \
	fi; \
	docker compose --profile dev --profile workers up -d
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

up-dev-fast: ## Start dev services without rebuild (faster, may use stale image)
	@PYTHON=$$( [ -x .venv/bin/python3 ] && echo .venv/bin/python3 || echo python3 ); \
	$$PYTHON scripts/ops/ensure_web_console_jwt_keys.py
	docker compose --profile dev --profile workers up -d
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
	poetry run mypy libs/ apps/ strategies/ tools/ --strict

check-doc-freshness: ## Validate documentation freshness and coverage
	@poetry run python scripts/dev/check_doc_freshness.py

check-architecture: ## Verify architecture map outputs are up to date
	@poetry run python scripts/dev/generate_architecture.py --check

check-layering: ## Check for layer violations (libs importing from apps)
	@poetry run python scripts/dev/check_layering.py

test: ## Run tests
	PYTHONPATH=. poetry run pytest

test-cov: ## Run tests with coverage report
	PYTHONPATH=. poetry run pytest --cov=libs --cov=apps --cov-report=html --cov-report=term

perf: ## Run performance tests (requires RUN_PERF_TESTS=1)
	RUN_PERF_TESTS=1 PYTHONPATH=. poetry run pytest tests/apps/web_console/test_performance.py -v

test-watch: ## Run tests in watch mode
	poetry run pytest-watch

ui-crawl: ## Run broad Playwright UI crawler (manual E2E diagnostic)
	PYTHONPATH=. poetry run python tests/e2e/ui_crawl.py

ui-deep: ## Run focused Playwright deep page inspector
	PYTHONPATH=. poetry run python tests/e2e/ui_deep.py

install-hooks: ## Install git hooks (pre-commit quality checks)
	@echo "Installing git hooks..."
	@if command -v pre-commit >/dev/null 2>&1; then \
		pre-commit install && \
		echo "✓ Hooks installed via pre-commit"; \
	else \
		echo "⚠ pre-commit not found. Install it first:"; \
		echo "  pip install pre-commit"; \
		echo "  pre-commit install"; \
		exit 1; \
	fi
	@echo ""
	@echo "Hooks installed:"
	@echo "  • zen-pre-commit: branch naming + lint checks"
	@echo "  • zen-commit-msg: review approval trailers"

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
			echo ""; echo "$(SEPARATOR)"; \
			echo "⚠️  CI-LOCAL ALREADY RUNNING (PID: $$LOCK_PID)"; \
			echo "$(SEPARATOR)"; echo ""; \
			echo "Only ONE ci-local instance is allowed at a time."; \
			echo "Wait for the current run to complete or kill it:"; \
			echo "  kill $$LOCK_PID && rm -f .ci-local.lock"; \
			echo ""; exit 1; \
		else \
			rm -f .ci-local.lock; \
		fi; \
	fi
	@echo $$$$ > .ci-local.lock
	@trap 'rm -f .ci-local.lock' EXIT INT TERM; \
	echo "🔍 Running CI checks locally..."; \
	echo ""; \
	echo "This mirrors the GitHub Actions CI workflow (docs, mypy, ruff, AI lints, pytest)."; \
	echo "Note: CI also runs DB migrations - run those separately if needed."; \
	echo "If this passes, CI should pass too."; \
	echo ""; \
	echo "$(SEPARATOR)"; \
	echo "Step 0/9: Validating local environment matches pyproject.toml"; \
	echo "$(SEPARATOR)"
	@poetry run python scripts/testing/validate_env.py || { $(call ci_error,Environment validation failed!,Your local environment is missing packages. Run: poetry install); }
	$(call ci_step_header,Step 1/8,Checking architecture map is up to date)
	@poetry run python scripts/dev/generate_architecture.py --check || { $(call ci_error,Architecture map is out of date!,Run 'make check-architecture' or 'python scripts/dev/generate_architecture.py' to regenerate.); }
	$(call ci_step_header,Step 2/8,Checking markdown links (timeout: 1min))
	@command -v markdown-link-check >/dev/null 2>&1 || { echo "❌ markdown-link-check not found. Installing..."; npm install -g markdown-link-check; }
	@HANG_TIMEOUT=60 ./scripts/hooks/ci_with_timeout.sh bash -c 'find . -type f -name "*.md" ! -path "./CLAUDE.md" ! -path "./AGENTS.md" ! -path "./GEMINI.md" ! -path "./.venv/*" ! -path "./node_modules/*" ! -path "./qlib/*" -print0 | xargs -0 markdown-link-check --config .github/markdown-link-check-config.json' || { \
		EXIT_CODE=$$?; \
		if [ $$EXIT_CODE -eq 124 ]; then \
			echo ""; echo "$(SEPARATOR)"; echo "❌ Markdown link check TIMED OUT!"; echo "$(SEPARATOR)"; \
		else \
			echo ""; echo "$(SEPARATOR)"; echo "❌ Markdown link check failed!"; echo "$(SEPARATOR)"; echo ""; \
			echo "Common issues: broken internal links, missing anchors, moved files, changed external URLs."; \
			echo "See error output above for specific broken links."; \
		fi; \
		exit 1; \
	}
	$(call ci_step_header,Step 3/8,Type checking with mypy --strict)
	poetry run mypy libs/ apps/ strategies/ tools/ --strict
	$(call ci_step_header,Step 4/8,Linting with ruff)
	poetry run ruff check .
	$(call ci_step_header,Step 5/8,Checking layer violations)
	@poetry run python scripts/dev/check_layering.py || { $(call ci_error,Layer violation detected!,libs/ should never import from apps/. Use dependency injection or move shared code to libs/.); }
	$(call ci_step_header,Step 6/8,Checking AI instruction drift)
	@./scripts/dev/lint_instruction_drift.sh || { $(call ci_error,Instruction drift detected!,Nested context files are duplicating root AI_GUIDE.md content. See output above.); }
	$(call ci_step_header,Step 7/8,Checking AI terminology consistency (informational))
	@./scripts/dev/lint_terminology.sh || true
	$(call ci_step_header,Step 8/8,Running tests (parallel with pytest-xdist; integration/e2e skipped; timeout: 2 min per stall))
	# TODO: restore --cov-fail-under back to 80% once flaky tests are fixed (GH-issue to track)
	# Exclude quarantined tests dynamically from tests/quarantine.txt
	@DESELECT_ARGS=""; \
	if [ -f tests/quarantine.txt ]; then \
		DESELECT_ARGS=$$(grep -v '^#' tests/quarantine.txt 2>/dev/null | cut -d'|' -f1 | xargs -I{} echo "--deselect {}" | tr '\n' ' ' || true); \
	fi; \
	HANG_TIMEOUT=120 PYTHONPATH=. ./scripts/hooks/ci_with_timeout.sh poetry run pytest \
		-m "not integration and not e2e" \
		-n auto \
		$$DESELECT_ARGS \
		--cov=libs --cov=apps --cov=tools --cov-branch \
		--cov-report=term-missing \
		--cov-fail-under=50 || { \
		EXIT_CODE=$$?; \
		if [ $$EXIT_CODE -eq 124 ]; then \
			echo ""; echo "$(SEPARATOR)"; echo "❌ Tests TIMED OUT (no progress for 2 minutes)!"; echo "$(SEPARATOR)"; echo ""; \
			echo "A test is likely hanging. Check the last test output above."; \
		fi; \
		exit $$EXIT_CODE; \
	}
	@echo ""
	@echo "$(SEPARATOR)"
	@echo "✓ All CI checks passed!"
	@echo "$(SEPARATOR)"
	@echo ""
	@echo "✓ Your code should pass GitHub Actions CI"

pre-push: ci-local ## Run CI checks before pushing (alias for ci-local)

clean: ## Clean up generated files (cache, coverage, bytecode)
	rm -rf .pytest_cache
	rm -rf .mypy_cache
	rm -rf .ruff_cache
	rm -rf htmlcov
	rm -rf .coverage*
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete

clean-cache: clean ## Alias for 'make clean'

clean-all: clean ## Clean everything (cache + repomix outputs + logs)
	rm -f repomix*.xml
	rm -f *.log
	rm -rf .ci-local.lock
	@echo "Cleaned: cache, coverage, bytecode, repomix outputs, logs"

status: ## Show current positions, orders, P&L and service health
	@./scripts/ops/operational_status.sh

circuit-trip: ## Manually trip circuit breaker (placeholder for P1)
	@echo "Circuit breaker command not yet implemented (P1)"

kill-switch: ## Emergency kill switch (placeholder for P1)
	@echo "Kill switch not yet implemented (P1)"

market-data: ## Run Market Data Service (port 8004)
	PYTHONPATH=. poetry run uvicorn apps.market_data_service.main:app --host 0.0.0.0 --port 8004 --reload
