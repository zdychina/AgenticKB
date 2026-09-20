"""In-memory ``KnowledgeProductRepository``.

与 ``file_management/repositories_memory.py`` 同一个用意：服务层测试跑内存实现，
PG 实现只在有真库时做 smoke（``test_repository_pg.py``）。两边共享同一组方法签名，
契约漂了会在服务层测试里立刻暴露。

行为对齐 018 的约束：``publish_revision`` 维持「一个制品至多一个已发布修订」，
``write_draft_revision`` 的整批写要么全成要么全不成。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Sequence

from knowledge_mining.mining.knowledge_product.evidence import EvidenceRef
from knowledge_mining.mining.knowledge_product.models import Edge
from knowledge_mining.mining.knowledge_product.repository import ObjectRow, ScopeItem


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class MemoryKnowledgeProductRepository:
    def __init__(self) -> None:
        self._products: dict[str, dict[str, Any]] = {}
        self._definitions: dict[tuple[str, int], dict[str, Any]] = {}
        self._scope: dict[tuple[str, int], list[ScopeItem]] = {}
        self._revisions: dict[tuple[str, int], dict[str, Any]] = {}
        self._objects: dict[tuple[str, int], list[ObjectRow]] = {}
        self._edges: dict[tuple[str, int], list[Edge]] = {}
        self._evidence: dict[tuple[str, int], list[EvidenceRef]] = {}
        self._reviews: list[dict[str, Any]] = []
        self._trials: list[dict[str, Any]] = []
        self._edits: list[dict[str, Any]] = []

    # ---------------------------------------------------------------- 制品

    async def create_product(
        self,
        *,
        product_id: str,
        product_type: str,
        name: str,
        owner: str,
        purpose: str | None,
        fields: dict[str, Any],
        object_rules: dict[str, Any],
        examples: list[Any],
        trial_questions: list[Any],
        scope_items: Sequence[ScopeItem],
    ) -> dict[str, Any]:
        if product_id in self._products:
            raise ValueError(f"制品已存在: {product_id!r}")
        now = _utcnow()
        product = {
            "id": product_id,
            "product_type": product_type,
            "name": name,
            "purpose": purpose,
            "owner": owner,
            "lifecycle_status": "draft",
            "current_draft_revision": 1,
            "released_revision": None,
            "created_at": now,
            "updated_at": now,
        }
        self._products[product_id] = product
        self._definitions[(product_id, 1)] = {
            "id": f"kpd_{uuid.uuid4().hex}",
            "product_id": product_id,
            "definition_revision": 1,
            "fields_json": fields,
            "object_rules_json": object_rules,
            "examples_json": examples,
            "trial_questions_json": trial_questions,
            "created_at": now,
            "created_by": owner,
        }
        self._scope[(product_id, 1)] = list(scope_items)
        self._revisions[(product_id, 1)] = {
            "product_id": product_id,
            "revision_no": 1,
            "status": "draft",
            "definition_revision": 1,
            "created_at": now,
            "published_at": None,
        }
        self._objects[(product_id, 1)] = []
        self._edges[(product_id, 1)] = []
        self._evidence[(product_id, 1)] = []
        return dict(product)

    async def get_product(self, product_id: str) -> dict[str, Any] | None:
        product = self._products.get(product_id)
        return dict(product) if product else None

    async def list_products(self) -> list[dict[str, Any]]:
        return [dict(p) for p in self._products.values()]

    async def get_definition(
        self, product_id: str, definition_revision: int
    ) -> dict[str, Any] | None:
        definition = self._definitions.get((product_id, definition_revision))
        return dict(definition) if definition else None

    async def list_scope_items(
        self, product_id: str, definition_revision: int
    ) -> list[dict[str, Any]]:
        return [
            {
                "document_id": item.document_id,
                "snapshot_id": item.snapshot_id,
                "allowed_sections_json": item.allowed_sections,
            }
            for item in self._scope.get((product_id, definition_revision), [])
        ]

    # ---------------------------------------------------------------- 修订

    async def get_revision(self, product_id: str, revision_no: int) -> dict[str, Any] | None:
        revision = self._revisions.get((product_id, revision_no))
        return dict(revision) if revision else None

    async def list_revisions(self, product_id: str) -> list[dict[str, Any]]:
        return [
            dict(r) for key, r in sorted(self._revisions.items())
            if key[0] == product_id
        ]

    async def write_draft_revision(
        self,
        *,
        product_id: str,
        objects: Sequence[ObjectRow],
        edges: Sequence[Edge],
        evidence: Sequence[EvidenceRef],
    ) -> int:
        if product_id not in self._products:
            raise ValueError(f"制品不存在: {product_id!r}")
        existing = [key[1] for key in self._revisions if key[0] == product_id]
        revision_no = (max(existing) if existing else 0) + 1
        definition_revision = max(
            (r["definition_revision"] for key, r in self._revisions.items() if key[0] == product_id),
            default=1,
        )

        now = _utcnow()
        self._revisions[(product_id, revision_no)] = {
            "product_id": product_id,
            "revision_no": revision_no,
            "status": "draft",
            "definition_revision": definition_revision,
            "created_at": now,
            "published_at": None,
        }
        self._objects[(product_id, revision_no)] = list(objects)
        # (from, relation, to) 去重，与 kp_edges 的 PK 一致
        self._edges[(product_id, revision_no)] = list(
            {(e.from_id, e.relation, e.to): e for e in edges}.values()
        )
        self._evidence[(product_id, revision_no)] = list(evidence)

        self._products[product_id]["current_draft_revision"] = revision_no
        self._products[product_id]["updated_at"] = now
        return revision_no

    # ---------------------------------------------------------------- 对象与边

    async def list_objects(self, product_id: str, revision_no: int) -> list[dict[str, Any]]:
        return [
            {
                "product_id": product_id,
                "object_id": row.object_id,
                "revision_no": revision_no,
                "type": row.type,
                "layer": row.layer,
                "scope": row.scope,
                "name": row.name,
                "frontmatter_json": row.frontmatter,
                "storage_object_id": row.storage_object_id,
                "review_status": row.review_status,
            }
            for row in sorted(
                self._objects.get((product_id, revision_no), []),
                key=lambda r: (r.layer, r.object_id),
            )
        ]

    async def get_object(
        self, product_id: str, object_id: str, revision_no: int
    ) -> dict[str, Any] | None:
        for row in await self.list_objects(product_id, revision_no):
            if row["object_id"] == object_id:
                return row
        return None

    async def list_edges(self, product_id: str, revision_no: int) -> list[dict[str, Any]]:
        return [
            {"from_id": e.from_id, "relation": e.relation, "to_id": e.to}
            for e in sorted(
                self._edges.get((product_id, revision_no), []),
                key=lambda e: (e.from_id, e.relation, e.to),
            )
        ]

    async def list_backlinks(self, to_id: str) -> list[dict[str, Any]]:
        out = []
        for (product_id, revision_no), edges in sorted(self._edges.items()):
            for edge in edges:
                if edge.to == to_id:
                    out.append(
                        {
                            "product_id": product_id,
                            "revision_no": revision_no,
                            "from_id": edge.from_id,
                            "relation": edge.relation,
                        }
                    )
        return out

    async def list_evidence(
        self, product_id: str, revision_no: int, object_id: str | None = None
    ) -> list[dict[str, Any]]:
        return [
            {
                "object_id": ref.object_id,
                "field_name": ref.field_name,
                "document_id": ref.document_id,
                "snapshot_id": ref.snapshot_id,
                "segment_id": ref.segment_id,
                "anchor": ref.anchor,
                "quoted_value": ref.quoted_value,
            }
            for ref in self._evidence.get((product_id, revision_no), [])
            if object_id is None or ref.object_id == object_id
        ]

    # ---------------------------------------------------------------- 人审与试用

    async def set_review_status(
        self, product_id: str, object_id: str, revision_no: int, status: str
    ) -> None:
        rows = self._objects.get((product_id, revision_no), [])
        for index, row in enumerate(rows):
            if row.object_id == object_id:
                rows[index] = ObjectRow(**{**row.__dict__, "review_status": status})
                return

    async def record_review(
        self,
        *,
        product_id: str,
        revision_no: int,
        reviewer: str,
        decision: str,
        notes: str | None,
        per_object: dict[str, Any],
        instance_id: str | None = None,
    ) -> dict[str, Any]:
        row = {
            "id": f"kprv_{uuid.uuid4().hex}",
            "product_id": product_id,
            "revision_no": revision_no,
            "instance_id": instance_id,
            "reviewer": reviewer,
            "decision": decision,
            "notes": notes,
            "per_object_json": per_object,
            "created_at": _utcnow(),
        }
        self._reviews.append(row)
        return dict(row)

    async def list_reviews(self, product_id: str, revision_no: int) -> list[dict[str, Any]]:
        return [
            dict(r) for r in self._reviews
            if r["product_id"] == product_id and r["revision_no"] == revision_no
        ]

    async def record_trial(
        self,
        *,
        product_id: str,
        revision_no: int,
        question: str,
        answer: str | None,
        expected_points: str | None,
        verdict: str,
        comment: str | None,
        tried_by: str,
    ) -> dict[str, Any]:
        row = {
            "id": f"kpt_{uuid.uuid4().hex}",
            "product_id": product_id,
            "revision_no": revision_no,
            "question": question,
            "answer": answer,
            "expected_points": expected_points,
            "verdict": verdict,
            "comment": comment,
            "tried_by": tried_by,
            "created_at": _utcnow(),
        }
        self._trials.append(row)
        return dict(row)

    async def list_trials(self, product_id: str, revision_no: int) -> list[dict[str, Any]]:
        return [
            dict(r) for r in self._trials
            if r["product_id"] == product_id and r["revision_no"] == revision_no
        ]

    async def record_object_edit(
        self,
        *,
        product_id: str,
        revision_no: int,
        object_id: str,
        editor: str,
        reason: str | None,
        basis: str | None,
        before_md: str | None,
        after_md: str | None,
    ) -> None:
        self._edits.append(
            {
                "id": f"kpe_{uuid.uuid4().hex}",
                "product_id": product_id,
                "revision_no": revision_no,
                "object_id": object_id,
                "editor": editor,
                "reason": reason,
                "basis": basis,
                "before_md": before_md,
                "after_md": after_md,
                "created_at": _utcnow(),
            }
        )

    async def list_object_edits(
        self, product_id: str, object_id: str | None = None
    ) -> list[dict[str, Any]]:
        return [
            dict(r) for r in reversed(self._edits)
            if r["product_id"] == product_id
            and (object_id is None or r["object_id"] == object_id)
        ]

    # ---------------------------------------------------------------- 发布

    async def publish_revision(self, product_id: str, revision_no: int) -> None:
        now = _utcnow()
        for key, revision in self._revisions.items():
            if key[0] == product_id and revision["status"] == "published":
                revision["status"] = "superseded"
        target = self._revisions.get((product_id, revision_no))
        if target is None:
            raise ValueError(f"修订不存在: {product_id!r}#{revision_no}")
        target["status"] = "published"
        target["published_at"] = now
        self._products[product_id]["released_revision"] = revision_no
        self._products[product_id]["lifecycle_status"] = "published"
        self._products[product_id]["updated_at"] = now
