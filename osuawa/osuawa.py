import os.path
from collections.abc import Sequence
from enum import Enum, unique

import orjson
import pandas as pd
from clayutil.futil import Properties
from osu import Client, GameModeStr

from .utils import Beatmap, calc_beatmap_attributes, score_info_list


@unique
class Path(Enum):
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

    def get_beatmaps(self, bids: Sequence[int]) -> dict[int, Beatmap]:
        beatmaps_dict = {}
        for i in range(0, len(bids), 50):
            bs_current = self.client.get_beatmaps(bids[i: i + 50])
            for b_current in bs_current:
                beatmaps_dict[b_current.id] = b_current
        return beatmaps_dict

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
                "is_hr",
                "is_ez",
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
                "b_pp_99if",
                "b_pp_95if",
                "b_pp_90if",
            ],
        )
        df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.tz_convert(self.tz)
        df["score_nf"] = df.apply(lambda row: row["score"] * 2 if row["is_nf"] else row["score"], axis=1)
        return df

    def get_username(self, user: int) -> str:
        return self.client.get_user(user).username

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

    def save_recent_scores(self, user: int, include_fails: bool = True) -> str:
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

        # calculate difficulty attributes
        bids_not_calculated = {x[0] for x in recent_scores_compact.values() if len(x) == 9}
        beatmaps_dict = self.get_beatmaps(tuple(bids_not_calculated))
        for score_id in recent_scores_compact:
            if len(recent_scores_compact[score_id]) == 9:
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
        return "%s: len local/got/diff = %d/%d/%d" % (
            self.get_username(user),
            len_local,
            len_got,
            len_diff,
        )
