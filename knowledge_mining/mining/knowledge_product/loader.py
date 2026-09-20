"""Turn raw md into a :class:`ProductObject`.

解析 → 查 registry 定 scope/layer → 全文建边。**不做校验**：校验在
``validate.py``，因为 ``submit_creation_result`` 要的是「哪些字段不合格」的清单，
不是一个异常（50号 §5.3）。
"""
from __future__ import annotations

from knowledge_mining.mining.knowledge_product.edges import build_edges
from knowledge_mining.mining.knowledge_product.md_parser import parse_md
from knowledge_mining.mining.knowledge_product.models import ProductObject
from knowledge_mining.mining.knowledge_product.registry import SCOPE_CROSS, Registry


def build_object(raw_md: str, registry: Registry) -> ProductObject:
    """→ :class:`ProductObject`。

    ``id`` 缺失即抛——没有身份就没有对象，这一条不进 issue 清单。未知类型不抛，
    留给 ``validate`` 报 ``unknown_type``（scope/layer 退化为 cross/未知）。
    """
    frontmatter, body, edge_section = parse_md(raw_md)

    object_id = frontmatter.get("id")
    if not object_id:
        raise ValueError("md 的 frontmatter 缺 id")
    type_name = frontmatter.get("type") or ""

    spec = registry.get(type_name)
    scope = spec.scope if spec else SCOPE_CROSS
    layer = spec.layer if spec else ""

    return ProductObject(
        id=object_id,
        type=type_name,
        layer=layer,
        scope=scope,
        frontmatter=frontmatter,
        body_md=body,
        edge_section=edge_section,
        raw_md=raw_md,
        edges=build_edges(body, edge_section, object_id),
    )
