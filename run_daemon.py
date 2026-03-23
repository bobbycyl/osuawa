"""
将后台程序单列，防止 streamlit 本身抽风影响
本程序使用默认的 streamlit 配置文件路径，如有必要，请自行修改
"""

import asyncio
import logging
import os
import os.path
from dataclasses import asdict
from datetime import datetime
from time import time
from typing import Optional, cast

import orjson
import redis
import schedule
import toml
from clayutil.cmdparse import CollectionField as Coll, Command, CommandParser, IntegerField as Int, JSONStringField as JsonStr
from ossapi import Domain, Scope, Score
from sqlalchemy import create_engine, text

from osuawa import OsuPlaylist, Osuawa
from osuawa.utils import BeatmapSpec, BeatmapToUpdate, C, CompletedPlaylistBeatmap, CompletedSimpleScoreInfo, DatabasePlaylistBeatmap, SimpleScoreInfo, _create_tmp_playlist_p, async_get_username, push_task

ST_CONFIG_FILENAME = "./.streamlit/config.toml"
ST_SECRETS_FILENAME = "./.streamlit/secrets.toml"

st_config = toml.load(ST_CONFIG_FILENAME)
st_secrets = toml.load(ST_SECRETS_FILENAME)

loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

_url = st_secrets["connections"]["osuawa"].get("url")
if _url is None:
    _dialect = st_secrets["connections"]["osuawa"]["dialect"]
    _host = st_secrets["connections"]["osuawa"]["host"]
    _port = st_secrets["connections"]["osuawa"]["port"]
    _username = st_secrets["connections"]["osuawa"]["username"]
    _password = st_secrets["connections"]["osuawa"]["password"]
    _database = st_secrets["connections"]["osuawa"]["database"]
    _url = "%s://%s:%s@%s:%s/%s" % (_dialect, _username, _password, _host, _port, _database)
engine = create_engine(_url)

r = redis.Redis(host="localhost", port=6379, db=0, decode_responses=True)

# Daemon 使用 Client Credentials Grant
daemon_awa = Osuawa(loop, st_secrets["args"]["client_id"], st_secrets["args"]["client_secret"], None, [Scope.PUBLIC.value], Domain.OSU.value, "daemon", None, None)
sem = asyncio.Semaphore(1)


def commands():
    return [
        Command(
            "save",
            "save user recent scores",
            [Int("user")],
            0,
            save_recent_scores,
        ),
        Command(
            "update",
            "update user recent scores",
            [
                Coll(
                    "user",
                    get_all_score_users(),
                ),
            ],
            0,
            save_recent_scores,
        ),
        Command(
            "beatmap",
            "update beatmaps",
            [
                JsonStr("obj"),
            ],
            0,
            update_beatmaps,
        ),
    ]


async def async_save_recent_scores(user: int, include_fails: bool) -> tuple[str, dict[str, CompletedSimpleScoreInfo]]:
    """返回 (username, completed_recent_scores_compact)"""
    user_scores: list[Score] = await daemon_awa.async_get_recent_scores(user, include_fails)
    recent_scores_compact: dict[str, SimpleScoreInfo] = {str(user_score.id): SimpleScoreInfo.from_score(user_score) for user_score in user_scores}
    return await asyncio.gather(
        async_get_username(daemon_awa.api, user),
        daemon_awa.complete_scores_compact(recent_scores_compact),
    )


def get_all_score_users() -> list[int]:
    with engine.begin() as conn:
        return list(
            conn.execute(
                text("SELECT DISTINCT USER_ID FROM SCORE ORDER BY USER_ID"),
            ).scalars(),
        )


def save_recent_scores(user: int, include_fails: bool = True) -> str:
    username: str
    completed_recent_scores_compact: dict[str, CompletedSimpleScoreInfo]
    username, completed_recent_scores_compact = daemon_awa.run_coro(async_save_recent_scores(user, include_fails))
    with engine.begin() as conn:
        # 插入到表 SCORE，如果遇到冲突，则放弃
        # 准备数据
        scores = []
        for pk, _v in completed_recent_scores_compact.items():
            score = asdict(
                _v,
                dict_factory=lambda items: {k.lstrip("_"): None if v is None else v.timestamp() if isinstance(v, datetime) else int(v) if isinstance(v, bool) else orjson.dumps(v).decode("utf-8") if isinstance(v, (list, dict)) else v for k, v in items},
            )
            score["score_id"] = pk
            scores.append(score)
        res = conn.execute(
            text(
                """INSERT INTO SCORE (SCORE_ID, BID, USER_ID, SCORE, ACCURACY, MAX_COMBO, PASSED, PP, MODS, TS, STATISTICS, ST, CS, HIT_WINDOW, PREEMPT, BPM, HIT_LENGTH, IS_NF, IS_HD, IS_HIGH_AR, IS_LOW_AR, IS_VERY_LOW_AR, IS_SPEED_UP, IS_SPEED_DOWN,
                                      INFO, ORIGINAL_DIFFICULTY, B_STAR_RATING, B_MAX_COMBO, B_AIM_DIFFICULTY, B_AIM_DIFFICULT_SLIDER_COUNT, B_SPEED_DIFFICULTY, B_SPEED_NOTE_COUNT, B_SLIDER_FACTOR, B_AIM_TOP_WEIGHTED_SLIDER_FACTOR,
                                      B_SPEED_TOP_WEIGHTED_SLIDER_FACTOR, B_AIM_DIFFICULT_STRAIN_COUNT, B_SPEED_DIFFICULT_STRAIN_COUNT, PP_AIM, PP_SPEED, PP_ACCURACY, B_PP_100IF_AIM, B_PP_100IF_SPEED, B_PP_100IF_ACCURACY, B_PP_100IF, B_PP_92IF,
                                      B_PP_81IF, B_PP_67IF)
                   VALUES (:score_id, :bid, :user, :score, :accuracy, :max_combo, :passed, :pp, :mods, :ts, :statistics, :st, :cs, :hit_window, :preempt, :bpm, :hit_length, :is_nf, :is_hd, :is_high_ar, :is_low_ar, :is_very_low_ar, :is_speed_up,
                           :is_speed_down, :info, :original_difficulty, :b_star_rating, :b_max_combo, :b_aim_difficulty, :b_aim_difficult_slider_count, :b_speed_difficulty, :b_speed_note_count, :b_slider_factor, :b_aim_top_weighted_slider_factor,
                           :b_speed_top_weighted_slider_factor, :b_aim_difficult_strain_count, :b_speed_difficult_strain_count, :pp_aim, :pp_speed, :pp_accuracy, :b_pp_100if_aim, :b_pp_100if_speed, :b_pp_100if_accuracy, :b_pp_100if, :b_pp_92if,
                           :b_pp_81if, :b_pp_67if)
                   ON CONFLICT DO NOTHING;""",
            ),
            scores,
        )

        len_diff = res.rowcount
    # noinspection PyStringFormat
    return "%s: got/diff: %d/%d" % (
        username,
        len(completed_recent_scores_compact),
        len_diff,
    )


# noinspection PyTypedDict
def create_tmp_playlist(name: str, beatmap_specs: list[BeatmapSpec]) -> list[DatabasePlaylistBeatmap]:
    """创建临时课题。仅创建，不生成

    status: 0=未审核, 1=已审核, 2=已提名

    OsuPlaylist beatmap 与 beatmap_specs、数据库字段对应关系如下：

    ```
    | BID  | SID | Artist - Title (Creator) [Version] | Stars | SR  | BPM | Hit Length | Max Combo | CS  | AR  | OD  | Mods | Notes | slot       |        |          |      |           |          |        | _Artist  | _Title  |
    | ---- | --- | ---------------------------------- | ----- | --- | --- | ---------- | --------- | --- | --- | --- | ---- | ----- | ---------- | ------ | -------- | ---- | --------- | -------- | ------ | -------- | ------- |
    | bid  |     |                                    |       |     |     |            |           |     |     |     |      | notes | slot       | status | comments | pool | suggestor | raw_mods | add_ts |          |         |
    | BID  | SID | INFO                               |       | SR  | BPM | HIT_LENGTH | MAX_COMBO | CS  | AR  | OD  | MODS | NOTES | SKILL_SLOT | STATUS | COMMENTS | POOL | SUGGESTOR | RAW_MODS | ADD_TS | U_ARTIST | U_TITLE |
    ```

    :param name: 课题名（在线环境中建议直接用 UID）
    :param beatmap_specs: bid, raw_mods, slot, pool, notes, status, comments, suggestor, add_ts
    :return: 一个谱面列表，为数据库字段优化了键名
    """
    tmp_playlist_filename = _create_tmp_playlist_p(name, beatmap_specs)
    # noinspection PyBroadException
    try:
        tmp_playlist = OsuPlaylist(daemon_awa, tmp_playlist_filename, css_style=1)  # 这里 css_style 不知道用哪一个好
        playlist_beatmaps_raw: list[CompletedPlaylistBeatmap] = daemon_awa.run_coro(tmp_playlist.playlist_task())  # 这里面每一个 dict 都表示一个 playlist beatmap
    except:  # 这里无法确定是什么东西报错了，因为内部是 async 的 TaskGroup  # noqa: E722
        raise ValueError("failed to parse the spec(s): %s" % beatmap_specs)
    # playlist_beatmaps_raw 的顺序可能和传入的 specs 顺序不一致
    # 原始 beatmap 的键应包含如下
    # # BID, SID, Artist - Title (Creator) [Version], Stars, SR, BPM, Hit Length, Max Combo, CS, AR, OD, Mods, Notes, slot
    # 根据 # 键对其进行排序是有必要的。# 从 1 开始递增，它与传入的 specs 顺序一致
    playlist_beatmaps_raw.sort(key=lambda x: int(x["#"]))
    playlist_beatmaps_db = []
    for i, playlist_beatmap_raw in enumerate(playlist_beatmaps_raw):
        if len(playlist_beatmap_raw["slot"]) < 3:
            raise ValueError("slot too short: %s" % playlist_beatmap_raw["slot"])
        if len(playlist_beatmap_raw["slot"]) > int(C.SLOT_MAX_LEN.value):
            raise ValueError("slot too long: %s" % playlist_beatmap_raw["slot"])
        playlist_beatmaps_db.append(
            DatabasePlaylistBeatmap(
                BID=playlist_beatmap_raw["BID"],
                SID=playlist_beatmap_raw["SID"],
                INFO=playlist_beatmap_raw["Artist - Title (Creator) [Version]"],
                SKILL_SLOT=playlist_beatmap_raw["slot"],
                SR=playlist_beatmap_raw["SR"],  # 相比 Stars，SR 不依赖特殊字体
                BPM=playlist_beatmap_raw["BPM"],
                HIT_LENGTH=playlist_beatmap_raw["Hit Length"],
                MAX_COMBO=playlist_beatmap_raw["Max Combo"],
                CS=playlist_beatmap_raw["CS"],
                AR=playlist_beatmap_raw["AR"],
                OD=playlist_beatmap_raw["OD"],
                MODS=playlist_beatmap_raw["Mods"],
                NOTES=playlist_beatmap_raw["Notes"],  # notes 在 OsuPlaylist 里有默认值处理
                STATUS=beatmap_specs[i][5],
                COMMENTS=beatmap_specs[i][6],
                POOL=beatmap_specs[i][3],
                SUGGESTOR=beatmap_specs[i][7],
                RAW_MODS=orjson.dumps(beatmap_specs[i][1]).decode(),  # orjson 序列化时输出的是紧凑的字符串
                ADD_TS=beatmap_specs[i][8],
                U_ARTIST=playlist_beatmap_raw["_Artist"],
                U_TITLE=playlist_beatmap_raw["_Title"],
            ),
        )
    # 删除临时文件
    if os.path.exists(tmp_playlist_filename):
        os.remove(tmp_playlist_filename)
    return playlist_beatmaps_db


def _update_beatmap(beatmap: Optional[DatabasePlaylistBeatmap], old_bid: Optional[int] = None, old_mods: Optional[str] = None) -> str:
    """更新课题谱面（包括删除）

    如果 beatmap 不为 None，则 old_bid 必须为 None

    :param beatmap: 欲更新的谱面
    :param old_bid: 欲删除的 BID（主键之一）
    :param old_mods: 欲删除的 MODS（主键之二）
    :return 结果消息
    """
    # 操作符号（二进制）: 0                0
    #                     ^                ^
    #             第一位代表更新   第二位代表删除
    # 如果 beatmap 不为 None，则第一位为 1
    # 如果 old_bid 和 old_mods 均不为 None，则第二位为 1
    # 如果结果为 00，则代表没有进行任何操作，raise ValueError
    # 如果结果为 10，则代表新增谱面
    # 如果结果为 01，则代表删除谱面
    # 如果结果为 11，则代表更新谱面
    action = 0b00

    if beatmap is not None:
        # 如果 beatmap 不为 None，old_bid 从 beatmap 中获取
        if old_bid is not None:
            # 提供 beatmap 意味着更新/修改，此时不允许删除操作
            raise ValueError("cannot update a beatmap from another bid")
        old_bid = beatmap["BID"]

    with engine.begin() as conn:
        action_bid: int
        action_mods: str
        if old_bid is not None and old_mods is not None:
            action |= 1
            # 有可能传入的是 numpy 类型，需要强制转化为原生类型
            old_bid: int = int(old_bid)
            old_mods: str = str(old_mods)
            action_bid = old_bid
            action_mods = old_mods
            conn.execute(
                text(
                    """DELETE
                       FROM BEATMAP
                       WHERE BID = :bid
                         AND MODS = :mods""",
                ),
                {"bid": old_bid, "mods": old_mods},
            )
        if beatmap is not None:
            action |= 2
            action_bid = beatmap["BID"]
            action_mods = beatmap["MODS"]
            conn.execute(
                text(
                    """INSERT INTO BEATMAP (BID, SID, INFO, SKILL_SLOT, SR, BPM, HIT_LENGTH, MAX_COMBO, CS, AR, OD, MODS, NOTES, STATUS, COMMENTS, POOL, SUGGESTOR, RAW_MODS, ADD_TS, U_ARTIST, U_TITLE)
                       VALUES (:BID, :SID, :INFO, :SKILL_SLOT, :SR, :BPM, :HIT_LENGTH, :MAX_COMBO, :CS, :AR, :OD, :MODS, :NOTES, :STATUS, :COMMENTS, :POOL, :SUGGESTOR, :RAW_MODS, :ADD_TS, :U_ARTIST, :U_TITLE)
                       ON CONFLICT (BID, MODS)
                           DO UPDATE SET SKILL_SLOT = EXCLUDED.SKILL_SLOT,
                                         SR         = EXCLUDED.SR,
                                         BPM        = EXCLUDED.BPM,
                                         HIT_LENGTH = EXCLUDED.HIT_LENGTH,
                                         MAX_COMBO  = EXCLUDED.MAX_COMBO,
                                         CS         = EXCLUDED.CS,
                                         AR         = EXCLUDED.AR,
                                         OD         = EXCLUDED.OD,
                                         MODS       = EXCLUDED.MODS,
                                         NOTES      = EXCLUDED.NOTES,
                                         STATUS     = EXCLUDED.STATUS,
                                         COMMENTS   = EXCLUDED.COMMENTS,
                                         POOL       = EXCLUDED.POOL,
                                         SUGGESTOR  = EXCLUDED.SUGGESTOR,
                                         RAW_MODS   = EXCLUDED.RAW_MODS,
                                         INFO       = EXCLUDED.INFO
                    -- 注意：ADD_TS 只会保留第一次创建记录时的值，后续不会被更新""",
                ),
                parameters=beatmap,
            )
        if action == 0b00:
            raise ValueError("no changes made")
    verb_mapping = {
        0b01: "deleted",
        0b10: "added",
        0b11: "updated",
    }
    msg = "%s (%d %s)" % (verb_mapping[action], action_bid, action_mods)
    if old_mods is not None:
        msg += " from %s" % old_mods
    return msg


def setup_scheduled_tasks():
    schedule.every(12).hours.do(
        push_task,
        r,
        "update .*",
    )


def update_beatmaps(obj: list[BeatmapToUpdate]):
    beatmap_specs: list[BeatmapSpec] = []
    name = ""
    has_spec_list: list[bool] = []
    for beatmap_to_update in obj:
        beatmap_spec: BeatmapSpec | list | None = beatmap_to_update.get("beatmap")  # JSON 反序列化时会自动转换为 list 类型，需要强制转换为 BeatmapSpec 类型
        if beatmap_spec is not None:
            has_spec_list.append(True)
            beatmap_specs.append(BeatmapSpec(*beatmap_spec))
            name = beatmap_to_update["name"]
        else:
            has_spec_list.append(False)
    database_beatmaps = create_tmp_playlist(name, beatmap_specs)
    for i, beatmap_to_update in enumerate(obj):
        database_beatmap = database_beatmaps[i] if has_spec_list[i] else None
        old_bid = beatmap_to_update.get("old_bid")
        old_mods = beatmap_to_update.get("old_mods")
        _update_beatmap(database_beatmap, old_bid, old_mods)


if __name__ == "__main__":
    cmdparser = CommandParser()
    cmdparser.register_command(0, *commands())

    formatter = logging.Formatter(st_config["logger"]["messageFormat"])
    ch = logging.StreamHandler()
    ch.setLevel(logging.DEBUG)
    ch.setFormatter(formatter)
    fh = logging.FileHandler(os.path.join(C.LOGS.value, "daemon.log"), encoding="utf-8")
    fh.setFormatter(formatter)
    logger = logging.getLogger("daemon")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(ch)
    logger.addHandler(fh)
    logger.info("Redis connected")

    setup_scheduled_tasks()

    while True:
        try:
            result: tuple[str, str] | None = cast(tuple[str, str], cast(object, r.brpop([C.TASK_QUEUE.value], timeout=5)))
            if result is None:
                continue
            task_info = result[1]
            if task_info:
                # task_info 结构为 uuid hex + command 的字符串拼接
                task_id = task_info[:32]
                task_cmd = task_info[32:]
                logger.info(f"[{task_id}/started]: {task_cmd}")
                g = cmdparser.parse_command(task_cmd)
                while True:
                    try:
                        logger.info(f"[{task_id}/executing]: {next(g)}")
                    except StopIteration as e:
                        logger.info(f"[{task_id}/success]: {e.value} sub-tasks done")
                        r.hset(
                            C.TASK_STATUS.value.format(task_id=task_id),
                            mapping={
                                "status": "success",
                                "result": str(e.value),
                                "time": time(),
                            },
                        )
                        break
                    except Exception as e:
                        logger.error(f"[{task_id}/error]: {e}", exc_info=True)
                        r.hset(
                            C.TASK_STATUS.value.format(task_id=task_id),
                            mapping={
                                "status": "error",
                                "result": str(e),
                                "time": time(),
                            },
                        )
                        break
        except KeyboardInterrupt:
            break
