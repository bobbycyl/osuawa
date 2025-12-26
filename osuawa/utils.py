import asyncio
import os
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum, unique
from math import log10, sqrt
from threading import BoundedSemaphore
from time import sleep
from typing import Any, Optional

import numpy as np
from clayutil.futil import Downloader
from ossapi import Beatmap, OssapiAsync, Score, User, UserCompact  # 避免与 rosu.Beatmap、slider.Beatmap 冲突
from osupp.core import init_osu_tools

init_osu_tools(os.path.join(os.path.dirname(__file__), "..", "osu-tools", "PerformanceCalculator", "bin", "Release", "net8.0"))
from osupp.difficulty import calculate_osu_difficulty
from osupp.performance import Performance, calculate_osu_performance

assert calculate_osu_difficulty

headers = {
    "Referer": "https://bobbycyl.github.io/playlists/",
    "User-Agent": "osuawa",
}
sem = BoundedSemaphore()

LANGUAGES = ["en_US", "zh_CN"]


@unique
class C(Enum):
    LOGS = "./logs/"
    LOCALE = "./share/locale/"
    OUTPUT_DIRECTORY = "./output/"
    STATIC_DIRECTORY = "./static/"
    UPLOADED_DIRECTORY = "./static/uploaded/"
    BEATMAPS_CACHE_DIRECTORY = "./static/beatmaps/"
    RAW_RECENT_SCORES = "raw_recent_scores/"
    RECENT_SCORES = "recent_scores/"


def user_raw_recent_scores_filename(user) -> str:
    return os.path.join(C.OUTPUT_DIRECTORY.value, C.RAW_RECENT_SCORES.value, f"{user}.json")


def user_recent_scores_directory(user) -> str:
    return os.path.join(C.OUTPUT_DIRECTORY.value, C.RECENT_SCORES.value, f"{user}")


@unique
class ColorBar(Enum):
    # https://github.com/ppy/osu/blob/master/osu.Game/Graphics/OsuColour.cs
    XP = [0.1, 1.25, 2.0, 2.5, 3.3, 4.2, 4.9, 5.8, 6.7, 7.7, 9.0]
    YP_R = [66, 79, 79, 124, 246, 255, 255, 198, 101, 24, 0]
    YP_G = [144, 192, 255, 255, 240, 128, 78, 69, 99, 21, 0]
    YP_B = [251, 255, 213, 79, 92, 104, 111, 184, 222, 142, 0]


def to_readable_mods(mods: list[dict[str, Any]]) -> list[str]:
    readable_mods: list[str] = []
    for i in range(len(mods)):
        if "settings" in mods[i]:
            readable_mods.append("%s(%s)" % (mods[i]["acronym"], ",".join(["%s=%s" % it for it in mods[i]["settings"].items()])))
        else:
            readable_mods.append(mods[i]["acronym"])
    return readable_mods


def calc_bin_size(data) -> float:
    return (max(data) - min(data)) / min((sqrt(len(data)), 10 * log10(len(data))))


async def simple_user_dict(user: User | UserCompact) -> dict[str, Any]:
    return {
        "username": user.username,
        "user_id": user.id,
        "country": user.country,
        "is_online": user.is_online,
        "is_supporter": user.is_supporter,
        "team": user.team,
        "stat_pp": user.statistics.pp,
        "stat_global_rank": user.statistics.global_rank,
        "stat_country_rank": user.statistics.country_rank,
    }


async def a_get_user_info(api: OssapiAsync, user: int | str) -> dict[str, Any]:
    return await simple_user_dict(await api.user(user, key="username" if isinstance(user, str) else "id"))


async def get_username(api: OssapiAsync, user: int) -> str:
    return (await api.user(user, key="id")).username


async def a_get_beatmaps(api: OssapiAsync, cut_bids: Sequence[list[int]]) -> list[list[Beatmap]]:
    tasks = []
    async with asyncio.TaskGroup() as tg:
        for bids in cut_bids:
            tasks.append(tg.create_task(api.beatmaps(bids)))
    return [task.result() for task in tasks]


def get_beatmaps_dict(api: OssapiAsync, bids: set[int]) -> dict[int, Beatmap]:
    bids = tuple(bids)
    cut_bids = []
    for i in range(0, len(bids), 50):
        cut_bids.append(list(bids[i : i + 50]))
    results = asyncio.run(a_get_beatmaps(api, cut_bids))
    return {b.id: b for bs in results for b in bs}


def calc_hit_window(original_accuracy: float, magnitude: float = 1.0) -> float:
    hit_window = 80.0 - 6.0 * original_accuracy
    return hit_window / magnitude


def calc_accuracy(hit_window: float) -> float:
    return (80.0 - hit_window) / 6.0


def calc_preempt(original_ar: float, magnitude: float = 1.0) -> float:
    if original_ar < 5.0:
        preempt = 1200.0 + 600.0 * (5.0 - original_ar) / 5.0
    else:
        preempt = 1200.0 - 750 * (original_ar - 5.0) / 5.0
    return preempt / magnitude


def calc_ar(preempt: float) -> float:
    if preempt > 1200.0:
        ar = 5.0 - (preempt - 1200.0) / 600 * 5.0
    else:
        ar = 5.0 + (1200.0 - preempt) / 750 * 5.0
    return ar


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
        self.osu_tool_mods = []  # [acronym]
        self.osu_tool_mod_options = []  # [acronym_setting_name=setting_value]

    def set_mods(self, mods: list):
        mods_dict = {}  # {acronym, settings}
        for mod in mods:
            mods_dict[mod["acronym"]] = mod["settings"] if mod.get("settings", None) else {}
            self.osu_tool_mods.append(mod["acronym"])
            for setting_name, setting_value in mod["settings"].items():
                self.osu_tool_mod_options.append("%s_%s=%s" % (mod["acronym"], setting_name, setting_value))
        if "NF" in mods_dict:
            self.is_nf = True
        if "HD" in mods_dict:
            self.is_hd = True
        if "HR" in mods_dict:
            self.cs = self.cs * 1.3
            if self.cs > 10:
                self.cs = 10.0
            self.accuracy = self.accuracy * 1.4
            if self.accuracy > 10:
                self.accuracy = 10.0
            self.ar = self.ar * 1.4
            if self.ar > 10:
                self.ar = 10.0
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
    """参数顺序

    0  => bid

    1  => user

    2  => score

    3  => accuracy

    4  => max_combo

    5  => passed

    6  => pp

    7  => _mods

    8  => ts

    9  => statistics

    10 => st

    """

    beatmap_id: int
    user_id: int
    total_score: int
    accuracy: float
    max_combo: int
    passed: bool
    pp: Optional[float]
    mods: list[dict[str, str] | dict[str, dict[str, str]]]
    ended_at: datetime
    statistics: dict[str, Optional[int]]
    started_at: Optional[datetime]

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
                "small_tick_hit": score.statistics.small_tick_hit,  # 这对 std 而言似乎是多余的
                "slider_tail_hit": score.statistics.slider_tail_hit,
                "great": score.statistics.great,
                "ok": score.statistics.ok,
                "meh": score.statistics.meh,
                "miss": score.statistics.miss,
            },
            score.started_at,
        )


@dataclass(slots=True)
class CompletedSimpleScoreInfo(SimpleScoreInfo):
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
    difficulty_rating: float
    rosu_stars: float
    rosu_max_combo: int
    rosu_aim: Optional[float]
    rosu_aim_difficult_slider_count: Optional[float]
    rosu_speed: Optional[float]
    rosu_speed_note_count: Optional[float]
    rosu_slider_factor: Optional[float]
    # rosu_ar: Optional[float]
    rosu_aim_top_weighted_slider_factor: Optional[float]
    rosu_speed_top_weighted_slider_factor: Optional[float]
    rosu_aim_difficult_strain_count: Optional[float]
    rosu_speed_difficult_strain_count: Optional[float]
    rosu_pp_aim: Optional[float]
    rosu_pp_speed: Optional[float]
    rosu_pp_accuracy: Optional[float]
    rosu_pp100_aim: Optional[float]
    rosu_pp100_speed: Optional[float]
    rosu_pp100_accuracy: Optional[float]
    rosu_pp100: float
    rosu_pp92: float
    rosu_pp81: float
    rosu_pp67: float


def download_osu(beatmap: Beatmap):
    need_download = False
    if not "%s.osu" % beatmap.id in os.listdir(C.BEATMAPS_CACHE_DIRECTORY.value):
        need_download = True
    if need_download:
        with sem:
            sleep(1)
            Downloader(C.BEATMAPS_CACHE_DIRECTORY.value).start("https://osu.ppy.sh/osu/%d" % beatmap.id, "%s.osu" % beatmap.id, headers)
            sleep(0.5)


def calc_beatmap_attributes(beatmap: Beatmap, score: SimpleScoreInfo) -> CompletedSimpleScoreInfo:
    """完整计算所需属性，这会覆盖 score 原本的 pp"""
    # 如果传递的 score 是完整的，那么截断为 SimpleScoreInfo，这里用 rosu_pp100 来检测
    if hasattr(score, "rosu_pp100"):
        score = SimpleScoreInfo(
            score.beatmap_id,
            score.user_id,
            score.total_score,
            score.accuracy,
            score.max_combo,
            score.passed,
            None,
            score.mods,
            score.ended_at,
            score.statistics,
            score.started_at,
        )
    mods = score.mods
    my_attr = SimpleOsuDifficultyAttribute(beatmap.cs, beatmap.accuracy, beatmap.ar, beatmap.bpm, beatmap.hit_length)
    my_attr.set_mods(mods)
    download_osu(beatmap)
    # rosu_map: rosu.Beatmap = rosu.Beatmap(path=os.path.join(C.BEATMAPS_CACHE_DIRECTORY.value, "%s.osu" % beatmap.id))
    # rosu_diff: rosu.Difficulty = rosu.Difficulty(mods=mods)
    # rosu_attr: rosu.DifficultyAttributes = rosu_diff.calculate(rosu_map)
    # perf_got: rosu.Performance = rosu.Performance(
    #     mods=mods,
    #     combo=score.max_combo,
    #     large_tick_hits=score.statistics.get("large_tick_hit", 0),
    #     small_tick_hits=score.statistics.get("small_tick_hit", 0),
    #     slider_end_hits=score.statistics.get("slider_tail_hit", 0),
    #     n300=score.statistics.get("great", 0),
    #     n100=score.statistics.get("ok", 0),
    #     n50=score.statistics.get("meh", 0),
    #     misses=score.statistics.get("miss", 0),
    #     lazer=bool(score.started_at),
    # )
    # perf100: rosu.Performance = rosu.Performance(mods=mods, accuracy=100, hitresult_priority=rosu.HitResultPriority.BestCase)
    # perf92: rosu.Performance = rosu.Performance(mods=mods, accuracy=92, hitresult_priority=rosu.HitResultPriority.WorstCase)
    # perf81: rosu.Performance = rosu.Performance(mods=mods, accuracy=81, hitresult_priority=rosu.HitResultPriority.WorstCase)
    # perf67: rosu.Performance = rosu.Performance(mods=mods, accuracy=67, hitresult_priority=rosu.HitResultPriority.WorstCase)
    # pp_got_attr = perf_got.calculate(rosu_attr)
    # pp_aim = pp_got_attr.pp_aim
    # pp_speed = pp_got_attr.pp_speed
    # pp_accuracy = pp_got_attr.pp_accuracy
    # pp100_attr = perf100.calculate(rosu_attr)
    # pp100_aim = pp100_attr.pp_aim
    # pp100_speed = pp100_attr.pp_speed
    # pp100_accuracy = pp100_attr.pp_accuracy
    # pp100 = pp100_attr.pp
    # pp92 = perf92.calculate(rosu_attr).pp
    # pp81 = perf81.calculate(rosu_attr).pp
    # pp67 = perf67.calculate(rosu_attr).pp
    # osupp_attr = calculate_osu_difficulty(os.path.join(C.BEATMAPS_CACHE_DIRECTORY.value, "%s.osu" % beatmap.id), my_attr.osu_tool_mods, my_attr.osu_tool_mod_options)
    calculator = calculate_osu_performance(
        beatmap_path=os.path.join(C.BEATMAPS_CACHE_DIRECTORY.value, "%s.osu" % beatmap.id),
        mods=my_attr.osu_tool_mods,
        mod_options=my_attr.osu_tool_mod_options,
    )
    osupp_attr = next(calculator)
    perf_got_attr = calculator.send(
        Performance(
            combo=score.max_combo,
            misses=score.statistics.get("miss", 0),
            mehs=score.statistics.get("meh"),
            oks=score.statistics.get("ok"),
            large_tick_hits=score.statistics.get("large_tick_hit"),
            slider_tail_hits=score.statistics.get("slider_tail_hit"),
        ),
    )
    perf100_attr = calculator.send(Performance())
    pp92 = calculator.send(Performance(accuracy_percent=92.0))["pp"]
    pp81 = calculator.send(Performance(accuracy_percent=81.0))["pp"]
    pp67 = calculator.send(Performance(accuracy_percent=67.0))["pp"]
    pp_got = perf_got_attr["pp"]
    pp_got_aim = perf_got_attr["aim"]
    pp_got_speed = perf_got_attr["speed"]
    pp_got_accuracy = perf_got_attr["accuracy"]
    pp100 = perf100_attr["pp"]
    pp100_aim = perf100_attr["aim"]
    pp100_speed = perf100_attr["speed"]
    pp100_accuracy = perf100_attr["accuracy"]

    return CompletedSimpleScoreInfo(
        score.beatmap_id,
        score.user_id,
        score.total_score,
        score.accuracy,
        score.max_combo,
        score.passed,
        pp_got,  # 使用本地计算的 pp
        score.mods,
        score.ended_at,
        score.statistics,
        score.started_at,
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
        # rosu_attr.stars,
        # rosu_attr.max_combo,
        # rosu_attr.aim,
        # rosu_attr.aim_difficult_slider_count,
        # rosu_attr.speed,
        # rosu_attr.speed_note_count,
        # rosu_attr.slider_factor,
        # rosu_attr.ar,
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
        score = 0.0
    score_pct = int((score - min_score) / (max_score - min_score) * 100.0)
    if score_pct > 100:
        score_pct = 100
    elif score_pct < 0:
        score_pct = 0
    return score_pct


def calc_star_rating_color(stars: float) -> str:
    if stars < 0.1:
        return "#aaaaaa"
    elif stars > 9.0:
        return "#000000"
    else:
        interp_r = np.interp(stars, ColorBar.XP.value, ColorBar.YP_R.value)
        interp_g = np.interp(stars, ColorBar.XP.value, ColorBar.YP_G.value)
        interp_b = np.interp(stars, ColorBar.XP.value, ColorBar.YP_B.value)
        return "#%02x%02x%02x" % (int(interp_r), int(interp_g), int(interp_b))
