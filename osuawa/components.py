import os.path
import shelve
from typing import Any, Optional

import streamlit as st


def save_value(key: str) -> None:
    # <key> <-> st.session_state._<key>_value
    st.session_state["_%s_value" % key] = st.session_state[key]
    # semi-persistent storage
    # ./.streamlit/.components/<ajs_anonymous_id>
    with shelve.open("./.streamlit/.components/%s" % st.context.cookies["ajs_anonymous_id"]) as db:
        db[key] = st.session_state["_%s_value" % key]


def load_value(key: str, default_value: Any) -> Any:
    # <key> <-> st.session_state._<key>_value
    if "_%s_value" % key not in st.session_state:
        if key not in st.session_state:
            # semi-persistent storage
            # ./.streamlit/.components/<ajs_anonymous_id>
            # 由于 load 可能发生在第一次访问（save 不会），所以还要检查 cookie 是否存在
            if "ajs_anonymous_id" in st.context.cookies and (
                os.path.exists("./.streamlit/.components/%s.bak" % st.context.cookies["ajs_anonymous_id"])
                or os.path.exists("./.streamlit/.components/%s.dat" % st.context.cookies["ajs_anonymous_id"])
                or os.path.exists("./.streamlit/.components/%s.dir" % st.context.cookies["ajs_anonymous_id"])
            ):
                with shelve.open("./.streamlit/.components/%s" % st.context.cookies["ajs_anonymous_id"], "r") as db:
                    st.session_state["_%s_value" % key] = db.get(key, default_value)
            else:
                st.session_state["_%s_value" % key] = default_value
        else:
            raise RuntimeError("the key of memorized component is used: %s" % key)
    st.session_state[key] = st.session_state["_%s_value" % key]


def memorized_multiselect(label: str, key: str, options: list, default_value: Any) -> None:
    load_value(key, default_value)
    st.multiselect(label, options, key=key, on_change=save_value, args=(key,))


def memorized_selectbox(label: str, key: str, options: list, default_value: Any) -> None:
    load_value(key, default_value)
    st.selectbox(label, options, key=key, on_change=save_value, args=(key,))


def init_page_layout(page_title: str, force_val: Optional[bool] = None) -> None:
    # force_val 好像要引入额外的 session_state 键值对才能做出来，暂时还是不考虑了
    # layout 部分由沉浸模式强制覆盖 CSS 暂时替代，看看效果如何
    st.set_page_config(
        # layout="wide" if st.session_state.get("wide_layout", False) else "centered",
        page_title=page_title,
    )
