import asyncio
import builtins
import gettext
import logging
import os
from html import escape as html_escape
from typing import Optional, TYPE_CHECKING

import requests
import streamlit as st
from babel import Locale, UnknownLocaleError
from clayutil.cmdparse import (
    CommandParser,
)
from ossapi import Domain, Scope
from streamlit import logger
from streamlit.runtime.scriptrunner import get_script_run_ctx

from osuawa import Awapi, C, LANGUAGES, Osuawa
from osuawa.components import get_session_id, load_value, register_commands, task_board
from osuawa.utils import RedisTaskId, create_unique_picker, update_user_cache

st.session_state._debugging_mode = st.secrets.args.debugging_mode
admins = st.secrets.args.admins
if TYPE_CHECKING:

    def _(text: str) -> str: ...


def convert_locale(accept_language: Optional[str]):
    if accept_language is None:
        return "en_US"
    try:
        parsed_locale = Locale.parse(accept_language.split(",")[0], sep="-")
        converted_lang = "%s_%s" % (parsed_locale.language, parsed_locale.territory)
        if converted_lang not in LANGUAGES:
            return "en_US"
        else:
            return converted_lang
    except (UnknownLocaleError, ValueError, AttributeError, IndexError):
        return "en_US"


def gettext_getfunc(lang):
    def translate(text):
        return gettext.translation("messages", localedir=C.LOCALE.value, languages=[lang], fallback=True).gettext(text)

    return translate


def gettext_translate(text):
    return st.session_state.translate(text)


@st.cache_data
def init_logger():
    fh = logging.FileHandler(os.path.join(C.LOGS.value, "streamlit.log"), encoding="utf-8")
    fh.setFormatter(logging.Formatter(st.get_option("logger.messageFormat")))
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
    load_value("uni_lang", convert_locale(st.context.locale))

# noinspection PyUnresolvedReferences
builtins.__dict__["_"] = gettext_translate
st.session_state.translate = gettext_getfunc(st.session_state._uni_lang_value)  # 想要绕过 load_value、save_value 就必须使用这种方式

pg_homepage = st.Page("Home.py", title=_("Homepage"))
pg_score_visualizer = st.Page("tools/Score_visualizer.py", title=_("Score visualizer"))
pg_playlist_generator = st.Page("tools/Playlist_generator.py", title=_("Playlist generator"))
pg_recorder = st.Page("tools/Recorder.py", title=_("Recorder"))

load_value("redis_tasks", [])
# noinspection PyTypeHints
st.session_state.redis_tasks: list[RedisTaskId]
if "cmdparser" not in st.session_state:
    st.session_state.cmdparser = CommandParser()

if "awa" not in st.session_state:
    memes: list[str] = [
        _("Loading... Keep your cursor steady."),
        _("PP has gone."),
        _("Attempting to parse a 400pp jump map..."),
        _("Who moved my mouse sensitivity?"),
        _("Don’t take it too seriously, this is just a toy."),
        _("Re-timing the map... No wait, it’s perfectly aligned this time!"),
        _("I've got a slider break!"),
        _("Shh, don’t tell anyone what this tool is built with."),
        _("Calculating how long your wrist can last."),
        _("This loading bar moves slower than a 128 BPM song."),
        _("Tip: You can nod your head to the beat even if the loading bar is stuck."),
        _("I want a rhythm-pulsing progress bar like Lazer’s."),
        _("Pooling is a headache."),
        _("Loading Stellar Railway... Wait, I meant star rating."),
        _("My ACC is expanding and contracting with temperature."),
        _("Generating fake SS screenshots..."),
        _("Loading miss hit sound... 404 Not Found."),
        _("How is your HP thicker than MMORPG bosses?"),
        _("I'm not a fan of DT."),
        _("Calculating how much patience you need..."),
        _('Loading "my hand slipped" excuse generator...'),
        _('Generating fake "this is my first time playing" claims...'),
        _("Loading C#, Rust, JavaScript and so on..."),
        _("Calculating how much time you have wasted..."),
        _("Sleeping..."),
        _("Refactoring spaghetti code? No, just piling it up."),
        _("If you see this tip for more than 5 seconds, the thread is probably dead."),
        _("There are no bugs, only undocumented features."),
        _("The loading bar is actually random length, stop staring at it."),
        _("If I told you it’s 99%% loaded, would you believe me?") % (),
        _("Analyzing your play history... seems you like Tech maps?"),
        _("Stop looking at the Accuracy, enjoy the music!"),
        _("Loading... (This tip is also part of the loading process)"),
    ]
    get_an_osu_meme = create_unique_picker(memes)
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
            if "ajs_anonymous_id" in st.context.cookies and os.path.exists(os.path.join(C.OAUTH_TOKEN_DIRECTORY.value, "%s.pickle" % st.context.cookies["ajs_anonymous_id"])):
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
            _oauth_r = requests.post(
                Awapi.TOKEN_URL.format(domain=domain),
                headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
                data={"client_id": client_id, "client_secret": client_secret, "code": code, "grant_type": "authorization_code", "redirect_uri": redirect_url},
            )
            awa = register_awa(client_id, client_secret, redirect_url, scopes, domain, _oauth_r.json().get("access_token"), _oauth_r.json().get("refresh_token"))
            awa.api._save_token(awa.api.session.token)
            st.query_params.pop("code")
            prepare_bar.progress(67, text=get_an_osu_meme())
        awa.tz = st.context.timezone
        st.session_state.awa = awa
        st.session_state.user, st.session_state.username = st.session_state.awa.user
        update_user_cache(st.session_state.user, st.session_state.username, [st.context.cookies["ajs_anonymous_id"]])
        if st.session_state._debugging_mode:
            from random import randint

            # 启用随机用户名
            st.session_state.username = "".join([chr(randint(ord("a"), ord("z"))) for _ in range(8)])
            ctx = get_script_run_ctx()
            if ctx is None:
                raise RuntimeError("no streamlit runtime")
            logger.get_logger("streamlit").info("renamed %s to %s at session %s" % (st.session_state.awa.user[1], st.session_state.username, get_session_id()))
    except NotImplementedError:
        # 这一般是 token 过期了
        if os.path.exists(os.path.join(C.OAUTH_TOKEN_DIRECTORY.value, "%s.pickle" % st.context.cookies["ajs_anonymous_id"])) and not st.session_state._debugging_mode:
            os.remove(os.path.join(C.OAUTH_TOKEN_DIRECTORY.value, "%s.pickle" % st.context.cookies["ajs_anonymous_id"]))
        # todo: 之前因为某些奇怪的原因把自动刷新去掉了，为了优化用户体验，以后可能还需要考虑如何加入自动刷新功能
        st.warning(_("OAuth2 token or code has expired. Please refresh the page."))
        prepare_bar.empty()
        if "code" in st.query_params:
            st.query_params.pop("code")
        st.stop()
    prepare_bar.progress(100, text=get_an_osu_meme())
    if st.session_state.user in admins:
        st.session_state.token = ""
        register_commands({"token": ""})
        st.session_state.perm = 4
    prepare_bar.empty()

pg = st.navigation([pg_homepage, pg_score_visualizer, pg_playlist_generator, pg_recorder]) if st.session_state.perm < 2 else st.navigation([pg_homepage, pg_score_visualizer, pg_playlist_generator, pg_recorder, st.Page("tools/Easter_egg.py")])
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
    if st.button(_("Tasks Board"), use_container_width=True, icon=":material/assignment:"):
        st.dialog(_("Tasks Board"), width="large")(task_board)()
# _page_manager = get_script_run_ctx().pages_manager
# _current_page_script_hash = _page_manager.current_page_script_hash
# _url_path = _page_manager.get_pages().get(_current_page_script_hash, None).get("url_pathname", "")
# print(_url_path)
pg.run()
