import os
from time import sleep

import pandas as pd
import streamlit as st

from osuawa import Path

user = st.selectbox("select user", [os.path.splitext(os.path.basename(x))[0] for x in os.listdir(os.path.join(str(Path.OUTPUT_DIRECTORY.value), Path.RECENT_SCORES.value))])


def main():
    if not os.path.exists(os.path.join(str(Path.OUTPUT_DIRECTORY.value), Path.RECENT_SCORES.value, f"{user}.csv")):
        raise ValueError("User not found")
    df = pd.read_csv(os.path.join(str(Path.OUTPUT_DIRECTORY.value), Path.RECENT_SCORES.value, f"{user}.csv"), index_col=0, parse_dates=["ts"])
    st.link_button("user profile", f"https://osu.ppy.sh/users/{user}")
    with st.container(border=True):
        st.markdown(
            f"""## PP Overall
based on {len(df)} ({len(df[df["passed"]])} passed) score(s)

got/100/95/90/85 {df["pp"].sum():.2f}/{df["b_pp_100if"].sum():.2f}/{df["b_pp_95if"].sum():.2f}/{df["b_pp_90if"].sum():.2f}/{df["b_pp_85if"].sum():.2f}pp

| Tag         | pp%                                                              | count%                                              |
| ----------- | ---------------------------------------------------------------- | --------------------------------------------------- |
| HD          |{df[df["is_hd"]]["pp"].sum() / df["pp"].sum() * 100:.2f}          | {len(df[df["is_hd"]]) / len(df) * 100:.2f}          |
| High_AR     |{df[df["is_high_ar"]]["pp"].sum() / df["pp"].sum() * 100:.2f}     | {len(df[df["is_high_ar"]]) / len(df) * 100:.2f}     |
| Low_AR      |{df[df["is_low_ar"]]["pp"].sum() / df["pp"].sum() * 100:.2f}      | {len(df[df["is_low_ar"]]) / len(df) * 100:.2f}      |
| Very_Low_AR |{df[df["is_very_low_ar"]]["pp"].sum() / df["pp"].sum() * 100:.2f} | {len(df[df["is_very_low_ar"]]) / len(df) * 100:.2f} |
| Speed_Up    |{df[df["is_speed_up"]]["pp"].sum() / df["pp"].sum() * 100:.2f}    | {len(df[df["is_speed_up"]]) / len(df) * 100:.2f}    |
| Speed_Down  |{df[df["is_speed_down"]]["pp"].sum() / df["pp"].sum() * 100:.2f}  | {len(df[df["is_speed_down"]]) / len(df) * 100:.2f}  |

"""
        )
    with st.container(border=True):
        st.markdown("## Scatter Plot")
        begin_date, end_date = st.date_input("date range", [df["ts"].min() - pd.Timedelta(days=1), pd.Timestamp.today() + pd.Timedelta(days=1)], key="cat_date_range")
        df1 = df[(df["ts"].dt.date > begin_date) & (df["ts"].dt.date < end_date)]
        sr_slider = st.slider("star rating", 0.0, 13.5, (0.5, 8.5))
        df2 = df1[(df1["b_star_rating"] > sr_slider[0]) & (df1["b_star_rating"] < sr_slider[1])]
        advanced_filter = st.text_input("advanced filter", key="cat_advanced_filter")
        if advanced_filter:
            df3 = df2.query(advanced_filter)
        else:
            df3 = df2
            t1 = st.toast("advanced filter disabled")
        enable_size = st.checkbox("enable scatter plot size parameter", key="cat_enable_size")
        if "default_x_with_size_enabled" not in st.session_state:
            st.session_state.default_x_with_size_enabled = 25
        if "default_size" not in st.session_state:
            st.session_state.default_size = 23
        if "default_x" not in st.session_state:
            st.session_state.default_x = 23

        if enable_size:
            col1, col2 = st.columns(2)
            with col1:
                x_radio_with_size_enabled = st.radio("x", df.columns, index=st.session_state.default_x_with_size_enabled, key="cat_x_with_size_enabled")
            with col2:
                size_radio = st.radio("size", df.columns, index=st.session_state.default_size, key="cat_size")
            y_multiselect = st.multiselect("y", df.columns, default="score_nf", key="cat_y_with_size_enabled")
            st.scatter_chart(
                df3,
                x=x_radio_with_size_enabled,
                y=y_multiselect,
                size=size_radio,
            )
            st.session_state.default_x_with_size_enabled = pd.Index.get_loc(df.columns, x_radio_with_size_enabled)
            st.session_state.default_size = pd.Index.get_loc(df.columns, size_radio)
            t2 = st.toast("set default x to %s and default size to %s when size enabled" % (st.session_state.default_x_with_size_enabled, st.session_state.default_size))
            sleep(1)
            t2.empty()
        else:
            x_radio = st.radio("x", df.columns, index=st.session_state.default_x, key="cat_x")
            y_multiselect = st.multiselect("y", df.columns, default="score_nf", key="cat_y")
            st.scatter_chart(
                df3,
                x=x_radio,
                y=y_multiselect,
            )
            st.session_state.default_x = pd.Index.get_loc(df.columns, x_radio)
            t3 = st.toast("set default x to %s when size disabled" % st.session_state.default_x)
            sleep(1)
            t3.empty()
        t1.empty()

    with st.container(border=True):
        st.markdown("## filtered data")
        st.dataframe(df3, key="cat_dataframe")


main()
