"""Knowledge-product service: 建制品、改草稿、读对象、出 diff。

编排 registry / loader / validate / content_store / repository。首期只做**制作
闭环的载体侧**——制作实例、任务票据、DSH 适配、提交回执属于 P2。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from knowledge_mining.mining.knowledge_product.content_store import ArtifactContentStore
from knowledge_mining.mining.knowledge_product.diff import RevisionDiff, diff_object, diff_revisions
from knowledge_mining.mining.knowledge_product.evidence import extract_batch
from knowledge_mining.mining.knowledge_product.loader import build_object
from knowledge_mining.mining.knowledge_product.models import ProductObject
from knowledge_mining.mining.knowledge_product import impact, review
from knowledge_mining.mining.knowledge_product.registry import Registry
from knowledge_mining.mining.knowledge_product.repository import (
    KnowledgeProductRepository,
    ObjectRow,
    ScopeItem,
)
from knowledge_mining.mining.knowledge_product.validate import Issue, validate_batch


class NotFound(Exception):
    pass


class ValidationRejected(Exception):
    """结构校验不通过。``issues`` 是逐条清单，不是一句话。"""

    def __init__(self, issues: Sequence[Issue]) -> None:
        super().__init__(f"{len(issues)} 项结构问题")
        self.issues = list(issues)


@dataclass(frozen=True)
class DraftWriteResult:
    revision_no: int
    object_count: int
    dangling: tuple[str, ...]
    diff: RevisionDiff


class KnowledgeProductService:
    def __init__(
        self,
        repo: KnowledgeProductRepository,
        content: ArtifactContentStore,
        registry: Registry | None = None,
    ) -> None:
        self._repo = repo
        self._content = content
        self._registry = registry or Registry.load()

    # ---------------------------------------------------------------- 建制品

    async def create_product(
        self,
        *,
        product_id: str,
        product_type: str,
        name: str,
        owner: str,
        purpose: str | None = None,
        fields: dict[str, Any] | None = None,
        object_rules: dict[str, Any] | None = None,
        examples: list[Any] | None = None,
        trial_questions: list[Any] | None = None,
        scope_items: Sequence[ScopeItem] = (),
    ) -> dict[str, Any]:
        return await self._repo.create_product(
            product_id=product_id,
            product_type=product_type,
            name=name,
            owner=owner,
            purpose=purpose,
            fields=fields or {},
            object_rules=object_rules or {},
            examples=examples or [],
            trial_questions=trial_questions or [],
            scope_items=scope_items,
        )

    async def get_product(self, product_id: str) -> dict[str, Any]:
        product = await self._repo.get_product(product_id)
        if product is None:
            raise NotFound(product_id)
        return product

    async def list_products(self) -> list[dict[str, Any]]:
        return await self._repo.list_products()

    # ---------------------------------------------------------------- 改草稿

    def parse(self, documents: Sequence[str]) -> list[ProductObject]:
        """md → 对象。``id`` 缺失即抛——没有身份就没有对象。"""
        return [build_object(raw, self._registry) for raw in documents]

    def validate(self, objects: Sequence[ProductObject]):
        """→ ``(issues, dangling)``。不抛异常，调用方决定拒收还是转待合并。"""
        return validate_batch(objects, self._registry)

    async def replace_draft(
        self, product_id: str, documents: Sequence[str]
    ) -> DraftWriteResult:
        """用一批 md **整体替换**草稿，产出一个新修订（人工编辑路径）。

        整批替换而非增量：每个草稿修订是一次完整快照，修订之间的差异由 diff 算，
        不靠增量记录拼。结构校验不通过整批拒收——不落半份。
        """
        objects = self.parse(documents)
        issues, dangling = self.validate(objects)
        if issues:
            raise ValidationRejected(issues)
        return await self._write(product_id, objects, dangling)

    async def merge_draft(
        self, product_id: str, objects: Sequence[ProductObject]
    ) -> DraftWriteResult:
        """把一批对象**并入**当前草稿，产出一个新修订（Agent 提交路径）。

        与 ``replace_draft`` 的区别在语义：Agent 是「完成一批内容后提交」，一次只
        交几行，没提到的对象必须留着。同 ``object_id`` 以本次提交为准。

        调用方负责先做校验与过滤——这里只管合并与落库。
        """
        current = await self._current_objects(product_id)
        merged = {**current, **{obj.id: obj for obj in objects}}
        _, dangling = self.validate(list(merged.values()))
        return await self._write(product_id, list(merged.values()), dangling)

    async def _write(
        self, product_id: str, objects: Sequence[ProductObject], dangling: set[str]
    ) -> DraftWriteResult:
        product = await self.get_product(product_id)
        before = await self._load_revision_bodies(
            product_id, product.get("current_draft_revision")
        )
        revision_diff = diff_revisions(before, {obj.id: obj.raw_md for obj in objects})

        # 人审结论要能跨修订活下来：内容没变的对象沿用上一版的 review_status，
        # 否则人刚确认完，下一次提交把整批重写一遍就把结论抹掉了。
        previous = await self.current_rows(product_id)
        rows = [
            await self._to_row(
                obj,
                previous_status=(
                    previous.get(obj.id, {}).get("review_status")
                    if before.get(obj.id) == obj.raw_md
                    else None
                ),
            )
            for obj in objects
        ]
        revision_no = await self._repo.write_draft_revision(
            product_id=product_id,
            objects=rows,
            edges=[edge for obj in objects for edge in obj.edges],
            evidence=extract_batch(objects),
        )
        return DraftWriteResult(
            revision_no=revision_no,
            object_count=len(rows),
            dangling=tuple(sorted(dangling)),
            diff=revision_diff,
        )

    async def _current_objects(self, product_id: str) -> dict[str, ProductObject]:
        product = await self.get_product(product_id)
        bodies = await self._load_revision_bodies(
            product_id, product.get("current_draft_revision")
        )
        return {oid: build_object(raw, self._registry) for oid, raw in bodies.items()}

    async def current_rows(self, product_id: str) -> dict[str, dict[str, Any]]:
        """当前草稿的对象索引行（含 ``review_status``）——人工修改保护要看它。"""
        product = await self.get_product(product_id)
        revision_no = product.get("current_draft_revision")
        if revision_no is None:
            return {}
        return {
            row["object_id"]: row
            for row in await self._repo.list_objects(product_id, int(revision_no))
        }

    async def _to_row(
        self, obj: ProductObject, *, previous_status: str | None = None
    ) -> ObjectRow:
        record = await self._content.put(obj.raw_md)
        has_conflict = any(
            isinstance(f, dict) and f.get("conflict") for f in obj.fields.values()
        )
        if has_conflict:
            # 有冲突字段的对象卡不得混进可发布集合，落库即标记——这条压过人审结论：
            # 冲突是新出现的事实，之前确认过不代表现在还成立
            status = "unresolved"
        else:
            status = previous_status or "agent_submitted"

        return ObjectRow(
            object_id=obj.id,
            type=obj.type,
            layer=obj.layer,
            scope=obj.scope,
            name=obj.name,
            frontmatter=obj.frontmatter,
            storage_object_id=record.id,
            review_status=status,
        )

    # ---------------------------------------------------------------- 读

    async def list_objects(
        self, product_id: str, revision_no: int | None = None
    ) -> list[dict[str, Any]]:
        revision_no = await self._resolve_revision(product_id, revision_no)
        return await self._repo.list_objects(product_id, revision_no)

    async def get_object_md(
        self, product_id: str, object_id: str, revision_no: int | None = None
    ) -> str:
        revision_no = await self._resolve_revision(product_id, revision_no)
        row = await self._repo.get_object(product_id, object_id, revision_no)
        if row is None or not row.get("storage_object_id"):
            raise NotFound(object_id)
        return await self._content.get(row["storage_object_id"])

    async def list_backlinks(self, object_id: str) -> list[dict[str, Any]]:
        """谁引用了这个对象。反向边不写回 md，只能从索引反查（F5）。"""
        return await self._repo.list_backlinks(object_id)

    # ---------------------------------------------------------------- 定义修订

    async def update_definition(
        self,
        product_id: str,
        *,
        editor: str,
        fields: dict[str, Any] | None = None,
        object_rules: dict[str, Any] | None = None,
        examples: list[Any] | None = None,
        trial_questions: list[Any] | None = None,
        scope_items: Sequence[ScopeItem] | None = None,
    ) -> dict[str, Any]:
        """改制品定义 = **开新的一版**，不是原地改。

        旧定义留着：已发布修订指向它，删了就没法解释「那一版当时按什么规则做的」。
        未给的部分从当前定义继承——改字段定义不该顺手把资料范围清空。

        调用方**必须**在此之后撤销该制品的在用票据：Agent 手里的旧票据绑定的是旧
        定义修订，按旧范围继续提交就等于绕过了这次改动（50号 §7.1）。撤票不在这里
        做——票据属于 ``agent_creation``，载体层不该反过来依赖制作面。
        """
        await self.get_product(product_id)
        current = await self._latest_definition(product_id) or {}
        next_revision = await self._repo.next_definition_revision(product_id)

        if scope_items is None:
            existing = await self._repo.list_scope_items(
                product_id, int(current.get("definition_revision") or 1)
            )
            scope_items = [
                ScopeItem(
                    document_id=item["document_id"],
                    snapshot_id=item["snapshot_id"],
                    allowed_sections=item.get("allowed_sections_json"),
                )
                for item in existing
            ]

        return await self._repo.add_definition(
            product_id=product_id,
            definition_revision=next_revision,
            fields=fields if fields is not None else (current.get("fields_json") or {}),
            object_rules=(
                object_rules if object_rules is not None
                else (current.get("object_rules_json") or {})
            ),
            examples=(
                examples if examples is not None else (current.get("examples_json") or [])
            ),
            trial_questions=(
                trial_questions if trial_questions is not None
                else (current.get("trial_questions_json") or [])
            ),
            scope_items=scope_items,
            created_by=editor,
        )

    async def _latest_definition(self, product_id: str) -> dict[str, Any] | None:
        revision = await self._repo.next_definition_revision(product_id) - 1
        if revision < 1:
            return None
        return await self._repo.get_definition(product_id, revision)

    async def current_definition(self, product_id: str) -> dict[str, Any] | None:
        return await self._latest_definition(product_id)

    # ---------------------------------------------------------------- 报告问题

    async def report_issue(
        self,
        product_id: str,
        *,
        problem: str,
        reporter: str,
        used_revision: int | None = None,
        object_id: str | None = None,
        field_name: str | None = None,
        task: str | None = None,
        correction_basis: str | None = None,
    ) -> dict[str, Any]:
        """在制品上报告问题（48号 §八）。

        缺省记的是**当前发布修订**——报告问题的人用的是对外那一份，不是负责人的
        草稿；记错版本会让后面的复核对着另一份内容看。
        """
        product = await self.get_product(product_id)
        if used_revision is None:
            used_revision = product.get("released_revision") or product.get(
                "current_draft_revision"
            )
        return await self._repo.record_issue(
            product_id=product_id,
            used_revision=int(used_revision) if used_revision is not None else None,
            object_id=object_id,
            field_name=field_name,
            task=task,
            problem=problem,
            correction_basis=correction_basis,
            reporter=reporter,
        )

    async def list_issues(
        self, product_id: str, status: str | None = None,
    ) -> list[dict[str, Any]]:
        return await self._repo.list_issues(product_id, status)

    async def resolve_issue(
        self,
        issue_id: str,
        *,
        status: str,
        resolved_by: str,
        resolution_kind: str | None = None,
        resolution_note: str | None = None,
    ) -> dict[str, Any]:
        if status not in ("triaged", "resolved", "rejected"):
            raise ValueError(f"非法的处置状态: {status!r}")
        row = await self._repo.resolve_issue(
            issue_id=issue_id, status=status, resolution_kind=resolution_kind,
            resolution_note=resolution_note, resolved_by=resolved_by,
        )
        if row is None:
            raise NotFound(issue_id)
        return row

    # ---------------------------------------------------------------- 影响分析

    async def source_alerts(
        self, product_id: str, revision_no: int | None = None,
    ) -> list[impact.SourceAlert]:
        """这一版引用的资料，现在有哪些变了（48号 §八）。

        缺省看**发布修订**：草稿的资料变动由负责人在制作中自然会碰到，真正要提醒
        的是「已经对外服务的那一份现在依赖着过时/失效的资料」。
        """
        product = await self.get_product(product_id)
        if revision_no is None:
            revision_no = product.get("released_revision") or product.get(
                "current_draft_revision"
            )
        if revision_no is None:
            return []

        evidence = await self._repo.evidence_of_revision(product_id, int(revision_no))
        if not evidence:
            return []
        states, newer = await self._repo.snapshot_states(
            [row["snapshot_id"] for row in evidence]
        )
        return impact.build_alerts(evidence, states, newer)

    # ---------------------------------------------------------------- 人工编辑

    async def edit_object(
        self,
        product_id: str,
        object_id: str,
        raw_md: str,
        *,
        editor: str,
        reason: str | None = None,
        basis: str | None = None,
    ) -> DraftWriteResult:
        """人工改一个对象，留痕后写新修订（48号 §六）。

        改过的对象直接标 ``human_confirmed``——人亲手写的内容不需要再被自己确认一遍，
        而且这个状态会让下一次 Agent 提交撞上「不覆盖人工结果」那道闸。
        """
        current = await self._current_objects(product_id)
        before = current.get(object_id)
        edited = build_object(raw_md, self._registry)
        if edited.id != object_id:
            raise ValueError(f"正文里的 id 是 {edited.id!r}，与要改的 {object_id!r} 不符")

        issues, _ = self.validate([edited])
        if issues:
            raise ValidationRejected(issues)

        product = await self.get_product(product_id)
        await self._repo.record_object_edit(
            product_id=product_id,
            revision_no=int(product.get("current_draft_revision") or 1),
            object_id=object_id,
            editor=editor,
            reason=reason,
            basis=basis,
            before_md=before.raw_md if before else None,
            after_md=raw_md,
        )

        merged = {**current, object_id: edited}
        _, dangling = self.validate(list(merged.values()))
        result = await self._write(product_id, list(merged.values()), dangling)
        await self._repo.set_review_status(
            product_id, object_id, result.revision_no, review.HUMAN_CONFIRMED
        )
        return result

    async def list_object_edits(
        self, product_id: str, object_id: str | None = None
    ) -> list[dict[str, Any]]:
        return await self._repo.list_object_edits(product_id, object_id)

    # ---------------------------------------------------------------- 人审

    async def review_objects(
        self,
        product_id: str,
        decisions: Mapping[str, str],
        *,
        reviewer: str,
        notes: str | None = None,
        instance_id: str | None = None,
    ) -> dict[str, Any]:
        """按对象逐个裁决。审核单位是一次提交批次，批次内在这里展开（A2）。

        决定不合法（比如想确认一个还带冲突的对象）整批不落——审核记录必须与实际
        状态一致，不能出现「记了确认但状态没动」。
        """
        product = await self.get_product(product_id)
        revision_no = int(product.get("current_draft_revision") or 1)
        rows = await self.current_rows(product_id)

        resolved: dict[str, str] = {}
        for object_id, decision in decisions.items():
            if object_id not in rows:
                raise review.ReviewRejected(f"对象不在当前草稿修订中: {object_id!r}")
            resolved[object_id] = review.next_status(
                rows[object_id].get("review_status", review.AGENT_SUBMITTED), decision
            )

        for object_id, status in resolved.items():
            await self._repo.set_review_status(product_id, object_id, revision_no, status)

        decision = (
            "rejected" if any(s == review.REJECTED for s in resolved.values()) else "approved"
        )
        return await self._repo.record_review(
            product_id=product_id,
            revision_no=revision_no,
            reviewer=reviewer,
            decision=decision,
            notes=notes,
            per_object=dict(decisions),
            instance_id=instance_id,
        )

    async def list_reviews(
        self, product_id: str, revision_no: int | None = None
    ) -> list[dict[str, Any]]:
        return await self._repo.list_reviews(
            product_id, await self._resolve_revision(product_id, revision_no)
        )

    # ---------------------------------------------------------------- 试用

    async def record_trial(
        self,
        product_id: str,
        *,
        question: str,
        verdict: str,
        tried_by: str,
        answer: str | None = None,
        expected_points: str | None = None,
        comment: str | None = None,
        revision_no: int | None = None,
    ) -> dict[str, Any]:
        return await self._repo.record_trial(
            product_id=product_id,
            revision_no=await self._resolve_revision(product_id, revision_no),
            question=question,
            answer=answer,
            expected_points=expected_points,
            verdict=verdict,
            comment=comment,
            tried_by=tried_by,
        )

    async def list_trials(
        self, product_id: str, revision_no: int | None = None
    ) -> list[dict[str, Any]]:
        return await self._repo.list_trials(
            product_id, await self._resolve_revision(product_id, revision_no)
        )

    # ---------------------------------------------------------------- 发布

    async def publish_gate(
        self, product_id: str, revision_no: int | None = None
    ) -> review.PublishGate:
        """算发布门禁清单（48号 §七）。返回逐项结果，不是一个布尔。"""
        revision_no = await self._resolve_revision(product_id, revision_no)
        product = await self.get_product(product_id)
        objects = await self._repo.list_objects(product_id, revision_no)
        edges = await self._repo.list_edges(product_id, revision_no)
        definition = await self._definition_of(product_id, revision_no)

        return review.evaluate_gate(
            product=product,
            definition=definition,
            objects=objects,
            edge_targets=[edge["to_id"] for edge in edges],
            trials=await self._repo.list_trials(product_id, revision_no),
        )

    async def publish(
        self, product_id: str, revision_no: int | None = None, *, force: bool = False
    ) -> dict[str, Any]:
        """把一个草稿修订转为发布修订。

        门禁不过就不发——``force`` 只留给「负责人明确要带着已知缺口发布」这种情况，
        并且会把当时的门禁结果原样记进审核轨迹，事后查得到是谁放的行。
        """
        revision_no = await self._resolve_revision(product_id, revision_no)
        gate = await self.publish_gate(product_id, revision_no)
        if not gate.passed and not force:
            raise review.ReviewRejected(
                "发布门禁未通过："
                + "；".join(check.detail for check in gate.blocking)
            )

        await self._repo.publish_revision(product_id, revision_no)
        return {
            "product_id": product_id,
            "released_revision": revision_no,
            "gate": gate.as_dict(),
            "forced": bool(force and not gate.passed),
        }

    async def _definition_of(
        self, product_id: str, revision_no: int
    ) -> dict[str, Any] | None:
        revision = await self._repo.get_revision(product_id, revision_no)
        if revision is None:
            return None
        return await self._repo.get_definition(
            product_id, int(revision.get("definition_revision") or 1)
        )

    # ---------------------------------------------------------------- diff

    async def diff(
        self, product_id: str, from_revision: int, to_revision: int
    ) -> RevisionDiff:
        return diff_revisions(
            await self._load_revision_bodies(product_id, from_revision),
            await self._load_revision_bodies(product_id, to_revision),
        )

    async def diff_one(
        self, product_id: str, object_id: str, from_revision: int, to_revision: int
    ) -> str:
        before = await self._load_revision_bodies(product_id, from_revision)
        after = await self._load_revision_bodies(product_id, to_revision)
        return diff_object(before.get(object_id), after.get(object_id), object_id=object_id)

    # ---------------------------------------------------------------- 内部

    async def _resolve_revision(self, product_id: str, revision_no: int | None) -> int:
        if revision_no is not None:
            return revision_no
        product = await self.get_product(product_id)
        current = product.get("current_draft_revision")
        if current is None:
            raise NotFound(f"{product_id} 尚无修订")
        return int(current)

    async def _load_revision_bodies(
        self, product_id: str, revision_no: int | None
    ) -> dict[str, str]:
        """→ ``{object_id: raw_md}``；修订不存在时为空（首次写草稿即此情形）。"""
        if revision_no is None:
            return {}
        bodies: dict[str, str] = {}
        for row in await self._repo.list_objects(product_id, revision_no):
            if row.get("storage_object_id"):
                bodies[row["object_id"]] = await self._content.get(row["storage_object_id"])
        return bodies
