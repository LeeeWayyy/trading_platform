# Redis Events and Streams

This document inventories Redis pub/sub channels and stream usage found in the codebase. It is based on direct code scans of `apps/` and `libs/` (tests are referenced only to confirm formats).

## Summary

- **Pub/Sub channels:** 4 (3 global channels + 1 pattern for price updates)
- **Streams:** None found (no `XADD`, `XREAD`, `XGROUP`, or related stream operations in code)
- **Primary schemas:** `libs/redis_client/events.py`, `libs/market_data/types.py`

## Pub/Sub Channels

### `signals.generated`

- **Schema:** `SignalEvent` (`libs/redis_client/events.py`)
  - `event_type`: `"signals.generated"`
  - `timestamp`: `datetime` (UTC, tz-aware)
  - `strategy_id`: `str`
  - `symbols`: `list[str]` (min length 1)
  - `num_signals`: `int` (>= 0)
  - `as_of_date`: `str` (ISO date)
- **Producer:** Signal Service
  - Code path: `apps/signal_service/main.py` publishes after signal generation via `EventPublisher.publish_signal_event()` with fallback buffering on failure.
- **Consumers:** **None found in code** (no `pubsub.subscribe()` or channel listeners in `apps/`/`libs/`).
- **Delivery semantics:** Redis pub/sub (best-effort, no persistence). Publish returns subscriber count; if Redis publish fails in Signal Service, payload is buffered and replayed when Redis recovers.
  - Retry + publish wrapper: `libs/redis_client/client.py`
  - EventPublisher: `libs/redis_client/event_publisher.py`
  - Fallback buffer and replay: `libs/redis_client/fallback_buffer.py`, `apps/signal_service/main.py`

### `orders.executed`

- **Schema:** `OrderEvent` (`libs/redis_client/events.py`)
  - `event_type`: `"orders.executed"`
  - `timestamp`: `datetime` (UTC, tz-aware)
  - `run_id`: `str` (UUID)
  - `strategy_id`: `str`
  - `num_orders`: `int` (>= 0)
  - `num_accepted`: `int` (>= 0)
  - `num_rejected`: `int` (>= 0)
- **Producer:** **Not found in code** (schema defined and channel constant exists in `EventPublisher`).
- **Consumers:** **None found in code**.
- **Delivery semantics:** Redis pub/sub best-effort (no persistence). If implemented, `EventPublisher.publish_order_event()` will publish JSON via Redis.

### `positions.updated`

- **Schema:** `PositionEvent` (`libs/redis_client/events.py`)
  - `event_type`: `"positions.updated"`
  - `timestamp`: `datetime` (UTC, tz-aware)
  - `symbol`: `str`
  - `action`: `str` (one of `buy`, `sell`, `fill`, `partial_fill`)
  - `qty_change`: `int`
  - `new_qty`: `int`
  - `price`: `str` (decimal serialized as string)
  - `strategy_id`: `str`
- **Producer:** **Not found in code** (schema defined and channel constant exists in `EventPublisher`).
- **Consumers:** **None found in code**.
- **Delivery semantics:** Redis pub/sub best-effort (no persistence). If implemented, `EventPublisher.publish_position_event()` will publish JSON via Redis.

### `price.updated.{symbol}`

- **Schema:** `PriceUpdateEvent` (`libs/market_data/types.py`)
  - `event_type`: `"price.updated"`
  - `symbol`: `str`
  - `price`: `Decimal` (mid price)
  - `timestamp`: `str` (ISO timestamp)
- **Producer:** Market Data Service
  - Code path: `libs/market_data/alpaca_stream.py` publishes via `EventPublisher` when quotes arrive; channel is formatted as `price.updated.{symbol}`.
  - Service wiring: `apps/market_data_service/main.py` creates `EventPublisher` and `AlpacaMarketDataStream`.
- **Consumers:** **None found in code**.
- **Delivery semantics:** Redis pub/sub best-effort (no persistence). No fallback buffer on publish failure in market data path.

## Redis Streams

No Redis Streams usage was found in the codebase (`XADD`, `XREAD`, `XGROUP`, `XACK`, etc.). If streams are introduced later, document:
- Stream name(s)
- Message format(s)
- Producers/consumers
- Consumer group semantics and acknowledgment strategy

## Notes and Gaps

- Pub/sub consumers appear to be **planned but not implemented** in code. If any consumers exist outside this repo (ops tools, external services), they are not captured here.
- `EventPublisher` and event schemas are centralized in `libs/redis_client` and should be used by future producers/consumers for consistency.

