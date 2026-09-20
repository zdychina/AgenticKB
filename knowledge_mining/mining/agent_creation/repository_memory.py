"""In-memory ``CreationRepository`` + ``SegmentLocator``.

服务层测试跑这套，PG 实现只在有真库时做 smoke。两边方法签名一致，契约漂了会在
服务层测试里立刻暴露。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Sequence

from knowledge_mining.mining.agent_creation.scope import SegmentLocation


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class MemoryCreationRepository:
    def __init__(self) -> None:
        self._instances: dict[str, dict[str, Any]] = {}
        self._tickets: dict[str, dict[str, Any]] = {}       # token_hash → row
        self._submissions: dict[tuple[str, str], dict[str, Any]] = {}

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
        instance = {
            "id": f"kpi_{uuid.uuid4().hex}",
            "product_id": product_id,
            "definition_revision": definition_revision,
            "base_draft_revision": base_draft_revision,
            "status": "pending",
            "harness_session_id": None,
            "last_event_cursor": None,
            "created_by": created_by,
            "created_at": now,
            "updated_at": now,
        }
        self._instances[instance["id"]] = instance
        return dict(instance)

    async def get_instance(self, instance_id: str) -> dict[str, Any] | None:
        instance = self._instances.get(instance_id)
        return dict(instance) if instance else None

    async def list_instances(self, product_id: str) -> list[dict[str, Any]]:
        return [
            dict(row) for row in self._instances.values()
            if row["product_id"] == product_id
        ]

    async def set_instance_status(self, instance_id: str, status: str) -> None:
        if instance_id in self._instances:
            self._instances[instance_id]["status"] = status
            self._instances[instance_id]["updated_at"] = _utcnow()

    async def bind_harness_session(self, instance_id: str, session_id: str) -> None:
        if instance_id in self._instances:
            self._instances[instance_id]["harness_session_id"] = session_id
            self._instances[instance_id]["updated_at"] = _utcnow()

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
        self._tickets[token_hash] = {
            "id": ticket_id,
            "instance_id": instance_id,
            "product_id": product_id,
            "token_hash": token_hash,
            "status": "active",
            "issued_at": _utcnow(),
            "expires_at": expires_at,
            "revoked_at": None,
            "revoked_reason": None,
        }

    async def get_ticket_by_hash(self, token_hash: str) -> dict[str, Any] | None:
        row = self._tickets.get(token_hash)
        return dict(row) if row else None

    async def revoke_tickets(self, instance_id: str, reason: str) -> int:
        return self._revoke(lambda r: r["instance_id"] == instance_id, reason)

    async def revoke_tickets_for_product(self, product_id: str, reason: str) -> int:
        return self._revoke(lambda r: r["product_id"] == product_id, reason)

    def _revoke(self, predicate, reason: str) -> int:
        count = 0
        for row in self._tickets.values():
            if row["status"] == "active" and predicate(row):
                row["status"] = "revoked"
                row["revoked_at"] = _utcnow()
                row["revoked_reason"] = reason
                count += 1
        return count

    # ---------------------------------------------------------------- 提交

    async def get_submission(
        self, product_id: str, submission_id: str
    ) -> dict[str, Any] | None:
        row = self._submissions.get((product_id, submission_id))
        return dict(row) if row else None

    async def claim_submission(
        self,
        *,
        product_id: str,
        submission_id: str,
        instance_id: str,
        based_on_draft_revision: int,
    ) -> bool:
        key = (product_id, submission_id)
        if key in self._submissions:
            return False
        self._submissions[key] = {
            "product_id": product_id,
            "submission_id": submission_id,
            "instance_id": instance_id,
            "based_on_draft_revision": based_on_draft_revision,
            "outcome": "pending",
            "written_revision": None,
            "accepted_count": 0,
            "rejected_count": 0,
            "receipt_json": {},
            "created_at": _utcnow(),
        }
        return True

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
        row = self._submissions.get((product_id, submission_id))
        if row is None or row["outcome"] != "pending":
            return
        row.update(
            outcome=outcome,
            written_revision=written_revision,
            accepted_count=accepted_count,
            rejected_count=rejected_count,
            receipt_json=receipt,
        )

    async def list_product_submissions(self, product_id: str) -> list[dict[str, Any]]:
        return [
            dict(row) for row in self._submissions.values()
            if row["product_id"] == product_id
        ]

    async def list_submissions(self, instance_id: str) -> list[dict[str, Any]]:
        return [
            dict(row) for row in self._submissions.values()
            if row["instance_id"] == instance_id
        ]


class MemorySegmentLocator:
    """按 segment_id 查归属的内存实现。

    ``add`` 登记的是「平台知道的真相」——测试里 Agent 自报别的 snapshot 就该被拒。
    """

    def __init__(self) -> None:
        self._segments: dict[str, SegmentLocation] = {}

    def add(
        self, segment_id: str, snapshot_id: str, section_titles: Sequence[str] = ()
    ) -> None:
        self._segments[segment_id] = SegmentLocation(
            segment_id=segment_id,
            snapshot_id=snapshot_id,
            section_titles=tuple(section_titles),
        )

    async def locate(self, segment_ids: Sequence[str]) -> dict[str, SegmentLocation]:
        return {
            sid: self._segments[sid] for sid in dict.fromkeys(segment_ids)
            if sid in self._segments
        }
