"""Pull field-level evidence out of an object's structured ``fields`` block.

逐字段回源的载体：``kp_evidence`` 一行 = 一个对象的一个字段的一条出处。冲突字段
的每个候选值各自带出处，也一并抽出来——「两份资料口径不同」这件事本身要可回源。

``segment_id`` 是硬校验的支点（50号 §2.2 修订、52号 F3）：它外键到
``asset_raw_segments``，平台据此判定「证据真在票据允许范围内」。``anchor`` 是自由
文本，只用于页面打开正确位置。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Iterator

from knowledge_mining.mining.knowledge_product.models import ProductObject


@dataclass(frozen=True)
class EvidenceRef:
    object_id: str
    field_name: str
    document_id: str
    snapshot_id: str
    segment_id: str
    anchor: str | None = None
    quoted_value: str | None = None


def iter_field_evidence(field: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """一个字段的全部证据——正常值的，加上冲突候选各自的。"""
    yield from (field.get("evidence") or [])
    for candidate in (field.get("conflict") or {}).get("candidates", []):
        evidence = candidate.get("evidence")
        if isinstance(evidence, dict):
            yield evidence
        elif isinstance(evidence, list):
            yield from evidence


def extract_evidence(obj: ProductObject) -> list[EvidenceRef]:
    """→ 该对象的全部证据引用。

    三个 ID 缺任何一个的证据**不产出**——它不成立，落库也没法回源。这类残缺由
    ``validate`` 报 ``evidence_incomplete``，由调用方决定拒收还是转待合并。
    """
    out: list[EvidenceRef] = []
    seen: set[tuple[str, str, str]] = set()

    for name, field in obj.fields.items():
        if not isinstance(field, dict):
            continue
        for evidence in iter_field_evidence(field):
            document_id = evidence.get("document_id")
            snapshot_id = evidence.get("snapshot_id")
            segment_id = evidence.get("segment_id")
            if not (document_id and snapshot_id and segment_id):
                continue
            key = (name, snapshot_id, segment_id)
            if key in seen:
                continue
            seen.add(key)
            out.append(
                EvidenceRef(
                    object_id=obj.id,
                    field_name=name,
                    document_id=document_id,
                    snapshot_id=snapshot_id,
                    segment_id=segment_id,
                    anchor=evidence.get("anchor"),
                    quoted_value=evidence.get("quoted_value"),
                )
            )
    return out


def extract_batch(objects: Iterable[ProductObject]) -> list[EvidenceRef]:
    return [ref for obj in objects for ref in extract_evidence(obj)]
