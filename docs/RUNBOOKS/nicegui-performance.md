# NiceGUI Performance Tuning Guide

Last updated: 2026-01-03

## Scope

This runbook covers performance tuning for the NiceGUI trading console with a
focus on WebSocket throughput, Redis Pub/Sub efficiency, AG Grid rendering, and
long-lived session memory stability.

## Connection Pool Sizing

### HTTP client (AsyncTradingClient)
- Keep timeouts tight to avoid pile-ups (connect ~2s, total ~5s).
- Limit retries at the client layer; do not allow unbounded retries.
- Prefer connection reuse by keeping the AsyncClient alive for the app lifespan.

### Redis connections
- For Sentinel: tune `REDIS_POOL_MAX_CONNECTIONS` to match expected WS sessions.
- Rule of thumb: pool max connections ~20-30% of peak WS sessions.
- Avoid closing the shared Redis store in per-client cleanup.

## Redis Pub/Sub Optimization

- Use a single listener task per channel per client session.
- Apply backpressure with bounded queues to avoid memory growth.
- Conflate multiple updates into the latest update (reduces UI churn).
- Prefer JSON payloads with stable schema to minimize decode errors.
- Ensure Pub/Sub reconnect logic cleans up old pubsub instances.

## AG Grid Performance Tips

- Always set `getRowId` for stable row identity.
- Use `applyTransaction` instead of full rowData replacement.
- Avoid per-row JS calls in timers; batch updates in one JS execution.
- Keep column renderers lightweight (avoid heavy DOM work per cell).
- Use `autoHeight` only if required; consider fixed-height with virtual scroll for large data.

## Memory Management for Long Sessions

- Register all per-client timers with the client lifecycle manager.
- Cancel timers and tasks on disconnect to prevent leaks.
- Avoid storing per-client state in module-level globals unless keyed by client_id.
- Cache shared market data only if entitlement-neutral and single-worker.
- For multi-worker, move shared caches to Redis or a dedicated service.

## Monitoring Recommendations

### Server-side
- Track active WS connections and per-client memory consumption.
- Emit Pub/Sub listener lag metrics (queue size, drop counts).
- Capture UI update latency (server publish time vs client receipt).

### Client-side
- Track P&L update latency using `Date.now()` vs server `_server_ts`.
- Log reconnect events and disconnect durations.
- Use a periodic health check timer to catch stalled UI updates.

## Load and Soak Testing

- Use `tests/load/web_console_dashboard.js` for staged load (100 users).
- Use `tests/load/web_console_soak.js` for 4-hour stability testing.
- Run in `AUTH_TYPE=dev` or `AUTH_TYPE=basic` (k6 cannot handle mTLS/OAuth2).
- Verify WS session cookie is present before WS connect.

## Troubleshooting Checklist

- High WS connect times: check reverse proxy timeouts and TLS termination.
- Missing updates: confirm Redis Pub/Sub connectivity and channel names.
- UI freezes: confirm AG Grid updates are batched and not full re-renders.
- Memory growth: confirm timers are canceled and listeners are cleaned up.

