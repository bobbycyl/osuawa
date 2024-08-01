import streamlit
from streamlit.web import bootstrap

if __name__ == '__main__':
    streamlit._is_running_with_streamlit = True
    bootstrap.run('app.py', False, [], {})
