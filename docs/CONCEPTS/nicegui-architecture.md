# NiceGUI Architecture

**Last Updated:** 2026-01-04
**Related:** [ADR-0031](../ADRs/ADR-0031-nicegui-migration.md), [nicegui-auth.md](./nicegui-auth.md), [nicegui-realtime.md](./nicegui-realtime.md)

## Overview

The NiceGUI web console uses an event-driven AsyncIO architecture that eliminates Streamlit's script-rerun model. Components update independently, async operations don't block the UI, and WebSocket push enables real-time updates.

## Execution Model Comparison

### Streamlit (Old)
```
User Interaction
    ↓
Re-execute entire script
    ↓
Re-render all components
    ↓
Display updated UI
```

### NiceGUI (New)
```
User Interaction
    ↓
Event handler fires
    ↓
Update only affected component
    ↓
WebSocket push to browser
```

## Key Patterns

### Page Structure

```python
from nicegui import ui
from apps.web_console_ng.auth.middleware import requires_auth
from apps.web_console_ng.ui.layout import main_layout

@ui.page("/my-page")
@requires_auth
@main_layout
async def my_page() -> None:
    """Page with auth and layout."""
    ui.label("Hello World")
```

### Async Service Calls

For sync services, use `run.io_bound()` to avoid blocking:

```python
from nicegui import run

# BAD - blocks event loop
result = sync_service.fetch_data()

# GOOD - runs in thread pool
result = await run.io_bound(sync_service.fetch_data)
```

### Reactive Updates with @ui.refreshable

```python
@ui.refreshable
def data_table() -> None:
    data = fetch_data()
    ui.table(columns=COLUMNS, rows=data)

# Initial render
data_table()

# Later, trigger refresh
data_table.refresh()
```

### Component Lifecycle

```python
from apps.web_console_ng.core.client_lifecycle import ClientLifecycleManager

lifecycle = ClientLifecycleManager.get()

# Register cleanup on client disconnect
lifecycle.on_disconnect(cleanup_callback)

# Register timer with auto-cleanup
timer = ui.timer(5.0, periodic_update)
lifecycle.register_timer(timer)
```

## Service Integration

Services from `apps/web_console/services/` are integrated via:

1. **Lazy Import**: Import inside function to avoid circular deps
2. **IO Bound Wrapper**: Wrap sync calls with `run.io_bound()`
3. **Error Handling**: Catch exceptions and show user-friendly messages
4. **Demo Mode**: Graceful degradation when service unavailable

```python
def _get_service() -> MyService | None:
    try:
        from apps.web_console.services.my_service import MyService
        return MyService(dependencies...)
    except Exception:
        logger.exception("Service init failed")
        return None

async def my_page() -> None:
    service = await run.io_bound(_get_service)
    if service is None:
        _render_demo_mode()
        return
    _render_with_service(service)
```

## Error Handling

```python
async def safe_operation() -> None:
    try:
        result = await run.io_bound(risky_call)
        ui.notify("Success!", type="positive")
    except ValidationError as e:
        ui.notify(f"Validation error: {e}", type="negative")
    except PermissionError:
        ui.notify("Permission denied", type="negative")
    except Exception:
        logger.exception("Unexpected error")
        ui.notify("Operation failed", type="negative")
```

## Directory Structure

```
apps/web_console_ng/
├── auth/           # Auth middleware, session store
├── components/     # Reusable UI components
├── core/           # Infrastructure (database, redis, client)
├── pages/          # Page handlers (@ui.page)
├── services/       # Page-specific services (optional)
├── ui/             # Layout, disconnect overlay
├── utils/          # Formatters, helpers
├── config.py       # Configuration
└── main.py         # Application entry point
```

## Best Practices

1. **Keep pages thin**: Business logic in services
2. **Use async**: Never block the event loop
3. **Handle errors**: Show user-friendly messages
4. **Demo mode**: Support graceful degradation
5. **Type hints**: Use strict mypy
6. **Component isolation**: Small, focused components
