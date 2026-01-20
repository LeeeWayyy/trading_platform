"""Reconciliation package for execution gateway.

This package provides the ReconciliationService and related utilities
for synchronizing broker state with the local database.

BACKWARD COMPATIBILITY:
    All exports from the original reconciliation.py module are re-exported
    here to maintain the same import paths:

    >>> from apps.execution_gateway.reconciliation import ReconciliationService
    >>> from apps.execution_gateway.reconciliation import SOURCE_PRIORITY_WEBHOOK
    >>> from apps.execution_gateway.reconciliation import QUARANTINE_STRATEGY_SENTINEL

Package Structure:
    - service.py: ReconciliationService orchestrator (main entry point)
    - state.py: Startup gate and override state management
    - context.py: Dependency injection context and config
    - orders.py: Order reconciliation and CAS updates
    - fills.py: Fill backfill from broker data and activities API
    - positions.py: Position synchronization
    - orphans.py: Orphan order detection and quarantine
    - helpers.py: Pure utility functions (no side effects)
"""

# Re-export the main service class
# Re-export context for dependency injection
from apps.execution_gateway.reconciliation.context import (
    ReconciliationConfig,
    ReconciliationContext,
)

# Re-export helper functions for direct use/testing
from apps.execution_gateway.reconciliation.helpers import (
    calculate_synthetic_fill,
    estimate_notional,
    extract_broker_client_ids,
    generate_fill_id_from_activity,
    merge_broker_orders,
)

# Re-export source priority constants (used by webhooks.py, slicing.py)
from apps.execution_gateway.reconciliation.orders import (
    SOURCE_PRIORITY_MANUAL,
    SOURCE_PRIORITY_RECONCILIATION,
    SOURCE_PRIORITY_WEBHOOK,
    reconciliation_conflicts_skipped_total,
    reconciliation_mismatches_total,
)

# Re-export quarantine sentinel (used for orphan order tracking)
# Re-export Prometheus metrics for compatibility
from apps.execution_gateway.reconciliation.orphans import (
    QUARANTINE_STRATEGY_SENTINEL,
    symbols_quarantined_total,
)
from apps.execution_gateway.reconciliation.service import (
    ReconciliationService,
    reconciliation_last_run_timestamp,
)

# Re-export state management for testing
from apps.execution_gateway.reconciliation.state import ReconciliationState

__all__ = [
    # Main service
    "ReconciliationService",
    # Source priority constants (critical for CAS ordering)
    "SOURCE_PRIORITY_MANUAL",
    "SOURCE_PRIORITY_RECONCILIATION",
    "SOURCE_PRIORITY_WEBHOOK",
    # Quarantine sentinel
    "QUARANTINE_STRATEGY_SENTINEL",
    # State management
    "ReconciliationState",
    # Dependency injection
    "ReconciliationConfig",
    "ReconciliationContext",
    # Pure helpers
    "calculate_synthetic_fill",
    "estimate_notional",
    "extract_broker_client_ids",
    "generate_fill_id_from_activity",
    "merge_broker_orders",
    # Prometheus metrics
    "reconciliation_conflicts_skipped_total",
    "reconciliation_last_run_timestamp",
    "reconciliation_mismatches_total",
    "symbols_quarantined_total",
]
