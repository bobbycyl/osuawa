from typing import Any

import streamlit as st


def save_value(key: str) -> None:
    # <key> <-> st.session_state._<key>_value
    st.session_state["_%s_value" % key] = st.session_state[key]


def load_value(key: str, default_value: Any) -> Any:
    # <key> <-> st.session_state._<key>_value
    if "_%s_value" % key not in st.session_state:
        st.session_state["_%s_value" % key] = default_value
    st.session_state[key] = st.session_state["_%s_value" % key]


def memorized_multiselect(label: str, key: str, options: list, default_value: Any) -> None:
    load_value(key, default_value)
    st.multiselect(label, options, key=key, on_change=save_value, args=(key,))


def memorized_selectbox(label: str, key: str, options: list, default_value: Any) -> None:
    load_value(key, default_value)
    st.selectbox(label, options, key=key, on_change=save_value, args=(key,))
