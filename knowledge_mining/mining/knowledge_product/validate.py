"""Registry-driven validation of knowledge-product objects.

返回 issue 清单而不是抛异常——``submit_creation_result`` 的契约是「返回哪些字段
不合格、哪些行无来源」，不是一个栈回溯（50号 §5.3）。

这里只做**结构**校验（registry 层）。两类校验不在这里：

- 业务字段与对象规则（``kp_product_definitions``）——那是第二级，随制品走；
- 证据是否真在票据允许范围内——要查 ``asset_raw_segments``，属于 ``agent_creation``。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from knowledge_mining.mining.knowledge_product.evidence import iter_field_evidence
from knowledge_mining.mining.knowledge_product.logical_id import build_local, split_id
from knowledge_mining.mining.knowledge_product.models import ProductObject
from knowledge_mining.mining.knowledge_product.registry import Registry


@dataclass(frozen=True)
class Issue:
    object_id: str
    code: str
    detail: str
    field: str | None = None


# issue code 常量，供调用方分流（拒绝 / 转待合并 / 仅告警）
UNKNOWN_TYPE = "unknown_type"
ID_SEGMENTS = "id_segments"
PRODUCT_MISMATCH = "product_mismatch"
LOCAL_MISMATCH = "local_mismatch"
MISSING_FRONTMATTER = "missing_frontmatter"
MISSING_SECTION = "missing_section"
EVIDENCE_INCOMPLETE = "evidence_incomplete"
VALUE_MISSING = "value_missing"


def validate_object(obj: ProductObject, registry: Registry) -> list[Issue]:
    issues: list[Issue] = []
    spec = registry.get(obj.type)
    if spec is None:
        return [Issue(obj.id, UNKNOWN_TYPE, f"类型 {obj.type!r} 不在 registry")]

    product, _type_name, local = split_id(obj.id)

    segments = obj.id.count("@") + 1
    if segments != spec.id_segments:
        issues.append(Issue(obj.id, ID_SEGMENTS, f"ID 应为 {spec.id_segments} 段，实为 {segments}"))

    if spec.is_product_scoped and product != obj.frontmatter.get("product"):
        issues.append(Issue(obj.id, PRODUCT_MISMATCH, "ID 首段与 frontmatter.product 不符"))

    for key in spec.frontmatter_required:
        if key not in obj.frontmatter:
            issues.append(Issue(obj.id, MISSING_FRONTMATTER, f"缺必填 frontmatter {key!r}", key))

    for section in spec.required_sections:
        if section not in obj.raw_md:
            issues.append(Issue(obj.id, MISSING_SECTION, f"缺必备章节 {section!r}"))

    # local 段由平台按 local_from 拼；对不上说明 Agent 自己拼了（52号 §5.3 F4）
    if spec.local_from:
        try:
            expected = build_local(obj.frontmatter, list(spec.local_from))
        except ValueError as exc:
            issues.append(Issue(obj.id, LOCAL_MISMATCH, str(exc)))
        else:
            if local != expected:
                issues.append(
                    Issue(obj.id, LOCAL_MISMATCH, f"local 段应为 {expected!r}，实为 {local!r}")
                )

    issues.extend(_validate_fields_block(obj, spec))
    return issues


def _validate_fields_block(obj: ProductObject, spec) -> list[Issue]:
    block = spec.fields_block
    if not block:
        return []

    issues: list[Issue] = []
    required_keys = set(block.get("evidence_required_keys", ()))

    for name, field in obj.fields.items():
        if not isinstance(field, dict):
            issues.append(Issue(obj.id, VALUE_MISSING, f"字段 {name!r} 不是结构化块", name))
            continue

        for evidence in iter_field_evidence(field):
            missing = required_keys - set(evidence)
            if missing:
                issues.append(
                    Issue(obj.id, EVIDENCE_INCOMPLETE, f"证据缺 {sorted(missing)}", name)
                )

        # 空值只有在登记了冲突时才允许——不许把冲突静默抹平成某一个值
        if block.get("value_required") and field.get("value") is None and not field.get("conflict"):
            issues.append(Issue(obj.id, VALUE_MISSING, "value 为空且未登记 conflict", name))

    return issues


def validate_batch(
    objects: Iterable[ProductObject], registry: Registry
) -> tuple[list[Issue], set[str]]:
    """→ ``(issues, dangling)``。

    悬挂边（指向本批不存在的 ID）**单独返回、不进 issues**：它是制作过程中的正常
    中间态，报告但不阻断提交（52号 A5）。
    """
    objects = list(objects)
    issues = [issue for obj in objects for issue in validate_object(obj, registry)]

    known = {obj.id for obj in objects}
    targets = {edge.to for obj in objects for edge in obj.edges}
    return issues, targets - known
