"""Shared constants used across services.

Keep constants here that must stay in sync between the execution gateway,
web console, and other services.  A single source of truth prevents
accidental drift when one file is updated but the other is forgotten.
"""

from __future__ import annotations

# Order statuses shown in the dashboard's Working-orders grid.
# This is a *strict subset* of ``PENDING_STATUSES`` from
# ``apps.execution_gateway.database``; the broader set includes
# ``submitted`` and ``submitted_unconfirmed`` which are not
# rendered in the UI's working-orders tab.
#
# Used by:
#   - ``apps.execution_gateway.routes.export``  (server-side SQL filter)
#   - ``apps.web_console_ng.components.tabbed_panel``  (client-side filter)
WORKING_ORDER_STATUSES: frozenset[str] = frozenset(
    {
        "new",
        "pending_new",
        "partially_filled",
        "accepted",
        "pending_cancel",
        "pending_replace",
    }
)
