# web_console_ng

<!-- Last reviewed: 2026-02-02 - P6T10: PR review fixes - replace cross join with iterative recovery date lookup -->

## Identity
- **Type:** Service (NiceGUI + FastAPI endpoints)
- **Port:** `WEB_CONSOLE_NG_PORT` (default 8080)
- **Container:** `apps/web_console_ng/Dockerfile`

## Interface
### Public API Endpoints (NiceGUI / FastAPI)
| Endpoint | Method | Parameters | Returns |
|----------|--------|------------|---------|
| `/login` | GET | Query `next` (optional) | Login UI (NiceGUI page). |
| `/auth/callback` | GET | `code`, `state` | OAuth2 callback handler; sets auth cookies and redirects. |
| `/mfa-verify` | GET/POST | Page params | MFA verification UI. |
| `/forgot-password` | GET | None | Password recovery UI. |
| `/dashboard` | GET | None | Real-time trading dashboard (P5T4). |
| `/manual-order` | GET | None | Manual order entry page (P5T5). |
| `/position-management` | GET | None | Position management with bulk actions (P5T5). |
| `/risk` | GET | None | Risk analytics dashboard with VaR, factor exposures, stress tests (P5T6). |
| `/health` | GET | None | System health monitor with service status, connectivity, latency (P5T7). |
| `/backtest` | GET | None | Backtest manager with job submission, progress, results comparison (P5T7). |
| `/admin` | GET | None | Admin dashboard with API keys, system config, audit logs (P5T7). |
| `/alerts` | GET | None | Alert configuration management (P5T7). |
| `/circuit-breaker` | GET | None | Circuit breaker management page (P5T7). |
| `/data-management` | GET | None | Data catalog and synchronization management (P5T7). |
| `/alpha-explorer` | GET | None | Alpha signal explorer with IC/decay/correlation charts (P5T8). |
| `/attribution` | GET | None | Factor attribution analytics with Fama-French models (P6T10). |
| `/compare` | GET | None | Strategy comparison tool with metrics and portfolio simulator (P5T8). |
| `/journal` | GET | None | Trade journal with filters, pagination, and export (P5T8). |
| `/notebooks` | GET | None | Research notebook launcher with templates and sessions (P5T8). |
| `/performance` | GET | None | Performance dashboard with real-time P&L and historical charts (P5T8). |
| `/reports` | GET | None | Scheduled reports management with run history (P5T8). |
| `/execution-quality` | GET | None | TCA dashboard with execution quality metrics (P6T8). |
| `/api/workspace/grid/{grid_id}` | GET/POST/DELETE | `grid_id`, state JSON | Workspace persistence API for grid state (P6T1). |
| `/healthz` | GET | None | Liveness probe (always 200 unless process unhealthy). |
| `/readyz` | GET | Internal probe headers optional | Readiness probe (checks Redis/backend). |
| `/metrics` | GET | Internal probe headers optional | Prometheus metrics. |
| `/spike/request` | GET | N/A | Experimental spike page (dev only). |

## Behavioral Contracts
### Startup (`main.py`)
**Purpose:** Initialize core services, middleware, and connection handlers.

**Behavior:**
1. Initialize optional DB pool and audit logger.
2. Initialize session store, state manager, and async trading client.
3. Add middleware in order: TrustedHost -> Admission -> Session -> Auth.
4. Register health endpoints and lifecycle hooks.
5. On startup: open DB pool, start trading client and audit logger, inject disconnect overlay.
6. On shutdown: close trading client, audit logger, DB pool, state manager, and Redis.

### Admission + Session Middleware
**Purpose:** Enforce connection limits and session validation before WebSocket upgrades.

**Behavior:**
- Admission control checks connection limits (global and per-session).
- Session middleware validates cookies and device binding before auth middleware.

### Health + Metrics
**Purpose:** Expose internal liveness/readiness and Prometheus metrics.

**Behavior:**
- `/healthz` returns liveness status without dependency checks.
- `/readyz` returns 503 during drain or if Redis/backends are unavailable.
- `/metrics` is protected by internal probe checks unless ingress is secured.

### Real-Time Dashboard (P5T4)
**Purpose:** Display live positions, orders, and account metrics with real-time updates.

**Behavior:**
- Positions grid uses AG Grid with `getRowId` for efficient delta updates via `applyTransaction`.
- Orders table displays open orders with cancel functionality.
- Account metrics (equity, buying power, P&L) update in real-time.
- Activity feed displays recent trades from trades table via `StrategyScopedDataAccess` (P5T10).
- WebSocket connection with automatic reconnection and disconnect overlay.
- `RealtimeUpdater` subscribes to Redis pub/sub channels for live data.
- `ClientLifecycleManager` handles cleanup on disconnect.

**Components:**
- `components/positions_grid.py` - AG Grid for positions with close position action.
- `components/orders_table.py` - AG Grid for open orders with cancel action.
- `core/realtime.py` - WebSocket subscription management via Redis pub/sub.
- `core/client_lifecycle.py` - Client connection lifecycle and cleanup.
- `core/synthetic_id.py` - Deterministic synthetic ID generation for orders missing client_order_id.

### Kill Switch Management (P5T5)
**Purpose:** Emergency trading halt with real-time status and safety confirmations.

**Note:** Kill switch functionality is now integrated into the Circuit Breaker page (`/circuit-breaker`).
The dedicated `/kill-switch` page was removed in P5T10 to consolidate trading controls.

**Behavior:**
- Displays current kill switch state (ENGAGED/DISENGAGED/UNKNOWN).
- Real-time status updates via Redis pub/sub subscription.
- Engage requires reason (min 10 chars) and single confirmation.
- Disengage requires admin role, resolution notes, and two-factor confirmation (type "CONFIRM").
- Unknown state disables both buttons for safety (fail-closed).
- All actions are audit-logged.

**Safety Pattern:**
- Double-check pattern: verify kill switch state at both preview and confirmation.
- Fail-closed: unknown states block action until verified.

### Header & Status Bar (P6T2)
**Purpose:** Always-visible trading health indicators and emergency state banner.

**Behavior:**
- Header shows NLV/leverage/day change, latency badge, connection status, and market clock.
- Latency badge pings `/healthz` every 5 seconds and shows rolling latency quality.
- Connection monitor tracks CONNECTED/DEGRADED/DISCONNECTED/RECONNECTING with stale-data warning.
- Read-only mode activates on disconnect/reconnect and disables order submission/cancel/flatten.
- Status bar shows `TRADING ACTIVE` (green) or `TRADING HALTED` (red) based on kill switch.

**Components:**
- `components/header_metrics.py` - NLV/Leverage/Day Change
- `components/status_bar.py` - Persistent trading status bar
- `components/market_clock.py` - Market session state + countdown
- `core/latency_monitor.py` - API latency monitoring
- `core/connection_monitor.py` - Connection state + read-only gating
- `ui/theme.py` - Centralized CSS class constants for consistent styling
- `libs/common/market_hours.py` - Exchange session logic via `exchange_calendars`

### Manual Order Entry (P5T5)
**Purpose:** Submit manual orders with safety checks and audit trail.

**Behavior:**
- Form validation: symbol required, qty >= 1 (whole numbers), reason >= 10 chars.
- Supports market and limit orders with time-in-force options (day, gtc, ioc, fok).
- Kill switch check at preview AND confirmation (double-check pattern).
- Order preview dialog shows all order details before submission.
- Backend generates deterministic `client_order_id` for idempotency.
- All submissions are audit-logged with order details and reason.

**Access Control:**
- Viewers cannot submit orders (redirected to home).
- Traders and admins can submit orders.

### Position Management (P5T5)
**Purpose:** Bulk position operations with safety confirmations.

**Behavior:**
- Close individual positions via positions grid (uses `close_position` endpoint).
- Cancel All Orders: requires confirmation, uses `cancel_all_orders` endpoint.
- Flatten All Positions: requires confirmation + type "FLATTEN", uses `flatten_all_positions`.
- Kill switch blocks close-position but allows risk-reducing cancels.
- Circuit breaker tripped state shows warning but allows risk-reducing closes.

**Safety Checks:**
- Kill switch state checked before close position dialog opens.
- Fresh kill switch check at confirmation time (double-check pattern).
- Synthetic order IDs (`unknown_*`) block cancel attempts with support message.

### Risk Analytics Dashboard (P5T6)
**Purpose:** Display portfolio risk analytics including VaR/CVaR, factor exposures, and stress test results.

**Behavior:**
- Displays risk overview metrics (total risk, factor risk, specific risk).
- VaR/CVaR metrics with risk budget utilization gauge.
- 30-day VaR history chart with risk limit threshold line.
- Factor exposure bar chart with canonical factor ordering.
- Stress test results table with factor contribution waterfall for worst-case scenario.
- Auto-refresh every 60 seconds with refresh lock to prevent concurrent calls.
- Placeholder warning banner when using demo data (risk model artifacts unavailable).
- Error state banner with retry option when data load fails.

**Data Handling:**
- Safe float conversion for all numeric values (rejects NaN/inf as invalid).
- Division by zero protection in drawdown calculations.
- Proper date sorting using datetime parsing (handles ISO formats and timezone normalization).
- Section-specific validators for VaR vs overview metrics.
- Skip entries with invalid data rather than displaying misleading 0.0 values.

**Components:**
- `components/drawdown_chart.py` - Drawdown visualization with division-by-zero protection.
- `components/equity_curve_chart.py` - Cumulative returns chart with non-finite filtering.
- `components/pnl_chart.py` - P&L equity curve and drawdown charts with date sorting.
- `components/var_chart.py` - VaR metrics, gauge, and history chart.
- `components/factor_exposure_chart.py` - Factor exposure bar chart with display names.
- `components/stress_test_results.py` - Stress test table and factor waterfall chart.

**Access Control:**
- Requires `VIEW_PNL` permission.
- Requires at least one authorized strategy.

### System Health Monitor (P5T7)
**Purpose:** Real-time monitoring of microservices, infrastructure connectivity, and latency metrics.

**Behavior:**
- Service status grid showing health status with staleness indicators.
- Infrastructure connectivity status (Redis, PostgreSQL) with latency.
- Latency metrics table with P50/P95/P99 percentiles.
- Auto-refresh with timer lifecycle cleanup.
- Graceful degradation when health data unavailable.

**Components:**
- `pages/health.py` - System health monitor page.
- `core/database.py` - Async database pool management.

**Access Control:**
- Requires `VIEW_REPORTS` permission.

### Backtest Manager (P5T7)
**Purpose:** Submit, monitor, and compare backtest jobs with security-scoped access.

**Behavior:**
- New Backtest tab: Submit jobs with alpha signal, date range, weight method, priority.
- Running Jobs tab: Monitor progress with progressive polling (2s-30s backoff).
- Results tab: View completed results with metrics, enable comparison mode.
- Job ownership validation prevents cross-user access (IDOR protection).
- Empty alpha catalog shows helpful guidance.
- Export requires EXPORT_DATA permission.

**Security:**
- `_verify_job_ownership()` validates user owns job before cancel/view/compare.
- `_get_user_id()` fails closed if user identification unavailable.
- Job listing is user-scoped via `created_by` filter.

**Components:**
- `pages/backtest.py` - Backtest manager page with ownership validation.
- `core/dependencies.py` - Sync Redis/DB pool access for backtest operations.

**Access Control:**
- Requires `VIEW_PNL` permission.

### Admin Dashboard (P5T7)
**Purpose:** Administrative functions for API keys, system configuration, reconciliation tools, and audit logs.

**Behavior:**
- API Keys tab: List and create API keys (revoke/rotate TODO).
- System Config tab: View and update system configuration.
- Reconciliation tab: Fills backfill from Alpaca account activities API (P5T10).
- Audit Logs tab: Searchable audit log viewer with pagination.
- Permission-gated navigation (only visible with admin permissions).

**Reconciliation Tools (P5T10):**
- Alpaca Fills Backfill: Fetches fills from broker, populates trades table, recalculates P&L.
- Configurable lookback hours (1-720).
- Optional recalculate all trades P&L flag.
- Requires `MANAGE_RECONCILIATION` permission.

**Components:**
- `pages/admin.py` - Admin dashboard with tabbed interface.

**Access Control:**
- Navigation requires MANAGE_API_KEYS, MANAGE_SYSTEM_CONFIG, MANAGE_RECONCILIATION, or VIEW_AUDIT.
- Individual tabs permission-gated.

### Alert Configuration (P5T7)
**Purpose:** Manage alert rules and notification channels.

**Behavior:**
- Alert Rules tab: List active alert rules.
- Notification Channels tab: Manage notification destinations.
- Create Alert dialog for new alert rules.

**Components:**
- `pages/alerts.py` - Alert configuration page.

**Access Control:**
- Requires `MANAGE_ALERTS` permission.

### Circuit Breaker Management (P5T7)
**Purpose:** Monitor and control circuit breaker state with history and audit trail.

**Behavior:**
- Current state display with color-coded status (OPEN/TRIPPED/UNKNOWN).
- Trip/Reset controls with confirmation dialogs.
- State history table showing recent transitions.
- Auto-refresh with timer lifecycle cleanup.

**Components:**
- `pages/circuit_breaker.py` - Circuit breaker management page.

**Access Control:**
- Requires `VIEW_SYSTEM_STATUS` permission for view.
- Requires `MANAGE_CIRCUIT_BREAKER` permission for trip/reset.

### Data Management (P5T7)
**Purpose:** Data catalog browsing and synchronization status monitoring.

**Behavior:**
- Alpha Catalog tab: Browse registered alpha signals.
- Feature Store tab: View feature definitions.
- Sync Status tab: Monitor data synchronization with refresh.
- Data Sync tab: Manual sync controls with history.

**Components:**
- `pages/data_management.py` - Data catalog and sync management page.

**Access Control:**
- Requires `VIEW_DATA_CATALOG` permission.

### Alpha Signal Explorer (P5T8)
**Purpose:** Analyze alpha signal quality with IC/ICIR visualization, decay curves, and correlation matrices.

**Behavior:**
- Signal selector to browse registered alpha signals.
- IC time-series chart showing information coefficient over time.
- IC statistics summary (mean IC, IC std, ICIR, hit rate, t-stat).
- Signal decay curve showing IC degradation across forward horizons.
- Correlation matrix heatmap for selected signals.
- Demo mode fallback when alpha service unavailable.
- Feature flag controlled (`FEATURE_ALPHA_EXPLORER`).

**Components:**
- `pages/alpha_explorer.py` - Alpha signal explorer page.
- `components/ic_chart.py` - IC time-series visualization.
- `components/decay_curve.py` - Signal decay curve chart.
- `components/correlation_matrix.py` - Correlation heatmap.

**Access Control:**
- Requires `VIEW_ALPHA_SIGNALS` permission.

### Strategy Comparison (P5T8)
**Purpose:** Compare multiple strategies with performance metrics, equity curves, and portfolio simulation.

**Behavior:**
- Multi-select for strategy comparison (2-5 strategies).
- Performance metrics table (total return, volatility, Sharpe, max drawdown, win rate).
- Equity curve comparison chart with normalized returns.
- Correlation heatmap between strategies.
- Portfolio simulator with weight allocation and combined metrics.
- Demo mode fallback when strategy data unavailable.
- Feature flag controlled (`FEATURE_STRATEGY_COMPARISON`).

**Components:**
- `pages/compare.py` - Strategy comparison page.

**Access Control:**
- Requires `VIEW_PNL` permission.

### Trade Journal (P5T8)
**Purpose:** Browse and analyze trade history with filters, pagination, and export capabilities.

**Behavior:**
- Date range filter with preset buttons (7D, 30D, 90D, YTD).
- Symbol and side filters for trade search.
- Paginated trade table with configurable page size.
- Trade statistics summary (total trades, P&L, win rate, avg win/loss).
- CSV and Excel export functionality with streaming for large datasets.
- Trades query excludes superseded entries from fills backfill (P5T10).
- Demo mode fallback when trade data unavailable.
- Feature flag controlled (`FEATURE_TRADE_JOURNAL`).

**Components:**
- `pages/journal.py` - Trade journal page.

**Access Control:**
- Requires `VIEW_TRADES` permission.

### Research Notebook Launcher (P5T8)
**Purpose:** Launch and manage research notebooks with template selection and session management.

**Behavior:**
- Template selector with description display.
- Dynamic parameters form based on template configuration.
- Launch notebook with confirmation and result display.
- Active sessions table with status and terminate option.
- Session refresh functionality.
- Demo mode fallback when notebook service unavailable.

**Components:**
- `pages/notebook_launcher.py` - Notebook launcher page.

**Access Control:**
- Requires `LAUNCH_NOTEBOOKS` permission.

### Performance Dashboard (P5T8)
**Purpose:** Display realized and unrealized P&L with historical performance charts.

**Behavior:**
- Real-time P&L section with open positions and unrealized P&L.
- Position summary table with realized P&L.
- Date range selector with presets (7D, 30D, 90D, YTD).
- Equity curve chart showing cumulative P&L.
- Drawdown chart with visual representation.
- Auto-refresh timer for real-time updates.
- Demo mode fallback when performance data unavailable.
- Feature flag controlled (`FEATURE_PERFORMANCE_DASHBOARD`).

**Components:**
- `pages/performance.py` - Performance dashboard page.

**Access Control:**
- Requires `VIEW_PNL` permission.

### Scheduled Reports (P5T8)
**Purpose:** Manage automated report schedules with creation, editing, and run history.

**Behavior:**
- Schedule list/selector for existing schedules.
- Create new schedule form (name, type, cron, parameters).
- Edit schedule with inline form.
- Delete schedule with confirmation.
- Run history table with status and timestamps.
- Download completed reports.
- Demo mode fallback when database unavailable.

**Components:**
- `pages/scheduled_reports.py` - Scheduled reports page.

**Access Control:**
- Requires `VIEW_REPORTS` permission for viewing.
- Requires `MANAGE_REPORTS` permission for create/edit/delete.

### Professional Trading Terminal Core (P6T1)
**Purpose:** High-performance grid infrastructure with dark mode, density controls, and workspace persistence.

**Behavior:**
- AG Grid async transactions with `asyncTransactionWaitMillis: 50ms` for batched updates.
- Per-grid throttling with hysteresis to prevent degradation mode flapping.
- Dark mode using Material Design surface levels (LEVEL_0-4: #121212 to #454545).
- Semantic trading colors: profit (#00E676), loss (#FF5252), warning (#FFB300).
- High-density layout with 22px row height and compact cards.
- Workspace persistence saves grid column state, sort, and filters to database.
- Kill switch state parsing: ENGAGED→True, DISENGAGED→False, unknown→None (fail-open for risk reduction).

**Security:**
- Workspace API requires session authentication (user derived from session cookie).
- CSRF token validation on POST/DELETE mutations.
- Grid ID allowlist prevents storage fan-out attacks (only `positions_grid`, `orders_grid`, etc.).
- State size limit (64KB) enforced at app and DB level.

**Components:**
- `core/grid_performance.py` - Python-side metrics monitoring with WeakKeyDictionary.
- `core/workspace_persistence.py` - Async DB persistence with DatabaseUnavailableError handling.
- `api/workspace.py` - REST API with CSRF, session auth, and grid ID allowlist.
- `ui/dark_theme.py` - Theme constants (SurfaceLevels, SemanticColors).
- `ui/trading_layout.py` - Layout helpers for compact grids and cards.
- `static/js/grid_throttle.js` - Browser-side throttle with hysteresis.
- `static/js/grid_state_manager.js` - Browser state manager with save loop prevention.
- `static/css/density.css` - Compact grid styles.

**Access Control:**
- Workspace persistence requires authenticated session.

### Header Metrics Display (P6T2)
**Purpose:** Compact header display for NLV, leverage ratio, and day change metrics.

**Behavior:**
- NLV (Net Liquidation Value) formatted with K/M/B suffixes.
- Leverage ratio with color coding: green (<2x), yellow (2-3x), red (>3x).
- Day change calculated from session baseline with +/- sign and percentage.
- Metrics isolated from kill switch updates (separate try/except).
- Stale indicator (opacity) after 30s without update.
- Auth headers passed to all API calls for production compatibility.

**Components:**
- `components/header_metrics.py` - HeaderMetrics class with NLV, leverage, day change labels.

**Data Sources:**
- `GET /api/v1/account` for `portfolio_value` (NLV).
- `GET /api/v1/positions` for position market values (leverage calculation).
- Leverage = Sum(abs(market_value)) / NLV.
- Day change = current NLV - baseline NLV (stored in `app.storage.user`).

**Isolation Pattern:**
- Metrics update runs AFTER kill switch/circuit breaker updates.
- Errors/timeouts only mark metrics stale; never affect connection badge or kill switch.
- Uses `asyncio.gather(return_exceptions=True)` for parallel account/positions fetch.
- Wrapped in `asyncio.wait_for(timeout=4.0)` to prevent blocking poll lock.

**Timezone Handling:**
- Day baseline resets at 00:00 ET using `ZoneInfo("America/New_York")`.
- Handles DST transitions automatically via `ZoneInfo`.

### Notification & Hotkey System (P6T3)
**Purpose:** Priority-based notification routing with quiet mode, keyboard hotkeys, and visual feedback.

**Behavior:**
- NotificationRouter routes alerts by priority (HIGH/MEDIUM/LOW) with quiet mode persistence.
- Quiet mode suppresses LOW/MEDIUM notifications while showing HIGH alerts (e.g., kill switch).
- LogDrawer displays notification history with unread badge count.
- HotkeyManager supports global and context-scoped keyboard shortcuts (GLOBAL, ORDER_FORM, GRID).
- ActionButton provides visual state feedback for trading actions (DEFAULT, SENDING, CONFIRMING, SUCCESS, FAILED, TIMEOUT).
- CellFlashManager highlights P&L changes in positions grid with green/red flash animations.

**Components:**
- `core/notification_router.py` - Priority routing, quiet mode, client context capture for thread-safe toasts.
- `core/hotkey_manager.py` - HotkeyBinding dataclass, handler registration, scope filtering.
- `components/log_drawer.py` - Notification history drawer with unread badge.
- `components/action_button.py` - State machine button with `manual_lifecycle` flag for order flows.
- `components/command_palette.py` - Ctrl+K command palette for quick actions.
- `components/loading_states.py` - Loading/skeleton state components.
- `static/js/hotkey_handler.js` - Browser-side keydown handler with scope detection.
- `static/js/cell_flash.js` - Per-grid flash animation manager respecting GridThrottle degradation.

**Default Hotkeys:**
| Key | Scope | Action |
|-----|-------|--------|
| `Ctrl+K` | GLOBAL | Open command palette |
| `?` | GLOBAL | Show hotkey help |
| `B` | ORDER_FORM | Focus buy side |
| `S` | ORDER_FORM | Focus sell side |
| `Enter` | ORDER_FORM | Submit order |
| `Escape` | ORDER_FORM | Cancel/clear form |

**Safety:**
- Hotkey submit routes through ActionButton.trigger() for state machine safety.
- Order form scope detection uses `_isInOrderForm()` to prevent accidental triggers.
- Client storage (`app.storage.client`) used for per-tab isolation (avoids multi-tab conflicts).

**Access Control:**
- Quiet mode preference persisted per-user via UserStateManager.

### Order Entry Ticket (P6T4)
**Purpose:** Always-visible order entry widget with comprehensive trading safety patterns.

**Behavior:**
- Two-phase order confirmation: Preview shows order details, Confirm re-validates all safety conditions.
- FAIL-CLOSED safety defaults: Kill switch engaged=True, circuit breaker tripped=True until explicitly confirmed safe.
- Staleness thresholds: Position (30s), Price (30s), Buying Power (60s), Limits (5min).
- Triple defense for position limits: Cached limits → Confirm-time fresh limits → Server validation.
- Idempotent submission via `client_order_id` generated at preview time.
- Form recovery pattern: Pending form state saved to Redis with client_order_id for reconnection recovery.
- Tab session isolation via unique `_tab_session_id` per component instance.

**Components:**
- `components/order_ticket.py` - Main order entry widget with OrderTicketState dataclass and OrderTicketComponent class.
- `components/quantity_presets.py` - One-click quantity preset buttons (100/500/1000/MAX) with buying power and position limit awareness.

**Quantity Presets:**
- Default presets: [100, 500, 1000] with MAX button.
- MAX respects both buying power AND position/notional limits.
- Uses most restrictive of all applicable limits with 95% safety margin.
- For limit/stop orders, uses effective_price (order price) instead of current_price.

**Safety Checks (at preview AND confirm time):**
- Connection state must be CONNECTED and not read-only.
- Kill switch must be explicitly DISENGAGED.
- Circuit breaker must be explicitly OPEN.
- Safety state must be loaded (not in fail-closed default state).
- Risk limits must be loaded.
- Position/price/buying power data must not be stale.
- Order notional must not exceed `max_notional_per_order` limit.
- Order quantity must not exceed position limit (`max_position_per_symbol`).
- Total exposure must not exceed `max_total_exposure` limit.

**Data Flow:**
- OrderTicketComponent receives state via callbacks from OrderEntryContext (no direct Redis subscriptions).
- Callbacks: `set_connection_state`, `set_kill_switch_state`, `set_circuit_breaker_state`, `set_price_data`, `set_position_data`, `set_buying_power`, `set_risk_limits`, `set_total_exposure`.

**Access Control:**
- Requires trading permissions (not viewer role).
- Kill switch engaged blocks all orders except risk-reducing exits.

### Order Entry Context Orchestrator (P6T4)
**Purpose:** Central coordinator for all order entry components with proper subscription ownership and lifecycle management.

**Behavior:**
- Single owner of all Redis pub/sub subscriptions (prevents duplicate subscriptions).
- Dispatches real-time updates to child components via typed callbacks.
- Tracks all timers and tasks for proper cleanup on dispose.
- Channel ownership model: Multiple components can share a channel; unsubscribe only when all owners release.
- Connection state monitoring with reconnection detection and resubscription.
- Risk limits refresh timer (every 4 minutes) to prevent staleness.

**Components:**
- `components/order_entry_context.py` - OrderEntryContext class coordinating all order entry widgets.

**Subscription Ownership Model:**
- OrderEntryContext subscribes once to each channel.
- Multiple owners (watchlist, selected_symbol) can request the same channel.
- Unsubscribe only when last owner releases (refcount pattern).
- Callback equality check (not identity) for bound methods.

**Data Flow:**
```
Redis Pub/Sub -> RealtimeUpdater -> OrderEntryContext -> Component callbacks
                                        |
                                        +-> OrderTicket.set_price_data()
                                        +-> OrderTicket.set_position_data()
                                        +-> OrderTicket.set_kill_switch_state()
                                        +-> MarketContext.set_price_data()
                                        +-> PriceChart.on_price_update()
                                        +-> Watchlist.set_symbol_price_data()
```

### Market Context Widget (P6T4)
**Purpose:** Real-time Level 1 market data display with bid/ask, spread, and last trade information.

**Behavior:**
- Displays bid/ask prices with size indicators.
- Spread calculation and display in price and percentage.
- Last trade price with volume and timestamp.
- Session high/low tracking with alerts at new highs/lows.
- Staleness detection (30s threshold) with visual indicator.

**Components:**
- `components/market_context.py` - MarketContextComponent class.

### Price Chart Widget (P6T4)
**Purpose:** Real-time candlestick chart using TradingView Lightweight Charts library.

**Behavior:**
- Candlestick chart with real-time price updates.
- Accumulates price data into OHLC candles.
- Dark theme integration with trading terminal.
- Throttled chart updates for performance.
- Symbol change clears chart and loads historical data.

**Components:**
- `components/price_chart.py` - PriceChartComponent class.
- `ui/lightweight_charts.py` - NiceGUI wrapper for Lightweight Charts JS library.

### Watchlist Widget (P6T4)
**Purpose:** Symbol watchlist with real-time price updates and sparkline charts.

**Behavior:**
- Configurable watchlist symbols with add/remove functionality.
- Real-time price updates from shared price channel subscriptions.
- Change calculation from previous close with color coding.
- Sparkline price charts (20 points) for visual trend.
- Symbol selection triggers order ticket and market context updates.
- Shared symbol validation with time utilities (`validate_and_normalize_symbol`).

**Components:**
- `components/watchlist.py` - WatchlistComponent class with WatchlistItem dataclass.

### Grid Enhancements (P6T5)
**Purpose:** Enhanced grid features including hierarchical TWAP orders, tabbed panel, Level 2 DOM data, and P&L sparklines.

**Behavior:**
- Hierarchical Orders: AG Grid Tree Data displays parent TWAP orders with expandable child slices.
- Tabbed Panel: NiceGUI tabs with lazy-loaded content for Positions/Working/Fills/History views.
- DOM Ladder: Real-time Level 2 order book visualization with bid/ask depth bars.
- Sparklines: Inline P&L trend charts using Redis ZSET for history storage.

**Components:**
- `components/hierarchical_orders.py` - AG Grid Tree Data configuration with parent/child aggregation.
- `components/tabbed_panel.py` - Lazy-loaded tabbed interface with badge counts and state persistence.
- `components/dom_ladder.py` - Real-time DOM ladder renderer with 30 FPS throttling.
- `components/depth_visualizer.py` - Bid/ask depth calculation and large-size detection.
- `components/sparkline_renderer.py` - SVG sparkline generation for P&L trends.
- `components/symbol_filter.py` - Reusable symbol filter dropdown for grid filtering.
- `core/level2_websocket.py` - Alpaca Pro Level 2 WebSocket with mock mode fallback.
- `core/sparkline_service.py` - Redis ZSET storage for P&L history with rate limiting.

**Level 2 Data Flow:**
```
Alpaca Pro WebSocket -> Level2WebSocketService -> Redis Pub/Sub -> DOMLadderComponent
                              |
                              +-> Mock mode (synthetic data) when credentials unavailable
```

**Safety:**
- L2 subscription refcounting prevents duplicate subscriptions and ensures cleanup.
- Symbol cap enforcement (default 30 symbols) prevents resource exhaustion.
- Mock mode warning displayed to prevent traders using synthetic data for decisions.
- User ID validation in OrderEntryContext prevents authorization bypass.

**Memory Management:**
- Sparkline rate-limit cache with max 10K entries and TTL-based pruning.
- L2 refcount only tracks unique user-symbol combinations.
- DOM ladder uses throttled updates (30 FPS) with pending payload coalescing.

**Access Control:**
- L2 data requires Alpaca Pro subscription (falls back to mock mode without credentials).
- Tabbed panel state persisted per-user via workspace persistence API.

### Order Actions & Safety Gate (P6T7)
**Purpose:** Centralized safety checking and position action controls with FAIL-OPEN/FAIL-CLOSED policies.

**Behavior:**
- SafetyGate provides policy-based safety checking for all trading actions.
- FAIL_OPEN policy (risk-reducing): allows action on uncertainty with warnings (cancel, flatten).
- FAIL_CLOSED policy (risk-increasing): blocks action on any uncertainty (reverse, one-click).
- FlattenControls provides flatten symbol, cancel all orders, and reverse position operations.
- OneClickHandler enables quick order entry with alt-click cancel functionality.
- CancelAllDialog provides bulk order cancellation with safety confirmations.

**Components:**
- `components/safety_gate.py` - SafetyGate class with FAIL_OPEN/FAIL_CLOSED policies, SafetyPolicy enum, SafetyCheckResult dataclass.
- `components/flatten_controls.py` - FlattenControls class with on_flatten_symbol, on_reverse_position methods.
- `components/one_click_handler.py` - OneClickHandler class for one-click order entry with alt-click cancel.
- `components/cancel_all_dialog.py` - Cancel all orders confirmation dialog.
- `components/order_replay.py` - Order replay functionality for re-submitting orders.
- `utils/orders.py` - Shared order validation utilities (is_cancellable_order_id, validate_symbol, UNCANCELLABLE_PREFIXES).

**Safety Gate Policies:**
| Policy | Use Case | Behavior on Uncertainty |
|--------|----------|------------------------|
| FAIL_OPEN | Risk-reducing (cancel, flatten) | Warn but proceed |
| FAIL_CLOSED | Risk-increasing (reverse, one-click) | Block action |

**Flatten Controls Operations:**
| Operation | Policy | Steps |
|-----------|--------|-------|
| Flatten Symbol | FAIL_OPEN | Cancel orders → Close position |
| Cancel All | FAIL_OPEN | Cancel all orders for symbol |
| Reverse Position | FAIL_CLOSED | Cancel orders → Verify cleared → Close → Poll until flat → Open opposite |

**Reverse Position Safety:**
- Requires all cached states (kill switch, connection, circuit breaker) to be known.
- Verifies order cancellations have cleared before proceeding (calls `_verify_orders_cleared`).
- Uses `actual_closed_qty` from close response to account for backend clamping.
- Requires 2 consecutive flat polls (qty=0) before opening opposite leg.
- Re-validates price freshness and fat-finger limits before open leg.

**One-Click Handler:**
- Quick order submission on symbol click in DOM ladder or watchlist.
- Alt-click triggers cancel for symbol instead of order.
- Validates connection state and safety checks before submission.
- Saves last notional for reuse across orders.

**Access Control:**
- Viewers cannot perform any trading actions.
- Traders and admins can flatten, cancel, reverse positions.
- All actions require authenticated session.

### Execution Analytics (P6T8)
**Purpose:** Transaction Cost Analysis (TCA) dashboard, grid export toolbar, and order audit trail.

**Behavior:**
- TCA Dashboard (`/execution-quality`): Visualizes execution quality metrics (implementation shortfall, VWAP slippage, fill rates).
- Grid Export Toolbar: Reusable component for CSV, Excel, and clipboard export with audit logging.
- Order Audit Panel: Displays order lifecycle events with IP, session, and action details.
- Formula injection sanitization in exports prevents Excel/CSV injection attacks.
- PII column exclusion for non-admin users.
- Strict audit mode blocks exports if audit record creation fails.

**Components:**
- `pages/execution_quality.py` - TCA dashboard page with metrics, charts, and order table.
- `components/grid_export_toolbar.py` - Reusable export toolbar with CSV/Excel/clipboard buttons. Uses `libs/platform/security/sanitization.py` for formula injection protection and `AsyncTradingClient` for properly signed API calls.
- `components/order_audit_panel.py` - Order audit trail display with timeline view.
- `components/tca_chart.py` - TCA visualization charts (shortfall decomposition, benchmark comparison).
- `static/js/grid_export.js` - Client-side export utilities with formula sanitization.

**Export Flow:**
```
Export button click
  -> Get grid state (filters, columns, sort)
  -> Create audit record via POST /api/v1/export/audit
  -> (Strict mode) Block if audit creation fails
  -> Execute export (CSV/Excel/Clipboard)
  -> Complete audit record via PATCH /api/v1/export/audit/{id}
```

**Access Control:**
- Requires `VIEW_TRADES` permission for TCA dashboard.
- Export requires `EXPORT_DATA` permission.
- PII columns excluded for non-admin users.

## Data Flow
```
Browser
  -> NiceGUI pages (/login, /mfa-verify, /dashboard, /kill-switch, /manual-order, /position-management, /risk, /health, /backtest, /admin, /alerts, /circuit-breaker, /data-management, /alpha-explorer, /compare, /journal, /notebooks, /performance, /reports)
  -> Session store (Redis)
  -> Execution Gateway (AsyncTradingClient)
  -> Audit log (Postgres, optional)
  -> Metrics/Health endpoints for probes

Real-Time Updates (P5T4/P5T5):
  Redis Pub/Sub channels
    -> RealtimeUpdater (WebSocket)
    -> NiceGUI UI updates (positions, orders, kill switch status)
    -> ClientLifecycleManager (cleanup on disconnect)

Trading Actions Flow:
  User action (close position, cancel order, submit order)
    -> Kill switch check (fail-closed)
    -> Confirmation dialog (double-check pattern)
    -> Fresh kill switch check at confirmation
    -> AsyncTradingClient API call
    -> Audit log
    -> UI notification

Risk Analytics Flow (P5T6):
  /risk page load
    -> RiskService.get_risk_dashboard_data()
    -> StrategyScopedDataAccess (risk_metrics, factor_exposures, stress_tests, var_history)
    -> Chart components (var_chart, factor_exposure_chart, stress_test_results, pnl_chart)
    -> Plotly visualizations in NiceGUI
    -> Auto-refresh timer (60s) with refresh lock
```

## Dependencies
- **Internal:** `apps/web_console_ng/auth/*`, `apps/web_console_ng/core/*`, `apps/web_console_ng/ui/*`, `apps/web_console_ng/utils/*`, `apps/web_console_ng/components/*`, `apps/web_console_ng/pages/*`, `apps/web_console/services/risk_service.py`, `apps/web_console/data/strategy_scoped_queries.py`, `apps/web_console/utils/validators.py`
- **External:** Redis (session + pub/sub), Postgres (optional, audit), Execution Gateway API, NiceGUI, AG Grid, Plotly, Prometheus

## Configuration
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `WEB_CONSOLE_NG_HOST` | No | `0.0.0.0` | Bind host. |
| `WEB_CONSOLE_NG_PORT` | No | `8080` | Bind port. |
| `WEB_CONSOLE_NG_DEBUG` | No | `false` | Debug mode; allows dev auth defaults. |
| `WEB_CONSOLE_AUTH_TYPE` | Yes (prod) | N/A | Auth type (`dev`, `basic`, `mtls`, `oauth2`). |
| `EXECUTION_GATEWAY_URL` | No | `http://localhost:8002` | Backend API base URL. |
| `REDIS_URL` | No | `redis://localhost:6379/1` | Session store Redis. |
| `REDIS_USE_SENTINEL` | No | `false` | Enable Redis Sentinel. |
| `ALLOWED_HOSTS` | No | `localhost,127.0.0.1,::1` | TrustedHost middleware. |
| `TRUSTED_PROXY_IPS` | No | N/A | Trusted proxies for client IP.
| `SESSION_IDLE_TIMEOUT_MINUTES` | No | `15` | Session idle timeout.
| `SESSION_ABSOLUTE_TIMEOUT_HOURS` | No | `4` | Absolute session timeout.
| `SESSION_COOKIE_*` | No | Varies | Cookie security settings.
| `INTERNAL_PROBE_TOKEN` | No | N/A | Internal probe auth for /metrics and /readyz.
| `HEALTH_CHECK_BACKEND_ENABLED` | No | `false` | Enable backend health checks in readiness.
| `WS_MAX_CONNECTIONS` | No | `1000` | Max WebSocket connections per pod.
| `WS_MAX_CONNECTIONS_PER_SESSION` | No | `2` | Max connections per session.
| `DB_POOL_MIN_SIZE` | No | `1` | Async DB pool min size.
| `DB_POOL_MAX_SIZE` | No | `5` | Async DB pool max size.
| `FEATURE_RISK_DASHBOARD` | No | `true` | Enable/disable risk analytics dashboard.
| `FEATURE_ALPHA_EXPLORER` | No | `true` | Enable/disable alpha signal explorer (P5T8).
| `FEATURE_STRATEGY_COMPARISON` | No | `true` | Enable/disable strategy comparison tool (P5T8).
| `FEATURE_TRADE_JOURNAL` | No | `true` | Enable/disable trade journal (P5T8).
| `FEATURE_PERFORMANCE_DASHBOARD` | No | `true` | Enable/disable performance dashboard (P5T8).
| `RISK_BUDGET_VAR_LIMIT` | No | `0.05` | Maximum VaR limit for risk budget gauge (5%).
| `RISK_BUDGET_WARNING_THRESHOLD` | No | `0.8` | Warning threshold for risk utilization (80%).

## Observability
- **Health:** `GET /healthz` (liveness), `GET /readyz` (readiness).
- **Metrics:** `GET /metrics` (internal/protected).
- **Logs:** Auth events, connection handling, Redis/backend health warnings.

## Security
- Auth middleware supports dev/basic/mTLS/OAuth2.
- Session cookies include secure/HttpOnly options and CSRF cookie where applicable.
- Trusted proxies and allowed hosts enforced.
- Admission control prevents connection exhaustion.
- Internal probe token protects /metrics and /readyz (configurable).

## Testing
- **Test Files:** `tests/apps/web_console_ng/`
- **Run Tests:** `pytest tests/apps/web_console_ng -v`

## Usage Examples
### Example 1: Liveness check
```bash
curl -s http://localhost:8080/healthz
```

### Example 2: Readiness check (internal probe)
```bash
curl -s -H "X-Internal-Probe: $INTERNAL_PROBE_TOKEN" http://localhost:8080/readyz
```

## Edge Cases & Boundaries
| Scenario | Input | Expected Behavior |
|----------|-------|-------------------|
| Missing probe token | `GET /readyz` without header | 401/403 or 503 (if internal check fails). |
| Redis unavailable | Session store down | `/readyz` returns 503. |
| Connection limit exceeded | Too many WS connections | Admission control rejects. |
| Kill switch ENGAGED | Close position attempt | Action blocked with error notification. |
| Kill switch UNKNOWN | Any trading action | Both engage/disengage disabled; action blocked. |
| Viewer role | Access `/manual-order` | Redirected to home with warning. |
| Synthetic order ID | Cancel `unknown_*` order | Cancel blocked; "contact support" message. |
| Fractional quantity | Submit order with 1.5 qty | Validation error; whole numbers required. |
| Circuit breaker TRIPPED | Close position | Warning shown; close allowed (risk reduction). |
| Order missing IDs | Order without client_order_id or broker_id | Synthetic ID generated; user notified to contact support. |

## Known Issues & TODO
| Issue | Severity | Description | Tracking |
|-------|----------|-------------|----------|
| None | - | No known issues | - |

## Related Specs
- `docs/SPECS/services/execution_gateway.md`
- `docs/SPECS/services/auth_service.md`
- `docs/SPECS/libs/web_console_auth.md`
- `docs/SPECS/libs/web_console_data.md`
- `docs/SPECS/libs/web_console_services.md`

## Metadata
- **Last Updated: 2026-02-01 (P6T10 Quantile & Attribution Analytics - quantile tearsheet, factor contribution chart, attribution page)
- **Source Files:** `apps/web_console_ng/main.py`, `apps/web_console_ng/config.py`, `apps/web_console_ng/core/health.py`, `apps/web_console_ng/core/metrics.py`, `apps/web_console_ng/core/realtime.py`, `apps/web_console_ng/core/client_lifecycle.py`, `apps/web_console_ng/core/client.py`, `apps/web_console_ng/core/audit.py`, `apps/web_console_ng/core/synthetic_id.py`, `apps/web_console_ng/core/database.py`, `apps/web_console_ng/core/dependencies.py`, `apps/web_console_ng/core/grid_performance.py`, `apps/web_console_ng/core/workspace_persistence.py`, `apps/web_console_ng/core/latency_monitor.py`, `apps/web_console_ng/core/connection_monitor.py`, `apps/web_console_ng/core/notification_router.py`, `apps/web_console_ng/core/hotkey_manager.py`, `apps/web_console_ng/core/level2_websocket.py`, `apps/web_console_ng/core/sparkline_service.py`, `apps/web_console_ng/api/workspace.py`, `apps/web_console_ng/auth/routes.py`, `apps/web_console_ng/auth/logout.py`, `apps/web_console_ng/utils/formatters.py`, `apps/web_console_ng/utils/session.py`, `apps/web_console_ng/utils/time.py`, `apps/web_console_ng/utils/orders.py`, `apps/web_console_ng/components/positions_grid.py`, `apps/web_console_ng/components/orders_table.py`, `apps/web_console_ng/components/drawdown_chart.py`, `apps/web_console_ng/components/equity_curve_chart.py`, `apps/web_console_ng/components/pnl_chart.py`, `apps/web_console_ng/components/var_chart.py`, `apps/web_console_ng/components/factor_exposure_chart.py`, `apps/web_console_ng/components/stress_test_results.py`, `apps/web_console_ng/components/ic_chart.py`, `apps/web_console_ng/components/decay_curve.py`, `apps/web_console_ng/components/correlation_matrix.py`, `apps/web_console_ng/components/header_metrics.py`, `apps/web_console_ng/components/market_clock.py`, `apps/web_console_ng/components/status_bar.py`, `apps/web_console_ng/components/log_drawer.py`, `apps/web_console_ng/components/action_button.py`, `apps/web_console_ng/components/command_palette.py`, `apps/web_console_ng/components/loading_states.py`, `apps/web_console_ng/components/order_ticket.py`, `apps/web_console_ng/components/quantity_presets.py`, `apps/web_console_ng/components/order_entry_context.py`, `apps/web_console_ng/components/market_context.py`, `apps/web_console_ng/components/price_chart.py`, `apps/web_console_ng/components/watchlist.py`, `apps/web_console_ng/components/hierarchical_orders.py`, `apps/web_console_ng/components/tabbed_panel.py`, `apps/web_console_ng/components/dom_ladder.py`, `apps/web_console_ng/components/depth_visualizer.py`, `apps/web_console_ng/components/sparkline_renderer.py`, `apps/web_console_ng/components/symbol_filter.py`, `apps/web_console_ng/components/execution_style_selector.py`, `apps/web_console_ng/components/fat_finger_validator.py`, `apps/web_console_ng/components/order_modify_dialog.py`, `apps/web_console_ng/components/twap_config.py`, `apps/web_console_ng/components/safety_gate.py`, `apps/web_console_ng/components/flatten_controls.py`, `apps/web_console_ng/components/one_click_handler.py`, `apps/web_console_ng/components/cancel_all_dialog.py`, `apps/web_console_ng/components/order_replay.py`, `apps/web_console_ng/components/grid_export_toolbar.py`, `apps/web_console_ng/components/order_audit_panel.py`, `apps/web_console_ng/components/tca_chart.py`, `apps/web_console_ng/components/quantile_tearsheet.py`, `apps/web_console_ng/components/factor_contribution_chart.py`, `apps/web_console_ng/ui/lightweight_charts.py`, `apps/web_console_ng/pages/dashboard.py`, `apps/web_console_ng/pages/manual_order.py`, `apps/web_console_ng/pages/position_management.py`, `apps/web_console_ng/pages/risk.py`, `apps/web_console_ng/pages/health.py`, `apps/web_console_ng/pages/backtest.py`, `apps/web_console_ng/pages/admin.py`, `apps/web_console_ng/pages/alerts.py`, `apps/web_console_ng/pages/circuit_breaker.py`, `apps/web_console_ng/pages/data_management.py`, `apps/web_console_ng/pages/alpha_explorer.py`, `apps/web_console_ng/pages/compare.py`, `apps/web_console_ng/pages/journal.py`, `apps/web_console_ng/pages/notebook_launcher.py`, `apps/web_console_ng/pages/performance.py`, `apps/web_console_ng/pages/scheduled_reports.py`, `apps/web_console_ng/pages/execution_quality.py`, `apps/web_console_ng/pages/attribution.py`, `apps/web_console_ng/ui/layout.py`, `apps/web_console_ng/ui/helpers.py`, `apps/web_console_ng/ui/dark_theme.py`, `apps/web_console_ng/ui/trading_layout.py`, `apps/web_console_ng/ui/theme.py`, `apps/web_console_ng/static/js/hotkey_handler.js`, `apps/web_console_ng/static/js/cell_flash.js`
- **ADRs:** ADR-0032 (Notification and Hotkey System), ADR-0033 (Order Modification Schema)
- **Tasks:** P5T4 (Real-Time Dashboard), P5T5 (Manual Trading Controls), P5T6 (Charts & Analytics), P5T7 (Remaining Pages), P5T8 (Alpha Explorer, Compare, Journal, Notebooks, Performance, Reports), P5T10 (Console Debug - Trades Integration, Admin Reconciliation), P6T1 (Core Infrastructure - throttling, dark mode, density, workspace persistence), P6T2 (Header Metrics - NLV, leverage, day change display), P6T3 (Notification & Hotkey System - notifications, hotkeys, action buttons, cell flash), P6T4 (Order Entry Context - time utilities, order ticket, quantity presets), P6T5 (Grid Enhancements - hierarchical orders, tabbed panel, DOM/L2, sparklines), P6T6 (Advanced Orders - stop orders, TWAP controls, fat finger validation, order modification), P6T7 (Order Actions - safety gate, flatten/reverse controls, one-click handler), P6T8 (Execution Analytics - TCA dashboard, grid export, order audit), P6T10 (Quantile & Attribution Analytics - quantile tearsheet, factor contribution chart, attribution page)
