"""本体库 service 层测试 —— 真库门控（@pytest.mark.postgres，67号 P0）。

覆盖：库 CRUD + 三级权限（域管理员写/域成员读/外人拒）；本体与属性全生命
周期（初始属性/键迁移/存量键清理/未知键/非标量）；本体关系与语义关系规则
（端点匹配/自环类型/自指边/防重边/改端点保护）；删除保护矩阵（§4.8 六行）；
schema 与一度邻接查询。本机无 PG 时整个模块在收集期跳过（与
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
    Duplicate, EndpointMismatch, Forbidden, InUse, InvalidAttributeValue,
    NotFound, OntologyService, UnknownAttribute,
)

#: registry 真实域（resolve_domain 校验走仓库内 domain_registry.yaml）
_DOMAIN = "cloud_core_network"
_OTHER_DOMAIN = "civil_engineering"


def _svc(pool) -> OntologyService:
    return OntologyService(OntologyDB(pool))


async def _make_user(pool, *, domain: str | None = None,
                     domain_admin: bool = False) -> dict:
    kbdb = KbDB(pool)
    user = await kbdb.create_user(username=f"ont-{uuid.uuid4().hex[:8]}")
    if domain is not None:
        async with pool.connection() as conn:
            await conn.execute(
                """INSERT INTO user_domains (user_id, domain, domain_role)
                   VALUES (%s, %s, %s)""",
                (user["id"], domain, "admin" if domain_admin else "member"),
            )
    return user


async def _make_library(svc, admin: dict, *, name: str | None = None) -> dict:
    return await svc.create_library(
        domain=_DOMAIN, name=name or f"测试库-{uuid.uuid4().hex[:6]}",
        description=None, user=admin,
    )


async def _make_ontology(svc, admin: dict, library_id: str, name: str,
                         attributes: list[dict] | None = None) -> dict:
    return await svc.create_ontology(
        library_id, name=name, description=None,
        attributes=attributes or [{"name": "备注"}],
        user=admin,
    )


# ------------------------------------------------------------- 库与权限

async def test_library_crud_and_permissions(async_pool) -> None:
    svc = _svc(async_pool)
    outsider = await _make_user(async_pool)                      # 无域绑定
    member = await _make_user(async_pool, domain=_DOMAIN)
    admin = await _make_user(async_pool, domain=_DOMAIN, domain_admin=True)

    # 外人/普通成员建库 → Forbidden；域管理员 OK
    with pytest.raises(Forbidden):
        await svc.create_library(domain=_DOMAIN, name="X", description=None,
                                 user=outsider)
    with pytest.raises(Forbidden):
        await svc.create_library(domain=_DOMAIN, name="X", description=None,
                                 user=member)
    lib = await _make_library(svc, admin, name="权限测试库")
    assert lib["domain"] == _DOMAIN

    # 域内重名 → Duplicate；跨域同名不冲突
    with pytest.raises(Duplicate):
        await svc.create_library(domain=_DOMAIN, name="权限测试库",
                                 description=None, user=admin)
    other_admin = await _make_user(async_pool, domain=_OTHER_DOMAIN,
                                   domain_admin=True)
    await svc.create_library(domain=_OTHER_DOMAIN, name="权限测试库",
                             description=None, user=other_admin)

    # 读权限：成员可见，域外用户不可见（Forbidden）；不存在的库 → NotFound
    assert len(await svc.list_libraries(domain=_DOMAIN, q="权限测试",
                                        user=member)) == 1
    detail = await svc.get_library(lib["id"], user=member)
    assert detail["ontology_count"] == 0
    with pytest.raises(Forbidden):
        await svc.get_library(lib["id"], user=outsider)
    with pytest.raises(NotFound):
        await svc.get_library("no-such-library", user=admin)

    # 成员改库 → Forbidden；管理员改 → OK
    with pytest.raises(Forbidden):
        await svc.update_library(lib["id"], name="新名", description=None,
                                 user=member)
    renamed = await svc.update_library(lib["id"], name="权限测试库2",
                                       description="d", user=admin)
    assert renamed["name"] == "权限测试库2"

    # 空库可删；有本体拒删（InUse）
    empty_lib = await _make_library(svc, admin)
    await svc.delete_library(empty_lib["id"], user=admin)
    await _make_ontology(svc, admin, lib["id"], "临时本体")
    with pytest.raises(InUse):
        await svc.delete_library(lib["id"], user=admin)


# ------------------------------------------------------------- 本体与属性

async def test_ontology_lifecycle_and_attributes(async_pool) -> None:
    svc = _svc(async_pool)
    admin = await _make_user(async_pool, domain=_DOMAIN, domain_admin=True)
    member = await _make_user(async_pool, domain=_DOMAIN)
    lib = await _make_library(svc, admin)

    onto = await svc.create_ontology(
        lib["id"], name="设备", description="域内设备",
        attributes=[
            {"name": "厂商", "description": "生产厂商"},
            {"name": "软件版本"},
        ],
        user=admin,
    )
    # 库内重名 → Duplicate；成员写 → Forbidden
    with pytest.raises(Duplicate):
        await _make_ontology(svc, admin, lib["id"], "设备")
    with pytest.raises(Forbidden):
        await svc.create_ontology(lib["id"], name="器材", description=None,
                                  attributes=None, user=member)

    detail = await svc.get_ontology(onto["id"], user=member)
    assert [a["name"] for a in detail["attributes"]] == ["厂商", "软件版本"]

    # 对象：未知属性键拒绝；非标量拒绝；正常创建（留空属性不写键）
    obj = await svc.create_object(
        onto["id"], name="AMF",
        attributes={"厂商": "华为、中兴", "软件版本": "V200R021"},
        user=admin,
    )
    assert obj["attributes_json"] == {"厂商": "华为、中兴", "软件版本": "V200R021"}
    with pytest.raises(UnknownAttribute):
        await svc.create_object(onto["id"], name="B",
                                attributes={"不存在": "x"}, user=admin)
    with pytest.raises(InvalidAttributeValue):
        await svc.create_object(onto["id"], name="C",
                                attributes={"厂商": ["a"]}, user=admin)
    # 同本体对象重名 → Duplicate
    with pytest.raises(Duplicate):
        await svc.create_object(onto["id"], name="AMF",
                                attributes=None, user=admin)

    # 属性改名 → 同事务迁移对象键（值保留）
    adapt = detail["attributes"][0]
    renamed_attr = await svc.update_attribute(adapt["id"], name="机型",
                                              description=None, sort_order=None,
                                              user=admin)
    assert renamed_attr["name"] == "机型"
    obj_after = await svc.get_object(obj["id"], user=admin)
    assert obj_after["attributes_json"] == {"机型": "华为、中兴", "软件版本": "V200R021"}
    assert obj_after["source"] == "manual"

    # 属性删除 → 同事务清理对象存量键
    usage_attr = renamed_attr = await svc.update_attribute(
        detail["attributes"][1]["id"], name=None, description=None,
        sort_order=None, user=admin,
    )
    await svc.delete_attribute(usage_attr["id"], user=admin)
    obj_final = await svc.get_object(obj["id"], user=admin)
    assert "软件版本" not in obj_final["attributes_json"]

    # 删除保护：本体有对象 → InUse；清空对象后（无关系引用）可删
    with pytest.raises(InUse):
        await svc.delete_ontology(onto["id"], user=admin)
    await svc.delete_object(obj["id"], user=admin)
    await svc.delete_ontology(onto["id"], user=admin)
    with pytest.raises(NotFound):
        await svc.get_ontology(onto["id"], user=admin)


async def test_ontology_delete_blocked_by_relation_ref(async_pool) -> None:
    """本体被本体关系引用（作端点）→ 拒删（即便没有对象）。"""
    svc = _svc(async_pool)
    admin = await _make_user(async_pool, domain=_DOMAIN, domain_admin=True)
    lib = await _make_library(svc, admin)
    device = await _make_ontology(svc, admin, lib["id"], "设备", [])
    feature = await _make_ontology(svc, admin, lib["id"], "特性", [])
    await svc.create_ontology_relation(
        lib["id"], name="支持", description=None,
        source_ontology_id=device["id"], target_ontology_id=feature["id"],
        user=admin,
    )
    with pytest.raises(InUse):
        await svc.delete_ontology(device["id"], user=admin)


# ------------------------------------------------------------- 关系与图

async def test_semantic_relations_rules(async_pool) -> None:
    svc = _svc(async_pool)
    admin = await _make_user(async_pool, domain=_DOMAIN, domain_admin=True)
    lib = await _make_library(svc, admin)
    device = await _make_ontology(svc, admin, lib["id"], "设备", [])
    feature = await _make_ontology(svc, admin, lib["id"], "特性", [])
    element = await _make_ontology(svc, admin, lib["id"], "网元", [])

    supports = await svc.create_ontology_relation(
        lib["id"], name="支持", description=None,
        source_ontology_id=device["id"], target_ontology_id=feature["id"],
        user=admin,
    )
    # 自环类型允许（网元-互备-网元）
    backup = await svc.create_ontology_relation(
        lib["id"], name="互备", description=None,
        source_ontology_id=element["id"], target_ontology_id=element["id"],
        user=admin,
    )
    # 库内关系重名 → Duplicate
    with pytest.raises(Duplicate):
        await svc.create_ontology_relation(
            lib["id"], name="支持", description=None,
            source_ontology_id=feature["id"], target_ontology_id=device["id"],
            user=admin,
        )
    # 端点本体不在本库 → NotFound
    lib2 = await _make_library(svc, admin)
    outside = await _make_ontology(svc, admin, lib2["id"], "外部本体", [])
    with pytest.raises(NotFound):
        await svc.create_ontology_relation(
            lib["id"], name="跨库", description=None,
            source_ontology_id=outside["id"], target_ontology_id=device["id"],
            user=admin,
        )

    amf = await svc.create_object(device["id"], name="AMF",
                                      attributes=None, user=admin)
    slice = await svc.create_object(feature["id"], name="网络切片",
                                  attributes=None, user=admin)
    active = await svc.create_object(element["id"], name="主用AMF",
                                    attributes=None, user=admin)
    standby = await svc.create_object(element["id"], name="备用AMF",
                                 attributes=None, user=admin)

    # 端点不匹配：特性→设备 用了 支持(设备→特性) → EndpointMismatch
    with pytest.raises(EndpointMismatch):
        await svc.create_semantic_relation(
            ontology_relation_id=supports["id"], source_object_id=slice["id"],
            target_object_id=amf["id"], user=admin,
        )
    # 自指边拒绝
    with pytest.raises(EndpointMismatch):
        await svc.create_semantic_relation(
            ontology_relation_id=backup["id"], source_object_id=active["id"],
            target_object_id=active["id"], user=admin,
        )
    # 正常边 + 防重边
    edge = await svc.create_semantic_relation(
        ontology_relation_id=supports["id"], source_object_id=amf["id"],
        target_object_id=slice["id"], user=admin,
    )
    with pytest.raises(Duplicate):
        await svc.create_semantic_relation(
            ontology_relation_id=supports["id"], source_object_id=amf["id"],
            target_object_id=slice["id"], user=admin,
        )
    await svc.create_semantic_relation(
        ontology_relation_id=backup["id"], source_object_id=active["id"],
        target_object_id=standby["id"], user=admin,
    )

    # 改端点：有实例 → InUse；改名总是允许
    with pytest.raises(InUse):
        await svc.update_ontology_relation(
            supports["id"], name=None, description=None,
            source_ontology_id=element["id"], target_ontology_id=feature["id"],
            user=admin,
        )
    await svc.update_ontology_relation(supports["id"], name="实现",
                                       description=None,
                                       source_ontology_id=None,
                                       target_ontology_id=None, user=admin)

    # 邻接查询
    neighbors = await svc.get_object_neighbors(amf["id"], user=admin)
    assert [n["other_name"] for n in neighbors["outgoing"]] == ["网络切片"]
    assert neighbors["incoming"] == []

    # 删除保护：边类型有实例拒删；对象有边拒删；清边后均可删
    with pytest.raises(InUse):
        await svc.delete_ontology_relation(supports["id"], user=admin)
    with pytest.raises(InUse):
        await svc.delete_object(amf["id"], user=admin)
    await svc.delete_semantic_relation(edge["id"], user=admin)
    await svc.delete_object(amf["id"], user=admin)
    await svc.delete_ontology_relation(supports["id"], user=admin)

    # 语义关系列表（过滤）
    result = await svc.list_semantic_relations(
        lib["id"], source_object_id=active["id"], target_object_id=None,
        ontology_relation_id=None, limit=50, offset=0, user=admin,
    )
    assert result["total"] == 1
    assert result["items"][0]["relation_name"] == "互备"


async def test_library_schema_and_object_listing(async_pool) -> None:
    svc = _svc(async_pool)
    admin = await _make_user(async_pool, domain=_DOMAIN, domain_admin=True)
    lib = await _make_library(svc, admin)
    device = await _make_ontology(svc, admin, lib["id"], "设备",
                                [{"name": "厂商"}])
    feature = await _make_ontology(svc, admin, lib["id"], "特性", [])
    await svc.create_ontology_relation(
        lib["id"], name="支持", description=None,
        source_ontology_id=device["id"], target_ontology_id=feature["id"],
        user=admin,
    )
    for i in range(3):
        await svc.create_object(device["id"], name=f"网元{i}",
                                attributes={"厂商": f"厂商{i}"}, user=admin)

    schema = await svc.get_library_schema(lib["id"], user=admin)
    assert {o["name"] for o in schema["ontologies"]} == {"设备", "特性"}
    assert schema["ontologies"][0]["attributes"][0]["name"] == "厂商"
    assert schema["relations"][0]["source_ontology_name"] == "设备"

    listing = await svc.list_objects(lib["id"], ontology_id=device["id"],
                                     q="网元", limit=10, offset=0, user=admin)
    assert listing["total"] == 3
    assert all(item["ontology_name"] == "设备" for item in listing["items"])
    # 详情含属性定义对齐 + 邻接计数
    detail = await svc.get_object(listing["items"][0]["id"], user=admin)
    assert detail["attribute_defs"][0]["name"] == "厂商"
    assert detail["outgoing_count"] == 0 and detail["incoming_count"] == 0


async def test_create_ontology_attributes_with_explicit_none(async_pool) -> None:
    """回归（1.1.30 生产冒烟）：路由 pydantic dump 出显式 ``sort_order=None``/
    ``description=None``（键存在值为 None）——``.get(key, default)`` 取不到
    默认值，曾把 NULL 插进 NOT NULL 列。service 层按该形态直调锁定。"""
    svc = _svc(async_pool)
    admin = await _make_user(async_pool, domain=_DOMAIN, domain_admin=True)
    lib = await _make_library(svc, admin)

    onto = await svc.create_ontology(
        lib["id"], name="显式空值本体", description=None,
        attributes=[
            {"name": "厂商", "description": None, "sort_order": None},
            {"name": "端口数", "description": None, "sort_order": None},
        ],
        user=admin,
    )
    detail = await svc.get_ontology(onto["id"], user=admin)
    assert [a["name"] for a in detail["attributes"]] == ["厂商", "端口数"]
    # None 兜底为下标序，而不是 NULL
    assert [a["sort_order"] for a in detail["attributes"]] == [0, 1]
