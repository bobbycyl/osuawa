import os
import subprocess

_ = lambda x: x  # dummy translation function
from osuawa import C, LANGUAGES

if __name__ == "__main__":
    # you might need to install babel and gettext first

    langs = LANGUAGES

    langs.remove("en_US")

    print("langs:", langs)

    for lang in langs:
        subprocess.run("pybabel extract -o {} --input-dirs . --project=osuawa".format(os.path.join(C.LOCALE.value, lang, "LC_MESSAGES", "messages.po.new")), encoding="utf-8")
        subprocess.run("msgmerge -U messages.po messages.po.new", cwd=os.path.join(C.LOCALE.value, lang, "LC_MESSAGES"), encoding="utf-8")
        try:
            os.remove(os.path.join(C.LOCALE.value, lang, "LC_MESSAGES", "messages.po.new"))
            os.remove(os.path.join(C.LOCALE.value, lang, "LC_MESSAGES", "messages.po~"))
        except FileNotFoundError:
            pass
        subprocess.run("msgfmt messages.po", cwd=os.path.join(C.LOCALE.value, lang, "LC_MESSAGES"), encoding="utf-8")
