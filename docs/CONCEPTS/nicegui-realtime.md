# NiceGUI Real-Time Updates

**Last Updated:** 2026-01-04
**Related:** [ADR-0031](../ADRs/ADR-0031-nicegui-migration.md), [nicegui-architecture.md](./nicegui-architecture.md)

## Overview

NiceGUI provides real-time updates via WebSocket push, eliminating the need for polling. Components can subscribe to data channels and receive updates immediately when server-side state changes.

## WebSocket Push Architecture

```
┌─────────────┐     ┌──────────────┐     ┌─────────┐
│   Browser   │<═══>│  NiceGUI     │<───>│  Redis  │
│  WebSocket  │     │  Server      │     │  PubSub │
└─────────────┘     └──────────────┘     └─────────┘
                           │
                           ▼
                    ┌──────────────┐
                    │  Trading     │
                    │  Services    │
                    └──────────────┘
```

## ui.timer for Periodic Updates

```python
from nicegui import ui

async def periodic_update() -> None:
    data = await fetch_latest_data()
    update_display(data)

# Update every 5 seconds
timer = ui.timer(5.0, periodic_update)
```

### Timer Lifecycle Management

```python
from apps.web_console_ng.core.client_lifecycle import ClientLifecycleManager

lifecycle = ClientLifecycleManager.get()

# Register timer for auto-cleanup on disconnect
timer = ui.timer(5.0, periodic_update)
lifecycle.register_timer(timer)
```

## Redis Pub/Sub for Cross-Instance Updates

```python
from apps.web_console_ng.core.realtime import RealtimeUpdater, kill_switch_channel

updater = RealtimeUpdater()

# Subscribe to channel
async def on_kill_switch_update(message: dict) -> None:
    update_ui(message)

await updater.subscribe(kill_switch_channel, on_kill_switch_update)
```

### Available Channels

| Channel | Description |
|---------|-------------|
| `kill_switch` | Kill switch state changes |
| `positions` | Position updates |
| `orders` | Order status updates |
| `circuit_breaker` | Circuit breaker state |

## Progressive Polling Pattern

For graceful degradation when WebSocket unavailable:

```python
# Start with fast polling, slow down over time
POLL_INTERVALS = [2, 5, 10, 30]  # seconds

class ProgressivePoller:
    def __init__(self) -> None:
        self.interval_index = 0
    
    async def poll(self) -> None:
        try:
            await self.fetch_and_update()
            self.interval_index = 0  # Reset on success
        except Exception:
            # Slow down on failure
            self.interval_index = min(
                self.interval_index + 1,
                len(POLL_INTERVALS) - 1
            )
        
        # Schedule next poll
        await asyncio.sleep(POLL_INTERVALS[self.interval_index])
```

## Connection Recovery

```python
from apps.web_console_ng.core.connection_events import setup_connection_handlers

# Setup handlers
setup_connection_handlers(app)

# On reconnect, rehydrate state
async def on_reconnect(client_id: str) -> None:
    await rehydrate_client_state(client_id)
```

## State Rehydration

When connection is restored, rehydrate UI state:

```python
from apps.web_console_ng.core.state_manager import get_state_manager

manager = get_state_manager()

# Save state before disconnect
await manager.save_state(client_id, state_data)

# Restore state on reconnect
state = await manager.restore_state(client_id)
```

## Real-Time vs Polling Trade-offs

| Aspect | Real-Time (WebSocket) | Polling |
|--------|----------------------|---------|
| Latency | <100ms | 2-30s |
| Network | Lower overall | Higher |
| Complexity | Higher | Lower |
| Scalability | Connection limits | Request limits |
| Battery | Better | Worse |

## Best Practices

1. **Use timers for user-facing pages**: Auto-refresh key metrics
2. **Register timers for cleanup**: Prevent memory leaks
3. **Progressive polling**: Graceful degradation
4. **Redis pub/sub for cross-instance**: Share state across servers
5. **State rehydration**: Handle reconnects gracefully
6. **Appropriate intervals**: Balance freshness vs load
