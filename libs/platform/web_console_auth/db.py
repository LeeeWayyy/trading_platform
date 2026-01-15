"""Database connection helpers (re-exported from libs.core.common.db).

Backward compatibility shim - the canonical implementation now lives in
libs/core/common/db.py to maintain proper layering (core → platform).
"""

from libs.core.common.db import acquire_connection

__all__ = ["acquire_connection"]
