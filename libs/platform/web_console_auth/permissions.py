"""RBAC roles and permission helpers shared across services.

Default‑deny: any unknown role or missing mapping returns False.
Designed for lightweight use in Streamlit as well as backend services.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import typing
from collections.abc import Callable, Iterable
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class Role(str, Enum):
    """Supported RBAC roles."""

    VIEWER = "viewer"
    RESEARCHER = "researcher"
    OPERATOR = "operator"
    ADMIN = "admin"


class Permission(str, Enum):
    """Discrete permissions enforced by the web console."""

    VIEW_POSITIONS = "view_positions"
    VIEW_PNL = "view_pnl"
    VIEW_TRADES = "view_trades"
    VIEW_MARKET_DATA = "view_market_data"
    CANCEL_ORDER = "cancel_order"
    CLOSE_POSITION = "close_position"
    ADJUST_POSITION = "adjust_position"
    FLATTEN_ALL = "flatten_all"
    MANAGE_USERS = "manage_users"
    MANAGE_STRATEGIES = "manage_strategies"  # [v1.5] Strategy configuration
    MANAGE_RECONCILIATION = "manage_reconciliation"  # [v2.0] Reconciliation override
    VIEW_ALL_STRATEGIES = "view_all_strategies"
    VIEW_AUDIT = "view_audit"  # [v1.5] Audit log access
    EXPORT_DATA = "export_data"
    MANAGE_API_KEYS = "manage_api_keys"  # [v2.0] API key lifecycle operations
    MANAGE_SYSTEM_CONFIG = "manage_system_config"  # [v2.0] System config editing

    # T8.1: Data Sync Dashboard
    VIEW_DATA_SYNC = "view_data_sync"
    TRIGGER_DATA_SYNC = "trigger_data_sync"
    MANAGE_SYNC_SCHEDULE = "manage_sync_schedule"

    # T8.2: Dataset Explorer
    QUERY_DATA = "query_data"

    # T8.3: Data Quality
    VIEW_DATA_QUALITY = "view_data_quality"
    ACKNOWLEDGE_ALERTS = "acknowledge_alerts"  # Note: distinct from ACKNOWLEDGE_ALERT

    # Circuit Breaker permissions (T7.1)
    VIEW_CIRCUIT_BREAKER = "view_circuit_breaker"
    TRIP_CIRCUIT = "trip_circuit"
    RESET_CIRCUIT = "reset_circuit"

    # Alert configuration (T7.3)
    VIEW_ALERTS = "view_alerts"
    CREATE_ALERT_RULE = "create_alert_rule"
    UPDATE_ALERT_RULE = "update_alert_rule"
    DELETE_ALERT_RULE = "delete_alert_rule"
    TEST_NOTIFICATION = "test_notification"
    ACKNOWLEDGE_ALERT = "acknowledge_alert"

    # Trading API permissions (C6)
    SUBMIT_ORDER = "submit_order"
    MODIFY_ORDER = "modify_order"
    GENERATE_SIGNALS = "generate_signals"

    # P4T7: Alpha Signal Explorer (C1)
    VIEW_ALPHA_SIGNALS = "view_alpha_signals"

    # P4T7: Factor Exposure Heatmap (C2)
    VIEW_FACTOR_ANALYTICS = "view_factor_analytics"
    VIEW_ALL_POSITIONS = "view_all_positions"  # Admin-only for global positions

    # P4T7: Research Notebook Launcher (C3)
    LAUNCH_NOTEBOOKS = "launch_notebooks"
    MANAGE_NOTEBOOKS = "manage_notebooks"

    # P4T7: Scheduled Reports (C4)
    MANAGE_REPORTS = "manage_reports"
    VIEW_REPORTS = "view_reports"

    # P4T7: Tax Lot Reporter (C5/C6)
    VIEW_TAX_LOTS = "view_tax_lots"
    MANAGE_TAX_LOTS = "manage_tax_lots"
    VIEW_TAX_REPORTS = "view_tax_reports"
    MANAGE_TAX_SETTINGS = "manage_tax_settings"

    # P6T8: Execution Analytics
    VIEW_TCA = "view_tca"  # Transaction Cost Analysis dashboard access


class DatasetPermission(str, Enum):
    """Per-dataset access permissions for licensing compliance."""

    CRSP_ACCESS = "dataset:crsp"
    COMPUSTAT_ACCESS = "dataset:compustat"
    TAQ_ACCESS = "dataset:taq"
    FAMA_FRENCH_ACCESS = "dataset:fama_french"


ROLE_PERMISSIONS: dict[Role, set[Permission]] = {
    Role.VIEWER: {
        Permission.VIEW_POSITIONS,
        Permission.VIEW_PNL,
        Permission.VIEW_TRADES,
        Permission.VIEW_DATA_SYNC,
        Permission.VIEW_DATA_QUALITY,
        Permission.VIEW_CIRCUIT_BREAKER,  # T7.1: Can view CB status
        Permission.VIEW_ALERTS,
        Permission.VIEW_REPORTS,
        Permission.VIEW_TAX_LOTS,
    },
    Role.RESEARCHER: {
        Permission.VIEW_ALPHA_SIGNALS,
        Permission.VIEW_FACTOR_ANALYTICS,
        Permission.LAUNCH_NOTEBOOKS,
        Permission.VIEW_REPORTS,
        Permission.VIEW_TAX_LOTS,
        Permission.VIEW_TAX_REPORTS,
    },
    Role.OPERATOR: {
        Permission.VIEW_POSITIONS,
        Permission.VIEW_PNL,
        Permission.VIEW_TRADES,
        Permission.VIEW_MARKET_DATA,
        Permission.CANCEL_ORDER,
        Permission.MODIFY_ORDER,
        Permission.CLOSE_POSITION,
        Permission.ADJUST_POSITION,
        Permission.FLATTEN_ALL,
        Permission.EXPORT_DATA,
        Permission.QUERY_DATA,
        Permission.VIEW_DATA_SYNC,
        Permission.TRIGGER_DATA_SYNC,
        Permission.VIEW_DATA_QUALITY,
        Permission.ACKNOWLEDGE_ALERTS,
        Permission.MANAGE_STRATEGIES,  # [v1.5] Operators can manage strategies
        Permission.VIEW_CIRCUIT_BREAKER,  # T7.1: Can view CB status
        Permission.TRIP_CIRCUIT,  # T7.1: Can manually trip CB
        Permission.RESET_CIRCUIT,  # T7.1: Can reset CB (with rate limit)
        Permission.VIEW_ALERTS,
        Permission.CREATE_ALERT_RULE,
        Permission.UPDATE_ALERT_RULE,
        Permission.TEST_NOTIFICATION,
        Permission.ACKNOWLEDGE_ALERT,
        Permission.SUBMIT_ORDER,  # C6: Trading API access
        Permission.GENERATE_SIGNALS,  # C6: Signal generation access
        Permission.VIEW_REPORTS,
        Permission.VIEW_TAX_LOTS,
        Permission.VIEW_TCA,  # P6T8: TCA dashboard access
        Permission.VIEW_AUDIT,  # P6T8: Audit trail access
    },
    Role.ADMIN: set(Permission),  # Admins have all permissions including VIEW_AUDIT
}

ROLE_DATASET_PERMISSIONS: dict[Role, set[DatasetPermission]] = {
    Role.VIEWER: {DatasetPermission.FAMA_FRENCH_ACCESS},
    Role.RESEARCHER: {DatasetPermission.FAMA_FRENCH_ACCESS},
    Role.OPERATOR: {
        DatasetPermission.FAMA_FRENCH_ACCESS,
        DatasetPermission.CRSP_ACCESS,
        DatasetPermission.COMPUSTAT_ACCESS,
        DatasetPermission.TAQ_ACCESS,  # P6T8: TCA requires TAQ data
    },
    Role.ADMIN: set(DatasetPermission),  # All datasets
}


def _normalize_role(role_value: Any) -> Role | None:
    """Convert arbitrary role value to Role enum or None if unknown."""

    if isinstance(role_value, Role):
        return role_value
    if isinstance(role_value, str):
        try:
            return Role(role_value)
        except ValueError:
            return None
    return None


def _extract_role(user_or_role: Any) -> Role | None:
    """Extract Role from Role/str/dict inputs."""

    if isinstance(user_or_role, dict):
        return _normalize_role(user_or_role.get("role"))

    role_attr = getattr(user_or_role, "role", None)
    if role_attr is not None:
        return _normalize_role(role_attr)

    return _normalize_role(user_or_role)


def _normalize_dataset_key(dataset: str) -> str:
    """Normalize dataset names for lookup."""

    normalized = dataset.strip().lower().replace(" ", "_").replace("-", "_")
    if normalized.startswith("dataset:"):
        normalized = normalized.split(":", 1)[1]
    return normalized


def has_permission(user_or_role: Any, permission: Permission) -> bool:
    """Check if role grants a permission (default‑deny on unknown)."""

    role = _extract_role(user_or_role)
    if role is None:
        return False

    if role is Role.ADMIN:
        return True

    return permission in ROLE_PERMISSIONS.get(role, set())


def has_dataset_permission(user_or_role: Any, dataset: str) -> bool:
    """Check if user has access to specific dataset."""

    if not dataset:
        return False

    role = _extract_role(user_or_role)
    if role is None:
        return False

    if role is Role.ADMIN:
        return True

    permission: DatasetPermission | None
    if isinstance(dataset, DatasetPermission):
        permission = dataset
    else:
        dataset_key = _normalize_dataset_key(str(dataset))
        permission = {
            "crsp": DatasetPermission.CRSP_ACCESS,
            "compustat": DatasetPermission.COMPUSTAT_ACCESS,
            "taq": DatasetPermission.TAQ_ACCESS,
            "fama_french": DatasetPermission.FAMA_FRENCH_ACCESS,
        }.get(dataset_key)

    if permission is None:
        return False

    return permission in ROLE_DATASET_PERMISSIONS.get(role, set())


def require_permission(
    permission: Permission,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator enforcing the given permission.

    Looks for a ``user`` or ``session`` kwarg, or first positional argument
    that is a mapping with ``role``. If the first positional argument is a
    request-like object, a ``user`` or ``session`` attribute on that object
    will be used. Default‑deny if none found.
    Supports both sync and async callables. The decorated function must either
    expose a ``user`` or ``session`` keyword argument, or accept a first
    positional argument that is one of:
    - a mapping containing ``role``
    - an object with ``role`` attribute
    - a request-like object exposing ``user`` or ``session`` attributes.
    Calls that do not satisfy this contract will be denied.
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        is_coroutine = asyncio.iscoroutinefunction(func)

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            subject = kwargs.get("user") or kwargs.get("session")
            if subject is None and args:
                first_arg = args[0]
                # Common pattern: FastAPI/Starlette Request exposes .user
                subject = getattr(first_arg, "user", None)
                if subject is None:
                    subject = getattr(first_arg, "session", None)
                if subject is None:
                    subject = first_arg

            if not has_permission(subject, permission):
                if subject is None:
                    role_value = None
                elif isinstance(subject, dict):
                    role_value = subject.get("role")
                else:
                    role_value = getattr(subject, "role", None)
                logger.warning(
                    "permission_denied",
                    extra={"permission": permission.value, "role": role_value},
                )
                raise PermissionError(f"Permission '{permission.value}' required")

            return await func(*args, **kwargs)

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            subject = kwargs.get("user") or kwargs.get("session")
            if subject is None and args:
                first_arg = args[0]
                subject = getattr(first_arg, "user", None)
                if subject is None:
                    subject = getattr(first_arg, "session", None)
                if subject is None:
                    subject = first_arg

            if not has_permission(subject, permission):
                if subject is None:
                    role_value = None
                elif isinstance(subject, dict):
                    role_value = subject.get("role")
                else:
                    role_value = getattr(subject, "role", None)
                logger.warning(
                    "permission_denied",
                    extra={"permission": permission.value, "role": role_value},
                )
                raise PermissionError(f"Permission '{permission.value}' required")

            return func(*args, **kwargs)

        # Preserve resolved annotations for frameworks (e.g., FastAPI) that inspect
        # wrapper signatures in a different module namespace.
        try:
            resolved_annotations = typing.get_type_hints(func, include_extras=True)
        except Exception:  # pragma: no cover - best-effort for runtime safety
            resolved_annotations = None

        wrapper = async_wrapper if is_coroutine else sync_wrapper
        # Ensure wrapper can resolve forward references from the original module.
        wrapper_globals = getattr(wrapper, "__globals__", None)
        if isinstance(wrapper_globals, dict):
            wrapper_globals.update(func.__globals__)
        if resolved_annotations is not None:
            wrapper.__annotations__ = resolved_annotations

        return wrapper

    return decorator


def is_admin(user_or_role: Any) -> bool:
    """Check if user has admin role.

    Use this for security-sensitive checks where only true admins should
    have access (e.g., PII visibility). Prefer this over permission-based
    checks when Role.ADMIN exclusivity is required.

    Args:
        user_or_role: User object, role string, or dict with 'role' key

    Returns:
        True if user has Role.ADMIN, False otherwise
    """
    role = _extract_role(user_or_role)
    return role is Role.ADMIN


def get_authorized_strategies(user: Any | None) -> list[str]:
    """Return list of strategies user may access (default‑deny).

    Admins (with VIEW_ALL_STRATEGIES) are expected to receive the full list of
    strategy IDs from provisioning. Callers without VIEW_ALL_STRATEGIES get
    only their explicitly assigned strategies. Unknown roles or missing
    strategies return an empty list to fail closed.
    """

    if not user:
        return []

    role = _extract_role(user)
    strategies: Iterable[str]
    if isinstance(user, dict):
        strategies = user.get("strategies", [])
    else:
        # Support ORM/user objects that expose a ``strategies`` attribute
        strategies = getattr(user, "strategies", []) or []

    if role is None:
        return []

    strategies_list = list(strategies)

    if not has_permission(user, Permission.VIEW_ALL_STRATEGIES):
        # Fail closed: only return explicitly assigned strategies
        return strategies_list

    return strategies_list


__all__ = [
    "Role",
    "Permission",
    "DatasetPermission",
    "ROLE_PERMISSIONS",
    "ROLE_DATASET_PERMISSIONS",
    "has_permission",
    "has_dataset_permission",
    "is_admin",
    "require_permission",
    "get_authorized_strategies",
]
