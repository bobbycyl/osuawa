import asyncio
import ctypes
import html
import os
import os.path
import platform
import re
from functools import cached_property
from shutil import rmtree
from threading import BoundedSemaphore
from time import sleep
from typing import Any, Optional

import orjson
import pandas as pd
import rosu_pp_py as rosu

if platform.system() == "Windows":
    fribidi = ctypes.CDLL("./osuawa/fribidi-0.dll")
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, UnidentifiedImageError
from clayutil.futil import Downloader, Properties, filelock
from fontfallback import writing
from osu import AsynchronousAuthHandler, Client, GameModeStr, Scope, AsynchronousClient

from .utils import (
    Beatmap,
    OsuDifficultyAttribute,
    calc_beatmap_attributes,
    calc_star_rating_color,
    get_beatmaps_dict,
    get_username,
    score_info_list,
    user_to_dict,
    Path,
    headers,
)

LANGUAGES = ["en_US", "zh_CN"]


def complete_scores_compact(scores_compact: dict[str, list], beatmaps_dict: dict[int, Beatmap]) -> dict[str, list]:
    for score_id in scores_compact:
        if len(scores_compact[score_id]) == 9:  # DO NOT CHANGE! the length of what score_info_list returns
            scores_compact[score_id].extend(calc_beatmap_attributes(beatmaps_dict[scores_compact[score_id][0]], scores_compact[score_id][7]))
    return scores_compact


class Osuawa(object):
    tz = "Asia/Shanghai"

    def __init__(self, oauth_filename: str, output_dir: str, code: str = None):
        auth_url = None
        p = Properties(oauth_filename)
        p.load()
        auth = AsynchronousAuthHandler(p["client_id"], p["client_secret"], p["redirect_url"], Scope("public", "identify", "friends.read"))
        if code is None:
            auth_url = auth.get_auth_url()
        else:
            asyncio.run(auth.get_auth_token(code))
        self.auth_url = auth_url
        self.client = AsynchronousClient(auth)
        self.output_dir = output_dir

    @cached_property
    def user(self) -> tuple[int, str]:
        own_data = asyncio.run(self.client.get_own_data())
        return own_data.id, own_data.username

    def create_scores_dataframe(self, scores: dict[str, list]) -> pd.DataFrame:
        df = pd.DataFrame.from_dict(
            scores,
            orient="index",
            columns=[
                "bid",
                "user",
                "score",
                "accuracy",
                "max_combo",
                "passed",
                "pp",
                "mods",
                "ts",
                "cs",
                "hit_window",
                "preempt",
                "bpm",
                "hit_length",
                "is_nf",
                "is_hd",
                "is_high_ar",
                "is_low_ar",
                "is_very_low_ar",
                "is_speed_up",
                "is_speed_down",
                "info",
                "original_difficulty",
                "b_star_rating",
                "b_max_combo",
                "b_aim_difficulty",
                "b_speed_difficulty",
                "b_speed_note_count",
                "b_slider_factor",
                "b_approach_rate",
                "b_overall_difficulty",
                "b_pp_100if",
                "b_pp_95if",
                "b_pp_90if",
                "b_pp_80if",
            ],
        )
        df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.tz_convert(self.tz)
        df["pp_pct"] = df["pp"] / df["b_pp_100if"]
        df["pp_95pct"] = df["pp"] / df["b_pp_95if"]
        df["pp_90pct"] = df["pp"] / df["b_pp_90if"]
        df["pp_80pct"] = df["pp"] / df["b_pp_80if"]
        df["combo_pct"] = df["max_combo"] / df["b_max_combo"]
        df["score_nf"] = df.apply(lambda row: row["score"] * 2 if row["is_nf"] else row["score"], axis=1)
        df["mods"] = df["mods"].apply(lambda x: orjson.dumps(x).decode())
        return df

    def get_user_info(self, username: str) -> dict[str, Any]:
        return user_to_dict(asyncio.run(self.client.get_user(username, key="username")))

    async def _get_score(self, score_id: int) -> list:
        score = await self.client.get_score_by_id_only(score_id)
        score_compact = score_info_list(score)
        score_compact.extend(
            calc_beatmap_attributes(
                await self.client.get_beatmap(score.beatmap_id),
                score_compact[7],
            )
        )
        return score_compact

    def get_score(self, score_id: int) -> pd.DataFrame:
        return self.create_scores_dataframe({str(score_id): asyncio.run(self._get_score(score_id))}).T

    async def _get_user_beatmap_scores(self, beatmap: int, user: int) -> dict[str, list]:
        user_scores = await self.client.get_user_beatmap_scores(beatmap, user)
        scores_compact = {str(x.id): score_info_list(x) for x in user_scores}
        return complete_scores_compact(scores_compact, {beatmap: await self.client.get_beatmap(beatmap)})

    def get_user_beatmap_scores(self, beatmap: int, user: Optional[int] = None) -> pd.DataFrame:
        if user is None:
            user = self.user[0]
        return self.create_scores_dataframe(asyncio.run(self._get_user_beatmap_scores(beatmap, user)))

    @filelock(1)
    def save_recent_scores(self, user: int, include_fails: bool = True) -> str:
        # get
        user_scores = []
        offset = 0
        while True:
            user_scores_current = asyncio.run(
                self.client.get_user_scores(
                    user=user,
                    type="recent",
                    mode=GameModeStr.STANDARD,
                    include_fails=include_fails,
                    limit=50,
                    offset=offset,
                )
            )
            if len(user_scores_current) == 0:
                break
            user_scores.extend(user_scores_current)
            offset += 50

        recent_scores_compact = {str(x.id): score_info_list(x) for x in user_scores}
        len_got = len(recent_scores_compact)

        # concatenate
        len_local = 0
        if os.path.exists(os.path.join(self.output_dir, Path.RAW_RECENT_SCORES.value, f"{user}.json")):
            with open(os.path.join(self.output_dir, Path.RAW_RECENT_SCORES.value, f"{user}.json"), encoding="utf-8") as fi:
                recent_scores_compact_old = orjson.loads(fi.read())
            len_local = len(recent_scores_compact_old)
            recent_scores_compact = {
                **recent_scores_compact,
                **recent_scores_compact_old,
            }
        len_diff = len(recent_scores_compact) - len_local

        # calculate difficulty attributes
        bids_not_calculated = {x[0] for x in recent_scores_compact.values() if len(x) == 9}
        beatmaps_dict = get_beatmaps_dict(self.client, tuple(bids_not_calculated))
        recent_scores_compact = complete_scores_compact(recent_scores_compact, beatmaps_dict)

        # save
        with open(
            os.path.join(self.output_dir, Path.RAW_RECENT_SCORES.value, f"{user}.json"),
            "w",
        ) as fo:
            fo.write(orjson.dumps(recent_scores_compact).decode("utf-8"))
        df = self.create_scores_dataframe(recent_scores_compact)
        df.to_csv(os.path.join(self.output_dir, Path.RECENT_SCORES.value, f"{user}.csv"))
        return "%s: local/got/diff: %d/%d/%d" % (
            get_username(self.client, user),
            len_local,
            len_got,
            len_diff,
        )

    @staticmethod
    def create_client_credential_grant_client(client_id: int, client_secret: str) -> Client:
        return Client.from_credentials(client_id=client_id, client_secret=client_secret, redirect_url=None)


class BeatmapCover(object):
    font_sans = "./osuawa/ResourceHanRoundedSC-Regular.ttf"
    font_sans_fallback = "./osuawa/DejaVuSansCondensed.ttf"
    font_sans_medium = "./osuawa/ResourceHanRoundedSC-Medium.ttf"
    font_mono_regular = "./osuawa/MapleMono-NF-CN-Regular.ttf"
    font_mono_italic = "./osuawa/MapleMono-NF-CN-Italic.ttf"
    font_mono_semibold = "./osuawa/MapleMono-NF-CN-SemiBold.ttf"

    def __init__(self, beatmap: Beatmap, block_color, stars1: float, cs: str, ar: str, od: str, bpm: str, hit_length: str, max_combo: str, stars2: Optional[float] = None):
        self.beatmap = beatmap
        self.block_color = block_color
        self.stars1 = stars1
        if self.stars1 > 6.5:
            self.stars_text_color = "#f0dd55"
        else:
            self.stars_text_color = "#000000"
        self.stars2 = stars2
        self.stars = "󰓎 %.2f" % self.stars1
        if self.stars2 is not None:
            self.stars = "%s (%.2f)" % (self.stars, self.stars2)
        self.cs = cs
        self.ar = ar
        self.od = od
        self.bpm = bpm
        self.hit_length = hit_length
        self.max_combo = max_combo

    async def download(self, d: Downloader, filename: str) -> str:
        # 下载 cover 原图，若无 cover 则使用默认图片
        cover_filename = await d.async_start(self.beatmap.beatmapset.covers.cover_2x, filename, headers)
        try:
            im = Image.open(cover_filename)
        except UnidentifiedImageError:
            try:
                im = Image.open(await d.async_start(self.beatmap.beatmapset.covers.slimcover_2x, filename, headers))
            except UnidentifiedImageError:
                im = Image.open("./osuawa/bg1.jpg")
                im = im.filter(ImageFilter.BLUR)
        im = im.resize((1296, int(im.height * 1296 / im.width)), Image.Resampling.LANCZOS)  # 缩放到宽为 1296
        im = im.crop((im.width // 2 - 648, 0, im.width // 2 + 648, 360))  # 从中间裁剪到 1296 x 360

        # 调整亮度
        be = ImageEnhance.Brightness(im)
        im = be.enhance(0.33)
        im.save(cover_filename)

        return cover_filename

    def cut_text(self, draw: ImageDraw.Draw, font, text: str, length_limit: float, use_dots: bool) -> str | int:
        text_len_dry_run = draw.textlength(text, font=font)
        if text_len_dry_run > length_limit:
            cut_length = -1
            while True:
                text_cut = "%s..." % text[:cut_length] if use_dots else text[:cut_length]
                text_len_dry_run = draw.textlength(text_cut, font=font)
                if text_len_dry_run <= length_limit:
                    break
                cut_length -= 1
            return text_cut
        else:
            return -1

    async def draw(self, cover_filename) -> str:
        im = Image.open(cover_filename)
        draw = ImageDraw.Draw(im)

        # 测试长度
        len_set = 1188
        text_pos = 16
        padding = 28
        mod_theme_len = 50
        stars_len = draw.textlength(self.stars, font=ImageFont.truetype(font=self.font_mono_semibold, size=48))
        title_u = self.beatmap.beatmapset.title_unicode
        t1_cut = self.cut_text(draw, ImageFont.truetype(font=self.font_sans, size=72), title_u, len_set - stars_len - text_pos - padding - mod_theme_len, False)
        if t1_cut != -1:
            title_u2 = title_u.lstrip(t1_cut)
            title_u = "%s\n%s" % (t1_cut, title_u2)
            t2_cut = self.cut_text(draw, ImageFont.truetype(font=self.font_sans, size=72), title_u2, len_set - padding - mod_theme_len, True)
            if t2_cut != -1:
                title_u = "%s\n%s" % (t1_cut, t2_cut)

        # 绘制左侧文字
        fonts = writing.load_fonts(self.font_sans, self.font_sans_fallback)
        version = self.beatmap.version
        ver_cut = self.cut_text(draw, ImageFont.truetype(font=self.font_sans, size=48), version, len_set - padding - mod_theme_len - 328, True)
        if ver_cut != -1:
            version = ver_cut
        writing.draw_text_v2(draw, (42, 29 + 298), version, "#1f1f1f", fonts, 48, "ls")
        writing.draw_text_v2(draw, (40, 26 + 298), version, "white", fonts, 48, "ls")
        writing.draw_text_v2(draw, (40, 27 + 298), version, "white", fonts, 48, "ls")
        writing.draw_multiline_text_v2(draw, (42, 192 - 88), title_u, "#1f1f1f", fonts, 72, "ls")
        writing.draw_multiline_text_v2(draw, (42, 191 - 88), title_u, "#1f1f1f", fonts, 72, "ls")
        writing.draw_multiline_text_v2(draw, (42, 193 - 88), title_u, (40, 40, 40), fonts, 72, "ls")
        writing.draw_multiline_text_v2(draw, (41, 193 - 88), title_u, (40, 40, 40), fonts, 72, "ls")
        writing.draw_multiline_text_v2(draw, (41, 192 - 88), title_u, (40, 40, 40), fonts, 72, "ls")
        writing.draw_multiline_text_v2(draw, (40, 189 - 88), title_u, "white", fonts, 72, "ls")
        writing.draw_multiline_text_v2(draw, (41, 189 - 88), title_u, "white", fonts, 72, "ls")
        writing.draw_multiline_text_v2(draw, (40, 190 - 88), title_u, "white", fonts, 72, "ls")
        writing.draw_multiline_text_v2(draw, (41, 190 - 88), title_u, "white", fonts, 72, "ls")
        writing.draw_text_v2(draw, (42, 260), self.beatmap.beatmapset.artist_unicode, "#1f1f1f", fonts, 48, "ls")
        writing.draw_text_v2(draw, (40, 257), self.beatmap.beatmapset.artist_unicode, "white", fonts, 48, "ls")
        writing.draw_text_v2(draw, (40, 258), self.beatmap.beatmapset.artist_unicode, "white", fonts, 48, "ls")
        draw.text((42 + 1188, 326), self.beatmap.beatmapset.creator, font=ImageFont.truetype(font=self.font_sans_medium, size=48), fill="#1f1f2a", anchor="rs")
        draw.text((41 + 1188, 324), self.beatmap.beatmapset.creator, font=ImageFont.truetype(font=self.font_sans_medium, size=48), fill=(180, 235, 250), anchor="rs")
        draw.text((40 + 1188, 324), self.beatmap.beatmapset.creator, font=ImageFont.truetype(font=self.font_sans_medium, size=48), fill=(180, 235, 250), anchor="rs")

        # 在右上角绘制星数
        draw.rounded_rectangle([len_set + text_pos - stars_len - padding, 32, len_set + text_pos + padding, 106], 72, fill="#1f1f1f")
        draw.rounded_rectangle([len_set + text_pos - stars_len - padding, 30, len_set + text_pos + padding, 104], 72, fill=calc_star_rating_color(self.stars1))

        if self.stars1 > 6.5:  # white text
            draw.text((len_set + text_pos, 37), self.stars, anchor="ra", font=ImageFont.truetype(font=self.font_mono_semibold, size=48), fill="#f0dd55")
        else:  # black text
            draw.text((len_set + text_pos, 37), self.stars, anchor="ra", font=ImageFont.truetype(font=self.font_mono_semibold, size=48), fill="#000000")

        # 绘制mod主题色
        draw.rectangle((len_set + text_pos + mod_theme_len, 0, 1296, 1080), fill=(40, 40, 40))
        draw.rectangle((len_set + text_pos + mod_theme_len, 0, 1296, 1080), fill=self.block_color)

        im.save(cover_filename)
        return cover_filename


class OsuPlaylist(object):
    mod_color = {"NM": "#1040eb", "HD": "#ebb910", "HR": "#eb4040", "EZ": "#40b940", "DT": "#b910eb", "FM": "#40507f", "TB": "#7f4050"}

    # osz_type = OneOf("full", "novideo", "mini")

    def __init__(self, awa: Osuawa, playlist_filename: str, suffix: str = "", use_css_cover: bool = False):
        self.awa = awa
        p = Properties(playlist_filename)
        p.load()
        self.playlist_filename = playlist_filename
        self.suffix = suffix
        self.use_css_cover = use_css_cover
        self.footer = p.pop("footer") if "footer" in p else ""
        self.custom_columns = orjson.loads(p.pop("custom_columns")) if "custom_columns" in p else []
        parsed_beatmap_list = []

        # pop p from end until empty
        current_parsed_beatmap: dict[str, int | list[dict[str, Any]]| Beatmap | None] = {"notes": ""}
        while p:
            k, v = p.popitem()
            if k[0] == "#":  # notes
                current_parsed_beatmap["notes"] += v.lstrip("#").lstrip(" ")
            else:
                current_parsed_beatmap["bid"] = int(k)
                obj_v = orjson.loads(v)
                if self.custom_columns:
                    for column in self.custom_columns:
                        current_parsed_beatmap[column] = obj_v.get(column)
                else:
                    current_parsed_beatmap["mods"] = obj_v
                parsed_beatmap_list.insert(0, current_parsed_beatmap)
                current_parsed_beatmap = {"notes": ""}

        beatmaps_dict = get_beatmaps_dict(self.awa.client, [int(x["bid"]) for x in parsed_beatmap_list])
        for element in parsed_beatmap_list:
            element["notes"] = element["notes"].rstrip("\n").replace("\n", "<br>")
            element["beatmap"] = beatmaps_dict[element["bid"]]
        self.beatmap_list = parsed_beatmap_list
        self.covers_dir = os.path.splitext(playlist_filename)[0] + ".covers"
        self.tmp_dir = os.path.splitext(playlist_filename)[0] + ".tmp"
        self.d = Downloader(self.covers_dir)
        self.tmp_d = Downloader(self.tmp_dir)
        self.playlist_name = os.path.splitext(os.path.basename(playlist_filename))[0]
        # self.osz_type = osz_type
        # self.output_zip = output_zip
        # if self.output_zip:
        #     self.osz_type = "full"

    async def beatmap_task(self, index_and_beatmap: tuple[int, dict]) -> dict:
        i, element = index_and_beatmap
        bid: int = element["bid"]
        b: Beatmap = element["beatmap"]
        raw_mods: list[dict[str, Any]] = element["mods"]
        mods_ready: list[str] = []
        notes: str = element["notes"]

        # 处理NM, FM, TB
        color_mod = raw_mods[0]["acronym"]
        is_fm = False
        mods = raw_mods
        for j in range(len(raw_mods)):
            if raw_mods[j]["acronym"] == "NM" or raw_mods[j]["acronym"] == "TB":
                mods = []
            if raw_mods[j]["acronym"] == "FM":
                is_fm = True
                mods = []
            if "settings" in raw_mods[j]:
                mods_ready.append("%s(%s)" % (raw_mods[j]["acronym"], ",".join(["%s=%s" % it for it in raw_mods[j]["settings"].items()])))
            else:
                mods_ready.append(raw_mods[j]["acronym"])

        # 下载谱面与计算难度（与 utils.calc_difficulty_and_performance 类似，但是省略了许多不必要的计算）
        if not "%d.osu" % bid in os.listdir(Path.BEATMAPS_CACHE_DIRECTORY.value):
            with BoundedSemaphore():
                sleep(1)
                Downloader(Path.BEATMAPS_CACHE_DIRECTORY.value).start("https://osu.ppy.sh/osu/%d" % bid, "%d.osu" % bid, headers)
                sleep(0.5)
        my_attr = OsuDifficultyAttribute(b.cs, b.accuracy, b.ar, b.bpm, b.hit_length)
        if mods:
            my_attr.set_mods(mods)
        rosu_map = rosu.Beatmap(path=os.path.join(Path.BEATMAPS_CACHE_DIRECTORY.value, "%d.osu" % bid))
        rosu_diff = rosu.Difficulty(mods=mods)
        rosu_attr = rosu_diff.calculate(rosu_map)
        stars1 = rosu_attr.stars
        stars2 = None
        if is_fm:
            rosu_diff_fm = rosu.Difficulty(mods=[{"acronym": "HR"}])
            rosu_attr_fm = rosu_diff_fm.calculate(rosu_map)
            stars2 = rosu_attr_fm.stars
        cs = "%s" % round(float(my_attr.cs), 2)
        ar = "%s" % round(rosu_attr.ar, 2)
        od = "%s" % round(rosu_attr.od, 2)
        bpm = "%s" % round(my_attr.bpm, 2)
        song_len_in_sec = my_attr.hit_length
        song_len_m, song_len_s = divmod(song_len_in_sec, 60)
        hit_length = "%d:%02d" % (song_len_m, song_len_s)
        max_combo = "%d" % rosu_attr.max_combo

        # 绘制cover
        cover = BeatmapCover(b, self.mod_color.get(color_mod, "#eb50eb"), stars1, cs, ar, od, bpm, hit_length, max_combo, stars2)
        cover_filename = await cover.download(self.d, "%d-%d.jpg" % (i, bid))
        img_src = "./" + (os.path.relpath(cover_filename, os.path.split(self.playlist_filename)[0])).replace("\\", "/")
        img_link = "https://osu.ppy.sh/b/%d" % b.id
        beatmap_info = '<a href="%s"><img class="beatmap-info-image" src="%s" alt="%s - %s (%s) [%s]" height="90"/></a>' \
                       % (img_link, img_src, html.escape(b.beatmapset.artist), html.escape(b.beatmapset.title), html.escape(b.beatmapset.creator), html.escape(b.version))
        if self.use_css_cover:
            beatmap_info = '<div class="beatmap-info %s" style="--bg: %s; --fg: %s">%s<div class="beatmap-info-text">'\
                        '<p class="beatmap-info-text-artist">%s</p>'\
                        '<p class="beatmap-info-text-title">%s</p>'\
                        '<p class="beatmap-info-text-creator">%s</p>'\
                        '<p class="beatmap-info-text-version>%s</p>'\
                        '<p class="beatmap-info-text-stars">%s</p>'\
                        '<p class="beatmap-info-text-cs">%s</p>'\
                        '<p class="beatmap-info-text-ar">%s</p>'\
                        '<p class="beatmap-info-text-od">%s</p>'\
                        '<p class="beatmap-info-text-bpm">%s</p>'\
                        '<p class="beatmap-info-text-hit-length">%s</p>'\
                        '<p class="beatmap-info-text-max-combo">%s</p>'\
                        '</div></div>' \
            % (color_mod, cover.stars_text_color, calc_star_rating_color(cover.stars1), beatmap_info, html.escape(b.beatmapset.artist), html.escape(b.beatmapset.title), html.escape(b.beatmapset.creator), html.escape(b.version), cover.stars, cs, ar, od, bpm, hit_length, max_combo)
        else:
            await cover.draw(cover_filename)

        # 保存数据

        completed_beatmap = {
            "#": i,
            "BID": b.id,
            "SID": b.beatmapset_id,
            "Beatmap Info": beatmap_info,
            "Artist - Title (Creator) [Version]": "%s - %s (%s) [%s]" % (b.beatmapset.artist, b.beatmapset.title, b.beatmapset.creator, b.version),
            "Stars": cover.stars,
            "SR": cover.stars.replace("󰓎", "★"),
            "BPM": cover.bpm,
            "Hit Length": cover.hit_length,
            "Max Combo": cover.max_combo,
            "CS": cover.cs,
            "AR": cover.ar,
            "OD": cover.od,
            "Mods": "; ".join(mods_ready),
            "Notes": notes,
        }
        for column in self.custom_columns:
            if column == "mods":
                continue
            else:
                completed_beatmap[column] = element[column]
        return completed_beatmap

    async def playlist_task(self) -> list[dict]:
        tasks = []
        async with asyncio.TaskGroup() as tg:
            for index_and_beatmap in enumerate(self.beatmap_list, start=1):
                tasks.append(tg.create_task(self.beatmap_task(index_and_beatmap)))
        return [task.result() for task in tasks]

    def generate(self) -> pd.DataFrame:
        playlist = asyncio.run(self.playlist_task())
        df_columns = ["#", "BID", "Beatmap Info", "Mods", "BPM", "Hit Length", "Max Combo", "CS", "AR", "OD"]
        df_standalone_columns = ["#", "BID", "SID", "Artist - Title (Creator) [Version]", "SR", "BPM", "Hit Length", "Max Combo", "CS", "AR", "OD", "Mods"]
        for column in self.custom_columns:
            if column == "mods":
                continue
            else:
                df_columns.insert(3, column)
                df_standalone_columns.insert(4, column)
        df_columns.append("Notes")
        df_standalone_columns.append("Notes")
        df = pd.DataFrame(playlist, columns=df_columns)
        df_standalone = pd.DataFrame(playlist, columns=df_standalone_columns)
        df.sort_values(by=["#"], inplace=True)
        df_standalone.sort_values(by=["#"], inplace=True)
        pd.set_option("colheader_justify", "center")
        if self.footer != "":
            html_string = '<html><head><meta charset="utf-8"><title>%s%s</title></head><link rel="stylesheet" type="text/css" href="style.css"/><body>{table}<footer class="footer">%s</footer></body></html>' % (
                self.playlist_name,
                self.suffix,
                self.footer,
            )
        else:
            html_string = '<html><head><meta charset="utf-8"><title>%s%s</title></head><link rel="stylesheet" type="text/css" href="style.css"/><body>{table}</body></html>' % (
                self.playlist_name,
                self.suffix,
            )
        with open(self.playlist_filename.replace(".properties", ".html"), "w", encoding="utf-8") as fi:
            fi.write(html_string.format(table=df.to_html(index=False, escape=False, classes="pd")))

        # 功能暂不可用
        # if self.output_zip:
        #     # 生成课题压缩包
        #     if not os.path.exists(Path.OUTPUT_DIRECTORY.value):
        #         os.mkdir(Path.OUTPUT_DIRECTORY.value)
        #     df_standalone.to_csv(os.path.join(self.tmp_dir, "table.csv"), index=False)
        #     compress_as_zip(self.tmp_dir, "./output/%s.zip" % self.playlist_name)

        # 清理临时文件夹
        rmtree(self.tmp_dir)

        return df_standalone

    @staticmethod
    def convert_legacy(legacy_playlist_filename: str):
        split_pattern = re.compile(r"(.*) \[(.*)] \((.*)\)")
        legacy_p = Properties(legacy_playlist_filename)
        legacy_p.load()
        open(os.path.join(Path.OUTPUT_DIRECTORY.value, os.path.split(legacy_playlist_filename)[1]), "w").close()
        converted_p = Properties(os.path.join(Path.OUTPUT_DIRECTORY.value, os.path.split(legacy_playlist_filename)[1]))
        legacy_playlist_raw = [int(x) for x in (legacy_p.keys()) if x[0] != "#"]
        for bid in legacy_playlist_raw:
            m = split_pattern.match(str(legacy_p[str(bid)]))  # mods、targets、notes
            legacy_mods = m.group(1).split(" ")
            mods = [{"acronym": mod} for mod in legacy_mods]
            converted_p[str(bid)] = orjson.dumps(mods).decode("utf-8")
            notes = m.group(3)
            if notes:
                converted_p["#%d" % (len(converted_p.keys()) + 1)] = "# %s\n" % notes
        converted_p.dump()
