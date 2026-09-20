"""Build edges from a knowledge-product md.

移植自 newsfc ``app/edges.py``，但**改了两条规则**（52号 §6.3）：

1. **全文扫，不只扫 ``## 边`` 段。** newsfc 的索引只扫边段，而它的 ``references``
   又是全文扫——同一份 md 两个口径。这里统一成全文扫 ``[[ID]]`` 建边，``## 边``
   段只负责标注关系类型；段外的内联引用记为 ``mentions``。
2. **按 (from, to, relation) 去重。** 统一成全文扫之后，同一目标常在正文内联和
   ``## 边`` 各出现一次；不去重会让边数虚增（样品实测 22 → 29）。同一目标同时
   出现在两处时，**有类型的那条胜出**，不额外记一条 ``mentions``。
"""
from __future__ import annotations

import re

from knowledge_mining.mining.knowledge_product.models import MENTIONS, Edge

# ``- 关系: <rest>``，rest 里可能有多个 [[...]]
_EDGE_LINE_RE = re.compile(r"^\s*-\s*(?P<relation>[^:]+?):\s*(?P<rest>.+?)\s*$", re.M)
# 行内 [[target]]；别名语法 [[target|alias]] 取 target
_WIKILINK_RE = re.compile(r"\[\[([^\]\|]+?)(?:\|[^\]]*)?\]\]")


def iter_wikilinks(text: str) -> list[str]:
    """按出现顺序列出去重后的 ``[[ID]]`` 目标。"""
    return list(dict.fromkeys(target.strip() for target in _WIKILINK_RE.findall(text)))


def parse_typed_edges(edge_section: str, from_id: str) -> list[Edge]:
    """只解析 ``## 边`` 段里 ``- 关系: [[目标]]`` 形式的有类型边。

    支持一行多目标：``- 复用步骤: [[A]], [[B]]`` 建两条同名关系的边。
    """
    if not edge_section:
        return []
    out: list[Edge] = []
    seen: set[tuple[str, str, str]] = set()
    for line in _EDGE_LINE_RE.finditer(edge_section):
        relation = line.group("relation").strip()
        for target in iter_wikilinks(line.group("rest")):
            key = (from_id, relation, target)
            if key in seen:
                continue
            seen.add(key)
            out.append(Edge(from_id=from_id, relation=relation, to=target))
    return out


def build_edges(body_md: str, edge_section: str, from_id: str) -> list[Edge]:
    """全文建边：``## 边`` 段给类型，段外的内联引用记为 ``mentions``。"""
    typed = parse_typed_edges(edge_section, from_id)
    typed_targets = {edge.to for edge in typed}

    mentions = [
        Edge(from_id=from_id, relation=MENTIONS, to=target)
        for target in iter_wikilinks(body_md)
        if target not in typed_targets
    ]
    return typed + mentions
