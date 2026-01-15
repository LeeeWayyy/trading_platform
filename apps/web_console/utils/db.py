"""Database connection helpers (re-exported from libs.platform.web_console_auth.db).

Kept for backward compatibility with existing imports inside the Web Console
app. The implementation now lives in libs.web_console_auth to avoid
cross-service coupling.
"""

from libs.platform.web_console_auth.db import acquire_connection

__all__ = ["acquire_connection"]
