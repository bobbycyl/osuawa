import os
from typing import Optional

import numpy as np
import pandas as pd
import streamlit as st
from plotly import figure_factory as ff

from osuawa import Path
from osuawa.utils import calc_bin_size, memorized_multiselect, memorized_selectbox

if "wide_layout" in st.session_state:
    st.set_page_config(page_title=_("Score visualizer") + " - osuawa", layout="wide" if st.session_state.wide_layout else "centered")
else:
    st.set_page_config(page_title=_("Score visualizer") + " - osuawa")
with st.sidebar:
    st.toggle(_("wide page layout"), key="wide_layout", value=False)
user = st.selectbox(_("user"), [os.path.splitext(os.path.basename(x))[0] for x in os.listdir(os.path.join(str(Path.OUTPUT_DIRECTORY.value), Path.RECENT_SCORES.value))])


def calc_pp_overall_main(df: pd.DataFrame, tag: Optional[str] = None) -> str:
    if tag is None:
        df_tag = df
    else:
        df_tag = df[df[tag]]
    return "%.2f (%.2f%%)" % (df_tag["pp"].sum(), df_tag["pp"].sum() / df["pp"].sum() * 100)


def calc_pp_overall_if(df: pd.DataFrame, tag: Optional[str] = None) -> str:
    if tag is None:
        df_tag = df
    else:
        df_tag = df[df[tag]]
    got_pp = df_tag["pp"].sum()
    pp_100if = df_tag["b_pp_100if"].sum()
    pp_92if = df_tag["b_pp_92if"].sum()
    pp_85if = df_tag["b_pp_85if"].sum()
    pp_67if = df_tag["b_pp_67if"].sum()
    return "%.2f%%/%.2f%%/%.2f%%/%.2f%%" % (got_pp / pp_100if * 100, got_pp / pp_92if * 100, got_pp / pp_85if * 100, got_pp / pp_67if * 100)


def calc_pp_overall_count(df: pd.DataFrame, tag: Optional[str] = None) -> str:
    if tag is None:
        df_tag = df
    else:
        df_tag = df[df[tag]]
    return "%d (%.2f%%)" % (len(df_tag), len(df_tag) / len(df) * 100)


def main():
    np.seterr(divide="ignore")
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

got/100/92/85/67 {dfp["pp"].sum():.2f}/{dfp["b_pp_100if"].sum():.2f}/{dfp["b_pp_92if"].sum():.2f}/{dfp["b_pp_85if"].sum():.2f}/{dfp["b_pp_67if"].sum():.2f}pp

| tag         | got (passed)                                  | if (passed)                                 | count (total)                                 |
| ----------- | --------------------------------------------- | ------------------------------------------- | --------------------------------------------- |
| HD          | {calc_pp_overall_main(dfp, "is_hd")}          | {calc_pp_overall_if(dfp, "is_hd")}          | {calc_pp_overall_count(df, "is_hd")}          |
| High_AR     | {calc_pp_overall_main(dfp, "is_high_ar")}     | {calc_pp_overall_if(dfp, "is_high_ar")}     | {calc_pp_overall_count(df, "is_high_ar")}     |
| Low_AR      | {calc_pp_overall_main(dfp, "is_low_ar")}      | {calc_pp_overall_if(dfp, "is_low_ar")}      | {calc_pp_overall_count(df, "is_low_ar")}      |
| Very_Low_AR | {calc_pp_overall_main(dfp, "is_very_low_ar")} | {calc_pp_overall_if(dfp, "is_very_low_ar")} | {calc_pp_overall_count(df, "is_very_low_ar")} |
| Speed_Up    | {calc_pp_overall_main(dfp, "is_speed_up")}    | {calc_pp_overall_if(dfp, "is_speed_up")}    | {calc_pp_overall_count(df, "is_speed_up")}    |
| Speed_Down  | {calc_pp_overall_main(dfp, "is_speed_down")}  | {calc_pp_overall_if(dfp, "is_speed_down")}  | {calc_pp_overall_count(df, "is_speed_down")}  |
| Total       | {calc_pp_overall_main(dfp)}                   | {calc_pp_overall_if(dfp)}                   | {calc_pp_overall_count(df)}                   |

"""
        )
    with st.container(border=True):
        st.markdown(_("## Scatter Plot"))
        begin_date, end_date = st.date_input(_("date range"), [df["ts"].min() - pd.Timedelta(days=1), pd.Timestamp.today() + pd.Timedelta(days=1)], key="cat_date_range")
        df1: pd.DataFrame = df[(df["ts"].dt.date > begin_date) & (df["ts"].dt.date < end_date)]
        sr_slider = st.slider(_("star rating"), 0.0, 13.5, (0.5, 8.5))
        memorized_multiselect(
            _("custom columns"),
            "cat_col",
            list(df.columns),
            [
                "ts",
                "passed",
                "combo_pct",
                "accuracy",
                "pp",
                "density",
                "aim_density_ratio",
                "speed_density_ratio",
                "aim_speed_ratio",
                "score_nf",
                "cs",
                "hit_window",
                "preempt",
                "mods",
                "info",
                "bid",
            ],
        )
        df2: pd.DataFrame = df1[(df1["b_star_rating"] > sr_slider[0]) & (df1["b_star_rating"] < sr_slider[1])]
        advanced_filter = st.text_input(_("advanced filter"), key="cat_advanced_filter")
        if advanced_filter:
            df3: pd.DataFrame = df2.query(advanced_filter)
        else:
            df3 = df2
        enable_complex = st.checkbox(_("more complex charts"), key="cat_enable_complex")

        if enable_complex:
            col1, col2 = st.columns(2)
            with col1:
                memorized_selectbox("x", "cat_x2", list(df.columns), "score_nf")
            with col2:
                memorized_selectbox("s", "cat_s", list(df.columns), "b_star_rating")
            memorized_multiselect("y", "cat_y2", list(df.columns), ["b_aim_difficulty", "b_speed_difficulty"])
            fig_data = [df3[col] for col in st.session_state.cat_y2]
            fig = ff.create_distplot(
                fig_data,
                st.session_state.cat_y2,
                bin_size=[calc_bin_size(data) for data in fig_data],
            )
            st.plotly_chart(fig)
            st.scatter_chart(
                df3,
                x=st.session_state.cat_x2,
                y=st.session_state.cat_y2,
                size=st.session_state.cat_s,
            )
        else:
            memorized_selectbox("x", "cat_x", list(df.columns), "b_star_rating")
            memorized_multiselect("y", "cat_y", list(df.columns), ["score_nf"])
            st.scatter_chart(
                df3,
                x=st.session_state.cat_x,
                y=st.session_state.cat_y,
            )

    with st.container(border=True):
        st.markdown(_("## filtered data"))
        if len(st.session_state.cat_col) > 0:
            st.dataframe(df3.sort_values(by="ts", ascending=False), key="cat_dataframe", column_order=st.session_state.cat_col, hide_index=True)
        else:
            st.dataframe(df3, key="cat_dataframe")


main()
