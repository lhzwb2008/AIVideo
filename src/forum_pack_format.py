"""论坛 post.md 解析辅助：表格合并、图注、各平台正文格式化。"""

from __future__ import annotations

import re
from html import escape

_CAPTION_RE = re.compile(r"^\*(图\d+[:：].+?)\*$")
_TABLE_SEP_RE = re.compile(r"^:?-+:?$")


def is_table_line(line: str) -> bool:
    return line.strip().startswith("|")


def is_caption_line(line: str) -> bool:
    s = line.strip()
    if _CAPTION_RE.match(s):
        return True
    return s.startswith("*图") and s.endswith("*")


def extract_caption(line: str) -> str:
    s = line.strip()
    m = _CAPTION_RE.match(s)
    if m:
        return m.group(1).strip()
    if s.startswith("*") and s.endswith("*"):
        return s.strip("*").strip()
    return s


def join_forum_paragraphs(paras: list[str]) -> str:
    """合并段落；连续 | 行合并为 markdown 表格块。"""
    blocks: list[str] = []
    i = 0
    while i < len(paras):
        line = paras[i].strip()
        if not line:
            i += 1
            continue
        if is_table_line(line):
            table_lines: list[str] = []
            while i < len(paras) and is_table_line(paras[i].strip()):
                table_lines.append(paras[i].strip())
                i += 1
            blocks.append("\n".join(table_lines))
            continue
        blocks.append(line)
        i += 1
    return "\n\n".join(blocks)


def split_body_blocks(body: str) -> list[str]:
    blocks: list[str] = []
    for block in re.split(r"\n{2,}", body.strip()):
        block = block.strip()
        if block:
            blocks.append(block)
    return blocks


def markdown_table_to_plaintext(table_md: str) -> str:
    lines = [ln.strip() for ln in table_md.strip().splitlines() if ln.strip().startswith("|")]
    rows: list[list[str]] = []
    for line in lines:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells and all(_TABLE_SEP_RE.fullmatch(c or "-") for c in cells):
            continue
        if cells:
            rows.append(cells)
    if len(rows) < 2:
        return table_md

    header = rows[0]
    parts: list[str] = []
    for row in rows[1:]:
        for col, val in zip(header, row):
            if val:
                parts.append(f"{col}：{val}")
        parts.append("")
    while parts and not parts[-1]:
        parts.pop()
    return "\n".join(parts)


def body_to_plaintext(body: str) -> str:
    """东财/雪球粘贴：表格转可读条目，保留段落。"""
    out: list[str] = []
    for block in split_body_blocks(body):
        if is_table_line(block.splitlines()[0]):
            out.append(markdown_table_to_plaintext(block))
        else:
            out.append(block)
    return "\n\n".join(out)


def _inline_html(text: str) -> str:
    text = escape(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<em>\1</em>", text)
    return text


def _table_html(lines: list[str]) -> str:
    rows: list[list[str]] = []
    for line in lines:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells and all(_TABLE_SEP_RE.fullmatch(c or "-") for c in cells):
            continue
        if cells:
            rows.append(cells)
    if not rows:
        return ""
    parts = ['<table border="1" cellspacing="0" cellpadding="6">']
    for i, row in enumerate(rows):
        tag = "th" if i == 0 else "td"
        parts.append("<tr>")
        for cell in row:
            parts.append(f"<{tag}>{_inline_html(cell)}</{tag}>")
        parts.append("</tr>")
    parts.append("</table>")
    return "".join(parts)


def body_to_html(body: str) -> str:
    parts: list[str] = []
    for block in split_body_blocks(body):
        lines = block.splitlines()
        if lines and is_table_line(lines[0]):
            table = _table_html(lines)
            if table:
                parts.append(table)
            continue
        for para in re.split(r"\n{2,}", block):
            p = para.strip()
            if p:
                parts.append(f"<p>{_inline_html(p)}</p>")
    return "\n".join(parts)


def markdown_table_to_compact_lines(table_md: str) -> list[str]:
    """B 站 opus：表格每行压成一条，避免换行被渲染成超大段间距。"""
    lines = [ln.strip() for ln in table_md.strip().splitlines() if ln.strip().startswith("|")]
    rows: list[list[str]] = []
    for line in lines:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells and all(_TABLE_SEP_RE.fullmatch(c or "-") for c in cells):
            continue
        if cells:
            rows.append(cells)
    if len(rows) < 2:
        return [table_md.strip()] if table_md.strip() else []

    header = rows[0]
    out: list[str] = []
    for row in rows[1:]:
        cells = [f"{col}：{val}" for col, val in zip(header, row) if val]
        if cells:
            out.append("；".join(cells))
    return out


def body_to_xueqiu_plaintext(body: str) -> str:
    """雪球正文：表格每行压成一条，避免手机端多行指标糊成一段。"""
    return "\n\n".join(body_to_opus_lines(body))


def body_to_opus_lines(body: str) -> list[str]:
    """B 站 opus 正文：普通段落保留，表格转紧凑单行列表。"""
    out: list[str] = []
    for block in split_body_blocks(body):
        lines = block.splitlines()
        if lines and is_table_line(lines[0]):
            out.extend(markdown_table_to_compact_lines(block))
        else:
            text = block.strip()
            if text:
                out.append(text)
    return out


def format_headline_plain(headline: str) -> str:
    h = headline.strip()
    if not h:
        return ""
    if h.startswith("【") and h.endswith("】"):
        return h
    return f"【{h}】"
