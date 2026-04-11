"""Shared order-related constants.

These constants are used across multiple services (execution gateway,
web console) and must remain in sync.  Centralising them here prevents
duplication and ensures consistent behaviour.
"""

from __future__ import annotations

# Order statuses considered "working" (active / in-flight).
# Orders in these statuses are shown on the "Working Orders" tab
# in the web console and should be the only statuses included
# when exporting from that tab.
WORKING_ORDER_STATUSES: frozenset[str] = frozenset({
    "new",
    "pending_new",
    "partially_filled",
    "accepted",
    "pending_cancel",
    "pending_replace",
})

__all__ = ["WORKING_ORDER_STATUSES"]
