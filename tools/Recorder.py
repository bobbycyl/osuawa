import asyncio
import os.path
import threading
from asyncio import Task
from typing import Any

import orjson
import pandas as pd
import streamlit as st
from streamlit import logger
from websockets.sync.client import connect

from osuawa import C, Osuawa
from osuawa.components import init_page
from osuawa.utils import CompletedSimpleScoreInfo, regex_search_column

assert isinstance(st.session_state.awa, Osuawa)
init_page(_("Recorder") + " - osuawa")


async def get_users_beatmap_scores(ids: list[int], beatmap: int) -> pd.DataFrame:
    tasks: list[Task[dict[str, CompletedSimpleScoreInfo]]] = []
    async with asyncio.TaskGroup() as tg:
        for user_id in ids:
            tasks.append(tg.create_task(st.session_state.awa.a_get_user_beatmap_scores(beatmap, user_id)))
    scores_compact: dict[str, CompletedSimpleScoreInfo] = {}
    for task in tasks:
        scores_compact.update(task.result())
    return st.session_state.awa.create_scores_dataframe(scores_compact)


@st.cache_data(ttl=60)
def friends() -> dict[int, str]:
    raw_friends: list[dict[str, Any]] = asyncio.run(st.session_state.awa.a_get_friends())
    ret_friends = {}
    for raw_friend in raw_friends:
        ret_friends[raw_friend["user_id"]] = raw_friend["username"]
    return ret_friends


def tosu_df_style(row) -> list[str]:
    if row["accuracy"] == 1.0:
        return ["background-color: lavenderblush"] * len(row)
    elif row["accuracy"] >= 0.95 and row["max_combo"] == row["b_max_combo"]:
        return ["background-color: aliceblue"] * len(row)
    elif row["accuracy"] >= 0.80:
        return ["background-color: mintcream"] * len(row)
    elif not row["passed"]:
        return ["background-color: darkgray"] * len(row)
    else:
        return [""] * len(row)


def tosu_main() -> None:
    with connect("%s%s" % (st.session_state.rec_tosu_url.rstrip("/"), "/websocket/v2")) as websocket:
        message = websocket.recv()
        obj = orjson.loads(message)
        bid: int = obj["beatmap"]["id"]
        logger.get_logger(st.session_state.username).info("getting records of %d" % bid)
        reversed_friends = {v: k for k, v in friends().items()}
        quickly_selected_friend_ids = [reversed_friends[username] for username in quickly_selected_friend_usernames]
        try:
            ids = sorted(list(set(orjson.loads(st.session_state.rec_user_ids) + quickly_selected_friend_ids)))
        except orjson.JSONDecodeError:
            st.error(_("invalid user ids"))
            st.stop()
        df = asyncio.run(get_users_beatmap_scores(ids, bid))
        df["username"] = df["user"].map(friends())
        # sort by score
        df = df.sort_values(by="score", ascending=False)

        # apply filter
        if st.session_state.rec_tosu_mods != "":
            # df = df[df["mods"].str.contains(st.session_state.rec_tosu_mods)]
            # 使用 re.search st.session_state.rec_tosu_mods，如果搜索得到，就显示
            df = regex_search_column(df, "mods", st.session_state.rec_tosu_mods)
        if st.session_state.rec_tosu_best:
            # select scores with best pp by user_id
            df = df.groupby("user").first()
        if st.session_state.rec_tosu_prettify:
            # row background color by rank
            column_order = ("username", "pp", "accuracy", "max_combo", "total_score", "mods", "ts")
            st.dataframe(df.style.apply(tosu_df_style, axis=1), column_order=column_order, hide_index=True)
        else:
            st.dataframe(df)
        with st.expander(_("full info")):
            st.write(obj)


st.markdown(_("## tosu Panel"))
st.text_input("tosu URL", value="ws://127.0.0.1:24050/", key="rec_tosu_url")
with st.expander(_("table options")):
    st.text_input(_("mods filter (regex)"), key="rec_tosu_mods")
    st.toggle(_("best only"), key="rec_tosu_best")
    st.toggle(_("prettify"), value=True, key="rec_tosu_prettify")
with st.form("quickly add friend ids"):
    quickly_selected_friend_usernames: list[str] = st.multiselect(_("friends"), list(friends().values()))
    st.form_submit_button(_("submit"))
st.text_input(_("manually add user ids"), value="[%d]" % st.session_state.user, key="rec_user_ids")
try:
    tosu_main()
except (ConnectionError, ConnectionRefusedError, TimeoutError):
    st.error(_("failed to connect to tosu"))
    st.stop()

st.divider()

st.markdown(_("## Local Beatmap ID Records"))
st.text_input(_("Ruleset"), value="osu", key="rec_mode")
st.number_input(_("Limit"), min_value=1, max_value=50, value=5, key="rec_limit")
w = st.text("")
user_scores_current = asyncio.run(
    st.session_state.awa.api.user_scores(
        user_id=st.session_state.user,
        type="recent",
        mode=st.session_state.rec_mode,
        include_fails=True,
        limit=st.session_state.rec_limit,
    ),
)
st.write(user_scores_current)
with threading.Lock():
    with open(os.path.join(C.OUTPUT_DIRECTORY.value, "records_%s.txt") % st.session_state.username, "w") as fo:
        fo.write("\n".join([f"{score.bid}" for score in user_scores_current]))

if st.button(_("clear all caches"), icon=":material/cleaning_services:"):
    st.cache_data.clear()
