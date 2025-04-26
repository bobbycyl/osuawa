import asyncio
import os.path
import threading

import orjson
import pandas as pd
import streamlit as st
from websockets.sync.client import connect

import osuawa
from osuawa import Path

st.session_state.awa: osuawa.Osuawa  # type: ignore

if "wide_layout" in st.session_state:
    st.set_page_config(page_title=_("Recorder") + " - osuawa", layout="wide" if st.session_state.wide_layout else "centered")
else:
    st.set_page_config(page_title=_("Recorder") + " - osuawa")
with st.sidebar:
    st.toggle(_("wide page layout"), key="wide_layout", value=False)


async def get_users_beatmap_scores(ids: list[int], bid: int) -> pd.DataFrame:
    ids.append(st.session_state.user_id)
    ids = list(set(ids))
    dfs = []
    tasks = []
    async with asyncio.TaskGroup() as tg:
        for user_id in ids:
            tasks.append(tg.create_task(st.session_state.awa.create_scores_dataframe(await st.session_state.awa._get_user_beatmap_scores(bid, user_id))))
    for task in tasks:
        df_cur = task.result()
        if not df_cur.empty:
            dfs.append(df_cur)
    return pd.concat(dfs, ignore_index=True)


st.text_input("tosu url", value="ws://127.0.0.1:24050/", key="rec_tosu_url")
with st.expander(_("table options")):
    st.text_input("mods contains", key="rec_tosu_mods")
    st.toggle(_("best only"), key="rec_tosu_best")
    st.toggle(_("prettify"), value=True, key="rec_tosu_prettify")


def tosu_df_style(row):
    if row["rank"] in ("X", "XH"):
        return ["background-color: lavenderblush"] * len(row)
    elif row["rank"] in ("S", "SH"):
        return ["background-color: aliceblue"] * len(row)
    elif row["rank"] == "A":
        return ["background-color: mintcream"] * len(row)
    elif row["rank"] == "F":
        return ["background-color: darkgray"] * len(row)
    else:
        return [""] * len(row)


def tosu_main():
    with connect("%s%s" % (st.session_state.rec_tosu_url.rstrip("/"), "/websocket/v2")) as websocket:
        message = websocket.recv()
        obj = orjson.loads(message)
        bid: int = obj["beatmap"]["id"]
        df = asyncio.run(get_users_beatmap_scores(orjson.loads(st.session_state.rec_user_ids), bid))

        # apply filter
        if st.session_state.rec_tosu_mods != "":
            df = df[df["mods"].str.contains(st.session_state.rec_tosu_mods)]
        if st.session_state.rec_tosu_best:
            # select scores with best pp by user_id
            df = df.groupby("user_id").first()
        if st.session_state.rec_tosu_prettify:
            # sort by pp
            df = df.sort_values(by="pp", ascending=False)
            # row background color by rank
            column_order = ("username", "pp", "accuracy", "max_combo", "total_score", "mods", "ts")
            hide_index = True
            st.dataframe(df.style.apply(tosu_df_style, axis=1), column_order=column_order, hide_index=hide_index)
        else:
            st.dataframe(df)
        with st.expander(_("full info")):
            st.write(obj)


st.text_input(_("User IDs"), value="", key="rec_user_ids")
tosu_main()
st.divider()
st.text_input(_("Ruleset"), value="osu", key="rec_mode")
st.number_input(_("Limit"), min_value=1, max_value=50, value=5, key="rec_limit")
w = st.text("")
user_scores_current = asyncio.run(
    st.session_state.awa.api.user_scores(
        user_id=st.session_state.user_id,
        type="recent",
        mode=st.session_state.rec_mode,
        include_fails=True,
        limit=st.session_state.rec_limit,
    )
)
st.write(user_scores_current)
with threading.Lock():
    with open(os.path.join(Path.OUTPUT_DIRECTORY.value, "records_%s.txt") % st.session_state.username, "w") as fo:
        fo.write("\n".join([f"{score.beatmap_id}" for score in user_scores_current]))
#
# if st.button(_("Clear all caches")):
#     st.cache_data.clear()
