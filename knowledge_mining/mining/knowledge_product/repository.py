"""PostgreSQL repository for the ``kp_*`` tables（018 DDL）。

风格对齐 ``mining/kb/db.py``：裸参数化 SQL 走共享 async 池、``dict_row``、TEXT id、
ISO 时间戳、JSONB 走 ``::jsonb`` 强转。每个方法自己开连接（一个逻辑事务）；写一整
个修订的方法在单连接单事务内完成——对象、边、证据必须一起成功或一起失败
（50号 §5.3 的原子性要求）。
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Sequence

from psycopg.rows import dict_row

from knowledge_mining.mining.knowledge_product.evidence import EvidenceRef
from knowledge_mining.mining.knowledge_product.models import Edge


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(obj: Any) -> str:
    return json.dumps(obj if obj is not None else {}, ensure_ascii=False, separators=(",", ":"))


@dataclass(frozen=True)
class ObjectRow:
    """写入 ``kp_objects`` 的一行。正文不在这里——它在对象存储里。"""

    object_id: str
    type: str
    layer: str
    scope: str
    name: str | None
    frontmatter: dict[str, Any]
    storage_object_id: str | None
    review_status: str = "agent_submitted"


@dataclass(frozen=True)
class ScopeItem:
    document_id: str
    snapshot_id: str
    allowed_sections: list[str] | None = None


class KnowledgeProductRepository:
    def __init__(self, pool: Any) -> None:
        self._pool = pool

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
        """建制品 + 定义修订 1 + 资料范围 + 草稿修订 1，单事务。"""
        now = _utcnow()
        async with self._pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """INSERT INTO kp_products
                       (id, product_type, name, purpose, owner, lifecycle_status,
                        current_draft_revision, released_revision, created_at, updated_at)
                       VALUES (%s, %s, %s, %s, %s, 'draft', 1, NULL, %s, %s)
                       RETURNING *""",
                    (product_id, product_type, name, purpose, owner, now, now),
                )
                product = await cur.fetchone()

                await cur.execute(
                    """INSERT INTO kp_product_definitions
                       (id, product_id, definition_revision, fields_json, object_rules_json,
                        examples_json, trial_questions_json, created_at, created_by)
                       VALUES (%s, %s, 1, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s)""",
                    (
                        _new_id("kpd"), product_id, _json(fields), _json(object_rules),
                        _json(examples), _json(trial_questions), now, owner,
                    ),
                )

                for item in scope_items:
                    await cur.execute(
                        """INSERT INTO kp_scope_items
                           (id, product_id, definition_revision, document_id, snapshot_id,
                            allowed_sections_json, created_at)
                           VALUES (%s, %s, 1, %s, %s, %s::jsonb, %s)""",
                        (
                            _new_id("kps"), product_id, item.document_id, item.snapshot_id,
                            json.dumps(item.allowed_sections) if item.allowed_sections else None,
                            now,
                        ),
                    )

                await cur.execute(
                    """INSERT INTO kp_revisions
                       (id, product_id, revision_no, status, definition_revision, created_at)
                       VALUES (%s, %s, 1, 'draft', 1, %s)""",
                    (_new_id("kpr"), product_id, now),
                )
        return dict(product)

    async def get_product(self, product_id: str) -> dict[str, Any] | None:
        async with self._pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute("SELECT * FROM kp_products WHERE id = %s", (product_id,))
                row = await cur.fetchone()
        return dict(row) if row else None

    async def list_products(self) -> list[dict[str, Any]]:
        async with self._pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute("SELECT * FROM kp_products ORDER BY created_at DESC")
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_definition(
        self, product_id: str, definition_revision: int
    ) -> dict[str, Any] | None:
        async with self._pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """SELECT * FROM kp_product_definitions
                       WHERE product_id = %s AND definition_revision = %s""",
                    (product_id, definition_revision),
                )
                row = await cur.fetchone()
        return dict(row) if row else None

    async def list_scope_items(
        self, product_id: str, definition_revision: int
    ) -> list[dict[str, Any]]:
        async with self._pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """SELECT * FROM kp_scope_items
                       WHERE product_id = %s AND definition_revision = %s
                       ORDER BY document_id""",
                    (product_id, definition_revision),
                )
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # ---------------------------------------------------------------- 修订

    async def get_revision(self, product_id: str, revision_no: int) -> dict[str, Any] | None:
        async with self._pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    "SELECT * FROM kp_revisions WHERE product_id = %s AND revision_no = %s",
                    (product_id, revision_no),
                )
                row = await cur.fetchone()
        return dict(row) if row else None

    async def list_revisions(self, product_id: str) -> list[dict[str, Any]]:
        async with self._pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    "SELECT * FROM kp_revisions WHERE product_id = %s ORDER BY revision_no",
                    (product_id,),
                )
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def write_draft_revision(
        self,
        *,
        product_id: str,
        objects: Sequence[ObjectRow],
        edges: Sequence[Edge],
        evidence: Sequence[EvidenceRef],
    ) -> int:
        """开一个新的草稿修订并把整批对象/边/证据写进去，单事务。

        新修订号 = 当前最大修订号 + 1。**不做增量**——每个草稿修订是一次完整快照，
        修订之间的差异由 ``diff`` 算出来，而不是靠增量记录拼。
        """
        now = _utcnow()
        async with self._pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """SELECT COALESCE(MAX(revision_no), 0) AS max_no, MAX(definition_revision) AS def_no
                       FROM kp_revisions WHERE product_id = %s""",
                    (product_id,),
                )
                head = await cur.fetchone()
                revision_no = int(head["max_no"]) + 1
                definition_revision = int(head["def_no"] or 1)

                await cur.execute(
                    """INSERT INTO kp_revisions
                       (id, product_id, revision_no, status, definition_revision, created_at)
                       VALUES (%s, %s, %s, 'draft', %s, %s)""",
                    (_new_id("kpr"), product_id, revision_no, definition_revision, now),
                )

                for obj in objects:
                    await cur.execute(
                        """INSERT INTO kp_objects
                           (product_id, object_id, revision_no, type, layer, scope, name,
                            frontmatter_json, storage_object_id, review_status, created_at)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s)""",
                        (
                            product_id, obj.object_id, revision_no, obj.type, obj.layer,
                            obj.scope, obj.name, _json(obj.frontmatter),
                            obj.storage_object_id, obj.review_status, now,
                        ),
                    )

                for edge in edges:
                    await cur.execute(
                        """INSERT INTO kp_edges
                           (product_id, revision_no, from_id, relation, to_id)
                           VALUES (%s, %s, %s, %s, %s)
                           ON CONFLICT DO NOTHING""",
                        (product_id, revision_no, edge.from_id, edge.relation, edge.to),
                    )

                for ref in evidence:
                    await cur.execute(
                        """INSERT INTO kp_evidence
                           (id, product_id, revision_no, object_id, field_name, document_id,
                            snapshot_id, segment_id, anchor, quoted_value, created_at)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                        (
                            _new_id("kpe"), product_id, revision_no, ref.object_id,
                            ref.field_name, ref.document_id, ref.snapshot_id,
                            ref.segment_id, ref.anchor, ref.quoted_value, now,
                        ),
                    )

                await cur.execute(
                    "UPDATE kp_products SET current_draft_revision = %s, updated_at = %s WHERE id = %s",
                    (revision_no, now, product_id),
                )
        return revision_no

    # ---------------------------------------------------------------- 对象与边

    async def list_objects(self, product_id: str, revision_no: int) -> list[dict[str, Any]]:
        async with self._pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """SELECT * FROM kp_objects
                       WHERE product_id = %s AND revision_no = %s
                       ORDER BY layer, object_id""",
                    (product_id, revision_no),
                )
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_object(
        self, product_id: str, object_id: str, revision_no: int
    ) -> dict[str, Any] | None:
        async with self._pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """SELECT * FROM kp_objects
                       WHERE product_id = %s AND object_id = %s AND revision_no = %s""",
                    (product_id, object_id, revision_no),
                )
                row = await cur.fetchone()
        return dict(row) if row else None

    async def list_edges(self, product_id: str, revision_no: int) -> list[dict[str, Any]]:
        async with self._pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """SELECT from_id, relation, to_id FROM kp_edges
                       WHERE product_id = %s AND revision_no = %s
                       ORDER BY from_id, relation, to_id""",
                    (product_id, revision_no),
                )
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def list_backlinks(self, to_id: str) -> list[dict[str, Any]]:
        """反向边：谁引用了 ``to_id``。

        F5 的落点——反向边**不**写回 md，按 ``idx_kp_edges_to`` 反查本表即可。
        跨制品共享对象被别的制品引用时，这是唯一能查全的地方。
        """
        async with self._pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """SELECT product_id, revision_no, from_id, relation FROM kp_edges
                       WHERE to_id = %s
                       ORDER BY product_id, revision_no, from_id""",
                    (to_id,),
                )
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def list_evidence(
        self, product_id: str, revision_no: int, object_id: str | None = None
    ) -> list[dict[str, Any]]:
        sql = """SELECT * FROM kp_evidence
                 WHERE product_id = %s AND revision_no = %s"""
        params: list[Any] = [product_id, revision_no]
        if object_id is not None:
            sql += " AND object_id = %s"
            params.append(object_id)
        sql += " ORDER BY object_id, field_name"

        async with self._pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(sql, tuple(params))
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # ---------------------------------------------------------------- 已发布面
    # 消费只认发布修订：JOIN 到 kp_products.released_revision 就是「对外服务的那一份」。
    # 草稿失败或新版未发布时，旧的可用版本继续服务（48号 §七）。

    _PUBLISHED_JOIN = """
        FROM kp_objects o
        JOIN kp_products p
          ON p.id = o.product_id AND p.released_revision = o.revision_no
    """

    async def list_published_products(self) -> list[dict[str, Any]]:
        async with self._pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """SELECT p.*, (
                           SELECT count(*) FROM kp_objects o
                           WHERE o.product_id = p.id AND o.revision_no = p.released_revision
                       ) AS object_count
                       FROM kp_products p
                       WHERE p.released_revision IS NOT NULL
                       ORDER BY p.updated_at DESC""",
                )
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_published_objects(self, object_ids: Sequence[str]) -> list[dict[str, Any]]:
        """按对象 ID 批量取已发布行。跨制品共享对象可能命中多个制品，全部返回。"""
        if not object_ids:
            return []
        async with self._pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    "SELECT o.*, p.name AS product_name" + self._PUBLISHED_JOIN
                    + " WHERE o.object_id = ANY(%s) ORDER BY o.object_id, o.product_id",
                    (list(object_ids),),
                )
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def list_published_objects(
        self, *, product_id: str | None = None, type_name: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = "SELECT o.*, p.name AS product_name" + self._PUBLISHED_JOIN
        clauses: list[str] = []
        params: list[Any] = []
        if product_id:
            clauses.append("o.product_id = %s")
            params.append(product_id)
        if type_name:
            clauses.append("o.type = %s")
            params.append(type_name)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY o.product_id, o.layer, o.object_id"

        async with self._pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(sql, tuple(params))
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # ---------------------------------------------------------------- 人审与试用

    async def set_review_status(
        self, product_id: str, object_id: str, revision_no: int, status: str
    ) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                """UPDATE kp_objects SET review_status = %s
                   WHERE product_id = %s AND object_id = %s AND revision_no = %s""",
                (status, product_id, object_id, revision_no),
            )

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
        now = _utcnow()
        async with self._pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """INSERT INTO kp_reviews
                       (id, product_id, revision_no, instance_id, reviewer, decision,
                        notes, per_object_json, created_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
                       RETURNING *""",
                    (
                        _new_id("kprv"), product_id, revision_no, instance_id, reviewer,
                        decision, notes, _json(per_object), now,
                    ),
                )
                row = await cur.fetchone()
        return dict(row)

    async def list_reviews(self, product_id: str, revision_no: int) -> list[dict[str, Any]]:
        async with self._pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """SELECT * FROM kp_reviews
                       WHERE product_id = %s AND revision_no = %s ORDER BY created_at""",
                    (product_id, revision_no),
                )
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

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
        now = _utcnow()
        async with self._pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """INSERT INTO kp_trials
                       (id, product_id, revision_no, question, answer, expected_points,
                        verdict, comment, tried_by, created_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       RETURNING *""",
                    (
                        _new_id("kpt"), product_id, revision_no, question, answer,
                        expected_points, verdict, comment, tried_by, now,
                    ),
                )
                row = await cur.fetchone()
        return dict(row)

    async def list_trials(self, product_id: str, revision_no: int) -> list[dict[str, Any]]:
        async with self._pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """SELECT * FROM kp_trials
                       WHERE product_id = %s AND revision_no = %s ORDER BY created_at""",
                    (product_id, revision_no),
                )
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

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
        async with self._pool.connection() as conn:
            await conn.execute(
                """INSERT INTO kp_object_edits
                   (id, product_id, revision_no, object_id, editor, reason, basis,
                    before_md, after_md, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    _new_id("kpe"), product_id, revision_no, object_id, editor,
                    reason, basis, before_md, after_md, _utcnow(),
                ),
            )

    async def list_object_edits(
        self, product_id: str, object_id: str | None = None
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM kp_object_edits WHERE product_id = %s"
        params: list[Any] = [product_id]
        if object_id is not None:
            sql += " AND object_id = %s"
            params.append(object_id)
        sql += " ORDER BY created_at DESC"

        async with self._pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(sql, tuple(params))
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # ---------------------------------------------------------------- 发布

    async def publish_revision(self, product_id: str, revision_no: int) -> None:
        """把一个草稿修订转为发布修订，旧的发布修订降为 superseded。

        「一个制品至多一个已发布修订」由 018 的部分唯一索引兜底——这里先降旧版
        再升新版，顺序反了会撞索引（那正是想要的：宁可报错也不要两个发布版）。
        """
        now = _utcnow()
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """UPDATE kp_revisions SET status = 'superseded'
                       WHERE product_id = %s AND status = 'published'""",
                    (product_id,),
                )
                await cur.execute(
                    """UPDATE kp_revisions SET status = 'published', published_at = %s
                       WHERE product_id = %s AND revision_no = %s""",
                    (now, product_id, revision_no),
                )
                await cur.execute(
                    """UPDATE kp_products SET released_revision = %s,
                           lifecycle_status = 'published', updated_at = %s
                       WHERE id = %s""",
                    (revision_no, now, product_id),
                )
