import multiprocessing
import os
import shutil
import site
import sys
import tarfile
import zipfile
from typing import Optional

import py7zr
import streamlit
from clayutil.futil import Downloader
from streamlit.web import bootstrap


def download_dependencies(output_dir: str, mirrors: Optional[dict[str, list[str]]] = None) -> None:
    d = Downloader(output_dir, mirrors=mirrors)

    if not os.path.exists(os.path.join(output_dir, "DejaVuSansCondensed.ttf")):
        ttf_dejavu = d.start("https://github.com/dejavu-fonts/dejavu-fonts/releases/download/version_2_37/dejavu-fonts-ttf-2.37.tar.bz2", "ttf-dejavu.tar.bz2")
        with tarfile.open(ttf_dejavu, "r:bz2") as tar:
            tar.extract("dejavu-fonts-ttf-2.37/ttf/DejaVuSansCondensed.ttf", output_dir, filter="fully_trusted")
        os.remove(ttf_dejavu)
        shutil.move(os.path.join(output_dir, "dejavu-fonts-ttf-2.37", "ttf", "DejaVuSansCondensed.ttf"), os.path.join(output_dir, "DejaVuSansCondensed.ttf"))
        shutil.rmtree(os.path.join(output_dir, "dejavu-fonts-ttf-2.37"))

    if not (os.path.exists(os.path.join(output_dir, "ResourceHanRoundedSC-Medium.ttf")) and os.path.exists(os.path.join(output_dir, "ResourceHanRoundedSC-Normal.ttf")) and os.path.exists(os.path.join(output_dir, "ResourceHanRoundedSC-Regular.ttf"))):
        ttf_rhr = d.start("https://github.com/CyanoHao/Resource-Han-Rounded/releases/download/v0.990/RHR-TTF-0.990.7z", "ttf-rhr.7z")
        with py7zr.SevenZipFile(ttf_rhr, mode="r") as p7z:
            p7z.extract(output_dir, {"ResourceHanRoundedSC-Medium.ttf", "ResourceHanRoundedSC-Normal.ttf", "ResourceHanRoundedSC-Regular.ttf"})
        os.remove(ttf_rhr)

    if not (
        os.path.exists(os.path.join(output_dir, "MapleMono-NF-CN-Italic.ttf"))
        and os.path.exists(os.path.join(output_dir, "MapleMono-NF-CN-Medium.ttf"))
        and os.path.exists(os.path.join(output_dir, "MapleMono-NF-CN-Regular.ttf"))
        and os.path.exists(os.path.join(output_dir, "MapleMono-NF-CN-SemiBold.ttf"))
    ):
        ttf_maple = d.start("https://github.com/subframe7536/maple-font/releases/download/v7.0-beta36/MapleMono-NF-CN.zip", "ttf-maple.zip")
        with zipfile.ZipFile(ttf_maple, "r") as zf:
            zf.extract("MapleMono-NF-CN-Italic.ttf", output_dir)
            zf.extract("MapleMono-NF-CN-Medium.ttf", output_dir)
            zf.extract("MapleMono-NF-CN-Regular.ttf", output_dir)
            zf.extract("MapleMono-NF-CN-SemiBold.ttf", output_dir)
        os.remove(ttf_maple)

    if not os.path.exists(os.path.join(output_dir, "fribidi-0.dll")):
        dll_fribidi = d.start("https://anaconda.org/anaconda/fribidi/1.0.10/download/win-64/fribidi-1.0.10-h62dcd97_0.tar.bz2", "dll-fribidi.tar.bz2")
        with tarfile.open(dll_fribidi, "r:bz2") as tar:
            tar.extract("Library/bin/fribidi-0.dll", output_dir, filter="fully_trusted")
        os.remove(dll_fribidi)
        shutil.move(os.path.join(output_dir, "Library", "bin", "fribidi-0.dll"), os.path.join(output_dir, "fribidi-0.dll"))
        shutil.rmtree(os.path.join(output_dir, "Library"))

    if not os.path.exists(os.path.join(site.getsitepackages()[-1], "fontfallback")):
        pkg_fontfallback = d.start("https://github.com/TrueMyst/PillowFontFallback/archive/refs/heads/main.zip")
        with zipfile.ZipFile(pkg_fontfallback, "r") as zf:
            zf.extractall(output_dir)
        os.remove(pkg_fontfallback)
        shutil.move(os.path.join(output_dir, "PillowFontFallback-main", "fontfallback"), site.getsitepackages()[-1])
        shutil.rmtree(os.path.join(output_dir, "PillowFontFallback-main"))
        sys.path.insert(0, os.path.join(site.getsitepackages()[-1], "fontfallback"))

    if not os.path.exists(os.path.join(output_dir, "bg1.jpg")):
        d.start("https://github.com/ppy/osu-resources/blob/master/osu.Game.Resources/Textures/Backgrounds/bg1.jpg?raw=true", "bg1.jpg")


if __name__ == "__main__":
    download_dependencies("./osuawa/")
    multiprocessing.freeze_support()
    streamlit._is_running_with_streamlit = True
    bootstrap.run("app.py", False, [], {})
