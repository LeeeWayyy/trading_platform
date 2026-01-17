"""Route modules for Execution Gateway.

This package contains FastAPI APIRouter modules organized by domain:
- health: Health check and root endpoints
- admin: Kill-switch, configuration, and strategy management
- orders: Order submission and cancellation
- slicing: TWAP/slice order execution
- positions: Position queries and performance metrics
- webhooks: Alpaca webhook handlers
- reconciliation: Reconciliation admin endpoints

Each router maintains the same API paths and behavior as the original main.py
while providing better organization and testability.
"""
