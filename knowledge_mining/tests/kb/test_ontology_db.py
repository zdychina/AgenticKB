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
        lib["id"], name="药品", description="域内药品",
        attributes=[
            {"name": "适应症", "description": "适用症状"},
            {"name": "用法用量"},
        ],
        user=admin,
    )
    # 库内重名 → Duplicate；成员写 → Forbidden
    with pytest.raises(Duplicate):
        await _make_ontology(svc, admin, lib["id"], "药品")
    with pytest.raises(Forbidden):
        await svc.create_ontology(lib["id"], name="器材", description=None,
                                  attributes=None, user=member)

    detail = await svc.get_ontology(onto["id"], user=member)
    assert [a["name"] for a in detail["attributes"]] == ["适应症", "用法用量"]

    # 对象：未知属性键拒绝；非标量拒绝；正常创建（留空属性不写键）
    obj = await svc.create_object(
        onto["id"], name="阿司匹林",
        attributes={"适应症": "发热、头痛", "用法用量": "口服"},
        user=admin,
    )
    assert obj["attributes_json"] == {"适应症": "发热、头痛", "用法用量": "口服"}
    with pytest.raises(UnknownAttribute):
        await svc.create_object(onto["id"], name="B",
                                attributes={"不存在": "x"}, user=admin)
    with pytest.raises(InvalidAttributeValue):
        await svc.create_object(onto["id"], name="C",
                                attributes={"适应症": ["a"]}, user=admin)
    # 同本体对象重名 → Duplicate
    with pytest.raises(Duplicate):
        await svc.create_object(onto["id"], name="阿司匹林",
                                attributes=None, user=admin)

    # 属性改名 → 同事务迁移对象键（值保留）
    adapt = detail["attributes"][0]
    renamed_attr = await svc.update_attribute(adapt["id"], name="主治",
                                              description=None, sort_order=None,
                                              user=admin)
    assert renamed_attr["name"] == "主治"
    obj_after = await svc.get_object(obj["id"], user=admin)
    assert obj_after["attributes_json"] == {"主治": "发热、头痛", "用法用量": "口服"}
    assert obj_after["source"] == "manual"

    # 属性删除 → 同事务清理对象存量键
    usage_attr = renamed_attr = await svc.update_attribute(
        detail["attributes"][1]["id"], name=None, description=None,
        sort_order=None, user=admin,
    )
    await svc.delete_attribute(usage_attr["id"], user=admin)
    obj_final = await svc.get_object(obj["id"], user=admin)
    assert "用法用量" not in obj_final["attributes_json"]

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
    drug = await _make_ontology(svc, admin, lib["id"], "药品", [])
    disease = await _make_ontology(svc, admin, lib["id"], "疾病", [])
    await svc.create_ontology_relation(
        lib["id"], name="治疗", description=None,
        source_ontology_id=drug["id"], target_ontology_id=disease["id"],
        user=admin,
    )
    with pytest.raises(InUse):
        await svc.delete_ontology(drug["id"], user=admin)


# ------------------------------------------------------------- 关系与图

async def test_semantic_relations_rules(async_pool) -> None:
    svc = _svc(async_pool)
    admin = await _make_user(async_pool, domain=_DOMAIN, domain_admin=True)
    lib = await _make_library(svc, admin)
    drug = await _make_ontology(svc, admin, lib["id"], "药品", [])
    disease = await _make_ontology(svc, admin, lib["id"], "疾病", [])
    person = await _make_ontology(svc, admin, lib["id"], "人物", [])

    treats = await svc.create_ontology_relation(
        lib["id"], name="治疗", description=None,
        source_ontology_id=drug["id"], target_ontology_id=disease["id"],
        user=admin,
    )
    # 自环类型允许（人物-认识-人物）
    knows = await svc.create_ontology_relation(
        lib["id"], name="认识", description=None,
        source_ontology_id=person["id"], target_ontology_id=person["id"],
        user=admin,
    )
    # 库内关系重名 → Duplicate
    with pytest.raises(Duplicate):
        await svc.create_ontology_relation(
            lib["id"], name="治疗", description=None,
            source_ontology_id=disease["id"], target_ontology_id=drug["id"],
            user=admin,
        )
    # 端点本体不在本库 → NotFound
    lib2 = await _make_library(svc, admin)
    outside = await _make_ontology(svc, admin, lib2["id"], "外部本体", [])
    with pytest.raises(NotFound):
        await svc.create_ontology_relation(
            lib["id"], name="跨库", description=None,
            source_ontology_id=outside["id"], target_ontology_id=drug["id"],
            user=admin,
        )

    aspirin = await svc.create_object(drug["id"], name="阿司匹林",
                                      attributes=None, user=admin)
    flu = await svc.create_object(disease["id"], name="感冒",
                                  attributes=None, user=admin)
    zhang = await svc.create_object(person["id"], name="张三",
                                    attributes=None, user=admin)
    li = await svc.create_object(person["id"], name="李四",
                                 attributes=None, user=admin)

    # 端点不匹配：疾病→药品 用了 治疗(药品→疾病) → EndpointMismatch
    with pytest.raises(EndpointMismatch):
        await svc.create_semantic_relation(
            ontology_relation_id=treats["id"], source_object_id=flu["id"],
            target_object_id=aspirin["id"], user=admin,
        )
    # 自指边拒绝
    with pytest.raises(EndpointMismatch):
        await svc.create_semantic_relation(
            ontology_relation_id=knows["id"], source_object_id=zhang["id"],
            target_object_id=zhang["id"], user=admin,
        )
    # 正常边 + 防重边
    edge = await svc.create_semantic_relation(
        ontology_relation_id=treats["id"], source_object_id=aspirin["id"],
        target_object_id=flu["id"], user=admin,
    )
    with pytest.raises(Duplicate):
        await svc.create_semantic_relation(
            ontology_relation_id=treats["id"], source_object_id=aspirin["id"],
            target_object_id=flu["id"], user=admin,
        )
    await svc.create_semantic_relation(
        ontology_relation_id=knows["id"], source_object_id=zhang["id"],
        target_object_id=li["id"], user=admin,
    )

    # 改端点：有实例 → InUse；改名总是允许
    with pytest.raises(InUse):
        await svc.update_ontology_relation(
            treats["id"], name=None, description=None,
            source_ontology_id=person["id"], target_ontology_id=disease["id"],
            user=admin,
        )
    await svc.update_ontology_relation(treats["id"], name="医治",
                                       description=None,
                                       source_ontology_id=None,
                                       target_ontology_id=None, user=admin)

    # 邻接查询
    neighbors = await svc.get_object_neighbors(aspirin["id"], user=admin)
    assert [n["other_name"] for n in neighbors["outgoing"]] == ["感冒"]
    assert neighbors["incoming"] == []

    # 删除保护：边类型有实例拒删；对象有边拒删；清边后均可删
    with pytest.raises(InUse):
        await svc.delete_ontology_relation(treats["id"], user=admin)
    with pytest.raises(InUse):
        await svc.delete_object(aspirin["id"], user=admin)
    await svc.delete_semantic_relation(edge["id"], user=admin)
    await svc.delete_object(aspirin["id"], user=admin)
    await svc.delete_ontology_relation(treats["id"], user=admin)

    # 语义关系列表（过滤）
    result = await svc.list_semantic_relations(
        lib["id"], source_object_id=zhang["id"], target_object_id=None,
        ontology_relation_id=None, limit=50, offset=0, user=admin,
    )
    assert result["total"] == 1
    assert result["items"][0]["relation_name"] == "认识"


async def test_library_schema_and_object_listing(async_pool) -> None:
    svc = _svc(async_pool)
    admin = await _make_user(async_pool, domain=_DOMAIN, domain_admin=True)
    lib = await _make_library(svc, admin)
    drug = await _make_ontology(svc, admin, lib["id"], "药品",
                                [{"name": "适应症"}])
    disease = await _make_ontology(svc, admin, lib["id"], "疾病", [])
    await svc.create_ontology_relation(
        lib["id"], name="治疗", description=None,
        source_ontology_id=drug["id"], target_ontology_id=disease["id"],
        user=admin,
    )
    for i in range(3):
        await svc.create_object(drug["id"], name=f"药{i}",
                                attributes={"适应症": f"症{i}"}, user=admin)

    schema = await svc.get_library_schema(lib["id"], user=admin)
    assert {o["name"] for o in schema["ontologies"]} == {"药品", "疾病"}
    assert schema["ontologies"][0]["attributes"][0]["name"] == "适应症"
    assert schema["relations"][0]["source_ontology_name"] == "药品"

    listing = await svc.list_objects(lib["id"], ontology_id=drug["id"],
                                     q="药", limit=10, offset=0, user=admin)
    assert listing["total"] == 3
    assert all(item["ontology_name"] == "药品" for item in listing["items"])
    # 详情含属性定义对齐 + 邻接计数
    detail = await svc.get_object(listing["items"][0]["id"], user=admin)
    assert detail["attribute_defs"][0]["name"] == "适应症"
    assert detail["outgoing_count"] == 0 and detail["incoming_count"] == 0
