"""Logical IDs for knowledge-product objects（移植自 newsfc ``app/logical_id.py``）。

两条铁律照搬：**版本不进 ID**（版本只在 frontmatter 和修订号里）、**文件名 =
完整逻辑 ID**（保留空格）。newsfc 的网元隔离位 ``nf`` 在这里换成制品 slug：

- ``product`` scope（3 段）：``{product}@{Type}@{local}``
- ``cross`` scope（2 段）：``{Type}@{slug}``

见 52号计划 §5.3。
"""
from __future__ import annotations

from typing import Any

SEPARATOR = "@"
#: 复合行标识的拼接符。``local_from: [model, product_version]`` → ``"M8 V300R022"``
LOCAL_JOINER = " "


def segment_count(object_id: str) -> int:
    return object_id.count(SEPARATOR) + 1


def is_product_scoped(object_id: str) -> bool:
    return segment_count(object_id) == 3


def split_id(object_id: str) -> tuple[str | None, str, str]:
    """→ ``(product, type, local)``；2 段时 product 为 None。

    ``local`` 内的空格原样保留——它是标识的一部分，不做归一化。
    """
    parts = object_id.split(SEPARATOR)
    if len(parts) == 3:
        return parts[0], parts[1], parts[2]
    if len(parts) == 2:
        return None, parts[0], parts[1]
    raise ValueError(f"非法逻辑 ID（既非 2 段也非 3 段）: {object_id!r}")


def build_local(frontmatter: dict[str, Any], local_from: list[str]) -> str:
    """按 registry 的 ``local_from`` 拼 local 段（52号 §5.3 F4）。

    **平台拼，不让 Agent 拼**——Agent 自己拼会对同一行产生两种写法。字段值取自
    结构化 ``fields`` 块。
    """
    fields = frontmatter.get("fields") or {}
    parts = []
    for key in local_from:
        entry = fields.get(key)
        if not isinstance(entry, dict) or entry.get("value") is None:
            raise ValueError(f"local_from 字段 {key!r} 缺值，无法拼 local 段")
        parts.append(str(entry["value"]))
    return LOCAL_JOINER.join(parts)
