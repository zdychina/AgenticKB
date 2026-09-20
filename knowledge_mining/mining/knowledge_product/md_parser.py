"""Parse a knowledge-product md into frontmatter / body / edge section.

移植自 newsfc ``app/md_parser.py``，包括两处踩过坑的处理，原样保留：

1. **换行归一化**：源 md 可能是 CRLF 甚至 ``\\r\\r\\n``。不归一化的话残留 ``\\r``
   会被 markdown 渲染器当成额外空行，导致表头与分隔符之间插入空行 → GFM 表格
   整表判废。
2. **删表内空行**：GFM 表格遇空行即整表判废，而导出的产品文档普遍在表头/分隔符/
   数据行之间混入空行。只删「前后都是管道表格行」的空行，段落间正常空行保留。
"""
from __future__ import annotations

import re
from typing import Any

import yaml

# 优先用 libyaml 的 C 加载器（比纯 Python SafeLoader 快数倍）。建索引要对成千上万
# 份 md 各解析一次 YAML，值得用 C；没编译 C 扩展时退回纯 Python。
try:
    from yaml import CSafeLoader as _SAFE_LOADER
except ImportError:  # pragma: no cover - 取决于本机 PyYAML 是否带 C 扩展
    from yaml import SafeLoader as _SAFE_LOADER  # type: ignore[assignment]

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.S)
_EDGE_HEADER_RE = re.compile(r"\n##\s*边\s*\n", re.M)

EDGE_HEADING = "## 边"


def _is_table_row(line: str) -> bool:
    """是否为管道表格行：非空且首尾均为 ``|``。"""
    stripped = line.strip()
    return len(stripped) >= 2 and stripped.startswith("|") and stripped.endswith("|")


def _collapse_table_internal_blanks(text: str) -> str:
    """删除夹在两行管道表格行之间的空行。"""
    lines = text.split("\n")
    # next_is_row[i]：第 i 行之后首个非空行是否为表格行
    next_is_row = [False] * len(lines)
    following = False
    for i in range(len(lines) - 1, -1, -1):
        next_is_row[i] = following
        if lines[i].strip():
            following = _is_table_row(lines[i])

    out: list[str] = []
    for i, line in enumerate(lines):
        if not line.strip() and out and _is_table_row(out[-1]) and next_is_row[i]:
            continue
        out.append(line)
    return "\n".join(out)


def normalize_newlines(text: str) -> str:
    text = re.sub(r"\r+\n", "\n", text)  # 任意 \r 串 + \n → 单个 \n
    return text.replace("\r", "\n")  # 残留的裸 \r（老 Mac 换行）→ \n


def parse_md(text: str) -> tuple[dict[str, Any], str, str]:
    """→ ``(frontmatter, body_md, edge_section)``。

    无 frontmatter 时返回空 dict；无 ``## 边`` 时 edge_section 为空串。
    ``body_md`` 已剔除 frontmatter 与 ``## 边`` 段。
    """
    text = normalize_newlines(text)
    frontmatter: dict[str, Any] = {}
    body = text

    match = _FRONTMATTER_RE.match(text)
    if match:
        frontmatter = yaml.load(match.group(1), Loader=_SAFE_LOADER) or {}
        body = match.group(2)

    # 必须在抽 frontmatter 之后做，避免误伤 YAML
    body = _collapse_table_internal_blanks(body)

    prefixed = "\n" + body
    edge_match = _EDGE_HEADER_RE.search(prefixed)
    edge_section = ""
    if edge_match:
        cut = edge_match.start()
        edge_section = prefixed[cut:].lstrip("\n")
        body = prefixed[:cut].rstrip("\n")

    return frontmatter, body, edge_section
