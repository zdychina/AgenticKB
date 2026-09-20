"""Creation-face routes（52号 P2）。

两组端点，身份模型不同：

- ``/api/knowledge-products/{id}/creation-instances`` 等**管理端点**走普通用户身份
  （负责人在平台页面上起/停制作）。
- ``/api/creation/context`` 与 ``/api/creation/submit`` 是 **MCP 工具的后端**，由
  ``mcp_server`` 持 ``X-Internal-Auth`` 转发；调用方范围由 body 里的 ``task_ticket``
  决定，不是由服务身份决定。这两条必须进 ``auth_guard`` 的 service-only 豁免名单，
  并在路由内自验内部密钥（与 ``kb/routes/mcp_tools.py`` 同模式）。

票据明文只在起实例的响应里出现一次；此后任何端点都不回显它。
"""
from __future__ import annotations

import logging
from hmac import compare_digest
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from knowledge_mining.mining.api.deps import get_domain_async_pool
from knowledge_mining.mining.agent_creation.models import TicketRejected
from knowledge_mining.mining.agent_creation.service import AgentCreationService
from knowledge_mining.mining.kb.auth import current_user
from knowledge_mining.mining.knowledge_product.service import NotFound

logger = logging.getLogger(__name__)

# 管理面：跟着制品走
admin_router = APIRouter(prefix="/api/knowledge-products", tags=["knowledge-product"])
# 工具面：mcp_server 转发，票据鉴权
tool_router = APIRouter(prefix="/api/creation", tags=["knowledge-product"])


async def get_creation_service(
    request: Request, domain: str = Query(...),
) -> AgentCreationService:
    """同 ``get_product_service``：仓储走按域路由的 asset_core 池。

    工具面（``/api/creation/*``）没有前端注入 domain，由 mcp_server 按**钥匙绑定的
    单域**显式传——票据本身不带域，而要查票据先得选对库，这个先后顺序解不开，
    所以域必须由调用方声明。
    """
    from knowledge_mining.mining.agent_creation.repository import (
        CreationRepository,
        PgSegmentLocator,
    )
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
    from knowledge_mining.mining.knowledge_product.service import KnowledgeProductService

    pool = await get_domain_async_pool(request, domain)
    config = request.app.state.object_store_config
    product_repo = KnowledgeProductRepository(pool)
    products = KnowledgeProductService(
        product_repo,
        ArtifactContentStore(
            request.app.state.object_store,
            PgStorageObjectRepository(pool),
            bucket_for(config.bucket_prefix),
        ),
    )
    return AgentCreationService(
        CreationRepository(pool), products, product_repo, PgSegmentLocator(pool)
    )


def _require_internal(request: Request) -> None:
    from knowledge_mining.mining.kb.auth import get_internal_verify_secret

    secret = get_internal_verify_secret()
    if not secret:
        raise HTTPException(401, "auth not initialized")
    if not compare_digest(request.headers.get("X-Internal-Auth", ""), secret):
        raise HTTPException(401, "unauthenticated")


# ----------------------------------------------------------------- models


class StartInstance(BaseModel):
    ttl_seconds: int | None = Field(default=None, ge=60, le=24 * 3600)


class CancelInstance(BaseModel):
    reason: str = Field(default="任务已取消", max_length=500)


class ContextRequest(BaseModel):
    task_ticket: str = Field(min_length=1)


class SubmitRequest(BaseModel):
    task_ticket: str = Field(min_length=1)
    submission_id: str = Field(min_length=1, max_length=200, description="Agent 生成的稳定 UUID")
    based_on_draft_revision: int = Field(ge=0)
    documents: list[str] = Field(min_length=1, description="本批对象的 md 正文")
    product_id: str | None = None


# ----------------------------------------------------------------- 管理面


@admin_router.post("/{product_id}/creation-instances", status_code=201)
async def start_instance(
    product_id: str,
    payload: StartInstance,
    user: dict = Depends(current_user),
    service: AgentCreationService = Depends(get_creation_service),
) -> dict[str, Any]:
    """起一个制作实例并签票。

    **票据明文只在这次响应里出现**——平台随后把它发给 DSH 会话，不交给浏览器。
    """
    from datetime import timedelta

    try:
        instance, ticket = await service.start_instance(
            product_id,
            created_by=user["username"],
            ttl=timedelta(seconds=payload.ttl_seconds) if payload.ttl_seconds else None,
        )
    except NotFound as exc:
        raise HTTPException(404, str(exc)) from exc

    return {
        "instance": instance,
        "ticket": {
            "ticket_id": ticket.ticket_id,
            "token": ticket.token,
            "expires_at": ticket.expires_at,
        },
    }


@admin_router.post("/{product_id}/creation-instances/{instance_id}/cancel")
async def cancel_instance(
    product_id: str,
    instance_id: str,
    payload: CancelInstance,
    _user: dict = Depends(current_user),
    service: AgentCreationService = Depends(get_creation_service),
) -> dict[str, Any]:
    """取消实例并撤票。

    调 DSH 的 cancel 之后**仍必须撤票据**——否则队列里迟到的工具调用还能写回来。
    """
    await service.cancel_instance(instance_id, reason=payload.reason)
    return {"instance_id": instance_id, "status": "cancelled"}


@admin_router.post("/{product_id}/tickets/revoke")
async def revoke_product_tickets(
    product_id: str,
    payload: CancelInstance,
    _user: dict = Depends(current_user),
    service: AgentCreationService = Depends(get_creation_service),
) -> dict[str, Any]:
    """资料范围收缩 / 定义不兼容变更后，让该制品全部在用票据立即失效。"""
    revoked = await service.invalidate_product_tickets(product_id, reason=payload.reason)
    return {"product_id": product_id, "revoked": revoked}


# ----------------------------------------------------------------- 工具面


@tool_router.post("/context")
async def get_creation_context(
    payload: ContextRequest,
    request: Request,
    service: AgentCreationService = Depends(get_creation_service),
) -> dict[str, Any]:
    """MCP 工具 ``get_creation_context`` 的后端。"""
    _require_internal(request)
    try:
        return (await service.get_context(payload.task_ticket)).as_dict()
    except TicketRejected as exc:
        raise HTTPException(403, detail={"code": exc.code, "message": exc.message}) from exc
    except NotFound as exc:
        raise HTTPException(404, str(exc)) from exc


@tool_router.post("/submit")
async def submit_creation_result(
    payload: SubmitRequest,
    request: Request,
    service: AgentCreationService = Depends(get_creation_service),
) -> dict[str, Any]:
    """MCP 工具 ``submit_creation_result`` 的后端。

    校验不过是**回执里的结果**（rejected / conflict），不是 HTTP 错误——只有票据
    这一道不过才 403，因为那时候连「提交目标是谁」都不成立。
    """
    _require_internal(request)
    try:
        receipt = await service.submit(
            payload.task_ticket,
            submission_id=payload.submission_id,
            based_on_draft_revision=payload.based_on_draft_revision,
            documents=payload.documents,
            product_id=payload.product_id,
        )
    except TicketRejected as exc:
        raise HTTPException(403, detail={"code": exc.code, "message": exc.message}) from exc
    except NotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    return receipt.as_dict()


@admin_router.get("/{product_id}/creation-instances")
async def list_creation_instances(
    product_id: str,
    _user: dict = Depends(current_user),
    service: AgentCreationService = Depends(get_creation_service),
) -> list[dict[str, Any]]:
    """本制品的制作实例。**不回显任何票据信息**——那是只在签发时出现一次的东西。"""
    return await service._repo.list_instances(product_id)


@admin_router.get("/{product_id}/submissions")
async def list_submissions(
    product_id: str,
    _user: dict = Depends(current_user),
    service: AgentCreationService = Depends(get_creation_service),
) -> list[dict[str, Any]]:
    """提交回执。被拒的也在里面——「Agent 说交了但草稿里没有」要能对账。"""
    return await service._repo.list_product_submissions(product_id)
