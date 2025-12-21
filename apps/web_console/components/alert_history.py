"""Alert history table with acknowledgment support."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pandas as pd
import streamlit as st

from apps.web_console.services.alert_service import MIN_ACK_NOTE_LENGTH
from libs.alerts.models import AlertEvent

# Maximum pending acknowledgments to show in UI
MAX_PENDING_ACKS_TO_SHOW = 5


def render_alert_history(
    events: list[AlertEvent],
    can_acknowledge: bool = False,
    on_acknowledge: Callable[[str, str], Any] | None = None,
) -> None:
    """Render alert history table with acknowledgment."""

    if not events:
        st.info("No alert events recorded.")
        return

    df = pd.DataFrame(
        [
            {
                "Time": event.triggered_at.strftime("%Y-%m-%d %H:%M:%S"),
                "Rule": event.rule_name or str(event.rule_id),
                "Value": str(event.trigger_value) if event.trigger_value is not None else "N/A",
                "Channels": ", ".join(event.routed_channels),
                "Acknowledged": "Yes" if event.acknowledged_at else "No",
                "Acknowledged By": event.acknowledged_by or "-",
            }
            for event in events
        ]
    )

    st.dataframe(df, use_container_width=True)

    if can_acknowledge:
        unacked = [e for e in events if not e.acknowledged_at]
        if unacked:
            st.subheader("Pending Acknowledgments")
            for event in unacked[:MAX_PENDING_ACKS_TO_SHOW]:
                rule_label = event.rule_name or str(event.rule_id)
                with st.expander(f"Alert: {rule_label} at {event.triggered_at}"):
                    note = st.text_area(
                        "Acknowledgment Note",
                        key=f"ack_note_{event.id}",
                    )
                    if st.button("Acknowledge", key=f"ack_{event.id}"):
                        if len(note.strip()) < MIN_ACK_NOTE_LENGTH:
                            st.warning(f"Please enter at least {MIN_ACK_NOTE_LENGTH} characters.")
                        elif on_acknowledge:
                            on_acknowledge(str(event.id), note)
                            st.success("Acknowledged!")
                            st.rerun()


__all__ = ["render_alert_history"]
