import builtins
import gettext
import os.path

import streamlit as st
from babel import Locale
from sqlalchemy import text

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
    # 半持久化保存
    if not os.path.exists("./.streamlit/.oauth"):
        os.mkdir("./.streamlit/.oauth")
    if not os.path.exists("./.streamlit/.components"):
        os.mkdir("./.streamlit/.components")
    # 数据库需要以下表和字段
    # 1. 表 BEATMAP，字段固定为 BID,SID,INFO,SKILL_SLOT,SR,BPM,HIT_LENGTH,MAX_COMBO,CS,AR,OD,MODS,NOTES,STATUS,COMMENTS,POOL,SUGGESTOR,RAW_MODS,ADD_TS （一个经过修改的课题字段，后续可以复用生成课题的代码，逻辑是一样的），使用 BID + MODS 作为主键
    conn = st.connection("osuawa", type="sql")
    with conn.session as s:
        s.execute(
            text(
                "CREATE TABLE IF NOT EXISTS BEATMAP(BID INT, SID INT, INFO TEXT, SKILL_SLOT TEXT, SR TEXT, BPM TEXT, HIT_LENGTH TEXT, MAX_COMBO TEXT, CS TEXT, AR TEXT, OD TEXT, MODS TEXT, NOTES TEXT, STATUS INT, COMMENTS TEXT, POOL TEXT, SUGGESTOR TEXT, RAW_MODS TEXT, ADD_TS REAL, PRIMARY KEY (BID, MODS));",
            ),
        )
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
    pg = st.navigation([pg_homepage])

if "immersive_active" not in st.session_state:
    st.session_state.immersive_active = False
if "immersive_toggled" not in st.session_state:
    st.session_state.immersive_toggled = False

with st.sidebar:

    def toggle_immersive():
        st.session_state.immersive_active = not st.session_state.immersive_active
        st.session_state.immersive_toggled = True

    st.button(_("Immersive Mode"), on_click=toggle_immersive, use_container_width=True, shortcut="F", icon="🖥️")
    # st.toggle(_("wide page layout"), key="wide_layout", value=False)

IMMERSIVE_CSS = """
<style>
    section[data-testid="stSidebar"] {
        display: none !important;
    }

    .stMainBlockContainer {
        padding-left: 1rem !important;
        padding-right: 1rem !important;
        padding-top: 1rem !important;
        max-width: 100% !important;
    }

    div.stAgGrid { 
        height: 100vh !important;
    }

    header[data-testid="stHeader"] {
        display: none !important;
    }
</style>
"""

# 根据状态注入 CSS
if st.session_state.immersive_active:
    st.markdown(IMMERSIVE_CSS, unsafe_allow_html=True)
    if st.session_state.immersive_toggled:
        st.toast(_("Press `F` to exit immersive mode."), icon="🖥️")
        st.session_state.immersive_toggled = False

pg.run()
