import os
import subprocess

if __name__ == "__main__":

    langs = ["zh_CN"]

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
