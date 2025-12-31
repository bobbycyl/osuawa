import os
from typing import Optional

import pandas as pd
import plotly.express as px
import streamlit as st
from plotly import figure_factory as ff
from scipy import stats

from osuawa import C
from osuawa.components import init_page_layout, memorized_multiselect, memorized_selectbox
from osuawa.utils import calc_bin_size, regex_search_column, user_recent_scores_directory

init_page_layout(_("Score visualizer") + " - osuawa")
all_users = [os.path.splitext(os.path.basename(x))[0] for x in os.listdir(os.path.join(str(C.OUTPUT_DIRECTORY.value), C.RECENT_SCORES.value))]
user = st.selectbox(_("user"), all_users)
st.date_input(_("date range"), [pd.Timestamp.today() - pd.Timedelta(days=30), pd.Timestamp.today() + pd.Timedelta(days=1)], key="cat_date_range")

THEME_COLOR_BLUE = "#4C95D9"
THEME_COLOR_RED = "#FF6A6A"
CO = THEME_COLOR_RED
CC = THEME_COLOR_BLUE


def calc_pp_overall_main(data: pd.DataFrame, tag: Optional[str] = None) -> str:
    if tag is None:
        df_tag = data
    else:
        df_tag = data[data[tag]]
    return "%.2f (%.2f%%)" % (df_tag["pp"].sum(), df_tag["pp"].sum() / data["pp"].sum() * 100)


def calc_pp_overall_if(data: pd.DataFrame, tag: Optional[str] = None) -> str:
    if tag is None:
        df_tag = data
    else:
        df_tag = data[data[tag]]
    got_pp = df_tag["pp"].sum()
    pp_100if = df_tag["b_pp_100if"].sum()
    pp_92if = df_tag["b_pp_92if"].sum()
    pp_81if = df_tag["b_pp_81if"].sum()
    pp_67if = df_tag["b_pp_67if"].sum()
    return "%.2f%%/%.2f%%/%.2f%%/%.2f%%" % (got_pp / pp_100if * 100, got_pp / pp_92if * 100, got_pp / pp_81if * 100, got_pp / pp_67if * 100)


def calc_pp_overall_count(data: pd.DataFrame, tag: Optional[str] = None) -> str:
    if tag is None:
        df_tag = data
    else:
        df_tag = data[data[tag]]
    return "%d (%.2f%%)" % (len(df_tag), len(df_tag) / len(data) * 100)


def apply_filter(data: pd.DataFrame) -> pd.DataFrame:
    srl, srh = st.session_state.cat_sr_range
    df1 = regex_search_column(data, "mods", st.session_state.rec_tosu_mods)
    if srl == 0.0 and srh == 10.0:
        # 0 - 10 视为无限制
        df2: pd.DataFrame = df1[((not st.session_state.cat_passed) | data["passed"]) & ((not st.session_state.cat_acm) | data["only_common_mods"])]
    else:
        df2: pd.DataFrame = df1[(df1["b_star_rating"] > srl) & (df1["b_star_rating"] < srh) & ((not st.session_state.cat_passed) | data["passed"]) & ((not st.session_state.cat_acm) | data["only_common_mods"])]
    if st.session_state.cat_advanced_filter != "":
        df3: pd.DataFrame = df2.query(st.session_state.cat_advanced_filter)
    else:
        df3 = df2
    return df3


def calc_statistics(data: pd.DataFrame, column: str) -> tuple[float, float, float, float, float, float, float, float, float, float, float, float, float, float, int]:
    data = data[column]
    # table: | index | min | Q1 | median | Q3 | max | mean | winsor_mean | std | var | CV | skew | kurtosis | 95% CI L | 95% CI U | N |
    data_se = data.sem()
    data_df = len(data) - 1
    t_critical = stats.t.ppf(0.975, data_df)
    margin_of_error = t_critical * data_se
    ci_l = data.mean() - margin_of_error
    ci_u = data.mean() + margin_of_error
    # 1% winsorize
    data_winsor = data.clip(lower=data.quantile(0.01), upper=data.quantile(0.99))
    return (
        data.min(),
        data.quantile(0.25),
        data.median(),
        data.quantile(0.75),
        data.max(),
        data.mean(),
        data_winsor.mean(),
        data.std(ddof=1),
        data.var(ddof=1),
        (data.std(ddof=1) / data.mean()),
        data.skew(),
        data.kurt(),
        ci_l,
        ci_u,
        len(data),
    )


def generate_stats_dataframe(data: pd.DataFrame, indexes: list[str]) -> pd.DataFrame:
    index_records = {}
    for index in indexes:
        index_records[index] = calc_statistics(data, index)
    df_stats = pd.DataFrame.from_dict(index_records, orient="index", columns=("min", "Q1", "median", "Q3", "max", "mean", "winsor_mean", "std", "var", "CV", "skew", "kurtosis", "_CIL", "_CIU", "N"))
    df_stats["95% CI"] = df_stats.apply(lambda row: f"[{row['_CIL']:.2f}, {row['_CIU']:.2f}]", axis=1)
    df_stats = df_stats[["min", "Q1", "median", "Q3", "max", "mean", "winsor_mean", "std", "var", "CV", "skew", "kurtosis", "95% CI", "N"]]
    return df_stats


if not os.path.exists(user_recent_scores_directory(user)):
    st.error(_("user not found"))
    st.stop()
begin_date, end_date = st.session_state.cat_date_range
df = pd.read_parquet(user_recent_scores_directory(user), filters=[("ts", ">=", begin_date), ("ts", "<=", end_date)])
if len(df) == 0:
    st.error(_("no scores found"))
    st.stop()
dfp = df[df["passed"]]
st.link_button(_("user profile"), f"https://osu.ppy.sh/users/{user}")

with st.container(border=True):
    st.markdown(_("## PP Overall"))
    st.markdown(
        f"""based on {len(df)} ({len(dfp)} passed) score(s)

got/100/92/81/67 {dfp["pp"].sum():.2f}/{dfp["b_pp_100if"].sum():.2f}/{dfp["b_pp_92if"].sum():.2f}/{dfp["b_pp_81if"].sum():.2f}/{dfp["b_pp_67if"].sum():.2f}pp

| tag         | got (passed)                                  | if (passed)                                 | count (total)                                 |
| ----------- | --------------------------------------------- | ------------------------------------------- | --------------------------------------------- |
| hd          | {calc_pp_overall_main(dfp, "is_hd")}          | {calc_pp_overall_if(dfp, "is_hd")}          | {calc_pp_overall_count(df, "is_hd")}          |
| high_ar     | {calc_pp_overall_main(dfp, "is_high_ar")}     | {calc_pp_overall_if(dfp, "is_high_ar")}     | {calc_pp_overall_count(df, "is_high_ar")}     |
| low_ar      | {calc_pp_overall_main(dfp, "is_low_ar")}      | {calc_pp_overall_if(dfp, "is_low_ar")}      | {calc_pp_overall_count(df, "is_low_ar")}      |
| very_low_ar | {calc_pp_overall_main(dfp, "is_very_low_ar")} | {calc_pp_overall_if(dfp, "is_very_low_ar")} | {calc_pp_overall_count(df, "is_very_low_ar")} |
| speed_up    | {calc_pp_overall_main(dfp, "is_speed_up")}    | {calc_pp_overall_if(dfp, "is_speed_up")}    | {calc_pp_overall_count(df, "is_speed_up")}    |
| speed_down  | {calc_pp_overall_main(dfp, "is_speed_down")}  | {calc_pp_overall_if(dfp, "is_speed_down")}  | {calc_pp_overall_count(df, "is_speed_down")}  |
| total       | {calc_pp_overall_main(dfp)}                   | {calc_pp_overall_if(dfp)}                   | {calc_pp_overall_count(df)}                   |

""",
    )

with st.expander(_("Filtering")):
    st.text_input(_("mods filter (regex)"), key="cat_mods")
    st.slider(_("star rating"), 0.0, 10.0, (1.5, 8.5), key="cat_sr_range")
    st.checkbox(_("passed only"), key="cat_passed")
    st.checkbox(_("common mods only"), key="cat_acm")
    memorized_multiselect(
        _("custom columns"),
        "cat_col",
        list(df.columns),
        [
            "ts",
            "passed",
            "combo_pct",
            "accuracy",
            "pp_pct",
            "pp_aim_pct",
            "pp_speed_pct",
            "pp_accuracy_pct",
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
    st.text_input(_("advanced filter"), key="cat_advanced_filter")

df_o = apply_filter(df)

with st.container(border=True):
    st.markdown(_("## Playing Preferences"))
    comp_user = st.selectbox(_("compared to"), all_users)
    df_c = apply_filter(pd.read_parquet(user_recent_scores_directory(comp_user), filters=[("ts", ">=", begin_date), ("ts", "<=", end_date)]))
    stats_indexes = [
        "accuracy",
        "hit_window",
        "preempt",
        "bpm",
        "hit_length",
        "b_star_rating",
        "b_max_combo",
        "b_aim_difficulty",
        "b_aim_difficult_slider_count",
        "b_speed_difficulty",
        "b_speed_note_count",
        "b_slider_factor",
        "time",
        "pp_pct",
        "pp_aim_pct",
        "pp_speed_pct",
        "pp_accuracy_pct",
        "pp_92pct",
        "pp_81pct",
        "pp_67pct",
        "combo_pct",
        "density",
        "aim_density_ratio",
        "speed_density_ratio",
        "aim_speed_ratio",
        "score_nf",
    ]
    df_o_stats = generate_stats_dataframe(df_o, stats_indexes)
    df_c_stats = generate_stats_dataframe(df_c, stats_indexes)
    with st.expander(_("Statistics")):
        st.dataframe(df_o_stats, column_order=("min", "median", "max", "mean", "winsor_mean", "std", "95% CI", "N"))
        st.dataframe(df_c_stats, column_order=("min", "median", "max", "mean", "winsor_mean", "std", "95% CI", "N"))

    memorized_selectbox(_("index"), "cat_comp_index", stats_indexes, "b_star_rating")

    # 根据用户选择的指标，将两个玩家的数据放在同一张表与图中呈现
    df_o_ind = df_o[st.session_state.cat_comp_index]
    df_c_ind = df_c[st.session_state.cat_comp_index]
    df_stats_ind_joined = pd.DataFrame(
        {
            comp_user: df_c_stats.T[st.session_state.cat_comp_index],
            user: df_o_stats.T[st.session_state.cat_comp_index],
        },
    )
    st.table(df_stats_ind_joined.round(2))
    can_show_chart_pr = True
    if df_o_stats.at[st.session_state.cat_comp_index, "std"] == 0:
        st.error(_("%s of user %s is constant (%s)") % (st.session_state.cat_comp_index, user, df_o_stats.at[st.session_state.cat_comp_index, "mean"]))
        can_show_chart_pr = False
    if df_c_stats.at[st.session_state.cat_comp_index, "std"] == 0:
        st.error(_("%s of user %s is constant (%s)") % (st.session_state.cat_comp_index, comp_user, df_c_stats.at[st.session_state.cat_comp_index, "mean"]))
        can_show_chart_pr = False
    if can_show_chart_pr:
        df_ind_joined = pd.DataFrame(
            {
                st.session_state.cat_comp_index: pd.concat([df_o_ind, df_c_ind], ignore_index=True),
                "user": [user] * len(df_o_ind) + [comp_user] * len(df_c_ind),
            },
        )
        fig_data = [list(df_o_ind), list(df_c_ind)]
        fig = ff.create_distplot(
            fig_data,
            [user, comp_user],
            bin_size=[calc_bin_size(data) for data in fig_data],
            show_rug=False,
            colors=[CO, CC],
        )
        st.plotly_chart(fig)

        fig = px.box(
            df_ind_joined,
            x="user",
            y=st.session_state.cat_comp_index,
            color="user",
            category_orders={"user": [comp_user, user]},  # 为了匹配 ff.create_distplot 的奇怪图例顺序行为
            color_discrete_map={
                user: CO,
                comp_user: CC,
            },
            points="suspectedoutliers",
            notched=True,
        )
        st.plotly_chart(fig)

with st.container(border=True):
    st.markdown(_("## Skills Analysis"))
    enable_complex = st.checkbox(_("more complex charts"))
    try:
        if enable_complex:
            col1, col2 = st.columns(2)
            with col1:
                memorized_selectbox("x", "cat_x2", list(df.columns), "score_nf")
            with col2:
                memorized_selectbox("s", "cat_s", list(df.columns), "b_star_rating")
            memorized_multiselect("y", "cat_y2", list(df.columns), ["b_aim_difficulty", "b_speed_difficulty"])
            fig_data = [list(df_o[col]) for col in st.session_state.cat_y2]
            fig = ff.create_distplot(
                fig_data,
                st.session_state.cat_y2,
                bin_size=[calc_bin_size(data) for data in fig_data],
            )
            st.plotly_chart(fig)
            st.scatter_chart(
                df_o,
                x=st.session_state.cat_x2,
                y=st.session_state.cat_y2,
                size=st.session_state.cat_s,
            )
        else:
            memorized_selectbox("x", "cat_x", list(df.columns), "b_star_rating")
            memorized_multiselect("y", "cat_y", list(df.columns), ["score_nf"])
            st.scatter_chart(
                df_o,
                x=st.session_state.cat_x,
                y=st.session_state.cat_y,
            )
    except Exception as e:
        st.error(str(e))

with st.container(border=True):
    st.markdown(_("## Filtered Data"))
    if len(st.session_state.cat_col) > 0:
        st.dataframe(df_o.sort_values(by="ts", ascending=False), key="cat_dataframe", column_order=st.session_state.cat_col, hide_index=True)
    else:
        st.dataframe(df_o, key="cat_dataframe")
