import os.path

import streamlit as st

import osuawa

DEBUG_MODE = False  # switch to False when deploying

if not os.path.exists(osuawa.Path.LOGS.value):
    os.mkdir(osuawa.Path.LOGS.value)
if not os.path.exists(osuawa.Path.OUTPUT_DIRECTORY.value):
    os.mkdir(osuawa.Path.OUTPUT_DIRECTORY.value)
    os.mkdir(os.path.join(osuawa.Path.OUTPUT_DIRECTORY.value, osuawa.Path.RAW_RECENT_SCORES.value))
    os.mkdir(os.path.join(osuawa.Path.OUTPUT_DIRECTORY.value, osuawa.Path.RECENT_SCORES.value))

if "lang" not in st.session_state:
    import locale

    st.session_state._lang = locale.getlocale()[0]
    st.session_state.lang = None

if st.session_state.lang != st.session_state._lang:  # language changed
    import gettext

    st.session_state.lang = st.session_state._lang
    t = gettext.translation("messages", localedir=osuawa.Path.LOCALE.value, languages=[st.session_state.lang], fallback=True)

    import builtins

    st.session_state._ = t.gettext
    builtins.__dict__["_"] = st.session_state._
pg_homepage = st.Page("Home.py", title=_("Homepage"))
pg_score_visualizer = st.Page("tools/Score_visualizer.py", title=_("Score visualizer"))
pg_playlist_generator = st.Page("tools/Playlist_generator.py", title=_("Playlist generator"))
pg_recorder = st.Page("tools/Recorder.py", title=_("Recorder"))
pg = st.navigation([pg_homepage, pg_score_visualizer, pg_playlist_generator, pg_recorder])

st.session_state.DEBUG_MODE = DEBUG_MODE
pg.run()
