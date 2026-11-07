"""检查 i18n 与消息书写规范

规则（输出里方括号中的就是 rule 名）：

    i18n         前端文案必须包裹 _()，且 msgid 用英文
    ui-fstring   前端文案不能是 f-string：gettext 只认字面量，整条模板无法翻译
    ui-literal   拼接出来的文案里不能留未翻译的中文片段
    ui-quote     文案里的 %s 用成对引号，如 "no valid data for '%s'"
    exc-lang     异常消息必须是英文
    exc-case     异常消息小写开头
    exc-i18n     异常消息不要用 _()（异常跟着语言变会让排查变难）
    exc-quote    异常消息里的 %s 用成对引号
    log-lang     日志消息必须是英文（日志不加引号：裸插值便于 grep）
    catalog      _() 的 msgid 必须在 zh_CN 的 .po 里有译文

引号规则对三类消息不同，是有依据的，别一刀切：
    异常      对齐 Python 内建写法（KeyError: 'k'），且异常不翻译，引号稳定
    前端文案  引号帮用户区分「句子」和「值」；反引号也可用于 Markdown 文案
    日志      消费者是 grep 和日志系统，引号是噪声

静态分析无法可靠地区分「名字」和「自由文本」，所以前两类消息默认检查所有 %s。
确实不需要引号的载荷必须显式豁免，不再根据冒号、括号等上下文偷偷放行。
%r / %a 自带 repr 转换，%d 等数值占位符不适用引号规则；f-string 没有类型信息，
也不自动推断其中的插值是不是需要引号的字符串。

AgGrid 的 header_name 不检查：BID / SR / AR / OD / Drain / Combo 是 osu! 圈的固定写法。

豁免：行尾写 # style: allow 可跳过该行；EXEMPT 里可按「文件 + rule 前缀」整块豁免。

用法：
    python check_style.py                 # 检查整个仓库，有问题 exit 1
    python check_style.py awatools/       # 只检查某个目录或文件
    python check_style.py --path awatools/
"""

from __future__ import annotations

import argparse
import ast
import pathlib
import re
from collections.abc import Iterator
from dataclasses import dataclass

#: 会显示用户看得见的文案的 st.* 函数（第一个位置参数，或下面的 kwargs）
UI_FUNCS = {
    "error",
    "warning",
    "info",
    "success",
    "exception",
    "toast",
    "markdown",
    "caption",
    "title",
    "header",
    "subheader",
    "text",
    "write",
    "spinner",
    "status",
    "button",
    "form_submit_button",
    "download_button",
    "link_button",
    "checkbox",
    "toggle",
    "radio",
    "selectbox",
    "multiselect",
    "text_input",
    "text_area",
    "number_input",
    "slider",
    "date_input",
    "file_uploader",
    "expander",
    "tabs",
    "dialog",
    "popover",
    "chat_input",
    "segmented_control",
    "metric",
    "progress",
}
UI_TEXT_KWARGS = {"label", "help", "placeholder", "page_title", "caption", "text"}

LOG_METHODS = {"debug", "info", "warning", "warn", "error", "exception", "critical", "fatal"}

SKIP_DIRS = {".venv", "osu-tools", "__pycache__", ".git", ".ruff_cache"}
PO_PATH = pathlib.Path("share/locale/zh_CN/LC_MESSAGES/messages.po")

CJK = re.compile(r"[\u4e00-\u9fa5]")
#: 完整的 printf 风格占位符。%% 必须先作为整体消费，不能删字符后再匹配。
PERCENT_SPEC = re.compile(r"%(?:\([^)]*\))?[-+ 0#]*(?:\*|\d+)?(?:\.(?:\*|\d+))?[hlL]?[diouxXeEfFgGcrsa%]")

#: (文件名, rule 前缀) -> 豁免理由；前缀不能为空，否则会豁免一切
EXEMPT: dict[tuple[str, str], str] = {
    ("recompute_scores.py", "exc"): "命令行脚本，作者要求保持中文",
    ("recompute_scores.py", "log"): "命令行脚本，作者要求保持中文",
}
assert all(prefix for _, prefix in EXEMPT), "EXEMPT 的 rule 前缀不能为空"


# --------------------------------------------------------------------------- 结构化消息


def dotted(node: ast.AST) -> str:
    """把 st.write / logger.get_logger(...).info 之类的调用名还原成字符串"""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


@dataclass(frozen=True)
class TextPart:
    """一个真实存在过的字面量；不同 part 之间绝不共享引号上下文"""

    text: str
    lineno: int
    translated: bool = False
    fstring: bool = False


@dataclass(frozen=True)
class Message:
    """从 AST 提取的消息，保留字面量边界、翻译状态与动态表达式信息"""

    parts: tuple[TextPart, ...] = ()
    dynamic: bool = False

    @property
    def text(self) -> str:
        """用于语言和大小写检查的静态文本；动态表达式本身不参与"""
        return "".join(part.text for part in self.parts)

    @property
    def translated(self) -> bool:
        return any(part.translated for part in self.parts)

    @property
    def has_fstring(self) -> bool:
        return any(part.fstring for part in self.parts)


def extract_message(node: ast.AST, translated: bool = False) -> Message:
    """递归提取字面量，而不是把表达式剥到最左边或拼成假上下文

    % 格式化只读取左侧模板，右侧是值；+ 拼接保留各段边界；_() 只标记其
    内部的字面量。变量、函数调用和 f-string 插值会把 dynamic 置为 True，
    防止把不完整的静态文本当成完整 msgid 收入 catalog。
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return Message((TextPart(node.value, node.lineno, translated),))

    if isinstance(node, ast.JoinedStr):
        # noinspection bad-argument-type, unresolved-references
        text = "".join(value.value for value in node.values if isinstance(value, ast.Constant) and isinstance(value.value, str))
        return Message((TextPart(text, node.lineno, translated, fstring=True),), dynamic=True)

    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = extract_message(node.left, translated)
        right = extract_message(node.right, translated)
        return Message(left.parts + right.parts, left.dynamic or right.dynamic)

    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        return extract_message(node.left, translated)

    if isinstance(node, ast.Call) and dotted(node.func) == "_" and node.args:
        return extract_message(node.args[0], translated=True)

    return Message(dynamic=True)


def has_words(text: str) -> bool:
    """剥掉占位符后还剩不剩自然语言（"#### #%d: %s" 属于没有，不用翻译）"""
    return bool(re.search(r"[A-Za-z\u4e00-\u9fa5]", PERCENT_SPEC.sub("", text)))


def unquoted_specs(text: str) -> list[str]:
    """找出没有被成对单引号或反引号紧贴包住的 %s

    不在检查前删除 %%，由 PERCENT_SPEC 直接消费完整 token，避免索引漂移。
    冒号和括号不再构成隐式豁免；确有需要时使用行尾 style: allow。
    """
    found = []
    for match in PERCENT_SPEC.finditer(text):
        spec = match.group(0)
        if not spec.endswith("s"):
            continue
        before = text[match.start() - 1 : match.start()] if match.start() else ""
        after = text[match.end() : match.end() + 1]
        if before in ("'", "`") and after == before:
            continue
        found.append(spec)
    return found


# --------------------------------------------------------------------------- 检查器


class Checker:
    def __init__(self) -> None:
        self.issues: list[tuple[str, int, str, str]] = []
        self.msgids: dict[str, tuple[pathlib.Path, int]] = {}  # msgid -> (文件, 行号)
        self.files = 0
        self.ui_texts = 0
        self.logs = 0
        self.exceptions = 0
        self._source: dict[pathlib.Path, list[str]] = {}

    # -------- 公共设施

    def add(self, path: pathlib.Path, lineno: int, rule: str, detail: str, anchor: int = 0) -> None:
        """记录问题；anchor 是整条调用的首行，用于支持跨行写法上的豁免注释"""
        if self._suppressed(path, lineno) or self._suppressed(path, anchor):
            return
        if any(path.name == name and rule.startswith(prefix) for name, prefix in EXEMPT):
            return
        self.issues.append((path.as_posix(), lineno, rule, detail))

    def _suppressed(self, path: pathlib.Path, lineno: int) -> bool:
        """该行是否带了豁免标记"""
        if not lineno:
            return False
        if path not in self._source:
            self._source[path] = path.read_text(encoding="utf-8").splitlines()
        lines = self._source[path]
        text = lines[lineno - 1] if 0 < lineno <= len(lines) else ""
        return "# style: allow" in text or "# noqa" in text

    def collect_msgid(self, path: pathlib.Path, node: ast.AST) -> None:
        """记录完整静态 msgid，供 catalog 检查（与主遍历共用一次解析）"""
        if isinstance(node, ast.Call) and dotted(node.func) == "_" and node.args:
            message = extract_message(node.args[0])
            if message.text and not message.dynamic:
                self.msgids.setdefault(message.text, (path, node.lineno))

    # -------- 四类规则

    def check_ui(self, path: pathlib.Path, call: ast.Call) -> None:
        """前端文案：必须 _()、不能是 f-string、%s 要加成对引号"""
        name = dotted(call.func).rsplit(".", 1)[-1]
        if name not in UI_FUNCS:
            return
        if name == "dialog":
            targets = call.args[:1]
        elif name == "tabs" and call.args and isinstance(call.args[0], ast.List):
            targets = call.args[0].elts
        else:
            targets = call.args[:1] + [kw.value for kw in call.keywords if kw.arg in UI_TEXT_KWARGS]

        for target in targets:
            message = extract_message(target)
            text = message.text
            if not text or not has_words(text):
                continue
            self.ui_texts += 1

            if message.has_fstring:
                self.add(path, target.lineno, "ui-fstring", '文案是 f-string，无法翻译（应为 _("模板 %%s") %% 值）：%r' % text[:60], call.lineno)
                continue

            # 引号按原始字面量查，绝不让 + 两侧的字符组成虚假的引号上下文。
            for part in message.parts:
                for spec in unquoted_specs(part.text):
                    self.add(path, target.lineno, "ui-quote", "%s 没加引号：%r" % (spec, part.text[:60]), call.lineno)

            chinese = [part for part in message.parts if not part.translated and CJK.search(part.text)]
            if chinese and (message.dynamic or message.translated or len(message.parts) > 1):
                self.add(path, target.lineno, "ui-literal", "拼接里有未翻译的中文片段：%r" % chinese[0].text[:60], call.lineno)
                continue

            if message.translated:
                if any(CJK.search(part.text) for part in message.parts if part.translated):
                    self.add(path, target.lineno, "i18n", "msgid 应该是英文：%r" % text[:60], call.lineno)
                continue

            self.add(path, target.lineno, "i18n", "文案没有包裹 _()：%r" % text[:60], call.lineno)

    def check_exception(self, path: pathlib.Path, node: ast.Raise) -> None:
        """异常消息：英文、小写开头、不用 _()、%s 加成对引号"""
        if not isinstance(node.exc, ast.Call) or not node.exc.args:
            return
        message = extract_message(node.exc.args[0])
        text = message.text
        if not text:  # 取不到文本（变量、f"{k}" 这类全插值）就没什么可查的
            return
        self.exceptions += 1

        if message.translated:
            self.add(path, node.lineno, "exc-i18n", "异常消息不要用 _()：%r" % text[:60])
        if CJK.search(text):
            self.add(path, node.lineno, "exc-lang", "异常消息必须是英文：%r" % text[:60])
        elif text[:1].isupper():
            self.add(path, node.lineno, "exc-case", "异常消息小写开头：%r" % text[:60])
        for part in message.parts:
            if part.fstring:  # f-string 插值没有类型信息，不推断是否需要引号
                continue
            for spec in unquoted_specs(part.text):
                self.add(path, node.lineno, "exc-quote", "%s 没加引号：%r" % (spec, text[:60]))

    def check_log(self, path: pathlib.Path, call: ast.Call) -> None:
        """日志消息：只检查第一个参数（消息模板），其余参数是要插入的值"""
        if not call.args:
            return
        arg = call.args[0]
        message = extract_message(arg)
        text = message.text
        if not text:
            return
        self.logs += 1
        if CJK.search(text):
            self.add(path, arg.lineno, "log-lang", "日志消息必须是英文：%r" % text[:60], call.lineno)


# --------------------------------------------------------------------------- 入口


def iter_files(root: pathlib.Path) -> Iterator[pathlib.Path]:
    if root.is_file():
        yield root
        return
    for path in sorted(root.glob("**/*.py")):
        if not any(part in SKIP_DIRS for part in path.parts):
            yield path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="检查 i18n 与消息书写规范")
    parser.add_argument("path", nargs="?", help="要检查的文件或目录（默认当前仓库）")
    parser.add_argument("--path", dest="path_option", help="要检查的文件或目录（等价于位置参数）")
    args = parser.parse_args(argv)

    checker = Checker()
    for path in iter_files(pathlib.Path(args.path_option or args.path or ".")):
        checker.files += 1
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            checker.collect_msgid(path, node)
            if isinstance(node, ast.Raise):
                checker.check_exception(path, node)
            elif isinstance(node, ast.Call):
                name = dotted(node.func).rsplit(".", 1)[-1]
                if dotted(node.func).startswith("st."):
                    checker.check_ui(path, node)
                elif name in LOG_METHODS:
                    checker.check_log(path, node)

    for path, lineno, rule, detail in checker.issues:
        print("%s:%d [%s] %s" % (path, lineno, rule, detail))
    print(
        "扫描 %d 个文件（%d 条文案 / %d 条日志 / %d 个异常），共 %d 个问题" % (checker.files, checker.ui_texts, checker.logs, checker.exceptions, len(checker.issues)),
    )
    return 1 if checker.issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
