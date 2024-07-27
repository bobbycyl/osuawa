import os
import subprocess

import rosu_pp_py as rosu
from osu.objects import Beatmap, LegacyScore, Mod, SoloScore


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
        self.is_hd = False
        self.is_hr = False
        self.is_ez = False
        self.is_speed_up = False
        self.is_speed_down = False

    def set_mods(self, mods: list):
        mods_dict = {mod["acronym"]: (mod["settings"] if mod.get("settings", None) is not None else {}) for mod in mods}
        if Mod.Hidden.value in mods_dict:
            self.is_hd = True
        if Mod.HardRock.value in mods_dict:
            self.is_hr = True
            self.cs = self.cs * 1.3
            if self.cs > 10:
                self.cs = 10
            self.accuracy = self.accuracy * 1.4
            if self.accuracy > 10:
                self.accuracy = 10
            self.ar = self.ar * 1.4
        elif Mod.Easy.value in mods_dict:
            self.is_ez = True
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
            # not official
            _settings = mods_dict[Mod.WindUp.value]
            magnitude = (_settings.get("initial_rate", 1.0) + _settings.get("final_rate", 1.5) * 3) / 4
        elif Mod.WindDown.value in mods_dict:
            # not official
            _settings = mods_dict[Mod.WindDown.value]
            magnitude = (_settings.get("initial_rate", 1.0) * 3 + _settings.get("final_rate", 0.75)) / 4
        if magnitude > 1:
            self.is_speed_up = True
        elif magnitude < 1:
            self.is_speed_down = True
        self.hit_window = calc_hit_window(self.accuracy, magnitude)
        self.accuracy = calc_accuracy(self.hit_window)
        self.preempt = calc_preempt(self.ar, magnitude)
        self.ar = calc_ar(self.preempt)
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
    perf100 = rosu.Performance(accuracy=100, misses=0, hitresult_priority=rosu.HitResultPriority.BestCase)
    perf99 = rosu.Performance(accuracy=99, misses=0, hitresult_priority=rosu.HitResultPriority.WorstCase)
    perf95 = rosu.Performance(accuracy=95, misses=0, hitresult_priority=rosu.HitResultPriority.WorstCase)
    perf90 = rosu.Performance(accuracy=90, misses=0, hitresult_priority=rosu.HitResultPriority.WorstCase)
    pp100 = perf100.calculate(diff_attr).pp
    pp99 = perf99.calculate(diff_attr).pp
    pp95 = perf95.calculate(diff_attr).pp
    pp90 = perf90.calculate(diff_attr).pp
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
        pp99,
        pp95,
        pp90,
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
            shell=True,
            cwd=perf_calc_path,
            capture_output=True,
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
        osu_diff_attr.is_hd,
        osu_diff_attr.is_hr,
        osu_diff_attr.is_ez,
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
