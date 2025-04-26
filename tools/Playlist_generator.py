import os.path
import shutil
from typing import Optional
from uuid import UUID

import pandas as pd
import streamlit as st
from clayutil.futil import compress_as_zip
from streamlit.runtime.scriptrunner import get_script_run_ctx

from osuawa import OsuPlaylist, Path

st.set_page_config(page_title=_("Playlist generator") + " - osuawa")
with st.sidebar:
    st.toggle(_("new style"), key="new_style", value=True)


@st.cache_data(show_spinner=False)
def convert_df(df: pd.DataFrame, filename: str):
    df.to_csv(filename, encoding="utf-8")


@st.cache_data(show_spinner=False)
def generate_playlist(filename: str, css_style: Optional[int] = None):
    playlist = OsuPlaylist(st.session_state.awa, filename, css_style=css_style)
    return playlist.generate()


uploaded_file = st.file_uploader(_("Choose a file"), type=["properties"], key="gen_uploaded_file")
if uploaded_file is None:
    st.error(_("Please upload a file first."))
else:
    playlist_name = os.path.splitext(uploaded_file.name)[0]
    uid = UUID(get_script_run_ctx().session_id).hex
    session_path = os.path.join(Path.UPLOADED_DIRECTORY.value, uid)
    if not os.path.exists(session_path):
        os.mkdir(session_path)
    playlist_filename = str(os.path.join(Path.UPLOADED_DIRECTORY.value, uid, "%s.properties" % playlist_name))
    html_filename = str(os.path.join(Path.UPLOADED_DIRECTORY.value, uid, "%s.html" % playlist_name))
    covers_dir = str(os.path.join(Path.UPLOADED_DIRECTORY.value, uid, "%s.covers" % playlist_name))
    csv_filename = str(os.path.join(Path.UPLOADED_DIRECTORY.value, uid, "%s.csv" % playlist_name))
    css_filename = str(os.path.join(Path.UPLOADED_DIRECTORY.value, uid, "style.css"))
    zip_filename = str(os.path.join(Path.UPLOADED_DIRECTORY.value, "%s.zip" % uid))
    content = uploaded_file.getvalue()

    with open(playlist_filename, "wb") as fo:
        fo.write(content)
    if st.session_state["new_style"]:
        table = generate_playlist(playlist_filename, 1)
    else:
        table = generate_playlist(playlist_filename)
        for pic in [x[0] for x in sorted([(x, int(x[: x.find("-")])) for x in os.listdir(covers_dir)], key=lambda x: x[1])]:
            st.image(os.path.join(covers_dir, pic), caption=pic, use_container_width=True)
    st.divider()
    convert_df(table, csv_filename)
    if os.path.exists("./playlists/style.css"):
        shutil.copy("./playlists/style.css", css_filename)
    st.dataframe(table, hide_index=True)
    compress_as_zip(session_path, zip_filename)
    with open(zip_filename, "rb") as zfi:
        st.download_button(label=_("Download the resources"), file_name="%s.zip" % uid, data=zfi)
