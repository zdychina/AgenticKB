"""Types for the creation face（52号 P2）。

制作实例、任务票据、提交回执。载体侧的类型在 ``knowledge_product/models.py``。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---- 实例状态机 -------------------------------------------------------------
PENDING = "pending"
RUNNING = "running"
PAUSED = "paused"
COMPLETED = "completed"
CANCELLED = "cancelled"
FAILED = "failed"

#: 只有这两个状态下才接收提交——暂停/取消/完成后到达的「迟到提交」一律拒收。
ACCEPTS_SUBMISSION = frozenset({PENDING, RUNNING})
TERMINAL = frozenset({COMPLETED, CANCELLED, FAILED})

# ---- 提交结果 ---------------------------------------------------------------
ACCEPTED = "accepted"
REJECTED = "rejected"
CONFLICT = "conflict"

# ---- 拒收原因码（对 Agent 可见，供它自我纠正）-------------------------------
TICKET_INVALID = "ticket_invalid"
TICKET_EXPIRED = "ticket_expired"
TICKET_REVOKED = "ticket_revoked"
TICKET_PRODUCT_MISMATCH = "ticket_product_mismatch"
INSTANCE_NOT_ACCEPTING = "instance_not_accepting"
REVISION_CONFLICT = "revision_conflict"
STRUCTURE_INVALID = "structure_invalid"
EVIDENCE_MISSING = "evidence_missing"
EVIDENCE_OUT_OF_SCOPE = "evidence_out_of_scope"
HUMAN_EDIT_PENDING_MERGE = "human_edit_pending_merge"


class TicketRejected(Exception):
    """票据校验不过。``code`` 是上面的原因码，``message`` 面向 Agent。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class IssuedTicket:
    """签发结果。``token`` 明文**只在这里出现一次**——库里只存哈希。"""

    ticket_id: str
    token: str
    instance_id: str
    product_id: str
    expires_at: str


@dataclass(frozen=True)
class TicketClaims:
    """校验通过的票据。"""

    ticket_id: str
    instance_id: str
    product_id: str
    definition_revision: int
    expires_at: str


@dataclass(frozen=True)
class RejectedRow:
    object_id: str
    code: str
    detail: str
    field: str | None = None


@dataclass(frozen=True)
class SubmissionReceipt:
    """提交回执。Agent 拿到它才能说「成果已交付」（50号 §2.2）。"""

    submission_id: str
    outcome: str
    written_revision: int | None = None
    accepted_count: int = 0
    rejected: tuple[RejectedRow, ...] = ()
    pending_merge: tuple[str, ...] = ()
    dangling: tuple[str, ...] = ()
    #: outcome=conflict 时给出当前草稿摘要，让 Agent 重新取上下文
    current_draft_revision: int | None = None
    message: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "submission_id": self.submission_id,
            "outcome": self.outcome,
            "written_revision": self.written_revision,
            "accepted_count": self.accepted_count,
            "rejected": [
                {
                    "object_id": row.object_id,
                    "code": row.code,
                    "field": row.field,
                    "detail": row.detail,
                }
                for row in self.rejected
            ],
            "pending_merge": list(self.pending_merge),
            "dangling": list(self.dangling),
            "current_draft_revision": self.current_draft_revision,
            "message": self.message,
        }


@dataclass(frozen=True)
class CreationContext:
    """``get_creation_context`` 的返回。

    首次不返回全部原文，只说「这次该做什么」和「可读材料在哪里」——大文档由
    ``get_knowledge`` 按需读（50号 §5.2）。
    """

    creation_instance_id: str
    product: dict[str, Any]
    output_contract: dict[str, Any]
    inputs: list[dict[str, Any]]
    examples: list[Any] = field(default_factory=list)
    human_decisions: list[Any] = field(default_factory=list)
    draft_summary: dict[str, Any] = field(default_factory=dict)
    next_action: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "creation_instance_id": self.creation_instance_id,
            "product": self.product,
            "output_contract": self.output_contract,
            "inputs": self.inputs,
            "examples": self.examples,
            "human_decisions": self.human_decisions,
            "draft_summary": self.draft_summary,
            "next_action": self.next_action,
        }
