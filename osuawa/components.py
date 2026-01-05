import os
import os.path
import re
import shelve
import shutil
from collections import deque
from secrets import token_hex
from shutil import copyfile
from typing import Any, Literal, Optional, TYPE_CHECKING
from uuid import UUID

import pandas as pd
import streamlit as st
from clayutil.cmdparse import (
    BoolField as Bool,
    CollectionField as Coll,
    Command,
    IntegerField as Int,
    JSONStringField as JsonStr,
    StringField as Str,
)
from streamlit import logger
from streamlit.runtime.scriptrunner import get_script_run_ctx

from osuawa import C, OsuPlaylist, Osuawa
from osuawa.utils import format_size, get_size_and_count, user_recent_scores_directory

if TYPE_CHECKING:

    def _(text: str) -> str: ...


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


def init_page(page_title: str, force_val: Optional[bool] = None, need_st_awa: bool = True) -> None:
    """Page 初始化相关

    :param page_title: 标题
    :param force_val: 强制赋值，None 为禁用
    :param need_st_awa: 需要 session_state.awa
    :return:
    """
    # force_val 好像要引入额外的 session_state 键值对才能做出来，暂时还是不考虑了
    # layout 部分由沉浸模式强制覆盖 CSS 暂时替代，看看效果如何
    st.set_page_config(
        # layout="wide" if st.session_state.get("wide_layout", False) else "centered",
        page_title=page_title,
    )
    if need_st_awa:
        try:
            assert isinstance(st.session_state.awa, Osuawa)
        except (AttributeError, AssertionError):
            st.error(_("Failed to initialize the osu!api wrapper. Please refresh the page."))
            st.stop()


project_dir = os.path.join(os.path.dirname(__file__), "..")


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
        Command("cat", _("show user recent scores (only saved scores are available)"), [Int("user")], 0, cat),
    ]


def files_action(action: Literal["show", "clean"], filename: Optional[str] = None) -> str:
    if filename is not None and ".." in os.path.relpath(filename, project_dir):
        raise ValueError("parent directory access is not allowed")
    ret_md = ""
    match action:
        case "show":
            if filename is None:
                # 展示相关文件
                ret_md += "# Show Files\n\n"
                # 三个主要文件夹
                ret_md += "## Storage\n\n"
                for _path in [C.OUTPUT_DIRECTORY.value, C.UPLOADED_DIRECTORY.value, C.BEATMAPS_CACHE_DIRECTORY.value]:
                    action_path = os.path.join(project_dir, _path)
                    size, count = get_size_and_count(action_path)
                    size = format_size(size)
                    # - **_path**: size, count
                    ret_md += f"- **{_path}**: {size}, {count}\n\n"
                # 检查 *LCK ./*LCK
                ret_md += "## Lock Files\n\n"
                for _path in os.listdir(os.path.join(project_dir)):
                    if _path.endswith(".LCK"):
                        ret_md += f"- {_path}\n\n"
                # 检查 token pickle ./.streamlit/.oauth/*.pickle
                ret_md += "## Token Pickles\n\n"
                for _path in os.listdir(os.path.join(project_dir, ".streamlit", ".oauth")):
                    if _path.endswith(".pickle"):
                        ret_md += f"- {_path}\n\n"
            else:
                _path = os.path.join(project_dir, filename)
                if os.path.exists(_path):
                    if os.path.isfile(_path):
                        # cat 前 10000 个字符
                        with open(_path, "r", encoding="utf-8") as fi:
                            ret_md += fi.read(10000)
                            ret_md += "(truncated)\n\n"
                    else:
                        size, count = get_size_and_count(_path)
                        size = format_size(size)
                        ret_md += f"- **{filename}**: {size}, {count}\n\n"
        case "clean":
            if filename is None:
                # 本来打算是清理相关文件夹，但为了安全考虑，如果不给定 filename，则不执行任何操作
                ret_md += "must specify a filename"
            else:
                # 删除文件或文件夹
                ret_md += "# Clean Files\n\n"
                _path = os.path.join(project_dir, filename)
                if os.path.exists(_path):
                    if os.path.isfile(_path):
                        os.remove(_path)
                        ret_md += f"cleaned the file: {filename}"
                    else:
                        shutil.rmtree(_path)
                        ret_md += f"cleaned the whole directory: {filename}"
                else:
                    ret_md += f"{filename} not found"
    return ret_md


def log_action(n: int = 100, keyword: Optional[str] = None) -> str:
    ret_md = "# Show last %d lines of logs" % n
    log_filename = os.path.join(project_dir, "./logs/streamlit.log")
    with open(log_filename, "r", encoding="utf-8") as fi:
        # 先拿到最后 N 行
        last_lines = deque(fi, maxlen=n)

    results = []
    if keyword is not None:
        ret_md += f' with keyword "{keyword}".\n\n'
        for line in last_lines:
            if keyword in line:
                results.append(line)
    else:
        ret_md += ".\n\n"
        results = list(last_lines)
    # 使用代码块包裹日志内容，防止 Markdown 格式错乱
    ret_md += "```log\n"
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
    df = st.session_state.awa.calculate_extended_scores_dataframe_with_timezone(pd.read_parquet(user_recent_scores_directory(user)))
    return df


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
            logger.get_logger("streamlit").info("generated token for session %s: %s" % (UUID(get_script_run_ctx().session_id).hex, st.session_state.token))
            ret = _("token generated")
            st.toast(_("You need to ask the web admin for the session token to unlock full features."))
    else:
        # 冗余设计
        pass
    st.session_state.cmdparser.register_command(st.session_state.perm, *commands())
    return ret
