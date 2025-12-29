"""Dynamic notebook parameter input form."""

from __future__ import annotations

from datetime import date
from typing import Any

import streamlit as st

from apps.web_console.services.notebook_launcher_service import NotebookParameter, NotebookTemplate


def render_notebook_parameters_form(template: NotebookTemplate) -> dict[str, Any]:
    """Render parameter inputs for a template and return values."""

    st.subheader("Parameters")

    if not template.parameters:
        st.info("This template does not require parameters.")
        return {}

    values: dict[str, Any] = {}

    for param in template.parameters:
        values[param.key] = _render_param_input(template.template_id, param)

    return values


def _render_param_input(template_id: str, param: NotebookParameter) -> Any:
    key = f"notebook_param_{template_id}_{param.key}"
    label = param.label

    if param.kind == "text":
        return st.text_input(label, value=str(param.default or ""), help=param.help, key=key)
    if param.kind == "int":
        int_default = int(param.default) if param.default is not None else 0
        return st.number_input(label, value=int_default, step=1, help=param.help, key=key)
    if param.kind == "float":
        float_default = float(param.default) if param.default is not None else 0.0
        return st.number_input(label, value=float_default, step=0.01, help=param.help, key=key)
    if param.kind == "bool":
        bool_default = bool(param.default) if param.default is not None else False
        return st.checkbox(label, value=bool_default, help=param.help, key=key)
    if param.kind == "date":
        date_default: date = param.default if isinstance(param.default, date) else date.today()
        return st.date_input(label, value=date_default, key=key, help=param.help)
    if param.kind == "select":
        options = param.options or []
        if not options:
            st.warning(f"No options configured for {label}.")
            return None
        default_index = 0
        if param.default in options:
            default_index = options.index(param.default)
        return st.selectbox(label, options=options, index=default_index, help=param.help, key=key)

    st.warning(f"Unsupported parameter type: {param.kind}")
    return param.default


__all__ = ["render_notebook_parameters_form"]
