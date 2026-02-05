import asyncio
import ctypes
import datetime
import html
import os
import os.path
import platform
import threading
from asyncio import Task
from dataclasses import fields
from functools import cached_property
from shutil import rmtree
from threading import Lock
from typing import Any, Optional, TYPE_CHECKING

import numpy as np
import orjson
import pandas as pd
import typing_extensions

assets_dir = os.path.join(os.path.dirname(__file__))
if platform.system() == "Windows":
    fribidi = ctypes.CDLL(os.path.join(assets_dir, "fribidi-0.dll"))
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, UnidentifiedImageError
from clayutil.futil import Downloader, Properties
from clayutil.validator import Integer

from fontfallback import writing
from ossapi import Grant, OssapiAsync, Domain, Scope, Score, User, GameMode, Beatmap

from .utils import (
    ExtendedSimpleScoreInfo,
    SimpleOsuDifficultyAttribute,
    calc_beatmap_attributes,
    calc_positive_percent,
    calc_star_rating_color,
    download_osu,
    CompletedSimpleScoreInfo,
    a_get_beatmaps_dict,
    to_readable_mods,
    SimpleScoreInfo,
    C,
    headers,
    a_get_user_info,
    simple_user_dict,
    calculate_difficulty,
)

assert datetime
if TYPE_CHECKING:

    def _(text: str) -> str: ...


def strip_quotes(text: str) -> str:
    # 判断是否被引号包裹，若是，则 strip
    if text.startswith('"') and text.endswith('"'):
        return text.strip('"')
    if text.startswith("'") and text.endswith("'"):
        return text.strip("'")
    return text


class Awapi(OssapiAsync):
    def __init__(
        self,
        client_id: int,
        client_secret: str,
        redirect_uri: Optional[str] = None,
        scopes: Optional[list[str]] = None,
        *,
        grant: Optional[Grant | str] = None,
        strict: bool = False,
        token_directory: str = "./.streamlit/.oauth/",
        token_key: Optional[str] = None,
        access_token: Optional[str] = None,
        refresh_token: Optional[str] = None,
        domain: Domain | str = Domain.OSU,
        api_version: int | str = 20240529,
    ):
        if scopes is None:
            scopes = [Scope.PUBLIC]
        super().__init__(client_id, client_secret, redirect_uri, scopes, grant=grant, strict=strict, token_directory=token_directory, token_key=token_key, access_token=access_token, refresh_token=refresh_token, domain=domain, api_version=api_version)

    def _new_authorization_grant(self, client_id, client_secret, redirect_uri, scopes) -> None:
        raise NotImplementedError()


class Osuawa(object):
    tz = "Asia/Shanghai"
    common_mods = {
        "EZ",
        "NF",
        "HT",
        "DC",
        "HR",
        "SD",
        "PF",
        "DT",
        "NC",
        "HD",
        "CL",
        "SO",
    }

    def __init__(self, client_id, client_secret, redirect_url, scopes, domain, token_key: str, oauth_token: Optional[str], oauth_refresh_token: Optional[str]):
        self.api = Awapi(client_id, client_secret, redirect_url, scopes, domain=domain, token_key=token_key, access_token=oauth_token, refresh_token=oauth_refresh_token)

    @cached_property
    def user(self) -> tuple[int, str]:
        """Get own user id and username

        :return: (user_id, username)
        """
        own_data: User = asyncio.run(self.api.get_me())
        return own_data.id, own_data.username

    def create_scores_dataframe(self, scores_compact: dict[str, CompletedSimpleScoreInfo]) -> pd.DataFrame:
        df = pd.DataFrame.from_dict(
            scores_compact,
            orient="index",
            columns=[f.name for f in fields(CompletedSimpleScoreInfo)],
        )
        df.reset_index(inplace=True)
        df.rename(columns={"index": "score_id"}, inplace=True)
        df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.tz_convert(self.tz)
        df["st"] = pd.to_datetime(df["st"], utc=True).dt.tz_convert(self.tz)
        ec = ExtendedSimpleScoreInfo.__slots__
        df[ec[0]] = df["ts"].dt.hour * 3600 + df["ts"].dt.minute * 60 + df["ts"].dt.second
        df[ec[1]] = df["pp"] / df["b_pp_100if"]
        df[ec[2]] = df["pp_aim"] / df["b_pp_100if_aim"]
        df[ec[3]] = df["pp_speed"] / df["b_pp_100if_speed"]
        df[ec[4]] = df["pp_accuracy"] / df["b_pp_100if_accuracy"]
        df[ec[5]] = df["pp"] / df["b_pp_92if"]
        df[ec[6]] = df["pp"] / df["b_pp_81if"]
        df[ec[7]] = df["pp"] / df["b_pp_67if"]
        df[ec[8]] = df["max_combo"] / df["b_max_combo"]
        df[ec[9]] = df["b_max_combo"] / df["hit_length"]
        df[ec[10]] = df["b_aim_difficulty"] / np.log1p(df["density"])
        df[ec[11]] = df["b_speed_difficulty"] / np.log1p(df["density"])
        df[ec[12]] = df["b_aim_difficulty"] / df["b_speed_difficulty"]
        df[ec[13]] = np.where(df["is_nf"], df["score"] * 2, df["score"])
        df[ec[14]] = df["_mods"].apply(lambda x: "; ".join(to_readable_mods(x)))
        df[ec[15]] = df["_mods"].map(
            lambda mods: ({m["acronym"] for m in mods} <= self.common_mods),
        )
        return df

    def get_user_info(self, username: str) -> dict[str, Any]:
        return asyncio.run(a_get_user_info(self.api, username))

    async def complete_scores_compact(self, scores_compact: dict[str, SimpleScoreInfo]) -> dict[str, CompletedSimpleScoreInfo]:
        beatmaps_dict = await a_get_beatmaps_dict(self.api, [x.bid for x in scores_compact.values()])
        return {score_id: calc_beatmap_attributes(beatmaps_dict[scores_compact[score_id].bid], scores_compact[score_id]) for score_id in scores_compact}

    async def a_get_friends(self) -> list[dict[str, Any]]:
        friends = await self.api.friends()
        tasks: list[Task[dict[str, Any]]] = []
        async with asyncio.TaskGroup() as tg:
            for friend in friends:
                tasks.append(tg.create_task(simple_user_dict(friend)))
        return [task.result() for task in tasks]

    async def a_get_score(self, score_id: int) -> dict[str, CompletedSimpleScoreInfo]:
        score = await self.api.score(score_id)
        score_compact = {str(score.id): SimpleScoreInfo.from_score(score)}
        return await self.complete_scores_compact(score_compact)

    def get_score(self, score_id: int) -> pd.DataFrame:
        return self.create_scores_dataframe(asyncio.run(self.a_get_score(score_id)))

    async def a_get_user_beatmap_scores(self, beatmap: int, user: int) -> dict[str, CompletedSimpleScoreInfo]:
        user_scores = await self.api.beatmap_user_scores(beatmap, user)
        scores_compact = {str(x.id): SimpleScoreInfo.from_score(x) for x in user_scores}
        return await self.complete_scores_compact(scores_compact)

    def get_user_beatmap_scores(self, beatmap: int, user: Optional[int] = None) -> pd.DataFrame:
        if user is None:
            user = self.user[0]
        return self.create_scores_dataframe(asyncio.run(self.a_get_user_beatmap_scores(beatmap, user)))

    async def a_get_recent_scores(self, user: int, include_fails: bool = True) -> list[Score]:
        user_scores = []
        offset = 0
        while True:
            user_scores_recent = await self.api.user_scores(
                user_id=user,
                type="recent",
                mode=GameMode.OSU,
                include_fails=include_fails,
                limit=50,
                offset=offset,
            )
            if len(user_scores_recent) == 0:
                break
            user_scores.extend(user_scores_recent)
            offset += 50

        return user_scores


def cut_text(draw: ImageDraw.ImageDraw, font, text: str, length_limit: float, use_dots: bool) -> str | int:
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
    font_sans = os.path.join(assets_dir, "ResourceHanRoundedSC-Regular.ttf")
    font_sans_fallback = os.path.join(assets_dir, "DejaVuSansCondensed.ttf")
    font_sans_medium = os.path.join(assets_dir, "ResourceHanRoundedSC-Medium.ttf")
    font_mono_regular = os.path.join(assets_dir, "MapleMono-NF-CN-Regular.ttf")
    font_mono_italic = os.path.join(assets_dir, "MapleMono-NF-CN-Italic.ttf")
    font_mono_semibold = os.path.join(assets_dir, "MapleMono-NF-CN-SemiBold.ttf")

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
        cover_filename = await d.async_start(self.beatmap.beatmapset().covers.cover_2x, filename, headers)
        try:
            im: Image.Image = Image.open(cover_filename)
        except UnidentifiedImageError:
            try:
                im = Image.open(await d.async_start(self.beatmap.beatmapset().covers.slimcover_2x, filename, headers))
            except UnidentifiedImageError:
                im = Image.open(os.path.join(assets_dir, "bg1.jpg"))
                im = im.filter(ImageFilter.BLUR)
        im = im.resize((1296, int(im.height * 1296 / im.width)), Image.Resampling.LANCZOS)  # 缩放到宽为 1296
        im = im.crop((im.width // 2 - 648, 0, im.width // 2 + 648, 360))  # 从中间裁剪到 1296 x 360

        # 调整亮度
        if im.mode != "RGB":
            im = im.convert("RGB")
        be = ImageEnhance.Brightness(im)
        im = be.enhance(0.33)
        with Lock():
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
        title_u = self.beatmap.beatmapset().title_unicode
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
        # noinspection PyTypeChecker
        writing.draw_text_v2(draw, (42, 29 + 298), version, "#1f1f1f", fonts, 48, "ls")
        # noinspection PyTypeChecker
        writing.draw_text_v2(draw, (40, 26 + 298), version, "white", fonts, 48, "ls")
        # noinspection PyTypeChecker
        writing.draw_text_v2(draw, (40, 27 + 298), version, "white", fonts, 48, "ls")
        # noinspection PyTypeChecker
        writing.draw_multiline_text_v2(draw, (42, 192 - 88), title_u, "#1f1f1f", fonts, 72, "ls")
        # noinspection PyTypeChecker
        writing.draw_multiline_text_v2(draw, (42, 191 - 88), title_u, "#1f1f1f", fonts, 72, "ls")
        writing.draw_multiline_text_v2(draw, (42, 193 - 88), title_u, (40, 40, 40), fonts, 72, "ls")
        writing.draw_multiline_text_v2(draw, (41, 193 - 88), title_u, (40, 40, 40), fonts, 72, "ls")
        writing.draw_multiline_text_v2(draw, (41, 192 - 88), title_u, (40, 40, 40), fonts, 72, "ls")
        # noinspection PyTypeChecker
        writing.draw_multiline_text_v2(draw, (40, 189 - 88), title_u, "white", fonts, 72, "ls")
        # noinspection PyTypeChecker
        writing.draw_multiline_text_v2(draw, (41, 189 - 88), title_u, "white", fonts, 72, "ls")
        # noinspection PyTypeChecker
        writing.draw_multiline_text_v2(draw, (40, 190 - 88), title_u, "white", fonts, 72, "ls")
        # noinspection PyTypeChecker
        writing.draw_multiline_text_v2(draw, (41, 190 - 88), title_u, "white", fonts, 72, "ls")
        # noinspection PyTypeChecker
        writing.draw_text_v2(draw, (42, 260), self.beatmap.beatmapset().artist_unicode, "#1f1f1f", fonts, 48, "ls")
        # noinspection PyTypeChecker
        writing.draw_text_v2(draw, (40, 257), self.beatmap.beatmapset().artist_unicode, "white", fonts, 48, "ls")
        # noinspection PyTypeChecker
        writing.draw_text_v2(draw, (40, 258), self.beatmap.beatmapset().artist_unicode, "white", fonts, 48, "ls")
        draw.text((42 + 1188, 326), self.beatmap.beatmapset().creator, font=ImageFont.truetype(font=self.font_sans_medium, size=48), fill="#1f1f2a", anchor="rs")
        draw.text((41 + 1188, 324), self.beatmap.beatmapset().creator, font=ImageFont.truetype(font=self.font_sans_medium, size=48), fill=(180, 235, 250), anchor="rs")
        draw.text((40 + 1188, 324), self.beatmap.beatmapset().creator, font=ImageFont.truetype(font=self.font_sans_medium, size=48), fill=(180, 235, 250), anchor="rs")

        # 在右上角绘制星数
        draw.rounded_rectangle([len_set + text_pos - stars_len - padding, 32, len_set + text_pos + padding, 106], 72, fill="#1f1f1f")
        draw.rounded_rectangle([len_set + text_pos - stars_len - padding, 30, len_set + text_pos + padding, 104], 72, fill=calc_star_rating_color(self.stars1))

        draw.text((len_set + text_pos, 37), self.stars, anchor="ra", font=ImageFont.truetype(font=self.font_mono_semibold, size=48), fill=self.stars_text_color)

        # 绘制mod主题色
        draw.rectangle((len_set + text_pos + mod_theme_len, 0, 1296, 1080), fill=(40, 40, 40))
        draw.rectangle((len_set + text_pos + mod_theme_len, 0, 1296, 1080), fill=self.block_color)

        with Lock():
            im.save(cover_filename)
        return cover_filename


class ParsedPlaylistBeatmap(typing_extensions.TypedDict, total=False, extra_items=Any):  # type: ignore[call-arg]
    bid: int
    mods: list[dict[str, Any]]
    notes: str
    beatmap: Beatmap


class OsuPlaylist(object):
    css_style = Integer(1, 2, True)
    custom_mods_acronym = {"NM", "TB", "FM", "F+", "SP"}
    mod_color = {"NM": "#107fb9", "HD": "#b97f10", "HR": "#b91010", "EZ": "#10b97f", "DT": "#7f10b9", "NC": "#b9107f", "HT": "#7f7f7f", "FM": "#40507f", "TB": "#7f4050", "F+": "#507f40"}

    # osz_type = OneOf("full", "novideo", "mini")

    def __init__(self, awa: Osuawa, playlist_filename: str, suffix: str = "", css_style: Optional[int] = None):
        self.awa = awa
        p = Properties(playlist_filename)
        p.load()
        self.playlist_filename = playlist_filename
        self.suffix = suffix
        self.css_style = css_style
        self.footer = strip_quotes(p.pop("footer")) if "footer" in p else ""
        self.banner = ""
        if "banner" in p:
            banner_img_src = strip_quotes(p.pop("banner"))
            self.banner = """
    <div class="relative w-full h-[90] sm:h-[135] lg:h-[185] hover:h-1/2 bg-cover bg-no-repeat bg-center object-cover transition-all duration-300 transform" style="background-image: url(%s)"><div class="absolute inset-0 banner-mask"></div>
    </div>
""" % banner_img_src
        self.custom_columns = orjson.loads(p.pop("custom_columns")) if "custom_columns" in p else []

        parsed_beatmap_list: list[ParsedPlaylistBeatmap] = []

        # pop p from end until empty
        current_parsed_beatmap: ParsedPlaylistBeatmap = {"notes": ""}
        while p:
            k, v = p.popitem()
            if k[0] == "#":  # notes
                current_parsed_beatmap["notes"] += v.lstrip("#").lstrip(" ")
            else:
                current_parsed_beatmap["bid"] = int(k)
                obj_v = orjson.loads(v)
                if self.custom_columns:
                    for column in self.custom_columns:
                        current_parsed_beatmap[column] = obj_v.get(column)  # type: ignore[literal-required]
                else:
                    current_parsed_beatmap["mods"] = obj_v
                parsed_beatmap_list.insert(0, current_parsed_beatmap)
                current_parsed_beatmap = {"notes": ""}

        beatmaps_dict = asyncio.run(a_get_beatmaps_dict(self.awa.api, [int(x["bid"]) for x in parsed_beatmap_list]))
        for element in parsed_beatmap_list:
            element["notes"] = element["notes"].rstrip("\n").replace("\n", "<br />")
            element["beatmap"] = beatmaps_dict[element["bid"]]
        self.beatmap_list = parsed_beatmap_list
        self.covers_dir = os.path.splitext(playlist_filename)[0] + ".covers"
        self.tmp_dir = os.path.splitext(playlist_filename)[0] + ".tmp"
        self.bg_dir = os.path.join(os.path.split(playlist_filename)[0], "darkened-backgrounds")
        self.tmp_d = Downloader(self.tmp_dir)
        if not os.path.exists(os.path.join(os.path.split(playlist_filename)[0], "images")):
            os.mkdir(os.path.join(os.path.split(playlist_filename)[0], "images"))
            with open(os.path.join(os.path.split(playlist_filename)[0], "images", "total_length.svg"), "w") as fo:
                fo.write(
                    """<?xml version="1.0" encoding="UTF-8" standalone="no"?>
                    <svg xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:cc="http://creativecommons.org/ns#" xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:svg="http://www.w3.org/2000/svg" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 562.5 562.5" height="562.5" width="562.5" xml:space="preserve" version="1.1" id="svg4155"><metadata id="metadata4161"><rdf:RDF><cc:Work rdf:about=""><dc:format>image/svg+xml</dc:format><dc:type rdf:resource="http://purl.org/dc/dcmitype/StillImage"/><dc:title/></cc:Work></rdf:RDF></metadata><defs id="defs4159"/><g transform="matrix(1.25,0,0,-1.25,0,562.5)" id="g4163"><g id="g4165"/><g id="g4167"><path id="path4169" style="fill:#441188;fill-opacity:0;fill-rule:evenodd;stroke:none" d="m 410.8631,145.698 c 43.7972,43.7973 43.7972,114.8067 0,158.604 0,0 -106.5611,106.5611 -106.5611,106.5611 -43.7973,43.7972 -114.8067,43.7972 -158.604,0 0,0 -106.56109,-106.5611 -106.56109,-106.5611 -43.797259,-43.7973 -43.797259,-114.8067 0,-158.604 0,0 106.56109,-106.56109 106.56109,-106.56109 43.7973,-43.797259 114.8067,-43.797259 158.604,0 0,0 106.5611,106.56109 106.5611,106.56109 z"/><path id="path4171" style="fill:#ffffff;fill-opacity:1;fill-rule:nonzero;stroke:none" d="m 250,293.75 c 0,3.5156 -2.7344,6.25 -6.25,6.25 0,0 -12.5,0 -12.5,0 -3.5156,0 -6.25,-2.7344 -6.25,-6.25 0,0 0,-68.75 0,-68.75 0,0 -43.75,0 -43.75,0 -3.5156,0 -6.25,-2.7344 -6.25,-6.25 0,0 0,-12.5 0,-12.5 0,-3.5156 2.7344,-6.25 6.25,-6.25 0,0 62.5,0 62.5,0 3.5156,0 6.25,2.7344 6.25,6.25 0,0 0,87.5 0,87.5 z M 331.25,225 c 0,-58.5938 -47.6563,-106.25 -106.25,-106.25 -58.5938,0 -106.25,47.6562 -106.25,106.25 0,58.5938 47.6562,106.25 106.25,106.25 58.5937,0 106.25,-47.6562 106.25,-106.25 z M 375,225 C 375,307.8125 307.8125,375 225,375 142.1875,375 75,307.8125 75,225 75,142.1875 142.1875,75 225,75 c 82.8125,0 150,67.1875 150,150 z"/></g></g></svg>""",
                )
            with open(os.path.join(os.path.split(playlist_filename)[0], "images", "bpm.svg"), "w") as fo:
                fo.write(
                    """<?xml version="1.0" encoding="UTF-8" standalone="no"?>
                    <svg xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:cc="http://creativecommons.org/ns#" xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:svg="http://www.w3.org/2000/svg" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 562.5 562.5" height="562.5" width="562.5" xml:space="preserve" version="1.1" id="svg4155"><metadata id="metadata4161"><rdf:RDF><cc:Work rdf:about=""><dc:format>image/svg+xml</dc:format><dc:type rdf:resource="http://purl.org/dc/dcmitype/StillImage"/><dc:title/></cc:Work></rdf:RDF></metadata><defs id="defs4159"/><g transform="matrix(1.25,0,0,-1.25,0,562.5)" id="g4163"><g id="g4165"/><g id="g4167"><path id="path4169" style="fill:#441188;fill-opacity:0;fill-rule:evenodd;stroke:none" d="m 410.8631,145.698 c 43.7972,43.7973 43.7972,114.8067 0,158.604 0,0 -106.5611,106.5611 -106.5611,106.5611 -43.7973,43.7972 -114.8067,43.7972 -158.604,0 0,0 -106.56109,-106.5611 -106.56109,-106.5611 -43.797259,-43.7973 -43.797259,-114.8067 0,-158.604 0,0 106.56109,-106.56109 106.56109,-106.56109 43.7973,-43.797259 114.8067,-43.797259 158.604,0 0,0 106.5611,106.56109 106.5611,106.56109 z"/><path id="path4171" style="fill:#ffffff;fill-opacity:1;fill-rule:nonzero;stroke:none" d="m 331.25,225 c 0,-58.5938 -47.6563,-106.25 -106.25,-106.25 -58.5938,0 -106.25,47.6562 -106.25,106.25 0,58.5938 47.6562,106.25 106.25,106.25 58.5937,0 106.25,-47.6562 106.25,-106.25 z M 375,225 C 375,307.8125 307.8125,375 225,375 142.1875,375 75,307.8125 75,225 75,142.1875 142.1875,75 225,75 c 82.8125,0 150,67.1875 150,150 z"/><path id="path4173" style="fill:#ffffff;fill-opacity:1;fill-rule:evenodd;stroke:none" d="m 178.3058,157.4747 c 0,0 -0.9539,0.0227 -0.9539,0.0227 0,0 -0.9517,0.0683 -0.9517,0.0683 0,0 -0.9473,0.1135 -0.9473,0.1135 0,0 -0.9408,0.1586 -0.9408,0.1586 0,0 -0.9322,0.2033 -0.9322,0.2033 0,0 -0.9215,0.2475 -0.9215,0.2475 0,0 -0.9086,0.2911 -0.9086,0.2911 0,0 -0.8937,0.3342 -0.8937,0.3342 0,0 -0.8767,0.3764 -0.8767,0.3764 0,0 -0.8578,0.4178 -0.8578,0.4178 0,0 -0.8369,0.4582 -0.8369,0.4582 0,0 -0.814,0.4977 -0.814,0.4977 0,0 -0.7895,0.5358 -0.7895,0.5358 0,0 -0.7629,0.573 -0.7629,0.573 0,0 -0.7348,0.6086 -0.7348,0.6086 0,0 -0.7049,0.643 -0.7049,0.643 0,0 -0.6734,0.6759 -0.6734,0.6759 0,0 -0.6404,0.7072 -0.6404,0.7072 0,0 -0.606,0.737 -0.606,0.737 0,0 -0.5701,0.7651 -0.5701,0.7651 0,0 -0.533,0.7913 -0.533,0.7913 0,0 -0.4947,0.8159 -0.4947,0.8159 0,0 -0.4552,0.8385 -0.4552,0.8385 0,0 -0.4146,0.8593 -0.4146,0.8593 0,0 -0.3732,0.8781 -0.3732,0.8781 0,0 -0.331,0.8949 -0.331,0.8949 0,0 -0.2878,0.9097 -0.2878,0.9097 0,0 -0.2442,0.9223 -0.2442,0.9223 0,0 -0.1998,0.933 -0.1998,0.933 0,0 -0.1552,0.9414 -0.1552,0.9414 0,0 -0.1101,0.9477 -0.1101,0.9477 0,0 -0.0647,0.9519 -0.0647,0.9519 0,0 -0.0193,0.954 -0.0193,0.954 0,0 0.0262,0.9537 0.0262,0.9537 0,0 0.0717,0.9514 0.0717,0.9514 0,0 0.117,0.947 0.117,0.947 0,0 0.162,0.9402 0.162,0.9402 0,0 0.2067,0.9315 0.2067,0.9315 0,0 0.2508,0.9205 0.2508,0.9205 0,0 0.2945,0.9076 0.2945,0.9076 0,0 0.3374,0.8924 0.3374,0.8924 0,0 46.8116,115.4124 46.8116,115.4124 0,0 0.3771,0.8701 0.3771,0.8701 0,0 0.418,0.8512 0.418,0.8512 0,0 0.4579,0.8304 0.4579,0.8304 0,0 0.4967,0.8078 0.4967,0.8078 0,0 0.5344,0.7834 0.5344,0.7834 0,0 0.571,0.7571 0.571,0.7571 0,0 0.6062,0.7292 0.6062,0.7292 0,0 0.6401,0.6997 0.6401,0.6997 0,0 0.6725,0.6685 0.6725,0.6685 0,0 0.7035,0.6359 0.7035,0.6359 0,0 0.7328,0.6018 0.7328,0.6018 0,0 0.7606,0.5665 0.7606,0.5665 0,0 0.7865,0.5297 0.7865,0.5297 0,0 0.8107,0.4919 0.8107,0.4919 0,0 0.8332,0.4529 0.8332,0.4529 0,0 0.8537,0.4128 0.8537,0.4128 0,0 0.8723,0.372 0.8723,0.372 0,0 0.8889,0.3302 0.8889,0.3302 0,0 0.9036,0.2877 0.9036,0.2877 0,0 0.9162,0.2445 0.9162,0.2445 0,0 0.9268,0.2008 0.9268,0.2008 0,0 0.9353,0.1567 0.9353,0.1567 0,0 0.9416,0.1121 0.9416,0.1121 0,0 0.9459,0.0674 0.9459,0.0674 0,0 0.948,0.0225 0.948,0.0225 0,0 0.948,-0.0225 0.948,-0.0225 0,0 0.9459,-0.0674 0.9459,-0.0674 0,0 0.9416,-0.1121 0.9416,-0.1121 0,0 0.9353,-0.1567 0.9353,-0.1567 0,0 0.9268,-0.2008 0.9268,-0.2008 0,0 0.9162,-0.2445 0.9162,-0.2445 0,0 0.9036,-0.2877 0.9036,-0.2877 0,0 0.8889,-0.3302 0.8889,-0.3302 0,0 0.8723,-0.372 0.8723,-0.372 0,0 0.8537,-0.4128 0.8537,-0.4128 0,0 0.8332,-0.4529 0.8332,-0.4529 0,0 0.8107,-0.4919 0.8107,-0.4919 0,0 0.7866,-0.5297 0.7866,-0.5297 0,0 0.7605,-0.5665 0.7605,-0.5665 0,0 0.7328,-0.6018 0.7328,-0.6018 0,0 0.7035,-0.6359 0.7035,-0.6359 0,0 0.6725,-0.6685 0.6725,-0.6685 0,0 0.6401,-0.6997 0.6401,-0.6997 0,0 0.6062,-0.7292 0.6062,-0.7292 0,0 0.571,-0.7571 0.571,-0.7571 0,0 0.5344,-0.7834 0.5344,-0.7834 0,0 0.4967,-0.8078 0.4967,-0.8078 0,0 0.4579,-0.8304 0.4579,-0.8304 0,0 0.418,-0.8512 0.418,-0.8512 0,0 0.3771,-0.8701 0.3771,-0.8701 0,0 46.8116,-115.4124 46.8116,-115.4124 0,0 0.3374,-0.8924 0.3374,-0.8924 0,0 0.2945,-0.9076 0.2945,-0.9076 0,0 0.2508,-0.9205 0.2508,-0.9205 0,0 0.2067,-0.9315 0.2067,-0.9315 0,0 0.162,-0.9402 0.162,-0.9402 0,0 0.117,-0.947 0.117,-0.947 0,0 0.0717,-0.9514 0.0717,-0.9514 0,0 0.0262,-0.9537 0.0262,-0.9537 0,0 -0.0192,-0.954 -0.0192,-0.954 0,0 -0.0648,-0.9519 -0.0648,-0.9519 0,0 -0.1101,-0.9477 -0.1101,-0.9477 0,0 -0.1551,-0.9414 -0.1551,-0.9414 0,0 -0.1999,-0.933 -0.1999,-0.933 0,0 -0.2442,-0.9223 -0.2442,-0.9223 0,0 -0.2878,-0.9097 -0.2878,-0.9097 0,0 -0.3309,-0.8949 -0.3309,-0.8949 0,0 -0.3732,-0.8781 -0.3732,-0.8781 0,0 -0.4147,-0.8593 -0.4147,-0.8593 0,0 -0.4552,-0.8385 -0.4552,-0.8385 0,0 -0.4946,-0.8159 -0.4946,-0.8159 0,0 -0.533,-0.7913 -0.533,-0.7913 0,0 -0.5702,-0.7651 -0.5702,-0.7651 0,0 -0.6059,-0.737 -0.6059,-0.737 0,0 -0.6405,-0.7072 -0.6405,-0.7072 0,0 -0.6734,-0.6759 -0.6734,-0.6759 0,0 -0.7049,-0.643 -0.7049,-0.643 0,0 -0.7348,-0.6086 -0.7348,-0.6086 0,0 -0.7629,-0.573 -0.7629,-0.573 0,0 -0.7894,-0.5358 -0.7894,-0.5358 0,0 -0.8141,-0.4977 -0.8141,-0.4977 0,0 -0.8369,-0.4582 -0.8369,-0.4582 0,0 -0.8578,-0.4178 -0.8578,-0.4178 0,0 -0.8767,-0.3764 -0.8767,-0.3764 0,0 -0.8937,-0.3342 -0.8937,-0.3342 0,0 -0.9086,-0.2911 -0.9086,-0.2911 0,0 -0.9214,-0.2475 -0.9214,-0.2475 0,0 -0.9322,-0.2033 -0.9322,-0.2033 0,0 -0.9409,-0.1586 -0.9409,-0.1586 0,0 -0.9473,-0.1135 -0.9473,-0.1135 0,0 -0.9517,-0.0683 -0.9517,-0.0683 0,0 -0.9538,-0.0227 -0.9538,-0.0227 0,0 -93.6231,0 -93.6231,0 z m 0,20 c 0,0 93.6231,0 93.6231,0 0,0 -46.8116,115.4124 -46.8116,115.4124 0,0 -46.8115,-115.4124 -46.8115,-115.4124 z"/><g transform="matrix(0.75,0,0,-0.75,0,450)" id="g4175"><path id="path4177" style="fill:none;stroke:#ffffff;stroke-width:26.66666985;stroke-linecap:round;stroke-linejoin:round;stroke-miterlimit:1.41421402;stroke-dasharray:none;stroke-opacity:1" d="m 307.0915,367.5999 c 0,0 -84.6338,-106.4347 -84.6338,-106.4347"/></g></g></g></svg>""",
                )
            with open(os.path.join(os.path.split(playlist_filename)[0], "images", "count_circles.svg"), "w") as fo:
                fo.write(
                    """<?xml version="1.0" encoding="UTF-8" standalone="no"?>
                    <svg xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:cc="http://creativecommons.org/ns#" xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:svg="http://www.w3.org/2000/svg" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 562.5 562.5" height="562.5" width="562.5" xml:space="preserve" version="1.1" id="svg4155"><metadata id="metadata4161"><rdf:RDF><cc:Work rdf:about=""><dc:format>image/svg+xml</dc:format><dc:type rdf:resource="http://purl.org/dc/dcmitype/StillImage"/><dc:title/></cc:Work></rdf:RDF></metadata><defs id="defs4159"/><g transform="matrix(1.25,0,0,-1.25,0,562.5)" id="g4163"><g id="g4165"/><g id="g4167"><path id="path4169" style="fill:#441188;fill-opacity:0;fill-rule:evenodd;stroke:none" d="m 410.8631,145.698 c 43.7972,43.7973 43.7972,114.8067 0,158.604 0,0 -106.5611,106.5611 -106.5611,106.5611 -43.7973,43.7972 -114.8067,43.7972 -158.604,0 0,0 -106.56109,-106.5611 -106.56109,-106.5611 -43.797259,-43.7973 -43.797259,-114.8067 0,-158.604 0,0 106.56109,-106.56109 106.56109,-106.56109 43.7973,-43.797259 114.8067,-43.797259 158.604,0 0,0 106.5611,106.56109 106.5611,106.56109 z"/><path id="path4171" style="fill:#ffffff;fill-opacity:1;fill-rule:nonzero;stroke:none" d="m 331.25,225 c 0,-58.5938 -47.6563,-106.25 -106.25,-106.25 -58.5938,0 -106.25,47.6562 -106.25,106.25 0,58.5938 47.6562,106.25 106.25,106.25 58.5937,0 106.25,-47.6562 106.25,-106.25 z M 375,225 C 375,307.8125 307.8125,375 225,375 142.1875,375 75,307.8125 75,225 75,142.1875 142.1875,75 225,75 c 82.8125,0 150,67.1875 150,150 z"/><g transform="matrix(0.75,0,0,-0.75,0,450)" id="g4173"><path id="path4175" style="fill:none;stroke:#ffffff;stroke-width:26.66666985;stroke-linecap:round;stroke-linejoin:round;stroke-miterlimit:1.41421402;stroke-dasharray:none;stroke-opacity:1" d="m 300,218.7598 c 44.8677,0 81.2402,36.3725 81.2402,81.2402 0,44.8677 -36.3725,81.2402 -81.2402,81.2402 -44.8677,0 -81.2402,-36.3725 -81.2402,-81.2402 0,-44.8677 36.3725,-81.2402 81.2402,-81.2402 z"/></g></g></g></svg>""",
                )
        self.playlist_name = os.path.splitext(os.path.basename(playlist_filename))[0]
        # self.osz_type = osz_type
        # self.output_zip = output_zip
        # if self.output_zip:
        #     self.osz_type = "full"

    async def beatmap_task(self, beatmap_index: int) -> dict:
        i, element = beatmap_index + 1, self.beatmap_list[beatmap_index]
        bid: int = element["bid"]
        b: Beatmap = element["beatmap"]
        raw_mods: list[dict[str, Any]] = element["mods"]
        notes: str = element["notes"]

        # 处理NM, FM, TB
        root_mod: str = raw_mods[0]["acronym"]
        last_root_mod: str = self.beatmap_list[beatmap_index - 1]["mods"][0]["acronym"] if beatmap_index != 0 else ""
        is_fm = False
        mods = raw_mods.copy()  # 只能使用官方 Mods 的用这个变量
        for j in range(len(raw_mods)):
            # 如果非官方 Mods 缩写在列表中，则处理过后剔除该 Mod
            if raw_mods[j]["acronym"] in self.custom_mods_acronym:
                if raw_mods[j]["acronym"] == "FM" or raw_mods[j]["acronym"] == "F+":
                    is_fm = True
                mods.pop(j)
        mods_ready: list[str] = to_readable_mods(raw_mods)  # 准备给用户看的 Mods 表现形式

        # 下载谱面与计算难度
        download_osu(b)
        my_attr = SimpleOsuDifficultyAttribute(b.cs, b.accuracy, b.ar, b.bpm, b.hit_length)
        my_attr.set_mods(mods)
        osupp_attr = calculate_difficulty(beatmap_path=os.path.join(C.BEATMAPS_CACHE_DIRECTORY.value, "%s.osu" % b.id), mods=my_attr.osu_tool_mods, mod_options=my_attr.osu_tool_mod_options)
        stars1 = osupp_attr["star_rating"]
        stars2 = None
        if is_fm:
            osupp_attr_fm = calculate_difficulty(beatmap_path=os.path.join(C.BEATMAPS_CACHE_DIRECTORY.value, "%s.osu" % b.id), mods=my_attr.osu_tool_mods + ["HR"], mod_options=my_attr.osu_tool_mod_options)
            stars2 = osupp_attr_fm["star_rating"]
        cs = "%s" % round(my_attr.cs, 2)
        ar = "0" if my_attr.ar is None else "%s" % round(my_attr.ar, 2)
        od = "0" if my_attr.accuracy is None else "%s" % round(my_attr.accuracy, 2)
        cs_pct = calc_positive_percent(my_attr.cs, 0, 10)
        ar_pct = calc_positive_percent(my_attr.ar, 0, 10)
        od_pct = calc_positive_percent(my_attr.accuracy, 0, 10)
        bpm = "%s" % round(my_attr.bpm, 2)
        song_len_in_sec = my_attr.hit_length
        song_len_m, song_len_s = divmod(song_len_in_sec, 60)
        hit_length = "%d:%02d" % (song_len_m, song_len_s)
        max_combo = "%d" % osupp_attr["max_combo"]

        # 绘制cover
        cover = BeatmapCover(b, self.mod_color.get(root_mod, "#eb50eb"), stars1, cs, ar, od, bpm, hit_length, max_combo, stars2)
        if self.css_style:
            # 将背景图片保存在统一文件夹内以减小占用
            if not os.path.exists(os.path.join(self.bg_dir, "%d.jpg" % bid)):
                bg_d = Downloader(self.bg_dir)
                bg_filename = await bg_d.async_start(f"https://assets.ppy.sh/beatmaps/%d/covers/fullsize.jpg" % b.beatmapset_id, "%d" % bid, headers)
                # bg_filename = await bg_d.async_start(f"https://beatconnect.io/bg/%d/%d" % (b.beatmapset_id, bid), "%d" % bid, headers)
                try:
                    im: Image.Image = Image.open(bg_filename)
                except UnidentifiedImageError:
                    im = Image.open(os.path.join(assets_dir, "bg1.jpg"))
                    im = im.filter(ImageFilter.BLUR)
                if im.mode != "RGB":
                    im = im.convert("RGB")
                be = ImageEnhance.Brightness(im)
                im = be.enhance(0.67)
                with Lock():
                    im.save(bg_filename)
            bg_filename = os.path.join(self.bg_dir, "%d.jpg" % bid)
            extra_notes = ""
            for column in self.custom_columns:
                if column == "mods":
                    continue
                else:
                    extra_notes += "<br />%s: %s" % (column, element[column])  # type: ignore[literal-required]

            if root_mod != last_root_mod and last_root_mod != "":
                beatmap_info = """    </div>
    <div class="p-4"><br /></div>
    <div class="grid grid-cols-1 md:grid-cols-2 2xl:grid-cols-3 gap-4 md:gap-6 xl:gap-8">
"""
            else:
                beatmap_info = ""
            beatmap_info += f'''      <div class="group relative">
        <div
          class="relative h-32 rounded-lg overflow-hidden shadow-lg transition-all duration-300 transform group-hover:rounded-b-none group-hover:h-64 group-hover:-translate-y-3">
          <img src="{"./" + (os.path.relpath(bg_filename, os.path.split(self.playlist_filename)[0])).replace("\\", "/")}" alt="{html.escape(b.beatmapset().artist)} - {html.escape(b.beatmapset().title)} ({html.escape(b.beatmapset().creator)}) [{html.escape(b.version)}]"
            class="w-full h-full object-cover brightness-90 dark:brightness-50 blur-0 contrast-100 scale-100 group-hover:brightness-50 dark:group-hover:brightness-50 group-hover:blur-sm group-hover:contrast-125 group-hover:scale-105 transition-all duration-300" />
          <div class="absolute top-0 left-0 right-0 p-4 flex flex-col">
            <div class="flex justify-between items-start">
              <div class="px-3 py-1 rounded-full text-white font-semibold shadow" style="background-color: {calc_star_rating_color(stars1)};">
                <div style="color: {cover.stars_text_color}; opacity: {'1' if cover.is_high_stars else '0.8'}; text-shadow: 0px 0.5px 1.5px rgba(185, 185, 185, 0.5);"><i class="fas fa-star" {'' if cover.is_high_stars else 'style="color: #0f172a;"'}></i>{cover.stars.replace("󰓎", "")}</div>
              </div>
              <div class="flex gap-2 has-tooltip">
                {"".join([f'<span class="card-main px-2 py-1 rounded text-white text-sm font-semibold shadow" style="background-color: {self.mod_color.get(mod["acronym"], "#eb50eb")}">{mod["acronym"]}{"<sup>*</sup>" if mod.get("settings") and mod["acronym"] not in self.custom_mods_acronym else ""}</span>' for mod in raw_mods])}
                <div class="tooltip flex">
                  <div class="flex-initial rounded text-xs shadow-xl mt-6 px-2 py-1 mx-4 w-auto h-auto break-all notes" style="opacity: 88%; white-space: pre-line;">{";\n".join(mods_ready)}</div>
                </div>
              </div>
            </div>
            <div class="text-white card-main pt-2">
              <h3 class="text-xl font-bold mb-1 truncate">{html.escape(b.beatmapset().title_unicode)}</h3>
              <p class="font-semibold overflow-ellipsis overflow-hidden whitespace-nowrap">{html.escape(b.beatmapset().artist_unicode)}</p>
              <div class="opacity-0 group-hover:opacity-100 transition-opacity duration-300 pt-2 pb-1">
                <p class="text-xs leading-[1.5] overflow-ellipsis overflow-hidden whitespace-nowrap opacity-[88%]">Mapper: <a class="font-semibold">{html.escape(b.beatmapset().creator)}</a></p>
                <p class="text-xs leading-[1.5] overflow-ellipsis overflow-hidden whitespace-nowrap opacity-[88%]">Difficulty: <span class="font-semibold">{html.escape(b.version)}</span></p>
                <p class="text-xs leading-[1.5] overflow-ellipsis overflow-hidden whitespace-nowrap opacity-[88%]">Beatmap ID: <span class="font-semibold">{b.id}</span></p>
                <div class="text-xs w-full grid grid-cols-3 mt-2 gap-6">
                  <div>
                    <div class="flex items-center justify-between">
                      <div class="text-left flex-initial w-6 opacity-[88%]"><span>CS</span></div>
                      <div class="flex-1 w-full mr-2">
                        <div class="w-full h-2 bg-gray-600 rounded">
                          <div class="h-full rounded" style="background-color: white; width: {cs_pct}%"></div>
                        </div>
                      </div>
                      <div class="flex-initial w-4 text-right font-semibold opacity-[88%]">{cover.cs}</div>
                    </div>
                  </div>
                  <div>
                    <div class="flex items-center justify-between">
                      <div class="text-left flex-initial w-6 opacity-[88%]"><span>AR</span></div>
                      <div class="flex-1 w-full mr-2">
                        <div class="w-full h-2 bg-gray-600 rounded">
                          <div class="h-full rounded" style="background-color: white; width: {ar_pct}%"></div>
                        </div>
                      </div>
                      <div class="flex-initial w-4 text-right font-semibold opacity-[88%]">{cover.ar}</div>
                    </div>
                  </div>
                  <div>
                    <div class="flex items-center justify-between">
                      <div class="text-left flex-initial w-6 opacity-[88%]"><span>OD</span></div>
                      <div class="flex-1 w-full mr-2">
                        <div class="w-full h-2 bg-gray-600 rounded">
                          <div class="h-full rounded" style="background-color: white; width: {od_pct}%"></div>
                        </div>
                      </div>
                      <div class="flex-initial w-4 text-right font-semibold opacity-[88%]">{cover.od}</div>
                    </div>
                  </div>
                </div>
                <div class="text-xs/6 w-full grid grid-cols-3 gap-6">
                  <div>
                    <div class="flex items-center justify-between opacity-[88%]"><img src="./images/bpm.svg" class="w-4"/>
                      <div class="flex-1 font-semibold ml-2">{cover.bpm}</div>
                    </div>
                  </div>
                  <div>
                    <div class="flex items-center justify-between opacity-[88%]"><img src="./images/total_length.svg" class="w-4"/>
                      <div class="flex-1 font-semibold ml-2">{cover.hit_length}</div>
                    </div>
                  </div>
                  <div>
                    <div class="flex items-center justify-between opacity-[88%]"><img src="./images/count_circles.svg" class="w-4"/>
                      <div class="flex-1 font-semibold ml-2">{cover.max_combo}</div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
        <div class="absolute py-2 z-10 w-full rounded-b-xl p-4 shadow-xl opacity-0 group-hover:opacity-100 transition-opacity duration-300 top-full -mt-10 notes">
          <p class="text-sm flex justify-between items-end">
            <span class="w-fit pr-2">{notes}{extra_notes}</span>
            <span class="w-12 justify-end items-end space-x-2">
              <a href="https://osu.ppy.sh/b/{b.id}"
              class="text-custom-900 dark:text-custom hover:text-custom-600"><i class="fas fa-external-link-alt"></i></a>
              <a href="osu://b/{b.id}"
              class="text-custom-900 dark:text-custom hover:text-custom-600"><i class="fas fa-download"></i></a>
            </span>
          </p>
        </div>
      </div>
'''
        else:
            cover_filename = await cover.download(Downloader(self.covers_dir), "%d-%d.jpg" % (i, bid))
            img_src = "./" + (os.path.relpath(cover_filename, os.path.split(self.playlist_filename)[0])).replace("\\", "/")
            img_link = "https://osu.ppy.sh/b/%d" % b.id
            beatmap_info = '<a href="%s"><img src="%s" alt="%s - %s (%s) [%s]" height="90" style="object-fit: cover;"/></a>' % (
                img_link,
                img_src,
                html.escape(b.beatmapset().artist),
                html.escape(b.beatmapset().title),
                html.escape(b.beatmapset().creator),
                html.escape(b.version),
            )
            await cover.draw(cover_filename)

        # 保存数据
        completed_beatmap = {
            "#": i,
            "BID": b.id,
            "SID": b.beatmapset_id,
            "Beatmap Info (Click to View)": beatmap_info,
            "Artist - Title (Creator) [Version]": "%s - %s (%s) [%s]" % (b.beatmapset().artist, b.beatmapset().title, b.beatmapset().creator, b.version),
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
            "_Artist": b.beatmapset().artist_unicode,
            "_Title": b.beatmapset().title_unicode,
        }
        for column in self.custom_columns:
            if column == "mods":
                continue
            else:
                completed_beatmap[column] = element[column]  # type: ignore[literal-required]
        return completed_beatmap

    async def playlist_task(self) -> list[dict]:
        tasks = []
        async with asyncio.TaskGroup() as tg:
            for i in range(len(self.beatmap_list)):
                tasks.append(tg.create_task(self.beatmap_task(i)))
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
        with threading.Lock():
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
                    html_body_prefix = """
  <header class="mb-2">
    %s
    <h1 class="relative text-2xl font-bold text-center pt-8">
      %s
    </h1>
  </header>
  <div class="min-h-screen p-4 sm:px-8 lg:px-12 xl:px-20 2xl:px-32">
    <div class="grid grid-cols-1 md:grid-cols-2 2xl:grid-cols-3 gap-4 md:gap-6 xl:gap-8">
""" % (
                        self.banner,
                        self.playlist_name,
                    )
                    html_body_suffix = """    </div>
  </div>
"""
                    fo.write(html_string.format(html_head=html_head, html_body="".join([cb["Beatmap Info (Click to View)"] for cb in playlist]), html_body_prefix=html_body_prefix, html_body_suffix=html_body_suffix))
                else:
                    fo.write(html_string.format(html_head="", html_body=df.to_html(index=False, escape=False, classes="pd"), html_body_prefix="", html_body_suffix=""))

            # 清理临时文件夹
            rmtree(self.tmp_dir)

        return df_standalone
