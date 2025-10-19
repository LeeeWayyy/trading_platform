.PHONY: help up down logs fmt lint test test-cov clean install

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

lint: ## Run linters (black, ruff, mypy)
	poetry run black --check .
	poetry run ruff check .
	poetry run mypy libs/ apps/ strategies/

test: ## Run tests
	poetry run pytest

test-cov: ## Run tests with coverage report
	poetry run pytest --cov=libs --cov=apps --cov-report=html --cov-report=term

test-watch: ## Run tests in watch mode
	poetry run pytest-watch

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
