import json

from osuawa.utils import *


def dump_mods(filename, obj):
    with open(filename, "w", encoding="utf-8") as fo:
        json.dump(obj, fo, indent=4, ensure_ascii=False)


dump_mods("OsuModsEntries.json", osu_mod_entries)
dump_mods("TaikoModsEntries.json", taiko_mod_entries)
dump_mods("CatchModsEntries.json", catch_mod_entries)
dump_mods("ManiaModsEntries.json", mania_mod_entries)
