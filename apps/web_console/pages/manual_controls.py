"""Manual Trade Controls page (T6.6)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import requests
import streamlit as st

from apps.web_console.auth.permissions import Permission, has_permission
from apps.web_console.config import (
    FEATURE_MANUAL_CONTROLS,
    MFA_STEP_UP_MAX_AGE_SECONDS,
    MIN_FLATTEN_ALL_REASON_LENGTH,
)
from apps.web_console.utils.api_client import (
    ManualControlsAPIError,
    fetch_api,
    get_manual_controls_api,
    post_manual_controls_api,
)


def _user_id(user: Mapping[str, Any]) -> str:
    return str(user.get("user_id") or user.get("sub") or "unknown")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# -----------------------------------------------------------------------------
# Action helpers
# -----------------------------------------------------------------------------


def cancel_order(order_id: str, reason: str, user: Mapping[str, Any]) -> dict[str, Any]:
    body = {
        "reason": reason,
        "requested_by": _user_id(user),
        "requested_at": _now_iso(),
    }
    return post_manual_controls_api(f"/orders/{order_id}/cancel", user=user, json_body=body)


def cancel_all_orders(symbol: str, reason: str, user: Mapping[str, Any]) -> dict[str, Any]:
    body = {
        "symbol": symbol.upper(),
        "reason": reason,
        "requested_by": _user_id(user),
        "requested_at": _now_iso(),
    }
    return post_manual_controls_api("/orders/cancel-all", user=user, json_body=body)


def close_position(
    symbol: str, reason: str, qty: Decimal | None, user: Mapping[str, Any]
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "reason": reason,
        "requested_by": _user_id(user),
        "requested_at": _now_iso(),
    }
    if qty is not None:
        body["qty"] = qty
    return post_manual_controls_api(f"/positions/{symbol}/close", user=user, json_body=body)


def adjust_position(
    symbol: str,
    target_qty: Decimal,
    reason: str,
    order_type: str,
    limit_price: Decimal | None,
    user: Mapping[str, Any],
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "target_qty": target_qty,
        "reason": reason,
        "requested_by": _user_id(user),
        "requested_at": _now_iso(),
        "order_type": order_type,
    }
    if order_type == "limit":
        body["limit_price"] = limit_price
    return post_manual_controls_api(f"/positions/{symbol}/adjust", user=user, json_body=body)


def flatten_all_positions(user: Mapping[str, Any], reason: str, id_token: str) -> dict[str, Any]:
    body = {
        "reason": reason,
        "requested_by": _user_id(user),
        "requested_at": _now_iso(),
        "id_token": id_token,
    }
    return post_manual_controls_api("/positions/flatten-all", user=user, json_body=body)


# -----------------------------------------------------------------------------
# Error handling
# -----------------------------------------------------------------------------

AUTH_ERRORS_REQUIRING_LOGIN = {
    "invalid_token",
    "invalid_signature",
    "token_expired",
    "token_revoked",
    "session_expired",
    "invalid_issuer",
    "invalid_audience",
    "subject_mismatch",
}

MFA_ERRORS = {"mfa_required", "mfa_expired", "mfa_invalid", "token_mismatch"}

ERROR_MESSAGES = {
    "validation_error": "Invalid request",
    "invalid_request": "Invalid request",
    "missing_header": "Missing required header. Please refresh.",
    "invalid_header": "Invalid header format. Please refresh.",
    "invalid_token": "Invalid session. Please log in again.",
    "invalid_signature": "Invalid session. Please log in again.",
    "token_expired": "Session expired. Please log in again.",
    "token_not_valid_yet": "Session not yet valid. Please wait.",
    "token_revoked": "Session revoked. Please log in again.",
    "token_replayed": "Session error. Please refresh and retry.",
    "permission_denied": "You don't have permission for this action",
    "strategy_unauthorized": "You are not authorized for this strategy",
    "mfa_required": "MFA verification required.",
    "mfa_expired": "MFA verification expired.",
    "mfa_invalid": "MFA verification failed.",
    "token_mismatch": "MFA token doesn't match user. Please re-authenticate.",
    "not_found": "Order/position not found",
    "rate_limited": "Too many requests. Please wait and retry.",
    "broker_error": "Broker error. Please retry.",
    "broker_unavailable": "Broker unavailable. Try again later.",
    "mfa_unavailable": "MFA service unavailable. Contact admin.",
    "internal_error": "Server error. Please try again.",
    "broker_timeout": "Broker timeout. Status may be unknown.",
}


def handle_api_error(e: Exception, action: str) -> None:
    """Handle API errors with user-friendly messages."""

    if isinstance(e, requests.exceptions.ConnectionError):
        st.error("Unable to connect to server. Check your network.")
        return
    if isinstance(e, requests.exceptions.Timeout):
        st.error(f"Request timed out. {action} status may be unknown.")
        return
    if isinstance(e, ManualControlsAPIError):
        if e.status_code == 422:
            detail = e.detail
            if isinstance(detail, list) and detail:
                msg = (
                    detail[0].get("msg", "Invalid input")
                    if isinstance(detail[0], Mapping)
                    else "Invalid input"
                )
            else:
                msg = "Invalid input"
            st.error(f"Validation error: {msg}")
            return

        msg = ERROR_MESSAGES.get(e.error_code, f"Error: {e.message}")

        if e.error_code in AUTH_ERRORS_REQUIRING_LOGIN:
            st.error(msg)
            st.info("Please refresh and log in again.")
        elif e.error_code in MFA_ERRORS:
            st.warning(msg)
        elif e.error_code == "rate_limited":
            retry_after = None
            if isinstance(e.detail, Mapping):
                retry_after = e.detail.get("retry_after")
            st.warning(f"Too many requests. Wait {retry_after or 'a few'} seconds and retry.")
        elif e.error_code in {"strategy_unauthorized", "permission_denied"}:
            st.warning(msg)
        else:
            st.error(msg)
        return

    st.error(f"Unexpected error: {e}")


# -----------------------------------------------------------------------------
# UI Sections
# -----------------------------------------------------------------------------
# DESIGN DECISION: Avoid dynamic input widgets inside loops.
# We use a two-step flow: per-order buttons set session state, and a single
# stable form renders below the list. This prevents Streamlit key churn when
# the list of orders changes after cancels.
# -----------------------------------------------------------------------------


def render_pending_orders(user: Mapping[str, Any]) -> None:
    st.subheader("Pending Orders")

    try:
        data = get_manual_controls_api("/orders/pending", user)
    except Exception as exc:  # ManualControlsAPIError + network
        handle_api_error(exc, "load pending orders")
        return

    orders = data.get("orders", []) if isinstance(data, Mapping) else []
    user_strategies = data.get("user_strategies", []) if isinstance(data, Mapping) else []
    if user_strategies:
        st.caption("Showing orders for strategies: " + ", ".join(user_strategies))

    if not orders:
        st.info("No pending orders found.")
        return

    order_ids = {order.get("client_order_id") for order in orders if order.get("client_order_id")}
    symbols = sorted({order.get("symbol") for order in orders if order.get("symbol")})

    for order in orders:
        order_id = order.get("client_order_id")
        symbol = order.get("symbol")
        strategy_id = order.get("strategy_id")
        status = order.get("status")

        with st.expander(f"{symbol} ({strategy_id}) - {status}"):
            st.write(order)

            if has_permission(user, Permission.CANCEL_ORDER) and order_id:
                if st.button("Cancel Order", key=f"cancel_btn_{order_id}"):
                    st.session_state["pending_cancel_order_id"] = order_id
                    st.session_state["pending_cancel_symbol"] = symbol
                    st.rerun()
            else:
                st.caption("Permission required: CANCEL_ORDER")

    pending_order_id = st.session_state.get("pending_cancel_order_id")
    if pending_order_id and pending_order_id not in order_ids:
        st.session_state.pop("pending_cancel_order_id", None)
        st.session_state.pop("pending_cancel_symbol", None)
        pending_order_id = None

    if has_permission(user, Permission.CANCEL_ORDER) and pending_order_id:
        pending_symbol = st.session_state.get("pending_cancel_symbol", "")
        st.markdown("---")
        st.subheader(f"Cancel Order {pending_order_id}")
        reason = st.text_input(
            "Cancel reason",
            key="pending_cancel_reason",
            placeholder="Enter reason (min 10 chars)",
        )
        confirm = st.button("Confirm Cancel", key="confirm_cancel_order")
        dismiss = st.button("Dismiss", key="dismiss_cancel_order")
        if dismiss:
            st.session_state.pop("pending_cancel_order_id", None)
            st.session_state.pop("pending_cancel_symbol", None)
            st.session_state.pop("pending_cancel_reason", None)
            st.rerun()
        if confirm:
            if len(reason.strip()) < 10:
                st.error("Reason must be at least 10 characters")
            else:
                try:
                    cancel_order(pending_order_id, reason.strip(), user)
                except Exception as exc:
                    handle_api_error(exc, "cancel order")
                else:
                    st.success(f"Cancel requested for {pending_symbol or pending_order_id}")
                    st.session_state.pop("pending_cancel_order_id", None)
                    st.session_state.pop("pending_cancel_symbol", None)
                    st.session_state.pop("pending_cancel_reason", None)
                    st.rerun()

    if has_permission(user, Permission.CANCEL_ORDER) and symbols:
        st.markdown("---")
        st.subheader("Cancel All Orders For Symbol")
        cancel_symbol = st.selectbox(
            "Symbol",
            symbols,
            key="cancel_all_symbol_select",
        )
        ca_reason = st.text_input(
            "Cancel all reason",
            key="cancel_all_reason",
            placeholder="Enter reason (min 10 chars)",
        )
        if st.button("Cancel All For Symbol", key="cancel_all_confirm"):
            if len(ca_reason.strip()) < 10:
                st.error("Reason must be at least 10 characters")
            else:
                try:
                    cancel_all_orders(cancel_symbol, ca_reason.strip(), user)
                except Exception as exc:
                    handle_api_error(exc, "cancel all orders")
                else:
                    st.success(f"Cancel-all requested for {cancel_symbol}")
                    st.session_state.pop("cancel_all_reason", None)
                    st.rerun()


def render_positions(user: Mapping[str, Any]) -> None:
    st.subheader("Open Positions")

    try:
        positions_data = fetch_api("positions", user)
    except Exception as exc:
        st.error(f"Failed to load positions: {exc}")
        return

    positions = positions_data.get("positions", []) if isinstance(positions_data, Mapping) else []

    if not positions:
        st.info("No open positions.")
        return

    for pos in positions:
        symbol = pos.get("symbol")
        qty = pos.get("qty")
        with st.expander(f"{symbol} qty={qty}"):
            st.write(pos)

            if has_permission(user, Permission.CLOSE_POSITION):
                reason = st.text_input(
                    "Close reason",
                    key=f"close_reason_{symbol}",
                    placeholder="Enter reason (min 10 chars)",
                )
                partial_qty = st.number_input(
                    "Qty to close (leave 0 for full)",
                    key=f"close_qty_{symbol}",
                    min_value=0.0,
                    value=0.0,
                    step=1.0,
                    format="%.0f",
                )
                if st.button("Close Position", key=f"close_btn_{symbol}"):
                    if len(reason.strip()) < 10:
                        st.error("Reason must be at least 10 characters")
                    else:
                        qty_param = Decimal(partial_qty) if partial_qty > 0 else None
                        try:
                            close_position(symbol, reason.strip(), qty_param, user)
                        except Exception as exc:
                            handle_api_error(exc, "close position")
                        else:
                            st.success(f"Close requested for {symbol}")
                            st.rerun()
            else:
                st.caption("Permission required: CLOSE_POSITION")

            if has_permission(user, Permission.ADJUST_POSITION):
                target_qty = st.number_input(
                    "Target quantity (can be negative)",
                    key=f"adjust_target_{symbol}",
                    value=float(qty) if qty is not None else 0.0,
                    step=1.0,
                    format="%.0f",
                )
                order_type = st.selectbox(
                    "Order type",
                    ["market", "limit"],
                    key=f"adjust_type_{symbol}",
                )
                limit_price = None
                if order_type == "limit":
                    limit_price = Decimal(
                        st.number_input(
                            "Limit price",
                            key=f"adjust_limit_{symbol}",
                            min_value=0.01,
                            value=0.01,
                            step=0.01,
                            format="%.2f",
                        )
                    )
                adj_reason = st.text_input(
                    "Adjust reason",
                    key=f"adjust_reason_{symbol}",
                    placeholder="Enter reason (min 10 chars)",
                )
                if st.button("Adjust Position", key=f"adjust_btn_{symbol}"):
                    if len(adj_reason.strip()) < 10:
                        st.error("Reason must be at least 10 characters")
                    else:
                        try:
                            adjust_position(
                                symbol,
                                Decimal(target_qty),
                                adj_reason.strip(),
                                order_type,
                                limit_price,
                                user,
                            )
                        except Exception as exc:
                            handle_api_error(exc, "adjust position")
                        else:
                            st.success(f"Adjust requested for {symbol}")
                            st.rerun()
            else:
                st.caption("Permission required: ADJUST_POSITION")


def _is_mfa_token_valid() -> bool:
    """Check if MFA step-up token exists and is not expired."""
    id_token = st.session_state.get("step_up_id_token")
    if not id_token:
        return False

    issued_at = st.session_state.get("step_up_id_token_issued_at")
    if not issued_at:
        # Token without timestamp - treat as expired for security
        return False

    try:
        issued_dt = datetime.fromisoformat(issued_at)
        age_seconds = (datetime.now(UTC) - issued_dt).total_seconds()
        # Require non-negative age (reject future timestamps) and within max age
        return 0 <= age_seconds < MFA_STEP_UP_MAX_AGE_SECONDS
    except (ValueError, TypeError):
        return False


def _clear_mfa_token() -> None:
    """Clear MFA step-up token from session state."""
    st.session_state.pop("step_up_id_token", None)
    st.session_state.pop("step_up_id_token_issued_at", None)


def render_flatten_all(user: Mapping[str, Any]) -> None:
    st.subheader("Emergency Actions")
    st.caption("Flatten all positions across authorized strategies (requires MFA)")

    if not has_permission(user, Permission.FLATTEN_ALL):
        st.info("Permission required: FLATTEN_ALL")
        return

    reason = st.text_area(
        "Reason",
        key="flatten_all_reason",
        placeholder=f"Enter reason (min {MIN_FLATTEN_ALL_REASON_LENGTH} chars)",
    )

    # Check MFA token validity (exists and not expired)
    mfa_valid = _is_mfa_token_valid()
    id_token = st.session_state.get("step_up_id_token") if mfa_valid else None

    if not mfa_valid:
        # Clear any expired token
        _clear_mfa_token()
        st.warning("Step-up MFA required. Please complete MFA to proceed.")
        if st.button("Authenticate with MFA", key="mfa_auth_btn"):
            # Trigger step-up auth flow - redirect to MFA page or set session flag
            st.session_state["mfa_step_up_requested"] = True
            st.info("Redirecting to MFA authentication...")
            st.rerun()

    if st.button("Flatten All", type="primary"):
        if len(reason.strip()) < MIN_FLATTEN_ALL_REASON_LENGTH:
            st.error(f"Reason must be at least {MIN_FLATTEN_ALL_REASON_LENGTH} characters")
            return
        if not id_token:
            st.error("MFA token missing or expired. Please complete step-up authentication.")
            return
        try:
            flatten_all_positions(user, reason.strip(), id_token)
        except Exception as exc:
            # Clear MFA token on error to force re-authentication
            _clear_mfa_token()
            handle_api_error(exc, "flatten all")
        else:
            # Clear MFA token after successful use (single-use)
            _clear_mfa_token()
            st.success("Flatten-all requested")
            st.rerun()


# -----------------------------------------------------------------------------
# Entry point
# -----------------------------------------------------------------------------


def render_manual_controls(
    user: Mapping[str, Any], db_pool: Any, audit_logger: Any
) -> None:  # noqa: ARG001
    """Render manual controls page."""

    if not FEATURE_MANUAL_CONTROLS:
        st.info("Manual controls feature is disabled.")
        return

    if not has_permission(user, Permission.VIEW_TRADES):
        st.error("Permission denied: VIEW_TRADES required")
        st.stop()

    st.title("Manual Trade Controls")

    render_pending_orders(user)
    st.divider()
    render_positions(user)
    st.divider()
    render_flatten_all(user)


__all__ = [
    "render_manual_controls",
    "handle_api_error",
    "cancel_order",
    "cancel_all_orders",
    "close_position",
    "adjust_position",
    "flatten_all_positions",
]
