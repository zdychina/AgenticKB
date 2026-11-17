"""本体管理 REST — /api/kb/ontology（67号）。

Auth: 网关注入 X-KB-User + X-Internal-Auth → current_user。
Permissions via OntologyService（域管理员写 / 域成员读，67号 §九）。
机制权威 = docs/下一阶段/67-本体管理MVP设计-2026-11-17.md §六/§七。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile

from pydantic import BaseModel

from knowledge_mining.mining.kb.auth import current_user
from knowledge_mining.mining.kb.deps import (
    get_ontology_extract_service, get_ontology_import_service,
    get_ontology_service,
)
from knowledge_mining.mining.kb.services.ontology_extract_service import (
    MAX_TEXT_CHARS, OntologyExtractError,
)
from knowledge_mining.mining.kb.services.ontology_import_service import (
    MAX_UPLOAD_BYTES, ImportValidationFailed, OntologyImportError,
)
from knowledge_mining.mining.kb.services.ontology_service import (
    BatchInstantiateFailed, EndpointMismatch, Forbidden, InUse,
    InvalidAttributeValue, InvalidDomain, InvalidName, NotFound,
    OntologyService, UnknownAttribute,
)

router = APIRouter(prefix="/api/kb/ontology", tags=["kb-ontology"])


# ----------------------------------------------------------------- models

class LibraryCreate(BaseModel):
    domain: str
    name: str
    description: str | None = None


class LibraryUpdate(BaseModel):
    name: str | None = None
    description: str | None = None


class AttributeDefIn(BaseModel):
    name: str
    description: str | None = None
    sort_order: int | None = None


class OntologyCreate(BaseModel):
    name: str
    description: str | None = None
    attributes: list[AttributeDefIn] | None = None


class OntologyUpdate(BaseModel):
    name: str | None = None
    description: str | None = None


class AttributeUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    sort_order: int | None = None


class OntologyRelationCreate(BaseModel):
    name: str
    source_ontology_id: str
    target_ontology_id: str
    description: str | None = None


class OntologyRelationUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    source_ontology_id: str | None = None
    target_ontology_id: str | None = None


class ObjectCreate(BaseModel):
    name: str
    attributes: dict[str, Any] | None = None


class ObjectUpdate(BaseModel):
    name: str | None = None
    attributes: dict[str, Any] | None = None


class SemanticRelationCreate(BaseModel):
    ontology_relation_id: str
    source_object_id: str
    target_object_id: str


class ObjectImportCommit(BaseModel):
    import_token: str
    name_column: str
    attribute_columns: dict[str, str] | None = None


class ExtractSourceIn(BaseModel):
    """抽取输入源（§8.1）：text = 粘贴或前端 FileReader 读出的 txt/md；
    kb_document = 知识库文档（只读单向，§8.2）。"""
    kind: str
    text: str | None = None
    kb_id: str | None = None
    document_id: str | None = None


class ExtractStart(BaseModel):
    ontology_id: str
    source: ExtractSourceIn
    suggest_relations: bool = True
    model: str | None = None


class InstantiateObjectIn(BaseModel):
    name: str
    attributes: dict[str, Any] | None = None


class InstantiateRelationIn(BaseModel):
    """关系两端各用 index（本批新对象下标）或 object_id（库内已有对象）
    二选一（§6.8，「新/已有」任意组合）。"""
    ontology_relation_id: str
    source_index: int | None = None
    source_object_id: str | None = None
    target_index: int | None = None
    target_object_id: str | None = None


class InstantiateIn(BaseModel):
    ontology_id: str
    source: str = "extract"
    objects: list[InstantiateObjectIn]
    relations: list[InstantiateRelationIn] = []


# ----------------------------------------------------------------- helpers

def _map_error(exc: Exception) -> HTTPException:
    if isinstance(exc, NotFound):
        return HTTPException(404, str(exc) or "not found")
    if isinstance(exc, Forbidden):
        return HTTPException(403, str(exc) or "forbidden")
    if isinstance(exc, BatchInstantiateFailed):
        return HTTPException(422, detail={
            "message": exc.report.get("message", "校验失败"),
            "report": exc.report,
        })
    if isinstance(exc, (InUse,)):
        return HTTPException(409, str(exc))
    if isinstance(exc, EndpointMismatch):
        return HTTPException(400, str(exc))
    if isinstance(exc, (UnknownAttribute, InvalidAttributeValue)):
        return HTTPException(400, str(exc))
    if isinstance(exc, InvalidDomain):
        return HTTPException(400, f"invalid domain: {exc}")
    if isinstance(exc, InvalidName):
        return HTTPException(400, str(exc))
    # Duplicate 及其它 OntologyError
    return HTTPException(409, str(exc))


def _map_extract_error(exc: Exception) -> HTTPException:
    """抽取向导错误：text_too_long→400、extract_limit→429、其余源级
    错误→422（code 串）；KB/文档/token 类走 NotFound(404)。"""
    if isinstance(exc, OntologyExtractError):
        code = exc.code
        if code == "text_too_long":
            return HTTPException(
                400, f"源文本超过 {MAX_TEXT_CHARS} 字符上限")
        if code == "extract_limit":
            return HTTPException(429, "进行中的抽取任务过多，请稍后再试")
        return HTTPException(422, code)
    return _map_error(exc)


def _map_import_error(exc: Exception) -> HTTPException:
    """导入向导错误（口径同 artifact_import_service）：file_too_large→413、
    preview_expired→410、preview_limit→429、校验报告→422（detail 带
    {message, report}）、其余解析/映射错误→422。"""
    if isinstance(exc, ImportValidationFailed):
        return HTTPException(422, detail={
            "message": exc.report.get("message", "校验失败"),
            "report": exc.report,
        })
    if isinstance(exc, OntologyImportError):
        code = exc.code
        if code == "file_too_large":
            return HTTPException(413, f"文件超过 {MAX_UPLOAD_BYTES // (1024 * 1024)}MB 上限")
        if code == "preview_expired":
            return HTTPException(410, "导入会话已过期，请重新上传文件")
        if code == "preview_limit":
            return HTTPException(429, "活跃导入会话过多，请稍后再试")
        return HTTPException(422, code)
    return _map_error(exc)


# ----------------------------------------------------------------- 本体库

@router.post("/libraries")
async def create_library(
    body: LibraryCreate,
    user: dict = Depends(current_user),
    svc: OntologyService = Depends(get_ontology_service),
) -> dict:
    try:
        return await svc.create_library(
            domain=body.domain, name=body.name, description=body.description,
            user=user,
        )
    except Exception as exc:
        raise _map_error(exc) from exc


@router.get("/libraries")
async def list_libraries(
    domain: str = Query(..., min_length=1),
    q: str | None = None,
    user: dict = Depends(current_user),
    svc: OntologyService = Depends(get_ontology_service),
) -> list[dict]:
    try:
        return await svc.list_libraries(domain=domain, q=q, user=user)
    except Exception as exc:
        raise _map_error(exc) from exc


@router.get("/libraries/{library_id}")
async def get_library(
    library_id: str,
    user: dict = Depends(current_user),
    svc: OntologyService = Depends(get_ontology_service),
) -> dict:
    try:
        return await svc.get_library(library_id, user=user)
    except Exception as exc:
        raise _map_error(exc) from exc


@router.put("/libraries/{library_id}")
async def update_library(
    library_id: str,
    body: LibraryUpdate,
    user: dict = Depends(current_user),
    svc: OntologyService = Depends(get_ontology_service),
) -> dict:
    try:
        return await svc.update_library(
            library_id, name=body.name, description=body.description, user=user,
        )
    except Exception as exc:
        raise _map_error(exc) from exc


@router.delete("/libraries/{library_id}", status_code=204)
async def delete_library(
    library_id: str,
    user: dict = Depends(current_user),
    svc: OntologyService = Depends(get_ontology_service),
) -> Response:
    try:
        await svc.delete_library(library_id, user=user)
    except Exception as exc:
        raise _map_error(exc) from exc
    return Response(status_code=204)


# ----------------------------------------------------------------- 本体

@router.post("/libraries/{library_id}/ontologies", status_code=201)
async def create_ontology(
    library_id: str,
    body: OntologyCreate,
    user: dict = Depends(current_user),
    svc: OntologyService = Depends(get_ontology_service),
) -> dict:
    try:
        return await svc.create_ontology(
            library_id, name=body.name, description=body.description,
            attributes=(
                [a.model_dump() for a in body.attributes]
                if body.attributes else None
            ),
            user=user,
        )
    except Exception as exc:
        raise _map_error(exc) from exc


@router.get("/libraries/{library_id}/ontologies")
async def list_ontologies(
    library_id: str,
    user: dict = Depends(current_user),
    svc: OntologyService = Depends(get_ontology_service),
) -> list[dict]:
    try:
        return await svc.list_ontologies(library_id, user=user)
    except Exception as exc:
        raise _map_error(exc) from exc


@router.get("/ontologies/{ontology_id}")
async def get_ontology(
    ontology_id: str,
    user: dict = Depends(current_user),
    svc: OntologyService = Depends(get_ontology_service),
) -> dict:
    try:
        return await svc.get_ontology(ontology_id, user=user)
    except Exception as exc:
        raise _map_error(exc) from exc


@router.put("/ontologies/{ontology_id}")
async def update_ontology(
    ontology_id: str,
    body: OntologyUpdate,
    user: dict = Depends(current_user),
    svc: OntologyService = Depends(get_ontology_service),
) -> dict:
    try:
        return await svc.update_ontology(
            ontology_id, name=body.name, description=body.description,
            user=user,
        )
    except Exception as exc:
        raise _map_error(exc) from exc


@router.delete("/ontologies/{ontology_id}", status_code=204)
async def delete_ontology(
    ontology_id: str,
    user: dict = Depends(current_user),
    svc: OntologyService = Depends(get_ontology_service),
) -> Response:
    try:
        await svc.delete_ontology(ontology_id, user=user)
    except Exception as exc:
        raise _map_error(exc) from exc
    return Response(status_code=204)


# ----------------------------------------------------------------- 属性

@router.post("/ontologies/{ontology_id}/attributes", status_code=201)
async def add_attribute(
    ontology_id: str,
    body: AttributeDefIn,
    user: dict = Depends(current_user),
    svc: OntologyService = Depends(get_ontology_service),
) -> dict:
    try:
        return await svc.add_attribute(
            ontology_id, name=body.name, description=body.description,
            sort_order=body.sort_order, user=user,
        )
    except Exception as exc:
        raise _map_error(exc) from exc


@router.put("/attributes/{attribute_id}")
async def update_attribute(
    attribute_id: str,
    body: AttributeUpdate,
    user: dict = Depends(current_user),
    svc: OntologyService = Depends(get_ontology_service),
) -> dict:
    try:
        return await svc.update_attribute(
            attribute_id, name=body.name, description=body.description,
            sort_order=body.sort_order, user=user,
        )
    except Exception as exc:
        raise _map_error(exc) from exc


@router.delete("/attributes/{attribute_id}", status_code=204)
async def delete_attribute(
    attribute_id: str,
    user: dict = Depends(current_user),
    svc: OntologyService = Depends(get_ontology_service),
) -> Response:
    try:
        await svc.delete_attribute(attribute_id, user=user)
    except Exception as exc:
        raise _map_error(exc) from exc
    return Response(status_code=204)


# ----------------------------------------------------------------- 本体关系

@router.post("/libraries/{library_id}/relations", status_code=201)
async def create_ontology_relation(
    library_id: str,
    body: OntologyRelationCreate,
    user: dict = Depends(current_user),
    svc: OntologyService = Depends(get_ontology_service),
) -> dict:
    try:
        return await svc.create_ontology_relation(
            library_id, name=body.name, description=body.description,
            source_ontology_id=body.source_ontology_id,
            target_ontology_id=body.target_ontology_id,
            user=user,
        )
    except Exception as exc:
        raise _map_error(exc) from exc


@router.get("/libraries/{library_id}/relations")
async def list_ontology_relations(
    library_id: str,
    user: dict = Depends(current_user),
    svc: OntologyService = Depends(get_ontology_service),
) -> list[dict]:
    try:
        return await svc.list_ontology_relations(library_id, user=user)
    except Exception as exc:
        raise _map_error(exc) from exc


@router.put("/relations/{relation_id}")
async def update_ontology_relation(
    relation_id: str,
    body: OntologyRelationUpdate,
    user: dict = Depends(current_user),
    svc: OntologyService = Depends(get_ontology_service),
) -> dict:
    try:
        return await svc.update_ontology_relation(
            relation_id, name=body.name, description=body.description,
            source_ontology_id=body.source_ontology_id,
            target_ontology_id=body.target_ontology_id,
            user=user,
        )
    except Exception as exc:
        raise _map_error(exc) from exc


@router.delete("/relations/{relation_id}", status_code=204)
async def delete_ontology_relation(
    relation_id: str,
    user: dict = Depends(current_user),
    svc: OntologyService = Depends(get_ontology_service),
) -> Response:
    try:
        await svc.delete_ontology_relation(relation_id, user=user)
    except Exception as exc:
        raise _map_error(exc) from exc
    return Response(status_code=204)


# ----------------------------------------------------------------- 语义对象

@router.post("/ontologies/{ontology_id}/objects", status_code=201)
async def create_object(
    ontology_id: str,
    body: ObjectCreate,
    user: dict = Depends(current_user),
    svc: OntologyService = Depends(get_ontology_service),
) -> dict:
    try:
        return await svc.create_object(
            ontology_id, name=body.name, attributes=body.attributes, user=user,
        )
    except Exception as exc:
        raise _map_error(exc) from exc


@router.get("/libraries/{library_id}/objects")
async def list_objects(
    library_id: str,
    ontology_id: str | None = None,
    q: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: dict = Depends(current_user),
    svc: OntologyService = Depends(get_ontology_service),
) -> dict:
    try:
        return await svc.list_objects(
            library_id, ontology_id=ontology_id, q=q,
            limit=limit, offset=offset, user=user,
        )
    except Exception as exc:
        raise _map_error(exc) from exc


@router.get("/objects/{object_id}")
async def get_object(
    object_id: str,
    user: dict = Depends(current_user),
    svc: OntologyService = Depends(get_ontology_service),
) -> dict:
    try:
        return await svc.get_object(object_id, user=user)
    except Exception as exc:
        raise _map_error(exc) from exc


@router.put("/objects/{object_id}")
async def update_object(
    object_id: str,
    body: ObjectUpdate,
    user: dict = Depends(current_user),
    svc: OntologyService = Depends(get_ontology_service),
) -> dict:
    try:
        return await svc.update_object(
            object_id, name=body.name, attributes=body.attributes, user=user,
        )
    except Exception as exc:
        raise _map_error(exc) from exc


@router.delete("/objects/{object_id}", status_code=204)
async def delete_object(
    object_id: str,
    user: dict = Depends(current_user),
    svc: OntologyService = Depends(get_ontology_service),
) -> Response:
    try:
        await svc.delete_object(object_id, user=user)
    except Exception as exc:
        raise _map_error(exc) from exc
    return Response(status_code=204)


@router.get("/objects/{object_id}/neighbors")
async def get_object_neighbors(
    object_id: str,
    user: dict = Depends(current_user),
    svc: OntologyService = Depends(get_ontology_service),
) -> dict:
    try:
        return await svc.get_object_neighbors(object_id, user=user)
    except Exception as exc:
        raise _map_error(exc) from exc


# ----------------------------------------------------------------- 批量导入

@router.post("/ontologies/{ontology_id}/objects/import/upload", status_code=202)
async def upload_object_import(
    ontology_id: str,
    file: UploadFile = File(...),
    user: dict = Depends(current_user),
    svc: OntologyService = Depends(get_ontology_service),
    imp: "Any" = Depends(get_ontology_import_service),
) -> dict:
    """Step 1 上传解析（67号 §7.2）：multipart .csv/.xlsx → 列清单 + 前 20
    行预览 + 自动映射预选 + import_token（1h 一次性）。openpyxl 解析在服务
    内经 anyio.to_thread 移出事件循环。"""
    try:
        content = await file.read(MAX_UPLOAD_BYTES + 1)
        return await imp.parse_upload(
            svc, ontology_id=ontology_id, file_bytes=content,
            file_name=file.filename or "", user=user,
        )
    except Exception as exc:
        raise _map_import_error(exc) from exc


@router.post("/ontologies/{ontology_id}/objects/import/commit")
async def commit_object_import(
    ontology_id: str,
    body: ObjectImportCommit,
    user: dict = Depends(current_user),
    svc: OntologyService = Depends(get_ontology_service),
    imp: "Any" = Depends(get_ontology_import_service),
) -> dict:
    """Step 2 提交导入：全量逐行校验（任一错误 422 整批拒绝，token 保留）
    → 全部通过 executemany 单事务落库（source='import'），token 失效。"""
    try:
        return await imp.commit_import(
            svc, ontology_id=ontology_id, import_token=body.import_token,
            name_column=body.name_column,
            attribute_columns=body.attribute_columns, user=user,
        )
    except Exception as exc:
        raise _map_import_error(exc) from exc


# --------------------------------------------- AI 辅助实例化（67号 §八）

@router.post("/libraries/{library_id}/extract", status_code=202)
async def start_ontology_extract(
    library_id: str,
    body: ExtractStart,
    user: dict = Depends(current_user),
    svc: OntologyService = Depends(get_ontology_service),
    ext: "Any" = Depends(get_ontology_extract_service),
) -> dict:
    """发起 AI 抽取（§8.3）：同步护栏（写权限 + 源文本装配，KB 文档只读）
    → 内存任务登记 → 后台 LLM 执行，立即返回 extract_token（1h 有效）。"""
    try:
        return await ext.start_extract(
            svc, library_id=library_id, ontology_id=body.ontology_id,
            source=body.source.model_dump(),
            suggest_relations=body.suggest_relations,
            model=body.model, user=user,
        )
    except Exception as exc:
        raise _map_extract_error(exc) from exc


@router.get("/libraries/{library_id}/extract/{extract_token}")
async def get_ontology_extract(
    library_id: str,
    extract_token: str,
    user: dict = Depends(current_user),
    ext: "Any" = Depends(get_ontology_extract_service),
) -> dict:
    """轮询抽取结果（前端 2s）：pending / done（candidates：对象候选带
    conflict 标记 + 关系候选带 endpoint_ok 预检 + warnings）/ failed。"""
    try:
        return await ext.get_extract(
            library_id=library_id, extract_token=extract_token, user=user,
        )
    except Exception as exc:
        raise _map_extract_error(exc) from exc


@router.post("/libraries/{library_id}/instantiate", status_code=201)
async def instantiate_batch(
    library_id: str,
    body: InstantiateIn,
    user: dict = Depends(current_user),
    svc: OntologyService = Depends(get_ontology_service),
) -> dict:
    """批量实例化（§6.8）：一批对象 + 一批关系单事务全有或全无；AI 确认
    落库与任何前端批量创建共用。任一校验失败 → 422 报告（objects[i]/
    relations[j] 定位），不落任何数据。"""
    try:
        return await svc.instantiate_batch(
            library_id, ontology_id=body.ontology_id, source=body.source,
            objects=[item.model_dump() for item in body.objects],
            relations=[item.model_dump() for item in body.relations],
            user=user,
        )
    except Exception as exc:
        raise _map_error(exc) from exc


# ----------------------------------------------------------------- 语义关系

@router.post("/semantic-relations", status_code=201)
async def create_semantic_relation(
    body: SemanticRelationCreate,
    user: dict = Depends(current_user),
    svc: OntologyService = Depends(get_ontology_service),
) -> dict:
    try:
        return await svc.create_semantic_relation(
            ontology_relation_id=body.ontology_relation_id,
            source_object_id=body.source_object_id,
            target_object_id=body.target_object_id,
            user=user,
        )
    except Exception as exc:
        raise _map_error(exc) from exc


@router.get("/libraries/{library_id}/semantic-relations")
async def list_semantic_relations(
    library_id: str,
    source_object_id: str | None = None,
    target_object_id: str | None = None,
    ontology_relation_id: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: dict = Depends(current_user),
    svc: OntologyService = Depends(get_ontology_service),
) -> dict:
    try:
        return await svc.list_semantic_relations(
            library_id, source_object_id=source_object_id,
            target_object_id=target_object_id,
            ontology_relation_id=ontology_relation_id,
            limit=limit, offset=offset, user=user,
        )
    except Exception as exc:
        raise _map_error(exc) from exc


@router.delete("/semantic-relations/{relation_id}", status_code=204)
async def delete_semantic_relation(
    relation_id: str,
    user: dict = Depends(current_user),
    svc: OntologyService = Depends(get_ontology_service),
) -> Response:
    try:
        await svc.delete_semantic_relation(relation_id, user=user)
    except Exception as exc:
        raise _map_error(exc) from exc
    return Response(status_code=204)


# ----------------------------------------------------------------- 图查询

@router.get("/libraries/{library_id}/schema")
async def get_library_schema(
    library_id: str,
    user: dict = Depends(current_user),
    svc: OntologyService = Depends(get_ontology_service),
) -> dict:
    try:
        return await svc.get_library_schema(library_id, user=user)
    except Exception as exc:
        raise _map_error(exc) from exc
