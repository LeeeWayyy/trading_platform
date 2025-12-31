"""Notebook template selector component."""

from __future__ import annotations

import streamlit as st

from apps.web_console.services.notebook_launcher_service import NotebookTemplate


def render_notebook_template_selector(
    templates: list[NotebookTemplate],
) -> NotebookTemplate | None:
    """Render a template selector and return the chosen template."""

    st.subheader("Notebook Template")

    if not templates:
        st.info("No notebook templates are available.")
        return None

    selected = st.selectbox(
        "Template",
        options=templates,
        format_func=lambda template: template.name,
    )

    st.caption(selected.description)
    return selected


__all__ = ["render_notebook_template_selector"]
