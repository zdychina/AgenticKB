"""Where an object's md lives（移植自 newsfc ``app/classify.py``）。

返回的是**对象存储里的相对键**，不是本地文件路径——正文存 MinIO，PG 只留索引
（52号 §5.2）。newsfc 的 ``{layer}/{nf}/{version}/`` 布局里，nf 换成制品 slug，
version 不再进路径（版本是制品修订，挂在 ``kp_objects.revision_no`` 上）。

文件名恒为 ``{完整逻辑 ID}.md``，空格原样保留。
"""
from __future__ import annotations

from typing import Any

from knowledge_mining.mining.knowledge_product.logical_id import split_id
from knowledge_mining.mining.knowledge_product.registry import Registry, TypeSpec


def object_filename(object_id: str) -> str:
    return f"{object_id}.md"


def classify(object_id: str, registry: Registry, frontmatter: dict[str, Any]) -> tuple[str, str]:
    """→ ``(相对目录, 文件名)``。"""
    product, type_name, _local = split_id(object_id)
    spec: TypeSpec = registry.require(type_name)
    filename = object_filename(object_id)

    if spec.is_product_scoped:
        # 制品 slug 以 ID 段 0 为准（权威），frontmatter.product 只用于校验
        if not product:
            raise ValueError(f"{object_id!r}: product scope 的 ID 首段为空")
        return f"{product}/{spec.layer}", filename

    parts = [spec.layer]
    for field_name in spec.path_fields:
        value = frontmatter.get(field_name)
        if not value:
            raise ValueError(f"跨制品类型 {type_name} 缺 frontmatter.{field_name}（id={object_id!r}）")
        parts.append(str(value))
    return "/".join(parts), filename


def storage_key(object_id: str, registry: Registry, frontmatter: dict[str, Any]) -> str:
    directory, filename = classify(object_id, registry, frontmatter)
    return f"{directory}/{filename}"
