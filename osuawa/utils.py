import asyncio
import os
from collections.abc import Sequence
from datetime import datetime
from enum import Enum, unique
from threading import BoundedSemaphore
from time import sleep
from typing import Any, Optional

import numpy as np
import rosu_pp_py as rosu
from clayutil.futil import Downloader
from ossapi import Beatmap as ApiBeatmap, OssapiAsync, Score, User, UserCompact  # 避免与 rosu.Beatmap 冲突
from rosu_pp_py import Beatmap as RosuMap  # 避免与 ossapi.Beatmap 冲突

headers = {
    "Referer": "https://bobbycyl.github.io/playlists/",
    "User-Agent": "osuawa",
}

LANGUAGES = ["en_US", "zh_CN"]


@unique
class Path(Enum):
    LOGS = "./logs/"
    LOCALE = "./share/locale/"
    OUTPUT_DIRECTORY = "./output/"
    STATIC_DIRECTORY = "./static/"
    UPLOADED_DIRECTORY = "./static/uploaded/"
    BEATMAPS_CACHE_DIRECTORY = "./static/beatmaps/"
    RAW_RECENT_SCORES = "raw_recent_scores/"
    RECENT_SCORES = "recent_scores/"


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
    return (np.max(data) - np.min(data)) / np.min((np.sqrt(len(data)), 10 * np.log10(len(data))))


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


async def _get_user_info(api: OssapiAsync, user: int | str) -> dict[str, Any]:
    return await simple_user_dict(await api.user(user, key="username" if isinstance(user, str) else "id"))


def get_username(api: OssapiAsync, user: int) -> str:
    return asyncio.run(api.user(user, key="id")).username


async def _get_beatmaps_dict(api: OssapiAsync, cut_bids: Sequence[list[int]]) -> list[list[ApiBeatmap]]:
    tasks = []
    async with asyncio.TaskGroup() as tg:
        for bids in cut_bids:
            tasks.append(tg.create_task(api.beatmaps(bids)))
    return [task.result() for task in tasks]


def get_beatmaps_dict(api: OssapiAsync, bids: Sequence[int]) -> dict[int, ApiBeatmap]:
    cut_bids = []
    for i in range(0, len(bids), 50):
        cut_bids.append(list(bids[i: i + 50]))
    results = asyncio.run(_get_beatmaps_dict(api, cut_bids))
    beatmaps_dict = {}
    for bs in results:
        for b in bs:
            beatmaps_dict[b.id] = b
    return beatmaps_dict


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


class OsuDifficultyAttribute(object):

    def __init__(self, cs: float, accuracy: float, ar: float, bpm: float, hit_length: int):
        self.cs = cs
        self.accuracy = accuracy
        self.hit_window = calc_hit_window(self.accuracy)
        self.ar = ar
        self.preempt = calc_preempt(self.ar)
        self.bpm = bpm
        self.hit_length = hit_length
        self.is_nf = False
        self.is_hd = False
        self.is_high_ar = False
        self.is_low_ar = False
        self.is_very_low_ar = False
        self.is_speed_up = False
        self.is_speed_down = False

    def set_mods(self, mods: list):
        mods_dict = {mod["acronym"]: (mod["settings"] if mod.get("settings", None) else {}) for mod in mods}
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
        elif "EZ" in mods_dict:
            self.cs = self.cs * 0.5
            self.accuracy = self.accuracy * 0.5
            self.ar = self.ar * 0.5
        elif "DA" in mods_dict:
            self.cs = mods_dict["DA"].get("circle_size", self.cs)
            self.accuracy = mods_dict["DA"].get("overall_difficulty", self.accuracy)
            self.ar = mods_dict["DA"].get("approach_rate", self.ar)
        magnitude = 1.0
        if "DT" in mods_dict:
            magnitude = mods_dict["DT"].get("speed_change", 1.5)
        elif "NC" in mods_dict:
            magnitude = mods_dict["NC"].get("speed_change", 1.5)
        elif "HT" in mods_dict:
            magnitude = mods_dict["HT"].get("speed_change", 0.75)
        elif "DC" in mods_dict:
            magnitude = mods_dict["DC"].get("speed_change", 0.75)
        elif "WU" in mods_dict:
            _settings = mods_dict["WU"]
            magnitude = 2.0 / (_settings.get("initial_rate", 1.0) + _settings.get("final_rate", 1.5))
        elif "WD" in mods_dict:
            _settings = mods_dict["WD"]
            magnitude = 2.0 / (_settings.get("initial_rate", 1.0) + _settings.get("final_rate", 0.75))
        if magnitude > 1.0:
            self.is_speed_up = True
        elif magnitude < 1.0:
            self.is_speed_down = True
        self.hit_window = calc_hit_window(self.accuracy, magnitude)
        self.accuracy = calc_accuracy(self.hit_window)
        self.preempt = calc_preempt(self.ar, magnitude)
        self.ar = calc_ar(self.preempt)
        if self.preempt <= 450.0:
            self.is_high_ar = True
        elif 675.0 <= self.preempt < 900.0:
            self.is_low_ar = True
        elif self.preempt >= 900.0:
            self.is_very_low_ar = True
        self.bpm *= magnitude
        self.hit_length = round(self.hit_length / magnitude)


score_info = tuple[int, int, int, float, int, bool, float, list[dict[str, str | dict] | dict[str, str]], datetime, dict[str, int], Optional[datetime]]


def score_info_tuple(score: Score) -> score_info:
    """Create compact score info list from a score.

    :param score: score object
    :return: [bid, user, score, accuracy, max_combo, passed, pp, mods, ts, statistics, st]
    """
    return (
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
            "small_tick_hit": score.statistics.small_tick_hit,
            "slider_tail_hit": score.statistics.slider_tail_hit,
            "great": score.statistics.great,
            "ok": score.statistics.ok,
            "meh": score.statistics.meh,
            "miss": score.statistics.miss,
        },
        score.started_at,
    )


def download_osu(beatmap: int):
    if not "%d.osu" % beatmap in os.listdir(Path.BEATMAPS_CACHE_DIRECTORY.value):
        with BoundedSemaphore():
            sleep(1)
            Downloader(Path.BEATMAPS_CACHE_DIRECTORY.value).start("https://osu.ppy.sh/osu/%d" % beatmap, "%d.osu" % beatmap, headers)
            sleep(1)


async def calc_beatmap_attributes(beatmap: ApiBeatmap, score: score_info) -> list:
    mods = score[7]
    my_attr = OsuDifficultyAttribute(beatmap.cs, beatmap.accuracy, beatmap.ar, beatmap.bpm, beatmap.hit_length)
    my_attr.set_mods(mods)
    download_osu(beatmap.id)
    rosu_map: RosuMap = rosu.Beatmap(path=os.path.join(Path.BEATMAPS_CACHE_DIRECTORY.value, "%d.osu" % beatmap.id))
    rosu_diff: rosu.Difficulty = rosu.Difficulty(mods=mods)
    rosu_attr: rosu.DifficultyAttributes = rosu_diff.calculate(rosu_map)
    perf_real: rosu.Performance = rosu.Performance(
        mods=mods,
        combo=score[4],
        large_tick_hits=score[9].get("large_tick_hit", 0),
        small_tick_hits=score[9].get("small_tick_hit", 0),
        slider_end_hits=score[9].get("slider_tail_hit", 0),
        n300=score[9].get("great", 0),
        n100=score[9].get("ok", 0),
        n50=score[9].get("meh", 0),
        misses=score[9].get("miss", 0),
        lazer=bool(score[10]),
    )
    perf100: rosu.Performance = rosu.Performance(mods=mods, accuracy=100, hitresult_priority=rosu.HitResultPriority.BestCase)
    perf92: rosu.Performance = rosu.Performance(mods=mods, accuracy=92, hitresult_priority=rosu.HitResultPriority.WorstCase)
    perf85: rosu.Performance = rosu.Performance(mods=mods, accuracy=85, hitresult_priority=rosu.HitResultPriority.WorstCase)
    perf67: rosu.Performance = rosu.Performance(mods=mods, accuracy=67, hitresult_priority=rosu.HitResultPriority.WorstCase)
    pp_real_attr = perf_real.calculate(rosu_attr)
    pp_aim = pp_real_attr.pp_aim
    pp_speed = pp_real_attr.pp_speed
    pp_accuracy = pp_real_attr.pp_accuracy
    pp100_attr = perf100.calculate(rosu_attr)
    pp100_aim = pp100_attr.pp_aim
    pp100_speed = pp100_attr.pp_speed
    pp100_accuracy = pp100_attr.pp_accuracy
    pp100 = pp100_attr.pp
    pp92 = perf92.calculate(rosu_attr).pp
    pp85 = perf85.calculate(rosu_attr).pp
    pp67 = perf67.calculate(rosu_attr).pp
    attr = [
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
        rosu_attr.stars,
        rosu_attr.max_combo,
        rosu_attr.aim,
        rosu_attr.aim_difficult_slider_count,
        rosu_attr.speed,
        rosu_attr.speed_note_count,
        rosu_attr.slider_factor,
        rosu_attr.ar,
        pp_aim,
        pp_speed,
        pp_accuracy,
        pp100_aim,
        pp100_speed,
        pp100_accuracy,
        pp100,
        pp92,
        pp85,
        pp67,
    ]
    return attr


def calc_positive_percent(score: int | float | None, min_score: int | float, max_score: int | float) -> int:
    if score is None:
        score = 0.0
    score_pct = int((score - min_score) / (max_score - min_score) * 100.0)
    if score_pct > 100.0:
        score_pct = 100.0
    elif score_pct < 0.0:
        score_pct = 0.0
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
