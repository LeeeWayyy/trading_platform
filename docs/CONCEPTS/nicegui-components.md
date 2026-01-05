# NiceGUI Components

**Last Updated:** 2026-01-04
**Related:** [ADR-0031](../ADRs/ADR-0031-nicegui-migration.md), [nicegui-architecture.md](./nicegui-architecture.md)

## Overview

NiceGUI components are reusable UI building blocks. This guide covers common patterns and custom components used in the web console.

## Component Organization

```
apps/web_console_ng/components/
├── activity_feed.py       # Real-time activity feed
├── correlation_matrix.py  # Signal correlation heatmap
├── decay_curve.py         # Alpha decay visualization
├── factor_exposure_chart.py
├── ic_chart.py            # IC time series chart
├── metric_card.py         # Metric display card
├── orders_table.py        # Orders grid with actions
├── pnl_chart.py           # P&L visualization
├── positions_grid.py      # AG Grid positions table
├── stress_test_results.py
└── var_chart.py           # VaR visualization
```

## AG Grid Usage

```python
from nicegui import ui

def render_positions_grid(positions: list[dict]) -> None:
    columns = [
        {"field": "symbol", "headerName": "Symbol", "sortable": True},
        {"field": "qty", "headerName": "Quantity", "type": "numericColumn"},
        {"field": "unrealized_pnl", "headerName": "P&L", "type": "numericColumn"},
    ]
    
    ui.aggrid({
        "columnDefs": columns,
        "rowData": positions,
        "defaultColDef": {"resizable": True, "filter": True},
    }).classes("h-64")
```

### AG Grid Features Used

- **Sorting**: Click column headers
- **Filtering**: Built-in column filters
- **Resizing**: Drag column borders
- **Selection**: Row selection for actions
- **Pagination**: Virtual scrolling for large datasets

## Form Patterns

### Basic Form

```python
with ui.card().classes("p-4"):
    symbol = ui.input("Symbol").classes("w-full")
    quantity = ui.number("Quantity", min=1)
    
    async def submit() -> None:
        if not symbol.value:
            ui.notify("Symbol required", type="negative")
            return
        await process_order(symbol.value, quantity.value)
    
    ui.button("Submit", on_click=submit)
```

### Form Validation

```python
def validate_order(symbol: str, qty: int) -> list[str]:
    errors = []
    if not symbol:
        errors.append("Symbol is required")
    if qty <= 0:
        errors.append("Quantity must be positive")
    return errors

async def submit() -> None:
    errors = validate_order(symbol.value, quantity.value)
    if errors:
        for e in errors:
            ui.notify(e, type="negative")
        return
    # Proceed
```

## Dialog Patterns

### Confirmation Dialog

```python
async def confirm_action() -> None:
    with ui.dialog() as dialog, ui.card():
        ui.label("Are you sure?")
        with ui.row():
            ui.button("Cancel", on_click=dialog.close)
            
            async def confirm() -> None:
                dialog.close()
                await perform_action()
            
            ui.button("Confirm", on_click=confirm, color="red")
    
    dialog.open()
```

## Tab Patterns

```python
with ui.tabs().classes("w-full") as tabs:
    tab_overview = ui.tab("Overview")
    tab_details = ui.tab("Details")
    tab_history = ui.tab("History")

with ui.tab_panels(tabs, value=tab_overview):
    with ui.tab_panel(tab_overview):
        render_overview()
    with ui.tab_panel(tab_details):
        render_details()
    with ui.tab_panel(tab_history):
        render_history()
```

## Chart Integration (Plotly)

```python
import plotly.graph_objects as go
from nicegui import ui

def render_equity_curve(dates: list, values: list) -> None:
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=dates,
        y=values,
        mode="lines",
        name="Equity",
        line={"color": "#1f77b4", "width": 2},
        fill="tozeroy",
    ))
    fig.update_layout(
        title="Equity Curve",
        xaxis_title="Date",
        yaxis_title="Value",
        height=350,
    )
    ui.plotly(fig).classes("w-full")
```

## Download Functionality

```python
import pandas as pd
from nicegui import ui

def export_csv(data: list[dict], filename: str) -> None:
    df = pd.DataFrame(data)
    csv_bytes = df.to_csv(index=False).encode("utf-8")
    ui.download(csv_bytes, filename)

ui.button("Export CSV", on_click=lambda: export_csv(data, "export.csv"))
```

## Metric Cards

```python
def metric_card(label: str, value: str, delta: str | None = None) -> None:
    with ui.card().classes("p-3 min-w-28"):
        ui.label(label).classes("text-xs text-gray-500")
        ui.label(value).classes("text-lg font-bold")
        if delta:
            color = "text-green-600" if delta.startswith("+") else "text-red-600"
            ui.label(delta).classes(f"text-sm {color}")
```

## Service Dependency Injection

Components receive services as parameters:

```python
def render_positions(client: AsyncTradingClient) -> None:
    async def fetch_and_render() -> None:
        positions = await client.get_positions()
        # Render positions
    
    ui.button("Refresh", on_click=fetch_and_render)
```

## Best Practices

1. **Keep components focused**: One responsibility per component
2. **Type hints**: All parameters and returns typed
3. **Error handling**: Graceful UI feedback on errors
4. **Accessibility**: Use semantic elements and ARIA labels
5. **Responsive design**: Use Tailwind classes for responsive layouts
6. **Avoid global state**: Pass data via parameters
7. **Lazy loading**: Load heavy data on demand
