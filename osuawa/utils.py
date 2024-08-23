import os
import subprocess
from collections.abc import Sequence
from typing import Any

import numpy as np
import rosu_pp_py as rosu
import streamlit as st
from osu import Client
from osu.objects import Beatmap, LegacyScore, Mod, SoloScore, UserCompact, UserStatistics

# https://github.com/ppy/osu/blob/master/osu.Game/Graphics/OsuColour.cs
XP = [0.1, 1.25, 2.0, 2.5, 3.3, 4.2, 4.9, 5.8, 6.7, 7.7, 9.0]
YP_R = [66, 79, 79, 124, 246, 255, 255, 198, 101, 24, 0]
YP_G = [144, 192, 255, 255, 240, 128, 78, 69, 99, 21, 0]
YP_B = [251, 255, 213, 79, 92, 104, 111, 184, 222, 142, 0]


def save_value(key: str) -> None:
    st.session_state["_%s_value" % key] = st.session_state[key]


def save_index(options, key: str) -> None:
    st.session_state["_%s_value" % key] = list(options).index(st.session_state[key])


def load_value(key: str, default_value: Any) -> Any:
    if "_%s_value" % key not in st.session_state:
        st.session_state["_%s_value" % key] = default_value
    return st.session_state["_%s_value" % key]


def memorized_multiselect(label: str, key: str, options, default_value: Any) -> None:
    st.multiselect(label, options, default=load_value(key, default_value), key=key, on_change=save_value, args=[key])


def memorized_selectbox(label: str, key: str, options, default_value: Any) -> None:
    st.selectbox(label, options, index=load_value(key, default_value), key=key, on_change=save_index, args=[options, key])


def user_to_dict(user: UserCompact) -> dict[str, Any]:
    attr_dict = {}
    for attr in UserCompact.__slots__:
        attr_dict[attr] = getattr(user, attr)
    stats = user.statistics
    stats_dict = {}
    for attr in UserStatistics.__slots__:
        stats_dict[attr] = getattr(stats, attr)
    attr_dict["statistics"] = stats_dict
    return attr_dict


def get_user_info(client: Client, username: str) -> dict[str, Any]:
    return user_to_dict(client.get_user(username, key="username"))


def get_username(client, user: int) -> str:
    return client.get_user(user, key="id").username


def get_beatmap_dict(client: Client, bids: Sequence[int]) -> dict[int, Beatmap]:
    beatmaps_dict = {}
    for i in range(0, len(bids), 50):
        bs_current = client.get_beatmaps(bids[i : i + 50])
        for b_current in bs_current:
            beatmaps_dict[b_current.id] = b_current
    return beatmaps_dict


def calc_hit_window(original_accuracy: float, magnitude: float = 1.0) -> float:
    hit_window = 80 - 6 * original_accuracy
    return hit_window / magnitude


def calc_accuracy(hit_window: float) -> float:
    return (80 - hit_window) / 6


def calc_preempt(original_ar: float, magnitude: float = 1.0) -> float:
    if original_ar < 5:
        preempt = 1200 + 600 * (5 - original_ar) / 5
    else:
        preempt = 1200 - 750 * (original_ar - 5) / 5
    return preempt / magnitude


def calc_ar(preempt: float) -> float:
    if preempt > 1200:
        ar = 5 - (preempt - 1200) / 600 * 5
    else:
        ar = 5 + (1200 - preempt) / 750 * 5
    return ar


class OsuDifficultyAttribute(object):

    def __init__(self, cs, accuracy, ar, bpm, hit_length):
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
        mods_dict = {mod["acronym"]: (mod["settings"] if mod.get("settings", None) is not None else {}) for mod in mods}
        if Mod.NoFail.value in mods_dict:
            self.is_nf = True
        if Mod.Hidden.value in mods_dict:
            self.is_hd = True
        if Mod.HardRock.value in mods_dict:
            self.cs = self.cs * 1.3
            if self.cs > 10:
                self.cs = 10
            self.accuracy = self.accuracy * 1.4
            if self.accuracy > 10:
                self.accuracy = 10
            self.ar = self.ar * 1.4
        elif Mod.Easy.value in mods_dict:
            self.cs = self.cs * 0.5
            self.accuracy = self.accuracy * 0.5
            self.ar = self.ar * 0.5
        elif Mod.DifficultyAdjust.value in mods_dict:
            self.cs = mods_dict[Mod.DifficultyAdjust.value].get("circle_size", self.cs)
            self.accuracy = mods_dict[Mod.DifficultyAdjust.value].get("overall_difficulty", self.accuracy)
            self.ar = mods_dict[Mod.DifficultyAdjust.value].get("approach_rate", self.ar)
        magnitude = 1.0
        if Mod.DoubleTime.value in mods_dict:
            magnitude = mods_dict[Mod.DoubleTime.value].get("speed_change", 1.5)
        elif Mod.Nightcore.value in mods_dict:
            magnitude = mods_dict[Mod.Nightcore.value].get("speed_change", 1.5)
        elif Mod.HalfTime.value in mods_dict:
            magnitude = mods_dict[Mod.HalfTime.value].get("speed_change", 0.75)
        elif Mod.Daycore.value in mods_dict:
            magnitude = mods_dict[Mod.Daycore.value].get("speed_change", 0.75)
        elif Mod.WindUp.value in mods_dict:
            # harmonic mean
            _settings = mods_dict[Mod.WindUp.value]
            magnitude = 2 / (1 / _settings.get("initial_rate", 1.0) + 1 / _settings.get("final_rate", 1.5))
        elif Mod.WindDown.value in mods_dict:
            # harmonic mean
            _settings = mods_dict[Mod.WindDown.value]
            magnitude = 2 / (1 / _settings.get("initial_rate", 1.0) + 1 / _settings.get("final_rate", 0.75))
        if magnitude > 1:
            self.is_speed_up = True
        elif magnitude < 1:
            self.is_speed_down = True
        self.hit_window = calc_hit_window(self.accuracy, magnitude)
        self.accuracy = calc_accuracy(self.hit_window)
        self.preempt = calc_preempt(self.ar, magnitude)
        self.ar = calc_ar(self.preempt)
        if self.preempt <= 450:
            self.is_high_ar = True
        elif 750 <= self.preempt < 1050:
            self.is_low_ar = True
        elif self.preempt >= 1050:
            self.is_very_low_ar = True
        self.bpm *= magnitude
        self.hit_length /= magnitude


def get_acronym(mod: Mod | str) -> str:
    if isinstance(mod, Mod):
        return mod.value
    else:
        return mod


def score_info_list(score: SoloScore | LegacyScore) -> list:
    # bid, user, score, accuracy, max_combo, passed, pp, mods, ts
    return [
        score.beatmap_id,
        score.user_id,
        score.total_score,
        score.accuracy,
        score.max_combo,
        score.passed,
        score.pp,
        [({"acronym": get_acronym(y.mod), "settings": y.settings} if y.settings is not None else {"acronym": get_acronym(y.mod)}) for y in score.mods],
        score.ended_at,
    ]


def rosu_calc(beatmap_file: str, mods: list) -> tuple:
    beatmap = rosu.Beatmap(path=beatmap_file)
    diff = rosu.Difficulty(mods=mods)
    diff_attr = diff.calculate(beatmap)
    note_count = diff_attr.n_circles
    perf100 = rosu.Performance(accuracy=100, misses=0, hitresult_priority=rosu.HitResultPriority.BestCase)
    perf95 = rosu.Performance(accuracy=95, misses=min(5, note_count), hitresult_priority=rosu.HitResultPriority.WorstCase)
    perf80h = rosu.Performance(accuracy=80, misses=int(note_count * 0.035), hitresult_priority=rosu.HitResultPriority.WorstCase)
    perf80l = rosu.Performance(accuracy=80, misses=int(note_count * 0.07), hitresult_priority=rosu.HitResultPriority.WorstCase)
    pp100 = perf100.calculate(diff_attr).pp
    pp95 = perf95.calculate(diff_attr).pp
    pp80h = perf80h.calculate(diff_attr).pp
    pp80l = perf80l.calculate(diff_attr).pp
    return (
        diff_attr.stars,
        diff_attr.max_combo,
        diff_attr.aim,
        diff_attr.speed,
        diff_attr.speed_note_count,
        diff_attr.slider_factor,
        diff_attr.ar,
        diff_attr.od,
        pp100,
        pp95,
        pp80h,
        pp80l,
    )


def calc_difficulty_and_performance(osu_tools_path: str, beatmap: int, mods: list) -> tuple:
    perf_calc_path = os.path.join(osu_tools_path, "PerformanceCalculator")
    beatmaps_cache_path = os.path.join(perf_calc_path, "cache")
    for osu_filename in os.listdir(beatmaps_cache_path):
        if "%d.osu" % beatmap == osu_filename:
            rosu_res = rosu_calc(os.path.join(beatmaps_cache_path, osu_filename), mods)
            break
    else:
        # use osu-tools cli to cache the .osu file
        subprocess.run(
            "dotnet run -- difficulty %d" % beatmap,
            cwd=perf_calc_path,
            capture_output=True,
            encoding="utf-8",
            text=True,
        )
        return calc_difficulty_and_performance(osu_tools_path, beatmap, mods)
    return rosu_res


def calc_beatmap_attributes(osu_tools_path: str, beatmap: Beatmap, mods: list) -> list:
    osu_diff_attr = OsuDifficultyAttribute(beatmap.cs, beatmap.accuracy, beatmap.ar, beatmap.bpm, beatmap.hit_length)
    osu_diff_attr.set_mods(mods)
    attr = [
        osu_diff_attr.cs,
        osu_diff_attr.hit_window,
        osu_diff_attr.preempt,
        osu_diff_attr.bpm,
        osu_diff_attr.hit_length,
        osu_diff_attr.is_nf,
        osu_diff_attr.is_hd,
        osu_diff_attr.is_high_ar,
        osu_diff_attr.is_low_ar,
        osu_diff_attr.is_very_low_ar,
        osu_diff_attr.is_speed_up,
        osu_diff_attr.is_speed_down,
        "%s - %s (%s) [%s]"
        % (
            beatmap.beatmapset.artist,
            beatmap.beatmapset.title,
            beatmap.beatmapset.creator,
            beatmap.version,
        ),
        beatmap.difficulty_rating,
    ]
    attr.extend(calc_difficulty_and_performance(osu_tools_path, beatmap.id, mods))
    return attr


def calc_star_rating_color(stars: float) -> str:
    if stars < 0.1:
        return "#aaaaaa"
    elif stars > 9.0:
        return "#000000"
    else:
        interp_r = np.interp(stars, XP, YP_R)
        interp_g = np.interp(stars, XP, YP_G)
        interp_b = np.interp(stars, XP, YP_B)
        return "#%02x%02x%02x" % (int(interp_r), int(interp_g), int(interp_b))
