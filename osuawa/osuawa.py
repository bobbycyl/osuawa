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
from clayutil.validator import Integer
from fontfallback import writing
from osu import AsynchronousAuthHandler, Client, GameModeStr, Scope, AsynchronousClient

from .utils import (
    Beatmap,
    OsuDifficultyAttribute,
    calc_beatmap_attributes,
    calc_positive_percent,
    calc_star_rating_color,
    get_beatmaps_dict,
    get_username,
    score_info_list,
    user_to_dict,
    Path,
    headers,
)

LANGUAGES = ["en_US", "zh_CN"]

html_body_suffix = """
    </div>
  </div>
"""


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


def cut_text(draw: ImageDraw.Draw, font, text: str, length_limit: float, use_dots: bool) -> str | int:
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
        self.is_high_stars = False
        if self.stars1 > 6.5:
            self.is_high_stars = True
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
        if im.mode != "RGB":
            im = im.convert("RGB")
        be = ImageEnhance.Brightness(im)
        im = be.enhance(0.33)
        im.save(cover_filename)

        return cover_filename

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
        t1_cut = cut_text(draw, ImageFont.truetype(font=self.font_sans, size=72), title_u, len_set - stars_len - text_pos - padding - mod_theme_len, False)
        if t1_cut != -1:
            title_u2 = title_u.lstrip(t1_cut)
            title_u = "%s\n%s" % (t1_cut, title_u2)
            t2_cut = cut_text(draw, ImageFont.truetype(font=self.font_sans, size=72), title_u2, len_set - padding - mod_theme_len, True)
            if t2_cut != -1:
                title_u = "%s\n%s" % (t1_cut, t2_cut)

        # 绘制左侧文字
        fonts = writing.load_fonts(self.font_sans, self.font_sans_fallback)
        version = self.beatmap.version
        ver_cut = cut_text(draw, ImageFont.truetype(font=self.font_sans, size=48), version, len_set - padding - mod_theme_len - 328, True)
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

        draw.text((len_set + text_pos, 37), self.stars, anchor="ra", font=ImageFont.truetype(font=self.font_mono_semibold, size=48), fill=self.stars_text_color)

        # 绘制mod主题色
        draw.rectangle((len_set + text_pos + mod_theme_len, 0, 1296, 1080), fill=(40, 40, 40))
        draw.rectangle((len_set + text_pos + mod_theme_len, 0, 1296, 1080), fill=self.block_color)

        im.save(cover_filename)
        return cover_filename


class OsuPlaylist(object):
    css_style = Integer(1, 2, True)
    mod_color = {"NM": "#107fb9", "HD": "#b97f10", "HR": "#b91010", "EZ": "#10b97f", "DT": "#7f10b9", "NC": "#b9107f", "HT": "#7f7f7f", "FM": "#40507f", "TB": "#7f4050", "F+": "#507f40"}

    # osz_type = OneOf("full", "novideo", "mini")

    def __init__(self, awa: Osuawa, playlist_filename: str, suffix: str = "", css_style: Optional[int] = None):
        self.awa = awa
        p = Properties(playlist_filename)
        p.load()
        self.playlist_filename = playlist_filename
        self.suffix = suffix
        self.css_style = css_style
        self.footer = p.pop("footer") if "footer" in p else ""
        self.custom_columns = orjson.loads(p.pop("custom_columns")) if "custom_columns" in p else []
        parsed_beatmap_list = []

        # pop p from end until empty
        current_parsed_beatmap: dict[str, str | int | list[dict[str, Any]] | Beatmap | None] = {"notes": ""}
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
        self.bg_dir = os.path.join(os.path.split(playlist_filename)[0], "darkened-backgrounds")
        self.covers_d = Downloader(self.covers_dir)
        self.tmp_d = Downloader(self.tmp_dir)
        self.bg_d = Downloader(self.bg_dir)
        if not os.path.exists(os.path.join(os.path.split(playlist_filename)[0], "images")):
            os.mkdir(os.path.join(os.path.split(playlist_filename)[0], "images"))
            with open(os.path.join(os.path.split(playlist_filename)[0], "images", "total_length.svg"), "w") as fo:
                fo.write(
                    """<?xml version="1.0" encoding="UTF-8" standalone="no"?>
                    <svg xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:cc="http://creativecommons.org/ns#" xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:svg="http://www.w3.org/2000/svg" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 562.5 562.5" height="562.5" width="562.5" xml:space="preserve" version="1.1" id="svg4155"><metadata id="metadata4161"><rdf:RDF><cc:Work rdf:about=""><dc:format>image/svg+xml</dc:format><dc:type rdf:resource="http://purl.org/dc/dcmitype/StillImage"/><dc:title/></cc:Work></rdf:RDF></metadata><defs id="defs4159"/><g transform="matrix(1.25,0,0,-1.25,0,562.5)" id="g4163"><g id="g4165"/><g id="g4167"><path id="path4169" style="fill:#441188;fill-opacity:0;fill-rule:evenodd;stroke:none" d="m 410.8631,145.698 c 43.7972,43.7973 43.7972,114.8067 0,158.604 0,0 -106.5611,106.5611 -106.5611,106.5611 -43.7973,43.7972 -114.8067,43.7972 -158.604,0 0,0 -106.56109,-106.5611 -106.56109,-106.5611 -43.797259,-43.7973 -43.797259,-114.8067 0,-158.604 0,0 106.56109,-106.56109 106.56109,-106.56109 43.7973,-43.797259 114.8067,-43.797259 158.604,0 0,0 106.5611,106.56109 106.5611,106.56109 z"/><path id="path4171" style="fill:#ffffff;fill-opacity:1;fill-rule:nonzero;stroke:none" d="m 250,293.75 c 0,3.5156 -2.7344,6.25 -6.25,6.25 0,0 -12.5,0 -12.5,0 -3.5156,0 -6.25,-2.7344 -6.25,-6.25 0,0 0,-68.75 0,-68.75 0,0 -43.75,0 -43.75,0 -3.5156,0 -6.25,-2.7344 -6.25,-6.25 0,0 0,-12.5 0,-12.5 0,-3.5156 2.7344,-6.25 6.25,-6.25 0,0 62.5,0 62.5,0 3.5156,0 6.25,2.7344 6.25,6.25 0,0 0,87.5 0,87.5 z M 331.25,225 c 0,-58.5938 -47.6563,-106.25 -106.25,-106.25 -58.5938,0 -106.25,47.6562 -106.25,106.25 0,58.5938 47.6562,106.25 106.25,106.25 58.5937,0 106.25,-47.6562 106.25,-106.25 z M 375,225 C 375,307.8125 307.8125,375 225,375 142.1875,375 75,307.8125 75,225 75,142.1875 142.1875,75 225,75 c 82.8125,0 150,67.1875 150,150 z"/></g></g></svg>"""
                )
            with open(os.path.join(os.path.split(playlist_filename)[0], "images", "bpm.svg"), "w") as fo:
                fo.write(
                    """<?xml version="1.0" encoding="UTF-8" standalone="no"?>
                    <svg xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:cc="http://creativecommons.org/ns#" xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:svg="http://www.w3.org/2000/svg" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 562.5 562.5" height="562.5" width="562.5" xml:space="preserve" version="1.1" id="svg4155"><metadata id="metadata4161"><rdf:RDF><cc:Work rdf:about=""><dc:format>image/svg+xml</dc:format><dc:type rdf:resource="http://purl.org/dc/dcmitype/StillImage"/><dc:title/></cc:Work></rdf:RDF></metadata><defs id="defs4159"/><g transform="matrix(1.25,0,0,-1.25,0,562.5)" id="g4163"><g id="g4165"/><g id="g4167"><path id="path4169" style="fill:#441188;fill-opacity:0;fill-rule:evenodd;stroke:none" d="m 410.8631,145.698 c 43.7972,43.7973 43.7972,114.8067 0,158.604 0,0 -106.5611,106.5611 -106.5611,106.5611 -43.7973,43.7972 -114.8067,43.7972 -158.604,0 0,0 -106.56109,-106.5611 -106.56109,-106.5611 -43.797259,-43.7973 -43.797259,-114.8067 0,-158.604 0,0 106.56109,-106.56109 106.56109,-106.56109 43.7973,-43.797259 114.8067,-43.797259 158.604,0 0,0 106.5611,106.56109 106.5611,106.56109 z"/><path id="path4171" style="fill:#ffffff;fill-opacity:1;fill-rule:nonzero;stroke:none" d="m 331.25,225 c 0,-58.5938 -47.6563,-106.25 -106.25,-106.25 -58.5938,0 -106.25,47.6562 -106.25,106.25 0,58.5938 47.6562,106.25 106.25,106.25 58.5937,0 106.25,-47.6562 106.25,-106.25 z M 375,225 C 375,307.8125 307.8125,375 225,375 142.1875,375 75,307.8125 75,225 75,142.1875 142.1875,75 225,75 c 82.8125,0 150,67.1875 150,150 z"/><path id="path4173" style="fill:#ffffff;fill-opacity:1;fill-rule:evenodd;stroke:none" d="m 178.3058,157.4747 c 0,0 -0.9539,0.0227 -0.9539,0.0227 0,0 -0.9517,0.0683 -0.9517,0.0683 0,0 -0.9473,0.1135 -0.9473,0.1135 0,0 -0.9408,0.1586 -0.9408,0.1586 0,0 -0.9322,0.2033 -0.9322,0.2033 0,0 -0.9215,0.2475 -0.9215,0.2475 0,0 -0.9086,0.2911 -0.9086,0.2911 0,0 -0.8937,0.3342 -0.8937,0.3342 0,0 -0.8767,0.3764 -0.8767,0.3764 0,0 -0.8578,0.4178 -0.8578,0.4178 0,0 -0.8369,0.4582 -0.8369,0.4582 0,0 -0.814,0.4977 -0.814,0.4977 0,0 -0.7895,0.5358 -0.7895,0.5358 0,0 -0.7629,0.573 -0.7629,0.573 0,0 -0.7348,0.6086 -0.7348,0.6086 0,0 -0.7049,0.643 -0.7049,0.643 0,0 -0.6734,0.6759 -0.6734,0.6759 0,0 -0.6404,0.7072 -0.6404,0.7072 0,0 -0.606,0.737 -0.606,0.737 0,0 -0.5701,0.7651 -0.5701,0.7651 0,0 -0.533,0.7913 -0.533,0.7913 0,0 -0.4947,0.8159 -0.4947,0.8159 0,0 -0.4552,0.8385 -0.4552,0.8385 0,0 -0.4146,0.8593 -0.4146,0.8593 0,0 -0.3732,0.8781 -0.3732,0.8781 0,0 -0.331,0.8949 -0.331,0.8949 0,0 -0.2878,0.9097 -0.2878,0.9097 0,0 -0.2442,0.9223 -0.2442,0.9223 0,0 -0.1998,0.933 -0.1998,0.933 0,0 -0.1552,0.9414 -0.1552,0.9414 0,0 -0.1101,0.9477 -0.1101,0.9477 0,0 -0.0647,0.9519 -0.0647,0.9519 0,0 -0.0193,0.954 -0.0193,0.954 0,0 0.0262,0.9537 0.0262,0.9537 0,0 0.0717,0.9514 0.0717,0.9514 0,0 0.117,0.947 0.117,0.947 0,0 0.162,0.9402 0.162,0.9402 0,0 0.2067,0.9315 0.2067,0.9315 0,0 0.2508,0.9205 0.2508,0.9205 0,0 0.2945,0.9076 0.2945,0.9076 0,0 0.3374,0.8924 0.3374,0.8924 0,0 46.8116,115.4124 46.8116,115.4124 0,0 0.3771,0.8701 0.3771,0.8701 0,0 0.418,0.8512 0.418,0.8512 0,0 0.4579,0.8304 0.4579,0.8304 0,0 0.4967,0.8078 0.4967,0.8078 0,0 0.5344,0.7834 0.5344,0.7834 0,0 0.571,0.7571 0.571,0.7571 0,0 0.6062,0.7292 0.6062,0.7292 0,0 0.6401,0.6997 0.6401,0.6997 0,0 0.6725,0.6685 0.6725,0.6685 0,0 0.7035,0.6359 0.7035,0.6359 0,0 0.7328,0.6018 0.7328,0.6018 0,0 0.7606,0.5665 0.7606,0.5665 0,0 0.7865,0.5297 0.7865,0.5297 0,0 0.8107,0.4919 0.8107,0.4919 0,0 0.8332,0.4529 0.8332,0.4529 0,0 0.8537,0.4128 0.8537,0.4128 0,0 0.8723,0.372 0.8723,0.372 0,0 0.8889,0.3302 0.8889,0.3302 0,0 0.9036,0.2877 0.9036,0.2877 0,0 0.9162,0.2445 0.9162,0.2445 0,0 0.9268,0.2008 0.9268,0.2008 0,0 0.9353,0.1567 0.9353,0.1567 0,0 0.9416,0.1121 0.9416,0.1121 0,0 0.9459,0.0674 0.9459,0.0674 0,0 0.948,0.0225 0.948,0.0225 0,0 0.948,-0.0225 0.948,-0.0225 0,0 0.9459,-0.0674 0.9459,-0.0674 0,0 0.9416,-0.1121 0.9416,-0.1121 0,0 0.9353,-0.1567 0.9353,-0.1567 0,0 0.9268,-0.2008 0.9268,-0.2008 0,0 0.9162,-0.2445 0.9162,-0.2445 0,0 0.9036,-0.2877 0.9036,-0.2877 0,0 0.8889,-0.3302 0.8889,-0.3302 0,0 0.8723,-0.372 0.8723,-0.372 0,0 0.8537,-0.4128 0.8537,-0.4128 0,0 0.8332,-0.4529 0.8332,-0.4529 0,0 0.8107,-0.4919 0.8107,-0.4919 0,0 0.7866,-0.5297 0.7866,-0.5297 0,0 0.7605,-0.5665 0.7605,-0.5665 0,0 0.7328,-0.6018 0.7328,-0.6018 0,0 0.7035,-0.6359 0.7035,-0.6359 0,0 0.6725,-0.6685 0.6725,-0.6685 0,0 0.6401,-0.6997 0.6401,-0.6997 0,0 0.6062,-0.7292 0.6062,-0.7292 0,0 0.571,-0.7571 0.571,-0.7571 0,0 0.5344,-0.7834 0.5344,-0.7834 0,0 0.4967,-0.8078 0.4967,-0.8078 0,0 0.4579,-0.8304 0.4579,-0.8304 0,0 0.418,-0.8512 0.418,-0.8512 0,0 0.3771,-0.8701 0.3771,-0.8701 0,0 46.8116,-115.4124 46.8116,-115.4124 0,0 0.3374,-0.8924 0.3374,-0.8924 0,0 0.2945,-0.9076 0.2945,-0.9076 0,0 0.2508,-0.9205 0.2508,-0.9205 0,0 0.2067,-0.9315 0.2067,-0.9315 0,0 0.162,-0.9402 0.162,-0.9402 0,0 0.117,-0.947 0.117,-0.947 0,0 0.0717,-0.9514 0.0717,-0.9514 0,0 0.0262,-0.9537 0.0262,-0.9537 0,0 -0.0192,-0.954 -0.0192,-0.954 0,0 -0.0648,-0.9519 -0.0648,-0.9519 0,0 -0.1101,-0.9477 -0.1101,-0.9477 0,0 -0.1551,-0.9414 -0.1551,-0.9414 0,0 -0.1999,-0.933 -0.1999,-0.933 0,0 -0.2442,-0.9223 -0.2442,-0.9223 0,0 -0.2878,-0.9097 -0.2878,-0.9097 0,0 -0.3309,-0.8949 -0.3309,-0.8949 0,0 -0.3732,-0.8781 -0.3732,-0.8781 0,0 -0.4147,-0.8593 -0.4147,-0.8593 0,0 -0.4552,-0.8385 -0.4552,-0.8385 0,0 -0.4946,-0.8159 -0.4946,-0.8159 0,0 -0.533,-0.7913 -0.533,-0.7913 0,0 -0.5702,-0.7651 -0.5702,-0.7651 0,0 -0.6059,-0.737 -0.6059,-0.737 0,0 -0.6405,-0.7072 -0.6405,-0.7072 0,0 -0.6734,-0.6759 -0.6734,-0.6759 0,0 -0.7049,-0.643 -0.7049,-0.643 0,0 -0.7348,-0.6086 -0.7348,-0.6086 0,0 -0.7629,-0.573 -0.7629,-0.573 0,0 -0.7894,-0.5358 -0.7894,-0.5358 0,0 -0.8141,-0.4977 -0.8141,-0.4977 0,0 -0.8369,-0.4582 -0.8369,-0.4582 0,0 -0.8578,-0.4178 -0.8578,-0.4178 0,0 -0.8767,-0.3764 -0.8767,-0.3764 0,0 -0.8937,-0.3342 -0.8937,-0.3342 0,0 -0.9086,-0.2911 -0.9086,-0.2911 0,0 -0.9214,-0.2475 -0.9214,-0.2475 0,0 -0.9322,-0.2033 -0.9322,-0.2033 0,0 -0.9409,-0.1586 -0.9409,-0.1586 0,0 -0.9473,-0.1135 -0.9473,-0.1135 0,0 -0.9517,-0.0683 -0.9517,-0.0683 0,0 -0.9538,-0.0227 -0.9538,-0.0227 0,0 -93.6231,0 -93.6231,0 z m 0,20 c 0,0 93.6231,0 93.6231,0 0,0 -46.8116,115.4124 -46.8116,115.4124 0,0 -46.8115,-115.4124 -46.8115,-115.4124 z"/><g transform="matrix(0.75,0,0,-0.75,0,450)" id="g4175"><path id="path4177" style="fill:none;stroke:#ffffff;stroke-width:26.66666985;stroke-linecap:round;stroke-linejoin:round;stroke-miterlimit:1.41421402;stroke-dasharray:none;stroke-opacity:1" d="m 307.0915,367.5999 c 0,0 -84.6338,-106.4347 -84.6338,-106.4347"/></g></g></g></svg>"""
                )
            with open(os.path.join(os.path.split(playlist_filename)[0], "images", "count_circles.svg"), "w") as fo:
                fo.write(
                    """<?xml version="1.0" encoding="UTF-8" standalone="no"?>
                    <svg xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:cc="http://creativecommons.org/ns#" xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:svg="http://www.w3.org/2000/svg" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 562.5 562.5" height="562.5" width="562.5" xml:space="preserve" version="1.1" id="svg4155"><metadata id="metadata4161"><rdf:RDF><cc:Work rdf:about=""><dc:format>image/svg+xml</dc:format><dc:type rdf:resource="http://purl.org/dc/dcmitype/StillImage"/><dc:title/></cc:Work></rdf:RDF></metadata><defs id="defs4159"/><g transform="matrix(1.25,0,0,-1.25,0,562.5)" id="g4163"><g id="g4165"/><g id="g4167"><path id="path4169" style="fill:#441188;fill-opacity:0;fill-rule:evenodd;stroke:none" d="m 410.8631,145.698 c 43.7972,43.7973 43.7972,114.8067 0,158.604 0,0 -106.5611,106.5611 -106.5611,106.5611 -43.7973,43.7972 -114.8067,43.7972 -158.604,0 0,0 -106.56109,-106.5611 -106.56109,-106.5611 -43.797259,-43.7973 -43.797259,-114.8067 0,-158.604 0,0 106.56109,-106.56109 106.56109,-106.56109 43.7973,-43.797259 114.8067,-43.797259 158.604,0 0,0 106.5611,106.56109 106.5611,106.56109 z"/><path id="path4171" style="fill:#ffffff;fill-opacity:1;fill-rule:nonzero;stroke:none" d="m 331.25,225 c 0,-58.5938 -47.6563,-106.25 -106.25,-106.25 -58.5938,0 -106.25,47.6562 -106.25,106.25 0,58.5938 47.6562,106.25 106.25,106.25 58.5937,0 106.25,-47.6562 106.25,-106.25 z M 375,225 C 375,307.8125 307.8125,375 225,375 142.1875,375 75,307.8125 75,225 75,142.1875 142.1875,75 225,75 c 82.8125,0 150,67.1875 150,150 z"/><g transform="matrix(0.75,0,0,-0.75,0,450)" id="g4173"><path id="path4175" style="fill:none;stroke:#ffffff;stroke-width:26.66666985;stroke-linecap:round;stroke-linejoin:round;stroke-miterlimit:1.41421402;stroke-dasharray:none;stroke-opacity:1" d="m 300,218.7598 c 44.8677,0 81.2402,36.3725 81.2402,81.2402 0,44.8677 -36.3725,81.2402 -81.2402,81.2402 -44.8677,0 -81.2402,-36.3725 -81.2402,-81.2402 0,-44.8677 36.3725,-81.2402 81.2402,-81.2402 z"/></g></g></g></svg>"""
                )
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
            if raw_mods[j]["acronym"] == "FM" or raw_mods[j]["acronym"] == "F+":
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
        cs = "%s" % round(my_attr.cs, 2)
        ar = "%s" % round(rosu_attr.ar, 2)
        od = "%s" % round(rosu_attr.od, 2)
        cs_pct = calc_positive_percent(my_attr.cs, 0, 10)
        ar_pct = calc_positive_percent(rosu_attr.ar, 0, 10)
        od_pct = calc_positive_percent(rosu_attr.od, 0, 10)
        bpm = "%s" % round(my_attr.bpm, 2)
        song_len_in_sec = my_attr.hit_length
        song_len_m, song_len_s = divmod(song_len_in_sec, 60)
        hit_length = "%d:%02d" % (song_len_m, song_len_s)
        max_combo = "%d" % rosu_attr.max_combo

        # 绘制cover
        cover = BeatmapCover(b, self.mod_color.get(color_mod, "#eb50eb"), stars1, cs, ar, od, bpm, hit_length, max_combo, stars2)
        cover_filename = await cover.download(self.covers_d, "%d-%d.jpg" % (i, bid))
        img_src = "./" + (os.path.relpath(cover_filename, os.path.split(self.playlist_filename)[0])).replace("\\", "/")
        img_link = "https://osu.ppy.sh/b/%d" % b.id
        beatmap_info = '<a href="%s"><img src="%s" alt="%s - %s (%s) [%s]" height="90"/></a>' % (
            img_link,
            img_src,
            html.escape(b.beatmapset.artist),
            html.escape(b.beatmapset.title),
            html.escape(b.beatmapset.creator),
            html.escape(b.version),
        )
        await cover.draw(cover_filename)
        if self.css_style:
            # 将背景图片保存在统一文件夹内以减小占用
            if not os.path.exists(os.path.join(self.bg_dir, "%d.jpg" % bid)):
                bg_filename = await self.bg_d.async_start(b.beatmapset.background_url, "%d" % bid, headers)
                try:
                    im = Image.open(bg_filename)
                except UnidentifiedImageError:
                    im = Image.open("./osuawa/bg1.jpg")
                    im = im.filter(ImageFilter.BLUR)
                if im.mode != "RGB":
                    im = im.convert("RGB")
                be = ImageEnhance.Brightness(im)
                im = be.enhance(0.67)
                im.save(bg_filename)
            bg_filename = os.path.join(self.bg_dir, "%d.jpg" % bid)
            extra_notes = ""
            for column in self.custom_columns:
                if column == "mods":
                    continue
                else:
                    extra_notes += "<br />%s: %s" % (column, element[column])
            beatmap_info = f'''      <div class="group relative">
        <div
          class="relative h-32 rounded-lg overflow-hidden shadow-lg transition-all duration-300 transform group-hover:rounded-b-none group-hover:h-64 group-hover:-translate-y-3">
          <img src="{"./" + (os.path.relpath(bg_filename, os.path.split(self.playlist_filename)[0])).replace("\\", "/")}" alt="{html.escape(b.beatmapset.artist)} - {html.escape(b.beatmapset.title)} ({html.escape(b.beatmapset.creator)}) [{html.escape(b.version)}]"
            class="w-full h-full object-cover brightness-90 dark:brightness-50 blur-0 contrast-100 scale-100 group-hover:brightness-50 dark:group-hover:brightness-50 group-hover:blur-sm group-hover:contrast-125 group-hover:scale-105 transition-all duration-300" />
          <div class="absolute inset-0 p-4 flex flex-col justify-between">
            <div class="flex justify-between items-start">
              <div class="px-3 py-1 rounded-full text-white font-semibold shadow" style="background-color: {calc_star_rating_color(stars1)};">
                <div style="color: {cover.stars_text_color}; opacity: {'1' if cover.is_high_stars else '0.8'}; text-shadow: 0px 0.5px 1.5px rgba(185, 185, 185, 0.5);"><i class="fas fa-star" {'' if cover.is_high_stars else 'style="color: #0f172a;"'}></i> {cover.stars.replace("󰓎", "")}</div>
              </div>
              <div class="flex gap-2 card-main">
                {"".join([f'<span class="px-2 py-1 rounded text-white text-sm font-semibold shadow" style="background-color: {self.mod_color.get(mod["acronym"], "#eb50eb")}">{mod["acronym"]}</span>' for mod in raw_mods])}
              </div>
            </div>
            <div class="text-white card-main" style="padding-top: 1rem">
              <h3 class="text-xl font-bold mb-1 line-clamp-1 overflow-ellipsis overflow-hidden group-hover:line-clamp-2">{html.escape(b.beatmapset.title_unicode)}</h3>
              <p class="font-semibold overflow-ellipsis overflow-hidden whitespace-nowrap">{html.escape(b.beatmapset.artist_unicode)}</p>
              <div class="opacity-0 group-hover:opacity-100 transition-opacity duration-300" style="padding-top: 0.5rem; padding-bottom: 0.5rem;">
                <p class="text-xs overflow-ellipsis overflow-hidden whitespace-nowrap" style="opacity: 0.88; line-height: 1.5;">Mapper: <a class="font-semibold">{html.escape(b.beatmapset.creator)}</a></p>
                <p class="text-xs overflow-ellipsis overflow-hidden whitespace-nowrap" style="opacity: 0.88; line-height: 1.5;">Difficulty: <span class="font-semibold">{html.escape(b.version)}</span></p>
                <p class="text-xs overflow-ellipsis overflow-hidden whitespace-nowrap" style="opacity: 0.88; line-height: 1.5;">Beatmap ID: <span class="font-semibold">{b.id}</span></p>
                <div class="items-center text-xs w-full grid grid-cols-12 mt-1">
                  <div class="col-span-3">
                    <div class="flex items-center justify-between" style="opacity: 0.88; line-height: 1.5;"><span>CS</span>
                      <div class="flex items-center flex-1 w-full h-2 bg-gray-600 rounded mx-2">
                        <div class="h-full rounded" style="background-color: white; width: {cs_pct}%"></div>
                      </div>
                    </div>
                  </div>
                  <div class="font-semibold">{cover.cs}</div>
                  <div class="col-span-3">
                    <div class="flex items-center justify-between" style="opacity: 0.88; line-height: 1.5;"><span>AR</span>
                      <div class="flex items-center flex-1 w-full h-2 bg-gray-600 rounded mx-2">
                        <div class="h-full rounded" style="background-color: white; width: {ar_pct}%"></div>
                      </div>
                    </div>
                  </div>
                  <div class="font-semibold">{cover.ar}</div>
                  <div class="col-span-3">
                    <div class="flex items-center justify-between" style="opacity: 0.88; line-height: 1.5;"><span>OD</span>
                      <div class="flex items-center flex-1 w-full h-2 bg-gray-600 rounded mx-2">
                        <div class="h-full rounded" style="background-color: white; width: {od_pct}%"></div>
                      </div>
                    </div>
                  </div>
                  <div class="font-semibold">{cover.od}</div>
                </div>
                <div class="text-xs w-full grid grid-cols-3">
                  <div>
                    <div class="flex items-center justify-between" style="opacity: 0.88; line-height: 1.5;"><img src="./images/bpm.svg" class="w-4"/>
                      <div class="flex items-center flex-1 font-semibold ml-2">{cover.bpm}</div>
                    </div>
                  </div>
                  <div>
                    <div class="flex items-center justify-between" style="opacity: 0.88; line-height: 1.5;"><img src="./images/total_length.svg" class="w-4"/>
                      <div class="flex items-center flex-1 font-semibold ml-2">{cover.hit_length}</div>
                    </div>
                  </div>
                  <div>
                    <div class="flex items-center justify-between" style="opacity: 0.88; line-height: 1.5;"><img src="./images/count_circles.svg" class="w-4"/>
                      <div class="flex items-center flex-1 font-semibold ml-2">{cover.max_combo}</div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
        <div class="absolute py-2 z-10 w-full rounded-b-xl p-4 shadow-xl opacity-0 group-hover:opacity-100 transition-opacity duration-300 top-full -mt-3 notes">
          <p class="text-xs flex justify-between items-end">
            <span>{notes}{extra_notes}</span><a href="https://osu.ppy.sh/b/{b.id}"
              class="text-custom hover:text-custom-600"><i class="fas fa-external-link-alt"></i></a>
          </p>
        </div>
      </div>
'''

        # 保存数据
        completed_beatmap = {
            "#": i,
            "BID": b.id,
            "SID": b.beatmapset_id,
            "Beatmap Info (Click to View)": beatmap_info,
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
        df_columns = ["#", "BID", "Beatmap Info (Click to View)", "Mods", "BPM", "Hit Length", "Max Combo", "CS", "AR", "OD"]
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
        html_footer = "" if self.footer == "" else '<footer class="footer">%s</footer>' % self.footer
        html_string = """<html>

<head>
  <meta charset="UTF-8" />
  {html_head}
  <title>%s%s</title>
</head>
<link rel="stylesheet" type="text/css" href="style.css" />

<body>{html_body_prefix}{html_body}{html_body_suffix}</body>%s

</html>
""" % (
            self.playlist_name,
            self.suffix,
            html_footer,
        )
        with open(self.playlist_filename.replace(".properties", ".html"), "w", encoding="utf-8") as fo:
            if self.css_style:
                html_head = """  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet" />
  <link href="https://ai-public.mastergo.com/gen_page/tailwind-custom.css" rel="stylesheet" />
  <script
    src="https://cdn.tailwindcss.com/3.4.5?plugins=forms@0.5.7,typography@0.5.13,aspect-ratio@0.4.2,container-queries@0.1.1"></script>
  <script src="https://ai-public.mastergo.com/gen_page/tailwind-config.min.js" data-color="#A0C8C8"
    data-border-radius="medium"></script>
"""
                html_body_prefix = (
                    """
    <div class="min-h-screen p-8">
    <header class="mb-8">
      <h1 class="text-2xl font-bold text-center">
        %s
      </h1>
    </header>
    <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-8">
"""
                    % self.playlist_name
                )
                fo.write(html_string.format(html_head=html_head, html_body="".join([cb["Beatmap Info (Click to View)"] for cb in playlist]), html_body_prefix=html_body_prefix, html_body_suffix=html_body_suffix))
            else:
                fo.write(html_string.format(html_head="", html_body=df.to_html(index=False, escape=False, classes="pd"), html_body_prefix="", html_body_suffix=""))

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
