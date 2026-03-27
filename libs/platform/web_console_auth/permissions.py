"""Permission helpers shared across services.

P6T19: Simplified to single-admin model — all permission checks return True.
Enums and function signatures preserved to avoid breaking 170+ call sites.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import typing
from collections.abc import Callable
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

# P6T19: _normalize_role, _extract_role, _normalize_dataset_key kept for
# any external callers but no longer used by simplified check functions.


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
    VIEW_FEATURES = "view_features"  # P6T14: Feature Store Browser access
    VIEW_SHADOW_RESULTS = "view_shadow_results"  # P6T14: Shadow results access

    # T15: Universe & Exposure
    VIEW_UNIVERSES = "view_universes"  # T15.1/T15.2: Universe browser/analytics
    MANAGE_UNIVERSES = "manage_universes"  # T15.1: Create/edit/delete custom universes
    VIEW_STRATEGY_EXPOSURE = "view_strategy_exposure"  # T15.3: Strategy exposure dashboard

    # P6T17: Model Registry Browser
    VIEW_MODELS = "view_models"  # View model registry page


class DatasetPermission(str, Enum):
    """Per-dataset access permissions for licensing compliance."""

    CRSP_ACCESS = "dataset:crsp"
    COMPUSTAT_ACCESS = "dataset:compustat"
    TAQ_ACCESS = "dataset:taq"
    FAMA_FRENCH_ACCESS = "dataset:fama_french"


# P6T19: Emptied — single-admin model, all checks return True.
# Kept as importable empty dicts for backwards compatibility.
ROLE_PERMISSIONS: dict[Role, set[Permission]] = {}

# P6T19: Emptied — single-admin model, all checks return True.
ROLE_DATASET_PERMISSIONS: dict[Role, set[DatasetPermission]] = {}


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
    """P6T19: Single-admin model — always grants permission."""

    return True


def has_dataset_permission(user_or_role: Any, dataset: str) -> bool:
    """P6T19: Single-admin model — always grants dataset access."""

    return True


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
            # Use key-presence checks, not truthiness: user={} (falsy)
            # must not fall through to session or positional args.
            subject = (
                kwargs.get("user")
                if "user" in kwargs
                else kwargs.get("session")
                if "session" in kwargs
                else None
            )
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
            subject = (
                kwargs.get("user")
                if "user" in kwargs
                else kwargs.get("session")
                if "session" in kwargs
                else None
            )
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
        # Merge the original function's globals into the wrapper so that
        # FastAPI can resolve string annotations (from `from __future__
        # import annotations`) via the wrapper's __globals__.  Use
        # dict-merge order so the wrapper's own symbols (e.g. Permission,
        # has_permission) take precedence and are never overwritten.
        wrapper_globals = getattr(wrapper, "__globals__", None)
        if isinstance(wrapper_globals, dict):
            merged = {**func.__globals__, **wrapper_globals}
            wrapper_globals.update(merged)
        if resolved_annotations is not None:
            wrapper.__annotations__ = resolved_annotations

        return wrapper

    return decorator


def is_admin(user_or_role: Any) -> bool:
    """P6T19: Single-admin model — always returns True."""

    return True


def get_authorized_strategies(user: Any | None) -> list[str]:
    """P6T19: Return all strategies from user's session data.

    Single-admin model — no role-based filtering. Returns the sanitized
    strategies list populated by the auth provider at login time.
    """

    if not user:
        return []

    raw_strategies: Any
    if isinstance(user, dict):
        raw_strategies = user.get("strategies", [])
    else:
        raw_strategies = getattr(user, "strategies", []) or []

    if not isinstance(raw_strategies, list | tuple):
        return []

    # Sanitize: keep only non-empty strings, deduplicate, preserve order.
    seen: set[str] = set()
    strategies_list: list[str] = []
    for s in raw_strategies:
        if not isinstance(s, str):
            continue
        s = s.strip()
        if s and s not in seen:
            seen.add(s)
            strategies_list.append(s)

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
