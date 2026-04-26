import asyncio
import builtins
import gettext
import logging
import os
import pickle
from html import escape as html_escape
from threading import Lock
from time import time
from typing import Optional, TYPE_CHECKING, cast

import requests
import streamlit as st
from babel import Locale, UnknownLocaleError
from clayutil.cmdparse import (
    CommandParser,
)
from ossapi.ossapiv2_async import Domain, Scope
from streamlit import logger
from streamlit.runtime.scriptrunner import get_script_run_ctx

from osuawa import Awapi, C, LANGUAGES, Osuawa
from osuawa.components import delete_user_cache, get_session_id, load_value, register_commands, task_board, update_user_cache
from osuawa.utils import RedisTaskId, create_unique_picker, read_injected_code

st.session_state._debugging_mode = st.secrets.args.debugging_mode
admins = st.secrets.args.admins
if TYPE_CHECKING:

    def _(_text: str) -> str: ...


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


def init_logger_fh():
    fh = logging.FileHandler(os.path.join(C.LOGS.value, "streamlit.log"), encoding="utf-8")
    fh.setFormatter(logging.Formatter(st.get_option("logger.messageFormat")))

    def _init_logger_fh(_logger: logging.Logger):

        if hasattr(_logger, "streamlit_custom_file_handler"):
            _logger.removeHandler(cast(logging.Handler, _logger.streamlit_custom_file_handler))

        _logger.streamlit_custom_file_handler = fh  # type: ignore
        _logger.addHandler(_logger.streamlit_custom_file_handler)  # type: ignore

    _init_logger_fh(logger.get_logger("streamlit"))
    _init_logger_fh(logger.get_logger(st.session_state.username))


def register_awa(ci, cs, ru, sc, dm, oauth_token: Optional[str] = None, oauth_refresh_token: Optional[str] = None):
    # 在 session_state 中持久化事件循环
    if "async_loop" not in st.session_state:
        # 创建新的事件循环
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        st.session_state.async_loop = loop
    return Osuawa(st.session_state.async_loop, ci, cs, ru, sc, dm, st.context.cookies["ajs_anonymous_id"], oauth_token, oauth_refresh_token, debugging_mode=st.session_state._debugging_mode)


def toggle_immersive():
    st.session_state.immersive_active = not st.session_state.immersive_active
    st.session_state.immersive_toggled = True


if "lck" not in st.session_state:
    # 由于使用 ajs_anonymous_id 作为半持久化文件名，所以一般来说线程锁足以，不需要文件锁
    st.session_state.lck = Lock()
if "translate" not in st.session_state:
    load_value("uni_lang", convert_locale(st.context.locale))

# noinspection PyUnresolvedReferences
builtins.__dict__["_"] = gettext_translate
st.session_state.translate = gettext_getfunc(st.session_state._uni_lang_value)  # 想要绕过 load_value、save_value 就必须使用这种方式

pg_homepage = st.Page("Home.py", title=_("Homepage"))
pg_score_visualizer = st.Page("tools/Score_visualizer.py", title=_("Score Visualizer"))
pg_playlist_generator = st.Page("tools/Playlist_generator.py", title=_("Playlist Generator"))
pg_recorder = st.Page("tools/Recorder.py", title=_("Recorder"))
pg_room_spectator = st.Page("tools/Room_spectator.py", title=_("Room Spectator"))

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
        _("This loading bar moves slower than a 128-BPM song."),
        _("Tip: You can nod your head to the beat even if the loading bar is stuck."),
        _("I want a rhythm-pulsing progress bar like Lazer’s."),
        _("Pooling is a headache."),
        _("Loading Stellar Railway... Wait, I meant star rating."),
        _("My ACC is expanding and contracting with temperature."),
        _("Generating fake SS screenshots..."),
        _("Loading miss hitsound... 404 Not Found."),
        _("Why is your HP thicker than MMORPG bosses?"),
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
        _("The loading bar is actually of random length, stop staring at it."),
        _("If I told you it were 99%% loaded, would you believe me?") % (),
        _("Analyzing your play history... it seems you like Tech maps?"),
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

    # 由于刷新 token 的任务已经完全交由 daemon 处理，所以这里只需要读取 token 文件即可
    try:
        if "code" not in st.query_params:
            # check if oauth token is pickled
            if "ajs_anonymous_id" in st.context.cookies and os.path.exists(os.path.join(C.OAUTH_TOKEN_DIRECTORY.value, "%s.pickle" % st.context.cookies["ajs_anonymous_id"])):
                with open(os.path.join(C.OAUTH_TOKEN_DIRECTORY.value, "%s.pickle" % st.context.cookies["ajs_anonymous_id"]), "rb") as fi_b:
                    _oauth_token = pickle.load(fi_b)
                with open(os.path.join(C.OAUTH_TOKEN_DIRECTORY.value, "refresh", "%s.pickle" % st.context.cookies["ajs_anonymous_id"]), "rb") as fi_b:
                    _refresh_token = pickle.load(fi_b)
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
            ).json()
            if _oauth_r.get("error"):
                raise NotImplementedError(_oauth_r.get("error_description"))
            st.query_params.pop("code")
            _oauth_token = _oauth_r.get("access_token")
            _refresh_token = _oauth_r.get("refresh_token")
            prepare_bar.progress(67, text=get_an_osu_meme())

        awa = register_awa(client_id, client_secret, redirect_url, scopes, domain, _oauth_token, _refresh_token)
        # set variables
        awa.tz = st.context.timezone
        st.session_state.awa = awa
        st.session_state.user, st.session_state.username = st.session_state.awa.user
        # save token
        with open(os.path.join(C.OAUTH_TOKEN_DIRECTORY.value, "%s.pickle" % st.context.cookies["ajs_anonymous_id"]), "wb") as fi_b:
            pickle.dump(_oauth_token, fi_b)
        with open(os.path.join(C.OAUTH_TOKEN_DIRECTORY.value, "refresh", "%s.pickle" % st.context.cookies["ajs_anonymous_id"]), "wb") as fi_b:
            pickle.dump(_refresh_token, fi_b)
    except Exception as e:
        # 由于自动刷新功能由 daemon 承担，这里理论上不会触发
        if not st.session_state._debugging_mode:
            delete_user_cache(st.context.cookies["ajs_anonymous_id"])
        st.error(str(e))
        prepare_bar.empty()
        if "code" in st.query_params:
            st.query_params.pop("code")
        st.stop()
    update_user_cache(st.session_state.user, st.session_state.username, st.context.cookies["ajs_anonymous_id"], time())

    if st.session_state._debugging_mode:
        from random import randint

        # 启用随机用户名
        st.session_state.username = "".join([chr(randint(ord("a"), ord("z"))) for _i in range(8)])
        ctx = get_script_run_ctx()
        if ctx is None:
            raise RuntimeError("no streamlit runtime")
        logger.get_logger("streamlit").info("renamed %s to %s at session %s" % (st.session_state.awa.user[1], st.session_state.username, get_session_id()))

    prepare_bar.progress(100, text=get_an_osu_meme())
    if st.session_state.user in admins:
        st.session_state.token = ""
        register_commands({"token": ""})
        st.session_state.perm = 4
    prepare_bar.empty()

if "fh_init" not in st.session_state:
    st.session_state.fh_init = True
    init_logger_fh()
register_commands({"simple": True})
pg_list = [pg_homepage, pg_score_visualizer, pg_playlist_generator, pg_recorder, pg_room_spectator]
if st.session_state.perm >= 2:
    pg_list.append(st.Page("tools/Easter_egg.py", title=_("Easter Egg")))
pg = st.navigation(pg_list)

if "immersive_active" not in st.session_state:
    st.session_state.immersive_active = False
if "immersive_toggled" not in st.session_state:
    st.session_state.immersive_toggled = False

IMMERSIVE_CSS = read_injected_code("immersive.css")

# 根据状态注入 CSS
if st.session_state.immersive_active:
    st.markdown(IMMERSIVE_CSS, unsafe_allow_html=True)
    if st.session_state.immersive_toggled:
        st.toast(_("Press `F` to exit immersive mode."), icon=":material/collapse_content:")
        st.session_state.immersive_toggled = False

# todo: 验证是否需要这个功能
if "basic_interaction_enabled" not in st.session_state or st.session_state.get("require_basic_interaction", False):
    st.session_state.basic_interaction_enabled = True

with st.sidebar:
    if not st.session_state.immersive_active:
        st.button(_("Immersive Mode"), on_click=toggle_immersive, use_container_width=True, shortcut="F", icon=":material/expand_content:", disabled=not st.session_state.basic_interaction_enabled)
    # st.toggle(_("wide page layout"), key="wide_layout", value=False)
    if st.button(_("Task Board"), use_container_width=True, icon=":material/assignment:", disabled=not st.session_state.basic_interaction_enabled):
        st.dialog(_("Task Board"), width="large")(task_board)()
if st.session_state.immersive_active:
    with st.container(gap="xxsmall"):
        st.button(_("Exit Immersive Mode"), on_click=toggle_immersive, type="tertiary", shortcut="F", icon=":material/collapse_content:", disabled=not st.session_state.basic_interaction_enabled)
# _page_manager = get_script_run_ctx().pages_manager
# _current_page_script_hash = _page_manager.current_page_script_hash
# _url_path = _page_manager.get_pages().get(_current_page_script_hash, None).get("url_pathname", "")
# print(_url_path)
pg.run()
