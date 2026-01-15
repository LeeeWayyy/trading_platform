# libs/core

## Identity
- **Type:** Library Group (Core Infrastructure)
- **Location:** `libs/core/`

## Overview
Core infrastructure libraries providing foundational services for the trading platform:

- **common/** - Common utilities, exceptions, helpers, and structured logging
- **health/** - Health check and Prometheus latency clients
- **redis_client/** - Redis connection pool and utilities for caching and pub/sub

## Libraries

### libs/core/common
See [libs/common.md](./common.md) for detailed specification.

**Purpose:** Common utilities, exceptions, and helpers used across all services.

**Key Features:**
- Centralized structured logging (JSON, Loki integration)
- API authentication dependency
- Rate limiting dependency
- Secrets management interface
- Custom exception classes

### libs/core/health
See [libs/health.md](./health.md) for detailed specification.

**Purpose:** Health check and Prometheus latency clients with cached, staleness-aware responses.

**Key Features:**
- Health check aggregation
- Cached health responses
- Prometheus latency tracking

### libs/core/redis_client
See [libs/redis_client.md](./redis_client.md) for detailed specification.

**Purpose:** Redis connection pool and utilities.

**Key Features:**
- Feature caching
- Event pub/sub (SignalEvent, OrderEvent)
- Circuit breaker state storage
- Fallback buffer for resilience

## Dependencies
- **Internal:** None (core layer)
- **External:** Redis, structlog, prometheus_client

## Related Specs
- Individual library specs listed above
- [../infrastructure/redis.md](../infrastructure/redis.md) - Redis infrastructure

## Metadata
- **Last Updated:** 2026-01-14
- **Source Files:** `libs/core/` (group index)
- **ADRs:** N/A
