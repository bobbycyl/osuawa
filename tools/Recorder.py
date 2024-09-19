import asyncio
import os.path
import time

import streamlit as st

from osuawa import Path

st.text_input(_("Ruleset"), value="osu", key="rec_mode")
st.number_input(_("Limit"), min_value=1, max_value=50, value=5, key="rec_limit")
w = st.text("")
while True:
    user_scores_current = asyncio.run(
        st.session_state.awa.client.get_user_scores(
            user=st.session_state.user_id,
            type="recent",
            mode=st.session_state.rec_mode,
            include_fails=True,
            limit=st.session_state.rec_limit,
        )
    )
    st.write(user_scores_current)
    with open(os.path.join(Path.OUTPUT_DIRECTORY.value, "records_%s.txt") % st.session_state.username, "w") as fo:
        fo.write("\n".join([f"{score.beatmap_id}" for score in user_scores_current]))
    left = 30
    while left > 0:
        w.text(_("Next update in %d seconds") % left)
        time.sleep(1)
        left -= 1
