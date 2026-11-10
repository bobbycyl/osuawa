from typing import TYPE_CHECKING

import streamlit as st

from osuawa import Osuawa
from osuawa.components import init_page
from osuawa.utils import CompletedSimpleScoreInfo, SimpleScoreInfo

if TYPE_CHECKING:

    def _(_text: str) -> str: ...


init_page(_("Room Spectator") + " - osuawa")


async def get_multiplayer_scores(
    awa: Osuawa,
    room_ids: list[int],
) -> dict[str, CompletedSimpleScoreInfo]:
    scores = await awa.async_get_rooms_scores(room_ids)
    scores_compact = {str(score.id): SimpleScoreInfo.from_score(score) for score in scores}
    return await awa.complete_scores_compact(scores_compact)


st.text_input(_("Room IDs, separated by spaces"), key="mp_room_id")

room_ids: list[int] = []
if st.session_state.mp_room_id is None or st.session_state.mp_room_id == "":
    st.error(_("Please input room IDs"))
    st.stop()
else:
    room_ids = [int(room_id) for room_id in st.session_state.mp_room_id.split()]

df = st.session_state.awa.create_scores_dataframe(st.session_state.awa.run_coro(get_multiplayer_scores(st.session_state.awa, room_ids)))
st.dataframe(df)
