import argparse
import gettext
import locale
import logging
import os.path

import streamlit as st
from clayutil.cmdparse import (
    CommandParser,
)
from streamlit import logger, runtime
from streamlit.runtime.scriptrunner import get_script_run_ctx

from osuawa import Path

DEBUG_MODE = True  # switch to False when deploying

if not os.path.exists(Path.LOGS.value):
    os.mkdir(Path.LOGS.value)
if not os.path.exists(Path.OUTPUT_DIRECTORY.value):
    os.mkdir(Path.OUTPUT_DIRECTORY.value)
    os.mkdir(os.path.join(Path.OUTPUT_DIRECTORY.value, Path.RAW_RECENT_SCORES.value))
    os.mkdir(os.path.join(Path.OUTPUT_DIRECTORY.value, Path.RECENT_SCORES.value))

arg_parser = argparse.ArgumentParser(description="osuawa")
arg_parser.add_argument("oauth_filename")
arg_parser.add_argument("osu_tools_path")
st.session_state.args = arg_parser.parse_args()
if "cmdparser" not in st.session_state:
    st.session_state.cmdparser = CommandParser()

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


@st.cache_data
def init_logger():
    fh = logging.FileHandler("./logs/streamlit.log", encoding="utf-8")
    fh.setFormatter(logging.Formatter("[%(asctime)s] [%(name)s/%(levelname)s]: %(message)s"))
    logger.get_logger("streamlit").addHandler(fh)
    logger.get_logger(runtime.get_instance().get_client(get_script_run_ctx().session_id).request.remote_ip).addHandler(fh)


init_logger()
st.session_state.DEBUG_MODE = DEBUG_MODE
pg.run()
