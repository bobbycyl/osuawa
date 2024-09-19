import multiprocessing

import streamlit
from streamlit.web import bootstrap

if __name__ == "__main__":
    multiprocessing.freeze_support()
    streamlit._is_running_with_streamlit = True
    bootstrap.run("app.py", False, [], {})
