"""69号批次0 · 抽取溯源（semantic_objects.source_document_id）真库测试。

覆盖（69号 §二/§五）：extract 通道 instantiate 带 source_document_id 落库，
列表/详情回读带 source_document_name/source_kb_id（LEFT JOIN asset_documents
滤 deleted_at）；manual/import 通道不溯（manual 显式传 → 服务层 InvalidName
+ 路由层模型 ValidationError 422；import 走 upload/commit 全链断言列为 NULL）；
文档软删后读取降级（id 仍在、name/kb_id → null，软删恢复自动回显）。
本机无 PG 时整个模块在收集期跳过（与 KB_RUN_POSTGRES_ACCEPTANCE 同款门槛）。
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]

if os.environ.get("KB_RUN_POSTGRES_ACCEPTANCE") != "1":
    pytest.skip(
        "set KB_RUN_POSTGRES_ACCEPTANCE=1 to run real PostgreSQL tests",
        allow_module_level=True,
    )

import pydantic  # noqa: E402

from knowledge_mining.mining.kb.db import KbDB  # noqa: E402
from knowledge_mining.mining.kb.db_ontology import OntologyDB  # noqa: E402
from knowledge_mining.mining.kb.routes.ontology import InstantiateIn  # noqa: E402
from knowledge_mining.mining.kb.services.ontology_import_service import (  # noqa: E402
    OntologyImportService,
)
from knowledge_mining.mining.kb.services.ontology_service import (  # noqa: E402
    InvalidName, OntologyService,
)

_DOMAIN = "cloud_core_network"


def _svc(pool) -> OntologyService:
    return OntologyService(OntologyDB(pool))


def _imp(pool) -> OntologyImportService:
    return OntologyImportService(OntologyDB(pool))


async def _make_admin(pool) -> dict:
    kbdb = KbDB(pool)
    user = await kbdb.create_user(username=f"ontprov-{uuid.uuid4().hex[:8]}")
    async with pool.connection() as conn:
        await conn.execute(
            """INSERT INTO user_domains (user_id, domain, domain_role)
               VALUES (%s, %s, 'admin')""",
            (user["id"], _DOMAIN),
        )
    return user


async def _make_library(svc, admin: dict) -> tuple[str, str]:
    """建库 + 本体，返回 (library_id, ontology_id)。"""
    lib = await svc.create_library(
        domain=_DOMAIN, name=f"溯源测试库-{uuid.uuid4().hex[:6]}",
        description=None, user=admin,
    )
    onto = await svc.create_ontology(
        lib["id"], name="网元", description=None, attributes=None, user=admin,
    )
    return lib["id"], onto["id"]


async def _insert_document(pool, admin: dict) -> str:
    """建真 KB（asset_documents.kb_id 有 FK）+ 直插一篇资产文档。"""
    kb = await KbDB(pool).create_kb(
        domain=_DOMAIN, name=f"溯源测试KB-{uuid.uuid4().hex[:6]}",
        owner_id=admin["id"],
    )
    doc_id = f"doc-{uuid.uuid4().hex[:12]}"
    async with pool.connection() as conn:
        await conn.execute(
            """INSERT INTO asset_documents
               (id, domain, document_key, document_name, document_type,
                metadata_json, created_at, kb_id)
               VALUES (%s, %s, %s, %s, 'other', '{}', %s, %s)""",
            (doc_id, _DOMAIN, f"prov:{doc_id}", "AMF开局指导书.md",
             datetime.now(timezone.utc).isoformat(), kb["id"]),
        )
    return doc_id


# ------------------------------------------------------------ 写入与回读

async def test_extract_provenance_roundtrip(async_pool) -> None:
    svc = _svc(async_pool)
    admin = await _make_admin(async_pool)
    library_id, ontology_id = await _make_library(svc, admin)
    doc_id = await _insert_document(async_pool, admin)

    result = await svc.instantiate_batch(
        library_id, ontology_id=ontology_id, source="extract",
        objects=[{"name": "AMF", "attributes": {}}],
        relations=[], user=admin, source_document_id=doc_id,
    )
    object_id = result["object_ids"][0]

    detail = await svc.get_object(object_id, user=admin)
    assert detail["source_document_id"] == doc_id
    assert detail["source_document_name"] == "AMF开局指导书.md"
    assert detail["source_kb_id"] is not None

    listing = await svc.list_objects(
        library_id, ontology_id=ontology_id, q=None,
        limit=50, offset=0, user=admin,
    )
    row = next(o for o in listing["items"] if o["id"] == object_id)
    assert row["source_document_id"] == doc_id
    assert row["source_document_name"] == "AMF开局指导书.md"
    assert row["source_kb_id"] == detail["source_kb_id"]


async def test_extract_provenance_optional(async_pool) -> None:
    """extract 不带 source_document_id 也可落库（text/file 源无文档可溯）。"""
    svc = _svc(async_pool)
    admin = await _make_admin(async_pool)
    library_id, ontology_id = await _make_library(svc, admin)

    result = await svc.instantiate_batch(
        library_id, ontology_id=ontology_id, source="extract",
        objects=[{"name": "SMF", "attributes": {}}],
        relations=[], user=admin, source_document_id=None,
    )
    detail = await svc.get_object(result["object_ids"][0], user=admin)
    assert detail["source_document_id"] is None
    assert detail["source_document_name"] is None
    assert detail["source_kb_id"] is None


# ------------------------------------------------------------ 通道收口

async def test_manual_channel_rejects_source_document(async_pool) -> None:
    svc = _svc(async_pool)
    admin = await _make_admin(async_pool)
    library_id, ontology_id = await _make_library(svc, admin)

    # 服务层直调：InvalidName（路由层映射 400）
    with pytest.raises(InvalidName):
        await svc.instantiate_batch(
            library_id, ontology_id=ontology_id, source="manual",
            objects=[{"name": "UPF", "attributes": {}}],
            relations=[], user=admin, source_document_id="doc-x",
        )

    # 路由层模型：pydantic ValidationError → FastAPI 自动 422
    with pytest.raises(pydantic.ValidationError):
        InstantiateIn(
            ontology_id=ontology_id, source="manual",
            objects=[{"name": "UPF"}],
            source_document_id="doc-x",
        )
    # 零写入
    listing = await svc.list_objects(
        library_id, ontology_id=ontology_id, q=None,
        limit=50, offset=0, user=admin,
    )
    assert listing["items"] == []


async def test_import_channel_writes_null_provenance(async_pool) -> None:
    """import 走 upload/commit 真链路落库，溯源列保持 NULL。"""
    svc = _svc(async_pool)
    imp = _imp(async_pool)
    admin = await _make_admin(async_pool)
    library_id, ontology_id = await _make_library(svc, admin)

    upload = await imp.parse_upload(
        svc, ontology_id=ontology_id,
        file_bytes="设备名称\nAMF\nSMF\n".encode("utf-8"),
        file_name="网元.csv", user=admin,
    )
    await imp.commit_import(
        svc, ontology_id=ontology_id, import_token=upload["import_token"],
        name_column="设备名称", attribute_columns=None, user=admin,
    )

    listing = await svc.list_objects(
        library_id, ontology_id=ontology_id, q=None,
        limit=50, offset=0, user=admin,
    )
    assert {o["name"] for o in listing["items"]} == {"AMF", "SMF"}
    for row in listing["items"]:
        assert row["source"] == "import"
        assert row["source_document_id"] is None
        assert row["source_document_name"] is None
        assert row["source_kb_id"] is None


# ------------------------------------------------------------ 删除降级

async def test_soft_deleted_document_degrades(async_pool) -> None:
    svc = _svc(async_pool)
    admin = await _make_admin(async_pool)
    library_id, ontology_id = await _make_library(svc, admin)
    doc_id = await _insert_document(async_pool, admin)

    result = await svc.instantiate_batch(
        library_id, ontology_id=ontology_id, source="extract",
        objects=[{"name": "AMF", "attributes": {}}],
        relations=[], user=admin, source_document_id=doc_id,
    )
    object_id = result["object_ids"][0]

    async with async_pool.connection() as conn:
        await conn.execute(
            "UPDATE asset_documents SET deleted_at = %s WHERE id = %s",
            (datetime.now(timezone.utc).isoformat(), doc_id),
        )

    detail = await svc.get_object(object_id, user=admin)
    assert detail["source_document_id"] == doc_id          # 事实仍在
    assert detail["source_document_name"] is None          # 展示降级
    assert detail["source_kb_id"] is None

    # 软删恢复 → 自动回显（不加 FK 的意义所在）
    async with async_pool.connection() as conn:
        await conn.execute(
            "UPDATE asset_documents SET deleted_at = NULL WHERE id = %s",
            (doc_id,),
        )
    detail = await svc.get_object(object_id, user=admin)
    assert detail["source_document_name"] == "AMF开局指导书.md"
