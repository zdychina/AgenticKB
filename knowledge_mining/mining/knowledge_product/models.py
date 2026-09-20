"""Carrier types for knowledge-product objects (52号计划 §5)。

制品的载体是 md：frontmatter 承载身份与结构化字段，正文承载给人读的呈现，
``## 边`` 段承载关系类型。这里只定义内存形态，持久化见
``databases/asset_core/schemas/018_knowledge_product.sql``。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# 无关系类型的边：正文内联 [[ID]] 但 ## 边 段未标注（52号 §6.3 规则 1）
MENTIONS = "mentions"


@dataclass(frozen=True)
class Edge:
    """一条有向边。按 ``(from_id, relation, to)`` 去重（52号 §6.3 规则 2）。

    反向边**不**在这里——它由 ``kp_edges`` 索引反查提供，不写回 md
    （52号 §6.3 规则 3）。
    """

    from_id: str
    relation: str
    to: str

    @property
    def is_typed(self) -> bool:
        return self.relation != MENTIONS


@dataclass(frozen=True)
class ProductObject:
    """制品内的一个 md 对象。

    ``scope`` 决定 ID 分段与归属：``product`` 是制品内对象（3 段，首段为制品
    slug），``cross`` 是跨制品共享对象（2 段）。见 52号 §5.3。
    """

    id: str
    type: str
    layer: str
    scope: str
    frontmatter: dict[str, Any]
    body_md: str
    edge_section: str
    raw_md: str
    edges: list[Edge] = field(default_factory=list)

    @property
    def product(self) -> str | None:
        """制品 slug；``cross`` scope 的对象不属于任何单一制品，返回 None。"""
        return self.frontmatter.get("product") if self.scope == "product" else None

    @property
    def name(self) -> str | None:
        return self.frontmatter.get("name")

    @property
    def fields(self) -> dict[str, Any]:
        """结构化字段块。校验与回源看它，不看正文（52号 §5.4 F2）。"""
        return self.frontmatter.get("fields") or {}
