import asyncio
import builtins
import gettext
import logging
import os
from html import escape as html_escape
from typing import Optional, TYPE_CHECKING
from uuid import UUID

import requests
import streamlit as st
from babel import Locale
from clayutil.cmdparse import (
    CommandParser,
)
from ossapi import Domain, Scope
from sqlalchemy import text
from streamlit import logger
from streamlit.runtime.scriptrunner import get_script_run_ctx

from osuawa import Awapi, C, LANGUAGES, Osuawa
from osuawa.components import load_value, register_commands
from osuawa.utils import get_an_osu_meme

st.session_state._debugging_mode = False
admins = st.secrets.args.admins
if TYPE_CHECKING:

    def _(text: str) -> str: ...


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


@st.cache_data
def init_logger():
    fh = logging.FileHandler("./logs/streamlit.log", encoding="utf-8")
    fh.setFormatter(logging.Formatter("[%(asctime)s] [%(name)s/%(levelname)s]: %(message)s"))
    if "streamlit" not in logger.get_logger("streamlit").handlers:
        logger.get_logger("streamlit").addHandler(fh)
    if st.session_state.username not in logger.get_logger("streamlit").handlers:
        logger.get_logger(st.session_state.username).addHandler(fh)


def register_awa(ci, cs, ru, sc, dm, oauth_token: Optional[str] = None, oauth_refresh_token: Optional[str] = None):
    # 在 session_state 中持久化事件循环
    if "async_loop" not in st.session_state:
        # 创建新的事件循环
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        st.session_state.async_loop = loop
    return Osuawa(st.session_state.async_loop, ci, cs, ru, sc, dm, st.context.cookies["ajs_anonymous_id"], oauth_token, oauth_refresh_token)


def toggle_immersive():
    st.session_state.immersive_active = not st.session_state.immersive_active
    st.session_state.immersive_toggled = True


if "translate" not in st.session_state:
    if not os.path.exists(C.LOGS.value):
        os.mkdir(C.LOGS.value)
    if not os.path.exists(C.OUTPUT_DIRECTORY.value):
        os.mkdir(C.OUTPUT_DIRECTORY.value)
    if not os.path.exists(C.STATIC_DIRECTORY.value):
        os.mkdir(C.STATIC_DIRECTORY.value)
    if not os.path.exists(C.UPLOADED_DIRECTORY.value):
        os.mkdir(C.UPLOADED_DIRECTORY.value)
    if not os.path.exists(C.BEATMAPS_CACHE_DIRECTORY.value):
        os.mkdir(C.BEATMAPS_CACHE_DIRECTORY.value)
    load_value("uni_lang", convert_locale(st.context.locale))
    # 半持久化保存
    if not os.path.exists("./.streamlit/.oauth"):
        os.mkdir("./.streamlit/.oauth")
    if not os.path.exists("./.streamlit/.components"):
        os.mkdir("./.streamlit/.components")
    # 数据库需要以下表和字段
    # 1. 表 BEATMAP，字段固定为 BID, SID, INFO, SKILL_SLOT, SR, BPM, HIT_LENGTH, MAX_COMBO, CS, AR, OD, MODS, NOTES, STATUS, COMMENTS, POOL, SUGGESTOR, RAW_MODS, ADD_TS, U_ARTIST, U_TITLE （一个经过修改的课题字段，后续可以复用生成课题的代码，逻辑是一样的），使用 BID + MODS 作为主键
    # 2. 表 SCORE，字段与 CompletedSimpleScoreInfo 大体一致，另附加 SCORE_ID 字段作为主键
    conn = st.connection("osuawa", type="sql", ttl=0)
    with conn.session as s:
        s.execute(
            text(
                "CREATE TABLE IF NOT EXISTS BEATMAP(BID BIGINT, SID BIGINT, INFO TEXT, SKILL_SLOT TEXT, SR TEXT, BPM TEXT, HIT_LENGTH TEXT, MAX_COMBO TEXT, CS TEXT, AR TEXT, OD TEXT, MODS TEXT, NOTES TEXT, STATUS INT, COMMENTS TEXT, POOL TEXT, SUGGESTOR TEXT, RAW_MODS TEXT, ADD_TS REAL, U_ARTIST TEXT, U_TITLE TEXT, PRIMARY KEY (BID, MODS));",
            ),
        )
        s.execute(
            text(
                "CREATE TABLE IF NOT EXISTS SCORE(SCORE_ID BIGINT, BID BIGINT, USER_ID BIGINT, SCORE INT, ACCURACY REAL, MAX_COMBO INT, PASSED INT, PP REAL, MODS TEXT, TS REAL, STATISTICS TEXT, ST REAL, \
                 CS REAL, HIT_WINDOW REAL, PREEMPT REAL, BPM REAL, HIT_LENGTH INT, IS_NF INT, IS_HD INT, IS_HIGH_AR INT, IS_LOW_AR INT, IS_VERY_LOW_AR INT, IS_SPEED_UP INT, IS_SPEED_DOWN INT, INFO TEXT, ORIGINAL_DIFFICULTY REAL, B_STAR_RATING REAL, B_MAX_COMBO INT, B_AIM_DIFFICULTY REAL, B_AIM_DIFFICULT_SLIDER_COUNT REAL, B_SPEED_DIFFICULTY REAL, B_SPEED_NOTE_COUNT REAL, B_SLIDER_FACTOR REAL, B_AIM_TOP_WEIGHTED_SLIDER_FACTOR REAL, B_SPEED_TOP_WEIGHTED_SLIDER_FACTOR REAL, B_AIM_DIFFICULT_STRAIN_COUNT REAL, B_SPEED_DIFFICULT_STRAIN_COUNT REAL, PP_AIM REAL, PP_SPEED REAL, PP_ACCURACY REAL, B_PP_100IF_AIM REAL, B_PP_100IF_SPEED REAL, B_PP_100IF_ACCURACY REAL, B_PP_100IF REAL, B_PP_92IF REAL, B_PP_81IF REAL, B_PP_67IF REAL, PRIMARY KEY (SCORE_ID));",
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

if "cmdparser" not in st.session_state:
    st.session_state.cmdparser = CommandParser()

if "awa" not in st.session_state:
    prepare_bar = st.progress(0, text=_("Loading necessary objects..."))
    client_id = st.secrets.args.client_id
    client_secret = st.secrets.args.client_secret
    redirect_url = st.secrets.args.redirect_url
    scopes = [Scope.PUBLIC.value, Scope.IDENTIFY.value, Scope.FRIENDS_READ.value]
    domain = Domain.OSU.value
    prepare_bar.progress(35, text=get_an_osu_meme())
    try:
        if "code" not in st.query_params:
            # check if ossapi token is pickled
            if "ajs_anonymous_id" in st.context.cookies and os.path.exists("./.streamlit/.oauth/%s.pickle" % st.context.cookies["ajs_anonymous_id"]):
                awa = register_awa(client_id, client_secret, redirect_url, scopes, domain)
                prepare_bar.progress(67, text=get_an_osu_meme())
            else:
                st.info(_("Please click the button below to authorize the app."))
                st.link_button(_("OAuth2 URL"), "%s?client_id=%s&redirect_uri=%s&response_type=code&scope=%s" % (Awapi.AUTH_CODE_URL.format(domain=domain), html_escape(str(client_id)), html_escape(redirect_url), "+".join(scopes)), icon=":material/login:")
                prepare_bar.empty()
                st.stop()
        else:
            code = st.query_params.code
            prepare_bar.progress(50, text=get_an_osu_meme())
            r = requests.post(
                Awapi.TOKEN_URL.format(domain=domain),
                headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
                data={"client_id": client_id, "client_secret": client_secret, "code": code, "grant_type": "authorization_code", "redirect_uri": redirect_url},
            )
            awa = register_awa(client_id, client_secret, redirect_url, scopes, domain, r.json().get("access_token"), r.json().get("refresh_token"))
            awa.api._save_token(awa.api.session.token)
            st.query_params.pop("code")
            prepare_bar.progress(67, text=get_an_osu_meme())
        awa.tz = st.context.timezone
        st.session_state.awa = awa
        st.session_state.user, st.session_state.username = st.session_state.awa.user
        if st.session_state._debugging_mode:
            from random import randint

            # 启用随机用户名
            st.session_state.username = "".join([chr(randint(ord("a"), ord("z"))) for _ in range(8)])
            logger.get_logger("streamlit").info("renamed %s to %s at session %s" % (st.session_state.awa.user[1], st.session_state.username, UUID(get_script_run_ctx().session_id).hex))
    except NotImplementedError:
        # 这一般是 token 过期了
        if os.path.exists("./.streamlit/.oauth/%s.pickle" % st.context.cookies["ajs_anonymous_id"]):
            os.remove("./.streamlit/.oauth/%s.pickle" % st.context.cookies["ajs_anonymous_id"])
        # st.warning(_("OAuth2 token or code has expired. Please remove the url parameter and refresh the page."))
        prepare_bar.empty()
        # 清除 query
        st.query_params.clear()
        st.rerun()
    prepare_bar.progress(100, text=get_an_osu_meme())
    if st.session_state.user in admins:
        st.session_state.token = ""
        register_commands({"token": ""})
        st.session_state.perm = 4
    prepare_bar.empty()

if st.session_state.perm < 4:
    pg = st.navigation([pg_homepage, pg_score_visualizer, pg_playlist_generator, pg_recorder])
else:
    pg = st.navigation([pg_homepage, pg_score_visualizer, pg_playlist_generator, pg_recorder, st.Page("tools/Easter_egg.py")])
init_logger()
register_commands({"simple": True})

if "immersive_active" not in st.session_state:
    st.session_state.immersive_active = False
if "immersive_toggled" not in st.session_state:
    st.session_state.immersive_toggled = False

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
        st.toast(_("Press `F` to exit immersive mode."), icon=":material/collapse_content:")
        st.session_state.immersive_toggled = False

with st.sidebar:
    st.button(_("Immersive Mode"), on_click=toggle_immersive, use_container_width=True, shortcut="F", icon=":material/expand_content:")
    # st.toggle(_("wide page layout"), key="wide_layout", value=False)

# _page_manager = get_script_run_ctx().pages_manager
# _current_page_script_hash = _page_manager.current_page_script_hash
# _url_path = _page_manager.get_pages().get(_current_page_script_hash, None).get("url_pathname", "")
# print(_url_path)
pg.run()
