"""Knowledge-product routes — /api/knowledge-products（52号 P1）。

首期只有载体侧四组接口：建制品、整篇替换草稿、列对象、出 diff。启动制作实例、
发人类补充、看过程、取消、试用/发布属于 P2 的 ``agent_creation``。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from knowledge_mining.mining.api.deps import get_domain_async_pool
from knowledge_mining.mining.kb.auth import current_user
from knowledge_mining.mining.knowledge_product.repository import ScopeItem
from knowledge_mining.mining.knowledge_product.review import ReviewRejected
from knowledge_mining.mining.knowledge_product.service import (
    KnowledgeProductService,
    NotFound,
    ValidationRejected,
)

router = APIRouter(prefix="/api/knowledge-products", tags=["knowledge-product"])


async def get_product_service(
    request: Request, domain: str = Query(...),
) -> KnowledgeProductService:
    """制品仓储走**按域路由**的 asset_core 池，不是主库。

    52号 D6：制品不跨知识域，``kp_*`` 落该域的 asset_core。鉴权仍在主库
    （kb_users 不分域），所以只有仓储这一侧换池。
    """
    from knowledge_mining.mining.file_management.repositories_pg import (
        PgStorageObjectRepository,
    )
    from knowledge_mining.mining.knowledge_product.content_store import (
        ArtifactContentStore,
        bucket_for,
    )
    from knowledge_mining.mining.knowledge_product.repository import (
        KnowledgeProductRepository,
    )

    pool = await get_domain_async_pool(request, domain)
    config = request.app.state.object_store_config
    return KnowledgeProductService(
        KnowledgeProductRepository(pool),
        ArtifactContentStore(
            request.app.state.object_store,
            PgStorageObjectRepository(pool),
            bucket_for(config.bucket_prefix),
        ),
    )


# ----------------------------------------------------------------- models


class ScopeItemIn(BaseModel):
    document_id: str
    snapshot_id: str
    allowed_sections: list[str] | None = None


class ProductCreate(BaseModel):
    product_id: str = Field(min_length=1, max_length=200, description="制品 slug，同时是对象逻辑 ID 首段")
    product_type: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1)
    purpose: str | None = None
    fields: dict[str, Any] = Field(default_factory=dict, description="字段定义（D5 第二级）")
    object_rules: dict[str, Any] = Field(default_factory=dict)
    examples: list[Any] = Field(default_factory=list)
    trial_questions: list[Any] = Field(default_factory=list)
    scope_items: list[ScopeItemIn] = Field(default_factory=list, description="资料范围，票据校验的唯一依据")


class DraftReplace(BaseModel):
    documents: list[str] = Field(min_length=1, description="整批 md 正文，整体替换草稿")


class ObjectEdit(BaseModel):
    raw_md: str = Field(min_length=1)
    reason: str | None = Field(default=None, max_length=1000)
    basis: str | None = Field(default=None, max_length=1000)


class ReviewDecisions(BaseModel):
    decisions: dict[str, str] = Field(min_length=1, description="object_id -> approve|reject")
    notes: str | None = Field(default=None, max_length=2000)
    instance_id: str | None = None


class TrialRecord(BaseModel):
    question: str = Field(min_length=1)
    verdict: str = Field(pattern="^(passed|failed|inconclusive)$")
    answer: str | None = None
    expected_points: str | None = None
    comment: str | None = None
    revision: int | None = None


class PublishRequest(BaseModel):
    revision: int | None = None
    force: bool = Field(default=False, description="带着已知缺口发布；门禁结果会原样留痕")


# ----------------------------------------------------------------- routes


@router.post("", status_code=201)
async def create_product(
    payload: ProductCreate,
    user: dict = Depends(current_user),
    service: KnowledgeProductService = Depends(get_product_service),
) -> dict[str, Any]:
    return await service.create_product(
        product_id=payload.product_id,
        product_type=payload.product_type,
        name=payload.name,
        owner=user["username"],
        purpose=payload.purpose,
        fields=payload.fields,
        object_rules=payload.object_rules,
        examples=payload.examples,
        trial_questions=payload.trial_questions,
        scope_items=[
            ScopeItem(
                document_id=item.document_id,
                snapshot_id=item.snapshot_id,
                allowed_sections=item.allowed_sections,
            )
            for item in payload.scope_items
        ],
    )


@router.get("")
async def list_products(
    _user: dict = Depends(current_user),
    service: KnowledgeProductService = Depends(get_product_service),
) -> list[dict[str, Any]]:
    return await service.list_products()


@router.get("/backlinks/{object_id:path}")
async def list_backlinks(
    object_id: str,
    _user: dict = Depends(current_user),
    service: KnowledgeProductService = Depends(get_product_service),
) -> list[dict[str, Any]]:
    """谁引用了这个对象。反向边不写回 md，只能从索引反查（52号 F5）。"""
    return await service.list_backlinks(object_id)


@router.get("/{product_id}")
async def get_product(
    product_id: str,
    _user: dict = Depends(current_user),
    service: KnowledgeProductService = Depends(get_product_service),
) -> dict[str, Any]:
    try:
        return await service.get_product(product_id)
    except NotFound as exc:
        raise HTTPException(404, str(exc)) from exc


@router.patch("/{product_id}/draft")
async def replace_draft(
    product_id: str,
    payload: DraftReplace,
    _user: dict = Depends(current_user),
    service: KnowledgeProductService = Depends(get_product_service),
) -> dict[str, Any]:
    """整批替换草稿，产出新修订。结构校验不通过整批拒收，逐条返回问题清单。"""
    try:
        result = await service.replace_draft(product_id, payload.documents)
    except NotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValidationRejected as exc:
        raise HTTPException(
            422,
            detail={
                "error": "validation_rejected",
                "issues": [
                    {
                        "object_id": issue.object_id,
                        "code": issue.code,
                        "field": issue.field,
                        "detail": issue.detail,
                    }
                    for issue in exc.issues
                ],
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    return {
        "revision_no": result.revision_no,
        "object_count": result.object_count,
        # 悬挂边报告但不阻断——制作过程中的正常中间态（52号 A5）
        "dangling": list(result.dangling),
        "diff": _diff_payload(result.diff),
    }


@router.get("/{product_id}/objects")
async def list_objects(
    product_id: str,
    revision: int | None = Query(default=None, description="缺省取当前草稿修订"),
    _user: dict = Depends(current_user),
    service: KnowledgeProductService = Depends(get_product_service),
) -> list[dict[str, Any]]:
    try:
        return await service.list_objects(product_id, revision)
    except NotFound as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/{product_id}/objects/{object_id}/md")
async def get_object_md(
    product_id: str,
    object_id: str,
    revision: int | None = Query(default=None),
    _user: dict = Depends(current_user),
    service: KnowledgeProductService = Depends(get_product_service),
) -> dict[str, Any]:
    try:
        return {"object_id": object_id, "md": await service.get_object_md(
            product_id, object_id, revision
        )}
    except (NotFound, KeyError) as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/{product_id}/diff")
async def diff_revisions(
    product_id: str,
    from_revision: int = Query(alias="from"),
    to_revision: int = Query(alias="to"),
    object_id: str | None = Query(default=None, description="给定则返回该对象的 unified diff"),
    _user: dict = Depends(current_user),
    service: KnowledgeProductService = Depends(get_product_service),
) -> dict[str, Any]:
    try:
        if object_id is not None:
            return {
                "object_id": object_id,
                "unified_diff": await service.diff_one(
                    product_id, object_id, from_revision, to_revision
                ),
            }
        return _diff_payload(await service.diff(product_id, from_revision, to_revision))
    except NotFound as exc:
        raise HTTPException(404, str(exc)) from exc


def _diff_payload(diff) -> dict[str, Any]:
    return {
        "added": list(diff.added),
        "modified": list(diff.modified),
        "removed": list(diff.removed),
        "total": diff.total,
        "changes": [
            {
                "object_id": change.object_id,
                "change": change.change,
                "added_lines": change.added_lines,
                "removed_lines": change.removed_lines,
            }
            for change in diff.changes
        ],
    }


# ----------------------------------------------------------------- 人审 / 试用 / 发布


@router.patch("/{product_id}/objects/{object_id}")
async def edit_object(
    product_id: str,
    object_id: str,
    payload: ObjectEdit,
    user: dict = Depends(current_user),
    service: KnowledgeProductService = Depends(get_product_service),
) -> dict[str, Any]:
    """人工改一个对象。留痕（谁改了什么、为什么、依据）后写新修订。"""
    try:
        result = await service.edit_object(
            product_id, object_id, payload.raw_md,
            editor=user["username"], reason=payload.reason, basis=payload.basis,
        )
    except NotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValidationRejected as exc:
        raise HTTPException(422, detail=_issue_payload(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"revision_no": result.revision_no, "diff": _diff_payload(result.diff)}


@router.get("/{product_id}/objects/{object_id}/edits")
async def list_object_edits(
    product_id: str,
    object_id: str,
    _user: dict = Depends(current_user),
    service: KnowledgeProductService = Depends(get_product_service),
) -> list[dict[str, Any]]:
    return await service.list_object_edits(product_id, object_id)


@router.post("/{product_id}/reviews")
async def review_objects(
    product_id: str,
    payload: ReviewDecisions,
    user: dict = Depends(current_user),
    service: KnowledgeProductService = Depends(get_product_service),
) -> dict[str, Any]:
    """按对象逐个裁决。决定不合法整批不落——审核记录必须与实际状态一致。"""
    try:
        return await service.review_objects(
            product_id, payload.decisions,
            reviewer=user["username"], notes=payload.notes,
            instance_id=payload.instance_id,
        )
    except NotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except ReviewRejected as exc:
        raise HTTPException(422, detail={"error": "review_rejected", "message": str(exc)}) from exc


@router.get("/{product_id}/reviews")
async def list_reviews(
    product_id: str,
    revision: int | None = Query(default=None),
    _user: dict = Depends(current_user),
    service: KnowledgeProductService = Depends(get_product_service),
) -> list[dict[str, Any]]:
    try:
        return await service.list_reviews(product_id, revision)
    except NotFound as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/{product_id}/trials", status_code=201)
async def record_trial(
    product_id: str,
    payload: TrialRecord,
    user: dict = Depends(current_user),
    service: KnowledgeProductService = Depends(get_product_service),
) -> dict[str, Any]:
    """登记一条试用记录。试用绑定具体修订——换了修订不自动继承。"""
    try:
        return await service.record_trial(
            product_id,
            question=payload.question, verdict=payload.verdict,
            tried_by=user["username"], answer=payload.answer,
            expected_points=payload.expected_points, comment=payload.comment,
            revision_no=payload.revision,
        )
    except NotFound as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/{product_id}/trials")
async def list_trials(
    product_id: str,
    revision: int | None = Query(default=None),
    _user: dict = Depends(current_user),
    service: KnowledgeProductService = Depends(get_product_service),
) -> list[dict[str, Any]]:
    try:
        return await service.list_trials(product_id, revision)
    except NotFound as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/{product_id}/publish-gate")
async def publish_gate(
    product_id: str,
    revision: int | None = Query(default=None),
    _user: dict = Depends(current_user),
    service: KnowledgeProductService = Depends(get_product_service),
) -> dict[str, Any]:
    """发布门禁清单。逐项返回——被挡住时人得知道卡在哪一条。"""
    try:
        return (await service.publish_gate(product_id, revision)).as_dict()
    except NotFound as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/{product_id}/publish")
async def publish(
    product_id: str,
    payload: PublishRequest,
    _user: dict = Depends(current_user),
    service: KnowledgeProductService = Depends(get_product_service),
) -> dict[str, Any]:
    try:
        return await service.publish(product_id, payload.revision, force=payload.force)
    except NotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except ReviewRejected as exc:
        gate = await service.publish_gate(product_id, payload.revision)
        raise HTTPException(
            422, detail={"error": "publish_gate_failed", "gate": gate.as_dict()}
        ) from exc


def _issue_payload(exc: ValidationRejected) -> dict[str, Any]:
    return {
        "error": "validation_rejected",
        "issues": [
            {
                "object_id": issue.object_id,
                "code": issue.code,
                "field": issue.field,
                "detail": issue.detail,
            }
            for issue in exc.issues
        ],
    }
