import subprocess

if __name__ == "__main__":

    langs = ["zh_CN"]

    for lang in langs:
        path = "./share/locale/{}/LC_MESSAGES/".format(lang)
        subprocess.run("pybabel extract -o {}/messages.po.new --input-dirs . --project=osuawa".format(path), shell=True, encoding="utf-8")
        subprocess.run("rm messages.mo", shell=True, cwd=path, encoding="utf-8")
        subprocess.run("msgmerge -U messages.po messages.po.new && rm messages.po.new && rm messages.po~", shell=True, cwd=path, encoding="utf-8")
        subprocess.run("msgfmt messages.po", shell=True, cwd=path, encoding="utf-8")
