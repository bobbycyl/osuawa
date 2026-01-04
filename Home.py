from time import sleep
from uuid import UUID

import streamlit as st
from clayutil.cmdparse import (
    CommandError,
)
from streamlit import logger
from streamlit.components.v1 import html
from streamlit.errors import Error
from streamlit.runtime.scriptrunner import get_script_run_ctx

from osuawa import LANGUAGES
from osuawa.components import init_page, memorized_selectbox

init_page(_("Homepage") + " - osuawa")


def run(g):
    while True:
        try:
            st.write(next(g))
        except CommandError as e:
            st.error(e)
            break  # use continue if you want to continue running the generator
        except StopIteration as e:
            st.success(_("%d tasks done") % e.value)
            break
        except (Error, NotImplementedError) as e:
            logger.get_logger("streamlit").exception(e)
            # st.session_state.clear()
            break
        except Exception as e:
            st.exception(e)
            logger.get_logger("streamlit").exception(e)
            break


def submit():
    logger.get_logger(st.session_state.username).info(st.session_state["input"])
    run(st.session_state.cmdparser.parse_command(st.session_state["input"]))
    st.session_state["delete_line"] = True
    st.session_state["counter"] += 1


with st.sidebar:
    memorized_selectbox(":material/language: lang", "uni_lang", LANGUAGES, None)

with st.spinner(_("Preparing for the next operation...")):
    sleep(1.5)
if "delete_line" not in st.session_state:
    st.session_state["delete_line"] = True
if "counter" not in st.session_state:
    st.success(_("Welcome!"))
    st.session_state["counter"] = 0
if st.session_state["delete_line"]:
    st.session_state["input"] = ""
    st.session_state["delete_line"] = False

y = st.text_input("> ", key="input", on_change=submit, placeholder=_('Type "help" to get started.'))

html(
    f"""<script>
    var input = window.parent.document.querySelectorAll("input[type=text]");
    for (var i = 0; i < input.length; ++i) {{
        input[i].focus();
    }}
</script>
""",
    height=0,
)

if y:
    st.text(y)

#################################
### DEBUGGING COMPONENTS AREA ###
#################################
if st.session_state._debugging_mode:
    from osuawa.components import memorized_selectbox, memorized_multiselect

    memorized_selectbox("Memorized Selectbox Test", "test_memorized_selectbox", list("abcde"), "c")
    memorized_multiselect("Memorized Multiselect Test", "test_memorized_multiselect", list("abcde"), ["c", "e"])

st.text(_("Session: %s") % UUID(get_script_run_ctx().session_id).hex)
