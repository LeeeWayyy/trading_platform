# Repo Map

- apps/
  - signal_service/ — loads model, emits target weights, polls model registry
  - execution_gateway/ — Alpaca client, idempotent orders, dry-run flag, webhooks
  - reconciler/ — compares DB vs broker, heals drift
  - risk_manager/ — pre/post-trade checks, circuit breaker integration
  - cli/ — operational scripts for status, breakers, kill switch
- strategies/
  - alpha_baseline/ — features.py, model.py, pipeline.py (Qlib-based)
- infra/
  - docker-compose.yml, prometheus/, grafana/
- db/
  - migrations/ — alembic or sql migrations
- docs/ — this directory
- prompts/ — guidance for AI coding tools
