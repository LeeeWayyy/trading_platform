---
name: architecture-overview
description: Architecture overview — service design, data flows, concurrency patterns, reconciliation. Trigger on architecture, service design, data flows, cross-service, microservices, Redis, Postgres, reconciliation, transactions, atomic.
---

# Architecture Overview

Platform architecture reference for the Qlib + Alpaca trading system.

## Multi-Service Design

Microservices pattern with FastAPI communicating via:
- **Redis** Streams/pub-sub for events and circuit breaker state
- **Postgres** for persistent state (orders, positions, snapshots, model registry)
- **Redis** for online features and quote caching

## Key Data Flows

**Signal Generation:**
```
Model Registry -> Signal Service -> Target Weights -> Risk Manager -> Execution Gateway
```

**Order Execution:**
```
Execution Gateway -> Alpaca API -> Webhook -> Position Tracker -> Reconciler
```

**Circuit Breaker:**
```
Post-Trade Monitor -> Redis Breaker State -> All Services Check Before Action
```

## Critical Patterns

| Pattern | Implementation |
|---------|----------------|
| **Idempotency** | `client_order_id = hash(symbol + side + qty + price + strategy + date)[:24]` |
| **Circuit Breaker** | Check `redis.get("cb:state") != b"TRIPPED"` before every order |
| **Risk Check** | Validate `abs(current_pos + order.qty) <= limits.max_pos_per_symbol` |
| **Feature Parity** | Share code between research/production (never duplicate logic) |

## Concurrency Invariants

- **Redis WATCH/MULTI/EXEC** for atomic operations on shared state
- **DB transactions** for all state changes (orders, positions, audit)
- **Read-modify-write** sequences must be atomic — never read, compute, then write without a transaction guard

## Reconciliation

Boot-time and periodic reconciliation (every 15 minutes):
1. Pull broker positions/orders via Alpaca API
2. Diff against database state
3. Heal discrepancies (cancel stale orders >15m, adjust position records)
4. Alert on unresolvable failures
5. Unlock service after successful reconciliation

## Structured Logging

All services use JSON-structured logging with mandatory context fields:
- `strategy_id` — which strategy produced the action
- `client_order_id` — idempotency key for order tracking
- `symbol` — the security being traded
- `trace_id` — distributed tracing across service calls

## Service Ports

| Service | Port |
|---------|------|
| Signal Service | 8001 |
| Execution Gateway | 8002 |
| Orchestrator | 8003 |
| Market Data Service | 8004 |
| Web Console | 8501 |

## Key Directories

- `apps/` — Microservice entry points (one per service)
- `libs/` — Shared libraries (risk management, data pipeline, common utilities)
- `strategies/` — Trading strategies (alpha models, feature definitions)
- `infra/` — Docker Compose, Grafana dashboards, Prometheus config
- `db/` — Alembic migrations and schema
