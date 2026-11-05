"""课题源文件（playlist source）的解析与生成

一个课题源文件就是一张「谱面 + mod」的清单，UTF-8 纯文本，一行一条。文件后缀是
``.playlist``（早期叫 ``.properties``，读取时两种都认）：

    banner = ./images/banner.png
    footer = '<a href="...">论坛页</a>'

    3231534 = NM
    # 紧跟条目后面的 # 行是它的 notes，空行会结束 notes 块

    3231534 = HDHR
    # 同一个 BID 可以出现多次

    4337667 = [{"acronym":"DT","settings":{"speed_change":1.1}}]

条目值有两种写法：

- 简写：只接受 mod 缩写
- JSON 对象：``{"mods":[...],"slot":"HD1"}``

构造与导出都在 PlaylistSource 上：from_text / from_file / from_specs / to_text。
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Self

import orjson

from .utils import BeatmapSpec, strip_quotes

_ENTRY_PATTERN = re.compile(r"^(\d+)\s*=\s*(.*)$")


@dataclass(slots=True)
class PlaylistEntry:
    bid: int
    mods: list[dict[str, Any]]
    notes: str = ""
    fields: dict[str, Any] = field(default_factory=dict)  # 附加列，如 slot


@dataclass(slots=True)
class PlaylistSource:
    """一份课题源文件：条目列表 + 文件级选项"""

    entries: list[PlaylistEntry] = field(default_factory=list)
    banner: str = ""
    footer: str = ""

    @property
    def field_names(self) -> list[str]:
        return list(dict.fromkeys(name for entry in self.entries for name in entry.fields))

    @classmethod
    def loads(cls, text: str) -> Self:
        """解析课题源文件内容"""
        entries: list[PlaylistEntry] = []
        banner = ""
        footer = ""
        current: PlaylistEntry | None = None
        notes: list[str] = []

        def flush() -> None:
            # notes 写在条目后面，所以只有遇到下一条（或文件结束）时才知道收齐了
            if current is not None:
                current.notes = "\n".join(notes).strip()
                notes.clear()

        for lineno, raw in enumerate(text.splitlines(), 1):
            line = raw.strip()
            if not line:
                # 空行之后、下一条之前的 # 行只是文件注释，不算 notes
                flush()
                current = None
                continue
            if line.startswith("#"):
                if current is not None:
                    notes.append(line[1:].strip())
                continue
            if not line[0].isdigit():
                # 非数字开头的是文件级选项（banner/footer/...），不是合法的会在这里报错
                key, value = _parse_option(line, lineno)
                if key == "banner":
                    banner = value
                elif key == "footer":
                    footer = value
                # custom_columns 由 field_names 从条目推导，旧文件里的这行只为兼容而接受
                continue
            match = _ENTRY_PATTERN.match(line)
            if match is None:
                raise ValueError("line %d: expected '<bid> = <mods>', got '%s'" % (lineno, line))
            flush()
            try:
                mods, fields = parse_entry_value(match.group(2).strip())
            except ValueError as e:
                raise ValueError("line %d: %s" % (lineno, e)) from e
            current = PlaylistEntry(bid=int(match.group(1)), mods=mods, fields=fields)
            # noinspection bad-argument-type
            entries.append(current)
        flush()
        # 条目和 notes 都收齐后再构造，source 一开始就是完整的
        return cls(entries=entries, banner=banner, footer=footer)

    @classmethod
    def from_file(cls, filename: str) -> Self:
        with open(filename, "r", encoding="utf-8") as fi:
            return cls.loads(fi.read())

    @classmethod
    def from_specs(cls, specs: Iterable[BeatmapSpec]) -> Self:
        """把数据库侧的 BeatmapSpec 转成源文件内容（slot 为空时不写这个字段）"""
        return cls(
            entries=[
                PlaylistEntry(
                    bid=spec.bid,
                    mods=list(spec.raw_mods),
                    notes=spec.notes,
                    fields={"slot": spec.slot} if spec.slot else {},
                )
                for spec in specs
            ],
        )

    def dumps(self) -> str:
        """导出为课题源文件内容：custom_columns + 每行一个 entry"""
        lines: list[str] = []
        if self.banner:
            lines.append("banner = %s" % self.banner)
        if self.footer:
            lines.append("footer = '%s'" % self.footer)
        lines.append("custom_columns = %s" % orjson.dumps(["mods"] + self.field_names).decode())
        for entry in self.entries:
            lines.append("%d = %s" % (entry.bid, orjson.dumps({"mods": entry.mods, **entry.fields}).decode()))
            lines.extend("# %s" % note for note in entry.notes.splitlines())
        return "\n".join(lines) + "\n"


def parse_entry_value(value: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if value.startswith("{"):  # {"mods":[...],"slot":"HD1"}
        obj = orjson.loads(value)
        if not isinstance(obj, dict) or "mods" not in obj:
            raise ValueError("entry object must have a 'mods' key")
        return _normalize_mods(obj["mods"]), {name: item for name, item in obj.items() if name != "mods"}
    return parse_mods(value), {}


def parse_mods(value: str) -> list[dict[str, Any]]:
    value = value.strip()
    if not value:
        raise ValueError("mods is empty")
    if value.startswith("["):  # [{"acronym":"DT","settings":{"speed_change":1.1}}]
        return _normalize_mods(orjson.loads(value))
    # 简写：只接受长度为 2 的 mod 缩写
    value = value.upper()
    return [{"acronym": value[index : index + 2]} for index in range(0, len(value), 2)]


def _normalize_mods(obj: Any) -> list[dict[str, Any]]:
    if not isinstance(obj, list) or not obj:
        raise ValueError("mods should be a non-empty list")
    mods: list[dict[str, Any]] = []
    for item in obj:
        if not isinstance(item, dict) or "acronym" not in item:
            raise ValueError("each mod should be an object with an 'acronym' key")
        settings = item.get("settings") or {}
        mods.append({"acronym": str(item["acronym"]), "settings": settings} if settings else {"acronym": str(item["acronym"])})
    return mods


def _parse_option(line: str, lineno: int) -> tuple[str, str]:
    """解析文件级选项行，返回 (key, value)"""
    key, separator, value = line.partition("=")
    if not separator:
        raise ValueError("line %d: expected '<option> = <value>', got '%s'" % (lineno, line))
    key, value = key.strip().lower(), strip_quotes(value.strip())
    if key not in ("banner", "footer", "custom_columns"):
        raise ValueError("line %d: unknown option '%s'" % (lineno, key))
    return key, value
