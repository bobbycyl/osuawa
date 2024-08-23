import builtins
import gettext
import locale
import os.path

import streamlit as st

from osuawa import LANGUAGES, Path

DEBUG_MODE = False  # switch to False when deploying


def gettext_getfunc(lang):
    def translate(text):
        return gettext.translation("messages", localedir=Path.LOCALE.value, languages=[lang], fallback=True).gettext(text)

    return translate


def gettext_translate(text):
    return st.session_state.translate(text)


if "translate" not in st.session_state:
    if not os.path.exists(Path.LOGS.value):
        os.mkdir(Path.LOGS.value)
    if not os.path.exists(Path.OUTPUT_DIRECTORY.value):
        os.mkdir(Path.OUTPUT_DIRECTORY.value)
        os.mkdir(os.path.join(Path.OUTPUT_DIRECTORY.value, Path.RAW_RECENT_SCORES.value))
        os.mkdir(os.path.join(Path.OUTPUT_DIRECTORY.value, Path.RECENT_SCORES.value))
    lang = locale.getlocale()[0]
else:
    lang = LANGUAGES[st.session_state._uni_lang_value]

builtins.__dict__["_"] = gettext_translate
st.session_state.translate = gettext_getfunc(lang)

pg_homepage = st.Page("Home.py", title=_("Homepage"))
pg_score_visualizer = st.Page("tools/Score_visualizer.py", title=_("Score visualizer"))
pg_playlist_generator = st.Page("tools/Playlist_generator.py", title=_("Playlist generator"))
pg_recorder = st.Page("tools/Recorder.py", title=_("Recorder"))
pg = st.navigation([pg_homepage, pg_score_visualizer, pg_playlist_generator, pg_recorder])

st.session_state.DEBUG_MODE = DEBUG_MODE
pg.run()
