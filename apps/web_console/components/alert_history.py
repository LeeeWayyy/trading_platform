"""Alert history table with acknowledgment support."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pandas as pd
import streamlit as st

from libs.alerts.models import AlertEvent
from libs.alerts.pii import mask_for_logs


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
                "Rule": event.rule_id,
                "Value": str(event.trigger_value) if event.trigger_value is not None else "N/A",
                "Channels": ", ".join(mask_for_logs(channel) for channel in event.routed_channels),
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
            for event in unacked[:5]:
                with st.expander(f"Alert: {event.rule_id} at {event.triggered_at}"):
                    note = st.text_area(
                        "Acknowledgment Note",
                        key=f"ack_note_{event.id}",
                    )
                    if st.button("Acknowledge", key=f"ack_{event.id}"):
                        if len(note.strip()) < 5:
                            st.warning("Please enter at least 5 characters.")
                        elif on_acknowledge:
                            on_acknowledge(str(event.id), note)
                            st.success("Acknowledged!")
                            st.rerun()


__all__ = ["render_alert_history"]
