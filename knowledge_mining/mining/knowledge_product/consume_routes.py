"""Published-surface routes — /api/published-products（52号 P6）。

独立前缀而不是挂在 ``/api/knowledge-products/published`` 下面：后者的第二段会被
``/{product_id}`` 这条动态路由抢先匹配。前缀分开也让「这是消费面、只读已发布」
一眼可见。

两类调用方同一份数据（48号 §七：网页和 MCP 必须读到同一个发布结果）：
- 前端带用户身份走代理，domain 由 proxyClient 注入；
- ``mcp_server`` 持 ``X-Internal-Auth`` 转发，domain 由钥匙绑定的单域声明。
  这两条在 ``auth_guard`` 的 service-only 豁免名单里，路由内自验内部密钥。
"""
from __future__ import annotations

from hmac import compare_digest
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from knowledge_mining.mining.api.deps import get_domain_async_pool
from knowledge_mining.mining.kb.auth import current_user
from knowledge_mining.mining.knowledge_product.consume import ConsumeRejected
from knowledge_mining.mining.knowledge_product.consume_service import ProductConsumeService

router = APIRouter(prefix="/api/published-products", tags=["knowledge-product"])
tool_router = APIRouter(prefix="/api/product-consume", tags=["knowledge-product"])


async def get_consume_service(
    request: Request, domain: str = Query(...),
) -> ProductConsumeService:
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
    return ProductConsumeService(
        KnowledgeProductRepository(pool),
        ArtifactContentStore(
            request.app.state.object_store,
            PgStorageObjectRepository(pool),
            bucket_for(config.bucket_prefix),
        ),
    )


def _require_internal(request: Request) -> None:
    from knowledge_mining.mining.kb.auth import get_internal_verify_secret

    secret = get_internal_verify_secret()
    if not secret:
        raise HTTPException(401, "auth not initialized")
    if not compare_digest(request.headers.get("X-Internal-Auth", ""), secret):
        raise HTTPException(401, "unauthenticated")


def _rejected(exc: ConsumeRejected) -> HTTPException:
    status = {
        "INVALID_ARGUMENT": 422,
        "OBJECT_NOT_FOUND": 404,
        "RESULT_TOO_LARGE": 413,
    }.get(exc.code, 400)
    return HTTPException(status, detail={"code": exc.code, "message": exc.message})


# ----------------------------------------------------------------- models


class FetchRequest(BaseModel):
    ids: list[str] = Field(min_length=1, description="对象逻辑 ID，去重后至多 50 个")


class SearchRequest(BaseModel):
    terms: list[str] = Field(min_length=1, max_length=10)
    match: str = Field(default="any", pattern="^(any|all)$")
    product_id: str | None = None
    type: str | None = None
    page: int = Field(default=1, ge=1)
    size: int = Field(default=20, ge=1, le=50)


# ----------------------------------------------------------------- 前端面


@router.get("")
async def catalog(
    _user: dict = Depends(current_user),
    service: ProductConsumeService = Depends(get_consume_service),
) -> list[dict[str, Any]]:
    return await service.catalog()


@router.get("/{product_id}")
async def outline(
    product_id: str,
    _user: dict = Depends(current_user),
    service: ProductConsumeService = Depends(get_consume_service),
) -> dict[str, Any]:
    try:
        return await service.outline(product_id)
    except ConsumeRejected as exc:
        raise _rejected(exc) from exc


@router.post("/fetch")
async def fetch(
    payload: FetchRequest,
    _user: dict = Depends(current_user),
    service: ProductConsumeService = Depends(get_consume_service),
) -> dict[str, Any]:
    try:
        return await service.fetch(payload.ids)
    except ConsumeRejected as exc:
        raise _rejected(exc) from exc


@router.post("/search")
async def search(
    payload: SearchRequest,
    _user: dict = Depends(current_user),
    service: ProductConsumeService = Depends(get_consume_service),
) -> dict[str, Any]:
    try:
        result = await service.search(
            payload.terms, match=payload.match, product_id=payload.product_id,
            type_name=payload.type, page=payload.page, size=payload.size,
        )
    except ConsumeRejected as exc:
        raise _rejected(exc) from exc
    return result.as_dict()


# ----------------------------------------------------------------- 工具面


class ToolFetch(FetchRequest):
    pass


class ToolSearch(SearchRequest):
    pass


@tool_router.post("/catalog")
async def tool_catalog(
    request: Request,
    service: ProductConsumeService = Depends(get_consume_service),
) -> dict[str, Any]:
    _require_internal(request)
    return {"products": await service.catalog()}


@tool_router.post("/outline")
async def tool_outline(
    request: Request,
    product_id: str = Query(...),
    service: ProductConsumeService = Depends(get_consume_service),
) -> dict[str, Any]:
    _require_internal(request)
    try:
        return await service.outline(product_id)
    except ConsumeRejected as exc:
        raise _rejected(exc) from exc


@tool_router.post("/fetch")
async def tool_fetch(
    payload: ToolFetch,
    request: Request,
    service: ProductConsumeService = Depends(get_consume_service),
) -> dict[str, Any]:
    _require_internal(request)
    try:
        return {"objects": await service.fetch(payload.ids)}
    except ConsumeRejected as exc:
        raise _rejected(exc) from exc


@tool_router.post("/search")
async def tool_search(
    payload: ToolSearch,
    request: Request,
    service: ProductConsumeService = Depends(get_consume_service),
) -> dict[str, Any]:
    _require_internal(request)
    try:
        result = await service.search(
            payload.terms, match=payload.match, product_id=payload.product_id,
            type_name=payload.type, page=payload.page, size=payload.size,
        )
    except ConsumeRejected as exc:
        raise _rejected(exc) from exc
    return result.as_dict()
