import builtins
import gettext
import os.path

import streamlit as st
from babel import Locale

from osuawa import C, LANGUAGES


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
        return gettext.translation("messages", localedir=C.LOCALE.value, languages=[lang], fallback=True).gettext(text)

    return translate


def gettext_translate(text):
    return st.session_state.translate(text)


if "translate" not in st.session_state:
    if not os.path.exists(C.LOGS.value):
        os.mkdir(C.LOGS.value)
    if not os.path.exists(C.OUTPUT_DIRECTORY.value):
        os.mkdir(C.OUTPUT_DIRECTORY.value)
        os.mkdir(os.path.join(C.OUTPUT_DIRECTORY.value, C.RAW_RECENT_SCORES.value))
        os.mkdir(os.path.join(C.OUTPUT_DIRECTORY.value, C.RECENT_SCORES.value))
    if not os.path.exists(C.STATIC_DIRECTORY.value):
        os.mkdir(C.STATIC_DIRECTORY.value)
    if not os.path.exists(C.UPLOADED_DIRECTORY.value):
        os.mkdir(C.UPLOADED_DIRECTORY.value)
    if not os.path.exists(C.BEATMAPS_CACHE_DIRECTORY.value):
        os.mkdir(C.BEATMAPS_CACHE_DIRECTORY.value)
    st.session_state._uni_lang_value = convert_locale(st.context.locale)
    # 数据库需要以下表和字段
    # 1. 表 BEATMAP，字段固定为 BID,SID,INFO,SLOT,SR,BPM,HIT_LENGTH,MAX_COMBO,CS,AR,OD,MODS,NOTES （一个经过修改的课题字段，后续可以复用生成课题的代码，逻辑是一样的），使用 BID + Mods 作为主键
    conn = st.connection("osuawa", type="sql")
    with conn.session as s:
        s.execute("CREATE TABLE IF NOT EXISTS BEATMAP(BID INT, SID INT, INFO TEXT, SLOT TEXT, SR REAL, BPM REAL, HIT_LENGTH INT, MAX_COMBO INT, CS REAL, AR REAL, OD REAL, MODS TEXT, NOTES TEXT, PRIMARY KEY (BID, MODS));")
        s.commit()

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
