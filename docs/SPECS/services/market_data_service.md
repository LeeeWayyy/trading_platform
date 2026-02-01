# Market Data Service

<!-- Last reviewed: 2026-02-01 - P6T10 PR: market_data routes and schemas updates -->

## Identity
- **Type:** Service
- **Port:** 8004
- **Container:** N/A

## Interface
### Public API Endpoints
| Endpoint | Method | Parameters | Returns |
|----------|--------|------------|---------|
| `/health` | GET | None | `HealthResponse` |
| `/api/v1/subscribe` | POST | `SubscribeRequest` (symbols[]) | `SubscribeResponse` |
| `/api/v1/subscribe/{symbol}` | DELETE | Path `symbol` | `UnsubscribeResponse` |
| `/api/v1/subscriptions` | GET | None | `SubscriptionsResponse` |
| `/api/v1/subscriptions/stats` | GET | None | Subscription manager stats JSON |
| `/api/v1/adv/{symbol}` | GET | Path `symbol` | `ADVResponse` (average_daily_volume, as_of) |
| `/metrics` | GET | None | Prometheus metrics |

## Behavioral Contracts
### Key Functions
#### subscribe_symbols(request: SubscribeRequest) -> SubscribeResponse
**Purpose:** Subscribe to real-time data for a list of symbols.

**Preconditions:**
- WebSocket stream is initialized.
- `symbols` is non-empty.

**Postconditions:**
- Symbols are subscribed on the Alpaca stream.
- Metrics updated for subscription count.

**Behavior:**
1. Validate stream and input.
2. Call `stream.subscribe_symbols`.
3. Return updated subscription counts.

**Raises:**
- `HTTPException 400` if symbols list is empty.
- `HTTPException 503` if stream not initialized.
- `HTTPException 500` on subscription failures.

#### unsubscribe_symbol(symbol: str) -> UnsubscribeResponse
**Purpose:** Remove a symbol subscription.

**Preconditions:**
- WebSocket stream initialized.

**Postconditions:**
- Symbol unsubscribed; subscription count updated.

#### health_check() -> HealthResponse
**Purpose:** Report WebSocket connection and subscription state.

**Preconditions:**
- Stream initialized; otherwise returns 503.

### Invariants
- Health returns 503 when the WebSocket stream is not initialized.
- Subscription stats mirror stream subscription state.
- Auto-subscription sync loop runs when `PositionBasedSubscription` is configured.

## Data Flow
```
Alpaca WebSocket -> AlpacaMarketDataStream -> Redis cache/events
                                       |
                                       v
                               Subscription Manager
```
- **Input format:** REST subscription requests.
- **Output format:** JSON status responses.
- **Side effects:** Redis cache updates, Redis event publishing, WebSocket subscriptions.

## Dependencies
- **Internal:** `apps/market_data_service/position_sync.py`, `apps/market_data_service/config.py`, `libs.market_data`, `libs.redis_client`.
- **External:** Alpaca market data WebSocket, Redis, Prometheus.

## Configuration
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ALPACA_API_KEY` | Yes | none | Alpaca API key |
| `ALPACA_SECRET_KEY` | Yes | none | Alpaca secret |
| `ALPACA_BASE_URL` | No | `https://paper-api.alpaca.markets` | Alpaca base URL |
| `SERVICE_NAME` | No | `market-data-service` | Service name |
| `PORT` | No | `8004` | Service port |
| `LOG_LEVEL` | No | `INFO` | Log level |
| `REDIS_HOST` | No | `localhost` | Redis host |
| `REDIS_PORT` | No | `6379` | Redis port |
| `REDIS_DB` | No | `0` | Redis DB |
| `REDIS_PASSWORD` | No | `None` | Redis password |
| `PRICE_CACHE_TTL` | No | `300` | Quote cache TTL seconds |
| `MAX_RECONNECT_ATTEMPTS` | No | `10` | WebSocket reconnect attempts |
| `RECONNECT_BASE_DELAY` | No | `5` | Reconnect backoff base seconds |
| `EXECUTION_GATEWAY_URL` | No | `http://localhost:8002` | For auto-subscribe by positions |
| `SUBSCRIPTION_SYNC_INTERVAL` | No | `300` | Auto-subscription sync interval |

## Observability (Services only)
### Health Check
- **Endpoint:** `/health`
- **Checks:** WebSocket connection status, subscription counts, reconnect stats.

### Metrics
- `market_data_subscription_requests_total{operation,status}`
- `market_data_subscription_duration_seconds`
- `market_data_subscribed_symbols_current`
- `market_data_websocket_messages_received_total{message_type}`
- `market_data_position_syncs_total{status}`
- `market_data_websocket_connection_status`
- `market_data_redis_connection_status`
- `market_data_reconnect_attempts_total`

## Security
- **Auth Required:** No
- **Auth Method:** N/A
- **Data Sensitivity:** Internal
- **RBAC Roles:** N/A

## Testing
- **Test Files:** `tests/apps/market_data_service/`
- **Run Tests:** `pytest tests/apps/market_data_service -v`
- **Coverage:** N/A

## Usage Examples
### Example 1: Health check
```bash
curl -s http://localhost:8004/health
```

### Example 2: Subscribe symbols
```bash
curl -s -X POST http://localhost:8004/api/v1/subscribe   -H 'Content-Type: application/json'   -d '{"symbols":["AAPL","MSFT"]}'
```

## Edge Cases & Boundaries
| Scenario | Input | Expected Behavior |
|----------|-------|-------------------|
| Empty symbols list | `{"symbols":[]}` | 400 validation error. |
| Stream not initialized | Startup incomplete | 503 health/subscribe response. |
| Duplicate symbols | Re-subscribe existing | Returns current subscription stats. |

## Known Issues & TODO
| Issue | Severity | Description | Tracking |
|-------|----------|-------------|----------|
| None | - | No known issues | - |

## Related Specs
- `execution_gateway.md`
- `../libs/redis_client.md`
- `../libs/market_data.md`

## Metadata
- **Last Updated:** 2026-01-29 (Type annotation fixes for dependencies)
- **Source Files:** `apps/market_data_service/main.py`, `apps/market_data_service/config.py`, `apps/market_data_service/position_sync.py`, `apps/market_data_service/schemas.py`, `apps/market_data_service/routes/market_data.py`, `apps/market_data_service/api/dependencies.py`, `libs/market_data`
- **ADRs:** N/A
