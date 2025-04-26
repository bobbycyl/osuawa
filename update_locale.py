import os
import subprocess

_ = lambda x: x  # dummy translation function
from osuawa import LANGUAGES

if __name__ == "__main__":
    # you might need to install babel and gettext first

    langs = LANGUAGES

    langs.remove("en_US")

    print("langs:", langs)

    for lang in langs:
        path = "./share/locale/{}/LC_MESSAGES".format(lang)
        subprocess.run("pybabel extract -o {}/messages.po.new --input-dirs . --project=osuawa".format(path), encoding="utf-8")
        subprocess.run("msgmerge -U messages.po messages.po.new", cwd=path, encoding="utf-8")
        try:
            os.remove("{}/messages.po.new".format(path))
            os.remove("{}/messages.po~".format(path))
        except FileNotFoundError:
            pass
        subprocess.run("msgfmt messages.po", cwd=path, encoding="utf-8")
