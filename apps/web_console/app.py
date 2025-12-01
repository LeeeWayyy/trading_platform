"""
Trading Platform Web Console - Main Application.

Streamlit-based web UI for operational oversight and manual intervention.
Provides dashboard, manual order entry, strategy controls, kill switch,
and audit log viewer for non-technical operators.

Key Features:
- Real-time dashboard with positions, P&L, and strategy status
- Manual order entry with two-step confirmation
- Strategy enable/disable controls
- Emergency kill switch integration
- Audit log viewer with filtering
- Authentication and session management

Usage:
    $ streamlit run apps/web_console/app.py --server.port 8501

Environment Variables:
    EXECUTION_GATEWAY_URL: Execution gateway base URL (default: http://localhost:8002)
    WEB_CONSOLE_AUTH_TYPE: Authentication type (dev, basic, oauth2)
    DATABASE_URL: PostgreSQL connection string
"""

import hashlib
import logging
import time
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

import requests
import streamlit as st
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

if TYPE_CHECKING:
    import psycopg_pool

from apps.web_console import auth, config
from apps.web_console.auth.streamlit_helpers import requires_auth
from apps.web_console.components.session_status import render_session_status

logger = logging.getLogger(__name__)

# M7 Fix: Use st.cache_resource for connection pool to persist across Streamlit reruns
# Streamlit re-executes the script on every interaction, so global variables reset.
# @st.cache_resource ensures the pool is created once and shared across all reruns/sessions.


@st.cache_resource(ttl=300)  # TTL=5min to recover from transient DB issues
def _get_db_pool() -> "psycopg_pool.ConnectionPool | None":
    """
    Get or initialize the database connection pool.

    M7 Fix: Uses @st.cache_resource to persist the pool across Streamlit reruns.
    This is critical because Streamlit re-executes the entire script on every
    user interaction - without caching, a new pool would be created each time.

    Returns:
        ConnectionPool instance or None if initialization failed
    """
    try:
        import psycopg_pool

        logger.info(
            f"Initializing database connection pool "
            f"(min={config.DB_POOL_MIN_SIZE}, max={config.DB_POOL_MAX_SIZE})"
        )

        pool = psycopg_pool.ConnectionPool(
            config.DATABASE_URL,
            min_size=config.DB_POOL_MIN_SIZE,
            max_size=config.DB_POOL_MAX_SIZE,
            timeout=config.DB_POOL_TIMEOUT,
            # Open pool immediately to verify connection works
            open=True,
        )

        logger.info("Database connection pool initialized successfully")
        return pool

    except ImportError:
        logger.warning(
            "psycopg_pool not installed - falling back to per-connection mode"
        )
        return None
    except Exception as e:
        logger.warning(
            f"Failed to initialize connection pool: {e} - "
            "falling back to per-connection mode"
        )
        return None


# ============================================================================
# Page Configuration
# ============================================================================

st.set_page_config(
    page_title=config.PAGE_TITLE,
    page_icon=config.PAGE_ICON,
    layout=config.LAYOUT,
    initial_sidebar_state="expanded",
)

# ============================================================================
# Helper Functions
# ============================================================================


def _get_api_session() -> requests.Session:
    """
    Get or create a requests session with retry logic for current Streamlit session.

    Each Streamlit user session gets its own requests.Session to avoid
    thread-safety issues with shared connection pools and cookies.

    Retry strategy:
    - 3 retries on connection errors, timeouts, and 5xx errors
    - Exponential backoff: 0.5s, 1s, 2s
    - No retry on 4xx client errors

    Returns:
        requests.Session: Configured session with retry adapter
    """
    # Check if session already exists for this Streamlit session
    if "api_session" not in st.session_state:
        session = requests.Session()

        # Configure retry strategy
        retry_strategy = Retry(
            total=3,  # Total number of retries
            backoff_factor=0.5,  # Exponential backoff: 0.5s, 1s, 2s
            status_forcelist=[500, 502, 503, 504],  # Retry on these HTTP status codes
            allowed_methods=[
                "GET",
                "POST",
                "DELETE",
            ],  # CRITICAL: Assumes POST endpoints are idempotent (via client_order_id)
            raise_on_status=False,  # Don't raise on retry exhaustion
        )

        # Mount adapter with retry strategy
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)

        st.session_state["api_session"] = session

    return cast(requests.Session, st.session_state["api_session"])


def fetch_api(
    endpoint: str, method: str = "GET", data: dict[str, Any] | None = None
) -> dict[str, Any]:
    """
    Fetch data from execution gateway API with retry logic.

    Automatically retries on transient failures (connection errors, timeouts,
    5xx server errors) with exponential backoff. Does NOT retry 4xx client errors.

    Each Streamlit user session gets its own requests.Session for thread safety.

    Args:
        endpoint: API endpoint name (from config.ENDPOINTS)
        method: HTTP method (GET, POST, DELETE)
        data: Request body for POST requests

    Returns:
        dict: API response JSON

    Raises:
        Exception: If API request fails after retries
    """
    url = config.ENDPOINTS[endpoint]
    session = _get_api_session()

    try:
        if method == "GET":
            response = session.get(url, timeout=config.API_REQUEST_TIMEOUT)
        elif method == "POST":
            response = session.post(url, json=data, timeout=config.API_REQUEST_TIMEOUT)
        elif method == "DELETE":
            response = session.delete(url, timeout=config.API_REQUEST_TIMEOUT)
        else:
            raise ValueError(f"Unsupported HTTP method: {method}")

        response.raise_for_status()
        return cast(dict[str, Any], response.json())
    except requests.exceptions.RequestException as e:
        st.error(f"API Error: {endpoint} - {str(e)}")
        raise


@st.cache_data(ttl=config.AUTO_REFRESH_INTERVAL)
def fetch_positions() -> dict[str, Any]:
    """Fetch current positions with caching."""
    return fetch_api("positions")


@st.cache_data(ttl=config.AUTO_REFRESH_INTERVAL)
def fetch_realtime_pnl() -> dict[str, Any]:
    """Fetch real-time P&L with caching."""
    return fetch_api("pnl_realtime")


def auto_refresh_loop() -> None:
    """
    Auto-refresh loop to keep data current.

    Uses st.rerun() with a timer to refresh the dashboard every AUTO_REFRESH_INTERVAL seconds.
    Streamlit will re-execute the entire script, triggering cache refresh for stale data.

    Implementation note:
        @st.cache_data(ttl=X) alone does NOT auto-refresh the dashboard - it only prevents
        redundant API calls within the TTL window. Without st.rerun(), cached data would
        become stale after TTL expires but the UI would not update until user interaction.
        This loop ensures the dashboard refreshes automatically every X seconds.
    """
    time.sleep(config.AUTO_REFRESH_INTERVAL)
    st.rerun()


def fetch_kill_switch_status() -> dict[str, Any]:
    """
    Fetch kill switch status WITHOUT caching.

    Kill switch state is safety-critical and must always be real-time.
    Caching could show stale data for up to 10s after state change.

    Raises:
        Exception: If API request fails (network error, timeout, HTTP error)
    """
    return fetch_api("kill_switch_status")


def fetch_gateway_config() -> dict[str, Any]:
    """
    Fetch gateway configuration WITHOUT caching.

    Configuration changes (dry_run, environment) are safety-critical.
    """
    return fetch_api("config")


def audit_log(action: str, details: dict[str, Any], reason: str | None = None) -> None:
    """
    Log manual action to audit trail.

    Args:
        action: Action type (manual_order, kill_switch_engage, etc.)
        details: Action-specific details
        reason: User-provided reason/justification

    Notes:
        Delegates to auth.audit_to_database for centralized audit logging.
    """
    user_info = auth.get_current_user()
    auth.audit_to_database(
        user_id=user_info["username"],
        action=action,
        details=details,
        reason=reason,
        session_id=user_info["session_id"],
    )


# ============================================================================
# Component 1: Dashboard
# ============================================================================


def render_dashboard() -> None:
    """Render main dashboard with positions, P&L, and system status."""
    st.header("Dashboard")

    # Fetch data
    try:
        pnl_data = fetch_realtime_pnl()
        kill_switch_status = fetch_kill_switch_status()
        gateway_config = fetch_gateway_config()
    except Exception as e:
        st.error(f"Failed to load dashboard data: {str(e)}")
        return

    # System status banner
    if kill_switch_status["state"] == "ENGAGED":
        st.error(
            f"🔴 **KILL SWITCH ENGAGED** - All trading halted by {kill_switch_status.get('engaged_by', 'unknown')}"
        )
    elif gateway_config.get("dry_run"):
        st.warning("⚠️ **DRY RUN MODE** - Orders will not be submitted to broker")
    else:
        st.success("✅ **LIVE TRADING MODE** - Orders will be submitted to broker")

    # P&L Summary
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric(
            "Total Positions",
            pnl_data.get("total_positions", 0),
        )
    with col2:
        unrealized_pnl = pnl_data.get("total_unrealized_pl", 0)
        st.metric(
            "Unrealized P&L",
            f"${float(unrealized_pnl):,.2f}",
            delta=(
                f"{float(pnl_data.get('total_unrealized_pl_pct', 0)):.2f}%"
                if pnl_data.get("total_unrealized_pl_pct")
                else None
            ),
        )
    with col3:
        realtime_count = pnl_data.get("realtime_prices_available", 0)
        total_positions = pnl_data.get("total_positions", 0)
        st.metric(
            "Real-time Prices",
            f"{realtime_count}/{total_positions}",
        )
    with col4:
        last_update = pnl_data.get("timestamp", "unknown")
        try:
            # Parse RFC3339 timestamps (Python 3.11+ supports Z suffix)
            if isinstance(last_update, str) and last_update != "unknown":
                parsed_time = datetime.fromisoformat(last_update)
                display_time = parsed_time.strftime("%H:%M:%S")
            else:
                display_time = "N/A"
        except (ValueError, AttributeError):
            display_time = "Error"
        st.metric("Last Update", display_time)

    # Positions Table
    st.subheader("Current Positions")
    positions = pnl_data.get("positions", [])

    if not positions:
        st.info("No open positions")
    else:
        # Format positions for display
        table_data = []
        for pos in positions:
            table_data.append(
                {
                    "Symbol": pos["symbol"],
                    "Qty": pos["qty"],
                    "Entry Price": f"${float(pos['avg_entry_price']):,.2f}",
                    "Current Price": f"${float(pos['current_price']):,.2f}",
                    "Unrealized P&L": f"${float(pos['unrealized_pl']):,.2f}",
                    "P&L %": f"{float(pos['unrealized_pl_pct']):.2f}%",
                    "Price Source": pos["price_source"],
                }
            )
        st.dataframe(table_data, use_container_width=True)

    # Strategy Status (Placeholder - backend API not yet implemented)
    st.subheader("Strategy Status")
    st.info(
        "⚠️ **Strategy management backend pending**\n\n"
        "This section will display active strategies, last signal times, and enable/disable toggles "
        "once the strategy management API is implemented in the execution gateway.\n\n"
        "**Planned features:**\n"
        "- List of all configured strategies\n"
        "- Active/inactive status with toggle controls\n"
        "- Last signal generation time\n"
        "- Performance metrics per strategy"
    )


# ============================================================================
# Component 2: Manual Order Entry
# ============================================================================


def render_manual_order_entry() -> None:
    """Render manual order entry form with two-step confirmation."""
    st.header("Manual Order Entry")

    # Initialize confirmation state (dict-style access for test compatibility)
    if "order_confirmation_pending" not in st.session_state:
        st.session_state["order_confirmation_pending"] = False
        st.session_state["order_preview"] = None

    # Step 1: Order Entry Form
    if not st.session_state.get("order_confirmation_pending", False):
        with st.form("order_entry_form"):
            st.subheader("Order Details")

            col1, col2 = st.columns(2)
            with col1:
                symbol = st.text_input(
                    "Symbol", placeholder="AAPL", help="Stock symbol (e.g., AAPL, MSFT)"
                )
                side = st.selectbox("Side", ["buy", "sell"])
                qty = st.number_input("Quantity", min_value=1, value=10, step=1)

            with col2:
                order_type = st.selectbox("Order Type", ["market", "limit"])
                limit_price = None
                if order_type == "limit":
                    limit_price = st.number_input(
                        "Limit Price", min_value=0.01, value=100.00, step=0.01, format="%.2f"
                    )

            reason = st.text_area(
                "Reason (Required)",
                placeholder="Enter reason for manual order (e.g., 'Closing position due to news event')",
                help="Justification is required for audit trail",
            )

            submit = st.form_submit_button("Preview Order", type="primary")

            if submit:
                # Validation
                if not symbol:
                    st.error("Symbol is required")
                elif not reason or len(reason.strip()) < config.MIN_REASON_LENGTH:
                    st.error(f"Reason must be at least {config.MIN_REASON_LENGTH} characters")
                else:
                    # Check kill switch (fresh, uncached)
                    try:
                        kill_switch = fetch_kill_switch_status()
                    except Exception as e:
                        st.error(f"Failed to check kill switch status: {str(e)}")
                        return
                    if kill_switch.get("state") == "ENGAGED":
                        st.error(
                            "🛑 Kill Switch is ENGAGED - Manual orders are blocked.\n\n"
                            f"Engaged by: {kill_switch.get('engaged_by', 'unknown')}\n\n"
                            f"Reason: {kill_switch.get('engagement_reason', 'N/A')}"
                        )
                    else:
                        # CRITICAL FIX (Codex High #2 - Iteration 3):
                        # Generate client_order_id ONCE during Preview step and store in session_state.
                        # This ensures idempotency: retries/double-clicks reuse the SAME ID,
                        # preventing duplicate orders.
                        #
                        # Use UUID nonce (not session_id) to allow multiple identical manual orders
                        # in same session/day. UUID is generated once per Preview and reused on Confirm,
                        # so retries get same ID but new previews get new IDs.
                        # LOW FIX (Gemini Low #1 - Iteration 4):
                        # Removed redundant imports (hashlib, uuid already imported at top)
                        date_str = datetime.now(UTC).strftime("%Y-%m-%d")
                        limit_price_str = str(limit_price) if limit_price else ""
                        preview_nonce = str(uuid.uuid4())  # Unique per preview, stable for retries
                        id_components = (
                            symbol.upper()
                            + side
                            + str(qty)
                            + limit_price_str
                            + "manual"
                            + date_str
                            + preview_nonce  # Unique per preview (allows repeat orders)
                        )
                        client_order_id = hashlib.sha256(id_components.encode()).hexdigest()[:24]

                        # Store order preview with pre-generated ID
                        st.session_state["order_preview"] = {
                            "symbol": symbol.upper(),
                            "side": side,
                            "qty": qty,
                            "order_type": order_type,
                            "limit_price": limit_price,
                            "reason": reason.strip(),
                            "client_order_id": client_order_id,  # Store for reuse on Confirm
                        }
                        st.session_state["order_confirmation_pending"] = True
                        st.rerun()

    # Step 2: Confirmation
    else:
        order = st.session_state.get("order_preview", {})

        st.subheader("⚠️ Confirm Order")
        st.warning("**Please review order details carefully before confirming**")

        # Display order summary
        col1, col2 = st.columns(2)
        with col1:
            st.markdown(f"**Symbol:** {order['symbol']}")
            st.markdown(f"**Side:** {order['side'].upper()}")
            st.markdown(f"**Quantity:** {order['qty']}")
        with col2:
            st.markdown(f"**Type:** {order['order_type']}")
            if order["limit_price"]:
                st.markdown(f"**Limit Price:** ${order['limit_price']:.2f}")
            st.markdown(f"**Reason:** {order['reason']}")

        col1, col2 = st.columns(2)
        with col1:
            if st.button("✅ Confirm & Submit", type="primary", use_container_width=True):
                # Final kill switch check (fresh, right before submission)
                try:
                    kill_switch = fetch_kill_switch_status()
                except Exception as e:
                    st.error(f"Failed to check kill switch status: {str(e)}")
                    # Clear confirmation state
                    st.session_state["order_confirmation_pending"] = False
                    st.session_state["order_preview"] = None
                    st.rerun()
                    return
                if kill_switch.get("state") == "ENGAGED":
                    st.error(
                        "🛑 Kill Switch is ENGAGED - Cannot submit order.\n\n"
                        f"Engaged by: {kill_switch.get('engaged_by', 'unknown')}\n\n"
                        f"Reason: {kill_switch.get('engagement_reason', 'N/A')}"
                    )
                    # Clear confirmation state and show toast
                    st.session_state["order_confirmation_pending"] = False
                    st.session_state["order_preview"] = None
                    st.toast("Order submission failed: Kill Switch is engaged.", icon="🛑")
                    st.rerun()
                    return

                # Submit order
                try:
                    # CRITICAL FIX (Gemini Critical #2, Codex Critical #1):
                    # Reuse client_order_id from Preview step (stored in order_preview).
                    # This ensures idempotency: retries/double-clicks use SAME ID,
                    # preventing duplicate orders. DO NOT regenerate ID here!
                    client_order_id = order.get("client_order_id")
                    if not client_order_id:
                        st.error("Internal error: client_order_id missing from preview")
                        st.session_state["order_confirmation_pending"] = False
                        st.session_state["order_preview"] = None
                        st.rerun()
                        return

                    order_request = {
                        "symbol": order["symbol"],
                        "side": order["side"],
                        "qty": order["qty"],
                        "order_type": order["order_type"],
                        "client_order_id": client_order_id,  # Reuse pre-generated ID
                    }
                    if order["limit_price"]:
                        order_request["limit_price"] = order[
                            "limit_price"
                        ]  # Keep as numeric, not string

                    response = fetch_api("submit_order", method="POST", data=order_request)

                    # Audit log
                    audit_log(
                        action="manual_order",
                        details={
                            "client_order_id": response.get("client_order_id"),
                            **order_request,
                        },
                        reason=order["reason"],
                    )

                    # Clear confirmation state and show toast
                    st.session_state["order_confirmation_pending"] = False
                    st.session_state["order_preview"] = None
                    st.toast("✅ Order submitted successfully!")
                    st.rerun()

                except Exception as e:
                    st.error(f"Order submission failed: {str(e)}")
                    audit_log(
                        action="manual_order_failed",
                        details={"error": str(e), **order_request},
                        reason=order["reason"],
                    )

        with col2:
            if st.button("❌ Cancel", use_container_width=True):
                st.session_state["order_confirmation_pending"] = False
                st.session_state["order_preview"] = None
                st.rerun()


# ============================================================================
# Component 4: Kill Switch
# ============================================================================


def render_kill_switch() -> None:
    """Render kill switch controls."""
    st.header("Emergency Kill Switch")

    try:
        status = fetch_kill_switch_status()
    except Exception as e:
        st.error(f"Failed to fetch kill switch status: {str(e)}")
        return

    current_state = status.get("state", "unknown")

    # Status display
    if current_state == "ENGAGED":
        st.error(
            "🔴 **KILL SWITCH ENGAGED**\n\n"
            f"All trading is halted.\n\n"
            f"**Engaged by:** {status.get('engaged_by', 'unknown')}\n\n"
            f"**Reason:** {status.get('engagement_reason', 'N/A')}\n\n"
            f"**Engaged at:** {status.get('engaged_at', 'unknown')}"
        )
    else:
        st.success("✅ **KILL SWITCH ACTIVE** - Trading allowed")

    st.divider()

    # Controls
    if current_state == "ENGAGED":
        # Disengage button
        st.subheader("Disengage Kill Switch")
        st.warning("This will resume normal trading operations")

        with st.form("disengage_form"):
            operator = auth.get_current_user()["username"]
            notes = st.text_area(
                "Notes (Required)",
                placeholder="Enter reason for disengaging kill switch (e.g., 'Issue resolved, resuming trading')",
            )
            disengage = st.form_submit_button("🟢 Disengage Kill Switch", type="primary")

            if disengage:
                if not notes or len(notes.strip()) < config.MIN_REASON_LENGTH:
                    st.error(f"Notes must be at least {config.MIN_REASON_LENGTH} characters")
                else:
                    try:
                        fetch_api(
                            "kill_switch_disengage",
                            method="POST",
                            data={"operator": operator, "notes": notes.strip()},
                        )

                        # Audit log
                        audit_log(
                            action="kill_switch_disengage",
                            details={"operator": operator},
                            reason=notes.strip(),
                        )

                        st.toast("✅ Kill switch disengaged successfully!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Failed to disengage kill switch: {str(e)}")
    else:
        # Engage button
        st.subheader("Engage Kill Switch")
        st.error("⚠️ **WARNING:** This will immediately halt ALL trading")

        with st.form("engage_form"):
            operator = auth.get_current_user()["username"]
            reason = st.text_area(
                "Reason (Required)",
                placeholder="Enter reason for engaging kill switch (e.g., 'Market anomaly detected, halting for investigation')",
            )
            engage = st.form_submit_button("🔴 ENGAGE KILL SWITCH", type="primary")

            if engage:
                if not reason or len(reason.strip()) < config.MIN_REASON_LENGTH:
                    st.error(f"Reason must be at least {config.MIN_REASON_LENGTH} characters")
                else:
                    try:
                        fetch_api(
                            "kill_switch_engage",
                            method="POST",
                            data={"operator": operator, "reason": reason.strip()},
                        )

                        # Audit log
                        audit_log(
                            action="kill_switch_engage",
                            details={"operator": operator},
                            reason=reason.strip(),
                        )

                        st.toast("🔴 Kill switch engaged successfully!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Failed to engage kill switch: {str(e)}")


# ============================================================================
# Component 5: Audit Log Viewer (Placeholder)
# ============================================================================


def render_audit_log() -> None:
    """
    Render audit log viewer with database integration.

    M7 Fix: Uses connection pooling for efficient database access.
    Falls back to per-connection mode if pool initialization fails.
    """
    st.header("Audit Log")

    st.success(
        "✅ **Audit log database integration active**\n\n"
        "All manual actions, authentication events, and kill switch operations are "
        "persisted to the `audit_log` database table with IP address tracking."
    )

    st.info(
        "**Future enhancements (post-MVP):**\n"
        "- Filter by date range, action type, user\n"
        "- Search by keywords\n"
        "- Export to CSV\n"
        "- Pagination for large datasets"
    )

    # Fetch recent audit log entries from database
    st.subheader(f"Recent Actions (Last {config.AUDIT_LOG_DISPLAY_LIMIT})")

    try:
        import psycopg

        rows: list[tuple[Any, ...]] = []

        # M7 Fix: Try to use connection pool first
        pool = _get_db_pool()
        if pool is not None:
            # Use pooled connection
            with pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT timestamp, user_id, action, details::text, reason, ip_address
                        FROM audit_log
                        ORDER BY timestamp DESC
                        LIMIT %s
                        """,
                        (config.AUDIT_LOG_DISPLAY_LIMIT,),
                    )
                    rows = cur.fetchall()
        else:
            # Fallback: New connection per render (graceful degradation)
            with psycopg.connect(
                config.DATABASE_URL, connect_timeout=config.DATABASE_CONNECT_TIMEOUT
            ) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT timestamp, user_id, action, details::text, reason, ip_address
                        FROM audit_log
                        ORDER BY timestamp DESC
                        LIMIT %s
                        """,
                        (config.AUDIT_LOG_DISPLAY_LIMIT,),
                    )
                    rows = cur.fetchall()

        if rows:
            audit_data = []
            for row in rows:
                # Guard against NULL details (Codex review feedback)
                details_str = row[3] if row[3] is not None else ""
                audit_data.append(
                    {
                        "Timestamp": row[0].strftime("%Y-%m-%d %H:%M:%S") if row[0] else "N/A",
                        "User": row[1],
                        "Action": row[2],
                        "Details": (
                            details_str[: config.AUDIT_LOG_DETAILS_TRUNCATE_LENGTH - 3] + "..."
                            if len(details_str) > config.AUDIT_LOG_DETAILS_TRUNCATE_LENGTH
                            else details_str
                        )
                        or "N/A",
                        "Reason": row[4] or "N/A",
                        "IP": row[5] or "N/A",
                    }
                )
            st.table(audit_data)
        else:
            st.info("No audit log entries yet. Take some actions to populate the audit trail!")

    except ModuleNotFoundError:
        st.warning(
            "psycopg module not installed - cannot fetch audit log from database.\n\n"
            "Audit events are still being logged to console."
        )
    except psycopg.Error as e:
        st.warning(
            f"Database error while fetching audit log: {str(e)}\n\n"
            "Audit events are still being logged (check console logs for fallback)."
        )


# ============================================================================
# Main Application
# ============================================================================


@requires_auth  # Component 4: OAuth2 protected page decorator
def main() -> None:
    """Main application entry point."""
    # Component 4 CRITICAL FIX (Codex Critical #1 - Iteration 3):
    # Token refresh is handled automatically by Component 3's idle_timeout_monitor.py
    # via background monitoring. No manual start needed in Component 4.
    # Removed broken import of non-existent start_token_refresh_monitor().

    # Sidebar
    with st.sidebar:
        st.title("Navigation")

        # User info
        user_info = auth.get_current_user()
        st.markdown(f"**User:** {user_info['username']}")
        st.markdown(f"**Auth:** {user_info['auth_method']}")

        # Component 4 Deliverable 3: Session status UI with idle timeout warnings
        # CRITICAL FIX (Codex Critical #1 - Iteration 4):
        # Use user_info["auth_method"], not undefined variable auth_method
        if user_info["auth_method"] == "oauth2":
            render_session_status()
            st.divider()

        # Component 4 Deliverable 4: Logout with confirmation
        if "logout_confirmation_pending" not in st.session_state:
            st.session_state["logout_confirmation_pending"] = False

        if st.session_state.get("logout_confirmation_pending", False):
            st.warning("⚠️ **Confirm Logout**")
            col1, col2 = st.columns(2)
            with col1:
                if st.button("Yes, Logout", type="primary", use_container_width=True):
                    st.session_state["logout_confirmation_pending"] = False
                    auth.logout()
            with col2:
                if st.button("Cancel", use_container_width=True):
                    st.session_state["logout_confirmation_pending"] = False
                    st.rerun()
        else:
            if st.button("Logout", use_container_width=True):
                st.session_state["logout_confirmation_pending"] = True
                st.rerun()

        st.divider()

        # Navigation
        page = st.radio(
            "Select Page",
            ["Dashboard", "Manual Order Entry", "Kill Switch", "Audit Log"],
            label_visibility="collapsed",
        )

        st.divider()

        # System info
        st.markdown("**System Info**")
        try:
            gateway_config = fetch_gateway_config()
            st.markdown(f"Mode: {gateway_config.get('environment', 'unknown')}")
            st.markdown(f"Dry Run: {'✅' if gateway_config.get('dry_run') else '❌'}")
        except requests.exceptions.RequestException:
            st.markdown("⚠️ Gateway unreachable")

        # Auto-refresh
        st.markdown(f"\n\n*Auto-refresh: {config.AUTO_REFRESH_INTERVAL}s*")

    # Main content
    if page == "Dashboard":
        render_dashboard()
        # Auto-refresh for dashboard only
        auto_refresh_loop()
    elif page == "Manual Order Entry":
        render_manual_order_entry()
    elif page == "Kill Switch":
        render_kill_switch()
    elif page == "Audit Log":
        render_audit_log()


if __name__ == "__main__":
    main()
