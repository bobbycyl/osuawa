import os
import os.path
import re
import shelve
import shutil
from collections import deque
from datetime import date, datetime, time
from secrets import token_hex
from shutil import copyfile
from typing import Any, Literal, Optional, TYPE_CHECKING, cast, overload
from uuid import UUID
from zoneinfo import ZoneInfo

import numpy as np
import orjson
import pandas as pd
import plotly.express as px
import redis
import streamlit as st
from clayutil.cmdparse import (
    BoolField as Bool,
    Command,
    IntegerField as Int,
    JSONStringField as JsonStr,
    StringField as Str,
)
from ossapi.ossapiv2_async import Beatmap, GameMode
from osu.Game.Rulesets.Catch import CatchRuleset
from osu.Game.Rulesets.Mania import ManiaRuleset
from osu.Game.Rulesets.Osu import OsuRuleset
from osu.Game.Rulesets.Taiko import TaikoRuleset
from plotly.graph_objs import Figure
from sqlalchemy import text
from streamlit import logger
from streamlit.runtime.scriptrunner import get_script_run_ctx

from osuawa import C, OsuPlaylist, Osuawa
from osuawa.osuawa import CachedMixIn
from osuawa.utils import (
    CompletedSimpleScoreInfo,
    RedisTaskId,
    ScoreStatistics,
    SimpleDifficultyAttribute,
    _build_upsert,
    _make_query_uppercase,
    calculate_performance,
    catch_mod_entries,
    catch_mod_indexes,
    download_osu,
    format_size,
    make_unstandardized_mods_from_lines,
    get_mod_type_mapping,
    get_size_and_count,
    mania_mod_entries,
    mania_mod_indexes,
    osu_mod_entries,
    osu_mod_indexes,
    push_task,
    taiko_mod_entries,
    taiko_mod_indexes,
)

if TYPE_CHECKING:

    def _(_text: str) -> str: ...

    # noinspection PyTypeHints
    st.session_state.awa: Osuawa

_conn = st.connection("osuawa", type="sql", ttl=60)
_conn.query = _make_query_uppercase(_conn.query)


def save_value(key: str) -> None:
    # <key> <-> st.session_state._<key>_value
    with st.session_state.lck:
        st.session_state["_%s_value" % key] = st.session_state[key]
        # semi-persistent storage
        # ./.streamlit/.components/<ajs_anonymous_id>
        with shelve.open(os.path.join(C.COMPONENTS_SHELVES_DIRECTORY.value, st.context.cookies["ajs_anonymous_id"])) as db:
            db[key] = st.session_state["_%s_value" % key]


def del_value(key: str) -> None:
    with st.session_state.lck:
        if "_%s_value" % key in st.session_state:
            del st.session_state["_%s_value" % key]
        if key in st.session_state:
            del st.session_state[key]
        with shelve.open(os.path.join(C.COMPONENTS_SHELVES_DIRECTORY.value, st.context.cookies["ajs_anonymous_id"])) as db:
            if key in db:
                del db[key]


def load_value(key: str, default_value: Any) -> None:
    # <key> <-> st.session_state._<key>_value
    if "_%s_value" % key not in st.session_state:
        if key not in st.session_state:
            # semi-persistent storage
            # ./.streamlit/.components/<ajs_anonymous_id>
            # 由于 load 可能发生在第一次访问（save 不会），所以还要检查 cookie 是否存在
            if "ajs_anonymous_id" in st.context.cookies and (
                os.path.exists(os.path.join(C.COMPONENTS_SHELVES_DIRECTORY.value, st.context.cookies["ajs_anonymous_id"]))
                or os.path.exists(os.path.join(C.COMPONENTS_SHELVES_DIRECTORY.value, "%s.bak" % st.context.cookies["ajs_anonymous_id"]))
                or os.path.exists(os.path.join(C.COMPONENTS_SHELVES_DIRECTORY.value, "%s.dat" % st.context.cookies["ajs_anonymous_id"]))
                or os.path.exists(os.path.join(C.COMPONENTS_SHELVES_DIRECTORY.value, "%s.dir" % st.context.cookies["ajs_anonymous_id"]))
            ):
                with shelve.open(os.path.join(C.COMPONENTS_SHELVES_DIRECTORY.value, st.context.cookies["ajs_anonymous_id"]), "r") as db:
                    st.session_state["_%s_value" % key] = db.get(key, default_value)
            else:
                st.session_state["_%s_value" % key] = default_value
        else:
            raise RuntimeError("the key of memorized component is used: %s" % key)
    st.session_state[key] = st.session_state["_%s_value" % key]


def memorized_multiselect(label: str, key: str, options: list, default_value: Any, **kwargs) -> None:
    load_value(key, default_value)
    st.multiselect(label, options, key=key, on_change=save_value, args=(key,), disabled=not st.session_state.basic_interaction_enabled, **kwargs)


def memorized_selectbox(label: str, key: str, options: list, default_value: Any, **kwargs) -> None:
    load_value(key, default_value)
    st.selectbox(label, options, key=key, on_change=save_value, args=(key,), disabled=not st.session_state.basic_interaction_enabled, **kwargs)


def memorized_checkbox(label: str, key: str, default_value: bool, **kwargs) -> None:
    load_value(key, default_value)
    st.checkbox(label, key=key, on_change=save_value, args=(key,), disabled=not st.session_state.basic_interaction_enabled, **kwargs)


def memorized_number_input(label: str, key: str, default_value: int | float, **kwargs) -> None:
    load_value(key, default_value)
    st.number_input(label, key=key, on_change=save_value, args=(key,), disabled=not st.session_state.basic_interaction_enabled, **kwargs)


def memorized_text_input(label: str, key: str, default_value: str, **kwargs) -> None:
    load_value(key, default_value)
    st.text_input(label, key=key, on_change=save_value, args=(key,), disabled=not st.session_state.basic_interaction_enabled, **kwargs)


def get_session_id() -> str:
    ctx = get_script_run_ctx()
    if ctx is None:
        raise RuntimeError("no streamlit ctx")
    return UUID(ctx.session_id).hex


def init_page(page_title: str, force_val: Optional[bool] = None) -> None:
    """Page 初始化相关

    :param page_title: 标题
    :param force_val: 强制赋值，None 为禁用
    :return:
    """
    # force_val 好像要引入额外的 session_state 键值对才能做出来，暂时还是不考虑了
    # layout 部分由沉浸模式强制覆盖 CSS 暂时替代，看看效果如何
    st.set_page_config(
        # layout="wide" if st.session_state.get("wide_layout", False) else "centered",
        page_title=page_title,
    )
    # always require awa
    try:
        assert isinstance(st.session_state.awa, Osuawa)
    except (AttributeError, AssertionError):
        st.error(_("Failed to initialize the osu! api wrapper. Please refresh the page."))
        st.stop()


@st.cache_resource
def get_redis_connection():
    r = redis.Redis(host="localhost", port=6379, db=0, decode_responses=True)
    return r


_r = get_redis_connection()


def commands():
    return [
        Command("reg", _("Register command parser"), [JsonStr("obj", True)], 0, register_commands),
        Command("fman", "Show or clean files", [Str("action"), Str("filename", True)], 4, files_action),
        Command("logfilter", "Tail logs", [Int("n", True), Str("keyword", True)], 3, tail_log),
        Command("apicache", "Show api cache", [], 3, CachedMixIn.get_cache),
        Command("where", _("Get user info"), [Str("username")], 0, st.session_state.awa.get_user_info),
        Command("save", _("Save user's recent scores"), [Int("user")], 1, lambda user: push_task_with_session_state("save %d" % user)),
        Command("score", _("Get and display score"), [Int("score_id")], 0, st.session_state.awa.get_score),
        Command("scores", _("Get and display user scores of a beatmap"), [Int("beatmap"), Int("user", True)], 0, st.session_state.awa.get_user_beatmap_scores),
        Command("gen", _("Generate local playlists"), [Bool("fast_mode", True), Bool("output_zip", True)], 4, generate_all_playlists),
        Command("cat", _("Display user's recent scores (only saved scores are available)"), [Int("user")], 0, cat),
        Command("strain", _("Draw strain graph of an osu! beatmap (converted beatmap supported)"), [Int("beatmap"), Str("mod_settings", True), Int("ruleset_id", True)], 0, draw_strain_graph),
        Command("sessions", _("Display all active sessions"), [], 0, query_all_sessions),
        Command("invalidate", _("Invalidate all sessions"), [], 0, invalidate_user_cache),
    ]


def files_action(action: Literal["show", "clean"], filename: Optional[str] = None) -> str:
    if filename is not None and ".." in os.path.relpath(filename, os.path.abspath(".")):
        raise ValueError("parent directory access is not allowed")
    ret_md = ""
    match action:
        case "show":
            if filename is None:
                # 展示相关文件
                ret_md += "# Show Files\n\n"
                # 三个主要文件夹
                # todo: components shelves 是否需要检查
                ret_md += "## Storage\n\n"
                for action_path in [C.OUTPUT_DIRECTORY.value, C.UPLOADED_DIRECTORY.value, C.BEATMAPS_CACHE_DIRECTORY.value]:
                    size, count = get_size_and_count(action_path)
                    size = format_size(size)
                    # - **action_path**: size, count
                    ret_md += f"- **{action_path}**: {size}, {count}\n\n"
                # 检查 *LCK ./*LCK
                ret_md += "## Lock Files\n\n"
                for action_path in os.listdir():
                    if action_path.endswith(".LCK"):
                        ret_md += f"- {action_path}\n\n"
                # 检查 token pickle ./.streamlit/.oauth/*.pickle
                ret_md += "## Token Pickles\n\n"
                for action_path in os.listdir(C.OAUTH_TOKEN_DIRECTORY.value):
                    if action_path.endswith(".pickle"):
                        ret_md += f"- {action_path}\n\n"
            else:
                if os.path.exists(filename):
                    if os.path.isfile(filename):
                        # cat 前 10000 个字符
                        with open(filename, "r", encoding="utf-8") as fi:
                            ret_md += fi.read(10000)
                            ret_md += "(truncated)\n\n"
                    else:
                        size, count = get_size_and_count(filename)
                        size = format_size(size)
                        ret_md += f"- **{filename}**: {size}, {count}\n\n"
        case "clean":
            if filename is None:
                # 本来打算是清理相关文件夹，但为了安全考虑，如果不给定 filename，则不执行任何操作
                ret_md += "must specify a filename"
            else:
                # 删除文件或文件夹
                ret_md += "# Clean Files\n\n"
                if os.path.exists(filename):
                    if os.path.isfile(filename):
                        os.remove(filename)
                        ret_md += f"cleaned the file: {filename}"
                    else:
                        shutil.rmtree(filename)
                        ret_md += f"cleaned the whole directory: {filename}"
                else:
                    ret_md += f"{filename} not found"
    return ret_md


def tail_log(n: int = 100, keyword: Optional[str] = None) -> str:
    ret_md = "# Show last %d lines of logs" % n
    for log_filename in ["streamlit.log", "daemon.log"]:
        ret_md += "## %s" % log_filename
        log_filename = os.path.join(C.LOGS.value, log_filename)
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
        ret_md += "\n```\n"
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


def get_all_score_users() -> list[int]:
    return _conn.query(
        """
        SELECT DISTINCT USER_ID
        FROM SCORE
        ORDER BY USER_ID""",
        ttl=0,
        show_spinner=_("querying the user list"),
    )["USER_ID"].to_list()


def cat(user: int):
    if user not in get_all_score_users():
        raise ValueError(_("user %d not found") % user)
    return get_scores_dataframe(user)


def register_commands(obj: Optional[dict] = None):
    ret = ""
    if obj is None:
        obj: dict = {}
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

            logger.get_logger("streamlit").info("generated token for session %s: %s" % (get_session_id(), st.session_state.token))
            ret = _("token generated")
            st.toast(_("You need to ask the web admin for the session token to unlock full features."))
    else:
        # 冗余设计
        pass
    st.session_state.cmdparser.register_command(st.session_state.perm, *commands())
    return ret


def get_scores_dataframe(user: int, date_range: Optional[tuple[date, date]] = None) -> pd.DataFrame:
    with _conn.session as s:
        if date_range is None:
            res = s.execute(
                text(
                    """
                    SELECT *
                    FROM SCORE
                    WHERE USER_ID = :user
                    ORDER BY TS""",
                ),
                params={"user": user},
            )
        else:
            begin_date_ts = datetime.combine(date_range[0], time.min).timestamp()
            end_date_ts = datetime.combine(date_range[1], time.max).timestamp()
            res = s.execute(
                text(
                    """
                    SELECT *
                    FROM SCORE
                    WHERE USER_ID = :user
                      AND TS >= :begin_date
                      AND TS <= :end_date
                    ORDER BY TS""",
                ),
                params={"user": user, "begin_date": begin_date_ts, "end_date": end_date_ts},
            )
        rows = res.fetchall()
    # 处理 bool 和 datetime
    completed_recent_scores_compact: dict[str, CompletedSimpleScoreInfo] = {
        str(row[0]): CompletedSimpleScoreInfo(
            # 基础字段
            row[1],
            row[2],
            row[3],
            row[4],
            row[5],
            bool(row[6]),
            row[7],
            orjson.loads(row[8]) if row[8] is not None else [],
            datetime.fromtimestamp(row[9]),
            (
                ScoreStatistics(**orjson.loads(row[10]))
                if row[10] is not None
                else ScoreStatistics(
                    miss=0,
                    meh=0,
                    ok=0,
                    good=0,
                    great=0,
                    perfect=None,
                    small_tick_hit=None,
                    large_tick_hit=None,
                    small_bonus=None,
                    large_bonus=None,
                    ignore_miss=None,
                    ignore_hit=None,
                    combo_break=None,
                    slider_tail_hit=None,
                )
            ),
            datetime.fromtimestamp(row[11]) if row[11] is not None else None,
            row[12],
            # 扩展字段
            row[13],
            row[14],
            row[15],
            row[16],
            row[17],
            bool(row[18]),
            bool(row[19]),
            bool(row[20]),
            bool(row[21]),
            bool(row[22]),
            bool(row[23]),
            bool(row[24]),
            row[25],
            row[26],
            row[27],
            row[28],
            row[29],
            row[30],
            row[31],
            row[32],
            row[33],
            row[34],
            row[35],
            row[36],
            row[37],
            row[38],
            row[39],
            row[40],
            row[41],
            row[42],
            row[43],
            row[44],
            row[45],
            row[46],
            row[47],
        )
        for row in rows
    }
    return st.session_state.awa.create_scores_dataframe(completed_recent_scores_compact)


def draw_strain_graph(bid: int, mod_settings: Optional[str] = None, ruleset_id: Optional[int] = None) -> Figure:
    beatmap: Beatmap = st.session_state.awa.run_coro(st.session_state.awa.api_beatmap(bid))
    match beatmap.mode:
        case GameMode.OSU:
            ruleset = OsuRuleset()
        case GameMode.TAIKO:
            ruleset = TaikoRuleset()
        case GameMode.CATCH:
            ruleset = CatchRuleset()
        case GameMode.MANIA:
            ruleset = ManiaRuleset()
    match ruleset_id:
        case 0:
            ruleset = OsuRuleset()
        case 1:
            ruleset = TaikoRuleset()
        case 2:
            ruleset = CatchRuleset()
        case 3:
            ruleset = ManiaRuleset()
        case None:  # 不使用转谱
            pass
        case _:
            raise ValueError(_("invalid ruleset_id"))
    download_osu(beatmap)

    if mod_settings is not None:
        mods = make_unstandardized_mods_from_lines("SP", mod_settings.replace(" ", "\n"))
        # 剔除 SP
        mods.remove({"acronym": "SP"})
    else:
        mods = []
    # 生成 osu_tools 所能接受的样式
    my_attr = SimpleDifficultyAttribute(beatmap.cs, beatmap.accuracy, beatmap.ar, beatmap.bpm or 0, beatmap.hit_length)
    my_attr.set_mods(mods)
    calculator = calculate_performance(os.path.join(C.BEATMAPS_CACHE_DIRECTORY.value, "%s.osu" % beatmap.id), ruleset, my_attr.osu_tool_mods, my_attr.osu_tool_mod_options)
    osupp_attr = next(calculator)
    strains: dict[str, list[tuple[float, float]]] = osupp_attr["__ek_strains"]
    timelines = osupp_attr["__ek_time_until_first_strain_adj"] + osupp_attr["__ek_ms_per_strain"] * np.arange(osupp_attr["__ek_strain_count"])
    df_strain = pd.DataFrame({**strains, "time": timelines})
    df_strain["time"] = pd.to_datetime(df_strain["time"], unit="ms")
    df_strain = df_strain.melt(id_vars="time", value_vars=strains.keys(), var_name="skill", value_name="strain")

    fig = px.line(df_strain, x="time", y="strain", color="skill", title="Difficulty Graph of %d" % beatmap.id, color_discrete_sequence=px.colors.qualitative.D3)
    fig.update_layout(
        xaxis_title="Start Time",
        yaxis_title="Strain",
    )
    for trace in fig.data:
        trace.fill = "tozeroy"
        # noinspection PyUnresolvedReferences
        trace.fillcolor = trace.line.color

    fig.update_traces(opacity=1)
    fig.update_xaxes(tickformat="%M:%S.%L")
    return fig


def query_all_sessions() -> pd.DataFrame:
    df = _conn.query("SELECT * FROM USER_CACHE WHERE USER_ID = %d" % st.session_state.user)
    df["LAST_SEEN_TS"] = cast(pd.Series, pd.to_datetime(df["LAST_SEEN_TS"], unit="s")).dt.tz_localize("UTC").dt.tz_convert(st.session_state.awa.tz)
    df.rename(columns={"AID": "ajs_anonymous_id", "LAST_SEEN_TS": "last_seen_datetime"}, inplace=True)
    df.drop(columns=["USER_ID", "USERNAME"], inplace=True)
    return df


def delete_user_cache(aid: str) -> None:
    with _conn.session as s:
        s.execute(
            text(
                "DELETE FROM USER_CACHE WHERE AID = :aid",
            ),
            params={"aid": aid},
        )
    if os.path.exists(os.path.join(C.OAUTH_TOKEN_DIRECTORY.value, "%s.pickle" % aid)):
        os.remove(os.path.join(C.OAUTH_TOKEN_DIRECTORY.value, "%s.pickle" % aid))


def invalidate_user_cache(user: int) -> None:
    with _conn.session as s:
        # 首先查询所有 aid，删除本地缓存的 token pickle
        res = s.execute(
            text(
                "SELECT AID FROM USER_CACHE WHERE USER_ID = :user",
            ),
            params={"user": user},
        )
        aids = [x[0] for x in res.fetchall()]
        for aid in aids:
            if os.path.exists(os.path.join(C.OAUTH_TOKEN_DIRECTORY.value, "%s.pickle" % aid)):
                os.remove(os.path.join(C.OAUTH_TOKEN_DIRECTORY.value, "%s.pickle" % aid))
        s.execute(
            text(
                "DELETE FROM USER_CACHE WHERE USER_ID = :user",
            ),
            params={"user": user},
        )


def update_user_cache(user: int, username: str, aid: str, last_seen_ts: float) -> None:
    with _conn.session as s:
        upsert_text = _build_upsert(
            st.secrets.connections.osuawa.get("dialect") or st.secrets.connections.osuawa.url.split("://")[0].split("+")[0],
            ["USER_ID", "USERNAME", "LAST_SEEN_TS"],
            ["AID"],
        )
        s.execute(
            text(
                """INSERT INTO USER_CACHE(USER_ID, USERNAME, AID, LAST_SEEN_TS)
                VALUES(:user, :username, :aid, :last_seen_ts)
                %s""" % upsert_text,
            ),
            params={"user": user, "username": username, "aid": aid, "last_seen_ts": last_seen_ts},
        )
        s.commit()


def push_task_with_session_state(task_command: str) -> str:
    _task_id = push_task(_r, task_command)
    st.session_state.redis_tasks.append(_task_id)
    save_value("redis_tasks")
    return "queued task: `%s`" % _task_id


def _mod_customization(key_suffix: int, ruleset: Literal["osu", "taiko", "catch", "mania"]) -> list[str]:
    match ruleset:
        case "osu":
            all_mods = osu_mod_entries
            all_mod_indexes = osu_mod_indexes
        case "taiko":
            all_mods = taiko_mod_entries
            all_mod_indexes = taiko_mod_indexes
        case "catch":
            all_mods = catch_mod_entries
            all_mod_indexes = catch_mod_indexes
        case "mania":
            all_mods = mania_mod_entries
            all_mod_indexes = mania_mod_indexes

    _mod_key = "modgen_mod_%d" % key_suffix
    ret = []
    with st.container(horizontal=True):
        memorized_selectbox(
            _("Mod"),
            _mod_key,
            [mod_entry["Acronym"] for mod_entry in all_mods],
            None,
            placeholder=_("Select a mod"),
            label_visibility="collapsed",
            format_func=lambda acronym: "%s %-4s - %s" % (get_mod_type_mapping(all_mod_indexes[acronym]["Type"], True), acronym, all_mod_indexes[acronym]["Name"]),
        )
        with st.container(gap="xxsmall"):
            if st.session_state[_mod_key] is not None:
                ret.append(st.session_state[_mod_key])
                _mod_entry = all_mod_indexes[st.session_state[_mod_key]]
                _settings = _mod_entry["Settings"]
                for _setting in _settings:
                    _name = _setting["Name"]
                    _type = _setting["Type"]
                    _mod_setting_key = "modgen_mod_%d_%s_%s" % (key_suffix, _name, _type)
                    _desc = _setting["Description"]
                    _enum_values = _setting["EnumValues"]
                    _default = _setting["Default"] or _setting["UnderlyingValue"]
                    if _default is None:
                        # 如果实在没有默认值，姑且根据 _type 决定默认值
                        match _type:
                            case "boolean":
                                _default = False
                            case "number":
                                _default = 0.0
                            case "string":
                                _default = ""
                            case "enum":
                                assert _enum_values is not None
                                _default = _enum_values[0]
                    _default = cast(Any, _default)  # 阻止类型推断
                    match _type:
                        case "boolean":
                            memorized_checkbox(_name, _mod_setting_key, _default, help=_desc)
                        case "number":
                            memorized_number_input(_name, _mod_setting_key, _default, help=_desc)
                        case "string":
                            memorized_text_input(_name, _mod_setting_key, _default, help=_desc)
                        case "enum":
                            assert _enum_values is not None
                            memorized_selectbox(_name, _mod_setting_key, _enum_values, _default, help=_desc)
                    # 如果选中值非不是默认值，才添加模组设置
                    if st.session_state[_mod_setting_key] != _default:
                        ret.append("%s_%s=%s" % (st.session_state[_mod_key], _name, st.session_state[_mod_setting_key]))
    return ret


def _add_mod():
    st.session_state.modgen_selected.append(st.session_state.modgen_increment)
    st.session_state.modgen_increment += 1


def _del_mod(suffix: int):
    st.session_state.modgen_selected.remove(suffix)


def _reset_mod():
    # 由于半持久化存储的存在，不能一直自增
    # 清空所有 modgen_ 开头的键
    for ss in st.session_state:
        if ss.startswith("modgen_"):
            del_value(ss)


@overload
def mods_generator(ret_type: Literal[0]) -> list[str]: ...


@overload
def mods_generator(ret_type: Literal[1]) -> list[dict[str, str | dict[str, str | float | bool]]]: ...


@overload
def mods_generator() -> None: ...


def mods_generator(ret_type: Optional[Literal[0, 1]] = None):
    ruleset = st.segmented_control(_("Ruleset"), options=["osu", "taiko", "catch", "mania"], key="modgen_ruleset", default="osu", width="stretch")
    lines: list[str] = []
    if "modgen_increment" not in st.session_state:
        st.session_state.modgen_increment = 0
    if "modgen_selected" not in st.session_state:
        # 这是一个整型列表，用于储存选中的模组索引
        st.session_state.modgen_selected = []
    if "modgen_ret" not in st.session_state:
        st.session_state.modgen_ret = deque(maxlen=1)
    st.write(st.session_state.modgen_increment)
    st.write(st.session_state.modgen_selected)

    for modgen_suffix in st.session_state.modgen_selected:
        col_content, col_del = st.columns([0.85, 0.15])
        with col_content:
            lines.extend(_mod_customization(modgen_suffix, cast(Literal["osu", "taiko", "catch", "mania"], ruleset)))
        with col_del:
            st.button(
                _("Delete"),
                key="modgen_del_%d" % modgen_suffix,
                type="primary",
                width="stretch",
                on_click=_del_mod,
                args=(modgen_suffix,),
            )

    col_add, col_reset, col_apply = st.columns(3)
    with col_add:
        st.button(_("Add"), on_click=_add_mod, width="stretch")
    with col_reset:
        st.button(_("Reset"), on_click=_reset_mod, width="stretch")
    with col_apply:
        apply = st.button(_("Apply to the form"), width="stretch")

    with st.expander(_("Preview")):
        st.code("\n".join(lines), language="properties")
        mods = make_unstandardized_mods_from_lines("SP", "\n".join(lines))
        mods.remove({"acronym": "SP"})
        st.json(mods)

    match ret_type:
        case 0:
            return lines
        case 1:
            return mods
        case _:
            if st.session_state.perm >= 1 and apply:
                st.session_state.modgen_ret.appendleft((lines, mods))
                st.rerun()
            return None


def tasks_grid(tasks: list[tuple[RedisTaskId, dict[str, str]]]):
    """以网格形式渲染任务"""

    status_color = {
        "pending": "#000000",
        "success": "darkseagreen",
        "error": "crimson",
    }

    for idx, (task_id, status_mapping) in enumerate(tasks, start=1):
        with st.container(border=True):
            status = status_mapping["status"]
            _result = orjson.loads(status_mapping["result"] or "{}")
            final = _result.get("final", "0")
            sub: list[str] = _result.get("sub", [])
            _time = status_mapping["time"]
            dt = datetime.fromtimestamp(float(_time), tz=ZoneInfo(st.session_state.awa.tz))
            status_color.get(status, "#808080")

            st.text(_("#%d: %s") % (idx, task_id))

            # 状态显示
            match status:
                case "pending":
                    st.spinner(_("pending..."))
                case "success":
                    st.json(sub, expanded=False)
                    st.success(_("%d sub-tasks done") % int(final))
                case "error":
                    st.json(sub, expanded=False)
                    st.error(final)
                case _:
                    st.json(_result, expanded=False)

            st.caption(f"updated at: {dt.strftime('%Y-%m-%d %H:%M:%S %Z%z')}")


def task_board():
    tasks_to_show: list[tuple[RedisTaskId, dict[str, str]]] = []
    for task_id in reversed(st.session_state.redis_tasks):
        status_key = C.TASK_STATUS.value.format(task_id=task_id)
        status_mapping: Optional[dict] = cast(Optional[dict], _r.hgetall(status_key))
        if status_mapping:
            tasks_to_show.append((task_id, status_mapping))

    # 使用 tabs 分类显示
    tab1, tab2, tab3, tab4 = st.tabs([":material/format_list_bulleted: all", ":material/pending: pending", ":material/check_circle: success", ":material/error: error"])
    with tab1:
        tasks_grid(tasks_to_show)
    with tab2:
        tasks_grid([(task_id, status_mapping) for task_id, status_mapping in tasks_to_show if status_mapping.get("status") == "pending"])
    with tab3:
        tasks_grid([(task_id, status_mapping) for task_id, status_mapping in tasks_to_show if status_mapping.get("status") == "success"])
    with tab4:
        tasks_grid([(task_id, status_mapping) for task_id, status_mapping in tasks_to_show if status_mapping.get("status") == "error"])
