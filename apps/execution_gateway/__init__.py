"""
Execution Gateway - T4 Implementation.

This module provides order execution capabilities with idempotent submission,
DRY_RUN mode support, and Alpaca broker integration.

Key Features:
- Idempotent order submission with deterministic client_order_id
- DRY_RUN mode for safe testing without broker submission
- Real-time order status updates via webhooks
- Position tracking from order fills
- Retry logic with exponential backoff

Components:
- schemas: Pydantic models for API requests/responses
- order_generator: Deterministic client_order_id generation
- alpaca_client: Alpaca API wrapper with retry logic
- database: Database operations for orders and positions
- main: FastAPI application with all endpoints

See ADR-0005 for architectural decisions.
"""

__version__ = "0.1.0"
