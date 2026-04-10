"""UI components for NiceGUI web console."""

from __future__ import annotations

from apps.web_console_ng.components.cancel_all_dialog import CancelAllDialog
from apps.web_console_ng.components.flatten_controls import FlattenControls
from apps.web_console_ng.components.log_tail_panel import LogTailPanel
from apps.web_console_ng.components.market_context import (
    MarketContextComponent,
    MarketDataSnapshot,
)
from apps.web_console_ng.components.one_click_handler import OneClickConfig, OneClickHandler
from apps.web_console_ng.components.order_entry_context import OrderEntryContext
from apps.web_console_ng.components.order_flow_panel import OrderFlowPanel
from apps.web_console_ng.components.order_replay import OrderReplayHandler, ReplayableOrder
from apps.web_console_ng.components.order_ticket import OrderTicketComponent, OrderTicketState
from apps.web_console_ng.components.price_chart import (
    CandleData,
    ExecutionMarker,
    PriceChartComponent,
)
from apps.web_console_ng.components.quantity_presets import QuantityPresetsComponent
from apps.web_console_ng.components.safety_gate import (
    SafetyCheckResult,
    SafetyGate,
    SafetyPolicy,
)
from apps.web_console_ng.components.strategy_context import StrategyContextWidget
from apps.web_console_ng.components.watchlist import (
    WatchlistComponent,
    WatchlistItem,
)

__all__ = [
    "CancelAllDialog",
    "CandleData",
    "ExecutionMarker",
    "FlattenControls",
    "LogTailPanel",
    "MarketContextComponent",
    "MarketDataSnapshot",
    "OneClickConfig",
    "OneClickHandler",
    "OrderEntryContext",
    "OrderFlowPanel",
    "OrderReplayHandler",
    "OrderTicketComponent",
    "OrderTicketState",
    "PriceChartComponent",
    "QuantityPresetsComponent",
    "ReplayableOrder",
    "SafetyCheckResult",
    "SafetyGate",
    "SafetyPolicy",
    "StrategyContextWidget",
    "WatchlistComponent",
    "WatchlistItem",
]
