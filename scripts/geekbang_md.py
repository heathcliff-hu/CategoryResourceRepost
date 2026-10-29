#!/usr/bin/env python3
"""极客时间专栏：残留 HTML 片段转 Markdown + 结构规范化（幂等，可重复运行）。

用法：
    python3 scripts/geekbang_md.py <目录>            # 干跑：只列出会改动的文件
    python3 scripts/geekbang_md.py <目录> --apply    # 写入改动

转换规则：
- <audio> 播放器保持原样，不转为链接（用户明确要求）
- <img src="..." alt=""> -> ![](url)
- <li>...</li> -> '- ' 列表项（支持多行、行内 <br>、单行三种形态）
- 引用/提示块 '> ' + <p>...</p> -> '>' 独占行 + 空行 + [标签 + 空行] + 正文；
  块内 <br> 分隔的每行独立成段（空行分隔）；短标签（**小提示：**、**例 1**：等）识别为标签行
- 紧凑引用块 '> ' + 短标签 -> 上述分段式
- <p>、</p>、<br>（含 <br/>、<br /> 变体）删除，换行保留为软换行；连续 <br><br> -> 段落分隔（空行）
- <strong> -> **（含已知乱码形态修复）
- &lt; / &gt; 实体保留

结构规范：
- '> ' 独占行 -> '>' + 空行（引用/提示块写法）
- 列表项说明区缩进：术语条目（短词条或整行加粗的列表项）成组出现且每条都有
  说明时，说明区（含代码块、嵌套列表）整体缩进 2 空格归入条目
- 粗体闭合修复：**……。**正文 渲染失效，补为 **……。** 正文
- 标题不加粗：## **xxx** -> ## xxx
- 无标题的空标题行删除
- 连续空行合并为一行；文件以单个换行结尾
- 控制字符（如 \\x08）清除

完整约定见仓库根 CLAUDE.md。
"""
import argparse
import re
import unicodedata
from pathlib import Path


# ---------- HTML 转 Markdown ----------

_BR = r"<br\s*/?>"


def _convert_li(m: re.Match) -> str:
    inner = m.group(1)
    inner = re.sub(_BR + r"[ \t]*\n", "\n", inner)
    inner = re.sub(_BR, "\n", inner)
    inner = inner.replace("<p>", "").replace("</p>", "")
    lines = inner.split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        return ""
    out = ["- " + lines[0]]
    for l in lines[1:]:
        out.append("  " + l if l.strip() else "")
    return "\n".join(out)


# ---------- 引用块 -> 分段式 ----------

_QUOTE_LABEL = r"(\*\*[^*\n]{1,12}[：:]\*\*|\*\*[^*\n]{1,12}\*\*[：:])"
_QUOTE_BLOCK = re.compile(r"^>[ \t]*\n<p>(.*?)</p>", re.M | re.S)
_QUOTE_COMPACT = re.compile(rf"(?m)^>[ \t]*\n(?:\n)?{_QUOTE_LABEL}\n(?=[^\n])")
_BR_RUN = re.compile(r"(?:<br\s*/?>\s*)+")
_LABEL_AT_START = re.compile(r"^" + _QUOTE_LABEL + r"<br\s*/?>")


def _fmt_quote_block(content: str) -> str:
    label = None
    m = _LABEL_AT_START.match(content)
    if m:
        label = m.group(1)
        content = content[m.end():]
    paras = [p for p in (s.strip() for s in _BR_RUN.split(content)) if p]
    body = "\n\n".join(paras)
    if label:
        return ">\n\n" + label + "\n\n" + body
    return ">\n\n" + body


def convert_quotes(text: str) -> str:
    """'> ' (+ <p>) 引用块 -> 分段式：'>' 独占行 + 空行 + [标签 + 空行] + 正文（每行独立成段）。"""
    out, pos = [], 0
    for m in _QUOTE_BLOCK.finditer(text):
        block = _fmt_quote_block(m.group(1))
        if m.end() < len(text) and text[m.end()] == "\n" and text[m.end() + 1: m.end() + 2] not in ("\n", ""):
            block += "\n"
        out.append(text[pos: m.start()])
        out.append(block)
        pos = m.end()
    text = "".join(out) + text[pos:]
    return _QUOTE_COMPACT.sub(r">\n\n\1\n\n", text)


def convert_html(text: str) -> str:
    # 连续 <br> -> 段落分隔
    text = re.sub(r"(?:" + _BR + r"\s*){2,}", "\n\n", text)
    # 已知乱码形态：**（<strong><strong>X**</strong>）</strong> -> **（X）**
    text = re.sub(
        r"\*\*（<strong><strong>(.*?)\*\*</strong>）</strong>", r"**（\1）**", text
    )
    text = re.sub(r"<strong>(.*?)</strong>", r"**\1**", text)
    text = re.sub(r'<img\s+src="([^"]+)"\s+alt="[^"]*"\s*/?>', r"![](\1)", text)
    text = re.sub(r"<li>(.*?)</li>", _convert_li, text, flags=re.S)
    text = re.sub(r"^<p>", "", text, flags=re.M)
    text = re.sub(r"</p>$", "", text, flags=re.M)
    text = re.sub(_BR + r"[ \t]*\n", "\n", text)
    text = re.sub(_BR, "\n", text)
    return text


# ---------- 列表项说明区缩进 ----------

_ITEM_RE = re.compile(r"^([-*+]|\d+\.)\s")
_LIST_STOP_RE = re.compile(r"^(#{1,6}\s|>|<|\[\^)")
_TERM_PUNCT = "。；！？："


def _is_term_item(line: str) -> bool:
    """术语条目：列表项文本是短词条或整行加粗；整句列表项视为说明区里的嵌套行。"""
    m = _ITEM_RE.match(line)
    if not m:
        return False
    text = line[m.end() :].strip()
    if len(text) >= 4 and text.startswith("**") and text.endswith("**"):
        return True
    return len(text) <= 20 and text[-1:] not in _TERM_PUNCT


def _fence_info(lines):
    """返回 (in_fence, zero_fence)：行是否在围栏代码块内；块开头是否在 0 列。"""
    in_fence = [False] * len(lines)
    zero_fence = [False] * len(lines)
    i, n = 0, len(lines)
    while i < n:
        if lines[i].startswith("```"):
            j = i + 1
            while j < n and not lines[j].startswith("```"):
                j += 1
            for k in range(i, min(j + 1, n)):
                in_fence[k] = True
                zero_fence[k] = True
            i = j + 1
        else:
            i += 1
    return in_fence, zero_fence


def indent_list_explanations(text: str) -> str:
    """术语条目成组出现且每条都有说明时，说明区缩进 2 空格归入条目。

    说明区含代码块时，代码块及其后的说明内容一并缩进（代码块之后的列表行
    视为嵌套内容，除非它本身是术语条目）。
    """
    lines = text.split("\n")
    n = len(lines)
    in_fence, zero_fence = _fence_info(lines)
    out = lines[:]

    def _starts_item(k: int) -> bool:
        return (
            not lines[k].startswith((" ", "\t"))
            and not in_fence[k]
            and _ITEM_RE.match(lines[k]) is not None
        )

    i = 0
    while i < n:
        if not _starts_item(i):
            i += 1
            continue
        items = [{"idx": i, "blocks": [], "fence": False}]
        j = i + 1
        while j < n:
            cur = lines[j]
            if in_fence[j]:
                items[-1]["fence"] = True
                j += 1
            elif cur == "":
                j += 1
            elif _LIST_STOP_RE.match(cur) and not in_fence[j]:
                break
            elif _starts_item(j) and (not items[-1]["fence"] or _is_term_item(cur)):
                items.append({"idx": j, "blocks": [], "fence": False})
                j += 1
            else:
                start = j
                while (
                    j < n
                    and not in_fence[j]
                    and lines[j] != ""
                    and not _LIST_STOP_RE.match(lines[j])
                    and not (_starts_item(j) and (not items[-1]["fence"] or _is_term_item(lines[j])))
                ):
                    j += 1
                if j > start:
                    items[-1]["blocks"].append((start, j))
        run_end = j

        if len(items) >= 2 and all(it["blocks"] for it in items):
            for k, it in enumerate(items):
                a = it["idx"] + 1
                b = items[k + 1]["idx"] if k + 1 < len(items) else run_end
                extend = False
                for _, end in it["blocks"]:
                    m = end
                    while m < b and lines[m] == "":
                        m += 1
                    if m < b and lines[m].startswith("```"):
                        extend = True
                        break
                todo = (
                    range(a, b)
                    if extend
                    else [r for s, e in it["blocks"] for r in range(s, e)]
                )
                for r in todo:
                    if out[r] == "":
                        continue
                    if zero_fence[r] or not out[r].startswith((" ", "\t")):
                        out[r] = "  " + out[r]
        i = run_end
    return "\n".join(out)


# ---------- 结构规范化 ----------

def _is_punct(c: str) -> bool:
    return unicodedata.category(c).startswith(("P", "S"))


def _fix_bold(line: str) -> str:
    """闭合 ** 前是标点且后紧跟正文时无法闭合（CommonMark right-flanking），补一个空格。"""
    pos = [m.start() for m in re.finditer(r"\*\*", line)]
    if not pos or len(pos) % 2:
        return line
    for k in range(len(pos) - 1, 0, -2):
        i = pos[k]
        if i == 0 or i + 2 >= len(line):
            continue
        prev_c, next_c = line[i - 1], line[i + 2]
        if (
            _is_punct(prev_c)
            and not next_c.isspace()
            and not _is_punct(next_c)
            and next_c != "*"
        ):
            line = line[: i + 2] + " " + line[i + 2 :]
    return line


def _fix_heading(line: str) -> str:
    m = re.match(r"^(#{1,6}\s+)(.*)$", line)
    if not m or "**" not in line:
        return line
    body = line[len(m.group(1)) :].replace("**", "")
    body = re.sub(r"^(\d+\.)\s*", r"\1 ", body)
    return m.group(1) + body


def normalize(text: str) -> str:
    out = []
    prev_blank = False
    for line in text.split("\n"):
        if re.match(r"^> *$", line):
            out.append(">")
            out.append("")
            prev_blank = True
            continue
        if re.match(r"^#{1,6}\s*$", line):
            continue
        if re.match(r"^#{1,6}\s", line):
            line = _fix_heading(line)
        else:
            line = _fix_bold(line)
        if line == "":
            if prev_blank:
                continue
            prev_blank = True
            out.append("")
        else:
            prev_blank = False
            out.append(line)
    return "\n".join(out).rstrip("\n") + "\n"


def strip_control(text: str):
    junk = sorted({hex(ord(c)) for c in text if ord(c) < 32 and c not in "\n\t"})
    clean = "".join(c for c in text if c == "\n" or c == "\t" or ord(c) >= 32)
    return clean, junk


def process(text: str):
    text, junk = strip_control(text)
    text = indent_list_explanations(convert_html(convert_quotes(text)))
    return normalize(text), junk


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("target", help="要处理的目录（递归查找 *.md）")
    ap.add_argument("--apply", action="store_true", help="写入改动（缺省为干跑）")
    args = ap.parse_args()

    root = Path(args.target)
    if not root.is_dir():
        ap.error(f"目录不存在：{root}")

    changed = total = 0
    for p in sorted(root.rglob("*.md")):
        total += 1
        orig = p.read_text(encoding="utf-8")
        result, junk = process(orig)
        if junk:
            print(f"  [控制字符 {','.join(junk)}] {p}")
        if result != orig:
            changed += 1
            if args.apply:
                p.write_text(result, encoding="utf-8")
            print(f"{'[done]' if args.apply else '[dry] '} {p}")
    print(f"{'已处理' if args.apply else '待处理'} {changed}/{total} 个文件")


if __name__ == "__main__":
    main()
