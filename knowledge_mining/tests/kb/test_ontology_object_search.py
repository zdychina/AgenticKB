"""域级对象名搜索测试 —— 真库门控（69号 §4.1，search_objects）。

覆盖：名称命中跨库聚合（hits 带库/本体名与 degree 口径=出+入）；
域隔离（他域库不出现在他域 domain 参数结果里）；无权限域 → Forbidden
（路由层映射 403）；limit 截断 truncated 标记；manual 来源 hit 的
source_document_id 为 None。本机无 PG 时整个模块在收集期跳过（与
KB_RUN_POSTGRES_ACCEPTANCE 验收门同款门槛）。
"""
from __future__ import annotations

import os
import uuid

import pytest

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]

if os.environ.get("KB_RUN_POSTGRES_ACCEPTANCE") != "1":
    pytest.skip(
        "set KB_RUN_POSTGRES_ACCEPTANCE=1 to run real PostgreSQL tests",
        allow_module_level=True,
    )

from knowledge_mining.mining.kb.db import KbDB  # noqa: E402
from knowledge_mining.mining.kb.db_ontology import OntologyDB  # noqa: E402
from knowledge_mining.mining.kb.services.ontology_service import (  # noqa: E402
    Forbidden, OntologyService,
)

_DOMAIN = "cloud_core_network"
_OTHER_DOMAIN = "civil_engineering"


def _svc(pool) -> OntologyService:
    return OntologyService(OntologyDB(pool))


async def _make_user(pool, *, domain: str | None = None,
                     domain_admin: bool = False) -> dict:
    kbdb = KbDB(pool)
    user = await kbdb.create_user(username=f"objsearch-{uuid.uuid4().hex[:8]}")
    if domain is not None:
        async with pool.connection() as conn:
            await conn.execute(
                """INSERT INTO user_domains (user_id, domain, domain_role)
                   VALUES (%s, %s, %s)""",
                (user["id"], domain, "admin" if domain_admin else "member"),
            )
    return user


async def _mk_library(svc, admin: dict, *, domain: str = _DOMAIN) -> dict:
    return await svc.create_library(
        domain=domain, name=f"对象搜索库-{uuid.uuid4().hex[:6]}",
        description=None, user=admin,
    )


async def test_search_cross_library_hits_and_degree(async_pool) -> None:
    svc = _svc(async_pool)
    admin = await _make_user(async_pool, domain=_DOMAIN, domain_admin=True)
    member = await _make_user(async_pool, domain=_DOMAIN)
    token = uuid.uuid4().hex[:6]

    lib_a = await _mk_library(svc, admin)
    lib_b = await _mk_library(svc, admin)
    onto_a = await svc.create_ontology(
        lib_a["id"], name=f"网元-{token}", description=None,
        attributes=None, user=admin,
    )
    onto_b = await svc.create_ontology(
        lib_b["id"], name=f"业务-{token}", description=None,
        attributes=None, user=admin,
    )
    # lib_a：AMF-tok --承载--> UPF-tok（各 1 度）+ 孤立对象 MME-tok（0 度）
    amf = await svc.create_object(
        onto_a["id"], name=f"AMF-{token}", attributes=None, user=admin)
    upf = await svc.create_object(
        onto_a["id"], name=f"UPF-{token}", attributes=None, user=admin)
    mme = await svc.create_object(
        onto_a["id"], name=f"MME-{token}", attributes=None, user=admin)
    rel = await svc.create_ontology_relation(
        lib_a["id"], name=f"承载-{token}", description=None,
        source_ontology_id=onto_a["id"], target_ontology_id=onto_a["id"],
        user=admin,
    )
    await svc.create_semantic_relation(
        ontology_relation_id=rel["id"], source_object_id=amf["id"],
        target_object_id=upf["id"], user=admin,
    )
    # lib_b：同名前缀对象，验证跨库聚合
    slice_obj = await svc.create_object(
        onto_b["id"], name=f"切片-{token}", attributes=None, user=admin)

    result = await svc.search_objects(
        domain=_DOMAIN, q=token, limit=20, user=member)

    assert result["truncated"] is False
    hits = {h["object_id"]: h for h in result["hits"]}
    assert set(hits) == {amf["id"], upf["id"], mme["id"], slice_obj["id"]}
    # 跨库聚合：hit 带所属库/本体名（前端卡片直用）
    assert hits[amf["id"]] == {
        "object_id": amf["id"], "object_name": f"AMF-{token}",
        "source_document_id": None, "ontology_id": onto_a["id"],
        "library_id": lib_a["id"], "library_name": lib_a["name"],
        "ontology_name": onto_a["name"], "degree": 1,
    }
    assert hits[slice_obj["id"]]["library_id"] == lib_b["id"]
    assert hits[slice_obj["id"]]["ontology_name"] == onto_b["name"]
    # degree 口径 = 出 + 入：UPF 只有入边也是 1，MME 孤立 0
    assert hits[upf["id"]]["degree"] == 1
    assert hits[mme["id"]]["degree"] == 0


async def test_search_domain_isolation_and_forbidden(async_pool) -> None:
    svc = _svc(async_pool)
    admin = await _make_user(async_pool, domain=_DOMAIN, domain_admin=True)
    member = await _make_user(async_pool, domain=_DOMAIN)
    outsider = await _make_user(async_pool, domain=_OTHER_DOMAIN,
                                domain_admin=True)
    token = uuid.uuid4().hex[:6]

    # 本域库 + 他域库各放一个同名 token 对象
    lib = await _mk_library(svc, admin)
    onto = await svc.create_ontology(
        lib["id"], name=f"网元-{token}", description=None,
        attributes=None, user=admin,
    )
    await svc.create_object(
        onto["id"], name=f"AMF-{token}", attributes=None, user=admin)
    other_lib = await _mk_library(svc, outsider, domain=_OTHER_DOMAIN)
    other_onto = await svc.create_ontology(
        other_lib["id"], name=f"网元-{token}", description=None,
        attributes=None, user=outsider,
    )
    await svc.create_object(
        other_onto["id"], name=f"AMF-{token}", attributes=None, user=outsider)

    # 本域成员搜本域：只见本域库，他域库不出现
    result = await svc.search_objects(
        domain=_DOMAIN, q=token, limit=20, user=member)
    assert {h["library_id"] for h in result["hits"]} == {lib["id"]}

    # 他域管理员搜他域：见自己的库（域参数收口而非全局搜）
    other_result = await svc.search_objects(
        domain=_OTHER_DOMAIN, q=token, limit=20, user=outsider)
    assert {h["library_id"] for h in other_result["hits"]} == {other_lib["id"]}

    # 本域成员搜他域：无权限 → Forbidden（路由层映射 403）
    with pytest.raises(Forbidden):
        await svc.search_objects(
            domain=_OTHER_DOMAIN, q=token, limit=20, user=member)


async def test_search_limit_truncation(async_pool) -> None:
    svc = _svc(async_pool)
    admin = await _make_user(async_pool, domain=_DOMAIN, domain_admin=True)
    token = uuid.uuid4().hex[:6]
    lib = await _mk_library(svc, admin)
    onto = await svc.create_ontology(
        lib["id"], name=f"网元-{token}", description=None,
        attributes=None, user=admin,
    )
    for i in range(3):
        await svc.create_object(
            onto["id"], name=f"AMF-{token}-{i}", attributes=None, user=admin)

    tight = await svc.search_objects(
        domain=_DOMAIN, q=token, limit=2, user=admin)
    assert len(tight["hits"]) == 2 and tight["truncated"] is True

    exact = await svc.search_objects(
        domain=_DOMAIN, q=token, limit=3, user=admin)
    assert len(exact["hits"]) == 3 and exact["truncated"] is False

    no_hit = await svc.search_objects(
        domain=_DOMAIN, q=f"{token}-none", limit=20, user=admin)
    assert no_hit == {"truncated": False, "hits": []}
