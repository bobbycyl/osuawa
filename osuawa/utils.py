"""
osuawa.py and utils.py should not contain i18n related text and streamlit related statement
"""

import asyncio
import contextlib
import os
import re
import uuid
from dataclasses import dataclass, fields
from datetime import datetime
from enum import Enum, unique
from math import log10, sqrt
from random import shuffle
from threading import BoundedSemaphore
from time import sleep, time, time_ns
from typing import Any, NamedTuple, NewType, Optional, TypedDict, Union, cast, get_args, get_origin

import numpy as np
import orjson
import pandas as pd
import typing_extensions
from clayutil.futil import Downloader, Properties
from clayutil.sutil import md5sum
from ossapi import Beatmap, OssapiAsync, Score, User, UserCompact  # 避免与 rosu.Beatmap、slider.Beatmap 冲突
from osupp.core import init_osu_tools
from redis import Redis

init_osu_tools(os.path.join(str(os.path.dirname(__file__)), "..", "osu-tools", "PerformanceCalculator", "bin", "Release", "net8.0"))
from osupp.core import OsuRuleset

# noinspection PyUnusedImports
from osupp.difficulty import calculate_difficulty as calculate_difficulty, get_all_mods
from osupp.performance import OsuPerformance, calculate_osu_performance

headers = {
    "Referer": "https://bobbycyl.github.io/playlists/",
    "User-Agent": "osuawa",
}
LANGUAGES = ["en_US", "zh_CN"]
all_osu_mods = {mod_info["Acronym"]: dict((s["Name"], s["Type"]) for s in mod_info["Settings"]) for mod_info in get_all_mods(OsuRuleset())}
sem = BoundedSemaphore()

TYPE_MAPPING: dict[type, str] = {
    int: "INT",
    float: "REAL",
    bool: "INT",
    str: "TEXT",
    bytes: "BLOB",
}

assets_dir: str = os.path.dirname(__file__)


@unique
class C(Enum):
    LOGS = "./logs/"
    LOCALE = "./share/locale/"
    OUTPUT_DIRECTORY = "./output/"
    STATIC_DIRECTORY = "./static/"
    UPLOADED_DIRECTORY = "./static/uploaded/"
    BEATMAPS_CACHE_DIRECTORY = "./static/beatmaps/"

    OAUTH_TOKEN_DIRECTORY = "./.streamlit/.oauth/"
    COMPONENTS_SHELVES_DIRECTORY = "./.streamlit/.components/"

    TASK_QUEUE = "awatasks:queue"
    TASK_STATUS = "awatask:status:{task_id}"

    SLOT_MAX_LEN = 5


@unique
class ColorBar(Enum):
    # https://github.com/ppy/osu/blob/master/osu.Game/Graphics/OsuColour.cs
    XP = [0.1, 1.25, 2.0, 2.5, 3.3, 4.2, 4.9, 5.8, 6.7, 7.7, 9.0]
    YP_R = [66, 79, 79, 124, 246, 255, 255, 198, 101, 24, 0]
    YP_G = [144, 192, 255, 255, 240, 128, 78, 69, 99, 21, 0]
    YP_B = [251, 255, 213, 79, 92, 104, 111, 184, 222, 142, 0]


@unique
class ColorTextBar(Enum):
    # https://github.com/ppy/osu/blob/master/osu.Game/Graphics/OsuColour.cs
    XP = [9.0, 9.9, 10.6, 11.5, 12.4]
    YP_R = [246, 255, 255, 198, 101]
    YP_G = [240, 128, 78, 69, 99]
    YP_B = [92, 104, 111, 185, 222]


def read_injected_code(filename: str) -> str:
    """
    读 inject 目录下的注入代码

    如果是 css，返回 <style> 标签包裹的 css 内容
    如果是 js 且顶层为函数，要求箭头函数，参数名为 params 或 event

    :param filename: inject 文件名
    :return: 文件内容
    """
    _path = os.path.join(assets_dir, "inject", filename)
    match os.path.splitext(filename)[1]:
        case ".css":
            with open(_path, "r", encoding="utf-8") as fi:
                return f"<style>{fi.read()}</style>"
        case ".js":
            with open(_path, "r", encoding="utf-8") as fi:
                first_line = fi.readline().strip()
                if first_line == "(params) => {":
                    first_line = "function(params) {"
                elif first_line == "(event) => {":
                    first_line = "function(event) {"
                return first_line + "\n" + fi.read()
        case _:
            raise ValueError("unsupported injected code type: %s" % filename)


def strip_quotes(text: str) -> str:
    # 判断是否被引号包裹，若是，则 strip
    if text.startswith('"') and text.endswith('"'):
        return text.strip('"')
    if text.startswith("'") and text.endswith("'"):
        return text.strip("'")
    return text


def create_unique_picker[_T](items: list[_T]):
    pool = items.copy()
    shuffle(pool)
    cursor = 0
    last_item = None

    def picker() -> _T:
        nonlocal cursor, last_item
        if cursor >= len(pool):
            cursor = 0
            shuffle(pool)
            # pool[0] 有可能是上一轮的 pool[-1]
            if len(pool) > 1 and pool[0] == last_item:
                pool[0], pool[1] = pool[1], pool[0]
        picked = pool[cursor]
        last_item = picked
        cursor += 1
        return picked

    return picker


def get_simple_sql_type(py_type: type) -> str:
    # 处理 Optional (例如 Optional[int] 或 Union[int, None])
    if get_origin(py_type) is Union:
        # 获取 Union 里的参数列表
        args = get_args(py_type)
        # 遍历找到 NoneType 以外的那个类型
        for arg in args:
            if arg is not type(None):
                py_type = arg
                break

    # 查表，默认 TEXT
    return TYPE_MAPPING.get(py_type, "TEXT")


def generate_columns_sql(dataclass_cls, name_mapping: Optional[dict] = None):
    parts = []
    for f in fields(dataclass_cls):
        sql_type = get_simple_sql_type(cast(type, f.type))
        if name_mapping and f.name in name_mapping:
            parts.append(f"{name_mapping[f.name]} {sql_type}")
        else:
            parts.append(f"{f.name.upper()} {sql_type}")
    return ", ".join(parts)


def to_readable_mods(mods: list[dict[str, Any]]) -> list[str]:
    readable_mods: list[str] = []
    for i in range(len(mods)):
        if "settings" in mods[i]:
            settings_str = []
            for setting_name, setting_value in mods[i]["settings"].items():
                if isinstance(setting_value, bool):
                    setting_value = "true" if setting_value else "false"
                settings_str.append("%s=%s" % (setting_name, setting_value))
            readable_mods.append("%s(%s)" % (mods[i]["acronym"], ",".join(settings_str)))
        else:
            readable_mods.append(mods[i]["acronym"])
    return readable_mods


def calc_bin_size(data) -> float:
    return (max(data) - min(data)) / min((sqrt(len(data)), 10 * log10(len(data))))


async def simple_user_dict(user: User | UserCompact) -> dict[str, Any]:
    # 注：虽然这里目前没有任何需要用到 asyncio 的地方，但是曾经存在过，并且未来可能扩充，因此保留 async
    # todo: 完善 simple_user_dict 所包含的信息
    base_dict = {
        "username": user.username,
        "user_id": user.id,
        "country": user.country,
        "online": user.is_online,
        "supporter": user.is_supporter,
        "team": user.team,
    }
    match user_statistics := user.statistics:
        case None:
            return base_dict
        case _:
            return {
                **base_dict,
                "level": user_statistics.level,
                "pp": user_statistics.pp,
                "global_rank": user_statistics.global_rank,
                "country_rank": user_statistics.country_rank,
                "play_count": user_statistics.play_count,
                "play_time": user_statistics.play_time,
                "ranked_score": user_statistics.ranked_score,
                "total_hits": user_statistics.total_hits,
                "total_score": user_statistics.total_score,
            }


async def async_get_user_info(api: OssapiAsync, user: int | str) -> dict[str, Any]:
    return await simple_user_dict(await api.user(user, key="username" if isinstance(user, str) else "id"))


async def async_get_username(api: OssapiAsync, user: int) -> str:
    return (await api.user(user, key="id")).username


async def async_get_beatmaps_dict(api: OssapiAsync, bids: list[int]) -> dict[int, Beatmap]:
    bids = list(set(bids))
    cut_bids: list[list[int]] = []
    for i in range(0, len(bids), 50):
        cut_bids.append(list(bids[i : i + 50]))
    tasks = []
    async with asyncio.TaskGroup() as tg:
        for bids in cut_bids:
            tasks.append(tg.create_task(api.beatmaps(bids)))
    results = [task.result() for task in tasks]
    return {b.id: b for bs in results for b in bs}


def calc_hit_window(original_accuracy: float, magnitude: float = 1.0) -> float:
    hit_window = 80.0 - 6.0 * original_accuracy
    return hit_window / magnitude


def calc_accuracy(hit_window: float) -> float:
    return (80.0 - hit_window) / 6.0


def calc_preempt(original_ar: float, magnitude: float = 1.0) -> float:
    preempt = 1200.0 + 600.0 * (5.0 - original_ar) / 5.0 if original_ar < 5.0 else 1200.0 - 750 * (original_ar - 5.0) / 5.0
    return preempt / magnitude


def calc_ar(preempt: float) -> float:
    return 5.0 - (preempt - 1200.0) / 600 * 5.0 if preempt > 1200.0 else 5.0 + (1200.0 - preempt) / 750 * 5.0


class SimpleOsuDifficultyAttribute(object):

    def __init__(self, cs: float, accuracy: float, ar: float, bpm: float, hit_length: int):
        self.cs = cs
        self.accuracy = accuracy
        self.hit_window = calc_hit_window(self.accuracy)
        self.ar = ar
        self.preempt = calc_preempt(self.ar)
        self.bpm = bpm
        self.hit_length = hit_length
        self.magnitude = 1.0
        self.is_nf = False
        self.is_hd = False
        self.is_high_ar = False
        self.is_low_ar = False
        self.is_very_low_ar = False
        self.is_speed_up = False
        self.is_speed_down = False
        self.osu_tool_mods: list[str] = []  # [acronym]
        self.osu_tool_mod_options: list[str] = []  # [acronym_setting_name=setting_value]

    def set_mods(self, mods: list):
        mods_dict = {}  # {acronym, settings}
        for mod in mods:
            acronym = mod["acronym"]
            if acronym not in all_osu_mods:
                raise ValueError("unknown mod '%s'" % acronym)
            _settings = mod.get("settings", {})
            mods_dict[acronym] = _settings
            self.osu_tool_mods.append(acronym)
            for setting_name, setting_value in _settings.items():
                if setting_name not in all_osu_mods[acronym]:
                    raise ValueError("unknown setting '%s' for mod '%s'" % (setting_name, acronym))
                expected_type: type[str | float | bool] = all_osu_mods[acronym][setting_name]
                if not isinstance(setting_value, expected_type):
                    raise ValueError(
                        "setting '%s' for mod '%s' should be of type '%s' (got '%s')"
                        % (
                            setting_name,
                            acronym,
                            expected_type.__name__,
                            type(setting_value).__name__,
                        ),
                    )
                if expected_type is bool:
                    setting_value = "true" if setting_value else "false"
                self.osu_tool_mod_options.append("%s_%s=%s" % (mod["acronym"], setting_name, setting_value))
        if "NF" in mods_dict:
            self.is_nf = True
        if "HD" in mods_dict:
            self.is_hd = True
        if "HR" in mods_dict:
            self.cs = min(self.cs * 1.3, 10.0)
            self.accuracy = min(self.accuracy * 1.4, 10.0)
            self.ar = min(self.ar * 1.4, 10.0)
        elif "EZ" in mods_dict:
            self.cs = self.cs * 0.5
            self.accuracy = self.accuracy * 0.5
            self.ar = self.ar * 0.5
        elif "DA" in mods_dict:
            self.cs = mods_dict["DA"].get("circle_size", self.cs)
            self.accuracy = mods_dict["DA"].get("overall_difficulty", self.accuracy)
            self.ar = mods_dict["DA"].get("approach_rate", self.ar)
        if "DT" in mods_dict:
            self.magnitude = mods_dict["DT"].get("speed_change", 1.5)
        elif "NC" in mods_dict:
            self.magnitude = mods_dict["NC"].get("speed_change", 1.5)
        elif "HT" in mods_dict:
            self.magnitude = mods_dict["HT"].get("speed_change", 0.75)
        elif "DC" in mods_dict:
            self.magnitude = mods_dict["DC"].get("speed_change", 0.75)
        elif "WU" in mods_dict:
            _settings = mods_dict["WU"]
            self.magnitude = 2.0 / (_settings.get("initial_rate", 1.0) + _settings.get("final_rate", 1.5))
        elif "WD" in mods_dict:
            _settings = mods_dict["WD"]
            self.magnitude = 2.0 / (_settings.get("initial_rate", 1.0) + _settings.get("final_rate", 0.75))
        if self.magnitude > 1.0:
            self.is_speed_up = True
        elif self.magnitude < 1.0:
            self.is_speed_down = True
        self.hit_window = calc_hit_window(self.accuracy, self.magnitude)
        self.accuracy = calc_accuracy(self.hit_window)
        self.preempt = calc_preempt(self.ar, self.magnitude)
        self.ar = calc_ar(self.preempt)
        if self.preempt <= 450.0:
            self.is_high_ar = True
        elif 675.0 <= self.preempt < 900.0:
            self.is_low_ar = True
        elif self.preempt >= 900.0:
            self.is_very_low_ar = True
        self.bpm *= self.magnitude
        self.hit_length = round(self.hit_length / self.magnitude)


@dataclass(slots=True)
class SimpleScoreInfo(object):
    """
    从在线成绩简化而来，只保留感兴趣的字段，json 里保存这些基本字段

    这里的 pp 在本地计算后应被覆盖
    """

    bid: int
    user: int
    score: int
    accuracy: float
    max_combo: int
    passed: bool
    pp: Optional[float]
    _mods: list[dict[str, str] | dict[str, dict[str, str]]]
    ts: datetime
    statistics: dict[str, Optional[int]]
    st: Optional[datetime]

    @classmethod
    def from_score(cls, score: Score):
        return cls(
            score.beatmap_id,
            score.user_id,
            score.total_score,
            score.accuracy,
            score.max_combo,
            score.passed,
            score.pp,
            [({"acronym": mod.acronym, "settings": mod.settings} if mod.settings else {"acronym": mod.acronym}) for mod in score.mods],
            score.ended_at,
            {
                "large_tick_hit": score.statistics.large_tick_hit,
                "slider_tail_hit": score.statistics.slider_tail_hit,
                # 为了数据美观，300/100/50/0 的值如果为 None，则设置为 0
                "great": score.statistics.great or 0,
                "ok": score.statistics.ok or 0,
                "meh": score.statistics.meh or 0,
                "miss": score.statistics.miss or 0,
            },
            score.started_at,
        )


@dataclass(slots=True)
class CompletedSimpleScoreInfo(SimpleScoreInfo):
    """
    用于记录获取完在线成绩后，需要本地补充计算的谱面、模组、成绩相关字段，parquet 里存储的就是这些字段，在必要的时候才需要重算

    ⚠ 父类中的 pp 在重算时也需要考虑 ⚠

    ``calc_beatmap_attributes`` 应该能够妥善处理这些情况
    """

    cs: float
    hit_window: float
    preempt: float
    bpm: float
    hit_length: int
    is_nf: bool
    is_hd: bool
    is_high_ar: bool
    is_low_ar: bool
    is_very_low_ar: bool
    is_speed_up: bool
    is_speed_down: bool
    info: str
    original_difficulty: float
    b_star_rating: float
    b_max_combo: int
    b_aim_difficulty: Optional[float]
    b_aim_difficult_slider_count: Optional[float]
    b_speed_difficulty: Optional[float]
    b_speed_note_count: Optional[float]
    b_slider_factor: Optional[float]
    b_aim_top_weighted_slider_factor: Optional[float]
    b_speed_top_weighted_slider_factor: Optional[float]
    b_aim_difficult_strain_count: Optional[float]
    b_speed_difficult_strain_count: Optional[float]
    pp_aim: Optional[float]
    pp_speed: Optional[float]
    pp_accuracy: Optional[float]
    b_pp_100if_aim: Optional[float]
    b_pp_100if_speed: Optional[float]
    b_pp_100if_accuracy: Optional[float]
    b_pp_100if: float
    b_pp_92if: float
    b_pp_81if: float
    b_pp_67if: float


@dataclass(slots=True)
class ExtendedSimpleScoreInfo(CompletedSimpleScoreInfo):
    """用于记录在生成 DataFrame 后计算的字段，使用向量化加速批量计算是个好选择

    所有新增的参数都可以追加到这里

    修改 ``Osuawa.calculate_extended_scores_dataframe_with_timezone`` 以匹配这些内容，或对父类字段进行二次处理（如时区显示等）
    """

    time: int
    pp_pct: Optional[float]
    pp_aim_pct: Optional[float]
    pp_speed_pct: Optional[float]
    pp_accuracy_pct: Optional[float]
    pp_92pct: Optional[float]
    pp_81pct: Optional[float]
    pp_67pct: Optional[float]
    combo_pct: Optional[float]
    density: Optional[float]
    aim_density_ratio: Optional[float]
    speed_density_ratio: Optional[float]
    aim_speed_ratio: Optional[float]
    score_nf: int
    mods: str
    only_common_mods: bool


# noinspection PyTypedDict
class ParsedPlaylistBeatmap(typing_extensions.TypedDict, total=False, extra_items=Any):
    bid: int
    mods: list[dict[str, Any]]
    notes: str
    beatmap: Beatmap


# noinspection PyArgumentList
CompletedPlaylistBeatmap = typing_extensions.TypedDict(
    "CompletedPlaylistBeatmap",
    {
        "#": int,
        "BID": int,
        "SID": int,
        "Beatmap Info (Click to View)": str,
        "Artist - Title (Creator) [Version]": str,
        "Stars": str,
        "SR": str,
        "BPM": str,
        "Hit Length": str,
        "Max Combo": str,
        "CS": str,
        "AR": str,
        "OD": str,
        "Mods": str,
        "Notes": str,
        "_Artist": str,
        "_Title": str,
    },
    total=False,
    extra_items=str,
)


class DatabasePlaylistBeatmap(TypedDict):
    BID: int
    SID: int
    INFO: str
    SKILL_SLOT: str
    SR: str
    BPM: str
    HIT_LENGTH: str
    MAX_COMBO: str
    CS: str
    AR: str
    OD: str
    MODS: str
    NOTES: str
    STATUS: int
    COMMENTS: str
    POOL: str
    SUGGESTOR: str
    RAW_MODS: str
    ADD_TS: float
    U_ARTIST: str
    U_TITLE: str


class BeatmapSpec(NamedTuple):
    bid: int
    raw_mods: list[dict[str, str | dict[str, str | float | bool]]]
    slot: str
    pool: str
    notes: str
    status: int
    comments: str
    suggestor: str
    add_ts: float


class BeatmapToUpdate(TypedDict, total=False):
    """
    Attributes:
        beatmap: 欲更新的谱面
        old_bid: 欲删除 BID
        old_mods: 欲删除 MODS
    """

    name: str
    beatmap: Optional[BeatmapSpec]
    old_bid: Optional[int]
    old_mods: Optional[str]


RedisTaskId = NewType("RedisTaskId", str)


def push_task(r: Redis, task_command: str) -> RedisTaskId:
    task_id = uuid.uuid4().hex
    r.lpush(C.TASK_QUEUE.value, "%s%s" % (task_id, task_command))
    r.hset(
        C.TASK_STATUS.value.format(task_id=task_id),
        mapping={
            "status": "pending",
            "result": "",
            "time": time(),
        },
    )
    return RedisTaskId(task_id)


def download_osu(beatmap: Beatmap):
    need_download = False
    if not os.path.exists(os.path.join(C.BEATMAPS_CACHE_DIRECTORY.value, "%s.osu" % beatmap.id)):
        need_download = True
    else:
        with open(os.path.join(C.BEATMAPS_CACHE_DIRECTORY.value, "%s.osu" % beatmap.id), "rb") as fi_b:
            if beatmap.checksum != md5sum(fi_b.read()):
                need_download = True
    if need_download:
        with sem:
            sleep(1)
            Downloader(C.BEATMAPS_CACHE_DIRECTORY.value).start("https://osu.ppy.sh/osu/%d" % beatmap.id, "%s.osu" % beatmap.id, headers)
            sleep(0.5)


def calc_beatmap_attributes(beatmap: Beatmap, score: SimpleScoreInfo) -> CompletedSimpleScoreInfo:
    """完整计算所需属性，这会覆盖 score 原本的 pp"""
    my_attr = SimpleOsuDifficultyAttribute(beatmap.cs, beatmap.accuracy, beatmap.ar, beatmap.bpm or 0, beatmap.hit_length)
    my_attr.set_mods(score._mods)
    download_osu(beatmap)
    calculator = calculate_osu_performance(
        beatmap_path=os.path.join(C.BEATMAPS_CACHE_DIRECTORY.value, "%s.osu" % beatmap.id),
        mods=my_attr.osu_tool_mods,
        mod_options=my_attr.osu_tool_mod_options,
    )
    osupp_attr = next(calculator)
    perf_got_attr = calculator.send(
        OsuPerformance(
            combo=score.max_combo,
            misses=score.statistics.get("miss") or 0,
            mehs=score.statistics.get("meh"),
            oks=score.statistics.get("ok"),
            large_tick_hits=score.statistics.get("large_tick_hit"),
            slider_tail_hits=score.statistics.get("slider_tail_hit"),
        ),
    )
    perf100_attr = calculator.send(OsuPerformance())
    pp92 = calculator.send(OsuPerformance(accuracy_percent=92.0))["pp"]
    pp81 = calculator.send(OsuPerformance(accuracy_percent=81.0))["pp"]
    pp67 = calculator.send(OsuPerformance(accuracy_percent=67.0))["pp"]
    pp_got = perf_got_attr["pp"]
    pp_got_aim = perf_got_attr["aim"]
    pp_got_speed = perf_got_attr["speed"]
    pp_got_accuracy = perf_got_attr["accuracy"]
    pp100 = perf100_attr["pp"]
    pp100_aim = perf100_attr["aim"]
    pp100_speed = perf100_attr["speed"]
    pp100_accuracy = perf100_attr["accuracy"]

    return CompletedSimpleScoreInfo(
        # 父类字段，除了 pp 全部照抄
        score.bid,
        score.user,
        score.score,
        score.accuracy,
        score.max_combo,
        score.passed,
        pp_got,  # 使用本地计算的 pp
        score._mods,
        score.ts,
        score.statistics,
        score.st,
        # 追加字段
        my_attr.cs,
        my_attr.hit_window,
        my_attr.preempt,
        my_attr.bpm,
        my_attr.hit_length,
        my_attr.is_nf,
        my_attr.is_hd,
        my_attr.is_high_ar,
        my_attr.is_low_ar,
        my_attr.is_very_low_ar,
        my_attr.is_speed_up,
        my_attr.is_speed_down,
        "%s - %s (%s) [%s]"
        % (
            beatmap.beatmapset().artist,
            beatmap.beatmapset().title,
            beatmap.beatmapset().creator,
            beatmap.version,
        ),
        beatmap.difficulty_rating,
        osupp_attr["star_rating"],
        osupp_attr["max_combo"],
        osupp_attr["aim_difficulty"],
        osupp_attr["aim_difficult_slider_count"],
        osupp_attr["speed_difficulty"],
        osupp_attr["speed_note_count"],
        osupp_attr["slider_factor"],
        osupp_attr["aim_top_weighted_slider_factor"],
        osupp_attr["speed_top_weighted_slider_factor"],
        osupp_attr["aim_difficult_strain_count"],
        osupp_attr["speed_difficult_strain_count"],
        pp_got_aim,
        pp_got_speed,
        pp_got_accuracy,
        pp100_aim,
        pp100_speed,
        pp100_accuracy,
        pp100,
        pp92,
        pp81,
        pp67,
    )


def calc_positive_percent(score: int | float | None, min_score: int | float, max_score: int | float) -> int:
    if score is None:
        score: float = 0.0
    score_pct = int((score - min_score) / (max_score - min_score) * 100.0)
    if score_pct > 100:
        score_pct = 100
    elif score_pct < 0:
        score_pct = 0
    return score_pct


def calc_star_rating_color(stars: float) -> str:
    if stars < ColorBar.XP.value[0]:
        return "#aaaaaa"
    elif stars > ColorBar.XP.value[-1]:
        return "#000000"
    else:
        interp_r = np.interp(stars, ColorBar.XP.value, ColorBar.YP_R.value)
        interp_g = np.interp(stars, ColorBar.XP.value, ColorBar.YP_G.value)
        interp_b = np.interp(stars, ColorBar.XP.value, ColorBar.YP_B.value)
        return "#%02x%02x%02x" % (int(interp_r), int(interp_g), int(interp_b))


def calc_high_star_rating_text_color(stars: float, new_style: bool = True) -> str:
    if stars < 6.5:
        raise ValueError("stars must be at least 6.5")
    elif stars < 9.0:
        # return "#f0dd55"
        return "#f6f05c"
    else:
        interp_r = np.interp(stars, ColorTextBar.XP.value, ColorTextBar.YP_R.value)
        interp_g = np.interp(stars, ColorTextBar.XP.value, ColorTextBar.YP_G.value)
        interp_b = np.interp(stars, ColorTextBar.XP.value, ColorTextBar.YP_B.value)
        return "#%02x%02x%02x" % (int(interp_r), int(interp_g), int(interp_b))


def get_size_and_count(path):
    """获取文件或目录的总大小和文件总数"""
    if os.path.isfile(path):
        return os.path.getsize(path), 1
    elif os.path.isdir(path):
        total_size = 0
        total_count = 0
        for root, _dirs, filenames in os.walk(path):
            for filename in filenames:
                filepath = os.path.join(str(root), str(filename))
                total_size += os.path.getsize(filepath)
                total_count += 1
        return total_size, total_count
    return 0, 0


def format_size(size_bytes):
    for unit in ["B", "KiB", "MiB", "GiB", "TiB"]:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} PiB"


def regex_search_column(data: pd.DataFrame, column: str, pattern: str):
    """对某一列进行正则搜索，有匹配则输出匹配内容，无匹配输出 None"""

    def search_func(text):
        if pd.isna(text):
            return None
        m = re.search(pattern, str(text))
        # 这里改成 .group(0) 显示匹配内容，或者直接返回 text 显示原行
        return text if m else None

    data[column] = data[column].apply(search_func)
    return data


def generate_mods_from_lines(slot: str, lines: str) -> list[dict[str, str | dict[str, str | float | bool]]]:
    # slot 本身自带一个 mod
    auto_recognized_mod = slot[:2]
    # mod_settings 是一个多行文本，每一行的格式是 <acronym>_<mod_setting>=<value> 或 <acronym>
    # 最终期望得到：[{"acronym":<acronym>,"settings":{<mod_setting>:<value>}}]，如果不存在 settings，则不需要 settings 键
    # 先转换为 {acronym: [{mod_setting: value}]}，最后检测如果 settings 为空则不要添加该键
    mods_dict: dict[str, dict[str, Any]] = {auto_recognized_mod: {}}

    for line in lines.splitlines():
        if line.strip():
            line_split = line.split("=", 1)
            if len(line_split) == 1:  # mod only
                mods_dict[line_split[0]] = mods_dict.get(line_split[0], {})
            else:  # mod with settings
                # 如果要设置 mod 参数，原则上要求 mod 本身已经加入
                # 但是为了方便起见，如果 mod 不存在，但又要求设置参数，则自动添加该 mod
                value: str | float | bool
                acronym_n_setting, value = line_split
                acronym, mod_setting = acronym_n_setting.split("_", 1)
                if acronym not in mods_dict:
                    mods_dict[acronym] = {}
                # 这里的 value 默认只是字符串
                # 这里不对输入的 mod_setting 类型进行检查，只进行类型推断转换
                # 实际上，mod_setting 一共有三种可能的类型，分别是字符串型、数字型、逻辑型
                # 这里与 osu_tools 不同的是，强制要求逻辑型用全小写的 true 或 false 表示
                if value == "true":
                    value = True
                elif value == "false":
                    value = False
                else:
                    with contextlib.suppress(ValueError):
                        value = float(value)
                mods_dict[acronym].update({mod_setting: value})

    return [{"acronym": acronym, "settings": _settings} if _settings else {"acronym": acronym} for acronym, _settings in mods_dict.items()]


def safe_norm(value, type_: type = str):
    if pd.isna(value):
        return None
    return type_(value)


def _create_tmp_playlist_p(name: str, beatmap_specs: list[BeatmapSpec]) -> str:
    # 暂时不考虑定制谱面/本地谱面需求，因为 playlist 要求是纯在线谱面
    # 或许可以考虑提供一个 placeholder 选项，配合一个本地的谱面解析工具
    # 然而，这个操作可能会需要完全重构 playlist 生成器的逻辑，因为其目前所使用的所有信息都是在线获取的
    # 所有在线谱面共用一个文件夹，设计之初是给一个团队使用的

    # 错误检查前置：预扫描 beatmap_specs，检查是否有重复的 bid
    bid_set: set[int] = set()
    for beatmap_spec in beatmap_specs:
        if (_bid := beatmap_spec[0]) in bid_set:
            raise ValueError(f"duplicated bid detected: {_bid}")
        bid_set.add(_bid)

    pool_path = os.path.join(C.UPLOADED_DIRECTORY.value, "online")
    if not os.path.exists(pool_path):
        os.mkdir(pool_path)
    # 创建一个临时谱面文件，以 name + time 为谱面名
    tmp_playlist_filename = str(os.path.join(pool_path, "%s_%d.properties" % (name, time_ns() // 1_000_000)))
    tmp_playlist_p = Properties(tmp_playlist_filename)
    tmp_playlist_p["custom_columns"] = '["mods", "slot"]'  # 一定要启用自定义列功能，不然不支持 slot
    # playlist Properties 文件格式如下：
    # bid = {"mods": mods, "slot": slot}
    # # notes
    # Properties 是一个 OrderedDict，往后依次添加内容即可。要注意 notes 必须以 \n 结尾，因为对于注释的解析是完整行
    for i, a in enumerate(beatmap_specs, start=1):
        tmp_playlist_p[str(a[0])] = orjson.dumps({"mods": a[1], "slot": a[2]}).decode()
        tmp_playlist_p["#%i" % (i * 2 - 1)] = "# %s\n" % a[4]
    tmp_playlist_p.dump()
    return tmp_playlist_filename


def _make_query_uppercase(original_query_func):
    """一个补丁，用于解决从数据库获取数据时列名小写的问题，使其与原始设计（使用 sqlite）保持一致"""

    def wrapper(sql, ttl=None, show_spinner: bool | str = False, **kwargs):
        df = original_query_func(sql, ttl=ttl, show_spinner=show_spinner, **kwargs)
        df.columns = df.columns.str.upper()
        return df

    return wrapper


def _build_upsert(dialect: str, update_fields: list[str], primary_keys: list[str]) -> str:
    """构建自适应的 upsert SQL 字符串"""
    if dialect[:5] == "mysql":
        updates = ", ".join([f"{k} = VALUES({k})" for k in update_fields])
        sql = f"ON DUPLICATE KEY UPDATE {updates}"
    else:
        updates = ", ".join([f"{k} = EXCLUDED.{k}" for k in update_fields])
        conflict = f"ON CONFLICT ({', '.join(primary_keys)})"
        sql = f"{conflict} DO UPDATE SET {updates}"
    return sql


def _build_update_ignore(dialect: str, body: str, primary_keys: list[str]) -> str:
    """构建自适应的 update ignore SQL 字符串"""
    if dialect[:5] == "mysql":
        sql = f"{body[:6]} IGNORE {body[7:]}"
    else:
        suffix = f"ON CONFLICT ({', '.join(primary_keys)}) DO NOTHING"
        sql = f"{body} {suffix}"
    return sql
