import os.path
from uuid import UUID

import pandas as pd
import streamlit as st
from clayutil.futil import compress_as_zip
from streamlit.runtime.scriptrunner import get_script_run_ctx

from osuawa import OsuPlaylist, Path

st.set_page_config(page_title=_("Playlist generator") + " - osuawa")


@st.cache_data(show_spinner=False)
def convert_df(df: pd.DataFrame, filename: str):
    df.to_csv(filename, encoding="utf-8")


@st.cache_data(show_spinner=False)
def generate_playlist(playlist_filename: str):
    playlist = OsuPlaylist(st.session_state.awa, playlist_filename)
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
    table = generate_playlist(playlist_filename)
    st.divider()
    for pic in [x[0] for x in sorted([(x, int(x[: x.find("-")])) for x in os.listdir(covers_dir)], key=lambda x: x[1])]:
        st.image(os.path.join(covers_dir, pic), caption=pic, use_container_width=True)
    convert_df(table, csv_filename)
    with open(css_filename, "w") as fo:
        fo.write(
            """.footer {
  border-top: 3px solid transparent;
  border-width: 3px 0px 0px;
  border-image: linear-gradient(
      90deg,
      rgb(66, 144, 251) 7%,
      rgb(79, 192, 255) 7%,
      rgb(79, 192, 255) 19.5%,
      rgb(79, 255, 213) 19.5%,
      rgb(79, 255, 213) 28%,
      rgb(124, 255, 79) 28%,
      rgb(124, 255, 79) 34%,
      rgb(246, 240, 92) 34%,
      rgb(246, 240, 92) 43%,
      rgb(255, 128, 104) 43%,
      rgb(255, 128, 104) 53%,
      rgb(255, 78, 111) 53%,
      rgb(255, 78, 111) 61%,
      rgb(198, 69, 184) 61%,
      rgb(198, 69, 184) 71%,
      rgb(101, 99, 222) 71%,
      rgb(101, 99, 222) 82%,
      rgb(24, 21, 142) 82%,
      rgb(24, 21, 142) 92%,
      rgb(0, 0, 0) 92%,
      rgb(0, 0, 0) 100%
    )
    1 / 1 / 0 stretch;
  bottom: 0;
  width: 100%;
  height: 50px;
  text-align: center;
  padding: 10px;
}

.pd {
  font-size: small;
  text-align: center;
  border-collapse: collapse;
}

.pd th {
  padding: 5px;
}

.pd td {
  padding: 1px 5px 1px;
}

/* .pd tr:hover {
  font-weight: bold;
} */

@media (prefers-color-scheme: dark) {
  body {
    color: white;
    background-color: #1f1f1f;
  }

  .pd {
    border: 1px solid #1c1c1c;
  }

  .pd tbody tr:hover {
    background: #303030;
  }
}

@media (prefers-color-scheme: light) {
  body {
    color: rgb(16, 40, 40);
    background-color: rgb(245, 255, 255);
  }

  .pd {
    border: 1px solid rgb(185, 200, 200);
  }

  .pd thead {
    background-color: rgb(127, 185, 185);
  }

  .pd tbody {
    background-color: rgb(235, 250, 250);
  }

  .pd tbody tr:hover {
    background: rgb(160, 200, 200);
  }
}
            """
        )
    st.dataframe(table, hide_index=True)
    compress_as_zip(session_path, zip_filename)
    with open(zip_filename, "rb") as zipfi:
        st.download_button(label=_("Download the resources"), file_name="%s.zip" % uid, data=zipfi)
