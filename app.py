import gettext
import os.path

import streamlit as st

import locale
from osuawa import Path

DEBUG_MODE = False  # switch to False when deploying

if not os.path.exists(Path.LOGS.value):
    os.mkdir(Path.LOGS.value)
if not os.path.exists(Path.OUTPUT_DIRECTORY.value):
    os.mkdir(Path.OUTPUT_DIRECTORY.value)
    os.mkdir(os.path.join(Path.OUTPUT_DIRECTORY.value, Path.RAW_RECENT_SCORES.value))
    os.mkdir(os.path.join(Path.OUTPUT_DIRECTORY.value, Path.RECENT_SCORES.value))

gettext.bindtextdomain(domain="messages", localedir=Path.LOCALE.value)
if "lang" not in st.session_state:
    lang = gettext.translation("messages", localedir=Path.LOCALE.value, languages=[locale.getlocale()[0]], fallback=True)
else:
    lang = gettext.translation("messages", localedir=Path.LOCALE.value, languages=[st.session_state.lang], fallback=True)
lang.install()
pg_homepage = st.Page("Home.py", title=_("Homepage"))
pg_score_visualizer = st.Page("tools/Score_visualizer.py", title=_("Score visualizer"))
pg_playlist_generator = st.Page("tools/Playlist_generator.py", title=_("Playlist generator"))
pg = st.navigation([pg_homepage, pg_score_visualizer, pg_playlist_generator])

st.session_state.DEBUG_MODE = DEBUG_MODE
pg.run()
