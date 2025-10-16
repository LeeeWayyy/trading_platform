# Ops Runbook

## Daily
- `make status` — check positions, open orders, P&L
- Review Grafana alerts (data freshness, API errors, DD)

## Incidents
- **Trip breaker:** `make circuit-trip`
- **Flatten:** `make kill-switch`
- **Data stale:** run replay test; switch to DRY_RUN if needed

## Recovery
- Restart services; ensure reconciler completes; verify breaker state is OPEN.
