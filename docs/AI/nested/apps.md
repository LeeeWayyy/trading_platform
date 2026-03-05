# Apps Directory Context

Microservice entry points. Each subdirectory is an independent FastAPI service.

## Services

- `signal_service/` — ML model loading, feature computation, signal generation (port 8001)
- `execution_gateway/` — Order submission, TWAP slicing, Alpaca API integration (port 8002)
- `orchestrator/` — Workflow coordination, scheduling, paper run orchestration (port 8003)
- `market_data_service/` — Price feeds, WebSocket client, quote caching (port 8004)
- `web_console_ng/` — NiceGUI web dashboard for monitoring and control (port 8501)
- `execution_gateway/reconciliation/` — Position/order reconciliation between DB and broker
- `alert_worker/` — Alert delivery (email, Slack, SMS) with retry and poison queue
- `auth_service/` — Authentication and session management
- `backtest_worker/` — Backtest execution and result storage
- `model_registry/` — ML model versioning and promotion pipeline

## Key Patterns

- Each service has `main.py` (FastAPI app), `config.py` (Pydantic settings), `schemas.py` (API models)
- Shared code lives in `libs/` — never duplicate logic between services
- Service-specific tests are in `tests/apps/<service_name>/`
- Local make targets: `make up` (start infra), `make down` (stop), `make paper-run` (full run)
