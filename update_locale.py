import os
import subprocess

_ = lambda x: x  # dummy translation function
from osuawa import C, LANGUAGES

if __name__ == "__main__":
    # you might need to install babel and gettext first

    langs = LANGUAGES.copy()

    langs.remove("en_US")

    print("langs:", langs)

    for lang in langs:
        subprocess.run(
            [
                "pybabel",
                "extract",
                "-o",
                os.path.join(C.LOCALE.value, lang, "LC_MESSAGES", "messages.po.new"),
                "--input-dirs",
                ".",
                "--project=osuawa",
            ],
            encoding="utf-8",
            text=True,
            check=True,
        )
        subprocess.run(
            [
                "msgmerge",
                "-U",
                "messages.po",
                "messages.po.new",
            ],
            cwd=os.path.join(C.LOCALE.value, lang, "LC_MESSAGES"),
            encoding="utf-8",
            text=True,
            check=True,
        )
        try:
            os.remove(os.path.join(C.LOCALE.value, lang, "LC_MESSAGES", "messages.po.new"))
            os.remove(os.path.join(C.LOCALE.value, lang, "LC_MESSAGES", "messages.po~"))
        except FileNotFoundError:
            pass
        subprocess.run(
            [
                "msgfmt",
                "messages.po",
            ],
            cwd=os.path.join(C.LOCALE.value, lang, "LC_MESSAGES"),
            encoding="utf-8",
            text=True,
            check=True,
        )
