"""检查 i18n 与消息书写规范

规则（输出里方括号中的就是 rule 名）：

    i18n         前端文案必须包裹 _()，且 msgid 用英文
    ui-fstring   前端文案不能是 f-string：gettext 只认字面量，整条模板无法翻译
    ui-literal   拼接出来的文案里不能留未翻译的中文片段
    ui-quote     文案里插值的「名字」用单引号，如 "no valid data for '%s'"
    exc-lang     异常消息必须是英文
    exc-case     异常消息小写开头
    exc-i18n     异常消息不要用 _()（异常跟着语言变会让排查变难）
    exc-quote    异常消息里的名字用单引号
    log-lang     日志消息必须是英文（日志不加引号：裸插值便于 grep）
    catalog      _() 的 msgid 必须在 zh_CN 的 .po 里有译文

引号规则对三类消息不同，是有依据的，别一刀切：
    异常      对齐 Python 内建写法（KeyError: 'k'），且异常不翻译，引号稳定
    前端文案  引号帮用户区分「句子」和「值」，只对名字类加；路径/URL 不加
    日志      消费者是 grep 和日志系统，引号是噪声

AgGrid 的 header_name 不检查：BID / SR / AR / OD / Drain / Combo 是 osu! 圈的固定写法。

豁免：行尾写 # style: allow 可跳过该行；EXEMPT 里可按「文件 + rule 前缀」整块豁免。

用法：
    python check_style.py                # 检查整个仓库，有问题 exit 1
    python check_style.py awatools/         # 只检查某个目录或文件
"""

from __future__ import annotations

import argparse
import ast
import pathlib
import re
import sys

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
STR_SPEC = re.compile(r"%(?:\([^)]*\))?[-+ 0#]*\d*(?:\.\d+)?s")

#: (文件名, rule 前缀) -> 豁免理由；前缀不能为空，否则会豁免一切
EXEMPT: dict[tuple[str, str], str] = {
    ("recompute_scores.py", "exc"): "命令行脚本，作者要求保持中文",
    ("recompute_scores.py", "log"): "命令行脚本，作者要求保持中文",
}
assert all(prefix for _, prefix in EXEMPT), "EXEMPT 的 rule 前缀不能为空"


# --------------------------------------------------------------------------- 语法小工具


def dotted(node: ast.AST) -> str:
    """把 st.write / logger.get_logger(...).info 之类的调用名还原成字符串"""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def literal(node: ast.AST) -> str | None:
    """取出可检查的文本：字面量、f-string 的字面量部分、_("x") 的 msgid

    _("x") % y 会取到 "x"；只取拼接的最左段（要整条文本请用 message_text）；
    其它表达式（变量、函数调用）返回 None，表示无法静态检查。
    """
    while isinstance(node, ast.BinOp):  # 剥掉 % 与 + 拼接，取最左边
        node = node.left
    if isinstance(node, ast.Call) and dotted(node.func) == "_" and node.args:
        node = node.args[0]
        while isinstance(node, ast.BinOp):
            node = node.left
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        # noinspection bad-argument-type, unresolved-references
        return "".join(v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str))
    return None


def add_operands(node: ast.AST) -> list[ast.AST]:
    """把 a + b + c 摊平成 [a, b, c]，用于逐段判断是否被 _() 包住"""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return add_operands(node.left) + add_operands(node.right)
    return [node]


def message_text(node: ast.AST) -> str:
    """把 a + b + _("c") 的各段拼成完整文本，取不到文本的段按空串算

    只看最左段会漏掉 "done" + "中文" 这类尾巴，日志/异常的语言与引号检查都走这里。
    """
    return "".join(literal(op) or "" for op in add_operands(node))


def is_translated(node: ast.AST) -> bool:
    """是否 _("x") 或 _("x") % y"""
    while isinstance(node, ast.BinOp):
        node = node.left
    return isinstance(node, ast.Call) and dotted(node.func) == "_"


def has_words(text: str) -> bool:
    """剥掉占位符后还剩不剩自然语言（"#### #%d: %s" 属于没有，不用翻译）"""
    return bool(re.search(r"[A-Za-z\u4e00-\u9fa5]", re.sub(r"%[-+ 0#]*\d*(?:\.\d+)?[A-Za-z]", "", text)))


def unquoted_specs(text: str) -> list[str]:
    """找出没被引号包住的 %s

    先抹掉 %% 转义再匹配，否则 "100%%s" 会被当成占位符。
    以下视为自由文本、不要求引号：已加引号的 '%s'、冒号后的载荷（": %s"）、
    括号里的说明（"(type %s)"、"(got %s)"）。
    """
    cleaned = text.replace("%%", "")
    found = []
    for match in STR_SPEC.finditer(cleaned):
        before = cleaned[max(0, match.start() - 2) : match.start()]
        after = cleaned[match.end() : match.end() + 1]
        if before.endswith(("'", "`")) and after in ("'", "`"):
            continue
        if before.endswith(": ") or before.endswith("(") or after == ")":
            continue
        found.append(match.group(0))
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
        """记录用到的 msgid，供 catalog 检查（与主遍历共用一次解析）"""
        if isinstance(node, ast.Call) and dotted(node.func) == "_" and node.args:
            text = literal(node.args[0])
            if text:
                self.msgids.setdefault(text, (path, node.lineno))

    # -------- 四条规则

    def check_ui(self, path: pathlib.Path, call: ast.Call) -> None:
        """前端文案：必须 _()、不能是 f-string、名字要加引号"""
        name = dotted(call.func).rsplit(".", 1)[-1]
        if name not in UI_FUNCS:
            return
        if name == "dialog":
            targets = call.args[:1]
        elif name == "tabs" and call.args and isinstance(call.args[0], ast.List):
            # noinspection unresolved-references
            targets = call.args[0].elts
        else:
            targets = call.args[:1] + [kw.value for kw in call.keywords if kw.arg in UI_TEXT_KWARGS]

        for target in targets:
            text = literal(target)
            if not text or not has_words(text):
                continue
            self.ui_texts += 1

            if isinstance(target, ast.JoinedStr):
                self.add(path, target.lineno, "ui-fstring", '文案是 f-string，无法翻译（应为 _("模板 %%s") %% 值）：%r' % text[:60], call.lineno)
                continue

            operands = add_operands(target)

            # 引号按段查：拼接时尾巴上的 %s 也要覆盖
            for op in operands:
                piece = literal(op)
                if piece:
                    for spec in unquoted_specs(piece):
                        self.add(path, target.lineno, "ui-quote", "%s 没加引号：%r" % (spec, piece[:60]), call.lineno)

            # 拼接：任何一段中文常量都是漏翻（"_() + 中文尾巴" 比 "前缀 + _()" 更常见）
            if len(operands) > 1:
                # noinspection unresolved-references
                chinese = [op.value for op in operands if isinstance(op, ast.Constant) and isinstance(op.value, str) and CJK.search(op.value)]
                if chinese:
                    # noinspection string-format, unresolved-references
                    self.add(path, target.lineno, "ui-literal", "拼接里有未翻译的中文片段：%r" % chinese[0][:60], call.lineno)
                    continue

            if is_translated(target):
                if CJK.search(text):
                    self.add(path, target.lineno, "i18n", "msgid 应该是英文：%r" % text[:60], call.lineno)
                continue

            if not any(is_translated(op) for op in operands):
                self.add(path, target.lineno, "i18n", "文案没有包裹 _()：%r" % text[:60], call.lineno)

    def check_exception(self, path: pathlib.Path, node: ast.Raise) -> None:
        """异常消息：英文、小写开头、不用 _()、名字加引号"""
        if not isinstance(node.exc, ast.Call) or not node.exc.args:
            return
        arg = node.exc.args[0]
        text = message_text(arg)
        if not text:  # 取不到文本（变量、f"{k}" 这类全插值）就没什么可查的
            return
        self.exceptions += 1

        if is_translated(arg):
            self.add(path, node.lineno, "exc-i18n", "异常消息不要用 _()：%r" % text[:60])
        if CJK.search(text):
            self.add(path, node.lineno, "exc-lang", "异常消息必须是英文：%r" % text[:60])
        elif text[:1].isupper():
            self.add(path, node.lineno, "exc-case", "异常消息小写开头：%r" % text[:60])
        if not isinstance(arg, ast.JoinedStr):  # f-string 没有 %s，引号规则不适用
            for spec in unquoted_specs(text):
                self.add(path, node.lineno, "exc-quote", "%s 没加引号：%r" % (spec, text[:60]))

    def check_log(self, path: pathlib.Path, call: ast.Call) -> None:
        """日志消息：只要求英文（引号不给日志加，便于 grep）"""
        for arg in call.args:
            text = message_text(arg)
            if not text:
                continue
            self.logs += 1
            if CJK.search(text):
                self.add(path, arg.lineno, "log-lang", "日志消息必须是英文：%r" % text[:60], call.lineno)

    def check_catalog(self) -> None:
        """_() 的 msgid 都要有译文"""
        try:
            # noinspection package-requirements
            from babel.messages.pofile import read_po
        except ImportError:
            print("跳过 catalog 检查：未安装 babel", file=sys.stderr)
            return
        if not PO_PATH.exists():
            return
        with open(PO_PATH, "rb") as po_file:
            # noinspection unhashable
            translated = {m.id for m in read_po(po_file) if isinstance(m.id, str) and m.id}
        for msgid in sorted(set(self.msgids) - translated):
            path, lineno = self.msgids[msgid]
            self.add(path, lineno, "catalog", "缺少译文：%r" % msgid[:60])


# --------------------------------------------------------------------------- 入口


def iter_files(root: pathlib.Path):
    if root.is_file():
        yield root
        return
    for path in sorted(root.glob("**/*.py")):
        if not any(part in SKIP_DIRS for part in path.parts):
            yield path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="检查 i18n 与消息书写规范")
    parser.add_argument("--path", default=".", help="要检查的文件或目录（默认当前仓库）")
    args = parser.parse_args(argv)

    checker = Checker()
    for path in iter_files(pathlib.Path(args.path)):
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
    checker.check_catalog()

    for path, lineno, rule, detail in checker.issues:
        print("%s:%d [%s] %s" % (path, lineno, rule, detail))
    print(
        "扫描 %d 个文件（%d 条文案 / %d 条日志 / %d 个异常），共 %d 个问题" % (checker.files, checker.ui_texts, checker.logs, checker.exceptions, len(checker.issues)),
    )
    return 1 if checker.issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
