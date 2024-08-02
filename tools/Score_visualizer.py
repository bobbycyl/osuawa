import os

import pandas as pd
import streamlit as st

from osuawa import Path
from osuawa.utils import memorized_multiselect, memorized_selectbox

if "wide_layout" in st.session_state:
    st.set_page_config(page_title=_("Score visualizer") + " - osuawa", layout="wide" if st.session_state.wide_layout else "centered")
else:
    st.set_page_config(page_title=_("Score visualizer") + " - osuawa")
with st.sidebar:
    st.toggle(_("wide page layout"), key="wide_layout", value=False)
user = st.selectbox(_("user"), [os.path.splitext(os.path.basename(x))[0] for x in os.listdir(os.path.join(str(Path.OUTPUT_DIRECTORY.value), Path.RECENT_SCORES.value))])


def main():
    if not os.path.exists(os.path.join(str(Path.OUTPUT_DIRECTORY.value), Path.RECENT_SCORES.value, f"{user}.csv")):
        st.error(_("user not found"))
        st.stop()
    df = pd.read_csv(os.path.join(str(Path.OUTPUT_DIRECTORY.value), Path.RECENT_SCORES.value, f"{user}.csv"), index_col=0, parse_dates=["ts"])
    if len(df) == 0:
        st.error(_("no scores found"))
        st.stop()
    dfp = df[df["passed"]]
    st.link_button(_("user profile"), f"https://osu.ppy.sh/users/{user}")
    with st.container(border=True):
        st.markdown(_("## PP Overall"))
        st.markdown(
            f"""based on {len(df)} ({len(dfp)} passed) score(s)

got/100/95/80h/80l {dfp["pp"].sum():.2f}/{dfp["b_pp_100if"].sum():.2f}/{dfp["b_pp_95if"].sum():.2f}/{dfp["b_pp_80hif"].sum():.2f}/{dfp["b_pp_80lif"].sum():.2f}pp

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
        st.markdown(_("## Scatter Plot"))
        begin_date, end_date = st.date_input(_("date range"), [df["ts"].min() - pd.Timedelta(days=1), pd.Timestamp.today() + pd.Timedelta(days=1)], key="cat_date_range")
        df1 = df[(df["ts"].dt.date > begin_date) & (df["ts"].dt.date < end_date)]
        sr_slider = st.slider(_("star rating"), 0.0, 13.5, (0.5, 8.5))
        df2 = df1[(df1["b_star_rating"] > sr_slider[0]) & (df1["b_star_rating"] < sr_slider[1])]
        advanced_filter = st.text_input(_("advanced filter"), key="cat_advanced_filter")
        if advanced_filter:
            df3 = df2.query(advanced_filter)
        else:
            df3 = df2
        enable_size = st.checkbox(_("enable scatter plot size parameter"), key="cat_enable_size")

        if enable_size:
            col1, col2 = st.columns(2)
            with col1:
                memorized_selectbox("x", "cat_x2", df, 25)
            with col2:
                memorized_selectbox("s", "cat_s", df, 23)
            memorized_multiselect("y", "cat_y2", df, ["score_nf"])
            st.scatter_chart(
                df3,
                x=st.session_state.cat_x2,
                y=st.session_state.cat_y2,
                size=st.session_state.cat_s,
            )
        else:
            memorized_selectbox("x", "cat_x", df, 23)
            memorized_multiselect("y", "cat_y", df, ["score_nf"])
            st.scatter_chart(
                df3,
                x=st.session_state.cat_x,
                y=st.session_state.cat_y,
            )

    with st.container(border=True):
        st.markdown(_("## filtered data"))
        st.dataframe(df3, key="cat_dataframe")


main()
