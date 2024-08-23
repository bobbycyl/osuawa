import os.path
from uuid import UUID

import pandas as pd
import streamlit as st
from clayutil.futil import compress_as_zip
from streamlit.runtime.scriptrunner import get_script_run_ctx

from osuawa import OsuPlaylist, Osuawa

st.set_page_config(page_title=_("Playlist generator") + " - osuawa")

if not os.path.exists("./static/uploaded"):
    os.makedirs("./static/uploaded")


@st.cache_data
def convert_df(df: pd.DataFrame, filename: str):
    df.to_csv(filename, encoding="utf-8")


@st.cache_data
def generate_playlist(playlist_filename: str):
    st.session_state.awa: Osuawa
    playlist = OsuPlaylist(st.session_state.awa.client, playlist_filename)
    return playlist.generate()


uploaded_file = st.file_uploader(_("Choose a file"), type=["properties"], key="gen_uploaded_file")
if uploaded_file is None:
    st.error(_("Please upload a file first."))
else:
    playlist_name = os.path.splitext(uploaded_file.name)[0]
    uid = UUID(get_script_run_ctx().session_id).hex
    session_path = "./static/uploaded/%s" % uid
    if not os.path.exists(session_path):
        os.mkdir(session_path)
    playlist_filename = "./static/uploaded/%s/%s.properties" % (uid, playlist_name)
    html_filename = "./static/uploaded/%s/%s.html" % (uid, playlist_name)
    covers_dir = "./static/uploaded/%s/%s.covers" % (uid, playlist_name)
    csv_filename = "./static/uploaded/%s/%s.csv" % (uid, playlist_name)
    css_filename = "./static/uploaded/%s/%s.css" % (uid, playlist_name)
    zip_filename = "./static/uploaded/%s.zip" % uid
    st.write(_("using filename: %s") % playlist_filename.replace("./static/", "app/static/"))
    content = uploaded_file.getvalue()

    table = pd.DataFrame()
    with open(playlist_filename, "wb") as fo:
        fo.write(content)
    try:
        table = generate_playlist(playlist_filename)
    except Exception as e:
        st.error(e)
    else:
        st.divider()
        for pic in [x[0] for x in sorted([(x, int(x[: x.find("-")])) for x in os.listdir(covers_dir)], key=lambda x: x[1])]:
            st.image(os.path.join(covers_dir, pic), caption=pic, use_column_width=True)
        convert_df(table, csv_filename)
        with open(css_filename, "w") as fo:
            fo.write(
                """body {
  background-color: #1f1f1f;
}

.pd {
  border-collapse: collapse;
  border: #1c1c1c;
  color: white;
  font-family: monospace;
}

.pd td,
th {
  padding: 5px;
}

.pd tr:hover {
  background: #303030;
  /* font-weight: bold; */
}
                """
            )
        st.dataframe(table, hide_index=True)
        compress_as_zip(session_path, zip_filename)
        with open(zip_filename, "rb") as zipfi:
            st.download_button(label=_("Download the resources"), file_name="%s.zip" % uid, data=zipfi)
