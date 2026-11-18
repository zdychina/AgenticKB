"""69号 P0：MCP 本体图查询内部端点（只读三件套）。

mcp_server 持 X-Internal-Auth 转发，路径前缀同 mcp_tools（静态字面量，
须在 kb_router 动态 /api/kb/{kb_id} 之前注册——由 app.py 挂载顺序保证）。

授权模型 = **钥匙的域 × 用户的域成员身份**（69号 §3.3）：钥匙定域（单域钥匙），
用户须是钥匙域的成员/站点管理员（service `_require_read` 同口径）；访问库级
资源再收一层 library.domain == key.domain（不匹配 → 404 不泄露存在性）。
与 kb 族的 key_open_kb_ids（按库开放）刻意不同粒度：本体库没有按库开放开关。

名称→id 解析：图载荷没有 ontologies 对应表、SQL 过滤只认 ontology_ids——
借 get_library_schema 的 {ontologies} 既做 Agent 复述的摘要键也做名称解析
（库内名唯一有 UNIQUE(library_id,name) 兜底，未知名 422 列出合法名）。
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from knowledge_mining.mining.kb.db import KbDB
from knowledge_mining.mining.kb.deps import get_kb_db, get_ontology_service
from knowledge_mining.mining.kb.routes.mcp_tools import _key_scope, _require_internal
from knowledge_mining.mining.kb.services.ontology_service import (
    Forbidden, InvalidDomain, NotFound, OntologyService,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/kb/mcp-tools", tags=["kb-mcp-ontology"])

#: 实例图节点上限（与网页端 GET /libraries/{id}/graph 的 limit 上限一致）。
_GRAPH_LIMIT_MAX = 2000


async def _ontology_scope(
    kbdb: KbDB, username: str, key_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """_key_scope（钥匙存在/本人/active）+ 完整 user 行（授权要 site_role）。"""
    user_id, key = await _key_scope(kbdb, username, key_id)
    user = await kbdb.get_user_by_username(username)
    if user is None or user["id"] != user_id:  # pragma: no cover - 竞态删除
        raise HTTPException(401, "invalid mcp key")
    return user, key


def _map_service_error(exc: Exception) -> HTTPException:
    if isinstance(exc, NotFound):
        return HTTPException(404, str(exc) or "not found")
    if isinstance(exc, Forbidden):
        return HTTPException(403, str(exc) or "forbidden")
    if isinstance(exc, InvalidDomain):
        return HTTPException(400, f"invalid domain: {exc}")
    return HTTPException(500, str(exc))


@router.post("/ontology/list-libraries", dependencies=[Depends(_require_internal)])
async def mcp_list_ontology_libraries(
    body: dict[str, Any],
    kbdb: KbDB = Depends(get_kb_db),
    svc: OntologyService = Depends(get_ontology_service),
) -> dict[str, Any]:
    """钥匙域内的本体库清单：id/name/description/本体数/对象数/关系数。"""
    user, key = await _ontology_scope(
        kbdb, str(body.get("username") or ""), str(body.get("key_id") or ""),
    )
    try:
        libraries = await svc.list_libraries(
            domain=key["domain"], q=None, user=user,
        )
    except Exception as exc:
        raise _map_service_error(exc) from exc
    return {"domain": key["domain"], "libraries": libraries}


@router.post("/ontology/library-graph", dependencies=[Depends(_require_internal)])
async def mcp_ontology_library_graph(
    body: dict[str, Any],
    kbdb: KbDB = Depends(get_kb_db),
    svc: OntologyService = Depends(get_ontology_service),
) -> dict[str, Any]:
    """库实例图（68号 §2.2 载荷）+ ontologies 摘要键（id/name 对应表）。

    ontology_names（名称比 id 对 Agent 友好）可过滤本体子集，未知名 422
    附合法名清单；limit 1..2000 与网页端口径一致。
    """
    user, key = await _ontology_scope(
        kbdb, str(body.get("username") or ""), str(body.get("key_id") or ""),
    )
    library_id = str(body.get("library_id") or "")
    try:
        # 读权限先行（404 缺失 / 403 非域成员），再做钥匙域收口
        library = await svc.get_library(library_id, user=user)
        if library.get("domain") != key["domain"]:
            raise HTTPException(404, "library not found")
        schema = await svc.get_library_schema(library_id, user=user)
    except HTTPException:
        raise
    except Exception as exc:
        raise _map_service_error(exc) from exc

    ontologies = schema.get("ontologies") or []
    summary = [{"id": o["id"], "name": o["name"]} for o in ontologies]

    ontology_ids: list[str] | None = None
    names = body.get("ontology_names")
    if names is not None:
        if not isinstance(names, list) or not all(
            isinstance(n, str) and n.strip() for n in names
        ):
            raise HTTPException(422, "ontology_names 须为非空字符串数组")
        by_name = {o["name"]: o["id"] for o in ontologies}
        unknown = [n for n in names if n not in by_name]
        if unknown:
            raise HTTPException(422, detail={
                "message": "ontology_names 含库内不存在的本体名",
                "unknown_names": unknown,
                "valid_names": [o["name"] for o in ontologies],
            })
        ontology_ids = [by_name[n] for n in names]

    raw_limit = body.get("limit", 2000)
    try:
        limit = int(raw_limit)
    except (TypeError, ValueError):
        raise HTTPException(422, f"limit 须为 1..{_GRAPH_LIMIT_MAX} 的整数") from None
    if not 1 <= limit <= _GRAPH_LIMIT_MAX:
        raise HTTPException(422, f"limit 须为 1..{_GRAPH_LIMIT_MAX} 的整数")

    try:
        graph = await svc.get_library_graph(
            library_id, ontology_ids=ontology_ids, limit=limit, user=user,
        )
    except Exception as exc:
        raise _map_service_error(exc) from exc
    graph["ontologies"] = summary
    return graph


@router.post("/ontology/object-neighbors", dependencies=[Depends(_require_internal)])
async def mcp_ontology_object_neighbors(
    body: dict[str, Any],
    kbdb: KbDB = Depends(get_kb_db),
    svc: OntologyService = Depends(get_ontology_service),
) -> dict[str, Any]:
    """对象卡（名称/本体/属性/溯源）+ 一度邻接 {outgoing[], incoming[]}。"""
    user, key = await _ontology_scope(
        kbdb, str(body.get("username") or ""), str(body.get("key_id") or ""),
    )
    object_id = str(body.get("object_id") or "")
    try:
        detail = await svc.get_object(object_id, user=user)
        # 对象→库→域校验链：钥匙域不覆盖对象属库 → 404 不泄露存在性
        library = await svc.get_library(detail["library_id"], user=user)
        if library.get("domain") != key["domain"]:
            raise HTTPException(404, "object not found")
        neighbors = await svc.get_object_neighbors(object_id, user=user)
    except HTTPException:
        raise
    except Exception as exc:
        raise _map_service_error(exc) from exc

    card = {
        k: detail.get(k) for k in (
            "id", "name", "ontology_id", "ontology_name", "attributes_json",
            "source", "source_document_id", "source_document_name",
            "source_kb_id", "outgoing_count", "incoming_count", "created_at",
        )
    }
    return {
        "object": card,
        "outgoing": neighbors.get("outgoing") or [],
        "incoming": neighbors.get("incoming") or [],
    }
