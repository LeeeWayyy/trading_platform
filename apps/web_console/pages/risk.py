"""Risk Analytics Dashboard page.

This page displays portfolio risk analytics including:
- Factor exposures
- VaR/CVaR metrics with risk budget monitoring
- Stress test results

Data flows: risk.py -> RiskService -> StrategyScopedDataAccess -> libs/risk/
No HTTP/API calls - all data fetched via RiskService.
"""

from __future__ import annotations

import logging
from typing import Any

import streamlit as st

from apps.web_console.auth.permissions import Permission, get_authorized_strategies, has_permission
from apps.web_console.auth import get_current_user
from apps.web_console.auth.streamlit_helpers import requires_auth
from apps.web_console.components.factor_exposure_chart import render_factor_exposure
from apps.web_console.components.stress_test_results import render_stress_tests
from apps.web_console.components.var_chart import render_var_history, render_var_metrics
from apps.web_console.config import (
    FEATURE_RISK_DASHBOARD,
    RISK_BUDGET_VAR_LIMIT,
    RISK_BUDGET_WARNING_THRESHOLD,
)
from apps.web_console.data.strategy_scoped_queries import StrategyScopedDataAccess
from apps.web_console.services.risk_service import RiskDashboardData, RiskService
from apps.web_console.utils.async_helpers import run_async
from apps.web_console.utils.db_pool import get_db_pool, get_redis_client
from apps.web_console.utils.validators import validate_risk_metrics

logger = logging.getLogger(__name__)


@st.cache_data(ttl=300)
def _fetch_risk_data(user_id: str, role: str, strategies: tuple[str, ...]) -> dict[str, Any]:
    """Fetch risk data via RiskService with caching.

    Cache key includes user_id and strategies tuple for isolation.

    Args:
        user_id: User ID for cache isolation
        strategies: Tuple of authorized strategies (hashable)

    Returns:
        Dict representation of RiskDashboardData

    Note:
        T6.4a: Now wires real DB and Redis connections via get_db_pool() and
        get_redis_client(). The AsyncConnectionAdapter creates fresh connections
        per request, avoiding event loop binding issues with run_async().
    """
    if not user_id:
        raise RuntimeError("Missing user_id; refuse to fetch risk data")
    if not role:
        raise RuntimeError("Missing role; refuse to fetch risk data")

    # T6.4a: Wire real DB/Redis connections for direct data access
    # get_db_pool() returns AsyncConnectionAdapter (fresh connections per call)
    # get_redis_client() returns redis.asyncio.Redis for strategy cache (DB=3)
    db_pool = get_db_pool()
    if db_pool is None:
        raise RuntimeError("Database connection not configured (DATABASE_URL not set)")

    scoped_access = StrategyScopedDataAccess(
        db_pool=db_pool,
        redis_client=get_redis_client(),
        user={"user_id": user_id, "role": role, "strategies": list(strategies)},
    )
    service = RiskService(scoped_access)

    # Execute async service method from sync Streamlit context
    data: RiskDashboardData = run_async(service.get_risk_dashboard_data())

    # Convert to dict for caching (dataclasses aren't directly cacheable)
    return {
        "risk_metrics": data.risk_metrics,
        "factor_exposures": data.factor_exposures,
        "stress_tests": data.stress_tests,
        "var_history": data.var_history,
        "is_placeholder": data.is_placeholder,
        "placeholder_reason": data.placeholder_reason,
    }


def render_risk_overview(risk_metrics: dict[str, Any]) -> None:
    """Render risk metrics overview section.

    Args:
        risk_metrics: Dict with total_risk, factor_risk, specific_risk
    """
    st.subheader("Risk Overview")

    if not risk_metrics:
        st.info("Risk metrics not available. Position data may be loading...")
        return

    cols = st.columns(3)

    with cols[0]:
        total_risk = risk_metrics.get("total_risk")
        if total_risk is not None:
            st.metric(
                label="Total Risk (Annualized)",
                value=f"{float(total_risk):.2%}",
                help="Annualized portfolio volatility",
            )
        else:
            st.metric(label="Total Risk", value="N/A")

    with cols[1]:
        factor_risk = risk_metrics.get("factor_risk")
        if factor_risk is not None:
            st.metric(
                label="Factor Risk",
                value=f"{float(factor_risk):.2%}",
                help="Systematic risk from factor exposures",
            )
        else:
            st.metric(label="Factor Risk", value="N/A")

    with cols[2]:
        specific_risk = risk_metrics.get("specific_risk")
        if specific_risk is not None:
            st.metric(
                label="Specific Risk",
                value=f"{float(specific_risk):.2%}",
                help="Idiosyncratic risk from individual positions",
            )
        else:
            st.metric(label="Specific Risk", value="N/A")


def render_risk_dashboard(data: dict[str, Any]) -> None:
    """Render complete risk dashboard with all sections.

    Args:
        data: Dict with risk_metrics, factor_exposures, stress_tests, var_history,
              is_placeholder, placeholder_reason
    """
    # CRITICAL: Show placeholder/demo data warning prominently
    if data.get("is_placeholder", False):
        st.error(
            "⚠️ **DEMO DATA - NOT FOR TRADING DECISIONS** ⚠️\n\n"
            f"{data.get('placeholder_reason', 'Risk model artifacts not available.')}\n\n"
            "The data shown below is simulated. Contact support to enable real risk analytics.",
            icon="🚨",
        )

    # Schema validation before rendering
    if not validate_risk_metrics(data.get("risk_metrics")):
        st.warning("Risk metrics incomplete. Some data may be unavailable.")

    # Risk Overview Section
    render_risk_overview(data.get("risk_metrics", {}))

    # VaR Section
    st.divider()
    st.subheader("Value at Risk (VaR)")
    render_var_metrics(
        data.get("risk_metrics", {}),
        var_limit=RISK_BUDGET_VAR_LIMIT,
        warning_threshold=RISK_BUDGET_WARNING_THRESHOLD,
    )

    # VaR History
    var_history = data.get("var_history", [])
    if var_history:
        render_var_history(var_history, var_limit=RISK_BUDGET_VAR_LIMIT)

    # Factor Exposures Section
    st.divider()
    st.subheader("Factor Exposures")
    render_factor_exposure(data.get("factor_exposures", []))

    # Stress Tests Section
    st.divider()
    render_stress_tests(data.get("stress_tests", []))


@requires_auth
def main() -> None:
    """Main entry point for risk dashboard page."""
    st.set_page_config(page_title="Risk Analytics Dashboard", page_icon="📊", layout="wide")

    st.title("Risk Analytics Dashboard")
    st.caption("Portfolio risk metrics, factor exposures, and stress test analysis.")

    # Feature flag check
    if not FEATURE_RISK_DASHBOARD:
        st.info("Risk Analytics Dashboard is not currently enabled.")
        st.stop()

    # Get current user
    user = get_current_user()

    # Permission check
    if not has_permission(user, Permission.VIEW_PNL):
        st.error("Permission denied: VIEW_PNL permission required.")
        st.stop()

    # Strategy access check
    authorized_strategies = get_authorized_strategies(user)
    if not authorized_strategies:
        st.warning(
            "You don't have access to any strategies. Contact your administrator "
            "to be assigned to one or more strategies."
        )
        st.stop()

    # Get user_id for cache key
    user_id = user.get("user_id") if isinstance(user, dict) else getattr(user, "user_id", None)
    if not user_id:
        st.error("Session error: missing user ID. Please re-authenticate.")
        st.stop()

    # Fetch risk data
    with st.spinner("Loading risk analytics..."):
        try:
            role = user.get("role") if isinstance(user, dict) else getattr(user, "role", None)
            data = _fetch_risk_data(
                user_id=str(user_id),
                role=str(role or ""),
                strategies=tuple(sorted(authorized_strategies)),
            )
        except PermissionError as e:
            st.error(f"Access denied: {e}")
            st.stop()
        except TimeoutError:
            st.error("Request timed out. Please try again.")
            st.stop()
        except RuntimeError as e:
            st.error(f"Configuration error: {e}")
            st.stop()
        except Exception:
            logger.exception("risk_dashboard_error")
            st.error("Failed to load risk data. Please try again later.")
            st.stop()

    # Render dashboard
    render_risk_dashboard(data)


if __name__ == "__main__":
    main()
