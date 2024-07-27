import argparse
import logging
import os.path
import time
from secrets import token_hex
from typing import Optional
from uuid import UUID

import pandas as pd
import streamlit as st
from clayutil.cmdparse import (
    BoolField as Bool,
    CollectionField as Coll,
    Command,
    CommandError,
    CommandParser,
    IntegerField as Int,
    JSONStringField as JsonStr,
)
from streamlit import logger, runtime
from streamlit.components.v1 import html
from streamlit.errors import Error
from streamlit.runtime.scriptrunner import get_script_run_ctx

from osuawa import Osuawa, Path

if not os.path.exists("./logs"):
    os.mkdir("./logs")

DEBUG_MODE = True

arg_parser = argparse.ArgumentParser(description="osuawa")
arg_parser.add_argument("oauth_filename")
arg_parser.add_argument("osu_tools_path")
arg_parser.add_argument("output_dir")
args = arg_parser.parse_args()
if "cmdparser" not in st.session_state:
    st.session_state.cmdparser = CommandParser()
if "query" not in st.session_state:
    st.session_state.query = ""


@st.cache_data
def init_logger():
    fh = logging.FileHandler("./logs/streamlit.log", encoding="utf-8")
    fh.setFormatter(logging.Formatter("[%(asctime)s] [%(name)s/%(levelname)s]: %(message)s"))
    logger.get_logger("streamlit").addHandler(fh)
    logger.get_logger(runtime.get_instance().get_client(get_script_run_ctx().session_id).request.remote_ip).addHandler(fh)


def run(g):
    while True:
        try:
            st.write(next(g))
        except CommandError as e:
            st.error(e)
            break
        except StopIteration as e:
            st.success("%s done" % e.value)
            break
        except Error as e:
            logger.get_logger("streamlit").exception(e)
            # st.session_state.clear()
            break
        except Exception as e:
            st.error("uncaught exception: %s" % str(e))
            logger.get_logger("streamlit").exception(e)
            break


def register_osu_api():
    with st.spinner("registering a client..."):
        return Osuawa(args.oauth_filename, args.osu_tools_path, args.output_dir)


def commands():
    return [
        Command(
            "reg",
            "register command parser",
            [JsonStr("obj", True)],
            0,
            register_cmdparser,
        ),
        Command(
            "ps",
            "save user recent scores",
            [Int("user"), Bool("include_fails", True)],
            1,
            st.session_state.awa.save_recent_scores,
        ),
        Command(
            "psa",
            "update user recent scores",
            [
                Coll(
                    "user",
                    [os.path.splitext(os.path.basename(x))[0] for x in os.listdir(os.path.join(str(args.output_dir), Path.RAW_RECENT_SCORES.value))],
                )
            ],
            1,
            st.session_state.awa.save_recent_scores,
        ),
        Command("s2", "get and show score", [Int("score_id")], 1, st.session_state.awa.get_score),
        Command(
            "s",
            "get and show user scores of a beatmap",
            [Int("beatmap"), Int("user")],
            1,
            st.session_state.awa.get_user_beatmap_scores,
        ),
        Command("cat", "show user recent scores", [Int("user")], 0, cat),
    ]


def register_cmdparser(obj: Optional[dict] = None):
    ret = ""
    if obj is None:
        obj = {}
    perm = 0
    if not obj.get("simple", False):
        if "token" in st.session_state and obj.get("token", "") == st.session_state.token:
            perm = 1
            ret = "token matched"
        else:
            st.session_state.token = token_hex(16)
            logger.get_logger("streamlit").info("%s -> %s" % (UUID(get_script_run_ctx().session_id).hex, st.session_state.token))
            ret = "token generated"
        if "awa" in st.session_state and obj.get("refresh", False):
            st.session_state.awa = register_osu_api()
    else:
        if DEBUG_MODE:
            perm = 999
            ret = "**WARNING: DEBUG MODE ON**"
    st.session_state.cmdparser.register_command(perm, *commands())
    return ret


@st.fragment
def cat(user: int):
    if not os.path.exists(os.path.join(str(args.output_dir), Path.RECENT_SCORES.value, f"{user}.csv")):
        raise ValueError("User not found")
    df = pd.read_csv(
        os.path.join(str(args.output_dir), Path.RECENT_SCORES.value, f"{user}.csv"),
        index_col=0,
        parse_dates=["ts"]
    )
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
        begin_date, end_date = st.date_input("date range", [df["ts"].min() - pd.Timedelta(days=1), pd.Timestamp.today() + pd.Timedelta(days=1)])
        df1 = df[(df["ts"].dt.date > begin_date) & (df["ts"].dt.date < end_date)]
        sr_slider = st.slider("star rating", 0.0, 13.5, (0.5, 8.5))
        df2 = df1[(df1["b_star_rating"] > sr_slider[0]) & (df1["b_star_rating"] < sr_slider[1])]
        advanced_filter = st.text_input("advanced filter")
        if advanced_filter:
            df3 = df2.query(advanced_filter)
        else:
            df3 = df2
            t1 = st.toast("advanced filter disabled")
        enable_size = st.checkbox("enable scatter plot size parameter")
        if "default_x_with_size_enabled" not in st.session_state:
            st.session_state.default_x_with_size_enabled = 25
        if "default_size" not in st.session_state:
            st.session_state.default_size = 23
        if "default_x" not in st.session_state:
            st.session_state.default_x = 23

        if enable_size:
            col1, col2 = st.columns(2)
            with col1:
                x_radio_with_size_enabled = st.radio("x", df.columns, index=st.session_state.default_x_with_size_enabled)
            with col2:
                size_radio = st.radio("size", df.columns, index=st.session_state.default_size)
            y_multiselect = st.multiselect("y", df.columns, default="score_nf")
            st.scatter_chart(
                df3,
                x=x_radio_with_size_enabled,
                y=y_multiselect,
                size=size_radio,
            )
            st.session_state.default_x_with_size_enabled = pd.Index.get_loc(df.columns, x_radio_with_size_enabled)
            st.session_state.default_size = pd.Index.get_loc(df.columns, size_radio)
            t2 = st.toast("set default x to %s and default size to %s when size enabled" % (st.session_state.default_x_with_size_enabled, st.session_state.default_size))
            time.sleep(1)
            t2.empty()
        else:
            x_radio = st.radio("x", df.columns, index=st.session_state.default_x)
            y_multiselect = st.multiselect("y", df.columns, default="score_nf")
            st.scatter_chart(
                df3,
                x=x_radio,
                y=y_multiselect,
            )
            st.session_state.default_x = pd.Index.get_loc(df.columns, x_radio)
            t3 = st.toast("set default x to %s when size disabled" % st.session_state.default_x)
            time.sleep(1)
            t3.empty()
        t1.empty()

    with st.container(border=True):
        st.markdown("## filtered data")
        st.dataframe(df3)
    return df


init_logger()
if "awa" not in st.session_state:
    st.session_state.awa = register_osu_api()
else:
    with st.spinner("preparing..."):
        time.sleep(3)
register_cmdparser({"simple": True})


def submit():
    logger.get_logger(runtime.get_instance().get_client(get_script_run_ctx().session_id).request.remote_ip).info(st.session_state["input"])
    run(st.session_state.cmdparser.parse_command(st.session_state["input"]))
    st.session_state["delete_line"] = True
    st.session_state["counter"] += 1


if "delete_line" not in st.session_state:
    st.session_state["delete_line"] = True
if "counter" not in st.session_state:
    st.session_state["counter"] = 0
if st.session_state["delete_line"]:
    st.session_state["input"] = ""
    st.session_state["delete_line"] = False

y = st.text_input("> ", key="input", on_change=submit, placeholder="type 'help' to get started")

html(
    f"""<script>
    var input = window.parent.document.querySelectorAll("input[type=text]");
    for (var i = 0; i < input.length; ++i) {{
        input[i].focus();
    }}
</script>
""",
    height=0,
)

if y:
    st.text(y)

st.text("Session: %s" % UUID(get_script_run_ctx().session_id).hex)
