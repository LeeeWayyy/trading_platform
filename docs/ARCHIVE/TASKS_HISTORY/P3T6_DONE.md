# P3T6: Docker Infrastructure and Runbook Fixes

**Status:** ✅ Complete

## Overview

Fix Docker container issues discovered during local development setup and update runbook documentation with troubleshooting steps.

## Priority

**Phase 3 Task 6** - Infrastructure fixes

## Problem Statement

When setting up the trading platform locally via Docker Compose, several issues were encountered:

1. **Web Console Docker Container Failures**
   - Missing `prometheus-client` dependency
   - Missing `libs` folder in Docker image
   - FastAPI import errors in Streamlit context

2. **Database Configuration Issues**
   - Port conflict with local PostgreSQL (5432)
   - Wrong DATABASE_URL format for psycopg3

3. **Loki 3.0.0 Compatibility**
   - Breaking changes in Loki 3.0.0 configuration
   - `shared_store` deprecated, requires `delete_request_store`
   - `allow_structured_metadata` must be disabled for boltdb-shipper

4. **Documentation Gaps**
   - No troubleshooting section in MAIN_RUNBOOK
   - No logging architecture documentation

## Scope

### Files Modified

1. **apps/web_console/Dockerfile**
   - Add `COPY libs /app/libs` for shared libraries
   - Change CMD to use `python -m streamlit` (pip --target doesn't install scripts to PATH)

2. **apps/web_console/requirements.txt**
   - Add `prometheus-client>=0.17.0` for metrics support

3. **libs/common/network_utils.py**
   - Use `TYPE_CHECKING` for FastAPI imports
   - Lazy import `HTTPException` inside functions
   - String annotations `"Request"` for type hints

4. **docker-compose.yml**
   - PostgreSQL port: 5433:5432 (avoid local conflict)
   - Redis port exposed for local dev
   - Loki upgraded to 3.0.0 with user: "0" for volume permissions
   - Promtail upgraded to 3.0.0

5. **infra/loki/loki-config.yml**
   - Add `allow_structured_metadata: false`
   - Change `shared_store` to `delete_request_store`
   - Add `storage_config` for delete requests

6. **.env.example**
   - Update DATABASE_URL to port 5433
   - Better documentation sections
   - Add WEB_CONSOLE_USER/PASSWORD

7. **apps/*/main.py and config.py files**
   - Update default DATABASE_URL to `postgresql://trader:trader@localhost:5433/trader`

8. **config/settings.py**
   - Update default to psycopg3 format

9. **docs/RUNBOOKS/MAIN_RUNBOOK.md** (new section)
   - Add Web Console Docker troubleshooting
   - Add logging architecture documentation

## Testing

1. Rebuild Web Console Docker image
2. Verify container starts healthy
3. Verify logging stack (Grafana → Loki → Promtail)
4. Run `make ci-local` for lint/test validation

## Acceptance Criteria

- [ ] Web Console Docker container starts without import errors
- [ ] Health check passes: `curl http://localhost:8501/_stcore/health` returns "ok"
- [ ] Loki container starts without errors
- [ ] DATABASE_URL defaults work with psycopg3
- [ ] MAIN_RUNBOOK includes troubleshooting section
- [ ] All CI checks pass

## Related Tasks

- P3T1-P3T5: Previous Phase 3 fixes
- Infrastructure setup documentation

## Estimated Effort

Small - configuration and documentation fixes
