"""Creation service: 起实例、签票、给上下文、收成果（52号 P2）。

``submit`` 实现 50号 §5.3 的六道校验，顺序即优先级：

1. 票据仍有效、实例仍在运行、提交目标匹配 —— 不过就**不写入任何内容**；
2. ``submission_id`` 是否已处理 —— 返回原回执，网络重试不重复写；
3. ``based_on_draft_revision`` 是否当前 —— 返回冲突与当前草稿摘要；
4. 是否符合字段与对象规则 —— 返回哪些字段不合格（逐对象拒，不整批毙）；
5. 每个关键值是否有有效来源 —— 三个 ID 齐全、段落真属该快照、且在允许章节内；
6. 人工是否已修改同一行 —— 转待合并，**不覆盖人工结果**。

成果只能经这里进入草稿。聊天回复、Agent 工作目录里的文件都不算（50号 §7.3）。
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any, Sequence

from knowledge_mining.mining.agent_creation import tickets
from knowledge_mining.mining.agent_creation.models import (
    ACCEPTED,
    CANCELLED,
    CONFLICT,
    EVIDENCE_OUT_OF_SCOPE,
    HUMAN_EDIT_PENDING_MERGE,
    REJECTED,
    RUNNING,
    STRUCTURE_INVALID,
    CreationContext,
    IssuedTicket,
    RejectedRow,
    SubmissionReceipt,
    TicketClaims,
    TicketRejected,
)
from knowledge_mining.mining.agent_creation.scope import ScopeGuard, SegmentLocator
from knowledge_mining.mining.knowledge_product.evidence import extract_evidence
from knowledge_mining.mining.knowledge_product.models import ProductObject
from knowledge_mining.mining.knowledge_product.service import KnowledgeProductService

#: 人工确认过的对象不许被 Agent 覆盖
HUMAN_CONFIRMED = "human_confirmed"


class AgentCreationService:
    def __init__(
        self,
        repo: Any,
        products: KnowledgeProductService,
        product_repo: Any,
        locator: SegmentLocator,
    ) -> None:
        self._repo = repo
        self._products = products
        self._product_repo = product_repo
        self._locator = locator

    # ---------------------------------------------------------------- 实例

    async def start_instance(
        self, product_id: str, *, created_by: str, ttl: timedelta | None = None
    ) -> tuple[dict[str, Any], IssuedTicket]:
        """起一个制作实例并签一张票。票据明文只在这里返回一次。"""
        product = await self._products.get_product(product_id)
        revisions = await self._product_repo.list_revisions(product_id)
        definition_revision = max((r["definition_revision"] for r in revisions), default=1)

        instance = await self._repo.create_instance(
            product_id=product_id,
            definition_revision=definition_revision,
            base_draft_revision=int(product.get("current_draft_revision") or 1),
            created_by=created_by,
        )
        ticket = await self._issue_ticket(instance, ttl=ttl)
        await self._repo.set_instance_status(instance["id"], RUNNING)
        return instance, ticket

    async def _issue_ticket(
        self, instance: dict[str, Any], *, ttl: timedelta | None = None
    ) -> IssuedTicket:
        token, token_hash = tickets.mint_token()
        ticket_id = tickets.new_ticket_id()
        expires_at = tickets.expiry_from(ttl)
        await self._repo.insert_ticket(
            ticket_id=ticket_id,
            instance_id=instance["id"],
            product_id=instance["product_id"],
            token_hash=token_hash,
            expires_at=expires_at,
        )
        return IssuedTicket(
            ticket_id=ticket_id,
            token=token,
            instance_id=instance["id"],
            product_id=instance["product_id"],
            expires_at=expires_at,
        )

    async def cancel_instance(self, instance_id: str, *, reason: str = "任务已取消") -> None:
        """取消实例并撤票。

        顺序要紧：**先改状态再撤票**两步都必须做——调 DSH 的 cancel 之后，队列里
        可能还有迟到的工具调用，只有票据撤掉才写不回来（50号 §8）。
        """
        await self._repo.set_instance_status(instance_id, CANCELLED)
        await self._repo.revoke_tickets(instance_id, reason)

    async def invalidate_product_tickets(self, product_id: str, *, reason: str) -> int:
        """资料范围收缩 / 定义不兼容变更 → 该制品全部在用票据立即失效。"""
        return await self._repo.revoke_tickets_for_product(product_id, reason)

    # ---------------------------------------------------------------- 票据解析

    async def _claims(self, token: str, *, product_id: str | None = None) -> TicketClaims:
        ticket_row = await self._repo.get_ticket_by_hash(tickets.hash_token(token))
        instance_row = (
            await self._repo.get_instance(ticket_row["instance_id"]) if ticket_row else None
        )
        return tickets.verify(ticket_row, instance_row, expected_product_id=product_id)

    # ---------------------------------------------------------------- 上下文

    async def get_context(self, token: str) -> CreationContext:
        """``get_creation_context``：这次该做什么 + 可读材料在哪里。

        不返回全部原文，也不返回其他制品草稿、未选文档清单或无权来源（50号 §5.2）。
        """
        claims = await self._claims(token)
        product = await self._products.get_product(claims.product_id)
        definition = await self._definition(claims)
        scope_items = await self._product_repo.list_scope_items(
            claims.product_id, claims.definition_revision
        )
        rows = await self._products.current_rows(claims.product_id)

        fields = definition.get("fields_json") or {}
        rules = definition.get("object_rules_json") or {}

        return CreationContext(
            creation_instance_id=claims.instance_id,
            product={
                "id": product["id"],
                "type": product["product_type"],
                "name": product["name"],
                "purpose": product.get("purpose"),
                "draft_revision": product.get("current_draft_revision"),
            },
            output_contract={
                "required_fields": [k for k, v in fields.items() if (v or {}).get("required")],
                "row_identity_rule": rules.get("row_identity_rule"),
                "source_required": True,
                "evidence_schema": [
                    "document_id", "snapshot_id", "segment_id", "anchor", "quoted_value",
                ],
                # segment_id 必填：它是「来源在允许范围内」唯一能硬校验的字段
                "evidence_required_keys": ["document_id", "snapshot_id", "segment_id"],
            },
            inputs=[
                {
                    "document_id": item["document_id"],
                    "snapshot_id": item["snapshot_id"],
                    "allowed_sections": item.get("allowed_sections_json"),
                }
                for item in scope_items
            ],
            examples=definition.get("examples_json") or [],
            human_decisions=(rules.get("human_decisions") or []),
            draft_summary={
                "object_count": len(rows),
                "unresolved": sorted(
                    oid for oid, row in rows.items() if row.get("review_status") == "unresolved"
                ),
                "human_confirmed": sorted(
                    oid for oid, row in rows.items()
                    if row.get("review_status") == HUMAN_CONFIRMED
                ),
            },
            next_action="先读样例文档，提交不超过十个对象的第一批结果",
        )

    async def _definition(self, claims: TicketClaims) -> dict[str, Any]:
        getter = getattr(self._product_repo, "get_definition", None)
        if getter is not None:
            return await getter(claims.product_id, claims.definition_revision) or {}
        return {}

    # ---------------------------------------------------------------- 提交

    async def submit(
        self,
        token: str,
        *,
        submission_id: str,
        based_on_draft_revision: int,
        documents: Sequence[str],
        product_id: str | None = None,
    ) -> SubmissionReceipt:
        # —— 第一道：票据 + 实例 + 目标匹配（不过则一个字节都不写）——
        claims = await self._claims(token, product_id=product_id)

        # —— 第二道：幂等。先抢主键再干活，真并发下也只有一方会写草稿 ——
        claimed = await self._repo.claim_submission(
            product_id=claims.product_id,
            submission_id=submission_id,
            instance_id=claims.instance_id,
            based_on_draft_revision=based_on_draft_revision,
        )
        if not claimed:
            return await self._existing_receipt(claims.product_id, submission_id)

        receipt = await self._process(claims, submission_id, based_on_draft_revision, documents)
        await self._repo.finalize_submission(
            product_id=claims.product_id,
            submission_id=submission_id,
            outcome=receipt.outcome,
            written_revision=receipt.written_revision,
            accepted_count=receipt.accepted_count,
            rejected_count=len(receipt.rejected),
            receipt=receipt.as_dict(),
        )
        return receipt

    async def _existing_receipt(
        self, product_id: str, submission_id: str
    ) -> SubmissionReceipt:
        row = await self._repo.get_submission(product_id, submission_id) or {}
        if row.get("outcome") == "pending":
            return SubmissionReceipt(
                submission_id=submission_id,
                outcome="pending",
                message="同一 submission_id 的提交正在处理中，请用同一 ID 稍后重试。",
            )
        stored = row.get("receipt_json") or {}
        return SubmissionReceipt(
            submission_id=submission_id,
            outcome=str(row.get("outcome") or REJECTED),
            written_revision=row.get("written_revision"),
            accepted_count=int(row.get("accepted_count") or 0),
            rejected=tuple(
                RejectedRow(
                    object_id=item.get("object_id", ""),
                    code=item.get("code", ""),
                    detail=item.get("detail", ""),
                    field=item.get("field"),
                )
                for item in (stored.get("rejected") or [])
            ),
            pending_merge=tuple(stored.get("pending_merge") or []),
            dangling=tuple(stored.get("dangling") or []),
            message="该提交此前已处理，返回原回执（未重复写入）。",
        )

    async def _process(
        self,
        claims: TicketClaims,
        submission_id: str,
        based_on_draft_revision: int,
        documents: Sequence[str],
    ) -> SubmissionReceipt:
        product = await self._products.get_product(claims.product_id)
        current_revision = int(product.get("current_draft_revision") or 0)

        # —— 第三道：草稿修订是否当前 ——
        if based_on_draft_revision != current_revision:
            return SubmissionReceipt(
                submission_id=submission_id,
                outcome=CONFLICT,
                current_draft_revision=current_revision,
                message=(
                    f"草稿已变化（你基于 {based_on_draft_revision}，当前 {current_revision}）。"
                    "请重新调用 get_creation_context 后再提交。"
                ),
            )

        try:
            objects = self._products.parse(documents)
        except ValueError as exc:
            return SubmissionReceipt(
                submission_id=submission_id,
                outcome=REJECTED,
                message=f"提交内容无法解析：{exc}",
            )

        rejected: list[RejectedRow] = []

        # —— 第四道：结构与字段规则（逐对象拒，不整批毙）——
        issues, _ = self._products.validate(objects)
        bad_ids = set()
        for issue in issues:
            bad_ids.add(issue.object_id)
            rejected.append(
                RejectedRow(issue.object_id, STRUCTURE_INVALID, issue.detail, issue.field)
            )

        survivors = [obj for obj in objects if obj.id not in bad_ids]

        # —— 第五道：证据必须落在票据允许的资料范围内 ——
        survivors, scope_rejected = await self._enforce_scope(claims, survivors)
        rejected.extend(scope_rejected)

        # —— 第六道：不覆盖人工修改 ——
        survivors, pending_merge = await self._hold_human_edits(claims.product_id, survivors)

        if not survivors:
            return SubmissionReceipt(
                submission_id=submission_id,
                outcome=REJECTED,
                rejected=tuple(rejected),
                pending_merge=tuple(pending_merge),
                current_draft_revision=current_revision,
                message="本批没有可接收的对象。",
            )

        result = await self._products.merge_draft(claims.product_id, survivors)
        return SubmissionReceipt(
            submission_id=submission_id,
            outcome=ACCEPTED,
            written_revision=result.revision_no,
            accepted_count=len(survivors),
            rejected=tuple(rejected),
            pending_merge=tuple(pending_merge),
            dangling=result.dangling,
            current_draft_revision=result.revision_no,
            message=f"已接收 {len(survivors)} 个对象，写入草稿修订 {result.revision_no}。",
        )

    async def _enforce_scope(
        self, claims: TicketClaims, objects: Sequence[ProductObject]
    ) -> tuple[list[ProductObject], list[RejectedRow]]:
        scope_items = await self._product_repo.list_scope_items(
            claims.product_id, claims.definition_revision
        )
        guard = ScopeGuard(scope_items, self._locator)

        refs = [ref for obj in objects for ref in extract_evidence(obj)]
        violations = await guard.check(refs)
        if not violations:
            return list(objects), []

        bad = {v.object_id for v in violations}
        rejected = [
            RejectedRow(v.object_id, EVIDENCE_OUT_OF_SCOPE, v.reason, v.field_name)
            for v in violations
        ]
        return [obj for obj in objects if obj.id not in bad], rejected

    async def _hold_human_edits(
        self, product_id: str, objects: Sequence[ProductObject]
    ) -> tuple[list[ProductObject], list[str]]:
        """人工确认过、且本次内容又不同的对象 → 转待合并，不覆盖。"""
        rows = await self._products.current_rows(product_id)
        if not rows:
            return list(objects), []

        kept: list[ProductObject] = []
        held: list[str] = []
        for obj in objects:
            row = rows.get(obj.id)
            if row is not None and row.get("review_status") == HUMAN_CONFIRMED:
                existing = await self._products.get_object_md(product_id, obj.id)
                if existing != obj.raw_md:
                    held.append(obj.id)
                    continue
            kept.append(obj)
        return kept, sorted(held)


__all__ = [
    "AgentCreationService",
    "HUMAN_CONFIRMED",
    "HUMAN_EDIT_PENDING_MERGE",
    "TicketRejected",
]
