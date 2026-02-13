import asyncio
import os
import os.path
import re
import shelve
import shutil
from collections import deque
from dataclasses import asdict
from datetime import date, datetime, time
from secrets import token_hex
from shutil import copyfile
from typing import Any, Literal, Optional, TYPE_CHECKING
from uuid import UUID

import orjson
import pandas as pd
import plotly.express as px
import streamlit as st
from clayutil.cmdparse import (
    BoolField as Bool,
    CollectionField as Coll,
    Command,
    CommandError,
    IntegerField as Int,
    JSONStringField as JsonStr,
    StringField as Str,
)
from ossapi import Beatmap, GameMode, Score
from osupp.performance import calculate_osu_performance
from plotly.graph_objs import Figure
from sqlalchemy import text
from streamlit import logger
from streamlit.runtime.scriptrunner import get_script_run_ctx

from osuawa import C, OsuPlaylist, Osuawa
from osuawa.utils import CompletedSimpleScoreInfo, SimpleOsuDifficultyAttribute, SimpleScoreInfo, _make_query_uppercase, async_get_username, download_osu, format_size, generate_mods_from_lines, get_size_and_count

if TYPE_CHECKING:

    def _(text: str) -> str: ...


_conn = st.connection("osuawa", type="sql", ttl=60)
_conn.query = _make_query_uppercase(_conn.query)


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
            save_recent_scores,
        ),
        Command(
            "update",
            _("update user recent scores"),
            [
                Coll(
                    "user",
                    get_all_score_users(),
                ),
            ],
            1,
            save_recent_scores,
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
        Command("prev", _("draw strain graph of an osu! standard beatmap"), [Int("beatmap"), Str("mod_settings", True)], 0, draw_strain_graph),
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


def get_all_score_users() -> list[int]:
    return _conn.query(
        """
        SELECT DISTINCT USER_ID
        FROM SCORE
        ORDER BY USER_ID""",
        ttl=0,
    )["USER_ID"].to_list()


def cat(user: int):
    if user not in get_all_score_users():
        raise ValueError(_("user %d not found") % user)
    return get_scores_dataframe(user)


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


def save_recent_scores(user: int, include_fails: bool = True) -> str:
    async def _save_recent_scores(_user: int, _include_fails: bool) -> tuple[str, dict[str, CompletedSimpleScoreInfo]]:
        """返回 (username, completed_recent_scores_compact)"""
        user_scores: list[Score] = await st.session_state.awa.async_get_recent_scores(_user, _include_fails)
        recent_scores_compact: dict[str, SimpleScoreInfo] = {str(user_score.id): SimpleScoreInfo.from_score(user_score) for user_score in user_scores}
        return await asyncio.gather(
            async_get_username(st.session_state.awa.api, _user),
            st.session_state.awa.complete_scores_compact(recent_scores_compact),
        )

    username: str
    completed_recent_scores_compact: dict[str, CompletedSimpleScoreInfo]
    username, completed_recent_scores_compact = st.session_state.awa.run_coro(_save_recent_scores(user, include_fails))
    with _conn.session as s:
        # 插入到表 SCORE，如果遇到冲突，则放弃
        # 准备数据
        scores = []
        for pk, _v in completed_recent_scores_compact.items():
            score = asdict(
                _v,
                dict_factory=lambda items: {k.lstrip("_"): None if v is None else v.timestamp() if isinstance(v, datetime) else int(v) if isinstance(v, bool) else orjson.dumps(v).decode("utf-8") if isinstance(v, (list, dict)) else v for k, v in items},
            )
            score["score_id"] = pk
            scores.append(score)
        res = s.execute(
            text(
                """INSERT INTO SCORE (SCORE_ID, BID, USER_ID, SCORE, ACCURACY, MAX_COMBO, PASSED, PP, MODS, TS, STATISTICS, ST, CS, HIT_WINDOW, PREEMPT, BPM, HIT_LENGTH, IS_NF, IS_HD, IS_HIGH_AR, IS_LOW_AR, IS_VERY_LOW_AR, IS_SPEED_UP, IS_SPEED_DOWN,
                                      INFO, ORIGINAL_DIFFICULTY, B_STAR_RATING, B_MAX_COMBO, B_AIM_DIFFICULTY, B_AIM_DIFFICULT_SLIDER_COUNT, B_SPEED_DIFFICULTY, B_SPEED_NOTE_COUNT, B_SLIDER_FACTOR, B_AIM_TOP_WEIGHTED_SLIDER_FACTOR,
                                      B_SPEED_TOP_WEIGHTED_SLIDER_FACTOR, B_AIM_DIFFICULT_STRAIN_COUNT, B_SPEED_DIFFICULT_STRAIN_COUNT, PP_AIM, PP_SPEED, PP_ACCURACY, B_PP_100IF_AIM, B_PP_100IF_SPEED, B_PP_100IF_ACCURACY, B_PP_100IF, B_PP_92IF,
                                      B_PP_81IF, B_PP_67IF)
                   VALUES (:score_id, :bid, :user, :score, :accuracy, :max_combo, :passed, :pp, :mods, :ts, :statistics, :st, :cs, :hit_window, :preempt, :bpm, :hit_length, :is_nf, :is_hd, :is_high_ar, :is_low_ar, :is_very_low_ar, :is_speed_up,
                           :is_speed_down, :info, :original_difficulty, :b_star_rating, :b_max_combo, :b_aim_difficulty, :b_aim_difficult_slider_count, :b_speed_difficulty, :b_speed_note_count, :b_slider_factor, :b_aim_top_weighted_slider_factor,
                           :b_speed_top_weighted_slider_factor, :b_aim_difficult_strain_count, :b_speed_difficult_strain_count, :pp_aim, :pp_speed, :pp_accuracy, :b_pp_100if_aim, :b_pp_100if_speed, :b_pp_100if_accuracy, :b_pp_100if, :b_pp_92if,
                           :b_pp_81if, :b_pp_67if)
                   ON CONFLICT DO NOTHING;""",
            ),
            params=scores,
        )

        len_diff = res.rowcount
        s.commit()
    return "%s: got/diff: %d/%d" % (
        username,
        len(completed_recent_scores_compact),
        len_diff,
    )


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
            orjson.loads(row[10]) if row[10] is not None else {},
            datetime.fromtimestamp(row[11]) if row[11] is not None else None,
            # 扩展字段
            row[12],
            row[13],
            row[14],
            row[15],
            row[16],
            bool(row[17]),
            bool(row[18]),
            bool(row[19]),
            bool(row[20]),
            bool(row[21]),
            bool(row[22]),
            bool(row[23]),
            row[24],
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
        )
        for row in rows
    }
    return st.session_state.awa.create_scores_dataframe(completed_recent_scores_compact)


def draw_strain_graph(bid: int, mod_settings: Optional[str] = None) -> Figure:
    beatmap: Beatmap = st.session_state.awa.run_coro(st.session_state.awa.api.beatmap(bid))
    if beatmap.mode != GameMode.OSU:
        raise CommandError(_("only osu! standard beatmap supported"))
    download_osu(beatmap)

    if mod_settings is not None:
        mods = generate_mods_from_lines("SP", mod_settings.replace(" ", "\n"))
        # 剔除 SP
        mods.remove({"acronym": "SP"})
    else:
        mods = []
    # 生成 osu_tools 所能接受的样式
    my_attr = SimpleOsuDifficultyAttribute(beatmap.cs, beatmap.accuracy, beatmap.ar, beatmap.bpm, beatmap.hit_length)
    my_attr.set_mods(mods)
    calculator = calculate_osu_performance(os.path.join(C.BEATMAPS_CACHE_DIRECTORY.value, "%s.osu" % beatmap.id), my_attr.osu_tool_mods, my_attr.osu_tool_mod_options)
    osupp_attr = next(calculator)
    aim_strain_timeline: list[tuple[float, float]] = osupp_attr["__ek_aim_strain_timeline"]
    speed_strain_timeline: list[tuple[float, float]] = osupp_attr["__ek_speed_strain_timeline"]
    # strain_timeline: [(time, strain)]
    # 二者的 time 理论上都是一样的，选择一个即可
    df_strain = pd.DataFrame(
        {
            "time": [x[0] for x in aim_strain_timeline],
            "aim": [x[1] for x in aim_strain_timeline],
            "speed": [x[1] for x in speed_strain_timeline],
        },
    )

    # 横坐标为 time，纵坐标为 strain，在一张折线图中同时绘制 aim strain 和 speed strain
    fig = px.line(df_strain, x="time", y=["aim", "speed"], title="Difficulty Graph of %d" % beatmap.id)
    fig.update_layout(
        xaxis_title="Start Time",
        yaxis_title="Strain",
    )
    return fig
