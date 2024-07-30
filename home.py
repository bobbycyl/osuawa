import argparse
import logging
import os.path
import re
import time
from secrets import token_hex
from shutil import copyfile
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
    StringField as Str,
)
from streamlit import logger, runtime
from streamlit.components.v1 import html
from streamlit.errors import Error
from streamlit.runtime.scriptrunner import get_script_run_ctx

from osuawa import OsuPlaylist, Osuawa, Path

if not os.path.exists("./logs"):
    os.mkdir("./logs")

DEBUG_MODE = True

arg_parser = argparse.ArgumentParser(description="osuawa")
arg_parser.add_argument("oauth_filename")
arg_parser.add_argument("osu_tools_path")
args = arg_parser.parse_args()
if "cmdparser" not in st.session_state:
    st.session_state.cmdparser = CommandParser()


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
        return Osuawa(args.oauth_filename, args.osu_tools_path, Path.OUTPUT_DIRECTORY.value)


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
            "where",
            "get user info",
            [Str("username")],
            1,
            st.session_state.awa.get_user_info,
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
                    [int(os.path.splitext(os.path.basename(x))[0]) for x in os.listdir(os.path.join(str(Path.OUTPUT_DIRECTORY.value), Path.RAW_RECENT_SCORES.value))],
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
        Command(
            "autogen",
            "generate local playlists",
            [Bool("fast_gen", True), Bool("output_zip", True)],
            1,
            generate_all_playlists,
        ),
        Command("cat", "show user recent scores", [Int("user")], 0, cat),
    ]


def register_cmdparser(obj: Optional[dict] = None):
    ret = ""
    if obj is None:
        obj = {}
    if "perm" not in st.session_state:
        st.session_state.perm = 0
    if not obj.get("simple", False):
        if "token" in st.session_state and obj.get("token", "") == st.session_state.token:
            st.session_state.perm = 1
            ret = "token matched"
        else:
            st.session_state.token = token_hex(16)
            logger.get_logger("streamlit").info("%s -> %s" % (UUID(get_script_run_ctx().session_id).hex, st.session_state.token))
            ret = "token generated"
        if "awa" in st.session_state and obj.get("refresh", False):
            st.session_state.awa = register_osu_api()
    else:
        if DEBUG_MODE:
            st.session_state.perm = 999
            ret = "**WARNING: DEBUG MODE ON**"
    st.session_state.cmdparser.register_command(st.session_state.perm, *commands())
    return ret


def generate_all_playlists(fast_gen: bool = False, output_zip: bool = False):
    original_playlist_pattern = re.compile(r"O\.(.*)\.properties")
    match_playlist_pattern = re.compile(r"M\.(.*)\.properties")
    community_playlist_pattern = re.compile(r"C\.(.*)\.properties")
    for filename in os.listdir("./playlists/raw/"):
        if m := original_playlist_pattern.match(filename):
            suffix = " — original playlist"
        elif m := match_playlist_pattern.match(filename):
            suffix = " — match playlist"
        elif m := community_playlist_pattern.match(filename):
            suffix = " — community playlist"
        else:
            continue
        if os.path.exists("./playlists/%s.html" % m.group(1)) and fast_gen:
            st.write("skipped %s" % m.group(1))
            continue
        try:
            copyfile("./playlists/raw/%s" % m.group(0), "./playlists/%s.properties" % m.group(1))
            OsuPlaylist(st.session_state.awa.client, "./playlists/%s.properties" % m.group(1), suffix=suffix, output_zip=output_zip).generate()
        finally:
            os.remove("./playlists/%s.properties" % m.group(1))


def cat(user: int):
    if not os.path.exists(os.path.join(str(Path.OUTPUT_DIRECTORY.value), Path.RECENT_SCORES.value, f"{user}.csv")):
        raise ValueError("user %d not found" % user)
    df = pd.read_csv(os.path.join(str(Path.OUTPUT_DIRECTORY.value), Path.RECENT_SCORES.value, f"{user}.csv"), index_col=0, parse_dates=["ts"])
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
