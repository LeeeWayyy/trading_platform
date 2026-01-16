"""Service layer for Execution Gateway.

This package contains extracted business logic and helper functions from main.py.
Each module provides focused, testable functions that can be imported and used
throughout the application.

Modules:
    pnl_calculator: P&L calculation functions for positions and performance
    performance_cache: Caching logic for performance dashboard
    order_helpers: Idempotency and fat-finger validation helpers
    auth_helpers: Authentication context building

See REFACTOR_EXECUTION_GATEWAY_TASK.md Phase 1 for design decisions.
"""
