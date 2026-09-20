"""PostgreSQL repository for the creation face（019 DDL）。

风格同 ``knowledge_product/repository.py``：裸参数化 SQL、``dict_row``、TEXT id、
ISO 时间戳。票据只按哈希查——明文不落库、不进 SQL 日志。
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Sequence

from psycopg.rows import dict_row

from knowledge_mining.mining.agent_creation.scope import SegmentLocation


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class CreationRepository:
    def __init__(self, pool: Any) -> None:
        self._pool = pool

    # ---------------------------------------------------------------- 实例

    async def create_instance(
        self,
        *,
        product_id: str,
        definition_revision: int,
        base_draft_revision: int,
        created_by: str,
    ) -> dict[str, Any]:
        now = _utcnow()
        async with self._pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """INSERT INTO kp_creation_instances
                       (id, product_id, definition_revision, base_draft_revision,
                        status, created_by, created_at, updated_at)
                       VALUES (%s, %s, %s, %s, 'pending', %s, %s, %s)
                       RETURNING *""",
                    (
                        _new_id("kpi"), product_id, definition_revision,
                        base_draft_revision, created_by, now, now,
                    ),
                )
                row = await cur.fetchone()
        return dict(row)

    async def get_instance(self, instance_id: str) -> dict[str, Any] | None:
        async with self._pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    "SELECT * FROM kp_creation_instances WHERE id = %s", (instance_id,)
                )
                row = await cur.fetchone()
        return dict(row) if row else None

    async def list_instances(self, product_id: str) -> list[dict[str, Any]]:
        async with self._pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """SELECT * FROM kp_creation_instances
                       WHERE product_id = %s ORDER BY created_at DESC""",
                    (product_id,),
                )
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def set_instance_status(self, instance_id: str, status: str) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                "UPDATE kp_creation_instances SET status = %s, updated_at = %s WHERE id = %s",
                (status, _utcnow(), instance_id),
            )

    async def bind_harness_session(self, instance_id: str, session_id: str) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                """UPDATE kp_creation_instances
                   SET harness_session_id = %s, updated_at = %s WHERE id = %s""",
                (session_id, _utcnow(), instance_id),
            )

    # ---------------------------------------------------------------- 票据

    async def insert_ticket(
        self,
        *,
        ticket_id: str,
        instance_id: str,
        product_id: str,
        token_hash: str,
        expires_at: str,
    ) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                """INSERT INTO kp_task_tickets
                   (id, instance_id, product_id, token_hash, status, issued_at, expires_at)
                   VALUES (%s, %s, %s, %s, 'active', %s, %s)""",
                (ticket_id, instance_id, product_id, token_hash, _utcnow(), expires_at),
            )

    async def get_ticket_by_hash(self, token_hash: str) -> dict[str, Any] | None:
        async with self._pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    "SELECT * FROM kp_task_tickets WHERE token_hash = %s", (token_hash,)
                )
                row = await cur.fetchone()
        return dict(row) if row else None

    async def revoke_tickets(self, instance_id: str, reason: str) -> int:
        """撤销一个实例的全部在用票据。→ 撤销条数。"""
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """UPDATE kp_task_tickets
                   SET status = 'revoked', revoked_at = %s, revoked_reason = %s
                   WHERE instance_id = %s AND status = 'active'""",
                (_utcnow(), reason, instance_id),
            )
            return cur.rowcount

    async def revoke_tickets_for_product(self, product_id: str, reason: str) -> int:
        """资料范围收缩 / 定义不兼容变更时，整个制品的票据一起撤。"""
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """UPDATE kp_task_tickets
                   SET status = 'revoked', revoked_at = %s, revoked_reason = %s
                   WHERE product_id = %s AND status = 'active'""",
                (_utcnow(), reason, product_id),
            )
            return cur.rowcount

    # ---------------------------------------------------------------- 提交

    async def get_submission(
        self, product_id: str, submission_id: str
    ) -> dict[str, Any] | None:
        async with self._pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """SELECT * FROM kp_submissions
                       WHERE product_id = %s AND submission_id = %s""",
                    (product_id, submission_id),
                )
                row = await cur.fetchone()
        return dict(row) if row else None

    async def claim_submission(
        self,
        *,
        product_id: str,
        submission_id: str,
        instance_id: str,
        based_on_draft_revision: int,
    ) -> bool:
        """抢占这次提交的处理权。→ True 表示抢到，False 表示已被处理或正在处理。

        先抢主键再干活：这样真并发（不只是网络重传）下也只有一方会写草稿，不需要
        额外的行锁或 advisory lock。
        """
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """INSERT INTO kp_submissions
                   (product_id, submission_id, instance_id, based_on_draft_revision,
                    outcome, accepted_count, rejected_count, receipt_json, created_at)
                   VALUES (%s, %s, %s, %s, 'pending', 0, 0, '{}'::jsonb, %s)
                   ON CONFLICT (product_id, submission_id) DO NOTHING""",
                (
                    product_id, submission_id, instance_id,
                    based_on_draft_revision, _utcnow(),
                ),
            )
            return cur.rowcount == 1

    async def finalize_submission(
        self,
        *,
        product_id: str,
        submission_id: str,
        outcome: str,
        written_revision: int | None,
        accepted_count: int,
        rejected_count: int,
        receipt: dict[str, Any],
    ) -> None:
        """把认领态的回执落成终态。只改 pending 的那条，不覆盖已终结的回执。"""
        async with self._pool.connection() as conn:
            await conn.execute(
                """UPDATE kp_submissions
                   SET outcome = %s, written_revision = %s, accepted_count = %s,
                       rejected_count = %s, receipt_json = %s::jsonb
                   WHERE product_id = %s AND submission_id = %s AND outcome = 'pending'""",
                (
                    outcome, written_revision, accepted_count, rejected_count,
                    json.dumps(receipt, ensure_ascii=False), product_id, submission_id,
                ),
            )

    async def list_product_submissions(self, product_id: str) -> list[dict[str, Any]]:
        async with self._pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """SELECT * FROM kp_submissions WHERE product_id = %s
                       ORDER BY created_at DESC""",
                    (product_id,),
                )
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def list_submissions(self, instance_id: str) -> list[dict[str, Any]]:
        async with self._pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """SELECT * FROM kp_submissions WHERE instance_id = %s
                       ORDER BY created_at""",
                    (instance_id,),
                )
                rows = await cur.fetchall()
        return [dict(r) for r in rows]


class PgSegmentLocator:
    """``SegmentLocator``：按 segment_id 查真实归属（快照 + 章节标题链）。

    这是「不信任 Agent 自报来源」的落点——snapshot 与章节都以本表为准。
    """

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def locate(self, segment_ids: Sequence[str]) -> dict[str, SegmentLocation]:
        ids = list(dict.fromkeys(segment_ids))
        if not ids:
            return {}
        from knowledge_mining.mining.agent_creation.scope import parse_section_titles

        async with self._pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """SELECT id, document_snapshot_id, section_path
                       FROM asset_raw_segments WHERE id = ANY(%s)""",
                    (ids,),
                )
                rows = await cur.fetchall()

        return {
            row["id"]: SegmentLocation(
                segment_id=row["id"],
                snapshot_id=row["document_snapshot_id"],
                section_titles=parse_section_titles(row["section_path"]),
            )
            for row in rows
        }
