import logging
import os
import re
import time
from secrets import token_hex
from shutil import copyfile
from typing import Optional
from uuid import UUID

import pandas as pd
import streamlit as st
from aiohttp import ClientError
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
from streamlit import logger
from streamlit.components.v1 import html
from streamlit.errors import Error
from streamlit.runtime.scriptrunner import get_script_run_ctx

from osuawa import LANGUAGES, OsuPlaylist, Osuawa, Path
from osuawa.utils import memorized_selectbox

permitted_ids = [30826405]

st.set_page_config(page_title=_("Homepage") + " - osuawa")


def set_sidebar():
    with st.sidebar:
        memorized_selectbox("lang", "uni_lang", LANGUAGES, None)


def run(g):
    while True:
        try:
            st.write(next(g))
        except CommandError as e:
            st.error(e)
            break  # use continue if you want to continue running the generator
        except StopIteration as e:
            st.success("%s done" % e.value)
            break
        except Error as e:
            logger.get_logger("streamlit").exception(e)
            # st.session_state.clear()
            break
        except Exception as e:
            st.error(_("uncaught exception: %s") % str(e))
            logger.get_logger("streamlit").exception(e)
            break


def register_awa(code: str = None):
    with st.spinner(_("registering a client...")):
        return Osuawa(st.secrets.args.oauth_filename, Path.OUTPUT_DIRECTORY.value, code)


def commands():
    return [
        Command(
            "reg",
            _("register command parser"),
            [JsonStr("obj", True)],
            0,
            register_commands,
        ),
        Command(
            "where",
            _("get user info"),
            [Str("username")],
            0,
            st.session_state.awa.get_user_info,
        ),
        Command(
            "ps",
            _("save user recent scores"),
            [Int("user"), Bool("include_fails", True)],
            1,
            st.session_state.awa.save_recent_scores,
        ),
        Command(
            "psa",
            _("update user recent scores"),
            [
                Coll(
                    "user",
                    [int(os.path.splitext(os.path.basename(x))[0]) for x in os.listdir(os.path.join(str(Path.OUTPUT_DIRECTORY.value), Path.RAW_RECENT_SCORES.value))],
                )
            ],
            1,
            st.session_state.awa.save_recent_scores,
        ),
        Command("s2", _("get and show score"), [Int("score_id")], 0, st.session_state.awa.get_score),
        Command(
            "s",
            _("get and show user scores of a beatmap"),
            [Int("beatmap"), Int("user", True)],
            0,
            st.session_state.awa.get_user_beatmap_scores,
        ),
        Command(
            "autogen",
            _("generate local playlists"),
            [Bool("fast_gen", True), Bool("output_zip", True)],
            1,
            generate_all_playlists,
        ),
        Command("cat", _("show user recent scores"), [Int("user")], 0, cat),
    ]


def register_commands(obj: Optional[dict] = None):
    ret = ""
    if obj is None:
        obj = {}
    if "perm" not in st.session_state:
        st.session_state.perm = 0
    if not obj.get("simple", False):
        if "token" in st.session_state and "token" in obj:
            if obj["token"] == st.session_state.token:
                st.session_state.perm = 1
                ret = _("token matched")
            else:
                ret = _("token mismatched")
        else:
            st.info(_('Use `reg {"token": "<token>"}` to pass the token, or `reg {"refresh": "true"}` to refresh the client.'))
            st.session_state.token = token_hex(16)
            logger.get_logger("streamlit").info("%s -> %s" % (UUID(get_script_run_ctx().session_id).hex, st.session_state.token))
            ret = _("token generated")
            st.toast(_("You need to ask the web admin for the session token to unlock the full features."))
        if "awa" in st.session_state and obj.get("refresh", False):
            st.session_state.awa = register_awa()
            ret = _("client refreshed")
    else:
        # 冗余设计
        pass
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
            OsuPlaylist(st.session_state.awa, "./playlists/%s.properties" % m.group(1), suffix=suffix).generate()
        except Exception as e:
            raise RuntimeError(_("could not generate %s") % m.group(1)) from e
        else:
            st.write(_("generated %s") % m.group(1))
        finally:
            os.remove("./playlists/%s.properties" % m.group(1))


def cat(user: int):
    if not os.path.exists(os.path.join(str(Path.OUTPUT_DIRECTORY.value), Path.RECENT_SCORES.value, f"{user}.csv")):
        raise ValueError(_("user %d not found") % user)
    df = pd.read_csv(os.path.join(str(Path.OUTPUT_DIRECTORY.value), Path.RECENT_SCORES.value, f"{user}.csv"), index_col=0, parse_dates=["ts"])
    return df


@st.cache_data
def init_logger():
    fh = logging.FileHandler("./logs/streamlit.log", encoding="utf-8")
    fh.setFormatter(logging.Formatter("[%(asctime)s] [%(name)s/%(levelname)s]: %(message)s"))
    logger.get_logger("streamlit").addHandler(fh)
    logger.get_logger(st.session_state.username).addHandler(fh)


def submit():
    logger.get_logger(st.session_state.username).info(st.session_state["input"])
    run(st.session_state.cmdparser.parse_command(st.session_state["input"]))
    st.session_state["delete_line"] = True
    st.session_state["counter"] += 1


if "cmdparser" not in st.session_state:
    st.session_state.cmdparser = CommandParser()

set_sidebar()

if "awa" in st.session_state:
    with st.spinner(_("preparing for the next command...")):
        time.sleep(1.5)
else:
    if "code" in st.query_params:
        try:
            awa = register_awa(st.query_params.code)
        except ClientError:
            st.error(_("invalid code"))
            st.stop()
        else:
            st.session_state.awa = awa
            st.session_state.user_id, st.session_state.username = st.session_state.awa.user
            if st.session_state.user_id in permitted_ids:
                st.session_state.token = ""
                register_commands({"token": ""})
            st.rerun()
    else:
        st.info(_("Please click the button below to authorize the app."))
        st.link_button(_("OAuth2 url"), register_awa().auth_url)
        st.stop()

init_logger()
register_commands({"simple": True})

if "delete_line" not in st.session_state:
    st.session_state["delete_line"] = True
if "counter" not in st.session_state:
    st.success(_("Welcome!"))
    st.session_state["counter"] = 0
if st.session_state["delete_line"]:
    st.session_state["input"] = ""
    st.session_state["delete_line"] = False

y = st.text_input("> ", key="input", on_change=submit, placeholder=_('Type "help" to get started.'))

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

st.text(_("Session: %s") % UUID(get_script_run_ctx().session_id).hex)
