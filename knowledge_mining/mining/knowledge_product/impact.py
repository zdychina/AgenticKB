"""Impact analysis: 资料变了、谁受影响（52号 P7，48号 §八）。

> 「某产品说明书更新了。制品详情提示『所引用文档有新版本』，列出直接引用它的
> 字段、关系或段落。用户选择更新，Agent 生成草稿，专家确认差异，再发布。」

**不需要新表**：文档→制品的直接关系已经在 ``kp_evidence``（每个字段都记了
``snapshot_id``），制品→制品的引用在 ``kp_edges``。这里只是把它们反着查。

48号 把变化分两类，本模块也分两类，因为处置方式不同：

| 变化 | 本模块怎么表达 | 谁来处置 |
|---|---|---|
| 新资料或新版本出现 | ``superseded``——旧快照被同文档的新快照取代 | 提示复核，旧依据是否仍适用由责任人按业务范围判断 |
| 内容被删除、权限收回或明确判定失效 | ``revoked`` / ``deprecated`` | 「不能只挂待办继续暴露」——消费面要能看见这个标记 |

首期只做**直接影响**。递归影响（制品 A 引用制品 B，B 的资料变了）按规模再扩展，
这条 48号 也是这么说的。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# 快照生命周期（008 的 CHECK）
READY = "READY"
DEPRECATED = "DEPRECATED"
REVOKED = "REVOKED"

# 影响类别
SUPERSEDED = "superseded"      # 同文档有更新的快照
DEPRECATED_SOURCE = "deprecated"
REVOKED_SOURCE = "revoked"

#: 需要「停止交付」而不只是「挂个待办」的类别（48号 §八 第二类）
BLOCKING = frozenset({REVOKED_SOURCE})


@dataclass(frozen=True)
class AffectedField:
    product_id: str
    revision_no: int
    object_id: str
    field_name: str
    document_id: str
    snapshot_id: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "product_id": self.product_id,
            "revision": self.revision_no,
            "object_id": self.object_id,
            "field": self.field_name,
            "document_id": self.document_id,
            "snapshot_id": self.snapshot_id,
        }


@dataclass(frozen=True)
class SourceAlert:
    """一条「你引用的资料变了」。"""

    kind: str
    snapshot_id: str
    document_id: str
    detail: str
    fields: tuple[AffectedField, ...]

    @property
    def blocking(self) -> bool:
        return self.kind in BLOCKING

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "blocking": self.blocking,
            "snapshot_id": self.snapshot_id,
            "document_id": self.document_id,
            "detail": self.detail,
            "affected_count": len(self.fields),
            # 只列前若干条：列出**哪些字段**受影响是为了让人判断，不是为了倒全量
            "affected": [f.as_dict() for f in self.fields[:20]],
        }


def classify_snapshot(row: dict[str, Any], *, has_newer: bool) -> tuple[str, str] | None:
    """一个被引用的快照现在是什么状态 → ``(kind, detail)``；健康则 None。

    先判失效再判过时：一份既被吊销、又有新版本的资料，要紧的是它已经不能用了。
    """
    status = (row.get("lifecycle_status") or READY).upper()
    if status == REVOKED:
        return REVOKED_SOURCE, "该资料已被撤回或权限被收回，引用它的内容不应继续对外交付"
    if status == DEPRECATED:
        return DEPRECATED_SOURCE, "该资料已标记废弃，请确认旧依据是否仍适用"
    if has_newer:
        return SUPERSEDED, "该文档已有更新的版本，请复核旧依据是否仍适用"
    return None


def build_alerts(
    evidence_rows: list[dict[str, Any]],
    snapshots: dict[str, dict[str, Any]],
    newer_documents: set[str],
) -> list[SourceAlert]:
    """把「证据引用」× 「快照现状」合成告警清单。

    ``newer_documents`` 是「这个 document_id 存在比被引用的那份更新的快照」的集合——
    由仓储算好传进来，这里不碰库。
    """
    grouped: dict[str, list[AffectedField]] = {}
    for row in evidence_rows:
        grouped.setdefault(row["snapshot_id"], []).append(
            AffectedField(
                product_id=row["product_id"],
                revision_no=int(row.get("revision_no") or 0),
                object_id=row["object_id"],
                field_name=row["field_name"],
                document_id=row["document_id"],
                snapshot_id=row["snapshot_id"],
            )
        )

    alerts: list[SourceAlert] = []
    for snapshot_id, fields in grouped.items():
        snapshot = snapshots.get(snapshot_id)
        if snapshot is None:
            # 快照整个不见了——比废弃更严重：连回源都做不到
            alerts.append(SourceAlert(
                kind=REVOKED_SOURCE,
                snapshot_id=snapshot_id,
                document_id=fields[0].document_id,
                detail="该资料在平台中已不存在，引用它的内容无法回源",
                fields=tuple(fields),
            ))
            continue

        verdict = classify_snapshot(
            snapshot, has_newer=fields[0].document_id in newer_documents
        )
        if verdict is None:
            continue
        kind, detail = verdict
        alerts.append(SourceAlert(
            kind=kind,
            snapshot_id=snapshot_id,
            document_id=fields[0].document_id,
            detail=detail,
            fields=tuple(fields),
        ))

    # 要停止交付的排前面——人先看见的应该是最要紧的那条
    alerts.sort(key=lambda a: (not a.blocking, a.kind, a.snapshot_id))
    return alerts
