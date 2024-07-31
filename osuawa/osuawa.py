import os
import os.path
import re
import zipfile
from enum import Enum, unique
from shutil import rmtree
from time import sleep
from typing import Any, Optional

import orjson
import pandas as pd
import rosu_pp_py as rosu
import streamlit as st
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, UnidentifiedImageError
from clayutil.futil import Downloader, Properties, compress_as_zip, filelock
from clayutil.validator import OneOf
from fontfallback import writing
from osu import Client, GameModeStr

from .utils import Beatmap, OsuDifficultyAttribute, calc_beatmap_attributes, calc_star_rating_color, get_beatmap_dict, get_username, score_info_list, user_to_dict


@unique
class Path(Enum):
    OUTPUT_DIRECTORY: str = "./output"
    RAW_RECENT_SCORES: str = "raw_recent_scores"
    RECENT_SCORES: str = "recent_scores"


class Osuawa(object):
    tz = "Asia/Shanghai"

    def __init__(self, oauth_filename: str, osu_tools_path: str, output_dir: str):
        p = Properties(oauth_filename)
        p.load()
        self.client = Client.from_client_credentials(p["client_id"], p["client_secret"], p["redirect_url"])
        if not os.path.exists(output_dir):
            os.mkdir(output_dir)
            os.mkdir(os.path.join(output_dir, Path.RAW_RECENT_SCORES.value))
            os.mkdir(os.path.join(output_dir, Path.RECENT_SCORES.value))
        self.osu_tools_path = osu_tools_path
        self.output_dir = output_dir

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
                "b_pp_85if",
            ],
        )
        df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.tz_convert(self.tz)
        df["score_nf"] = df.apply(lambda row: row["score"] * 2 if row["is_nf"] else row["score"], axis=1)
        return df

    def get_user_info(self, username: str) -> dict[str, Any]:
        return user_to_dict(self.client.get_user(username, key="username"))

    def get_score(self, score_id: int) -> pd.DataFrame:
        score = self.client.get_score_by_id_only(score_id)
        score_compact = score_info_list(score)
        score_compact.extend(
            calc_beatmap_attributes(
                self.osu_tools_path,
                self.client.get_beatmap(score.beatmap_id),
                score_compact[6],
            )
        )
        return self.create_scores_dataframe({str(score.id): score_compact})

    def get_user_beatmap_scores(self, beatmap: int, user: int) -> pd.DataFrame:
        user_scores = self.client.get_user_beatmap_scores(beatmap, user)
        scores_compact = {str(x.id): score_info_list(x) for x in user_scores}
        for score_id in scores_compact:
            scores_compact[score_id].extend(
                calc_beatmap_attributes(
                    self.osu_tools_path,
                    self.client.get_beatmap(beatmap),
                    scores_compact[score_id][6],
                )
            )
        return self.create_scores_dataframe(scores_compact)

    @filelock
    def save_recent_scores(self, user: int, include_fails: bool = True) -> str:
        with st.status("saving recent scores of %d" % user, expanded=True) as status:
            st.text("getting scores...")
            # get
            user_scores = []
            offset = 0
            while True:
                user_scores_current = self.client.get_user_scores(
                    user=user,
                    type="recent",
                    mode=GameModeStr.STANDARD,
                    include_fails=include_fails,
                    limit=50,
                    offset=offset,
                )
                if len(user_scores_current) == 0:
                    break
                user_scores.extend(user_scores_current)
                offset += 50

            recent_scores_compact = {str(x.id): score_info_list(x) for x in user_scores}
            len_got = len(recent_scores_compact)

            st.text("merging scores...")
            # concatenate
            len_local = 0
            if os.path.exists(os.path.join(self.output_dir, Path.RAW_RECENT_SCORES.value, f"{user}.json")):
                with open(os.path.join(self.output_dir, Path.RAW_RECENT_SCORES.value, f"{user}.json")) as fi:
                    recent_scores_compact_old = orjson.loads(fi.read())
                len_local = len(recent_scores_compact_old)
                recent_scores_compact = {
                    **recent_scores_compact,
                    **recent_scores_compact_old,
                }
            len_diff = len(recent_scores_compact) - len_local

            writer = st.text("calculating difficulty attributes...")
            # calculate difficulty attributes
            bids_not_calculated = {x[0] for x in recent_scores_compact.values() if len(x) == 9}
            beatmaps_dict = get_beatmap_dict(self.client, tuple(bids_not_calculated))
            current = 0
            for score_id in recent_scores_compact:
                if len(recent_scores_compact[score_id]) == 9:
                    current += 1
                    writer.text(f"calculating difficulty attributes... {current}/{len(recent_scores_compact)} ({len(bids_not_calculated)} unique)")
                    recent_scores_compact[score_id].extend(
                        calc_beatmap_attributes(
                            self.osu_tools_path,
                            beatmaps_dict[recent_scores_compact[score_id][0]],
                            recent_scores_compact[score_id][7],
                        )
                    )

            # save
            with open(
                os.path.join(self.output_dir, Path.RAW_RECENT_SCORES.value, f"{user}.json"),
                "w",
            ) as fo:
                fo.write(orjson.dumps(recent_scores_compact).decode("utf-8"))
            df = self.create_scores_dataframe(recent_scores_compact)
            df.to_csv(os.path.join(self.output_dir, Path.RECENT_SCORES.value, f"{user}.csv"))
            status.update(label="recent scores of %d saved" % user, state="complete", expanded=False)
        return "%s: len local/got/diff = %d/%d/%d" % (
            get_username(self.client, user),
            len_local,
            len_got,
            len_diff,
        )

    @staticmethod
    def create_client_credential_grant_client(client_id: int, client_secret: str) -> Client:
        return Client.from_client_credentials(client_id=client_id, client_secret=client_secret, redirect_url=None)


class BeatmapCover(object):
    font_sans = "./osuawa/DejaVuSansSC.ttf"
    font_sans_fallback = "./osuawa/AlibabaPuHuiTi-3-55-Regular.ttf"
    font_mono_regular = "./osuawa/MapleMono-NF-CN-Regular-V7.0-Beta22.ttf"
    font_mono_italic = "./osuawa/MapleMono-NF-CN-Italic-V7.0-Beta22.ttf"
    font_mono_semibold = "./osuawa/MapleMono-NF-CN-SemiBold-V7.0-Beta22.ttf"

    def __init__(self, beatmap: Beatmap, block_color, stars1: float, cs: str, ar: str, od: str, bpm: str, hit_length: str, max_combo: str, stars2: Optional[float] = None):
        self.beatmap = beatmap
        self.block_color = block_color
        self.stars1 = stars1
        self.stars2 = stars2
        self.cs = cs
        self.ar = ar
        self.od = od
        self.bpm = bpm
        self.hit_length = hit_length
        self.max_combo = max_combo

    def draw(self, d: Downloader, filename: str) -> str:
        b = self.beatmap

        # 下载cover原图，若无cover则使用默认图片
        cover_filename = d.start(b.beatmapset.covers.slimcover, filename)
        try:
            im = Image.open(cover_filename)
        except UnidentifiedImageError:
            try:
                im = Image.open(d.start(b.beatmapset.covers.cover, filename))
            except UnidentifiedImageError:
                im = Image.open("./osuawa/bg1.jpg")
                im = im.filter(ImageFilter.BLUR)
            im = im.resize((1920, int(im.height * 1920 / im.width)), Image.Resampling.LANCZOS)  # 缩放到宽为1920
            im = im.crop((im.width // 2 - 960, 0, im.width // 2 + 960, 360))  # 从中间裁剪到1920x360

        # 调整亮度
        be = ImageEnhance.Brightness(im)
        im = be.enhance(0.33)
        draw = ImageDraw.Draw(im)

        # 测试长度
        len_set = 1400
        title_u = b.beatmapset.title_unicode
        title_len_dry_run = draw.textlength(title_u, font=ImageFont.truetype(font=self.font_sans, size=72))
        if title_len_dry_run > len_set - 16:
            cut_length = -1
            while True:
                t1_cut = "%s..." % title_u[:cut_length]
                title_len_dry_run = draw.textlength(t1_cut, font=ImageFont.truetype(font=self.font_sans, size=72))
                if title_len_dry_run <= len_set - 16:
                    break
                cut_length -= 1
            title_u = t1_cut

        # 绘制左侧文字
        fonts = writing.load_fonts(self.font_sans, self.font_sans_fallback)
        draw.text((42, 19), b.version, font=ImageFont.truetype(font=self.font_mono_semibold, size=48), fill="#1f1f1f")
        draw.text((40, 16), b.version, font=ImageFont.truetype(font=self.font_mono_semibold, size=48), fill="white")
        # draw.text((41, 16), b.version, font=ImageFont.truetype(font=self.font_mono_regular, size=48), fill="white")
        draw.text((40, 17), b.version, font=ImageFont.truetype(font=self.font_mono_semibold, size=48), fill="white")
        # draw.text((41, 17), b.version, font=ImageFont.truetype(font=self.font_mono_regular, size=48), fill="white")
        writing.draw_text_v2(draw, (42, 132), title_u, "#1f1f1f", fonts, 72)
        writing.draw_text_v2(draw, (42, 131), title_u, "#1f1f1f", fonts, 72)
        writing.draw_text_v2(draw, (42, 133), title_u, (40, 40, 40), fonts, 72)
        writing.draw_text_v2(draw, (41, 133), title_u, (40, 40, 40), fonts, 72)
        writing.draw_text_v2(draw, (41, 132), title_u, (40, 40, 40), fonts, 72)
        writing.draw_text_v2(draw, (40, 129), title_u, "white", fonts, 72)
        writing.draw_text_v2(draw, (41, 129), title_u, "white", fonts, 72)
        writing.draw_text_v2(draw, (40, 130), title_u, "white", fonts, 72)
        writing.draw_text_v2(draw, (41, 130), title_u, "white", fonts, 72)
        writing.draw_text_v2(draw, (41, 218), b.beatmapset.artist_unicode, "#1f1f1f", fonts, 44)
        writing.draw_text_v2(draw, (40, 218), b.beatmapset.artist_unicode, (40, 40, 40), fonts, 44)
        writing.draw_text_v2(draw, (40, 216), b.beatmapset.artist_unicode, "white", fonts, 44)
        draw.text((41, 292), "mapped by", font=ImageFont.truetype(font=self.font_mono_italic, size=36), fill="#1f1f1f")
        draw.text((40, 292), "mapped by", font=ImageFont.truetype(font=self.font_mono_italic, size=36), fill=(40, 40, 40))
        draw.text((40, 290), "mapped by", font=ImageFont.truetype(font=self.font_mono_italic, size=36), fill="white")
        draw.text((266, 292), b.beatmapset.creator, font=ImageFont.truetype(font=self.font_mono_regular, size=36), fill="#1f1f2a")
        draw.text((265, 290), b.beatmapset.creator, font=ImageFont.truetype(font=self.font_mono_regular, size=36), fill=(180, 235, 250))
        draw.text((264, 290), b.beatmapset.creator, font=ImageFont.truetype(font=self.font_mono_regular, size=36), fill=(180, 235, 250))

        # 在右上角绘制星数
        draw.rounded_rectangle([len_set + 22, 18, 1920, 80], 12, fill="#1f1f1f")
        draw.rounded_rectangle([len_set + 20, 16, 1920, 78], 12, fill=calc_star_rating_color(self.stars1))
        stars = "%.2f" % self.stars1
        if self.stars2 is not None:
            stars = "%s (%.2f)" % (stars, self.stars2)
        # draw.text((len_set + 50, 18), stars, font=ImageFont.truetype(font=self.font_mono_regular, size=48), fill="#1f1f1f")
        if self.stars1 > 6.5:  # white text
            draw.text((len_set + 48, 17), stars, font=ImageFont.truetype(font=self.font_mono_semibold, size=48), fill="#f0dd55")
            # draw.text((len_set + 49, 17), stars, font=ImageFont.truetype(font=self.font_mono_regular, size=48), fill="#f0dd55")
        else:  # black text
            draw.text((len_set + 48, 17), stars, font=ImageFont.truetype(font=self.font_mono_semibold, size=48), fill="#000000")
            # draw.text((len_set + 49, 17), stars, font=ImageFont.truetype(font=self.font_mono_regular, size=48), fill="#000000")

        # 在右侧从下到上依次绘制CS AR OD 󰟚 󱑓 󰺕
        draw_list1: list[tuple[str, str]] = [("OD", self.od), ("AR", self.ar), ("CS", self.cs)]
        draw_list2: list[tuple[str, str]] = [("󰺕", self.max_combo), ("󱑓", self.hit_length), ("󰟚", self.bpm)]
        for i in range(len(draw_list1)):
            draw.text((len_set + 32, 291 - 74 * i), "%s %s" % draw_list1[i], font=ImageFont.truetype(font=self.font_mono_semibold, size=36), fill="#1f1f1f")
            draw.text((len_set + 31, 290 - 74 * i), "%s %s" % draw_list1[i], font=ImageFont.truetype(font=self.font_mono_semibold, size=36), fill="#f0dd55")
        for i in range(len(draw_list2)):
            draw.text((len_set + 258, 291 - 74 * i), "%s %s" % draw_list2[i], font=ImageFont.truetype(font=self.font_mono_semibold, size=36), fill="#1f1f1f")
            draw.text((len_set + 257, 290 - 74 * i), "%s %s" % draw_list2[i], font=ImageFont.truetype(font=self.font_mono_semibold, size=36), fill="#f0dd55")

        # 绘制mod主题色
        draw.rectangle((len_set + 470, 0, 1920, 1080), fill=(40, 40, 40))
        draw.rectangle((len_set + 476, 0, 1920, 1080), fill=self.block_color)

        im.save(cover_filename)
        return cover_filename


class OsuPlaylist(object):
    headers = {
        "Referer": "https://bobbycyl.github.io/playlists/",
        "User-Agent": "osuawa",
    }
    mod_color = {"NM": "#1050eb", "HD": "#ebb910", "HR": "#eb4040", "EZ": "#40b940", "DT": "#b910eb", "FM": "#40507f", "TB": "#7f4050"}
    osz_type = OneOf("full", "novideo", "mini")

    def __init__(self, client: Client, playlist_filename: str, suffix: str = "", osz_type: str = "mini", output_zip: bool = False):
        self.client = client
        p = Properties(playlist_filename)
        p.load()
        self.playlist_filename = playlist_filename
        self.suffix = suffix
        self.footer = p.pop("footer") if "footer" in p else ""
        parsed_beatmap_list = []

        # pop p from end until empty
        current_parsed_beatmap: dict[str, str | int | Beatmap | None] = {"notes": ""}
        while p:
            k, v = p.popitem()
            if k[0] == "#":  # notes
                current_parsed_beatmap["notes"] += v.lstrip("#").lstrip(" ")
            else:
                current_parsed_beatmap["bid"] = int(k)
                current_parsed_beatmap["mods"] = str(v)
                parsed_beatmap_list.insert(0, current_parsed_beatmap)
                current_parsed_beatmap = {"notes": ""}

        beatmap_dict = get_beatmap_dict(self.client, [int(x["bid"]) for x in parsed_beatmap_list])
        for element in parsed_beatmap_list:
            element["notes"] = element["notes"].rstrip("\n")
            element["beatmap"] = beatmap_dict[element["bid"]]
        self.beatmap_list = parsed_beatmap_list
        self.covers_dir = os.path.splitext(playlist_filename)[0] + ".covers"
        self.tmp_dir = os.path.splitext(playlist_filename)[0] + ".tmp"
        self.d = Downloader(self.covers_dir)
        self.tmp_d = Downloader(self.tmp_dir)
        self.playlist_name = os.path.splitext(os.path.basename(playlist_filename))[0]
        self.osz_type = osz_type
        self.output_zip = output_zip
        if self.output_zip:
            self.osz_type = "full"

    def generate(self) -> pd.DataFrame:
        playlist: list[dict] = []
        with st.status("generating %s" % self.playlist_name, expanded=True) as status:
            for i, element in enumerate(self.beatmap_list, start=1):
                bid: int = element["bid"]
                b_writer = st.text("%16d" % bid)
                b: Beatmap = element["beatmap"]
                raw_mods: list[dict[str, Any]] = orjson.loads(element["mods"])
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

                # 下载谱面
                b_writer.text("%16d: downloading the beatmapset..." % bid)
                beatmapset_filename = self.tmp_d.start("https://dl.sayobot.cn/beatmaps/download/%s/%s" % (self.osz_type, b.beatmapset_id))
                beatmapset_dir = os.path.join(self.tmp_dir, str(b.beatmapset_id))
                with zipfile.ZipFile(beatmapset_filename, "r") as zipf:
                    zipf.extractall(beatmapset_dir)
                if os.path.exists(os.path.join(beatmapset_dir, "%s/" % self.osz_type)):
                    beatmapset_dir = os.path.join(beatmapset_dir, "%s/" % self.osz_type)
                if os.path.exists(os.path.join(beatmapset_dir, str(b.beatmapset_id))):
                    beatmapset_dir = os.path.join(beatmapset_dir, str(b.beatmapset_id))
                found_beatmap_filename = ""
                if "%s - %s (%s) [%s].osu" % (b.beatmapset.artist, b.beatmapset.title, b.beatmapset.creator, b.version) in os.listdir(beatmapset_dir):
                    found_beatmap_filename = "%s - %s (%s) [%s].osu" % (b.beatmapset.artist, b.beatmapset.title, b.beatmapset.creator, b.version)
                for beatmap_filename in os.listdir(beatmapset_dir):
                    try:
                        with open(os.path.join(beatmapset_dir, beatmap_filename)) as osuf:
                            for line in osuf:
                                if line[:9] == "BeatmapID":
                                    if line.lstrip("BeatmapID:").rstrip("\n") == str(bid):
                                        found_beatmap_filename = beatmap_filename
                                        break
                    except UnicodeDecodeError:
                        continue
                    except IsADirectoryError:
                        continue
                if found_beatmap_filename == "":
                    raise ValueError("beatmap %s not found" % bid)

                b_writer.text("%16d: calculating difficulty..." % bid)
                my_attr = OsuDifficultyAttribute(b.cs, b.accuracy, b.ar, b.bpm, b.hit_length)
                if mods:
                    my_attr.set_mods(mods)
                rosu_map = rosu.Beatmap(path=os.path.join(beatmapset_dir, found_beatmap_filename))
                rosu_diff = rosu.Difficulty(mods=mods)
                rosu_attr = rosu_diff.calculate(rosu_map)
                stars1 = rosu_attr.stars
                stars2 = None
                if is_fm:
                    rosu_diff_fm = rosu.Difficulty(mods=[{"acronym": "HR"}])
                    rosu_attr_fm = rosu_diff_fm.calculate(rosu_map)
                    stars2 = rosu_attr_fm.stars
                cs = "%.2f" % my_attr.cs
                ar = "%.2f" % rosu_attr.ar
                od = "%.2f" % rosu_attr.od
                bpm = "%.2f" % my_attr.bpm
                song_len_in_sec = my_attr.hit_length
                song_len_m, song_len_s = divmod(song_len_in_sec, 60)
                hit_length = "%2d:%02d" % (song_len_m, song_len_s)
                max_combo = "%d" % rosu_attr.max_combo

                # 绘制cover
                b_writer.text("%16d: drawing the cover..." % bid)
                cover = BeatmapCover(b, self.mod_color.get(color_mod, "#eb50eb"), stars1, cs, ar, od, bpm, hit_length, max_combo, stars2)
                cover_filename = cover.draw(self.d, "%d-%d.jpg" % (i, bid))

                # 保存数据
                img_src = "./" + (os.path.relpath(cover_filename, os.path.split(self.playlist_filename)[0])).replace("\\", "/")
                img_link = "https://osu.ppy.sh/b/%d" % b.id
                playlist.append(
                    {
                        "#": i,
                        "BID": b.id,
                        # "SID": b.beatmapset_id,
                        "Beatmap Info": '<a href="%s"><img src="%s" alt="%s - %s (%s) [%s]" height="135"/></a>' % (img_link, img_src, b.beatmapset.artist, b.beatmapset.title, b.beatmapset.creator, b.version),
                        "Mods": orjson.dumps(mods).decode("utf-8"),
                        "Notes": notes,
                    }
                )
                b_writer.text("%16d: finished" % bid)

                sleep(0.5)
                rmtree(beatmapset_dir)
            df = pd.DataFrame(playlist)
            df.sort_values(by=["#"], inplace=True)
            pd.set_option("colheader_justify", "center")
            html_string = '<html><head><meta charset="utf-8"><title>%s%s</title></head><link rel="stylesheet" type="text/css" href="style.css"/><body bgcolor="#1f1f1f">{table}<footer>%s</footer></body></html>' % (
                self.playlist_name,
                self.suffix,
                self.footer,
            )
            with open(self.playlist_filename.replace(".properties", ".html"), "w", encoding="utf-8") as fi:
                fi.write(html_string.format(table=df.to_html(index=False, escape=False, classes="pd")))

            if self.output_zip:
                # 生成课题压缩包
                if not os.path.exists(Path.OUTPUT_DIRECTORY.value):
                    os.mkdir(Path.OUTPUT_DIRECTORY.value)
                df.to_csv(os.path.join(self.tmp_dir, "table.csv"), index=False)
                compress_as_zip(self.tmp_dir, "./output/%s.zip" % self.playlist_name)

            # 清理临时文件夹
            rmtree(self.tmp_dir)

            status.update(label="generated %s" % self.playlist_name, state="complete", expanded=False)
        return df

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
