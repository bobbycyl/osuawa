import importlib
import os

osu_tools_home = os.path.abspath(os.path.join(str(os.path.dirname(__file__)), "..", "osu-tools"))
if os.path.exists(osu_tools_home) and os.path.isdir(osu_tools_home):
    os.environ["OSU_TOOLS_HOME"] = osu_tools_home

importlib.import_module("osupp")

from .osuawa import Awapi as Awapi, C as C, OsuPlaylist as OsuPlaylist, Osuawa as Osuawa
from .utils import LANGUAGES as LANGUAGES
