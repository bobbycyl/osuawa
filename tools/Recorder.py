import os.path
import time

import streamlit as st

from osuawa import Osuawa, Path

if st.session_state.get("perm", 0) >= 1:
    st.text_input(_("Ruleset"), value="osu", key="rec_mode")
    st.number_input(_("Limit"), min_value=1, max_value=50, value=5, key="rec_limit")
    st.session_state.awa: Osuawa
    user = st.session_state.awa.client.get_own_data()
    w = st.text("")
    while True:
        user_scores_current = st.session_state.awa.client.get_user_scores(
            user=user.id,
            type="recent",
            mode=st.session_state.rec_mode,
            include_fails=True,
            limit=st.session_state.rec_limit,
        )
        with open(os.path.join(Path.OUTPUT_DIRECTORY.value, "records.txt"), "w") as fo:
            fo.write("\n".join([f"{score.beatmap_id}" for score in user_scores_current]))
        left = 30
        while left > 0:
            w.text(_("Next update in %d seconds") % left)
            time.sleep(1)
            left -= 1
else:
    st.error(_("Permission denied"))
