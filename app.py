import builtins
import gettext
import os.path

import streamlit as st
from babel import Locale

from osuawa import LANGUAGES, Path


def convert_locale(accept_language: str):
    try:
        parsed_locale = Locale.parse(accept_language.split(",")[0], sep="-")
        converted_lang = "%s_%s" % (parsed_locale.language, parsed_locale.territory)
        if converted_lang not in LANGUAGES:
            return "en_US"
        else:
            return converted_lang
    except:
        return "en_US"


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
    if not os.path.exists(Path.STATIC_DIRECTORY.value):
        os.mkdir(Path.STATIC_DIRECTORY.value)
    if not os.path.exists(Path.UPLOADED_DIRECTORY.value):
        os.mkdir(Path.UPLOADED_DIRECTORY.value)
    if not os.path.exists(Path.BEATMAPS_CACHE_DIRECTORY.value):
        os.mkdir(Path.BEATMAPS_CACHE_DIRECTORY.value)
    st.session_state._uni_lang_value = convert_locale(st.context.locale)

# noinspection PyUnresolvedReferences
builtins.__dict__["_"] = gettext_translate
st.session_state.translate = gettext_getfunc(st.session_state._uni_lang_value)

pg_homepage = st.Page("Home.py", title=_("Homepage"))
pg_score_visualizer = st.Page("tools/Score_visualizer.py", title=_("Score visualizer"))
pg_playlist_generator = st.Page("tools/Playlist_generator.py", title=_("Playlist generator"))
pg_recorder = st.Page("tools/Recorder.py", title=_("Recorder"))
if "awa" in st.session_state:
    pg = st.navigation([pg_homepage, pg_score_visualizer, pg_playlist_generator, pg_recorder])
else:
    pg = st.navigation([pg_homepage, pg_score_visualizer])

pg.run()
