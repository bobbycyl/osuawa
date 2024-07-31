import os.path
from uuid import UUID

import streamlit as st
from clayutil.futil import compress_as_zip
from streamlit.runtime.scriptrunner import get_script_run_ctx

from osuawa import OsuPlaylist, Osuawa

if not os.path.exists("./static/uploaded"):
    os.makedirs("./static/uploaded")
if "content" not in st.session_state:
    st.session_state.content = b""

st.write("1. **Enter your client credential.** ([get one](https://osu.ppy.sh/home/account/edit))")
client_id = st.text_input("Client ID", key="gen_client_id")
if not client_id.isdigit():
    st.error("Client ID must be an integer")
client_secret = st.text_input("Client Secret", key="gen_client_secret")
st.divider()
st.write("2. **Upload a playlist source file.**")
uploaded_file = st.file_uploader("Choose a file", type=["properties"])
if uploaded_file is None:
    st.error("Please upload a file first.")
elif not client_id or not client_secret:
    st.error("Please enter your client ID and secret first.")
else:
    playlist_name = os.path.splitext(uploaded_file.name)[0]
    uid = UUID(get_script_run_ctx().session_id).hex
    session_path = "./static/uploaded/%s" % uid
    if not os.path.exists(session_path):
        os.mkdir(session_path)
    playlist_filename = "./static/uploaded/%s/%s.properties" % (uid, playlist_name)
    html_filename = "./static/uploaded/%s/%s.html" % (uid, playlist_name)
    covers_dir = "./static/uploaded/%s/%s.covers" % (uid, playlist_name)
    zip_filename = "./static/uploaded/%s.zip" % uid
    st.write("using filename: %s" % playlist_filename)
    content = uploaded_file.getvalue()

    if st.session_state.content != content:
        st.session_state.content = content
        with open(playlist_filename, "wb") as fo:
            fo.write(st.session_state.content)
        client = Osuawa.create_client_credential_grant_client(int(client_id), client_secret)
        st.session_state.table = OsuPlaylist(client, playlist_filename).generate()
    st.divider()
    st.write("3. **Preview and download the generated playlist.**")
    for pic in [x[0] for x in sorted([(x, int(x[:x.find("-")])) for x in os.listdir(covers_dir)], key=lambda x: x[1])]:
        st.image(os.path.join(covers_dir, pic), caption=pic, use_column_width=True)
    st.dataframe(st.session_state.table, hide_index=True)
    compress_as_zip(session_path, zip_filename)
    with open(zip_filename, "rb") as zipfi:
        st.download_button(label="Download the resources", data=zipfi, file_name="%s.zip" % playlist_name)
