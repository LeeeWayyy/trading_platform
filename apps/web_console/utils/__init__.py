"""Shared utilities for web console authentication.

This module now re-exports network utilities from libs/common/network_utils.py
for backward compatibility. The functions were moved to resolve an architectural
layering violation where auth_service was depending on web_console.

Direct users should import from libs.common.network_utils instead.
"""

# Re-export for backward compatibility
from libs.common.network_utils import (  # noqa: F401
    extract_client_ip_from_fastapi,
    extract_user_agent_from_fastapi,
    validate_trusted_proxy,
)
