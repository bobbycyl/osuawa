from typing import TYPE_CHECKING

import streamlit as st

from osuawa import Osuawa
from osuawa.components import init_page
from osuawa.utils import CompletedSimpleScoreInfo, SimpleScoreInfo

if TYPE_CHECKING:

    def _(_text: str) -> str: ...

    # noinspection PyTypeHints
    st.session_state.awa: Osuawa

init_page(_("Room Spectator") + " - osuawa")


async def create_multiplayer_scores_dataframe(room_ids: list[int]) -> dict[str, CompletedSimpleScoreInfo]:
    scores = await st.session_state.awa.async_get_rooms_scores(room_ids)
    scores_compact = {str(score.id): SimpleScoreInfo.from_score(score) for score in scores}
    return await st.session_state.awa.complete_scores_compact(scores_compact)


room_ids_input = st.text_input(_("Room IDs, separated by spaces"), key="mp_room_id")

room_ids: list[int] = []
if room_ids_input is None or room_ids_input == "":
    st.error(_("Please input room IDs"))
else:
    room_ids = [int(room_id) for room_id in room_ids_input.split()]

df = st.session_state.awa.create_scores_dataframe(st.session_state.awa.run_coro(create_multiplayer_scores_dataframe(room_ids)))
st.dataframe(df)
