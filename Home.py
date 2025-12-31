import logging
import os
import re
import shutil
import time
from collections import deque
from html import escape as html_escape
from secrets import token_hex
from shutil import copyfile
from typing import Literal, Optional
from uuid import UUID

import pandas as pd
import requests
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
from ossapi import Domain, Scope
from streamlit import logger
from streamlit.components.v1 import html
from streamlit.errors import Error
from streamlit.runtime.scriptrunner import get_script_run_ctx

from osuawa import Awapi, C, LANGUAGES, OsuPlaylist, Osuawa
from osuawa.components import init_page_layout, memorized_selectbox
from osuawa.utils import format_size, get_an_osu_meme, get_size_and_count, user_recent_scores_directory

init_page_layout(_("Homepage") + " - osuawa", False)

admins = st.secrets.args.admins
home_bar = st.progress(0, text=_("Loading necessary objects..."))
project_dir = os.path.dirname(__file__)


def run(g):
    while True:
        try:
            st.write(next(g))
        except CommandError as e:
            st.error(e)
            break  # use continue if you want to continue running the generator
        except StopIteration as e:
            st.success(_("%s tasks done") % e.value)
            break
        except (Error, NotImplementedError) as e:
            logger.get_logger("streamlit").exception(e)
            # st.session_state.clear()
            break
        except Exception as e:
            st.exception(e)
            logger.get_logger("streamlit").exception(e)
            break


def register_awa(ci, cs, ru, s, d, oauth_token: Optional[str] = None, oauth_refresh_token: Optional[str] = None):
    with st.spinner(_("registering a client...")):
        return Osuawa(ci, cs, ru, s, d, st.context.cookies["ajs_anonymous_id"], oauth_token, oauth_refresh_token)


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
            "fman",
            "show or clean files",
            [Str("action"), Str("filename", True)],
            4,
            files_action,
        ),
        Command(
            "logfilter",
            "tail logs",
            [Int("n", True), Str("keyword", True)],
            4,
            log_action,
        ),
        Command(
            "where",
            _("get user info"),
            [Str("username")],
            0,
            st.session_state.awa.get_user_info,
        ),
        Command(
            "save",
            _("save user recent scores"),
            [Int("user"), Bool("include_fails", True)],
            1,
            st.session_state.awa.save_recent_scores,
        ),
        Command(
            "update",
            _("update user recent scores"),
            [
                Coll(
                    "user",
                    [int(os.path.splitext(os.path.basename(x))[0]) for x in os.listdir(os.path.join(str(C.OUTPUT_DIRECTORY.value), C.RAW_RECENT_SCORES.value))],
                ),
            ],
            1,
            st.session_state.awa.save_recent_scores,
        ),
        Command("score", _("get and show score"), [Int("score_id")], 0, st.session_state.awa.get_score),
        Command(
            "scores",
            _("get and show user scores of a beatmap"),
            [Int("beatmap"), Int("user", True)],
            0,
            st.session_state.awa.get_user_beatmap_scores,
        ),
        Command(
            "gen",
            _("generate local playlists"),
            [Bool("fast_mode", True), Bool("output_zip", True)],
            4,
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
            st.info(_('use `reg {"token": "<token>"}` to pass the token'))
            st.session_state.token = token_hex(16)
            logger.get_logger("streamlit").info("%s -> %s" % (UUID(get_script_run_ctx().session_id).hex, st.session_state.token))
            ret = _("token generated")
            st.toast(_("You need to ask the web admin for the session token to unlock full features."))
    else:
        # 冗余设计
        pass
    st.session_state.cmdparser.register_command(st.session_state.perm, *commands())
    return ret


home_bar.progress(15, text=get_an_osu_meme())


def files_action(action: Literal["show", "clean"], filename: str = None) -> str:
    if ".." in filename:
        raise ValueError("parent directory access is not allowed")
    ret_md = ""
    match action:
        case "show":
            if filename is None:
                # 展示相关文件
                ret_md += "# Show Files\n\n"
                # 三个主要文件夹
                ret_md += "## Storage\n\n"
                for path in [C.OUTPUT_DIRECTORY.value, C.UPLOADED_DIRECTORY, C.BEATMAPS_CACHE_DIRECTORY]:
                    action_path = os.path.join(project_dir, path)
                    size, count = get_size_and_count(action_path)
                    size = format_size(size)
                    # - **path**: size, count
                    ret_md += f"- **{path}**: {size}, {count}\n\n"
                # 检查 *LCK ./*LCK
                ret_md += "## Lock Files\n\n"
                for path in os.listdir(os.path.join(project_dir)):
                    if path.endswith(".LCK"):
                        ret_md += f"- {path}\n\n"
                # 检查 token pickle ./.streamlit/*.pickle
                ret_md += "## Token Pickles\n\n"
                for path in os.listdir(os.path.join(project_dir, ".streamlit")):
                    if path.endswith(".pickle"):
                        ret_md += f"- {path}\n\n"
            else:
                path = os.path.join(project_dir, filename)
                if os.path.exists(path):
                    if os.path.isfile(path):
                        # cat 前 10000 个字符
                        with open(path, "r", encoding="utf-8") as fi:
                            ret_md += fi.read(10000)
                            ret_md += "(truncated)\n\n"
                    else:
                        size, count = get_size_and_count(path)
                        size = format_size(size)
                        ret_md += f"- **{path}**: {size}, {count}\n\n"
        case "clean":
            if filename is None:
                # 本来打算是清理相关文件夹，但为了安全考虑，如果不给定 filename，则不执行任何操作
                ret_md += "must specify a filename"
            else:
                # 删除文件或文件夹
                ret_md += "# Clean Files\n\n"
                path = os.path.join(project_dir, filename)
                if os.path.exists(path):
                    if os.path.isfile(path):
                        os.remove(path)
                        ret_md += f"cleaned the file: {path}"
                    else:
                        shutil.rmtree(path)
                        ret_md += f"cleaned the whole directory: {path}"
                else:
                    ret_md += f"{path} not found"
    return ret_md


def log_action(n: int = 100, keyword: Optional[str] = None) -> str:
    ret_md = "# Show last %d lines of logs" % n
    log_filename = os.path.join(project_dir, "./logs/streamlit.log")
    with open(log_filename, "r", encoding="utf-8") as f:
        # 先拿到最后 N 行
        last_lines = deque(f, maxlen=n)

    results = []
    if keyword is not None:
        ret_md += f'with keyword "{keyword}".\n\n'
        for line in last_lines:
            if keyword in line:
                results.append(line)
    else:
        ret_md += ".\n\n"
        results = list(last_lines)
    # 使用代码块包裹日志内容，防止 Markdown 格式错乱
    ret_md += "```text\n"
    # strip() 防止末尾多余空行
    ret_md += "".join(results).strip()
    ret_md += "\n```"
    return ret_md


def generate_all_playlists(fast_mode: bool = False, output_zip: bool = False):
    original_playlist_pattern = re.compile(r"O\.(.*)\.properties")
    match_playlist_pattern = re.compile(r"M\.(.*)\.properties")
    community_playlist_pattern = re.compile(r"C\.(.*)\.properties")
    original_playlist_beatmaps: dict[int, int] = {}
    for filename in os.listdir("./playlists/raw/"):
        if m := original_playlist_pattern.match(filename):
            suffix = " — original playlist"
        elif m := match_playlist_pattern.match(filename):
            suffix = " — match playlist"
        elif m := community_playlist_pattern.match(filename):
            suffix = " — community playlist"
        else:
            continue
        if os.path.exists("./playlists/%s.html" % m.group(1)) and fast_mode:
            st.write(_("skipped %s") % m.group(1))
            continue
        try:
            copyfile("./playlists/raw/%s" % m.group(0), "./playlists/%s.properties" % m.group(1))
            o = OsuPlaylist(st.session_state.awa, "./playlists/%s.properties" % m.group(1), suffix, 1)
            if suffix == " — original playlist":
                for element in o.beatmap_list:
                    original_playlist_beatmaps[element["bid"]] = original_playlist_beatmaps.get(element["bid"], 0) + 1
            df = o.generate()
            df.to_csv("./playlists/%s.csv" % m.group(1), index=False)
        except Exception as e:
            raise RuntimeError("%s (%s)" % (_("failed to generate %s") % m.group(1), str(e))) from e
        else:
            st.write(_("generated %s") % m.group(1))
        finally:
            os.remove("./playlists/%s.properties" % m.group(1))
    # report duplicates
    st.write(["%s(%s) " % (k, v) for k, v in original_playlist_beatmaps.items() if v > 1])


def cat(user: int):
    if not os.path.exists(user_recent_scores_directory(user)):
        raise ValueError(_("user %d not found") % user)
    df = pd.read_parquet(user_recent_scores_directory(user))
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


with st.sidebar:
    memorized_selectbox("lang", "uni_lang", LANGUAGES, None)

if "cmdparser" not in st.session_state:
    st.session_state.cmdparser = CommandParser()

if "awa" in st.session_state:
    home_bar.progress(92, text=get_an_osu_meme())
    time.sleep(1.5)
    init_logger()
    register_commands({"simple": True})
    home_bar.progress(100, text=get_an_osu_meme())
    home_bar.empty()

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
else:
    client_id = st.secrets.args.client_id
    client_secret = st.secrets.args.client_secret
    redirect_url = st.secrets.args.redirect_url
    scopes = [Scope.PUBLIC.value, Scope.IDENTIFY.value, Scope.FRIENDS_READ.value]
    domain = Domain.OSU.value
    home_bar.progress(35, text=get_an_osu_meme())
    try:
        if "code" not in st.query_params:
            # check if ossapi token is pickled
            if "ajs_anonymous_id" in st.context.cookies and os.path.exists("./.streamlit/.oauth/%s.pickle" % st.context.cookies["ajs_anonymous_id"]):
                awa = register_awa(client_id, client_secret, redirect_url, scopes, domain)
                home_bar.progress(67, text=get_an_osu_meme())
            else:
                home_bar.progress(55, text=get_an_osu_meme())
                st.info(_("Please click the button below to authorize the app."))
                st.link_button(_("OAuth2 URL"), "%s?client_id=%s&redirect_uri=%s&response_type=code&scope=%s" % (Awapi.AUTH_CODE_URL.format(domain=domain), html_escape(str(client_id)), html_escape(redirect_url), "+".join(scopes)))
                home_bar.progress(100, text=get_an_osu_meme())
                home_bar.empty()
                st.stop()
        else:
            code = st.query_params.code
            home_bar.progress(50, text=get_an_osu_meme())
            r = requests.post(
                Awapi.TOKEN_URL.format(domain=domain),
                headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
                data={"client_id": client_id, "client_secret": client_secret, "code": code, "grant_type": "authorization_code", "redirect_uri": redirect_url},
            )
            home_bar.progress(67, text=get_an_osu_meme())
            awa = register_awa(client_id, client_secret, redirect_url, scopes, domain, r.json().get("access_token"), r.json().get("refresh_token"))
            awa.api._save_token(awa.api.session.token)
        awa.tz = st.context.timezone
        st.session_state.awa = awa
        st.session_state.user, st.session_state.username = st.session_state.awa.user
    except NotImplementedError:
        if os.path.exists("./.streamlit/.oauth/%s.pickle" % st.context.cookies["ajs_anonymous_id"]):
            os.remove("./.streamlit/%s.pickle" % st.context.cookies["ajs_anonymous_id"])
        st.warning(_("OAuth2 token or code has expired. Please remove the url parameter and refresh the page."))
        home_bar.empty()
        st.stop()
    home_bar.progress(81, text=get_an_osu_meme())
    if st.session_state.user in admins:
        st.session_state.token = ""
        register_commands({"token": ""})
        st.session_state.perm = 4
    st.rerun()
