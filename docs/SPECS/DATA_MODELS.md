# Data Models

This document catalogs application data models discovered in source code and SQL migrations. It covers Pydantic models, dataclasses, TypedDicts, DuckDB catalog usage, and SQL tables.

**Sources scanned:** `apps/`, `libs/`, `strategies/`, `migrations/`, `db/migrations/`.

## DuckDB / OLAP Catalog

- `libs/duckdb_catalog.py`: DuckDB views are created via `register_table(table_name, parquet_path)`; schemas are derived from Parquet files at query time. No static schema definitions are present in code.

## Domains

### Orders & Execution

#### Application Models

- `FatFingerBreach` (dataclass) — `apps/execution_gateway/fat_finger_validator.py`
  - `threshold_type`: `ThresholdType` — No description in code.
  - `limit`: `Decimal | int | None` — No description in code.
  - `actual`: `Decimal | int | None` — No description in code.
  - `metadata`: `dict[str, Any]` — No description in code.

- `FatFingerResult` (dataclass) — `apps/execution_gateway/fat_finger_validator.py`
  - `breached`: `bool` — No description in code.
  - `breaches`: `tuple[FatFingerBreach, ...]` — No description in code.
  - `thresholds`: `FatFingerThresholds` — No description in code.
  - `notional`: `Decimal | None` — No description in code.
  - `adv`: `int | None` — No description in code.
  - `adv_pct`: `Decimal | None` — No description in code.
  - `price`: `Decimal | None` — No description in code.

- `RecoveryState` (dataclass) — `apps/execution_gateway/recovery_manager.py`
  - `kill_switch`: `KillSwitch | None` — No description in code. (default: `None`)
  - `circuit_breaker`: `CircuitBreaker | None` — No description in code. (default: `None`)
  - `position_reservation`: `PositionReservation | None` — No description in code. (default: `None`)
  - `slice_scheduler`: `SliceScheduler | None` — No description in code. (default: `None`)
  - `kill_switch_unavailable`: `bool` — No description in code. (default: `True`)
  - `circuit_breaker_unavailable`: `bool` — No description in code. (default: `True`)
  - `position_reservation_unavailable`: `bool` — No description in code. (default: `True`)
  - `_kill_switch_lock`: `threading.Lock` — No description in code. (default: `field(default_factory=threading.Lock)`)
  - `_circuit_breaker_lock`: `threading.Lock` — No description in code. (default: `field(default_factory=threading.Lock)`)
  - `_position_reservation_lock`: `threading.Lock` — No description in code. (default: `field(default_factory=threading.Lock)`)
  - `_recovery_lock`: `threading.Lock` — No description in code. (default: `field(default_factory=threading.Lock)`)

- `ConfigResponse` (pydantic) — `apps/execution_gateway/schemas.py`
  - `service`: `str` — Service name (default: `Field(..., description='Service name')`)
  - `version`: `str` — Service version (default: `Field(..., description='Service version')`)
  - `environment`: `str` — Environment (dev, staging, production) (default: `Field(..., description='Environment (dev, staging, production)')`)
  - `dry_run`: `bool` — Dry-run mode enabled (no real orders) (default: `Field(..., description='Dry-run mode enabled (no real orders)')`)
  - `alpaca_paper`: `bool` — Alpaca paper trading mode (default: `Field(..., description='Alpaca paper trading mode')`)
  - `circuit_breaker_enabled`: `bool` — Circuit breaker feature enabled (default: `Field(..., description='Circuit breaker feature enabled')`)
  - `liquidity_check_enabled`: `bool` — Liquidity-aware slicing enabled (ADV-based limits) (default: `Field(..., description='Liquidity-aware slicing enabled (ADV-based limits)')`)
  - `max_slice_pct_of_adv`: `float` — Max slice size as pct of ADV when liquidity checks enabled (default: `Field(..., description='Max slice size as pct of ADV when liquidity checks enabled')`)
  - `timestamp`: `datetime` — Response timestamp (UTC) (default: `Field(..., description='Response timestamp (UTC)')`)

- `DailyPerformanceResponse` (pydantic) — `apps/execution_gateway/schemas.py`
  - `daily_pnl`: `list[DailyPnL]` — No description in code.
  - `total_realized_pl`: `Decimal` — No description in code.
  - `max_drawdown_pct`: `Decimal` — No description in code.
  - `start_date`: `date` — No description in code.
  - `end_date`: `date` — No description in code.
  - `data_source`: `str` — No description in code. (default: `'realized_only'`)
  - `note`: `str` — No description in code. (default: `'Shows realized P&L from closed positions. Unrealized P&L is not included.'`)
  - `data_available_from`: `date | None` — No description in code.
  - `last_updated`: `datetime` — No description in code.

- `DailyPnL` (pydantic) — `apps/execution_gateway/schemas.py`
  - `date`: `date` — No description in code.
  - `realized_pl`: `Decimal` — No description in code.
  - `cumulative_realized_pl`: `Decimal` — No description in code.
  - `peak_equity`: `Decimal` — No description in code.
  - `drawdown_pct`: `Decimal` — No description in code.
  - `closing_trade_count`: `int` — No description in code.

- `ErrorResponse` (pydantic) — `apps/execution_gateway/schemas.py`
  - `error`: `str` — No description in code.
  - `detail`: `str | None` — No description in code. (default: `None`)
  - `timestamp`: `datetime` — No description in code.

- `FatFingerThresholds` (pydantic) — `apps/execution_gateway/schemas.py`
  - `max_notional`: `Decimal | None` — Max order notional (in USD) (default: `Field(default=None, gt=0, description='Max order notional (in USD)')`)
  - `max_qty`: `int | None` — Max order quantity (shares) (default: `Field(default=None, gt=0, description='Max order quantity (shares)')`)
  - `max_adv_pct`: `Decimal | None` — Max order size as fraction of ADV (0-1) (default: `Field(default=None, gt=0, le=1, description='Max order size as fraction of ADV (0-1)')`)

- `FatFingerThresholdsResponse` (pydantic) — `apps/execution_gateway/schemas.py`
  - `default_thresholds`: `FatFingerThresholds` — No description in code.
  - `symbol_overrides`: `dict[str, FatFingerThresholds]` — No description in code.
  - `updated_at`: `datetime` — No description in code.

- `FatFingerThresholdsUpdateRequest` (pydantic) — `apps/execution_gateway/schemas.py`
  - `default_thresholds`: `FatFingerThresholds | None` — Default thresholds applied when no override exists (default: `Field(default=None, description='Default thresholds applied when no override exists')`)
  - `symbol_overrides`: `dict[str, FatFingerThresholds | None] | None` — Per-symbol overrides; set value to null to remove override (default: `Field(default=None, description='Per-symbol overrides; set value to null to remove override')`)

- `HealthResponse` (pydantic) — `apps/execution_gateway/schemas.py`
  - `status`: `Literal['healthy', 'degraded', 'unhealthy']` — No description in code.
  - `service`: `str` — No description in code. (default: `'execution_gateway'`)
  - `version`: `str` — No description in code.
  - `dry_run`: `bool` — No description in code.
  - `database_connected`: `bool` — No description in code.
  - `alpaca_connected`: `bool` — No description in code.
  - `timestamp`: `datetime` — No description in code.
  - `details`: `dict[str, Any] | None` — No description in code. (default: `None`)

- `KillSwitchDisengageRequest` (pydantic) — `apps/execution_gateway/schemas.py`
  - `operator`: `str` — Operator ID/name (for audit trail) (default: `Field(..., description='Operator ID/name (for audit trail)')`)
  - `notes`: `str | None` — Optional notes about resolution (default: `Field(None, description='Optional notes about resolution')`)

- `KillSwitchEngageRequest` (pydantic) — `apps/execution_gateway/schemas.py`
  - `reason`: `str` — Human-readable reason for engagement (default: `Field(..., description='Human-readable reason for engagement')`)
  - `operator`: `str` — Operator ID/name (for audit trail) (default: `Field(..., description='Operator ID/name (for audit trail)')`)
  - `details`: `dict[str, Any] | None` — Optional additional context (default: `Field(None, description='Optional additional context')`)

- `OrderDetail` (pydantic) — `apps/execution_gateway/schemas.py`
  - `client_order_id`: `str` — No description in code.
  - `strategy_id`: `str` — No description in code.
  - `symbol`: `str` — No description in code.
  - `side`: `Literal['buy', 'sell']` — No description in code.
  - `qty`: `int` — No description in code.
  - `order_type`: `Literal['market', 'limit', 'stop', 'stop_limit']` — No description in code.
  - `limit_price`: `Decimal | None` — No description in code. (default: `None`)
  - `stop_price`: `Decimal | None` — No description in code. (default: `None`)
  - `time_in_force`: `Literal['day', 'gtc', 'ioc', 'fok']` — No description in code.
  - `status`: `OrderStatus` — No description in code.
  - `broker_order_id`: `str | None` — No description in code. (default: `None`)
  - `error_message`: `str | None` — No description in code. (default: `None`)
  - `retry_count`: `int` — No description in code.
  - `created_at`: `datetime` — No description in code.
  - `updated_at`: `datetime` — No description in code.
  - `submitted_at`: `datetime | None` — No description in code. (default: `None`)
  - `filled_at`: `datetime | None` — No description in code. (default: `None`)
  - `filled_qty`: `Decimal` — No description in code.
  - `filled_avg_price`: `Decimal | None` — No description in code. (default: `None`)
  - `metadata`: `dict[str, Any]` — No description in code. (default: `Field(default_factory=dict)`)
  - `parent_order_id`: `str | None` — No description in code. (default: `None`)
  - `slice_num`: `int | None` — No description in code. (default: `None`)
  - `total_slices`: `int | None` — No description in code. (default: `None`)
  - `scheduled_time`: `datetime | None` — No description in code. (default: `None`)

- `OrderEventData` (pydantic) — `apps/execution_gateway/schemas.py`
  - `event`: `Literal['new', 'fill', 'partial_fill', 'canceled', 'expired', 'done_for_day', 'replaced', 'rejected', 'pending_new', 'pending_cancel', 'pending_replace', 'stopped', 'suspended', 'calculated']` — No description in code.
  - `order`: `dict[str, Any]` — No description in code.
  - `timestamp`: `datetime` — No description in code.
  - `execution_id`: `str | None` — No description in code. (default: `None`)
  - `position_qty`: `str | None` — No description in code. (default: `None`)

- `OrderRequest` (pydantic) — `apps/execution_gateway/schemas.py`
  - `symbol`: `str` — Stock symbol (e.g., 'AAPL') (default: `Field(..., description="Stock symbol (e.g., 'AAPL')")`)
  - `side`: `Literal['buy', 'sell']` — Order side (default: `Field(..., description='Order side')`)
  - `qty`: `int` — Order quantity (must be positive) (default: `Field(..., gt=0, description='Order quantity (must be positive)')`)
  - `order_type`: `Literal['market', 'limit', 'stop', 'stop_limit']` — Order type (default: `Field(default='market', description='Order type')`)
  - `limit_price`: `Decimal | None` — Limit price (required for limit orders) (default: `Field(default=None, description='Limit price (required for limit orders)')`)
  - `stop_price`: `Decimal | None` — Stop price (required for stop orders) (default: `Field(default=None, description='Stop price (required for stop orders)')`)
  - `time_in_force`: `Literal['day', 'gtc', 'ioc', 'fok']` — Time in force (default: `Field(default='day', description='Time in force')`)

- `OrderResponse` (pydantic) — `apps/execution_gateway/schemas.py`
  - `client_order_id`: `str` — No description in code.
  - `status`: `OrderStatus` — No description in code.
  - `broker_order_id`: `str | None` — No description in code. (default: `None`)
  - `symbol`: `str` — No description in code.
  - `side`: `Literal['buy', 'sell']` — No description in code.
  - `qty`: `int` — No description in code.
  - `order_type`: `Literal['market', 'limit', 'stop', 'stop_limit']` — No description in code.
  - `limit_price`: `Decimal | None` — No description in code. (default: `None`)
  - `created_at`: `datetime` — No description in code.
  - `message`: `str` — No description in code.

- `PerformanceRequest` (pydantic) — `apps/execution_gateway/schemas.py`
  - `start_date`: `date` — Start date (UTC, inclusive). Defaults to 30 days ago. (default: `Field(default_factory=lambda : date.today() - timedelta(days=30), description='Start date (UTC, inclusive). Defaults to 30 days ago.')`)
  - `end_date`: `date` — End date (UTC, inclusive). Defaults to today. (default: `Field(default_factory=date.today, description='End date (UTC, inclusive). Defaults to today.')`)

- `Position` (pydantic) — `apps/execution_gateway/schemas.py`
  - `symbol`: `str` — No description in code.
  - `qty`: `Decimal` — No description in code.
  - `avg_entry_price`: `Decimal` — No description in code.
  - `current_price`: `Decimal | None` — No description in code. (default: `None`)
  - `unrealized_pl`: `Decimal | None` — No description in code. (default: `None`)
  - `realized_pl`: `Decimal` — No description in code.
  - `updated_at`: `datetime` — No description in code.
  - `last_trade_at`: `datetime | None` — No description in code. (default: `None`)

- `PositionsResponse` (pydantic) — `apps/execution_gateway/schemas.py`
  - `positions`: `list[Position]` — No description in code.
  - `total_positions`: `int` — No description in code.
  - `total_unrealized_pl`: `Decimal | None` — No description in code. (default: `None`)
  - `total_realized_pl`: `Decimal` — No description in code.

- `RealtimePnLResponse` (pydantic) — `apps/execution_gateway/schemas.py`
  - `positions`: `list[RealtimePositionPnL]` — No description in code.
  - `total_positions`: `int` — No description in code.
  - `total_unrealized_pl`: `Decimal` — No description in code.
  - `total_unrealized_pl_pct`: `Decimal | None` — Total unrealized P&L as percentage of total investment (default: `Field(None, description='Total unrealized P&L as percentage of total investment')`)
  - `realtime_prices_available`: `int` — Number of positions with real-time prices (default: `Field(description='Number of positions with real-time prices')`)
  - `timestamp`: `datetime` — Response generation timestamp (default: `Field(description='Response generation timestamp')`)

- `RealtimePositionPnL` (pydantic) — `apps/execution_gateway/schemas.py`
  - `symbol`: `str` — No description in code.
  - `qty`: `Decimal` — No description in code.
  - `avg_entry_price`: `Decimal` — No description in code.
  - `current_price`: `Decimal` — No description in code.
  - `price_source`: `Literal['real-time', 'database', 'fallback']` — Source of current price (real-time=Redis, database=last known, fallback=entry price) (default: `Field(description='Source of current price (real-time=Redis, database=last known, fallback=entry price)')`)
  - `unrealized_pl`: `Decimal` — No description in code.
  - `unrealized_pl_pct`: `Decimal` — Unrealized P&L as percentage (default: `Field(description='Unrealized P&L as percentage')`)
  - `last_price_update`: `datetime | None` — Timestamp of last price update from market data (default: `Field(None, description='Timestamp of last price update from market data')`)

- `ReconciliationForceCompleteRequest` (pydantic) — `apps/execution_gateway/schemas.py`
  - `reason`: `str | None` — Operator-provided reason for forcing reconciliation completion (default: `Field(None, description='Operator-provided reason for forcing reconciliation completion')`)

- `SliceDetail` (pydantic) — `apps/execution_gateway/schemas.py`
  - `slice_num`: `int` — Slice number (0-indexed) (default: `Field(..., ge=0, description='Slice number (0-indexed)')`)
  - `qty`: `int` — Slice quantity (default: `Field(..., gt=0, description='Slice quantity')`)
  - `scheduled_time`: `datetime` — Scheduled execution time (UTC) (default: `Field(..., description='Scheduled execution time (UTC)')`)
  - `client_order_id`: `str` — Deterministic slice order ID (default: `Field(..., description='Deterministic slice order ID')`)
  - `strategy_id`: `str` — Strategy ID for this slice (e.g., 'twap_slice_parent123_0') (default: `Field(..., description="Strategy ID for this slice (e.g., 'twap_slice_parent123_0')")`)
  - `status`: `OrderStatus` — Current slice status (default: `Field(default='pending_new', description='Current slice status')`)

- `SlicingPlan` (pydantic) — `apps/execution_gateway/schemas.py`
  - `parent_order_id`: `str` — Parent order deterministic ID (default: `Field(..., description='Parent order deterministic ID')`)
  - `parent_strategy_id`: `str` — Strategy ID for parent order (e.g., 'twap_parent_5m_60s') (default: `Field(..., description="Strategy ID for parent order (e.g., 'twap_parent_5m_60s')")`)
  - `symbol`: `str` — Stock symbol (default: `Field(..., description='Stock symbol')`)
  - `side`: `Literal['buy', 'sell']` — Order side (default: `Field(..., description='Order side')`)
  - `total_qty`: `int` — Total quantity (default: `Field(..., gt=0, description='Total quantity')`)
  - `total_slices`: `int` — Number of slices (default: `Field(..., gt=0, description='Number of slices')`)
  - `duration_minutes`: `int` — Slicing duration in minutes (default: `Field(..., gt=0, description='Slicing duration in minutes')`)
  - `interval_seconds`: `int` — Interval between slices in seconds (default: `Field(..., gt=0, description='Interval between slices in seconds')`)
  - `slices`: `list[SliceDetail]` — Child slice details (ordered by slice_num) (default: `Field(..., description='Child slice details (ordered by slice_num)')`)

- `SlicingRequest` (pydantic) — `apps/execution_gateway/schemas.py`
  - `symbol`: `str` — Stock symbol (e.g., 'AAPL') (default: `Field(..., description="Stock symbol (e.g., 'AAPL')")`)
  - `side`: `Literal['buy', 'sell']` — Order side (default: `Field(..., description='Order side')`)
  - `qty`: `int` — Total order quantity (must be positive) (default: `Field(..., gt=0, description='Total order quantity (must be positive)')`)
  - `duration_minutes`: `int` — Total slicing duration in minutes (default: `Field(..., gt=0, description='Total slicing duration in minutes')`)
  - `interval_seconds`: `int` — Interval between slices in seconds (default: 60 = 1 minute) (default: `Field(default=60, gt=0, description='Interval between slices in seconds (default: 60 = 1 minute)')`)
  - `order_type`: `Literal['market', 'limit', 'stop', 'stop_limit']` — Order type for each slice (default: `Field(default='market', description='Order type for each slice')`)
  - `limit_price`: `Decimal | None` — Limit price (required for limit orders) (default: `Field(default=None, description='Limit price (required for limit orders)')`)
  - `stop_price`: `Decimal | None` — Stop price (required for stop orders) (default: `Field(default=None, description='Stop price (required for stop orders)')`)
  - `time_in_force`: `Literal['day', 'gtc', 'ioc', 'fok']` — Time in force for each slice (default: `Field(default='day', description='Time in force for each slice')`)
  - `trade_date`: `date | None` — Trading date for order ID generation (defaults to today UTC). CRITICAL for idempotency: retries after midnight must pass same trade_date to avoid creating duplicate orders. (default: `Field(default=None, description='Trading date for order ID generation (defaults to today UTC). CRITICAL for idempotency: retries after midnight must pass same trade_date to avoid creating duplicate orders.')`)

- `StrategiesListResponse` (pydantic) — `apps/execution_gateway/schemas.py`
  - `strategies`: `list[StrategyStatusResponse]` — No description in code.
  - `total_count`: `int` — No description in code.
  - `timestamp`: `datetime` — No description in code.

- `StrategyStatusResponse` (pydantic) — `apps/execution_gateway/schemas.py`
  - `strategy_id`: `str` — No description in code.
  - `name`: `str` — No description in code.
  - `status`: `Literal['active', 'paused', 'error', 'inactive']` — No description in code.
  - `model_version`: `str | None` — No description in code. (default: `None`)
  - `model_status`: `Literal['active', 'inactive', 'testing', 'failed'] | None` — No description in code. (default: `None`)
  - `last_signal_at`: `datetime | None` — No description in code. (default: `None`)
  - `last_error`: `str | None` — No description in code. (default: `None`)
  - `positions_count`: `int` — No description in code. (default: `0`)
  - `open_orders_count`: `int` — No description in code. (default: `0`)
  - `today_pnl`: `Decimal | None` — No description in code. (default: `None`)
  - `timestamp`: `datetime` — No description in code.

- `WebhookEvent` (pydantic) — `apps/execution_gateway/schemas.py`
  - `event_type`: `str` — No description in code. (default: `Field(..., alias='event')`)
  - `data`: `OrderEventData` — No description in code.

- `AdjustPositionRequest` (pydantic) — `apps/execution_gateway/schemas_manual_controls.py`
  - `target_qty`: `Decimal` — No description in code.
  - `reason`: `str` — No description in code. (default: `Field(..., min_length=10)`)
  - `requested_by`: `str` — No description in code.
  - `requested_at`: `datetime` — No description in code.
  - `order_type`: `Literal['market', 'limit']` — No description in code. (default: `'market'`)
  - `limit_price`: `Decimal | None` — No description in code. (default: `None`)

- `AdjustPositionResponse` (pydantic) — `apps/execution_gateway/schemas_manual_controls.py`
  - `status`: `Literal['adjusting']` — No description in code.
  - `symbol`: `str` — No description in code.
  - `current_qty`: `Decimal` — No description in code.
  - `target_qty`: `Decimal` — No description in code.
  - `order_id`: `str | None` — No description in code. (default: `None`)

- `CancelAllOrdersRequest` (pydantic) — `apps/execution_gateway/schemas_manual_controls.py`
  - `symbol`: `str` — Symbol to cancel orders for (default: `Field(..., description='Symbol to cancel orders for')`)
  - `reason`: `str` — No description in code. (default: `Field(..., min_length=10)`)
  - `requested_by`: `str` — No description in code.
  - `requested_at`: `datetime` — No description in code.

- `CancelAllOrdersResponse` (pydantic) — `apps/execution_gateway/schemas_manual_controls.py`
  - `status`: `Literal['cancelled']` — No description in code.
  - `symbol`: `str` — No description in code.
  - `cancelled_count`: `int` — No description in code.
  - `order_ids`: `list[str]` — No description in code.
  - `strategies_affected`: `list[str]` — No description in code.

- `CancelOrderRequest` (pydantic) — `apps/execution_gateway/schemas_manual_controls.py`
  - `reason`: `str` — No description in code. (default: `Field(..., min_length=10)`)
  - `requested_by`: `str` — No description in code.
  - `requested_at`: `datetime` — No description in code.

- `CancelOrderResponse` (pydantic) — `apps/execution_gateway/schemas_manual_controls.py`
  - `status`: `Literal['cancelled']` — No description in code.
  - `order_id`: `str` — No description in code.
  - `cancelled_at`: `datetime` — No description in code.

- `ClosePositionRequest` (pydantic) — `apps/execution_gateway/schemas_manual_controls.py`
  - `reason`: `str` — No description in code. (default: `Field(..., min_length=10)`)
  - `requested_by`: `str` — No description in code.
  - `requested_at`: `datetime` — No description in code.
  - `qty`: `Decimal | None` — Optional partial close quantity (positive number) (default: `Field(default=None, description='Optional partial close quantity (positive number)')`)

- `ClosePositionResponse` (pydantic) — `apps/execution_gateway/schemas_manual_controls.py`
  - `status`: `Literal['closing', 'already_flat']` — No description in code.
  - `symbol`: `str` — No description in code.
  - `order_id`: `str | None` — No description in code. (default: `None`)
  - `qty_to_close`: `Decimal` — No description in code.

- `ErrorPayload` (pydantic) — `apps/execution_gateway/schemas_manual_controls.py`
  - `error`: `str` — No description in code.
  - `message`: `str` — No description in code.
  - `retry_after`: `int | None` — No description in code. (default: `None`)
  - `timestamp`: `datetime` — No description in code.

- `FlattenAllRequest` (pydantic) — `apps/execution_gateway/schemas_manual_controls.py`
  - `reason`: `str` — No description in code. (default: `Field(..., min_length=20)`)
  - `requested_by`: `str` — No description in code.
  - `requested_at`: `datetime` — No description in code.
  - `id_token`: `str` — ID token proving MFA (default: `Field(..., description='ID token proving MFA')`)

- `FlattenAllResponse` (pydantic) — `apps/execution_gateway/schemas_manual_controls.py`
  - `status`: `Literal['flattening']` — No description in code.
  - `positions_closed`: `int` — No description in code.
  - `orders_created`: `list[str]` — No description in code.
  - `strategies_affected`: `list[str]` — No description in code.

- `PendingOrdersParams` (pydantic) — `apps/execution_gateway/schemas_manual_controls.py`
  - `strategy_id`: `str | None` — No description in code. (default: `None`)
  - `symbol`: `str | None` — No description in code. (default: `None`)
  - `limit`: `int` — No description in code. (default: `100`)
  - `offset`: `int` — No description in code. (default: `0`)
  - `sort_by`: `str` — No description in code. (default: `'created_at'`)
  - `sort_order`: `Literal['asc', 'desc']` — No description in code. (default: `'desc'`)

- `PendingOrdersResponse` (pydantic) — `apps/execution_gateway/schemas_manual_controls.py`
  - `orders`: `list[OrderDetail]` — No description in code.
  - `total`: `int` — No description in code.
  - `limit`: `int` — No description in code.
  - `offset`: `int` — No description in code.
  - `filtered_by_strategy`: `bool` — No description in code.
  - `user_strategies`: `list[str]` — No description in code.

- `StrategyScopedPosition` (pydantic) — `apps/execution_gateway/schemas_manual_controls.py`
  - `position`: `Position` — No description in code.
  - `strategy_id`: `str | None` — No description in code. (default: `None`)

- `MarketClockSnapshot` (dataclass) — `apps/execution_gateway/slice_scheduler.py`
  - `is_open`: `bool` — No description in code.
  - `next_open`: `datetime | None` — No description in code.

- `OrderRequest` (pydantic) — `apps/orchestrator/schemas.py`
  - `symbol`: `str` — No description in code.
  - `side`: `str` — No description in code.
  - `qty`: `int` — No description in code.
  - `order_type`: `str` — No description in code.
  - `limit_price`: `Decimal | None` — No description in code. (default: `None`)
  - `stop_price`: `Decimal | None` — No description in code. (default: `None`)
  - `time_in_force`: `str` — No description in code. (default: `'day'`)

- `OrderSubmission` (pydantic) — `apps/orchestrator/schemas.py`
  - `client_order_id`: `str` — No description in code.
  - `status`: `str` — No description in code.
  - `broker_order_id`: `str | None` — No description in code. (default: `None`)
  - `symbol`: `str` — No description in code.
  - `side`: `str` — No description in code.
  - `qty`: `int` — No description in code.
  - `order_type`: `str` — No description in code.
  - `limit_price`: `Decimal | None` — No description in code. (default: `None`)
  - `created_at`: `datetime` — No description in code.
  - `message`: `str` — No description in code.

- `SignalOrderMapping` (pydantic) — `apps/orchestrator/schemas.py`
  - `symbol`: `str` — No description in code.
  - `predicted_return`: `float` — No description in code.
  - `rank`: `int` — No description in code.
  - `target_weight`: `float` — No description in code.
  - `client_order_id`: `str | None` — No description in code. (default: `None`)
  - `order_qty`: `int | None` — No description in code. (default: `None`)
  - `order_side`: `str | None` — No description in code. (default: `None`)
  - `broker_order_id`: `str | None` — No description in code. (default: `None`)
  - `order_status`: `str | None` — No description in code. (default: `None`)
  - `filled_qty`: `Decimal | None` — No description in code. (default: `None`)
  - `filled_avg_price`: `Decimal | None` — No description in code. (default: `None`)
  - `skip_reason`: `str | None` — No description in code. (default: `None`)

- `OrderEvent` (pydantic) — `libs/core/redis_client/events.py`
  - `event_type`: `str` — Event type identifier (default: `Field(default='orders.executed', description='Event type identifier')`)
  - `timestamp`: `datetime` — Event timestamp (UTC) (default: `Field(..., description='Event timestamp (UTC)')`)
  - `run_id`: `str` — Orchestration run ID (UUID) (default: `Field(..., description='Orchestration run ID (UUID)')`)
  - `strategy_id`: `str` — Strategy that generated orders (default: `Field(..., description='Strategy that generated orders')`)
  - `num_orders`: `int` — Total number of orders submitted (default: `Field(..., ge=0, description='Total number of orders submitted')`)
  - `num_accepted`: `int` — Number of orders accepted (default: `Field(..., ge=0, description='Number of orders accepted')`)
  - `num_rejected`: `int` — Number of orders rejected (default: `Field(..., ge=0, description='Number of orders rejected')`)

#### SQL Tables

- `orders` — `migrations/002_create_execution_tables.sql`
  - `client_order_id`: `TEXT`
  - `strategy_id`: `TEXT`
  - `symbol`: `TEXT`
  - `side`: `TEXT`
  - `qty`: `NUMERIC`
  - `order_type`: `TEXT`
  - `limit_price`: `NUMERIC`
  - `stop_price`: `NUMERIC`
  - `time_in_force`: `TEXT`
  - `status`: `TEXT`
  - `broker_order_id`: `TEXT`
  - `error_message`: `TEXT`
  - `retry_count`: `INTEGER`
  - `created_at`: `TIMESTAMPTZ`
  - `updated_at`: `TIMESTAMPTZ`
  - `submitted_at`: `TIMESTAMPTZ`
  - `filled_at`: `TIMESTAMPTZ`
  - `filled_qty`: `NUMERIC`
  - `filled_avg_price`: `NUMERIC`
  - `metadata`: `JSONB`

- `orphan_orders` — `db/migrations/0009_add_conflict_resolution_columns.sql`
  - `id`: `SERIAL`
  - `broker_order_id`: `TEXT`
  - `client_order_id`: `TEXT`
  - `symbol`: `TEXT`
  - `strategy_id`: `TEXT`
  - `side`: `TEXT`
  - `qty`: `INTEGER`
  - `estimated_notional`: `DECIMAL(18,2)`
  - `status`: `TEXT`
  - `detected_at`: `TIMESTAMPTZ`
  - `resolved_at`: `TIMESTAMPTZ`

- `signal_order_mappings` — `migrations/003_create_orchestration_tables.sql`
  - `id`: `SERIAL`
  - `run_id`: `UUID`
  - `symbol`: `VARCHAR(10)`
  - `predicted_return`: `NUMERIC(10,`
  - `rank`: `INTEGER`
  - `target_weight`: `NUMERIC(5,`
  - `client_order_id`: `TEXT`
  - `order_qty`: `INTEGER`
  - `order_side`: `VARCHAR(10)`
  - `broker_order_id`: `TEXT`
  - `order_status`: `VARCHAR(20)`
  - `filled_qty`: `NUMERIC(15,`
  - `filled_avg_price`: `NUMERIC(15,`
  - `skip_reason`: `TEXT`
  - `created_at`: `TIMESTAMPTZ`
  - `updated_at`: `TIMESTAMPTZ`
  - constraints:
    - `CONSTRAINT fk_signal_order_mappings_run_id FOREIGN KEY (run_id) REFERENCES orchestration_runs(run_id) ON DELETE CASCADE`

### Positions & PnL

#### Application Models

- `PositionLimitsConfig` (pydantic) — `apps/web_console/components/config_editor.py`
  - `max_position_per_symbol`: `int` — No description in code. (default: `Field(default=1000, ge=1, le=100000)`)
  - `max_notional_total`: `Decimal` — No description in code. (default: `Field(default=Decimal('100000'), ge=Decimal('1000'), le=Decimal('10000000'))`)
  - `max_open_orders`: `int` — No description in code. (default: `Field(default=10, ge=1, le=1000)`)

- `ReturnDecompositionResult` (dataclass) — `libs/platform/analytics/attribution.py`
  - `schema_version`: `str` — No description in code. (default: `'1.0.0'`)
  - `portfolio_id`: `str` — No description in code. (default: `''`)
  - `decomposition`: `pl.DataFrame | None` — No description in code. (default: `None`)
  - `attribution_result`: `AttributionResult | None` — No description in code. (default: `None`)
  - `dataset_version_id`: `str` — No description in code. (default: `''`)
  - `computation_timestamp`: `datetime` — No description in code. (default: `field(default_factory=lambda : datetime.now(UTC))`)

- `PositionEvent` (pydantic) — `libs/core/redis_client/events.py`
  - `event_type`: `str` — Event type identifier (default: `Field(default='positions.updated', description='Event type identifier')`)
  - `timestamp`: `datetime` — Event timestamp (UTC) (default: `Field(..., description='Event timestamp (UTC)')`)
  - `symbol`: `str` — Stock symbol (default: `Field(..., description='Stock symbol')`)
  - `action`: `str` — Action that caused update (buy/sell/fill) (default: `Field(..., description='Action that caused update (buy/sell/fill)')`)
  - `qty_change`: `int` — Change in position quantity (signed) (default: `Field(..., description='Change in position quantity (signed)')`)
  - `new_qty`: `int` — New total position quantity (signed) (default: `Field(..., description='New total position quantity (signed)')`)
  - `price`: `str` — Execution price (Decimal as string) (default: `Field(..., description='Execution price (Decimal as string)')`)
  - `strategy_id`: `str` — Strategy that owns the position (default: `Field(..., description='Strategy that owns the position')`)

- `PositionLimits` (pydantic) — `libs/trading/risk_management/config.py`
  - `max_position_size`: `int` — Maximum shares per symbol (absolute value) (default: `Field(default=1000, description='Maximum shares per symbol (absolute value)', ge=1)`)
  - `max_position_pct`: `Decimal` — Maximum position as % of portfolio (0.20 = 20%) (default: `Field(default=Decimal('0.20'), description='Maximum position as % of portfolio (0.20 = 20%)', ge=Decimal('0.01'), le=Decimal('1.00'))`)

#### SQL Tables

- `positions` — `migrations/002_create_execution_tables.sql`
  - `symbol`: `TEXT`
  - `qty`: `NUMERIC`
  - `avg_entry_price`: `NUMERIC`
  - `current_price`: `NUMERIC`
  - `unrealized_pl`: `NUMERIC`
  - `realized_pl`: `NUMERIC`
  - `updated_at`: `TIMESTAMPTZ`
  - `last_trade_at`: `TIMESTAMPTZ`
  - `metadata`: `JSONB`

- `tax_lot_dispositions` — `db/migrations/0019_create_tax_lots.sql`
  - `id`: `UUID`
  - `lot_id`: `UUID`
  - `quantity`: `DECIMAL(18,`
  - `cost_basis`: `DECIMAL(18,`
  - `proceeds_per_share`: `DECIMAL(18,`
  - `total_proceeds`: `DECIMAL(18,`
  - `disposed_at`: `TIMESTAMPTZ`
  - `disposition_type`: `VARCHAR(20)`
  - `destination_order_id`: `TEXT`
  - `idempotency_key`: `TEXT`
  - `realized_gain_loss`: `DECIMAL(18,`
  - `holding_period`: `VARCHAR(20)`
  - `wash_sale_disallowed`: `DECIMAL(18,`
  - `created_at`: `TIMESTAMPTZ`
  - constraints:
    - `CONSTRAINT uq_tax_lot_dispositions_idempotency UNIQUE (idempotency_key)`

### Signals & Orchestration

#### Application Models

- `ConfigResponse` (pydantic) — `apps/orchestrator/schemas.py`
  - `service`: `str` — Service name (default: `Field(..., description='Service name')`)
  - `version`: `str` — Service version (default: `Field(..., description='Service version')`)
  - `environment`: `str` — Environment (dev, staging, production) (default: `Field(..., description='Environment (dev, staging, production)')`)
  - `dry_run`: `bool` — Dry-run mode enabled (no real orders) (default: `Field(..., description='Dry-run mode enabled (no real orders)')`)
  - `alpaca_paper`: `bool` — Alpaca paper trading mode (default: `Field(..., description='Alpaca paper trading mode')`)
  - `circuit_breaker_enabled`: `bool` — Circuit breaker feature enabled (default: `Field(..., description='Circuit breaker feature enabled')`)
  - `timestamp`: `datetime` — Response timestamp (UTC) (default: `Field(..., description='Response timestamp (UTC)')`)

- `HealthResponse` (pydantic) — `apps/orchestrator/schemas.py`
  - `status`: `str` — No description in code.
  - `service`: `str` — No description in code.
  - `version`: `str` — No description in code.
  - `timestamp`: `datetime` — No description in code.
  - `signal_service_url`: `str` — No description in code.
  - `execution_gateway_url`: `str` — No description in code.
  - `signal_service_healthy`: `bool` — No description in code.
  - `execution_gateway_healthy`: `bool` — No description in code.
  - `database_connected`: `bool` — No description in code.
  - `details`: `dict[str, Any] | None` — No description in code. (default: `None`)

- `KillSwitchDisengageRequest` (pydantic) — `apps/orchestrator/schemas.py`
  - `operator`: `str` — Operator ID/name (for audit trail) (default: `Field(..., description='Operator ID/name (for audit trail)')`)
  - `notes`: `str | None` — Optional notes about resolution (default: `Field(None, description='Optional notes about resolution')`)

- `KillSwitchEngageRequest` (pydantic) — `apps/orchestrator/schemas.py`
  - `reason`: `str` — Human-readable reason for engagement (default: `Field(..., description='Human-readable reason for engagement')`)
  - `operator`: `str` — Operator ID/name (for audit trail) (default: `Field(..., description='Operator ID/name (for audit trail)')`)
  - `details`: `dict[str, Any] | None` — Optional additional context (default: `Field(None, description='Optional additional context')`)

- `OrchestrationRequest` (pydantic) — `apps/orchestrator/schemas.py`
  - `symbols`: `list[str]` — List of symbols to trade (default: `Field(..., min_length=1, description='List of symbols to trade')`)
  - `as_of_date`: `str | None` — Date for signal generation (YYYY-MM-DD) (default: `Field(None, description='Date for signal generation (YYYY-MM-DD)')`)
  - `capital`: `Decimal | None` — Override capital amount (default: `Field(None, description='Override capital amount')`)
  - `max_position_size`: `Decimal | None` — Override max position size (default: `Field(None, description='Override max position size')`)
  - `dry_run`: `bool | None` — Override DRY_RUN setting (default: `Field(None, description='Override DRY_RUN setting')`)

- `OrchestrationResult` (pydantic) — `apps/orchestrator/schemas.py`
  - `run_id`: `UUID` — No description in code.
  - `status`: `str` — No description in code.
  - `strategy_id`: `str` — No description in code.
  - `as_of_date`: `str` — No description in code.
  - `symbols`: `list[str]` — No description in code.
  - `capital`: `Decimal` — No description in code.
  - `num_signals`: `int` — No description in code.
  - `signal_metadata`: `dict[str, Any] | None` — No description in code. (default: `None`)
  - `num_orders_submitted`: `int` — No description in code.
  - `num_orders_accepted`: `int` — No description in code.
  - `num_orders_rejected`: `int` — No description in code.
  - `num_orders_filled`: `int | None` — No description in code. (default: `None`)
  - `mappings`: `list[SignalOrderMapping]` — No description in code.
  - `started_at`: `datetime` — No description in code.
  - `completed_at`: `datetime | None` — No description in code. (default: `None`)
  - `duration_seconds`: `Decimal | None` — No description in code. (default: `None`)
  - `error_message`: `str | None` — No description in code. (default: `None`)

- `OrchestrationRunSummary` (pydantic) — `apps/orchestrator/schemas.py`
  - `run_id`: `UUID` — No description in code.
  - `status`: `str` — No description in code.
  - `strategy_id`: `str` — No description in code.
  - `as_of_date`: `str` — No description in code.
  - `num_signals`: `int` — No description in code.
  - `num_orders_submitted`: `int` — No description in code.
  - `num_orders_accepted`: `int` — No description in code.
  - `num_orders_rejected`: `int` — No description in code.
  - `started_at`: `datetime` — No description in code.
  - `completed_at`: `datetime | None` — No description in code. (default: `None`)
  - `duration_seconds`: `Decimal | None` — No description in code. (default: `None`)

- `OrchestrationRunsResponse` (pydantic) — `apps/orchestrator/schemas.py`
  - `runs`: `list[OrchestrationRunSummary]` — No description in code.
  - `total`: `int` — No description in code.
  - `limit`: `int` — No description in code.
  - `offset`: `int` — No description in code.

- `Signal` (pydantic) — `apps/orchestrator/schemas.py`
  - `symbol`: `str` — No description in code.
  - `predicted_return`: `float` — No description in code.
  - `rank`: `int` — No description in code.
  - `target_weight`: `float` — No description in code.

- `SignalMetadata` (pydantic) — `apps/orchestrator/schemas.py`
  - `as_of_date`: `str` — No description in code.
  - `model_version`: `str` — No description in code.
  - `strategy`: `str` — No description in code.
  - `num_signals`: `int` — No description in code.
  - `generated_at`: `str` — No description in code.
  - `top_n`: `int` — No description in code.
  - `bottom_n`: `int` — No description in code.

- `SignalServiceResponse` (pydantic) — `apps/orchestrator/schemas.py`
  - `signals`: `list[Signal]` — No description in code.
  - `metadata`: `SignalMetadata` — No description in code.

- `HealthResponse` (pydantic) — `apps/signal_service/main.py`
  - `status`: `str` — Service health status (default: `Field(..., description='Service health status')`)
  - `service`: `str` — Service name (default: `Field(default='signal_service', description='Service name')`)
  - `model_loaded`: `bool` — Whether model is loaded (default: `Field(..., description='Whether model is loaded')`)
  - `model_info`: `dict[str, Any] | None` — Model metadata (default: `Field(None, description='Model metadata')`)
  - `redis_status`: `str` — Redis connection status (connected/disconnected/disabled) (default: `Field(..., description='Redis connection status (connected/disconnected/disabled)')`)
  - `feature_cache_enabled`: `bool` — Whether feature caching is active (default: `Field(..., description='Whether feature caching is active')`)
  - `timestamp`: `str` — Current timestamp (default: `Field(..., description='Current timestamp')`)

- `PrecomputeRequest` (pydantic) — `apps/signal_service/main.py`
  - `symbols`: `list[str]` — List of stock symbols to pre-compute features for (default: `Field(..., min_length=1, description='List of stock symbols to pre-compute features for', examples=[['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'TSLA']])`)
  - `as_of_date`: `str | None` — Date for feature computation (ISO format: YYYY-MM-DD). Defaults to today. (default: `Field(default=None, description='Date for feature computation (ISO format: YYYY-MM-DD). Defaults to today.', examples=['2024-12-31'])`)

- `PrecomputeResponse` (pydantic) — `apps/signal_service/main.py`
  - `cached_count`: `int` — Number of symbols successfully cached (default: `Field(..., description='Number of symbols successfully cached')`)
  - `skipped_count`: `int` — Number of symbols skipped (already cached or error) (default: `Field(..., description='Number of symbols skipped (already cached or error)')`)
  - `symbols_cached`: `list[str]` — List of newly cached symbols (default: `Field(..., description='List of newly cached symbols')`)
  - `symbols_skipped`: `list[str]` — List of skipped symbols (default: `Field(..., description='List of skipped symbols')`)
  - `as_of_date`: `str` — Date features were computed for (default: `Field(..., description='Date features were computed for')`)

- `SignalRequest` (pydantic) — `apps/signal_service/main.py`
  - `symbols`: `list[str]` — List of stock symbols to generate signals for (default: `Field(..., min_length=1, description='List of stock symbols to generate signals for', examples=[['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'TSLA']])`)
  - `as_of_date`: `str | None` — Date for signal generation (ISO format: YYYY-MM-DD). Defaults to today. (default: `Field(default=None, description='Date for signal generation (ISO format: YYYY-MM-DD). Defaults to today.', examples=['2024-12-31'])`)
  - `top_n`: `int | None` — Number of long positions (overrides default) (default: `Field(default=None, ge=0, description='Number of long positions (overrides default)', examples=[3])`)
  - `bottom_n`: `int | None` — Number of short positions (overrides default) (default: `Field(default=None, ge=0, description='Number of short positions (overrides default)', examples=[3])`)

- `SignalResponse` (pydantic) — `apps/signal_service/main.py`
  - `signals`: `list[dict[str, Any]]` — List of trading signals (default: `Field(..., description='List of trading signals')`)
  - `metadata`: `dict[str, Any]` — Request and model metadata (default: `Field(..., description='Request and model metadata')`)

- `ModelMetadata` (dataclass) — `apps/signal_service/model_registry.py`
  - `id`: `int` — No description in code.
  - `strategy_name`: `str` — No description in code.
  - `version`: `str` — No description in code.
  - `mlflow_run_id`: `str | None` — No description in code.
  - `mlflow_experiment_id`: `str | None` — No description in code.
  - `model_path`: `str` — No description in code.
  - `status`: `str` — No description in code.
  - `performance_metrics`: `dict[str, Any]` — No description in code.
  - `config`: `dict[str, Any]` — No description in code.
  - `created_at`: `datetime` — No description in code.
  - `activated_at`: `datetime | None` — No description in code.

- `ShadowValidationResult` (dataclass) — `apps/signal_service/shadow_validator.py`
  - `passed`: `bool` — No description in code.
  - `correlation`: `float` — No description in code.
  - `mean_abs_diff_ratio`: `float` — No description in code.
  - `sign_change_rate`: `float` — No description in code.
  - `sample_count`: `int` — No description in code.
  - `old_range`: `float` — No description in code.
  - `new_range`: `float` — No description in code.
  - `message`: `str` — No description in code.

- `HydrationResult` (typeddict) — `apps/signal_service/signal_generator.py`
  - `dates_attempted`: `int` — No description in code.
  - `dates_succeeded`: `int` — No description in code.
  - `dates_failed`: `int` — No description in code.
  - `cached_count`: `int` — No description in code.
  - `skipped_count`: `int` — No description in code.

- `PrecomputeResult` (typeddict) — `apps/signal_service/signal_generator.py`
  - `cached_count`: `int` — No description in code.
  - `skipped_count`: `int` — No description in code.
  - `symbols_cached`: `list[str]` — No description in code.
  - `symbols_skipped`: `list[str]` — No description in code.

- `SignalMetrics` (dataclass) — `apps/web_console/services/alpha_explorer_service.py`
  - `signal_id`: `str` — No description in code.
  - `name`: `str` — No description in code.
  - `version`: `str` — No description in code.
  - `mean_ic`: `float` — No description in code.
  - `icir`: `float` — No description in code.
  - `hit_rate`: `float` — No description in code.
  - `coverage`: `float` — No description in code.
  - `average_turnover`: `float` — No description in code.
  - `decay_half_life`: `float | None` — No description in code.
  - `n_days`: `int` — No description in code.
  - `start_date`: `date` — No description in code.
  - `end_date`: `date` — No description in code.

- `SignalSummary` (dataclass) — `apps/web_console/services/alpha_explorer_service.py`
  - `signal_id`: `str` — No description in code.
  - `display_name`: `str` — No description in code.
  - `version`: `str` — No description in code.
  - `mean_ic`: `float | None` — No description in code.
  - `icir`: `float | None` — No description in code.
  - `created_at`: `date` — No description in code.
  - `backtest_job_id`: `str | None` — No description in code.

- `SignalEvent` (pydantic) — `libs/core/redis_client/events.py`
  - `event_type`: `str` — Event type identifier (default: `Field(default='signals.generated', description='Event type identifier')`)
  - `timestamp`: `datetime` — Event timestamp (UTC) (default: `Field(..., description='Event timestamp (UTC)')`)
  - `strategy_id`: `str` — Strategy that generated signals (default: `Field(..., description='Strategy that generated signals')`)
  - `symbols`: `list[str]` — Symbols with generated signals (default: `Field(..., min_length=1, description='Symbols with generated signals')`)
  - `num_signals`: `int` — Number of signals generated (default: `Field(..., ge=0, description='Number of signals generated')`)
  - `as_of_date`: `str` — Date for which signals were generated (ISO format) (default: `Field(..., description='Date for which signals were generated (ISO format)')`)

#### SQL Tables

- `orchestration_runs` — `migrations/003_create_orchestration_tables.sql`
  - `id`: `SERIAL`
  - `run_id`: `UUID`
  - `strategy_id`: `VARCHAR(100)`
  - `as_of_date`: `DATE`
  - `status`: `VARCHAR(20)`
  - `symbols`: `TEXT[]`
  - `capital`: `NUMERIC(15,`
  - `max_position_size`: `NUMERIC(15,`
  - `num_signals`: `INTEGER`
  - `model_version`: `VARCHAR(50)`
  - `num_orders_submitted`: `INTEGER`
  - `num_orders_accepted`: `INTEGER`
  - `num_orders_rejected`: `INTEGER`
  - `num_orders_filled`: `INTEGER`
  - `started_at`: `TIMESTAMPTZ`
  - `completed_at`: `TIMESTAMPTZ`
  - `duration_seconds`: `NUMERIC(10,`
  - `error_message`: `TEXT`
  - `signal_service_response`: `JSONB`
  - `execution_gateway_responses`: `JSONB`
  - `created_at`: `TIMESTAMPTZ`
  - `updated_at`: `TIMESTAMPTZ`

### Market Data

#### Application Models

- `HealthResponse` (pydantic) — `apps/market_data_service/main.py`
  - `status`: `str` — No description in code.
  - `service`: `str` — No description in code.
  - `websocket_connected`: `bool` — No description in code.
  - `subscribed_symbols`: `int` — No description in code.
  - `reconnect_attempts`: `int` — No description in code.
  - `max_reconnect_attempts`: `int` — No description in code.

- `SubscribeRequest` (pydantic) — `apps/market_data_service/main.py`
  - `symbols`: `list[str]` — No description in code.

- `SubscribeResponse` (pydantic) — `apps/market_data_service/main.py`
  - `message`: `str` — No description in code.
  - `subscribed_symbols`: `list[str]` — No description in code.
  - `total_subscriptions`: `int` — No description in code.

- `SubscriptionsResponse` (pydantic) — `apps/market_data_service/main.py`
  - `symbols`: `list[str]` — No description in code.
  - `count`: `int` — No description in code.

- `UnsubscribeResponse` (pydantic) — `apps/market_data_service/main.py`
  - `message`: `str` — No description in code.
  - `remaining_subscriptions`: `int` — No description in code.

- `PriceData` (pydantic) — `libs/data/market_data/types.py`
  - `symbol`: `str` — Stock symbol (default: `Field(..., description='Stock symbol')`)
  - `bid`: `Decimal` — Bid price (default: `Field(..., description='Bid price', ge=0)`)
  - `ask`: `Decimal` — Ask price (default: `Field(..., description='Ask price', ge=0)`)
  - `mid`: `Decimal` — Mid price (default: `Field(..., description='Mid price', ge=0)`)
  - `bid_size`: `int` — Bid size (default: `Field(default=0, description='Bid size', ge=0)`)
  - `ask_size`: `int` — Ask size (default: `Field(default=0, description='Ask size', ge=0)`)
  - `timestamp`: `str` — ISO format timestamp (default: `Field(..., description='ISO format timestamp')`)
  - `exchange`: `str | None` — Exchange code (default: `Field(None, description='Exchange code')`)

- `PriceUpdateEvent` (pydantic) — `libs/data/market_data/types.py`
  - `event_type`: `Literal['price.updated']` — No description in code. (default: `'price.updated'`)
  - `symbol`: `str` — Stock symbol (default: `Field(..., description='Stock symbol')`)
  - `price`: `Decimal` — Mid price (default: `Field(..., description='Mid price', ge=0)`)
  - `timestamp`: `str` — ISO format timestamp (default: `Field(..., description='ISO format timestamp')`)

- `QuoteData` (pydantic) — `libs/data/market_data/types.py`
  - `symbol`: `str` — Stock symbol (e.g., 'AAPL') (default: `Field(..., description="Stock symbol (e.g., 'AAPL')")`)
  - `bid_price`: `Decimal` — Best bid price (default: `Field(..., description='Best bid price', ge=0)`)
  - `ask_price`: `Decimal` — Best ask price (default: `Field(..., description='Best ask price', ge=0)`)
  - `bid_size`: `int` — Bid size in shares (default: `Field(..., description='Bid size in shares', ge=0)`)
  - `ask_size`: `int` — Ask size in shares (default: `Field(..., description='Ask size in shares', ge=0)`)
  - `timestamp`: `datetime` — Quote timestamp (UTC) (default: `Field(..., description='Quote timestamp (UTC)')`)
  - `exchange`: `str | None` — Exchange code (e.g., 'NASDAQ') (default: `Field(None, description="Exchange code (e.g., 'NASDAQ')")`)

### Risk & Limits

#### Application Models

- `RiskDashboardData` (dataclass) — `apps/web_console/services/risk_service.py`
  - `risk_metrics`: `dict[str, float]` — No description in code.
  - `factor_exposures`: `list[dict[str, Any]]` — No description in code.
  - `stress_tests`: `list[dict[str, Any]]` — No description in code.
  - `var_history`: `list[dict[str, Any]]` — No description in code.
  - `is_placeholder`: `bool` — No description in code. (default: `False`)
  - `placeholder_reason`: `str` — No description in code. (default: `''`)

- `BarraRiskModel` (dataclass) — `libs/trading/risk/barra_model.py`
  - `factor_covariance`: `NDArray[np.floating[Any]]` — No description in code.
  - `factor_names`: `list[str]` — No description in code.
  - `factor_loadings`: `pl.DataFrame` — No description in code.
  - `specific_risks`: `pl.DataFrame` — No description in code.
  - `as_of_date`: `date` — No description in code.
  - `dataset_version_ids`: `dict[str, str]` — No description in code.
  - `config`: `BarraRiskModelConfig` — No description in code. (default: `field(default_factory=BarraRiskModelConfig)`)
  - `model_version`: `str` — No description in code. (default: `'barra_v1.0'`)

- `BarraRiskModelConfig` (dataclass) — `libs/trading/risk/barra_model.py`
  - `annualization_factor`: `int` — No description in code. (default: `252`)
  - `min_coverage`: `float` — No description in code. (default: `0.8`)
  - `var_confidence_95`: `float` — No description in code. (default: `0.95`)
  - `var_confidence_99`: `float` — No description in code. (default: `0.99`)

- `CovarianceConfig` (dataclass) — `libs/trading/risk/factor_covariance.py`
  - `halflife_days`: `int` — No description in code. (default: `60`)
  - `min_observations`: `int` — No description in code. (default: `126`)
  - `newey_west_lags`: `int` — No description in code. (default: `5`)
  - `shrinkage_intensity`: `float | None` — No description in code. (default: `None`)
  - `min_stocks_per_day`: `int` — No description in code. (default: `100`)
  - `lookback_days`: `int` — No description in code. (default: `252`)

- `CovarianceResult` (dataclass) — `libs/trading/risk/factor_covariance.py`
  - `factor_covariance`: `NDArray[np.floating[Any]]` — No description in code.
  - `factor_names`: `list[str]` — No description in code.
  - `factor_returns`: `pl.DataFrame` — No description in code.
  - `as_of_date`: `date` — No description in code.
  - `dataset_version_ids`: `dict[str, str]` — No description in code.
  - `computation_timestamp`: `datetime` — No description in code. (default: `field(default_factory=lambda : datetime.now(UTC))`)
  - `shrinkage_intensity`: `float` — No description in code. (default: `0.0`)
  - `effective_observations`: `float` — No description in code. (default: `0.0`)
  - `reproducibility_hash`: `str` — No description in code. (default: `''`)
  - `skipped_days`: `list[date]` — No description in code. (default: `field(default_factory=list)`)
  - `halflife_days`: `int` — No description in code. (default: `60`)

- `BoxConstraint` (dataclass) — `libs/trading/risk/portfolio_optimizer.py`
  - `min_weight`: `float` — No description in code. (default: `0.0`)
  - `max_weight`: `float` — No description in code. (default: `0.1`)

- `BudgetConstraint` (dataclass) — `libs/trading/risk/portfolio_optimizer.py`
  - `target`: `float` — No description in code. (default: `1.0`)
  - `tolerance`: `float` — No description in code. (default: `0.0`)

- `FactorExposureConstraint` (dataclass) — `libs/trading/risk/portfolio_optimizer.py`
  - `factor_name`: `str` — No description in code.
  - `target_exposure`: `float` — No description in code. (default: `0.0`)
  - `tolerance`: `float` — No description in code. (default: `0.5`)

- `GrossLeverageConstraint` (dataclass) — `libs/trading/risk/portfolio_optimizer.py`
  - `max_leverage`: `float` — No description in code. (default: `1.0`)

- `OptimizationResult` (dataclass) — `libs/trading/risk/portfolio_optimizer.py`
  - `solution_id`: `str` — No description in code.
  - `as_of_date`: `date` — No description in code.
  - `objective`: `str` — No description in code.
  - `status`: `str` — No description in code.
  - `optimal_weights`: `pl.DataFrame` — No description in code.
  - `expected_return`: `float | None` — No description in code.
  - `expected_risk`: `float` — No description in code.
  - `sharpe_ratio`: `float | None` — No description in code.
  - `turnover`: `float` — No description in code.
  - `transaction_cost`: `float` — No description in code.
  - `solver_time_ms`: `int` — No description in code.
  - `solver_status`: `str` — No description in code.
  - `model_version`: `str` — No description in code.
  - `dataset_version_ids`: `dict[str, str]` — No description in code.

- `OptimizerConfig` (dataclass) — `libs/trading/risk/portfolio_optimizer.py`
  - `solver`: `str` — No description in code. (default: `'CLARABEL'`)
  - `solver_timeout`: `float` — No description in code. (default: `30.0`)
  - `verbose`: `bool` — No description in code. (default: `False`)
  - `max_position_weight`: `float` — No description in code. (default: `0.1`)
  - `min_position_weight`: `float` — No description in code. (default: `0.0`)
  - `max_sector_weight`: `float` — No description in code. (default: `0.3`)
  - `max_factor_exposure`: `float` — No description in code. (default: `0.5`)
  - `gross_leverage_max`: `float` — No description in code. (default: `1.0`)
  - `net_exposure_target`: `float` — No description in code. (default: `1.0`)
  - `tc_linear_bps`: `float` — No description in code. (default: `10.0`)
  - `tc_quadratic_bps`: `float` — No description in code. (default: `0.0`)
  - `turnover_penalty`: `float` — No description in code. (default: `0.0`)
  - `risk_free_rate`: `float` — No description in code. (default: `0.0`)
  - `min_coverage`: `float` — No description in code. (default: `0.8`)
  - `enable_constraint_relaxation`: `bool` — No description in code. (default: `False`)

- `RelaxableConstraint` (dataclass) — `libs/trading/risk/portfolio_optimizer.py`
  - `constraint`: `Any` — No description in code.
  - `priority`: `ConstraintPriority` — No description in code.
  - `relaxation_factor`: `float` — No description in code. (default: `1.5`)
  - `max_relaxations`: `int` — No description in code. (default: `3`)
  - `max_relaxation_factor`: `float` — No description in code. (default: `2.0`)
  - `initial_constraint`: `Any` — No description in code. (default: `None`)
  - `current_relaxations`: `int` — No description in code. (default: `field(default=0, init=False)`)

- `ReturnTargetConstraint` (dataclass) — `libs/trading/risk/portfolio_optimizer.py`
  - `expected_returns`: `dict[int, float]` — No description in code.
  - `min_return`: `float` — No description in code.

- `SectorConstraint` (dataclass) — `libs/trading/risk/portfolio_optimizer.py`
  - `sector_map`: `dict[int, str]` — No description in code.
  - `max_sector_weight`: `float` — No description in code. (default: `0.3`)
  - `min_sector_weight`: `float` — No description in code. (default: `0.0`)

- `TurnoverConstraint` (dataclass) — `libs/trading/risk/portfolio_optimizer.py`
  - `current_weights`: `dict[int, float]` — No description in code.
  - `max_turnover`: `float` — No description in code. (default: `0.5`)

- `FactorContribution` (dataclass) — `libs/trading/risk/risk_decomposition.py`
  - `analysis_id`: `str` — No description in code.
  - `factor_name`: `str` — No description in code.
  - `marginal_contribution`: `float` — No description in code.
  - `component_contribution`: `float` — No description in code.
  - `percent_contribution`: `float` — No description in code.

- `PortfolioRiskResult` (dataclass) — `libs/trading/risk/risk_decomposition.py`
  - `analysis_id`: `str` — No description in code.
  - `portfolio_id`: `str` — No description in code.
  - `as_of_date`: `date` — No description in code.
  - `total_risk`: `float` — No description in code.
  - `factor_risk`: `float` — No description in code.
  - `specific_risk`: `float` — No description in code.
  - `var_95`: `float` — No description in code.
  - `var_99`: `float | None` — No description in code.
  - `cvar_95`: `float` — No description in code.
  - `model_version`: `str` — No description in code.
  - `dataset_version_ids`: `dict[str, str]` — No description in code.
  - `computation_timestamp`: `datetime` — No description in code. (default: `field(default_factory=lambda : datetime.now(UTC))`)
  - `factor_contributions`: `pl.DataFrame | None` — No description in code. (default: `None`)
  - `coverage_ratio`: `float` — No description in code. (default: `1.0`)

- `SpecificRiskResult` (dataclass) — `libs/trading/risk/specific_risk.py`
  - `specific_risks`: `pl.DataFrame` — No description in code.
  - `as_of_date`: `date` — No description in code.
  - `dataset_version_ids`: `dict[str, str]` — No description in code.
  - `computation_timestamp`: `datetime` — No description in code. (default: `field(default_factory=lambda : datetime.now(UTC))`)
  - `coverage`: `float` — No description in code. (default: `0.0`)
  - `reproducibility_hash`: `str` — No description in code. (default: `''`)
  - `floored_count`: `int` — No description in code. (default: `0`)

- `StressScenario` (dataclass) — `libs/trading/risk/stress_testing.py`
  - `name`: `str` — No description in code.
  - `scenario_type`: `str` — No description in code.
  - `description`: `str` — No description in code.
  - `factor_shocks`: `dict[str, float] | None` — No description in code. (default: `None`)
  - `start_date`: `date | None` — No description in code. (default: `None`)
  - `end_date`: `date | None` — No description in code. (default: `None`)

- `StressTestResult` (dataclass) — `libs/trading/risk/stress_testing.py`
  - `test_id`: `str` — No description in code.
  - `portfolio_id`: `str` — No description in code.
  - `scenario_name`: `str` — No description in code.
  - `scenario_type`: `str` — No description in code.
  - `as_of_date`: `date` — No description in code.
  - `portfolio_pnl`: `float` — No description in code.
  - `specific_risk_estimate`: `float` — No description in code.
  - `total_pnl`: `float` — No description in code.
  - `factor_impacts`: `dict[str, float]` — No description in code.
  - `worst_position_permno`: `int | None` — No description in code.
  - `worst_position_loss`: `float | None` — No description in code.
  - `position_impacts`: `pl.DataFrame | None` — No description in code.
  - `model_version`: `str` — No description in code.
  - `dataset_version_ids`: `dict[str, str]` — No description in code.
  - `computation_timestamp`: `datetime` — No description in code. (default: `field(default_factory=lambda : datetime.now(UTC))`)

- `LossLimits` (pydantic) — `libs/trading/risk_management/config.py`
  - `daily_loss_limit`: `Decimal` — Maximum daily loss before circuit breaker trips ($) (default: `Field(default=Decimal('5000.00'), description='Maximum daily loss before circuit breaker trips ($)', ge=Decimal('0.00'))`)
  - `max_drawdown_pct`: `Decimal` — Maximum drawdown from peak equity (0.10 = 10%) (default: `Field(default=Decimal('0.10'), description='Maximum drawdown from peak equity (0.10 = 10%)', ge=Decimal('0.01'), le=Decimal('0.50'))`)

- `PortfolioLimits` (pydantic) — `libs/trading/risk_management/config.py`
  - `max_total_notional`: `Decimal` — Maximum total notional exposure ($) (default: `Field(default=Decimal('100000.00'), description='Maximum total notional exposure ($)', ge=Decimal('1000.00'))`)
  - `max_long_exposure`: `Decimal` — Maximum long exposure ($) (default: `Field(default=Decimal('80000.00'), description='Maximum long exposure ($)', ge=Decimal('0.00'))`)
  - `max_short_exposure`: `Decimal` — Maximum short exposure ($) (default: `Field(default=Decimal('20000.00'), description='Maximum short exposure ($)', ge=Decimal('0.00'))`)

- `RiskConfig` (pydantic) — `libs/trading/risk_management/config.py`
  - `position_limits`: `PositionLimits` — No description in code. (default: `Field(default_factory=PositionLimits)`)
  - `portfolio_limits`: `PortfolioLimits` — No description in code. (default: `Field(default_factory=PortfolioLimits)`)
  - `loss_limits`: `LossLimits` — No description in code. (default: `Field(default_factory=LossLimits)`)
  - `blacklist`: `list[str]` — Symbols forbidden from trading (e.g., ['GME', 'AMC']) (default: `Field(default_factory=list, description="Symbols forbidden from trading (e.g., ['GME', 'AMC'])")`)

- `ReleaseResult` (dataclass) — `libs/trading/risk_management/position_reservation.py`
  - `success`: `bool` — No description in code.
  - `reason`: `str` — No description in code.
  - `previous_position`: `int | None` — No description in code. (default: `None`)
  - `new_position`: `int | None` — No description in code. (default: `None`)

- `ReservationResult` (dataclass) — `libs/trading/risk_management/position_reservation.py`
  - `success`: `bool` — No description in code.
  - `token`: `str | None` — No description in code.
  - `reason`: `str` — No description in code.
  - `previous_position`: `int` — No description in code.
  - `new_position`: `int` — No description in code.

### Model Registry

#### Application Models

- `ServiceToken` (dataclass) — `apps/model_registry/auth.py`
  - `token`: `str` — No description in code.
  - `scopes`: `list[str]` — No description in code.
  - `service_name`: `str` — No description in code.

- `CurrentModelResponse` (pydantic) — `apps/model_registry/schemas.py`
  - `model_type`: `str` — Type of model artifact (default: `Field(..., description='Type of model artifact')`)
  - `version`: `str` — Semantic version (default: `Field(..., description='Semantic version')`)
  - `checksum`: `str` — SHA-256 checksum (default: `Field(..., description='SHA-256 checksum')`)
  - `dataset_version_ids`: `dict[str, str]` — Dataset versions used for training (default: `Field(..., description='Dataset versions used for training')`)

- `EnvironmentMetadataResponse` (pydantic) — `apps/model_registry/schemas.py`
  - `python_version`: `str` — Python version (default: `Field(..., description='Python version')`)
  - `dependencies_hash`: `str` — Hash of dependencies (default: `Field(..., description='Hash of dependencies')`)
  - `platform`: `str` — Platform identifier (default: `Field(..., description='Platform identifier')`)
  - `created_by`: `str` — Creator identifier (default: `Field(..., description='Creator identifier')`)
  - `numpy_version`: `str` — NumPy version (default: `Field(..., description='NumPy version')`)
  - `polars_version`: `str` — Polars version (default: `Field(..., description='Polars version')`)
  - `sklearn_version`: `str | None` — scikit-learn version (default: `Field(None, description='scikit-learn version')`)
  - `cvxpy_version`: `str | None` — CVXPY version (default: `Field(None, description='CVXPY version')`)

- `ErrorResponse` (pydantic) — `apps/model_registry/schemas.py`
  - `detail`: `str` — Error message (default: `Field(..., description='Error message')`)
  - `code`: `str` — Error code (default: `Field(..., description='Error code')`)

- `ModelListResponse` (pydantic) — `apps/model_registry/schemas.py`
  - `models`: `list[ModelMetadataResponse]` — List of models (default: `Field(..., description='List of models')`)
  - `total`: `int` — Total count (default: `Field(..., description='Total count')`)

- `ModelMetadataResponse` (pydantic) — `apps/model_registry/schemas.py`
  - `model_id`: `str` — Unique model identifier (default: `Field(..., description='Unique model identifier')`)
  - `model_type`: `str` — Type of model artifact (default: `Field(..., description='Type of model artifact')`)
  - `version`: `str` — Semantic version (default: `Field(..., description='Semantic version')`)
  - `status`: `str` — Model status (staged/production/archived) (default: `Field(..., description='Model status (staged/production/archived)')`)
  - `artifact_path`: `str` — Path to artifact directory (default: `Field(..., description='Path to artifact directory')`)
  - `checksum_sha256`: `str` — SHA-256 checksum (default: `Field(..., description='SHA-256 checksum')`)
  - `dataset_version_ids`: `dict[str, str]` — Dataset versions used for training (default: `Field(..., description='Dataset versions used for training')`)
  - `snapshot_id`: `str` — Snapshot identifier (default: `Field(..., description='Snapshot identifier')`)
  - `factor_list`: `list[str]` — Factors used in model (default: `Field(..., description='Factors used in model')`)
  - `parameters`: `dict[str, Any]` — Model parameters (default: `Field(..., description='Model parameters')`)
  - `metrics`: `dict[str, float]` — Performance metrics (default: `Field(..., description='Performance metrics')`)
  - `config`: `dict[str, Any]` — Training configuration (default: `Field(..., description='Training configuration')`)
  - `config_hash`: `str` — Hash of config (default: `Field(..., description='Hash of config')`)
  - `feature_formulas`: `list[str] | None` — Feature formulas (default: `Field(None, description='Feature formulas')`)
  - `env`: `EnvironmentMetadataResponse` — Environment metadata (default: `Field(..., description='Environment metadata')`)
  - `experiment_id`: `str | None` — Experiment ID (Qlib) (default: `Field(None, description='Experiment ID (Qlib)')`)
  - `run_id`: `str | None` — Run ID (Qlib) (default: `Field(None, description='Run ID (Qlib)')`)
  - `dataset_uri`: `str | None` — Dataset URI (default: `Field(None, description='Dataset URI')`)
  - `qlib_version`: `str | None` — Qlib version (default: `Field(None, description='Qlib version')`)
  - `created_at`: `datetime` — Creation timestamp (default: `Field(..., description='Creation timestamp')`)
  - `promoted_at`: `datetime | None` — Promotion timestamp (default: `Field(None, description='Promotion timestamp')`)

- `ValidationResultResponse` (pydantic) — `apps/model_registry/schemas.py`
  - `valid`: `bool` — Whether model is valid (default: `Field(..., description='Whether model is valid')`)
  - `model_id`: `str` — Model identifier (default: `Field(..., description='Model identifier')`)
  - `checksum_verified`: `bool` — Checksum verification passed (default: `Field(..., description='Checksum verification passed')`)
  - `load_successful`: `bool` — Model loaded successfully (default: `Field(..., description='Model loaded successfully')`)
  - `errors`: `list[str]` — Validation errors (default: `Field(default_factory=list, description='Validation errors')`)

- `CompatibilityResult` (dataclass) — `libs/models/models/compatibility.py`
  - `compatible`: `bool` — No description in code.
  - `level`: `str` — No description in code.
  - `warnings`: `list[str]` — No description in code. (default: `field(default_factory=list)`)

- `ArtifactInfo` (dataclass) — `libs/models/models/types.py`
  - `path`: `str` — No description in code.
  - `checksum`: `str` — No description in code.
  - `size_bytes`: `int` — No description in code.
  - `serialized_at`: `datetime` — No description in code.

- `BackupManifest` (dataclass) — `libs/models/models/types.py`
  - `backup_id`: `str` — No description in code.
  - `created_at`: `datetime` — No description in code.
  - `source_path`: `str` — No description in code.
  - `backup_path`: `str` — No description in code.
  - `checksum`: `str` — No description in code.
  - `size_bytes`: `int` — No description in code.

- `EnvironmentMetadata` (pydantic) — `libs/models/models/types.py`
  - `python_version`: `str` — e.g., '3.11.5' (default: `Field(..., description="e.g., '3.11.5'")`)
  - `dependencies_hash`: `str` — SHA-256 of sorted requirements.txt (default: `Field(..., description='SHA-256 of sorted requirements.txt')`)
  - `platform`: `str` — e.g., 'linux-x86_64' (default: `Field(..., description="e.g., 'linux-x86_64'")`)
  - `created_by`: `str` — User/service that created the model (default: `Field(..., description='User/service that created the model')`)
  - `numpy_version`: `str` — NumPy version (default: `Field(..., description='NumPy version')`)
  - `polars_version`: `str` — Polars version (default: `Field(..., description='Polars version')`)
  - `sklearn_version`: `str | None` — scikit-learn version if used (default: `Field(None, description='scikit-learn version if used')`)
  - `cvxpy_version`: `str | None` — CVXPY version if used (default: `Field(None, description='CVXPY version if used')`)

- `GCReport` (dataclass) — `libs/models/models/types.py`
  - `dry_run`: `bool` — No description in code.
  - `expired_staged`: `list[str]` — No description in code.
  - `expired_archived`: `list[str]` — No description in code.
  - `bytes_freed`: `int` — No description in code.
  - `run_at`: `datetime` — No description in code.

- `ModelMetadata` (pydantic) — `libs/models/models/types.py`
  - `model_id`: `str` — Unique model identifier (default: `Field(..., description='Unique model identifier')`)
  - `model_type`: `ModelType` — Type of model artifact (default: `Field(..., description='Type of model artifact')`)
  - `version`: `str` — Semantic version (immutable) (default: `Field(..., pattern='^v\\d+\\.\\d+\\.\\d+$', description='Semantic version (immutable)')`)
  - `created_at`: `datetime` — Creation timestamp (UTC) (default: `Field(..., description='Creation timestamp (UTC)')`)
  - `dataset_version_ids`: `dict[str, str]` — Dataset versions: {'crsp': 'v1.2.3', 'compustat': 'v1.0.1'} (default: `Field(..., description="Dataset versions: {'crsp': 'v1.2.3', 'compustat': 'v1.0.1'}")`)
  - `snapshot_id`: `str` — DatasetVersionManager snapshot ID (default: `Field(..., description='DatasetVersionManager snapshot ID')`)
  - `factor_list`: `list[str]` — List of factors used (default: `Field(default_factory=list, description='List of factors used')`)
  - `parameters`: `dict[str, Any]` — Model parameters including artifact-specific fields (default: `Field(default_factory=dict, description='Model parameters including artifact-specific fields')`)
  - `checksum_sha256`: `str` — SHA-256 checksum of artifact (default: `Field(..., description='SHA-256 checksum of artifact')`)
  - `metrics`: `dict[str, float]` — Performance metrics (IC, Sharpe, etc.) (default: `Field(default_factory=dict, description='Performance metrics (IC, Sharpe, etc.)')`)
  - `env`: `EnvironmentMetadata` — Environment at creation (default: `Field(..., description='Environment at creation')`)
  - `config`: `dict[str, Any]` — Hyperparameters and settings (default: `Field(default_factory=dict, description='Hyperparameters and settings')`)
  - `config_hash`: `str` — SHA-256 of config dict (default: `Field(..., description='SHA-256 of config dict')`)
  - `feature_formulas`: `list[str] | None` — Phase 3 placeholder for FormulaicFactor (default: `Field(None, description='Phase 3 placeholder for FormulaicFactor')`)
  - `experiment_id`: `str | None` — Experiment grouping ID (default: `Field(None, description='Experiment grouping ID')`)
  - `run_id`: `str | None` — Individual training run ID (default: `Field(None, description='Individual training run ID')`)
  - `dataset_uri`: `str | None` — Reference to dataset location (default: `Field(None, description='Reference to dataset location')`)
  - `qlib_version`: `str | None` — Qlib version if used (default: `Field(None, description='Qlib version if used')`)

- `PromotionGates` (dataclass) — `libs/models/models/types.py`
  - `min_ic`: `float` — No description in code. (default: `0.02`)
  - `min_sharpe`: `float` — No description in code. (default: `0.5`)
  - `min_paper_trade_hours`: `int` — No description in code. (default: `24`)

- `PromotionResult` (dataclass) — `libs/models/models/types.py`
  - `success`: `bool` — No description in code.
  - `model_id`: `str` — No description in code.
  - `from_version`: `str | None` — No description in code.
  - `to_version`: `str` — No description in code.
  - `promoted_at`: `datetime` — No description in code.
  - `message`: `str` — No description in code. (default: `''`)

- `RegistryManifest` (pydantic) — `libs/models/models/types.py`
  - `registry_version`: `str` — Schema version (default: `Field('1.0.0', description='Schema version')`)
  - `created_at`: `datetime` — When registry was created (default: `Field(..., description='When registry was created')`)
  - `last_updated`: `datetime` — Last update timestamp (default: `Field(..., description='Last update timestamp')`)
  - `artifact_count`: `int` — Total number of artifacts (default: `Field(0, description='Total number of artifacts')`)
  - `production_models`: `dict[str, str]` — {model_type: version} (default: `Field(default_factory=dict, description='{model_type: version}')`)
  - `total_size_bytes`: `int` — Total storage used (default: `Field(0, description='Total storage used')`)
  - `checksum`: `str` — SHA-256 of registry.db (default: `Field(..., description='SHA-256 of registry.db')`)
  - `last_backup_at`: `datetime | None` — Last backup timestamp (default: `Field(None, description='Last backup timestamp')`)
  - `backup_location`: `str | None` — S3/GCS path if configured (default: `Field(None, description='S3/GCS path if configured')`)

- `RestoreResult` (dataclass) — `libs/models/models/types.py`
  - `success`: `bool` — No description in code.
  - `backup_date`: `datetime` — No description in code.
  - `restored_at`: `datetime` — No description in code.
  - `models_restored`: `int` — No description in code.
  - `message`: `str` — No description in code. (default: `''`)

- `RollbackResult` (dataclass) — `libs/models/models/types.py`
  - `success`: `bool` — No description in code.
  - `model_type`: `ModelType` — No description in code.
  - `from_version`: `str` — No description in code.
  - `to_version`: `str | None` — No description in code.
  - `rolled_back_at`: `datetime` — No description in code.
  - `message`: `str` — No description in code. (default: `''`)

- `SyncResult` (dataclass) — `libs/models/models/types.py`
  - `success`: `bool` — No description in code.
  - `remote_path`: `str` — No description in code.
  - `synced_at`: `datetime` — No description in code.
  - `bytes_transferred`: `int` — No description in code.
  - `message`: `str` — No description in code. (default: `''`)

- `ValidationResult` (dataclass) — `libs/models/models/types.py`
  - `valid`: `bool` — No description in code.
  - `model_id`: `str` — No description in code.
  - `checksum_verified`: `bool` — No description in code.
  - `load_successful`: `bool` — No description in code.
  - `errors`: `list[str]` — No description in code. (default: `field(default_factory=list)`)

#### SQL Tables

- `model_registry` — `migrations/001_create_model_registry.sql`
  - `id`: `SERIAL`
  - `strategy_name`: `TEXT`
  - `version`: `TEXT`
  - `mlflow_run_id`: `TEXT`
  - `mlflow_experiment_id`: `TEXT`
  - `model_path`: `TEXT`
  - `status`: `TEXT`
  - `performance_metrics`: `JSONB`
  - `config`: `JSONB`
  - `created_at`: `TIMESTAMP`
  - `activated_at`: `TIMESTAMP`
  - `deactivated_at`: `TIMESTAMP`
  - `created_by`: `TEXT`
  - `notes`: `TEXT`
  - constraints:
    - `UNIQUE(strategy_name, version)`

### Data Quality & ETL

#### Application Models

- `ETLProgressManifest` (pydantic) — `libs/data/data_pipeline/historical_etl.py`
  - `dataset`: `str` — No description in code.
  - `last_updated`: `datetime` — No description in code.
  - `symbol_last_dates`: `dict[str, str]` — No description in code.
  - `years_completed`: `list[int]` — No description in code.
  - `years_remaining`: `list[int]` — No description in code.
  - `status`: `Literal['running', 'paused', 'completed', 'failed']` — No description in code.

- `ETLResult` (dataclass) — `libs/data/data_pipeline/historical_etl.py`
  - `total_rows`: `int` — No description in code.
  - `partitions_written`: `list[str]` — No description in code.
  - `symbols_processed`: `list[str]` — No description in code.
  - `start_date`: `date` — No description in code.
  - `end_date`: `date` — No description in code.
  - `duration_seconds`: `float` — No description in code.
  - `manifest_checksum`: `str` — No description in code.

- `SyncProgress` (pydantic) — `libs/data/data_providers/sync_manager.py`
  - `dataset`: `str` — No description in code.
  - `started_at`: `datetime.datetime` — No description in code.
  - `last_checkpoint`: `datetime.datetime` — No description in code.
  - `years_completed`: `list[int]` — No description in code.
  - `years_remaining`: `list[int]` — No description in code.
  - `total_rows_synced`: `int` — No description in code.
  - `status`: `Literal['running', 'paused', 'completed', 'failed']` — No description in code.

- `TAQSyncProgress` (pydantic) — `libs/data/data_providers/taq_storage.py`
  - `dataset`: `str` — No description in code.
  - `tier`: `Literal['aggregates', 'samples']` — No description in code.
  - `started_at`: `datetime.datetime` — No description in code.
  - `last_checkpoint`: `datetime.datetime` — No description in code.
  - `partitions_completed`: `list[str]` — No description in code.
  - `partitions_remaining`: `list[str]` — No description in code.
  - `total_rows_synced`: `int` — No description in code.
  - `status`: `Literal['running', 'paused', 'completed', 'failed']` — No description in code.

- `FetcherConfig` (dataclass) — `libs/data/data_providers/unified_fetcher.py`
  - `provider`: `ProviderType` — No description in code. (default: `ProviderType.AUTO`)
  - `environment`: `str` — No description in code. (default: `'development'`)
  - `yfinance_storage_path`: `Path | None` — No description in code. (default: `None`)
  - `crsp_storage_path`: `Path | None` — No description in code. (default: `None`)
  - `manifest_path`: `Path | None` — No description in code. (default: `None`)
  - `fallback_enabled`: `bool` — No description in code. (default: `True`)

- `SyncManifest` (pydantic) — `libs/data/data_quality/manifest.py`
  - `dataset`: `str` — No description in code.
  - `sync_timestamp`: `datetime` — No description in code.
  - `start_date`: `date` — No description in code.
  - `end_date`: `date` — No description in code.
  - `row_count`: `int` — No description in code.
  - `checksum`: `str` — No description in code.
  - `checksum_algorithm`: `Literal['sha256']` — No description in code. (default: `'sha256'`)
  - `schema_version`: `str` — No description in code.
  - `wrds_query_hash`: `str` — No description in code.
  - `file_paths`: `list[str]` — No description in code.
  - `validation_status`: `Literal['passed', 'failed', 'quarantined']` — No description in code.
  - `quarantine_path`: `str | None` — No description in code. (default: `None`)
  - `manifest_version`: `int` — No description in code. (default: `1`)
  - `previous_checksum`: `str | None` — No description in code. (default: `None`)

- `DatasetSchema` (dataclass) — `libs/data/data_quality/schema.py`
  - `dataset`: `str` — No description in code.
  - `version`: `str` — No description in code.
  - `columns`: `dict[str, str]` — No description in code.
  - `created_at`: `datetime` — No description in code.
  - `description`: `str` — No description in code. (default: `''`)

- `SchemaDrift` (dataclass) — `libs/data/data_quality/schema.py`
  - `added_columns`: `list[str]` — No description in code. (default: `field(default_factory=list)`)
  - `removed_columns`: `list[str]` — No description in code. (default: `field(default_factory=list)`)
  - `changed_columns`: `list[tuple[str, str, str]]` — No description in code. (default: `field(default_factory=list)`)

- `DiskSpaceStatus` (dataclass) — `libs/data/data_quality/types.py`
  - `level`: `Literal['ok', 'warning', 'critical']` — No description in code.
  - `free_bytes`: `int` — No description in code.
  - `total_bytes`: `int` — No description in code.
  - `used_pct`: `float` — No description in code.
  - `message`: `str` — No description in code.

- `LockToken` (dataclass) — `libs/data/data_quality/types.py`
  - `pid`: `int` — No description in code.
  - `hostname`: `str` — No description in code.
  - `writer_id`: `str` — No description in code.
  - `acquired_at`: `datetime.datetime` — No description in code.
  - `expires_at`: `datetime.datetime` — No description in code.
  - `lock_path`: `Path` — No description in code.

- `AnomalyAlert` (dataclass) — `libs/data/data_quality/validation.py`
  - `metric`: `str` — No description in code.
  - `current_value`: `float` — No description in code.
  - `expected_value`: `float` — No description in code.
  - `deviation_pct`: `float` — No description in code.
  - `message`: `str` — No description in code.

- `ValidationError` (dataclass) — `libs/data/data_quality/validation.py`
  - `field`: `str` — No description in code.
  - `message`: `str` — No description in code.
  - `severity`: `Literal['error', 'warning']` — No description in code.
  - `value`: `Any` — No description in code. (default: `None`)

- `BacktestLinkage` (pydantic) — `libs/data/data_quality/versioning.py`
  - `backtest_id`: `str` — No description in code.
  - `created_at`: `datetime` — No description in code.
  - `snapshot_version`: `str` — No description in code.
  - `dataset_versions`: `dict[str, int]` — No description in code.
  - `checksum`: `str` — No description in code.
  - `orphaned_at`: `datetime | None` — No description in code. (default: `None`)

- `CASEntry` (pydantic) — `libs/data/data_quality/versioning.py`
  - `hash`: `str` — No description in code.
  - `size_bytes`: `int` — No description in code.
  - `original_path`: `str` — No description in code.
  - `created_at`: `datetime` — No description in code.
  - `ref_count`: `int` — No description in code.
  - `referencing_snapshots`: `list[str]` — No description in code.

- `CASIndex` (pydantic) — `libs/data/data_quality/versioning.py`
  - `files`: `dict[str, CASEntry]` — No description in code. (default: `Field(default_factory=dict)`)
  - `total_size_bytes`: `int` — No description in code. (default: `0`)
  - `last_gc_at`: `datetime | None` — No description in code. (default: `None`)

- `DatasetSnapshot` (pydantic) — `libs/data/data_quality/versioning.py`
  - `dataset`: `str` — No description in code.
  - `sync_manifest_version`: `int` — No description in code.
  - `files`: `list[FileStorageInfo]` — No description in code.
  - `row_count`: `int` — No description in code.
  - `date_range_start`: `date` — No description in code.
  - `date_range_end`: `date` — No description in code.

- `DiffFileEntry` (pydantic) — `libs/data/data_quality/versioning.py`
  - `path`: `str` — No description in code.
  - `old_hash`: `str | None` — No description in code.
  - `new_hash`: `str` — No description in code.
  - `storage`: `Literal['inline', 'cas']` — No description in code.
  - `inline_data`: `Base64Bytes` — No description in code. (default: `None`)
  - `cas_hash`: `str | None` — No description in code. (default: `None`)

- `FileStorageInfo` (pydantic) — `libs/data/data_quality/versioning.py`
  - `path`: `str` — No description in code.
  - `original_path`: `str` — No description in code.
  - `storage_mode`: `Literal['hardlink', 'copy', 'cas']` — No description in code.
  - `target`: `str` — No description in code.
  - `size_bytes`: `int` — No description in code.
  - `checksum`: `str` — No description in code.

- `SnapshotDiff` (pydantic) — `libs/data/data_quality/versioning.py`
  - `from_version`: `str` — No description in code.
  - `to_version`: `str` — No description in code.
  - `created_at`: `datetime` — No description in code.
  - `added_files`: `list[DiffFileEntry]` — No description in code.
  - `removed_files`: `list[str]` — No description in code.
  - `changed_files`: `list[DiffFileEntry]` — No description in code.
  - `checksum`: `str` — No description in code.
  - `orphaned_at`: `datetime | None` — No description in code. (default: `None`)

- `SnapshotManifest` (pydantic) — `libs/data/data_quality/versioning.py`
  - `version_tag`: `str` — No description in code.
  - `created_at`: `datetime` — No description in code.
  - `datasets`: `dict[str, DatasetSnapshot]` — No description in code.
  - `total_size_bytes`: `int` — No description in code.
  - `aggregate_checksum`: `str` — No description in code.
  - `referenced_by`: `list[str]` — No description in code. (default: `Field(default_factory=list)`)
  - `prev_snapshot_checksum`: `str | None` — No description in code. (default: `None`)

#### SQL Tables

- `data_query_audit` — `db/migrations/0014_query_audit.sql`
  - `id`: `BIGSERIAL`
  - `user_id`: `VARCHAR(255)`
  - `dataset`: `VARCHAR(100)`
  - `query_fingerprint`: `VARCHAR(64)`
  - `query_text`: `TEXT`
  - `row_count`: `INTEGER`
  - `duration_ms`: `INTEGER`
  - `client_ip`: `VARCHAR(45)`
  - `result`: `VARCHAR(20)`
  - `created_at`: `TIMESTAMP`

- `data_sync_logs` — `db/migrations/0012_sync_logs.sql`
  - `id`: `BIGSERIAL`
  - `dataset`: `VARCHAR(100)`
  - `level`: `VARCHAR(20)`
  - `message`: `TEXT`
  - `extra`: `JSONB`
  - `sync_run_id`: `UUID`
  - `created_at`: `TIMESTAMP`

- `data_sync_schedule` — `db/migrations/0013_sync_schedule.sql`
  - `id`: `UUID`
  - `dataset`: `VARCHAR(100)`
  - `enabled`: `BOOLEAN`
  - `cron_expression`: `VARCHAR(100)`
  - `last_scheduled_run`: `TIMESTAMP`
  - `next_scheduled_run`: `TIMESTAMP`
  - `updated_by`: `VARCHAR(255)`
  - `updated_at`: `TIMESTAMP`
  - `created_at`: `TIMESTAMP`
  - `version`: `INTEGER`

- `data_validation_results` — `db/migrations/0015_validation_results.sql`
  - `id`: `UUID`
  - `dataset`: `VARCHAR(100)`
  - `sync_run_id`: `UUID`
  - `validation_type`: `VARCHAR(50)`
  - `status`: `VARCHAR(20)`
  - `expected_value`: `TEXT`
  - `actual_value`: `TEXT`
  - `error_message`: `TEXT`
  - `created_at`: `TIMESTAMP`

### Alerts & Notifications

#### Application Models

- `AlertAcknowledgmentDTO` (pydantic) — `apps/web_console/schemas/data_management.py`
  - `id`: `str` — No description in code.
  - `alert_id`: `str` — No description in code.
  - `dataset`: `str` — No description in code.
  - `metric`: `str` — No description in code.
  - `severity`: `str` — No description in code.
  - `acknowledged_by`: `str` — No description in code.
  - `acknowledged_at`: `AwareDatetime` — No description in code.
  - `reason`: `str | None` — No description in code. (default: `None`)

- `AnomalyAlertDTO` (pydantic) — `apps/web_console/schemas/data_management.py`
  - `id`: `str` — No description in code.
  - `dataset`: `str` — No description in code.
  - `metric`: `str` — No description in code.
  - `severity`: `str` — No description in code.
  - `current_value`: `float | int` — No description in code.
  - `expected_value`: `float | int | None` — No description in code. (default: `None`)
  - `deviation_pct`: `float | None` — No description in code. (default: `None`)
  - `message`: `str` — No description in code.
  - `acknowledged`: `bool` — No description in code.
  - `acknowledged_by`: `str | None` — No description in code. (default: `None`)
  - `created_at`: `AwareDatetime` — No description in code.

- `AlertRuleCreate` (pydantic) — `apps/web_console/services/alert_service.py`
  - `name`: `str` — No description in code.
  - `condition_type`: `str` — No description in code.
  - `threshold_value`: `Decimal` — No description in code.
  - `comparison`: `str` — No description in code.
  - `channels`: `list[ChannelConfig]` — No description in code.
  - `enabled`: `bool` — No description in code. (default: `True`)

- `AlertRuleUpdate` (pydantic) — `apps/web_console/services/alert_service.py`
  - `name`: `str | None` — No description in code. (default: `None`)
  - `condition_type`: `str | None` — No description in code. (default: `None`)
  - `threshold_value`: `Decimal | None` — No description in code. (default: `None`)
  - `comparison`: `str | None` — No description in code. (default: `None`)
  - `channels`: `list[ChannelConfig] | None` — No description in code. (default: `None`)
  - `enabled`: `bool | None` — No description in code. (default: `None`)

- `_RuleChannels` (dataclass) — `libs/platform/alerts/alert_manager.py`
  - `name`: `str` — No description in code.
  - `channels`: `list[ChannelConfig]` — No description in code.

- `AlertDelivery` (pydantic) — `libs/platform/alerts/models.py`
  - `id`: `UUID` — No description in code.
  - `alert_id`: `UUID` — No description in code.
  - `channel`: `ChannelType` — No description in code.
  - `recipient`: `str` — No description in code.
  - `dedup_key`: `str` — No description in code.
  - `status`: `DeliveryStatus` — No description in code.
  - `attempts`: `int` — No description in code. (default: `Field(ge=0, le=3, default=0)`)
  - `last_attempt_at`: `datetime | None` — No description in code. (default: `None`)
  - `delivered_at`: `datetime | None` — No description in code. (default: `None`)
  - `poison_at`: `datetime | None` — No description in code. (default: `None`)
  - `error_message`: `str | None` — No description in code. (default: `None`)
  - `created_at`: `datetime` — No description in code.

- `AlertEvent` (pydantic) — `libs/platform/alerts/models.py`
  - `id`: `UUID` — No description in code.
  - `rule_id`: `UUID` — No description in code.
  - `rule_name`: `str | None` — No description in code. (default: `None`)
  - `triggered_at`: `datetime` — No description in code.
  - `trigger_value`: `Decimal | None` — No description in code. (default: `None`)
  - `acknowledged_at`: `datetime | None` — No description in code. (default: `None`)
  - `acknowledged_by`: `str | None` — No description in code. (default: `None`)
  - `acknowledged_note`: `str | None` — No description in code. (default: `None`)
  - `routed_channels`: `list[str]` — No description in code. (default: `Field(default_factory=list)`)
  - `created_at`: `datetime` — No description in code.

- `AlertRule` (pydantic) — `libs/platform/alerts/models.py`
  - `id`: `UUID` — No description in code.
  - `name`: `str` — No description in code.
  - `condition_type`: `str` — No description in code.
  - `threshold_value`: `Decimal` — No description in code.
  - `comparison`: `str` — No description in code.
  - `channels`: `list[ChannelConfig]` — No description in code.
  - `enabled`: `bool` — No description in code.
  - `created_by`: `str` — No description in code.
  - `created_at`: `datetime` — No description in code.
  - `updated_at`: `datetime` — No description in code.

- `ChannelConfig` (pydantic) — `libs/platform/alerts/models.py`
  - `type`: `ChannelType` — No description in code.
  - `recipient`: `str` — No description in code.
  - `enabled`: `bool` — No description in code. (default: `True`)

- `DeliveryResult` (pydantic) — `libs/platform/alerts/models.py`
  - `success`: `bool` — No description in code.
  - `message_id`: `str | None` — No description in code. (default: `None`)
  - `error`: `str | None` — No description in code. (default: `None`)
  - `retryable`: `bool` — No description in code. (default: `True`)
  - `metadata`: `dict[str, str]` — No description in code. (default: `Field(default_factory=dict)`)

#### SQL Tables

- `alert_deliveries` — `db/migrations/0011_create_alert_tables.sql`
  - `id`: `UUID`
  - `alert_id`: `UUID`
  - `channel`: `VARCHAR(20)`
  - `recipient`: `TEXT`
  - `dedup_key`: `VARCHAR(255)`
  - `status`: `VARCHAR(20)`
  - `attempts`: `INTEGER`
  - `last_attempt_at`: `TIMESTAMPTZ`
  - `delivered_at`: `TIMESTAMPTZ`
  - `poison_at`: `TIMESTAMPTZ`
  - `error_message`: `TEXT`
  - `created_at`: `TIMESTAMPTZ`
  - constraints:
    - `CONSTRAINT alert_deliveries_channel_check CHECK (channel IN ('email', 'slack', 'sms'))`
    - `CONSTRAINT alert_deliveries_status_check CHECK (status IN ('pending', 'in_progress', 'delivered', 'failed', 'poison'))`
    - `CONSTRAINT alert_deliveries_attempts_check CHECK (attempts >= 0 AND attempts <= 3)`

- `alert_events` — `db/migrations/0011_create_alert_tables.sql`
  - `id`: `UUID`
  - `rule_id`: `UUID`
  - `triggered_at`: `TIMESTAMPTZ`
  - `trigger_value`: `NUMERIC`
  - `acknowledged_at`: `TIMESTAMPTZ`
  - `acknowledged_by`: `VARCHAR(255)`
  - `acknowledged_note`: `TEXT`
  - `routed_channels`: `JSONB`
  - `created_at`: `TIMESTAMPTZ`

- `alert_rules` — `db/migrations/0011_create_alert_tables.sql`
  - `id`: `UUID`
  - `name`: `VARCHAR(255)`
  - `condition_type`: `VARCHAR(50)`
  - `threshold_value`: `NUMERIC`
  - `comparison`: `VARCHAR(10)`
  - `channels`: `JSONB`
  - `enabled`: `BOOLEAN`
  - `created_by`: `VARCHAR(255)`
  - `created_at`: `TIMESTAMPTZ`
  - `updated_at`: `TIMESTAMPTZ`

- `data_anomaly_alerts` — `db/migrations/0016_anomaly_alerts.sql`
  - `id`: `UUID`
  - `dataset`: `VARCHAR(100)`
  - `metric`: `VARCHAR(100)`
  - `severity`: `VARCHAR(20)`
  - `current_value`: `DOUBLE`
  - `expected_value`: `DOUBLE`
  - `deviation_pct`: `DOUBLE`
  - `message`: `TEXT`
  - `sync_run_id`: `UUID`
  - `created_at`: `TIMESTAMP`

- `data_quality_alert_acknowledgments` — `db/migrations/0017_alert_acknowledgments.sql`
  - `id`: `UUID`
  - `alert_id`: `UUID`
  - `dataset`: `VARCHAR(100)`
  - `metric`: `VARCHAR(100)`
  - `severity`: `VARCHAR(20)`
  - `acknowledged_by`: `VARCHAR(255)`
  - `acknowledged_at`: `TIMESTAMP`
  - `reason`: `TEXT`
  - `original_alert`: `JSONB`
  - `created_at`: `TIMESTAMP`

### Auth & Users

#### Application Models

- `CSPReportWrapper` (pydantic) — `apps/auth_service/routes/csp_report.py`
  - `csp_report`: `CSPViolationReport` — No description in code. (default: `Field(..., alias='csp-report')`)

- `CSPViolationReport` (pydantic) — `apps/auth_service/routes/csp_report.py`
  - `document_uri`: `str` — No description in code. (default: `Field(..., alias='document-uri')`)
  - `violated_directive`: `str` — No description in code. (default: `Field(..., alias='violated-directive')`)
  - `effective_directive`: `str` — No description in code. (default: `Field(..., alias='effective-directive')`)
  - `original_policy`: `str` — No description in code. (default: `Field(..., alias='original-policy')`)
  - `blocked_uri`: `str` — No description in code. (default: `Field(..., alias='blocked-uri')`)
  - `status_code`: `int` — No description in code. (default: `Field(..., alias='status-code')`)
  - `referrer`: `str` — No description in code. (default: `''`)
  - `source_file`: `str | None` — No description in code. (default: `Field(None, alias='source-file')`)
  - `line_number`: `int | None` — No description in code. (default: `Field(None, alias='line-number')`)
  - `column_number`: `int | None` — No description in code. (default: `Field(None, alias='column-number')`)
  - `sample`: `str | None` — No description in code. (default: `None`)

- `IdPHealthStatus` (pydantic) — `apps/web_console/auth/idp_health.py`
  - `healthy`: `bool` — No description in code.
  - `checked_at`: `datetime` — No description in code.
  - `response_time_ms`: `float` — No description in code.
  - `error`: `str | None` — No description in code. (default: `None`)
  - `consecutive_failures`: `int` — No description in code. (default: `0`)
  - `consecutive_successes`: `int` — No description in code. (default: `0`)
  - `fallback_mode`: `bool` — No description in code. (default: `False`)

- `CertificateInfo` (pydantic) — `apps/web_console/auth/mtls_fallback.py`
  - `valid`: `bool` — No description in code.
  - `cn`: `str` — No description in code.
  - `dn`: `str` — No description in code.
  - `fingerprint`: `str` — No description in code.
  - `not_before`: `datetime` — No description in code.
  - `not_after`: `datetime` — No description in code.
  - `lifetime_days`: `float` — No description in code.
  - `is_admin`: `bool` — No description in code. (default: `False`)
  - `error`: `str | None` — No description in code. (default: `None`)
  - `crl_status`: `str` — No description in code. (default: `'unknown'`)

- `OAuth2Config` (pydantic) — `apps/web_console/auth/oauth2_flow.py`
  - `auth0_domain`: `str` — No description in code.
  - `client_id`: `str` — No description in code.
  - `client_secret`: `str` — No description in code.
  - `audience`: `str` — No description in code.
  - `redirect_uri`: `str` — No description in code.
  - `logout_redirect_uri`: `str` — No description in code.

- `OAuth2State` (pydantic) — `apps/web_console/auth/oauth2_state.py`
  - `state`: `str` — No description in code.
  - `code_verifier`: `str` — No description in code.
  - `nonce`: `str` — No description in code.
  - `code_challenge`: `str` — No description in code.
  - `redirect_uri`: `str` — No description in code.
  - `created_at`: `datetime` — No description in code.

- `SessionData` (pydantic) — `apps/web_console/auth/session_store.py`
  - `access_token`: `str` — No description in code.
  - `refresh_token`: `str` — No description in code.
  - `id_token`: `str` — No description in code.
  - `user_id`: `str` — No description in code.
  - `email`: `str` — No description in code.
  - `created_at`: `datetime` — No description in code.
  - `last_activity`: `datetime` — No description in code.
  - `ip_address`: `str` — No description in code.
  - `user_agent`: `str` — No description in code.
  - `access_token_expires_at`: `datetime | None` — No description in code. (default: `None`)
  - `role`: `str` — No description in code. (default: `'viewer'`)
  - `strategies`: `list[str]` — No description in code. (default: `Field(default_factory=list)`)
  - `session_version`: `int` — No description in code. (default: `1`)
  - `step_up_claims`: `dict[str, Any] | None` — No description in code. (default: `None`)
  - `step_up_requested_at`: `datetime | None` — No description in code. (default: `None`)
  - `pending_action`: `str | None` — No description in code. (default: `None`)

- `UserInfo` (dataclass) — `apps/web_console/services/user_management.py`
  - `user_id`: `str` — No description in code.
  - `role`: `str` — No description in code.
  - `session_version`: `int` — No description in code.
  - `updated_at`: `str` — No description in code.
  - `updated_by`: `str | None` — No description in code.
  - `strategy_count`: `int` — No description in code.

- `AuthResult` (dataclass) — `apps/web_console_ng/auth/auth_result.py`
  - `success`: `bool` — No description in code.
  - `cookie_value`: `str | None` — No description in code. (default: `None`)
  - `csrf_token`: `str | None` — No description in code. (default: `None`)
  - `user_data`: `dict[str, Any] | None` — No description in code. (default: `None`)
  - `error_message`: `str | None` — No description in code. (default: `None`)
  - `warning_message`: `str | None` — No description in code. (default: `None`)
  - `requires_mfa`: `bool` — No description in code. (default: `False`)
  - `rate_limited`: `bool` — No description in code. (default: `False`)
  - `retry_after`: `int` — No description in code. (default: `0`)
  - `locked_out`: `bool` — No description in code. (default: `False`)
  - `lockout_remaining`: `int` — No description in code. (default: `0`)

- `CookieConfig` (dataclass) — `apps/web_console_ng/auth/cookie_config.py`
  - `secure`: `bool` — No description in code.
  - `httponly`: `bool` — No description in code.
  - `samesite`: `str` — No description in code.
  - `path`: `str` — No description in code.
  - `domain`: `str | None` — No description in code.

- `APIAuthConfig` (dataclass) — `libs/core/common/api_auth_dependency.py`
  - `action`: `str` — No description in code.
  - `require_role`: `Role | None` — No description in code. (default: `None`)
  - `require_permission`: `Permission | None` — No description in code. (default: `None`)

- `AuthContext` (dataclass) — `libs/core/common/api_auth_dependency.py`
  - `user`: `AuthenticatedUser | None` — No description in code.
  - `internal_claims`: `InternalTokenClaims | None` — No description in code.
  - `auth_type`: `str` — No description in code.
  - `is_authenticated`: `bool` — No description in code.

- `InternalTokenClaims` (dataclass) — `libs/core/common/api_auth_dependency.py`
  - `service_id`: `str` — No description in code.
  - `user_id`: `str | None` — No description in code.
  - `strategy_id`: `str | None` — No description in code.
  - `nonce`: `str` — No description in code.
  - `timestamp`: `int` — No description in code.

- `AuthConfig` (dataclass) — `libs/platform/web_console_auth/config.py`
  - `jwt_private_key_path`: `Path` — No description in code. (default: `Path('apps/web_console_ng/certs/jwt_private.key')`)
  - `jwt_public_key_path`: `Path` — No description in code. (default: `Path('apps/web_console_ng/certs/jwt_public.pem')`)
  - `jwt_algorithm`: `str` — No description in code. (default: `'RS256'`)
  - `jwt_issuer`: `str` — No description in code. (default: `'trading-platform-web-console'`)
  - `jwt_audience`: `str` — No description in code. (default: `'trading-platform-api'`)
  - `access_token_ttl`: `int` — No description in code. (default: `900`)
  - `refresh_token_ttl`: `int` — No description in code. (default: `14400`)
  - `clock_skew_seconds`: `int` — No description in code. (default: `30`)
  - `max_sessions_per_user`: `int` — No description in code. (default: `3`)
  - `session_binding_strict`: `bool` — No description in code. (default: `True`)
  - `rate_limit_window`: `int` — No description in code. (default: `900`)
  - `rate_limit_max_attempts`: `int` — No description in code. (default: `5`)
  - `rate_limit_enabled`: `bool` — No description in code. (default: `True`)
  - `cookie_secure`: `bool` — No description in code. (default: `True`)
  - `cookie_httponly`: `bool` — No description in code. (default: `True`)
  - `cookie_samesite`: `str` — No description in code. (default: `'Strict'`)
  - `cookie_domain`: `str | None` — No description in code. (default: `None`)
  - `cookie_path`: `str` — No description in code. (default: `'/'`)
  - `cookie_max_age`: `int | None` — No description in code. (default: `None`)
  - `redis_session_prefix`: `str` — No description in code. (default: `'web_console:session:'`)
  - `redis_blacklist_prefix`: `str` — No description in code. (default: `'web_console:token_blacklist:'`)
  - `redis_session_index_prefix`: `str` — No description in code. (default: `'web_console:user_sessions:'`)
  - `redis_rate_limit_prefix`: `str` — No description in code. (default: `'web_console:rate_limit:'`)

- `AuthenticatedUser` (dataclass) — `libs/platform/web_console_auth/gateway_auth.py`
  - `user_id`: `str` — No description in code.
  - `role`: `Role | None` — No description in code.
  - `strategies`: `list[str]` — No description in code.
  - `session_version`: `int` — No description in code.
  - `request_id`: `str` — No description in code.

- `RedisConfig` (dataclass) — `libs/platform/web_console_auth/redis_client.py`
  - `host`: `str` — No description in code.
  - `port`: `int` — No description in code.
  - `db`: `int` — No description in code.

#### SQL Tables

- `api_keys` — `db/migrations/0011_create_api_keys.sql`
  - `id`: `UUID`
  - `user_id`: `VARCHAR(255)`
  - `name`: `VARCHAR(50)`
  - `key_hash`: `VARCHAR(64)`
  - `key_salt`: `VARCHAR(64)`
  - `key_prefix`: `VARCHAR(16)`
  - `scopes`: `JSONB`
  - `expires_at`: `TIMESTAMPTZ`
  - `last_used_at`: `TIMESTAMPTZ`
  - `revoked_at`: `TIMESTAMPTZ`
  - `created_at`: `TIMESTAMPTZ`
  - constraints:
    - `CONSTRAINT fk_api_keys_user FOREIGN KEY (user_id) REFERENCES user_roles(user_id) ON DELETE CASCADE`
    - `CONSTRAINT uq_api_keys_prefix UNIQUE (key_prefix)`
    - `CONSTRAINT chk_key_prefix_format CHECK (key_prefix ~ '^tp_live_[a-zA-Z0-9_-]{8}$')`

- `tax_user_settings` — `db/migrations/0019_create_tax_lots.sql`
  - `id`: `UUID`
  - `user_id`: `VARCHAR(255)`
  - `cost_basis_method`: `VARCHAR(20)`
  - `created_at`: `TIMESTAMPTZ`
  - `updated_at`: `TIMESTAMPTZ`

- `user_roles` — `db/migrations/0006_create_rbac_tables.sql`
  - `user_id`: `VARCHAR(255)`
  - `role`: `VARCHAR(20)`
  - `session_version`: `INTEGER`
  - `updated_by`: `VARCHAR(255)`
  - `updated_at`: `TIMESTAMPTZ`
  - constraints:
    - `CONSTRAINT valid_role CHECK (role IN ('viewer', 'operator', 'admin'))`

- `user_strategy_access` — `db/migrations/0006_create_rbac_tables.sql`
  - `user_id`: `VARCHAR(255)`
  - `strategy_id`: `VARCHAR(50)`
  - `granted_by`: `VARCHAR(255)`
  - `granted_at`: `TIMESTAMPTZ`
  - constraints:
    - `PRIMARY KEY (user_id, strategy_id)`
    - `CONSTRAINT fk_user FOREIGN KEY (user_id) REFERENCES user_roles(user_id) ON DELETE CASCADE`
    - `CONSTRAINT fk_strategy FOREIGN KEY (strategy_id) REFERENCES strategies(strategy_id) ON DELETE CASCADE`

### Config & Admin

#### Application Models

- `SystemDefaultsConfig` (pydantic) — `apps/web_console/components/config_editor.py`
  - `dry_run`: `bool` — No description in code. (default: `True`)
  - `circuit_breaker_enabled`: `bool` — No description in code. (default: `True`)
  - `drawdown_threshold`: `Decimal` — No description in code. (default: `Field(default=Decimal('0.05'), ge=Decimal('0.01'), le=Decimal('0.50'))`)

- `TradingHoursConfig` (pydantic) — `apps/web_console/components/config_editor.py`
  - `market_open`: `time` — No description in code. (default: `time(9, 30)`)
  - `market_close`: `time` — No description in code. (default: `time(16, 0)`)
  - `pre_market_enabled`: `bool` — No description in code. (default: `False`)
  - `after_hours_enabled`: `bool` — No description in code. (default: `False`)

- `CombinerConfig` (dataclass) — `libs/trading/alpha/alpha_combiner.py`
  - `weighting`: `WeightingMethod` — No description in code. (default: `WeightingMethod.IC`)
  - `lookback_days`: `int` — No description in code. (default: `252`)
  - `min_lookback_days`: `int` — No description in code. (default: `60`)
  - `normalize`: `bool` — No description in code. (default: `True`)
  - `correlation_threshold`: `float` — No description in code. (default: `0.7`)
  - `winsorize_pct`: `float` — No description in code. (default: `0.01`)

- `FactorAttributionConfig` (dataclass) — `libs/platform/analytics/attribution.py`
  - `model`: `Literal['ff3', 'ff5', 'ff6']` — No description in code. (default: `'ff5'`)
  - `window_trading_days`: `int` — No description in code. (default: `252`)
  - `rebalance_freq`: `Literal['daily', 'weekly', 'monthly']` — No description in code. (default: `'monthly'`)
  - `std_errors`: `Literal['ols', 'hc3', 'newey_west']` — No description in code. (default: `'newey_west'`)
  - `newey_west_lags`: `int` — No description in code. (default: `0`)
  - `min_observations`: `int` — No description in code. (default: `60`)
  - `vif_threshold`: `float` — No description in code. (default: `5.0`)
  - `annualization_factor`: `int` — No description in code. (default: `252`)
  - `min_market_cap_usd`: `float | None` — No description in code. (default: `100000000`)
  - `market_cap_percentile`: `float | None` — No description in code. (default: `0.2`)
  - `currency`: `str | None` — No description in code. (default: `'USD'`)
  - `aggregation_method`: `Literal['equal_weight', 'value_weight']` — No description in code. (default: `'equal_weight'`)
  - `rebalance_on_filter`: `bool` — No description in code. (default: `True`)

- `EventStudyConfig` (dataclass) — `libs/platform/analytics/event_study.py`
  - `estimation_window`: `int` — No description in code. (default: `120`)
  - `gap_days`: `int` — No description in code. (default: `5`)
  - `pre_window`: `int` — No description in code. (default: `5`)
  - `post_window`: `int` — No description in code. (default: `20`)
  - `min_estimation_obs`: `int` — No description in code. (default: `60`)
  - `expected_return_model`: `ExpectedReturnModel` — No description in code. (default: `ExpectedReturnModel.MARKET`)
  - `significance_test`: `SignificanceTest` — No description in code. (default: `SignificanceTest.T_TEST`)
  - `newey_west_lags`: `int | None` — No description in code. (default: `None`)
  - `overlap_policy`: `OverlapPolicy` — No description in code. (default: `OverlapPolicy.DROP_LATER`)
  - `min_days_between_events`: `int | None` — No description in code. (default: `None`)
  - `clustering_mitigation`: `ClusteringMitigation` — No description in code. (default: `ClusteringMitigation.AUTO`)
  - `winsorize_ar_percentile`: `float` — No description in code. (default: `0.99`)
  - `cap_beta`: `float` — No description in code. (default: `5.0`)
  - `roll_nontrading_direction`: `Literal['forward', 'backward']` — No description in code. (default: `'forward'`)

- `BacktestJobConfig` (dataclass) — `libs/trading/backtest/job_queue.py`
  - `alpha_name`: `str` — No description in code.
  - `start_date`: `date` — No description in code.
  - `end_date`: `date` — No description in code.
  - `weight_method`: `WeightMethod` — No description in code. (default: `WeightMethod.ZSCORE`)
  - `extra_params`: `dict[str, Any]` — No description in code. (default: `field(default_factory=dict)`)

- `MonteCarloConfig` (dataclass) — `libs/trading/backtest/monte_carlo.py`
  - `n_simulations`: `int` — No description in code. (default: `1000`)
  - `method`: `Literal['bootstrap', 'shuffle']` — No description in code. (default: `'bootstrap'`)
  - `random_seed`: `int | None` — No description in code. (default: `None`)
  - `confidence_levels`: `tuple[float, ...]` — No description in code. (default: `(0.05, 0.5, 0.95)`)

- `WalkForwardConfig` (dataclass) — `libs/trading/backtest/walk_forward.py`
  - `train_months`: `int` — No description in code. (default: `12`)
  - `test_months`: `int` — No description in code. (default: `3`)
  - `step_months`: `int` — No description in code. (default: `3`)
  - `min_train_samples`: `int` — No description in code. (default: `252`)
  - `overfitting_threshold`: `float` — No description in code. (default: `2.0`)

- `RateLimitConfig` (dataclass) — `libs/core/common/rate_limit_dependency.py`
  - `action`: `str` — No description in code.
  - `max_requests`: `int` — No description in code.
  - `window_seconds`: `int` — No description in code. (default: `60`)
  - `burst_buffer`: `int` — No description in code. (default: `0`)
  - `fallback_mode`: `str` — No description in code. (default: `'deny'`)
  - `global_limit`: `int | None` — No description in code. (default: `None`)
  - `anonymous_factor`: `float` — No description in code. (default: `0.1`)

- `FactorConfig` (dataclass) — `libs/models/factors/factor_definitions.py`
  - `winsorize_pct`: `float` — No description in code. (default: `0.01`)
  - `neutralize_sector`: `bool` — No description in code. (default: `True`)
  - `min_stocks_per_sector`: `int` — No description in code. (default: `5`)
  - `lookback_days`: `int` — No description in code. (default: `365`)
  - `report_date_column`: `str | None` — No description in code. (default: `None`)

- `DataConfig` (dataclass) — `strategies/alpha_baseline/config.py`
  - `symbols`: `list[str]` — No description in code. (default: `field(default_factory=lambda : ['AAPL', 'MSFT', 'GOOGL'])`)
  - `data_dir`: `Path` — No description in code. (default: `Path('data/adjusted')`)
  - `train_start`: `str` — No description in code. (default: `'2020-01-01'`)
  - `train_end`: `str` — No description in code. (default: `'2023-12-31'`)
  - `valid_start`: `str` — No description in code. (default: `'2024-01-01'`)
  - `valid_end`: `str` — No description in code. (default: `'2024-06-30'`)
  - `test_start`: `str` — No description in code. (default: `'2024-07-01'`)
  - `test_end`: `str` — No description in code. (default: `'2024-12-31'`)

- `ModelConfig` (dataclass) — `strategies/alpha_baseline/config.py`
  - `objective`: `str` — No description in code. (default: `'regression'`)
  - `metric`: `str` — No description in code. (default: `'mae'`)
  - `boosting_type`: `str` — No description in code. (default: `'gbdt'`)
  - `num_boost_round`: `int` — No description in code. (default: `100`)
  - `learning_rate`: `float` — No description in code. (default: `0.05`)
  - `max_depth`: `int` — No description in code. (default: `6`)
  - `num_leaves`: `int` — No description in code. (default: `31`)
  - `feature_fraction`: `float` — No description in code. (default: `0.8`)
  - `bagging_fraction`: `float` — No description in code. (default: `0.8`)
  - `bagging_freq`: `int` — No description in code. (default: `5`)
  - `min_data_in_leaf`: `int` — No description in code. (default: `20`)
  - `lambda_l1`: `float` — No description in code. (default: `0.1`)
  - `lambda_l2`: `float` — No description in code. (default: `0.1`)
  - `verbose`: `int` — No description in code. (default: `-1`)
  - `seed`: `int` — No description in code. (default: `42`)
  - `num_threads`: `int` — No description in code. (default: `4`)

- `StrategyConfig` (dataclass) — `strategies/alpha_baseline/config.py`
  - `data`: `DataConfig` — No description in code. (default: `field(default_factory=DataConfig)`)
  - `model`: `ModelConfig` — No description in code. (default: `field(default_factory=ModelConfig)`)
  - `training`: `TrainingConfig` — No description in code. (default: `field(default_factory=TrainingConfig)`)

- `TrainingConfig` (dataclass) — `strategies/alpha_baseline/config.py`
  - `early_stopping_rounds`: `int` — No description in code. (default: `20`)
  - `save_best_only`: `bool` — No description in code. (default: `True`)
  - `model_dir`: `Path` — No description in code. (default: `Path('artifacts/models')`)
  - `experiment_name`: `str` — No description in code. (default: `'alpha_baseline'`)
  - `run_name`: `str | None` — No description in code. (default: `None`)

- `AdaptiveWeightConfig` (dataclass) — `strategies/ensemble/config.py`
  - `enabled`: `bool` — No description in code. (default: `False`)
  - `lookback_days`: `int` — No description in code. (default: `30`)
  - `update_frequency`: `Literal['intraday', 'daily', 'weekly']` — No description in code. (default: `'daily'`)
  - `min_trades`: `int` — No description in code. (default: `10`)
  - `performance_metric`: `Literal['sharpe', 'returns', 'win_rate', 'profit_factor']` — No description in code. (default: `'sharpe'`)
  - `smoothing_factor`: `float` — No description in code. (default: `0.2`)

- `EnsembleConfig` (dataclass) — `strategies/ensemble/config.py`
  - `combination_method`: `CombinationMethod` — No description in code. (default: `CombinationMethod.WEIGHTED_AVERAGE`)
  - `strategy_weights`: `dict[str, float]` — No description in code. (default: `field(default_factory=lambda : {'mean_reversion': 0.5, 'momentum': 0.5})`)
  - `min_confidence`: `float` — No description in code. (default: `0.6`)
  - `signal_threshold`: `float` — No description in code. (default: `0.3`)
  - `min_strategies`: `int` — No description in code. (default: `2`)
  - `require_agreement`: `bool` — No description in code. (default: `False`)
  - `version`: `str` — No description in code. (default: `'0.1.0'`)

- `MeanReversionConfig` (dataclass) — `research/strategies/mean_reversion/config.py`
  - `features`: `MeanReversionFeatureConfig` — No description in code. (default: `field(default_factory=MeanReversionFeatureConfig)`)
  - `model`: `MeanReversionModelConfig` — No description in code. (default: `field(default_factory=MeanReversionModelConfig)`)
  - `trading`: `MeanReversionTradingConfig` — No description in code. (default: `field(default_factory=MeanReversionTradingConfig)`)
  - `strategy_name`: `str` — No description in code. (default: `'mean_reversion'`)
  - `version`: `str` — No description in code. (default: `'0.1.0'`)

- `MeanReversionFeatureConfig` (dataclass) — `research/strategies/mean_reversion/config.py`
  - `rsi_period`: `int` — No description in code. (default: `14`)
  - `bb_period`: `int` — No description in code. (default: `20`)
  - `bb_std`: `float` — No description in code. (default: `2.0`)
  - `stoch_k_period`: `int` — No description in code. (default: `14`)
  - `stoch_d_period`: `int` — No description in code. (default: `3`)
  - `zscore_period`: `int` — No description in code. (default: `20`)

- `MeanReversionModelConfig` (dataclass) — `research/strategies/mean_reversion/config.py`
  - `objective`: `str` — No description in code. (default: `'regression'`)
  - `metric`: `str` — No description in code. (default: `'rmse'`)
  - `num_leaves`: `int` — No description in code. (default: `31`)
  - `learning_rate`: `float` — No description in code. (default: `0.05`)
  - `feature_fraction`: `float` — No description in code. (default: `0.8`)
  - `bagging_fraction`: `float` — No description in code. (default: `0.8`)
  - `bagging_freq`: `int` — No description in code. (default: `5`)
  - `min_data_in_leaf`: `int` — No description in code. (default: `50`)
  - `lambda_l1`: `float` — No description in code. (default: `0.1`)
  - `lambda_l2`: `float` — No description in code. (default: `0.1`)
  - `max_depth`: `int` — No description in code. (default: `7`)
  - `num_boost_round`: `int` — No description in code. (default: `100`)
  - `early_stopping_rounds`: `int` — No description in code. (default: `20`)

- `MeanReversionTradingConfig` (dataclass) — `research/strategies/mean_reversion/config.py`
  - `rsi_oversold`: `float` — No description in code. (default: `30.0`)
  - `rsi_overbought`: `float` — No description in code. (default: `70.0`)
  - `bb_entry_threshold`: `float` — No description in code. (default: `0.0`)
  - `zscore_entry`: `float` — No description in code. (default: `-2.0`)
  - `zscore_exit`: `float` — No description in code. (default: `0.0`)
  - `min_confidence`: `float` — No description in code. (default: `0.6`)
  - `max_position_size`: `float` — No description in code. (default: `0.1`)
  - `stop_loss_pct`: `float` — No description in code. (default: `0.05`)
  - `take_profit_pct`: `float` — No description in code. (default: `0.1`)

- `MomentumConfig` (dataclass) — `research/strategies/momentum/config.py`
  - `features`: `MomentumFeatureConfig` — No description in code. (default: `field(default_factory=MomentumFeatureConfig)`)
  - `model`: `MomentumModelConfig` — No description in code. (default: `field(default_factory=MomentumModelConfig)`)
  - `trading`: `MomentumTradingConfig` — No description in code. (default: `field(default_factory=MomentumTradingConfig)`)
  - `strategy_name`: `str` — No description in code. (default: `'momentum'`)
  - `version`: `str` — No description in code. (default: `'0.1.0'`)

- `MomentumFeatureConfig` (dataclass) — `research/strategies/momentum/config.py`
  - `ma_fast_period`: `int` — No description in code. (default: `10`)
  - `ma_slow_period`: `int` — No description in code. (default: `50`)
  - `macd_fast`: `int` — No description in code. (default: `12`)
  - `macd_slow`: `int` — No description in code. (default: `26`)
  - `macd_signal`: `int` — No description in code. (default: `9`)
  - `roc_period`: `int` — No description in code. (default: `14`)
  - `adx_period`: `int` — No description in code. (default: `14`)

- `MomentumModelConfig` (dataclass) — `research/strategies/momentum/config.py`
  - `objective`: `str` — No description in code. (default: `'regression'`)
  - `metric`: `str` — No description in code. (default: `'rmse'`)
  - `num_leaves`: `int` — No description in code. (default: `31`)
  - `learning_rate`: `float` — No description in code. (default: `0.05`)
  - `feature_fraction`: `float` — No description in code. (default: `0.8`)
  - `bagging_fraction`: `float` — No description in code. (default: `0.8`)
  - `bagging_freq`: `int` — No description in code. (default: `5`)
  - `min_data_in_leaf`: `int` — No description in code. (default: `50`)
  - `lambda_l1`: `float` — No description in code. (default: `0.1`)
  - `lambda_l2`: `float` — No description in code. (default: `0.1`)
  - `max_depth`: `int` — No description in code. (default: `7`)
  - `num_boost_round`: `int` — No description in code. (default: `100`)
  - `early_stopping_rounds`: `int` — No description in code. (default: `20`)

- `MomentumTradingConfig` (dataclass) — `research/strategies/momentum/config.py`
  - `adx_threshold`: `float` — No description in code. (default: `25.0`)
  - `roc_entry`: `float` — No description in code. (default: `5.0`)
  - `macd_entry`: `bool` — No description in code. (default: `True`)
  - `ma_cross_required`: `bool` — No description in code. (default: `True`)
  - `min_confidence`: `float` — No description in code. (default: `0.6`)
  - `max_position_size`: `float` — No description in code. (default: `0.1`)
  - `stop_loss_pct`: `float` — No description in code. (default: `0.05`)
  - `take_profit_pct`: `float` — No description in code. (default: `0.15`)

#### SQL Tables

- `system_config` — `db/migrations/0011_create_api_keys.sql`
  - `id`: `UUID`
  - `config_key`: `VARCHAR(100)`
  - `config_value`: `JSONB`
  - `config_type`: `VARCHAR(50)`
  - `updated_by`: `VARCHAR(255)`
  - `updated_at`: `TIMESTAMPTZ`
  - `created_at`: `TIMESTAMPTZ`
  - constraints:
    - `CONSTRAINT chk_config_type CHECK (config_type IN ('trading_hours', 'position_limits', 'system_defaults'))`

### Tax & Reporting

#### Application Models

- `ReportRun` (dataclass) — `apps/web_console/services/scheduled_reports_service.py`
  - `id`: `str` — No description in code.
  - `schedule_id`: `str` — No description in code.
  - `run_key`: `str` — No description in code.
  - `status`: `str` — No description in code.
  - `started_at`: `datetime | None` — No description in code.
  - `completed_at`: `datetime | None` — No description in code.
  - `error_message`: `str | None` — No description in code.
  - `format`: `str` — No description in code. (default: `'pdf'`)

- `ReportSchedule` (dataclass) — `apps/web_console/services/scheduled_reports_service.py`
  - `id`: `str` — No description in code.
  - `user_id`: `str` — No description in code.
  - `name`: `str` — No description in code.
  - `report_type`: `str` — No description in code.
  - `cron`: `str | None` — No description in code.
  - `params`: `dict[str, Any]` — No description in code.
  - `recipients`: `list[str]` — No description in code.
  - `strategies`: `list[str]` — No description in code.
  - `enabled`: `bool` — No description in code.
  - `last_run_at`: `datetime | None` — No description in code.
  - `next_run_at`: `datetime | None` — No description in code.
  - `created_at`: `datetime` — No description in code.
  - `updated_at`: `datetime` — No description in code.

- `TaxLot` (dataclass) — `apps/web_console/services/tax_lot_service.py`
  - `lot_id`: `str` — No description in code.
  - `symbol`: `str` — No description in code.
  - `quantity`: `Decimal` — No description in code.
  - `cost_basis`: `Decimal` — No description in code.
  - `acquisition_date`: `datetime` — No description in code.
  - `strategy_id`: `str | None` — No description in code.
  - `status`: `str` — No description in code.

- `TaxReportRow` (dataclass) — `libs/platform/tax/export.py`
  - `symbol`: `str` — No description in code.
  - `quantity`: `Decimal` — No description in code.
  - `acquired_date`: `date` — No description in code.
  - `disposed_date`: `date` — No description in code.
  - `cost_basis`: `Decimal` — No description in code.
  - `proceeds`: `Decimal` — No description in code.
  - `gain_loss`: `Decimal` — No description in code.
  - `holding_period`: `str` — No description in code.
  - `wash_sale_adjustment`: `Decimal | None` — No description in code. (default: `None`)
  - `lot_id`: `str | None` — No description in code. (default: `None`)
  - `disposition_id`: `str | None` — No description in code. (default: `None`)

- `Form8949Row` (dataclass) — `libs/platform/tax/form_8949.py`
  - `description`: `str` — No description in code.
  - `date_acquired`: `date` — No description in code.
  - `date_sold`: `date` — No description in code.
  - `proceeds`: `Decimal` — No description in code.
  - `cost_basis`: `Decimal` — No description in code.
  - `adjustment_code`: `str | None` — No description in code.
  - `adjustment_amount`: `Decimal | None` — No description in code.
  - `gain_or_loss`: `Decimal` — No description in code.

- `HarvestingOpportunity` (dataclass) — `libs/platform/tax/tax_loss_harvesting.py`
  - `lot_id`: `UUID` — No description in code.
  - `symbol`: `str` — No description in code.
  - `shares`: `Decimal` — No description in code.
  - `cost_basis`: `Decimal` — No description in code.
  - `current_price`: `Decimal` — No description in code.
  - `unrealized_loss`: `Decimal` — No description in code.
  - `holding_period`: `str` — No description in code.
  - `wash_sale_risk`: `bool` — No description in code.
  - `wash_sale_clear_date`: `date | None` — No description in code.
  - `repurchase_restricted_until`: `date | None` — No description in code. (default: `None`)

- `HarvestingRecommendation` (dataclass) — `libs/platform/tax/tax_loss_harvesting.py`
  - `opportunities`: `list[HarvestingOpportunity]` — No description in code.
  - `total_harvestable_loss`: `Decimal` — No description in code.
  - `estimated_tax_savings`: `Decimal` — No description in code.
  - `warnings`: `list[str]` — No description in code.

- `WashSaleAdjustment` (dataclass) — `libs/platform/tax/wash_sale_detector.py`
  - `lot_id`: `UUID` — No description in code.
  - `disallowed_loss`: `Decimal` — No description in code.
  - `basis_adjustment`: `Decimal` — No description in code.
  - `holding_period_adjustment_days`: `int` — No description in code.

- `WashSaleMatch` (dataclass) — `libs/platform/tax/wash_sale_detector.py`
  - `loss_disposition_id`: `UUID` — No description in code.
  - `replacement_lot_id`: `UUID` — No description in code.
  - `symbol`: `str` — No description in code.
  - `disallowed_loss`: `Decimal` — No description in code.
  - `matching_shares`: `Decimal` — No description in code.
  - `sale_date`: `datetime` — No description in code.
  - `replacement_date`: `datetime` — No description in code.

#### SQL Tables

- `report_archives` — `db/migrations/0018_create_report_tables.sql`
  - `id`: `UUID`
  - `schedule_id`: `UUID`
  - `user_id`: `VARCHAR(255)`
  - `idempotency_key`: `VARCHAR(100)`
  - `generated_at`: `TIMESTAMPTZ`
  - `file_path`: `VARCHAR(500)`
  - `file_format`: `VARCHAR(20)`
  - `file_size_bytes`: `BIGINT`
  - `created_at`: `TIMESTAMPTZ`

- `report_schedule_runs` — `db/migrations/0018_create_report_tables.sql`
  - `id`: `UUID`
  - `schedule_id`: `UUID`
  - `run_key`: `VARCHAR(100)`
  - `status`: `VARCHAR(20)`
  - `started_at`: `TIMESTAMPTZ`
  - `completed_at`: `TIMESTAMPTZ`
  - `error_message`: `TEXT`

- `report_schedules` — `db/migrations/0018_create_report_tables.sql`
  - `id`: `UUID`
  - `user_id`: `VARCHAR(255)`
  - `name`: `VARCHAR(255)`
  - `template_type`: `VARCHAR(50)`
  - `schedule_config`: `JSONB`
  - `recipients`: `JSONB`
  - `strategies`: `JSONB`
  - `enabled`: `BOOLEAN`
  - `last_run_at`: `TIMESTAMPTZ`
  - `next_run_at`: `TIMESTAMPTZ`
  - `created_at`: `TIMESTAMPTZ`
  - `updated_at`: `TIMESTAMPTZ`

- `tax_lots` — `db/migrations/0019_create_tax_lots.sql`
  - `id`: `UUID`
  - `user_id`: `VARCHAR(255)`
  - `symbol`: `VARCHAR(20)`
  - `quantity`: `DECIMAL(18,`
  - `cost_per_share`: `DECIMAL(18,`
  - `total_cost`: `DECIMAL(18,`
  - `acquired_at`: `TIMESTAMPTZ`
  - `acquisition_type`: `VARCHAR(20)`
  - `remaining_quantity`: `DECIMAL(18,`
  - `closed_at`: `TIMESTAMPTZ`
  - `created_at`: `TIMESTAMPTZ`

- `tax_wash_sale_adjustments` — `db/migrations/0019_create_tax_lots.sql`
  - `id`: `UUID`
  - `disposition_id`: `UUID`
  - `replacement_lot_id`: `UUID`
  - `matching_shares`: `DECIMAL(18,`
  - `disallowed_loss`: `DECIMAL(18,`
  - `holding_period_adjustment_days`: `INTEGER`
  - `created_at`: `TIMESTAMPTZ`
  - constraints:
    - `UNIQUE(disposition_id, replacement_lot_id)`

### Health & Observability

#### Application Models

- `HealthData` (dataclass) — `apps/web_console/pages/health.py`
  - `statuses`: `dict[str, ServiceHealthResponse]` — No description in code.
  - `connectivity`: `ConnectivityStatus` — No description in code.
  - `latencies`: `dict[str, LatencyMetrics]` — No description in code.
  - `latencies_stale`: `bool` — No description in code.
  - `latencies_age`: `float | None` — No description in code.

- `ConnectivityStatus` (pydantic) — `apps/web_console/services/health_service.py`
  - `redis_connected`: `bool` — No description in code.
  - `redis_info`: `dict[str, Any] | None` — No description in code.
  - `redis_error`: `str | None` — No description in code. (default: `None`)
  - `postgres_connected`: `bool` — No description in code.
  - `postgres_latency_ms`: `float | None` — No description in code.
  - `postgres_error`: `str | None` — No description in code. (default: `None`)
  - `checked_at`: `datetime` — No description in code.
  - `is_stale`: `bool` — No description in code. (default: `False`)
  - `stale_age_seconds`: `float | None` — No description in code. (default: `None`)

- `ServiceHealthResponse` (pydantic) — `libs/core/health/health_client.py`
  - `status`: `str` — No description in code.
  - `service`: `str` — No description in code.
  - `timestamp`: `datetime` — No description in code.
  - `response_time_ms`: `float` — No description in code.
  - `details`: `dict[str, Any]` — No description in code.
  - `error`: `str | None` — No description in code. (default: `None`)
  - `is_stale`: `bool` — No description in code. (default: `False`)
  - `stale_age_seconds`: `float | None` — No description in code. (default: `None`)
  - `last_operation_timestamp`: `datetime | None` — No description in code. (default: `None`)

- `LatencyMetrics` (pydantic) — `libs/core/health/prometheus_client.py`
  - `service`: `str` — No description in code.
  - `operation`: `str` — No description in code.
  - `p50_ms`: `float | None` — No description in code.
  - `p95_ms`: `float | None` — No description in code.
  - `p99_ms`: `float | None` — No description in code.
  - `error`: `str | None` — No description in code. (default: `None`)
  - `is_stale`: `bool` — No description in code. (default: `False`)
  - `stale_age_seconds`: `float | None` — No description in code. (default: `None`)
  - `fetched_at`: `datetime | None` — No description in code. (default: `None`)

### Backtest & Research

#### Application Models

- `CombineResult` (dataclass) — `libs/trading/alpha/alpha_combiner.py`
  - `composite_signal`: `pl.DataFrame` — No description in code.
  - `signal_weights`: `dict[str, float]` — No description in code.
  - `weight_history`: `pl.DataFrame | None` — No description in code.
  - `correlation_analysis`: `CorrelationAnalysisResult` — No description in code.
  - `coverage_pct`: `float` — No description in code.
  - `turnover_result`: `TurnoverResult | None` — No description in code.
  - `warnings`: `list[str]` — No description in code.
  - `weighting_method`: `WeightingMethod` — No description in code.
  - `lookback_days`: `int` — No description in code.
  - `computation_timestamp`: `datetime` — No description in code.
  - `n_signals_combined`: `int` — No description in code.
  - `date_range`: `tuple[date, date]` — No description in code.

- `CorrelationAnalysisResult` (dataclass) — `libs/trading/alpha/alpha_combiner.py`
  - `correlation_matrix`: `pl.DataFrame` — No description in code.
  - `highly_correlated_pairs`: `list[tuple[str, str, float]]` — No description in code.
  - `condition_number`: `float` — No description in code.
  - `warnings`: `list[str]` — No description in code. (default: `field(default_factory=list)`)

- `AlphaResult` (dataclass) — `libs/trading/alpha/alpha_definition.py`
  - `alpha_name`: `str` — No description in code.
  - `as_of_date`: `date` — No description in code.
  - `signals`: `pl.DataFrame` — No description in code.
  - `dataset_version_ids`: `dict[str, str]` — No description in code.
  - `computation_timestamp`: `datetime` — No description in code. (default: `field(default_factory=lambda : datetime.now(UTC))`)
  - `n_stocks`: `int` — No description in code. (default: `0`)
  - `coverage`: `float` — No description in code. (default: `0.0`)

- `DecayCurveResult` (dataclass) — `libs/trading/alpha/metrics.py`
  - `decay_curve`: `pl.DataFrame` — No description in code.
  - `half_life`: `float | None` — No description in code.

- `ICIRResult` (dataclass) — `libs/trading/alpha/metrics.py`
  - `icir`: `float` — No description in code.
  - `mean_ic`: `float` — No description in code.
  - `std_ic`: `float` — No description in code.
  - `n_periods`: `int` — No description in code.

- `ICResult` (dataclass) — `libs/trading/alpha/metrics.py`
  - `pearson_ic`: `float` — No description in code.
  - `rank_ic`: `float` — No description in code.
  - `n_observations`: `int` — No description in code.
  - `coverage`: `float` — No description in code.

- `TurnoverResult` (dataclass) — `libs/trading/alpha/portfolio.py`
  - `daily_turnover`: `pl.DataFrame` — No description in code.
  - `average_turnover`: `float` — No description in code.
  - `annualized_turnover`: `float` — No description in code.

- `BacktestResult` (dataclass) — `libs/trading/alpha/research_platform.py`
  - `alpha_name`: `str` — No description in code.
  - `backtest_id`: `str` — No description in code.
  - `start_date`: `date` — No description in code.
  - `end_date`: `date` — No description in code.
  - `snapshot_id`: `str` — No description in code.
  - `dataset_version_ids`: `dict[str, str]` — No description in code.
  - `daily_signals`: `pl.DataFrame` — No description in code.
  - `daily_ic`: `pl.DataFrame` — No description in code.
  - `mean_ic`: `float` — No description in code.
  - `icir`: `float` — No description in code.
  - `hit_rate`: `float` — No description in code.
  - `coverage`: `float` — No description in code.
  - `long_short_spread`: `float` — No description in code.
  - `autocorrelation`: `dict[int, float]` — No description in code.
  - `weight_method`: `str` — No description in code.
  - `daily_weights`: `pl.DataFrame` — No description in code.
  - `turnover_result`: `TurnoverResult` — No description in code.
  - `decay_curve`: `pl.DataFrame` — No description in code.
  - `decay_half_life`: `float | None` — No description in code.
  - `daily_portfolio_returns`: `pl.DataFrame` — No description in code. (default: `field(default_factory=lambda : pl.DataFrame(schema={'date': pl.Date, 'return': pl.Float64}))`)
  - `computation_timestamp`: `datetime` — No description in code. (default: `field(default_factory=lambda : datetime.now(UTC))`)
  - `n_days`: `int` — No description in code. (default: `0`)
  - `n_symbols_avg`: `float` — No description in code. (default: `0.0`)

- `BacktestJob` (dataclass) — `libs/trading/backtest/models.py`
  - `id`: `UUID` — No description in code.
  - `job_id`: `str` — No description in code.
  - `status`: `JobStatus` — No description in code.
  - `alpha_name`: `str` — No description in code.
  - `start_date`: `date` — No description in code.
  - `end_date`: `date` — No description in code.
  - `weight_method`: `WeightMethod` — No description in code.
  - `config_json`: `dict[str, Any]` — No description in code.
  - `created_at`: `datetime` — No description in code.
  - `created_by`: `str` — No description in code.
  - `job_timeout`: `int` — No description in code.
  - `started_at`: `datetime | None` — No description in code. (default: `None`)
  - `completed_at`: `datetime | None` — No description in code. (default: `None`)
  - `worker_id`: `str | None` — No description in code. (default: `None`)
  - `progress_pct`: `int` — No description in code. (default: `0`)
  - `result_path`: `str | None` — No description in code. (default: `None`)
  - `mean_ic`: `float | None` — No description in code. (default: `None`)
  - `icir`: `float | None` — No description in code. (default: `None`)
  - `hit_rate`: `float | None` — No description in code. (default: `None`)
  - `coverage`: `float | None` — No description in code. (default: `None`)
  - `long_short_spread`: `float | None` — No description in code. (default: `None`)
  - `average_turnover`: `float | None` — No description in code. (default: `None`)
  - `decay_half_life`: `float | None` — No description in code. (default: `None`)
  - `snapshot_id`: `str | None` — No description in code. (default: `None`)
  - `dataset_version_ids`: `dict[str, str] | None` — No description in code. (default: `None`)
  - `error_message`: `str | None` — No description in code. (default: `None`)
  - `retry_count`: `int` — No description in code. (default: `0`)

- `ConfidenceInterval` (dataclass) — `libs/trading/backtest/monte_carlo.py`
  - `metric_name`: `str` — No description in code.
  - `observed`: `float` — No description in code.
  - `quantiles`: `dict[float, float]` — No description in code.

- `MonteCarloResult` (dataclass) — `libs/trading/backtest/monte_carlo.py`
  - `config`: `MonteCarloConfig` — No description in code.
  - `n_simulations`: `int` — No description in code.
  - `sharpe_ci`: `ConfidenceInterval` — No description in code.
  - `max_drawdown_ci`: `ConfidenceInterval` — No description in code.
  - `mean_ic_ci`: `ConfidenceInterval` — No description in code.
  - `hit_rate_ci`: `ConfidenceInterval` — No description in code.
  - `sharpe_distribution`: `NDArray[np.floating[Any]]` — No description in code.
  - `max_drawdown_distribution`: `NDArray[np.floating[Any]]` — No description in code.
  - `mean_ic_distribution`: `NDArray[np.floating[Any]]` — No description in code.
  - `hit_rate_distribution`: `NDArray[np.floating[Any]]` — No description in code.
  - `p_value_sharpe`: `float` — No description in code.

- `SearchResult` (dataclass) — `libs/trading/backtest/param_search.py`
  - `best_params`: `dict[str, Any]` — No description in code.
  - `best_score`: `float` — No description in code.
  - `all_results`: `list[dict[str, Any]]` — No description in code.

- `WalkForwardResult` (dataclass) — `libs/trading/backtest/walk_forward.py`
  - `windows`: `list[WindowResult]` — No description in code.
  - `aggregated_test_ic`: `float` — No description in code.
  - `aggregated_test_icir`: `float` — No description in code.
  - `overfitting_ratio`: `float` — No description in code.
  - `overfitting_threshold`: `float` — No description in code. (default: `2.0`)

- `WindowResult` (dataclass) — `libs/trading/backtest/walk_forward.py`
  - `window_id`: `int` — No description in code.
  - `train_start`: `date` — No description in code.
  - `train_end`: `date` — No description in code.
  - `test_start`: `date` — No description in code.
  - `test_end`: `date` — No description in code.
  - `best_params`: `dict[str, Any]` — No description in code.
  - `train_ic`: `float` — No description in code.
  - `test_ic`: `float` — No description in code.
  - `test_icir`: `float` — No description in code.

### Analytics & Factors

#### Application Models

- `DecayAnalysisResult` (dataclass) — `libs/trading/alpha/analytics.py`
  - `decay_curve`: `pl.DataFrame` — No description in code.
  - `half_life`: `float | None` — No description in code.
  - `decay_rate`: `float | None` — No description in code.
  - `is_persistent`: `bool` — No description in code.

- `GroupedICResult` (dataclass) — `libs/trading/alpha/analytics.py`
  - `by_group`: `pl.DataFrame` — No description in code.
  - `overall_ic`: `float` — No description in code.
  - `high_ic_groups`: `list[str]` — No description in code.
  - `low_ic_groups`: `list[str]` — No description in code.

- `AttributionResult` (dataclass) — `libs/platform/analytics/attribution.py`
  - `schema_version`: `str` — No description in code. (default: `'1.0.0'`)
  - `portfolio_id`: `str` — No description in code. (default: `''`)
  - `as_of_date`: `date | None` — No description in code. (default: `None`)
  - `dataset_version_id`: `str` — No description in code. (default: `''`)
  - `dataset_versions`: `dict[str, str | None]` — No description in code. (default: `field(default_factory=dict)`)
  - `snapshot_id`: `str | None` — No description in code. (default: `None`)
  - `regression_config`: `dict[str, Any]` — No description in code. (default: `field(default_factory=dict)`)
  - `alpha_annualized_bps`: `float` — No description in code. (default: `0.0`)
  - `alpha_daily`: `float` — No description in code. (default: `0.0`)
  - `alpha_t_stat`: `float` — No description in code. (default: `0.0`)
  - `alpha_p_value`: `float` — No description in code. (default: `0.0`)
  - `r_squared_adj`: `float` — No description in code. (default: `0.0`)
  - `residual_vol_annualized`: `float` — No description in code. (default: `0.0`)
  - `betas`: `dict[str, float]` — No description in code. (default: `field(default_factory=dict)`)
  - `beta_t_stats`: `dict[str, float]` — No description in code. (default: `field(default_factory=dict)`)
  - `beta_p_values`: `dict[str, float]` — No description in code. (default: `field(default_factory=dict)`)
  - `n_observations`: `int` — No description in code. (default: `0`)
  - `multicollinearity_warnings`: `list[str]` — No description in code. (default: `field(default_factory=list)`)
  - `durbin_watson`: `float` — No description in code. (default: `0.0`)
  - `filter_stats`: `dict[str, Any]` — No description in code. (default: `field(default_factory=dict)`)
  - `computation_timestamp`: `datetime` — No description in code. (default: `field(default_factory=lambda : datetime.now(UTC))`)

- `RollingExposureResult` (dataclass) — `libs/platform/analytics/attribution.py`
  - `schema_version`: `str` — No description in code. (default: `'1.0.0'`)
  - `portfolio_id`: `str` — No description in code. (default: `''`)
  - `exposures`: `pl.DataFrame | None` — No description in code. (default: `None`)
  - `skipped_windows`: `list[dict[str, Any]]` — No description in code. (default: `field(default_factory=list)`)
  - `config`: `dict[str, Any]` — No description in code. (default: `field(default_factory=dict)`)
  - `dataset_version_id`: `str` — No description in code. (default: `''`)
  - `dataset_versions`: `dict[str, str | None]` — No description in code. (default: `field(default_factory=dict)`)
  - `snapshot_id`: `str | None` — No description in code. (default: `None`)
  - `computation_timestamp`: `datetime` — No description in code. (default: `field(default_factory=lambda : datetime.now(UTC))`)

- `EventStudyAnalysis` (dataclass) — `libs/platform/analytics/event_study.py`
  - `event_id`: `str` — No description in code.
  - `symbol`: `str` — No description in code.
  - `permno`: `int` — No description in code.
  - `event_date`: `date` — No description in code.
  - `adjusted_event_date`: `date` — No description in code.
  - `event_type`: `str` — No description in code.
  - `config`: `EventStudyConfig` — No description in code.
  - `alpha`: `float` — No description in code.
  - `beta`: `float` — No description in code.
  - `model_type`: `ExpectedReturnModel` — No description in code.
  - `car_pre`: `float` — No description in code.
  - `car_event`: `float` — No description in code.
  - `car_post`: `float` — No description in code.
  - `car_window`: `float` — No description in code.
  - `daily_ar`: `pl.DataFrame` — No description in code.
  - `abnormal_volume`: `float | None` — No description in code.
  - `volume_estimation_avg`: `float | None` — No description in code.
  - `t_statistic`: `float` — No description in code.
  - `p_value`: `float` — No description in code.
  - `is_significant`: `bool` — No description in code.
  - `se_car`: `float` — No description in code.
  - `newey_west_lags`: `int` — No description in code.
  - `patell_z`: `float | None` — No description in code. (default: `None`)
  - `bmp_t`: `float | None` — No description in code. (default: `None`)
  - `is_delisted`: `bool` — No description in code. (default: `False`)
  - `delisting_return`: `float | None` — No description in code. (default: `None`)
  - `warnings`: `list[str]` — No description in code. (default: `field(default_factory=list)`)

- `EventStudyResult` (dataclass) — `libs/platform/analytics/event_study.py`
  - `dataset_version_id`: `str` — No description in code.
  - `dataset_versions`: `dict[str, str] | None` — No description in code.
  - `computation_timestamp`: `datetime` — No description in code.
  - `as_of_date`: `date | None` — No description in code.

- `IndexRebalanceResult` (dataclass) — `libs/platform/analytics/event_study.py`
  - `index_name`: `str` — No description in code.
  - `config`: `EventStudyConfig` — No description in code.
  - `n_additions`: `int` — No description in code.
  - `n_deletions`: `int` — No description in code.
  - `addition_car_pre`: `float` — No description in code.
  - `addition_car_post`: `float` — No description in code.
  - `addition_t_stat`: `float` — No description in code.
  - `addition_significant`: `bool` — No description in code.
  - `deletion_car_pre`: `float` — No description in code.
  - `deletion_car_post`: `float` — No description in code.
  - `deletion_t_stat`: `float` — No description in code.
  - `deletion_significant`: `bool` — No description in code.
  - `addition_volume_change`: `float` — No description in code.
  - `deletion_volume_change`: `float` — No description in code.
  - `uses_announcement_date`: `bool` — No description in code.
  - `announcement_effective_gap_days`: `float | None` — No description in code.
  - `addition_results`: `pl.DataFrame` — No description in code.
  - `deletion_results`: `pl.DataFrame` — No description in code.
  - `clustering_mitigation_used`: `ClusteringMitigation` — No description in code.
  - `clustering_info`: `dict[str, int | bool] | None` — No description in code.
  - `warnings`: `list[str]` — No description in code. (default: `field(default_factory=list)`)

- `MarketModelResult` (dataclass) — `libs/platform/analytics/event_study.py`
  - `symbol`: `str` — No description in code.
  - `permno`: `int` — No description in code.
  - `estimation_start`: `date` — No description in code.
  - `estimation_end`: `date` — No description in code.
  - `n_observations`: `int` — No description in code.
  - `model_type`: `ExpectedReturnModel` — No description in code.
  - `alpha`: `float` — No description in code.
  - `beta`: `float` — No description in code.
  - `factor_betas`: `dict[str, float] | None` — No description in code.
  - `alpha_tstat`: `float` — No description in code.
  - `beta_tstat`: `float` — No description in code.
  - `r_squared`: `float` — No description in code.
  - `residual_std`: `float` — No description in code.
  - `market_mean`: `float` — No description in code.
  - `market_sxx`: `float` — No description in code.
  - `warnings`: `list[str]` — No description in code. (default: `field(default_factory=list)`)

- `PEADAnalysisResult` (dataclass) — `libs/platform/analytics/event_study.py`
  - `holding_period_days`: `int` — No description in code.
  - `n_events`: `int` — No description in code.
  - `n_events_excluded`: `int` — No description in code.
  - `analysis_start`: `date` — No description in code.
  - `analysis_end`: `date` — No description in code.
  - `config`: `EventStudyConfig` — No description in code.
  - `quintile_results`: `pl.DataFrame` — No description in code.
  - `drift_magnitude`: `float` — No description in code.
  - `drift_t_stat`: `float` — No description in code.
  - `drift_significant`: `bool` — No description in code.
  - `n_overlapping_dropped`: `int` — No description in code.
  - `clustering_mitigation_used`: `ClusteringMitigation` — No description in code.
  - `clustering_info`: `dict[str, int | bool] | None` — No description in code.
  - `warnings`: `list[str]` — No description in code. (default: `field(default_factory=list)`)

- `ExecutionAnalysisResult` (dataclass) — `libs/platform/analytics/execution_quality.py`
  - `dataset_version_id`: `str` — No description in code.
  - `dataset_versions`: `dict[str, str] | None` — No description in code.
  - `computation_timestamp`: `datetime` — No description in code.
  - `as_of_date`: `date | None` — No description in code.
  - `symbol`: `str` — No description in code.
  - `side`: `Literal['buy', 'sell']` — No description in code.
  - `execution_date`: `date` — No description in code.
  - `arrival_price`: `float` — No description in code.
  - `execution_price`: `float` — No description in code.
  - `vwap_benchmark`: `float` — No description in code.
  - `twap_benchmark`: `float` — No description in code.
  - `mid_price_at_arrival`: `float | None` — No description in code.
  - `price_shortfall_bps`: `float` — No description in code.
  - `vwap_slippage_bps`: `float` — No description in code.
  - `fee_cost_bps`: `float` — No description in code.
  - `opportunity_cost_bps`: `float` — No description in code.
  - `total_cost_bps`: `float` — No description in code.
  - `market_impact_bps`: `float` — No description in code.
  - `timing_cost_bps`: `float` — No description in code.
  - `fill_rate`: `float` — No description in code.
  - `total_filled_qty`: `int` — No description in code.
  - `unfilled_qty`: `int` — No description in code.
  - `total_target_qty`: `int` — No description in code.
  - `total_notional`: `float` — No description in code.
  - `total_fees`: `float` — No description in code.
  - `close_price`: `float | None` — No description in code.
  - `execution_duration_seconds`: `float` — No description in code.
  - `num_fills`: `int` — No description in code.
  - `warnings`: `list[str]` — No description in code. (default: `field(default_factory=list)`)
  - `arrival_source`: `Literal['decision_time', 'submission_time']` — No description in code. (default: `'decision_time'`)
  - `clock_drift_warning`: `bool` — No description in code. (default: `False`)
  - `fills_before_decision_warning`: `bool` — No description in code. (default: `False`)
  - `side_mismatch_warning`: `bool` — No description in code. (default: `False`)
  - `mixed_currency_warning`: `bool` — No description in code. (default: `False`)
  - `non_usd_fee_warning`: `bool` — No description in code. (default: `False`)
  - `vwap_coverage_pct`: `float` — No description in code. (default: `0.0`)

- `ExecutionWindowRecommendation` (dataclass) — `libs/platform/analytics/execution_quality.py`
  - `symbol`: `str` — No description in code.
  - `target_date`: `date` — No description in code.
  - `order_size_shares`: `int` — No description in code.
  - `recommended_start_time`: `datetime` — No description in code.
  - `recommended_end_time`: `datetime` — No description in code.
  - `expected_participation_rate`: `float` — No description in code.
  - `avg_spread_bps`: `float` — No description in code.
  - `liquidity_score`: `float` — No description in code.
  - `warnings`: `list[str]` — No description in code. (default: `field(default_factory=list)`)

- `Fill` (pydantic) — `libs/platform/analytics/execution_quality.py`
  - `fill_id`: `str` — Broker-assigned unique fill ID (default: `Field(..., description='Broker-assigned unique fill ID')`)
  - `order_id`: `str` — Parent order ID (default: `Field(..., description='Parent order ID')`)
  - `client_order_id`: `str` — Idempotent client order ID (default: `Field(..., description='Idempotent client order ID')`)
  - `timestamp`: `datetime` — Fill timestamp (UTC required) (default: `Field(..., description='Fill timestamp (UTC required)')`)
  - `symbol`: `str` — Symbol at fill level (default: `Field(..., description='Symbol at fill level')`)
  - `side`: `Literal['buy', 'sell']` — Side at fill level (default: `Field(..., description='Side at fill level')`)
  - `price`: `float` — Fill price per share (default: `Field(..., gt=0, description='Fill price per share')`)
  - `quantity`: `int` — Shares filled (default: `Field(..., gt=0, description='Shares filled')`)
  - `exchange`: `str | None` — Exchange where fill occurred (default: `Field(default=None, description='Exchange where fill occurred')`)
  - `liquidity_flag`: `Literal['add', 'remove'] | None` — Liquidity flag: 'add' (maker) or 'remove' (taker) (default: `Field(default=None, description="Liquidity flag: 'add' (maker) or 'remove' (taker)")`)
  - `fee_amount`: `float` — Total fee (positive) or rebate (negative) (default: `Field(default=0.0, description='Total fee (positive) or rebate (negative)')`)
  - `fee_currency`: `str` — Currency of fee (default: `Field(default='USD', description='Currency of fee')`)

- `FillBatch` (pydantic) — `libs/platform/analytics/execution_quality.py`
  - `symbol`: `str` — Primary symbol (default: `Field(..., description='Primary symbol')`)
  - `side`: `Literal['buy', 'sell']` — Primary side (default: `Field(..., description='Primary side')`)
  - `fills`: `list[Fill]` — List of fills (default: `Field(..., description='List of fills')`)
  - `decision_time`: `datetime` — When signal was generated (arrival price source) (default: `Field(..., description='When signal was generated (arrival price source)')`)
  - `submission_time`: `datetime` — When order was submitted to broker (default: `Field(..., description='When order was submitted to broker')`)
  - `total_target_qty`: `int` — Total quantity intended to fill (default: `Field(..., gt=0, description='Total quantity intended to fill')`)

- `CompositeVersionInfo` (dataclass) — `libs/platform/analytics/microstructure.py`
  - `versions`: `dict[str, str]` — No description in code.
  - `snapshot_id`: `str | None` — No description in code.
  - `is_pit`: `bool` — No description in code.

- `IntradayPatternResult` (dataclass) — `libs/platform/analytics/microstructure.py`
  - `symbol`: `str` — No description in code.
  - `start_date`: `date` — No description in code.
  - `end_date`: `date` — No description in code.
  - `data`: `pl.DataFrame` — No description in code.

- `MicrostructureResult` (dataclass) — `libs/platform/analytics/microstructure.py`
  - `dataset_version_id`: `str` — No description in code.
  - `dataset_versions`: `dict[str, str] | None` — No description in code.
  - `computation_timestamp`: `datetime` — No description in code.
  - `as_of_date`: `date | None` — No description in code.

- `RealizedVolatilityResult` (dataclass) — `libs/platform/analytics/microstructure.py`
  - `symbol`: `str` — No description in code.
  - `date`: `date` — No description in code.
  - `rv_daily`: `float` — No description in code.
  - `rv_annualized`: `float` — No description in code.
  - `sampling_freq_minutes`: `int` — No description in code.
  - `num_observations`: `int` — No description in code.

- `SpreadDepthResult` (dataclass) — `libs/platform/analytics/microstructure.py`
  - `symbol`: `str` — No description in code.
  - `date`: `date` — No description in code.
  - `qwap_spread`: `float` — No description in code.
  - `ewas`: `float` — No description in code.
  - `avg_bid_depth`: `float` — No description in code.
  - `avg_ask_depth`: `float` — No description in code.
  - `avg_total_depth`: `float` — No description in code.
  - `depth_imbalance`: `float` — No description in code.
  - `quotes`: `int` — No description in code.
  - `trades`: `int` — No description in code.
  - `has_locked_markets`: `bool` — No description in code.
  - `has_crossed_markets`: `bool` — No description in code.
  - `locked_pct`: `float` — No description in code.
  - `crossed_pct`: `float` — No description in code.
  - `stale_quote_pct`: `float` — No description in code.
  - `depth_is_estimated`: `bool` — No description in code.

- `VPINResult` (dataclass) — `libs/platform/analytics/microstructure.py`
  - `symbol`: `str` — No description in code.
  - `date`: `date` — No description in code.
  - `data`: `pl.DataFrame` — No description in code.
  - `num_buckets`: `int` — No description in code.
  - `num_valid_vpin`: `int` — No description in code.
  - `avg_vpin`: `float` — No description in code.
  - `warnings`: `list[str]` — No description in code. (default: `field(default_factory=list)`)

- `HARForecastResult` (dataclass) — `libs/platform/analytics/volatility.py`
  - `forecast_date`: `date` — No description in code.
  - `rv_forecast`: `float` — No description in code.
  - `rv_forecast_annualized`: `float` — No description in code.
  - `model_r_squared`: `float` — No description in code.
  - `dataset_version_id`: `str` — No description in code.

- `HARModelResult` (dataclass) — `libs/platform/analytics/volatility.py`
  - `intercept`: `float` — No description in code.
  - `coef_daily`: `float` — No description in code.
  - `coef_weekly`: `float` — No description in code.
  - `coef_monthly`: `float` — No description in code.
  - `r_squared`: `float` — No description in code.
  - `n_observations`: `int` — No description in code.
  - `dataset_version_id`: `str` — No description in code.
  - `fit_timestamp`: `datetime` — No description in code.
  - `forecast_horizon`: `int` — No description in code.

- `PortfolioExposureResult` (dataclass) — `libs/models/factors/analysis.py`
  - `date`: `date` — No description in code.
  - `exposures`: `pl.DataFrame` — No description in code.
  - `stock_exposures`: `pl.DataFrame` — No description in code.
  - `coverage`: `pl.DataFrame` — No description in code.

- `ICAnalysis` (dataclass) — `libs/models/factors/factor_analytics.py`
  - `factor_name`: `str` — No description in code.
  - `ic_mean`: `float` — No description in code.
  - `ic_std`: `float` — No description in code.
  - `icir`: `float` — No description in code.
  - `t_statistic`: `float` — No description in code.
  - `hit_rate`: `float` — No description in code.
  - `n_periods`: `int` — No description in code.

- `_SnapshotContext` (dataclass) — `libs/models/factors/factor_builder.py`
  - `manifest_adapter`: `SnapshotManifestAdapter` — No description in code.
  - `snapshot_id`: `str` — No description in code.
  - `crsp_manifest`: `SyncManifest` — No description in code.
  - `compustat_manifest`: `SyncManifest | None` — No description in code.

- `FactorResult` (dataclass) — `libs/models/factors/factor_definitions.py`
  - `exposures`: `pl.DataFrame` — No description in code.
  - `as_of_date`: `date` — No description in code.
  - `dataset_version_ids`: `dict[str, str]` — No description in code.
  - `computation_timestamp`: `datetime` — No description in code. (default: `field(default_factory=lambda : datetime.now(UTC))`)
  - `reproducibility_hash`: `str` — No description in code. (default: `''`)

### Events & Messaging

#### Application Models

- `BufferOutcome` (dataclass) — `libs/core/redis_client/fallback_buffer.py`
  - `buffered`: `int` — No description in code.
  - `dropped`: `int` — No description in code.
  - `size`: `int` — No description in code.

- `BufferedMessage` (dataclass) — `libs/core/redis_client/fallback_buffer.py`
  - `channel`: `str` — No description in code.
  - `payload`: `str` — No description in code.
  - `created_at`: `str` — No description in code.

### Other

#### Application Models

- `AsyncResources` (dataclass) — `apps/alert_worker/entrypoint.py`
  - `db_pool`: `AsyncConnectionPool` — No description in code.
  - `redis_client`: `redis_async.Redis` — No description in code.
  - `poison_queue`: `PoisonQueue` — No description in code.
  - `rate_limiter`: `RateLimiter` — No description in code.

- `AuditFilters` (dataclass) — `apps/web_console/components/audit_log_viewer.py`
  - `user_id`: `str | None` — No description in code.
  - `action`: `str | None` — No description in code.
  - `event_type`: `str | None` — No description in code.
  - `outcome`: `str | None` — No description in code.
  - `start_at`: `datetime | None` — No description in code.
  - `end_at`: `datetime | None` — No description in code.

- `DataPreviewDTO` (pydantic) — `apps/web_console/schemas/data_management.py`
  - `columns`: `list[str]` — No description in code.
  - `rows`: `list[dict[str, Any]]` — No description in code.
  - `total_count`: `int` — No description in code.

- `DatasetInfoDTO` (pydantic) — `apps/web_console/schemas/data_management.py`
  - `name`: `str` — No description in code.
  - `description`: `str | None` — No description in code. (default: `None`)
  - `row_count`: `int | None` — No description in code. (default: `None`)
  - `date_range`: `dict[str, str] | None` — No description in code. (default: `None`)
  - `symbol_count`: `int | None` — No description in code. (default: `None`)
  - `last_sync`: `AwareDatetime | None` — No description in code. (default: `None`)

- `ExportJobDTO` (pydantic) — `apps/web_console/schemas/data_management.py`
  - `id`: `str` — No description in code.
  - `status`: `str` — No description in code.
  - `format`: `str` — No description in code.
  - `row_count`: `int | None` — No description in code. (default: `None`)
  - `file_path`: `str | None` — No description in code. (default: `None`)
  - `expires_at`: `AwareDatetime | None` — No description in code. (default: `None`)

- `QualityTrendDTO` (pydantic) — `apps/web_console/schemas/data_management.py`
  - `dataset`: `str` — No description in code.
  - `period_days`: `int` — No description in code.
  - `data_points`: `list[QualityTrendPointDTO]` — No description in code.

- `QualityTrendPointDTO` (pydantic) — `apps/web_console/schemas/data_management.py`
  - `date`: `AwareDatetime` — No description in code.
  - `metric`: `str` — No description in code.
  - `value`: `float | int` — No description in code.

- `QuarantineEntryDTO` (pydantic) — `apps/web_console/schemas/data_management.py`
  - `dataset`: `str` — No description in code.
  - `quarantine_path`: `str` — No description in code.
  - `reason`: `str` — No description in code.
  - `created_at`: `AwareDatetime` — No description in code.

- `QueryResultDTO` (pydantic) — `apps/web_console/schemas/data_management.py`
  - `columns`: `list[str]` — No description in code.
  - `rows`: `list[dict[str, Any]]` — No description in code.
  - `total_count`: `int` — No description in code.
  - `has_more`: `bool` — No description in code.
  - `cursor`: `str | None` — No description in code. (default: `None`)

- `SyncJobDTO` (pydantic) — `apps/web_console/schemas/data_management.py`
  - `id`: `str` — No description in code.
  - `dataset`: `str` — No description in code.
  - `status`: `str` — No description in code.
  - `started_at`: `AwareDatetime | None` — No description in code. (default: `None`)
  - `completed_at`: `AwareDatetime | None` — No description in code. (default: `None`)
  - `row_count`: `int | None` — No description in code. (default: `None`)
  - `error`: `str | None` — No description in code. (default: `None`)

- `SyncLogEntry` (pydantic) — `apps/web_console/schemas/data_management.py`
  - `id`: `str` — No description in code.
  - `dataset`: `str` — No description in code.
  - `level`: `str` — No description in code.
  - `message`: `str` — No description in code.
  - `extra`: `dict[str, Any] | None` — No description in code. (default: `None`)
  - `sync_run_id`: `str | None` — No description in code. (default: `None`)
  - `created_at`: `AwareDatetime` — No description in code.

- `SyncScheduleDTO` (pydantic) — `apps/web_console/schemas/data_management.py`
  - `id`: `str` — No description in code.
  - `dataset`: `str` — No description in code.
  - `enabled`: `bool` — No description in code.
  - `cron_expression`: `str` — No description in code.
  - `last_scheduled_run`: `AwareDatetime | None` — No description in code. (default: `None`)
  - `next_scheduled_run`: `AwareDatetime | None` — No description in code. (default: `None`)
  - `version`: `int` — No description in code.

- `SyncScheduleUpdateDTO` (pydantic) — `apps/web_console/schemas/data_management.py`
  - `enabled`: `bool | None` — No description in code. (default: `None`)
  - `cron_expression`: `str | None` — No description in code. (default: `None`)

- `SyncStatusDTO` (pydantic) — `apps/web_console/schemas/data_management.py`
  - `dataset`: `str` — No description in code.
  - `last_sync`: `AwareDatetime | None` — No description in code. (default: `None`)
  - `row_count`: `int | None` — No description in code. (default: `None`)
  - `validation_status`: `str | None` — No description in code. (default: `None`)
  - `schema_version`: `str | None` — No description in code. (default: `None`)

- `ValidationResultDTO` (pydantic) — `apps/web_console/schemas/data_management.py`
  - `id`: `str` — No description in code.
  - `dataset`: `str` — No description in code.
  - `sync_run_id`: `str | None` — No description in code. (default: `None`)
  - `validation_type`: `str` — No description in code.
  - `status`: `str` — No description in code.
  - `expected_value`: `str | float | int | None` — No description in code. (default: `None`)
  - `actual_value`: `str | float | int | None` — No description in code. (default: `None`)
  - `error_message`: `str | None` — No description in code. (default: `None`)
  - `created_at`: `AwareDatetime` — No description in code.

- `TestResult` (pydantic) — `apps/web_console/services/alert_service.py`
  - `success`: `bool` — No description in code.
  - `error`: `str | None` — No description in code. (default: `None`)

- `ExposureData` (dataclass) — `apps/web_console/services/factor_exposure_service.py`
  - `exposures`: `pl.DataFrame` — No description in code.
  - `factors`: `list[str]` — No description in code.
  - `date_range`: `tuple[date, date]` — No description in code.

- `FactorDefinition` (dataclass) — `apps/web_console/services/factor_exposure_service.py`
  - `name`: `str` — No description in code.
  - `category`: `str` — No description in code.
  - `description`: `str` — No description in code.

- `NotebookParameter` (dataclass) — `apps/web_console/services/notebook_launcher_service.py`
  - `key`: `str` — No description in code.
  - `label`: `str` — No description in code.
  - `kind`: `str` — No description in code.
  - `default`: `Any | None` — No description in code. (default: `None`)
  - `required`: `bool` — No description in code. (default: `False`)
  - `options`: `list[str] | None` — No description in code. (default: `None`)
  - `help`: `str | None` — No description in code. (default: `None`)

- `NotebookSession` (dataclass) — `apps/web_console/services/notebook_launcher_service.py`
  - `session_id`: `str` — No description in code.
  - `template_id`: `str` — No description in code.
  - `parameters`: `dict[str, Any]` — No description in code.
  - `status`: `SessionStatus` — No description in code.
  - `created_at`: `datetime` — No description in code.
  - `updated_at`: `datetime` — No description in code.
  - `process_id`: `int | None` — No description in code. (default: `None`)
  - `port`: `int | None` — No description in code. (default: `None`)
  - `token`: `str | None` — No description in code. (default: `None`)
  - `access_url`: `str | None` — No description in code. (default: `None`)
  - `error_message`: `str | None` — No description in code. (default: `None`)
  - `command`: `list[str] | None` — No description in code. (default: `None`)

- `NotebookTemplate` (dataclass) — `apps/web_console/services/notebook_launcher_service.py`
  - `template_id`: `str` — No description in code.
  - `name`: `str` — No description in code.
  - `description`: `str` — No description in code.
  - `notebook_path`: `str | None` — No description in code. (default: `None`)
  - `parameters`: `tuple[NotebookParameter, ...]` — No description in code. (default: `()`)

- `StrategyInfo` (dataclass) — `apps/web_console/services/user_management.py`
  - `strategy_id`: `str` — No description in code.
  - `name`: `str` — No description in code.
  - `description`: `str | None` — No description in code.

- `ApiKeyScopes` (pydantic) — `libs/platform/admin/api_keys.py`
  - `read_positions`: `bool` — No description in code. (default: `False`)
  - `read_orders`: `bool` — No description in code. (default: `False`)
  - `write_orders`: `bool` — No description in code. (default: `False`)
  - `read_strategies`: `bool` — No description in code. (default: `False`)

#### SQL Tables

- `audit_log` — `db/migrations/0004_add_audit_log.sql`
  - `id`: `BIGSERIAL`
  - `timestamp`: `TIMESTAMPTZ`
  - `user_id`: `TEXT`
  - `action`: `TEXT`
  - `details`: `JSONB`
  - `reason`: `TEXT`
  - `ip_address`: `TEXT`
  - `session_id`: `TEXT`

- `backtest_jobs` — `db/migrations/0008_create_backtest_jobs.sql`
  - `id`: `UUID`
  - `job_id`: `VARCHAR(32)`
  - `status`: `VARCHAR(20)`
  - `alpha_name`: `VARCHAR(255)`
  - `start_date`: `DATE`
  - `end_date`: `DATE`
  - `weight_method`: `VARCHAR(50)`
  - `config_json`: `JSONB`
  - `created_at`: `TIMESTAMPTZ`
  - `started_at`: `TIMESTAMPTZ`
  - `completed_at`: `TIMESTAMPTZ`
  - `worker_id`: `VARCHAR(255)`
  - `progress_pct`: `SMALLINT`
  - `job_timeout`: `INTEGER`
  - `result_path`: `VARCHAR(512)`
  - `mean_ic`: `FLOAT`
  - `icir`: `FLOAT`
  - `hit_rate`: `FLOAT`
  - `coverage`: `FLOAT`
  - `long_short_spread`: `FLOAT`
  - `average_turnover`: `FLOAT`
  - `decay_half_life`: `FLOAT`
  - `snapshot_id`: `VARCHAR(255)`
  - `dataset_version_ids`: `JSONB`
  - `error_message`: `TEXT`
  - `retry_count`: `SMALLINT`
  - `created_by`: `VARCHAR(255)`

- `reconciliation_high_water_mark` — `db/migrations/0009_add_conflict_resolution_columns.sql`
  - `name`: `TEXT`
  - `last_check_time`: `TIMESTAMPTZ`
  - `updated_at`: `TIMESTAMPTZ`

- `strategies` — `db/migrations/0006_create_rbac_tables.sql`
  - `strategy_id`: `VARCHAR(50)`
  - `name`: `VARCHAR(255)`
  - `description`: `TEXT`
  - `created_at`: `TIMESTAMPTZ`
