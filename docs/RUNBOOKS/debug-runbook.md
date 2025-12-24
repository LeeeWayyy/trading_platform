# Debug Runbook

Operational troubleshooting notes and recovery steps for local development.

---

## Web Console Pages Empty (Admin/Alerts/Circuit Breaker/Health/Manual Controls)

**Symptoms**
- Pages render but show empty panels or minimal info.
- Health page shows services unreachable.
- Manual Controls show no orders/positions.

**Root Causes**
- Missing FastAPI services (execution_gateway, signal_service, etc.).
- Missing seed data (model_registry, alert_rules, api_keys, audit_log).
- Missing dev env vars (market_data_service requires ALPACA_API_KEY/ALPACA_SECRET_KEY).

**Checklist**
```bash
# 1) Ensure .env has dev environment + Alpaca keys (market_data_service)
rg -n "ENVIRONMENT=dev|ALPACA_API_KEY=|ALPACA_SECRET_KEY=" .env

# 2) Start FastAPI services with .env loaded
set -a; source .env; set +a
source .venv/bin/activate
PYTHONPATH=. poetry run uvicorn apps.execution_gateway.main:app --host 0.0.0.0 --port 8002 > logs/execution_gateway.log 2>&1 &
PYTHONPATH=. poetry run uvicorn apps.signal_service.main:app --host 0.0.0.0 --port 8001 > logs/signal_service.log 2>&1 &
PYTHONPATH=. poetry run uvicorn apps.orchestrator.main:app --host 0.0.0.0 --port 8003 > logs/orchestrator.log 2>&1 &
PYTHONPATH=. poetry run uvicorn apps.market_data_service.main:app --host 0.0.0.0 --port 8004 > logs/market_data_service.log 2>&1 &
PYTHONPATH=. poetry run uvicorn apps.model_registry.main:app --host 0.0.0.0 --port 8005 > logs/model_registry.log 2>&1 &

# 3) Health check
for port in 8001 8002 8003 8004 8005; do echo "== $port =="; curl -sS -m 2 http://localhost:${port}/health || true; echo; done
```

**Seed Model Registry (required for signal_service)**
```bash
# If signal_service fails: "No active model found for strategy: alpha_baseline"
docker exec -i trading_platform_postgres psql -U trader -d trader -c "
INSERT INTO model_registry
  (strategy_name, version, model_path, status, performance_metrics, config, activated_at, created_by, notes)
VALUES
  ('alpha_baseline','local-dev','artifacts/models/alpha_baseline.txt','active',
   '{\"ic\":0.0}','{\"source\":\"local\"}',NOW(),'dev','Seeded for local dev')
ON CONFLICT (strategy_name, version)
DO UPDATE SET model_path=EXCLUDED.model_path, status=EXCLUDED.status,
  activated_at=EXCLUDED.activated_at, performance_metrics=EXCLUDED.performance_metrics,
  config=EXCLUDED.config, notes=EXCLUDED.notes;"
```

**Notes per page**
- **Admin**: API Keys/System Config/Audit Logs require data (create keys/config via UI).
- **Admin Users**: Requires at least one row in `user_roles`.
- **Alerts**: Needs `FEATURE_ALERTS=true` and at least one alert rule.
- **Circuit Breaker**: Requires Redis and `FEATURE_CIRCUIT_BREAKER=true`.
- **Health**: Requires FastAPI services on localhost ports.
- **Manual Controls**: Requires execution_gateway on `http://localhost:8002`.

---

## Web Console Redis Error: `Cannot connect to Redis at localhost:6379`

**Symptom**
Web console pages (Circuit Breaker / Health Monitor) crash with
`Cannot connect to Redis at localhost:6379` inside the container.

**Cause**
The web_console container defaults to `REDIS_HOST=localhost`, which
points to itself. It must use the Docker network hostname `redis`.

**Fix**
```bash
# Ensure web_console has Redis host set
rg -n "REDIS_HOST" docker-compose.yml
# Should include: REDIS_HOST=redis and REDIS_PORT=6379 for web_console

# Recreate web_console to load env
docker compose up -d --force-recreate web_console_dev
```

---

## Docker Build Error: `snapshot ... does not exist`

**Symptom**
`docker compose build` fails with
`failed to stat active key during commit: snapshot ... does not exist`.

**Cause**
BuildKit cache corruption or a stuck buildkit/bake instance.

**Fix**
```bash
# Use classic builder for this build
DOCKER_BUILDKIT=0 docker compose build web_console_dev

# If it keeps failing, restart Docker Desktop and retry.
```
