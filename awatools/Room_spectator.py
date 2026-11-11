from typing import TYPE_CHECKING

import pandas as pd
import streamlit as st

from osuawa import Osuawa
from osuawa.components import awa_query, init_page
from osuawa.utils import CompletedSimpleScoreInfo, SimpleScoreInfo, slot_sort_key

if TYPE_CHECKING:

    def _(_text: str) -> str: ...


init_page(_("Room Spectator") + " - osuawa")


async def get_multiplayer_scores(
    awa: Osuawa,
    room_ids: list[int],
    pool_bids: set[int] | None = None,
) -> dict[str, CompletedSimpleScoreInfo]:
    scores = await awa.async_get_rooms_scores(room_ids)
    if pool_bids is not None:
        scores = [score for score in scores if int(score.beatmap_id) in pool_bids]
    scores_compact = {str(score.id): SimpleScoreInfo.from_score(score) for score in scores}
    return await awa.complete_scores_compact(scores_compact)


st.text_input(_("Room IDs, separated by spaces"), key="mp_room_id")
available_pools = awa_query(
    "BEATMAP",
    """SELECT DISTINCT POOL
                                          FROM BEATMAP
                                          ORDER BY POOL""",
)["POOL"].to_list()
st.selectbox(_("Playlist Pool"), available_pools, key="mp_pool")
st.checkbox(_("Only maps in pool"), value=True, key="mp_only_pool")

published_playlist = awa_query(
    "BEATMAP",
    """SELECT *
                                             FROM BEATMAP
                                             WHERE POOL = :pool
                                               AND STATUS = 2""",
    params={"pool": st.session_state.mp_pool},
)
published_playlist.columns = published_playlist.columns.str.lower()
# 不按 mods 匹配：FM 会扩展成选手实际选择的 mod，标称 mods 不能当匹配键
duplicate_bids = published_playlist["bid"].duplicated(keep=False)
if duplicate_bids.any():
    dup_list = sorted(published_playlist.loc[duplicate_bids, "bid"].unique().tolist())
    st.warning(_("Duplicate BIDs in the published playlist: %s. Only the first row of each BID is kept.") % ", ".join(str(bid) for bid in dup_list))
pool_maps = published_playlist[["bid", "skill_slot", "pool"]].drop_duplicates("bid", keep="first").rename(columns={"skill_slot": "slot"})
pool_bids = set(pool_maps["bid"].astype(int))

if not st.session_state.mp_room_id:
    st.error(_("Please input room IDs"))
    st.stop()
room_ids = [int(room_id) for room_id in st.session_state.mp_room_id.split()]

completed = st.session_state.awa.run_coro(
    get_multiplayer_scores(
        st.session_state.awa,
        room_ids,
        pool_bids if st.session_state.mp_only_pool else None,
    )
)
if not completed:
    st.warning(_("No scores found."))
    st.stop()

detail_df = st.session_state.awa.create_scores_dataframe(completed).merge(pool_maps, on="bid", how="left")

score = pd.to_numeric(detail_df["score"], errors="coerce")
grouped = score.groupby(detail_df["bid"])
detail_df["zscore"] = (score - grouped.transform("mean")) / grouped.transform("std")
detail_df["bid_rank"] = score.groupby(detail_df["bid"]).rank(ascending=False, method="min").astype("Int64")

st.header(_("Statsheet"))
st.caption(_("Global zscore and rank per BID, across all rooms."))
stat_columns = [column for column in ("score_id", "user", "score", "accuracy", "max_combo", "mods", "zscore", "bid_rank") if column in detail_df.columns]
groups = dict(detail_df.groupby("bid", sort=False))
bid_slots = {bid: group["slot"].dropna().iloc[0] if group["slot"].notna().any() else None for bid, group in groups.items()}
# 展示顺序与 Playlist Generator 的 "Match sorting" 共用同一套 slot 排序规则
for bid in sorted(groups, key=lambda b: slot_sort_key(bid_slots[b])):
    group = groups[bid]
    slot = bid_slots[bid] or "-"
    st.markdown("### %s · %s" % (slot, bid))
    st.dataframe(group[stat_columns].sort_values("bid_rank"), hide_index=True, use_container_width=True)
